"""LLM 上游：OpenAI / Anthropic 透传与 Ollama 原生协议适配。"""
from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx

from .base import BaseProvider, ProviderError, new_client

ANTHROPIC_VERSION = "2023-06-01"


def _anthropic_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + "/messages"
    if base.endswith("/messages"):
        return base
    return base + "/v1/messages"


def _openai_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def protocol_root(base_url: str, fmt: str) -> str:
    """移除当前协议的已知后缀，保留反向代理路径前缀。"""
    url = urlsplit(base_url.strip())
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Invalid upstream URL")
    url.port  # Validate the port before constructing a client.
    path = url.path.rstrip("/")
    suffixes = {
        "ollama": ("/api/chat", "/api/tags", "/api"),
        "anthropic": ("/v1/messages", "/v1/models", "/messages", "/models", "/v1"),
        "openai": ("/chat/completions", "/embeddings", "/models"),
    }[fmt]
    for suffix in suffixes:
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((url.scheme, url.netloc, path, "", ""))


def models_endpoint(base_url: str, fmt: str) -> str:
    root = protocol_root(base_url, fmt)
    if fmt == "ollama":
        return root + "/api/tags"
    if fmt == "anthropic":
        return root + "/v1/models"
    return root + "/models"


def parse_model_ids(data: Any, fmt: str) -> list[str]:
    rows = data.get("models" if fmt == "ollama" else "data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Invalid model list")
    ids = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid model list entry")
        value = (row.get("model") or row.get("name")) if fmt == "ollama" else row.get("id")
        if not isinstance(value, str) or not value:
            raise ValueError("Invalid model id")
        if value not in ids:
            ids.append(value)
    return ids


def ollama_payload(payload: dict[str, Any]) -> dict[str, Any]:
    messages = copy.deepcopy(payload.get("messages", []))
    names = {}
    for message in messages:
        if message.get("role") == "developer":
            message["role"] = "system"
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            if call.get("id"):
                names[call["id"]] = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError as exc:
                    raise ProviderError("Invalid tool arguments") from exc
            if not isinstance(args, dict):
                raise ProviderError("Tool arguments must be a JSON object")
            fn["arguments"] = args
            call.pop("id", None)
            call.pop("type", None)
        if message.get("role") == "tool":
            message["tool_name"] = names.get(message.pop("tool_call_id", ""), message.get("name", ""))
        message.pop("name", None)
        if isinstance(message.get("content"), list):
            text, images = [], []
            for part in message["content"]:
                if part.get("type") == "text":
                    text.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    image = part.get("image_url", {}).get("url", "")
                    if not image.startswith("data:") or ";base64," not in image:
                        raise ProviderError("Ollama requires base64 image data, not remote image URLs")
                    images.append(image.split(";base64,", 1)[1])
                else:
                    raise ProviderError("Unsupported Ollama content block")
            message["content"] = "\n".join(text)
            if images:
                message["images"] = images
        if message.get("content") is None:
            message["content"] = ""
        if "reasoning_content" in message:
            message["thinking"] = message.pop("reasoning_content")
    result = {"model": payload.get("model"), "messages": messages, "stream": bool(payload.get("stream", False))}
    options = {}
    for key, target in (("temperature", "temperature"), ("top_p", "top_p"), ("max_tokens", "num_predict"), ("stop", "stop")):
        if payload.get(key) is not None:
            value = payload[key]
            options[target] = [value] if key == "stop" and isinstance(value, str) else value
    if options:
        result["options"] = options
    if payload.get("tools") and payload.get("tool_choice") != "none":
        result["tools"] = payload["tools"]
    if payload.get("tool_choice") not in (None, "auto", "none"):
        raise ProviderError("Ollama does not support forced tool_choice")
    response_format = payload.get("response_format") or {}
    if response_format.get("type") == "json_object":
        result["format"] = "json"
    elif response_format.get("type") == "json_schema":
        result["format"] = response_format.get("json_schema", {}).get("schema", {})
    return result


