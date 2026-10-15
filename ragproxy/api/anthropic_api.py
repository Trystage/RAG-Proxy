"""Anthropic 兼容接口：POST /v1/messages（支持 stream）。"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..providers.base import ProviderError
from ..providers.llm import build_anthropic_payload
from ..security import bearer_token, check_api_key
from .common import (
    ModelNotFoundError,
    FormatMismatchError,
    extract_anthropic_response,
    query_preview,
    run_pipeline,
    should_log_response,
)

router = APIRouter()


class AnthropicMessage(BaseModel):
    role: str
    content: Any


class AnthropicRequest(BaseModel):
    model: str
    messages: list[AnthropicMessage]
    max_tokens: int = 2048
    system: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    stream: bool = False
    metadata: dict | None = None


def _error(status: int, message: str, err_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": err_type, "message": message},
    )


def _require_api_key(request: Request) -> bool:
    state = request.app.state.app  # type: ignore[attr-defined]
    allowed = state.config.get("api_keys", []) or []
    if not allowed:
        return True
    token = bearer_token(request.headers.get("authorization"))
    return check_api_key(allowed, token)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text", "")))
        return "\n".join(parts)
    return str(content)


def _to_openai_messages(body: AnthropicRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if body.system:
        messages.append({"role": "system", "content": body.system})
    for m in body.messages:
        messages.append({"role": m.role, "content": _content_to_text(m.content)})
    return messages


def _from_openai_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI 风格消息 → (system, anthropic messages)。"""
    system_parts: list[str] = []
    anon: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            c = m.get("content", "")
            if isinstance(c, list):
                c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
            if c:
                system_parts.append(str(c))
        else:
            anon.append({"role": m["role"], "content": m.get("content", "")})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, anon


@router.post("/v1/messages")
async def messages(request: Request, body: AnthropicRequest):
    state = request.app.state.app  # type: ignore[attr-defined]
    if not _require_api_key(request):
        return _error(401, "无效或缺失 API key", "authentication_error")

    started = time.perf_counter()
    openai_messages = _to_openai_messages(body)

    try:
        result = await run_pipeline(state, openai_messages, body.model, "anthropic")
    except ModelNotFoundError as e:
        return _error(404, str(e))
    except FormatMismatchError as e:
        return _error(400, str(e))
    except ProviderError as e:
        return _error(502, str(e), "upstream_error")

    system, anon_messages = _from_openai_messages(result.messages)
    # 上游配置了 model 时用上游模型名覆盖下游请求的 model
    up_model = result.provider.model
    payload = build_anthropic_payload(
        up_model or body.model,
        anon_messages,
        system,
        max_tokens=body.max_tokens,
        extra={
            "temperature": body.temperature,
            "top_p": body.top_p,
            "top_k": body.top_k,
            "stop": body.stop_sequences,
        },
    )
    if body.stream:
        payload["stream"] = True

    async def _record(status: int, stream: bool, response: str = "") -> None:
        try:
            reqlog = state.reqlog
            if reqlog:
                kb = ",".join(state.orch.kb_names(result.mapping)) if state.orch else ""
                await reqlog.log(
                    ts=time.strftime("%Y-%m-%d %H:%M:%S"),
                    model=result.mapping.get("name", ""),
                    kb=kb,
                    query_preview=query_preview(result.query, state),
                    hit=result.hit,
                    hit_docs=[c.get("id") or "" for c in result.chunks],
                    retrieve_ms=result.stats.get("retrieve_ms", 0.0),
                    total_ms=(time.perf_counter() - started) * 1000,
                    status=status,
                    upstream=result.mapping.get("upstream", ""),
                    stream=stream,
                    fmt="anthropic",
                    response=response,
                )
        except Exception:  # noqa: BLE001
            pass

    log_resp = should_log_response(state)

    if not body.stream:
        try:
            resp = await result.provider.chat(payload)
        except ProviderError as e:
            return _error(502, str(e), "upstream_error")
        status = resp.status_code
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"type": "error", "message": resp.text}
        response = extract_anthropic_response(data) if log_resp else ""
        await _record(status, False, response)
        return JSONResponse(status_code=status, content=data)

    acc: list[str] = []

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for line in result.provider.chat_stream(payload):
                if log_resp:
                    try:
                        text = line.decode("utf-8", "replace")
                        if "content_block_delta" in text:
                            obj = json.loads(text.split("data:", 1)[1].strip())
                            if obj.get("delta", {}).get("type") == "text_delta":
                                acc.append(obj["delta"].get("text", ""))
                    except Exception:  # noqa: BLE001
                        pass
                yield line
        except ProviderError as e:
            payload_err = json.dumps(
                {"type": "upstream_error", "message": str(e)}, ensure_ascii=False
            ).encode("utf-8")
            yield b"event: error\ndata: " + payload_err + b"\n\n"
        finally:
            await _record(200, True, "".join(acc)[:4000])

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
