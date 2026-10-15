"""LLM Provider：转发上游大模型（OpenAI 兼容 / Anthropic），支持流式透传。

仅做请求转发与 SSE 透传，不做格式转换。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

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
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/chat/completions"


class LLMProvider(BaseProvider):
    provider_type = "llm"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.fmt = str(config.get("type", "openai")).lower()
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = config.get("api_key") or None
        self.model = config.get("model") or None  # 可选：上游实际模型名（覆盖下游请求的 model）
        self.timeout = float(config.get("timeout", 120))
        self._client: httpx.AsyncClient | None = None

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
        return (
            _anthropic_endpoint(self.base_url)
            if self.fmt == "anthropic"
            else _openai_endpoint(self.base_url)
        )

    def _headers(self) -> dict[str, str]:
        if self.fmt == "anthropic":
            h = {
                "x-api-key": self.api_key or "",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
        else:
            h = {"content-type": "application/json"}
            if self.api_key:
                h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def test(self) -> dict[str, Any]:
        try:
            async with self.client.stream(
                "POST",
                self.endpoint(),
                json={"model": self.model or "test", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                headers=self._headers(),
            ) as resp:
                if resp.status_code < 400:
                    return {"ok": True, "message": "连接成功", "status": resp.status_code}
                body = (await resp.aread())[:500].decode("utf-8", "replace")
                return {
                    "ok": False,
                    "message": f"上游返回 {resp.status_code}",
                    "detail": body,
                }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"连接失败：{e}"}

    async def chat(
        self, payload: dict[str, Any]
    ) -> httpx.Response:
        """非流式转发，返回上游原始响应。"""
        headers = self._headers()
        try:
            resp = await self.client.post(
                self.endpoint(), json=payload, headers=headers
            )
            return resp
        except httpx.TimeoutException as e:
            raise ProviderError(f"上游 LLM 超时：{e}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"上游 LLM 连接失败：{e}") from e

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """流式转发，逐行透传上游 SSE 原始字节。"""
        headers = self._headers()
        try:
            async with self.client.stream(
                "POST", self.endpoint(), json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread())[:2000]
                    raise ProviderError(
                        f"上游返回 {resp.status_code}: {body.decode('utf-8', 'replace')}",
                        status_code=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    if line.strip():
                        yield (line + "\n").encode("utf-8")
        except httpx.TimeoutException as e:
            raise ProviderError(f"上游 LLM 超时：{e}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"上游 LLM 连接失败：{e}") from e


def build_llm_payload(
    messages: list[dict[str, Any]],
    stream: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"messages": messages, "stream": stream}
    if extra:
        for k in ("model", "temperature", "top_p", "max_tokens", "stop", "tools", "tool_choice", "response_format"):
            if k in extra and extra[k] is not None:
                payload[k] = extra[k]
    return payload


def build_anthropic_payload(
    model: str,
    messages: list[dict[str, Any]],
    system: str | None,
    max_tokens: int = 2048,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 OpenAI 风格 message 列表转成 Anthropic 请求体（仅用于 Anthropic 上游）。"""
    anon: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue  # system 单独放
        content = m.get("content")
        anon.append({"role": role, "content": content})
    payload: dict[str, Any] = {
        "model": model,
        "messages": anon,
        "max_tokens": max_tokens,
    }
    if system:
        payload["system"] = system
    if extra:
        if extra.get("temperature") is not None:
            payload["temperature"] = extra["temperature"]
        if extra.get("top_p") is not None:
            payload["top_p"] = extra["top_p"]
        if extra.get("stop"):
            payload["stop_sequences"] = extra["stop"]
        if extra.get("top_k") is not None:
            payload["top_k"] = extra["top_k"]
    return payload