def ollama_completion(data: dict[str, Any], completion_id: str, created: int, stream: bool = False, tool_index: int = 0, had_tools: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Invalid Ollama response")
    if data.get("error"):
        raise ProviderError("Ollama upstream returned an error")
    if not isinstance(data.get("done"), bool) or (not stream and not data["done"]):
        raise ValueError("Invalid Ollama completion status")
    message = data.get("message")
    if message is None and stream and data["done"]:
        message = {}
    if not isinstance(message, dict):
        raise ValueError("Invalid Ollama message")
    converted: dict[str, Any] = {"role": "assistant", "content": message.get("content", "")}
    if message.get("thinking"):
        converted["reasoning_content"] = message["thinking"]
    calls = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        args = fn.get("arguments", {})
        item = {"id": f"call_{completion_id}_{tool_index + i}", "type": "function", "function": {"name": fn.get("name", ""), "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}}
        if stream:
            item["index"] = tool_index + i
        calls.append(item)
    if calls:
        converted["tool_calls"] = calls
    done = data["done"]
    finish = ("tool_calls" if calls or had_tools else "length" if data.get("done_reason") == "length" else "stop") if done else None
    result = {"id": completion_id, "object": "chat.completion.chunk" if stream else "chat.completion", "created": created, "model": data.get("model", ""), "choices": [{"index": 0, "delta" if stream else "message": converted, "finish_reason": finish}]}
    if done:
        prompt, output = data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        if not isinstance(prompt, int) or not isinstance(output, int):
            raise ValueError("Invalid Ollama usage")
        result["usage"] = {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}
    return result


class LLMProvider(BaseProvider):
    provider_type = "llm"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.fmt = str(config.get("type", "openai")).lower()
        self.base_url = str(config.get("base_url", "")).strip().rstrip("/")
        self.api_key = config.get("api_key") or None
        self.model = config.get("model") or None
        self.timeout = float(config.get("timeout", 120))
        self._client: httpx.AsyncClient | None = None

    def supports_format(self, fmt: str) -> bool:
        return fmt == self.fmt or (self.fmt == "ollama" and fmt == "openai")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = new_client(self.base_url, None, self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def endpoint(self) -> str:
        if self.fmt == "ollama":
            return protocol_root(self.base_url, self.fmt) + "/api/chat"
        return _anthropic_endpoint(self.base_url) if self.fmt == "anthropic" else _openai_endpoint(self.base_url)

    def _headers(self) -> dict[str, str]:
        if self.fmt == "anthropic":
            return {"x-api-key": self.api_key or "", "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def test(self) -> dict[str, Any]:
        try:
            response = await self.chat({"model": self.model or "test", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1, "stream": False})
            if response.status_code < 400:
                return {"ok": True, "message": "Connection successful", "status": response.status_code}
            return {"ok": False, "message": f"Upstream returned {response.status_code}", "detail": response.text[:500]}
        except Exception as exc:
            return {"ok": False, "message": f"Connection failed: {exc}"}

    async def chat(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            body = ollama_payload(payload) if self.fmt == "ollama" else payload
            response = await self.client.post(self.endpoint(), json=body, headers=self._headers())
            if self.fmt == "ollama":
                if response.status_code >= 300:
                    raise ProviderError(f"Ollama upstream returned {response.status_code}", status_code=response.status_code)
                data = ollama_completion(response.json(), "chatcmpl-" + uuid.uuid4().hex, int(time.time()))
                return httpx.Response(response.status_code, json=data)
            return response
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderError("Invalid Ollama upstream response or configuration") from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("LLM upstream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("LLM upstream connection failed") from exc

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        try:
            body = ollama_payload(payload) if self.fmt == "ollama" else payload
            completion_id, created = "chatcmpl-" + uuid.uuid4().hex, int(time.time())
            tool_index = 0
            async with self.client.stream("POST", self.endpoint(), json=body, headers=self._headers()) as response:
                if response.status_code >= 400 or (self.fmt == "ollama" and response.status_code >= 300):
                    raise ProviderError(f"Upstream returned {response.status_code}", status_code=response.status_code)
                async for line in response.aiter_lines():
                    if self.fmt != "ollama":
                        yield (line + "\n").encode("utf-8")
                        continue
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    chunk = ollama_completion(data, completion_id, created, True, tool_index, tool_index > 0)
                    tool_index += len((data.get("message") or {}).get("tool_calls") or [])
                    yield ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8")
                    if data.get("done"):
                        yield b"data: [DONE]\n\n"
                        return
                if self.fmt == "ollama":
                    raise ProviderError("Ollama stream ended before done")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProviderError("Invalid Ollama upstream response or configuration") from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("LLM upstream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("LLM upstream connection failed") from exc


def build_llm_payload(messages: list[dict[str, Any]], stream: bool, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"messages": messages, "stream": stream}
    if extra:
        for k in ("model", "temperature", "top_p", "max_tokens", "stop", "tools", "tool_choice", "response_format"):
            if k in extra and extra[k] is not None:
                payload[k] = extra[k]
    return payload


def build_anthropic_payload(model: str, messages: list[dict[str, Any]], system: str | None, max_tokens: int = 2048, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    anon = [{"role": m.get("role"), "content": m.get("content")} for m in messages if m.get("role") != "system"]
    payload: dict[str, Any] = {"model": model, "messages": anon, "max_tokens": max_tokens}
    if system:
        payload["system"] = system
    if extra:
        for key in ("temperature", "top_p", "top_k"):
            if extra.get(key) is not None:
                payload[key] = extra[key]
        if extra.get("stop"):
            payload["stop_sequences"] = extra["stop"]
    return payload
