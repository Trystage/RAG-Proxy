"""OpenAI 兼容接口：POST /v1/chat/completions（支持 stream）、GET /v1/models。"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..providers.base import ProviderError
from ..providers.llm import build_llm_payload
from ..security import bearer_token, check_api_key
from .common import (
    ModelNotFoundError,
    FormatMismatchError,
    extract_openai_response,
    query_preview,
    run_pipeline,
    should_log_response,
)

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, alias="max_tokens")
    stop: list[str] | str | None = None
    tools: list[dict] | None = None
    tool_choice: Any = None
    response_format: dict | None = None
    n: int | None = None


def _error(status: int, message: str, err_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "param": None, "code": None}},
    )


def _require_api_key(request: Request) -> str | None:
    state = request.app.state.app  # type: ignore[attr-defined]
    allowed = state.config.get("api_keys", []) or []
    if not allowed:
        return None
    token = bearer_token(request.headers.get("authorization"))
    if not check_api_key(allowed, token):
        return None
    return token


@router.get("/v1/models")
async def list_models(request: Request):
    if _require_api_key(request) is None and (request.app.state.app.config.get("api_keys") or []):
        return _error(401, "无效或缺失 API key", "authentication_error")
    state = request.app.state.app  # type: ignore[attr-defined]
    return {"object": "list", "data": state.orch.list_models() if state.orch else []}


async def _process_chat(request: Request, body: ChatRequest) -> tuple[Any, dict[str, Any]]:
    """执行流水线，返回 (messages, meta)。"""
    state = request.app.state.app  # type: ignore[attr-defined]
    started = time.perf_counter()
    messages = [m.model_dump(exclude_none=True) for m in body.messages]
    result = await run_pipeline(state, messages, body.model, "openai")
    extra = {
        "model": body.model,
        "temperature": body.temperature,
        "top_p": body.top_p,
        "max_tokens": body.max_tokens,
        "stop": body.stop,
        "tools": body.tools,
        "tool_choice": body.tool_choice,
        "response_format": body.response_format,
    }
    meta = {
        "result": result,
        "extra": extra,
        "started": started,
        "mapping": result.mapping,
    }
    return result.messages, meta


def _payload_from_meta(meta: dict[str, Any], stream: bool) -> dict[str, Any]:
    payload = build_llm_payload(meta["result"].messages, stream, meta["extra"])
    # 上游配置了 model 时，用上游模型名覆盖（解决 Ollama 等按真实模型名路由的场景）
    up_model = meta["result"].provider.model
    if up_model:
        payload["model"] = up_model
    return payload


async def _record(state, meta, status, stream: bool, response: str = "") -> None:
    try:
        r = meta["result"]
        kb = ",".join(state.orch.kb_names(r.mapping)) if state.orch else ""
        reqlog = state.reqlog
        if reqlog:
            await reqlog.log(
                ts=time.strftime("%Y-%m-%d %H:%M:%S"),
                model=r.mapping.get("name", ""),
                kb=kb,
                query_preview=query_preview(r.query, state),
                hit=r.hit,
                hit_docs=[c.get("id") or "" for c in r.chunks],
                retrieve_ms=r.stats.get("retrieve_ms", 0.0),
                total_ms=(time.perf_counter() - meta["started"]) * 1000,
                status=status,
                upstream=r.mapping.get("upstream", ""),
                stream=stream,
                fmt="openai",
                response=response,
            )
    except Exception:  # noqa: BLE001
        pass


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatRequest):
    state = request.app.state.app  # type: ignore[attr-defined]
    if _require_api_key(request) is None and (state.config.get("api_keys") or []):
        return _error(401, "无效或缺失 API key", "authentication_error")

    try:
        messages, meta = await _process_chat(request, body)
    except ModelNotFoundError as e:
        fake = {
            "started": time.perf_counter(),
            "result": SimpleNamespace(
                mapping={"name": body.model}, query="", hit=False, chunks=[], stats={}
            ),
        }
        await _record(state, fake, 404, body.stream)
        return _error(404, str(e), "invalid_request_error")
    except FormatMismatchError as e:
        return _error(400, str(e))
    except ProviderError as e:
        return _error(502, str(e), "upstream_error")

    provider = meta["result"].provider
    log_resp = should_log_response(state)

    if not body.stream:
        try:
            resp = await provider.chat(_payload_from_meta(meta, False))
        except ProviderError as e:
            return _error(502, str(e), "upstream_error")
        status = resp.status_code
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw": resp.text}
        response = extract_openai_response(data) if log_resp else ""
        await _record(state, meta, status, False, response)
        return JSONResponse(status_code=status, content=data)

    # 流式：SSE 透传（可选累积响应用于日志）
    acc: list[str] = []

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for line in provider.chat_stream(_payload_from_meta(meta, True)):
                if log_resp:
                    try:
                        text = line.decode("utf-8", "replace")
                        if text.startswith("data:") and "[DONE]" not in text:
                            obj = json.loads(text[5:].strip())
                            for ch in obj.get("choices", []) or []:
                                delta = (ch.get("delta") or {}).get("content")
                                if isinstance(delta, str) and delta:
                                    acc.append(delta)
                    except Exception:  # noqa: BLE001
                        pass
                yield line
        except ProviderError as e:
            payload = json.dumps(
                {
                    "error": {
                        "message": str(e),
                        "type": "upstream_error",
                        "param": None,
                        "code": None,
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            yield b"data: " + payload + b"\n\n"
        finally:
            await _record(state, meta, 200, True, "".join(acc)[:4000])

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
