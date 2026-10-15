"""Provider 基类与通用工具。"""
from __future__ import annotations

import abc
from typing import Any

import httpx


class ProviderError(RuntimeError):
    """Provider 调用失败（连接、鉴权、上游错误）。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BaseProvider(abc.ABC):
    provider_type = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}

    @property
    def id(self) -> str:
        return str(self.config.get("id", ""))

    @abc.abstractmethod
    async def test(self) -> dict[str, Any]:
        """连接测试，返回 {ok, message, detail?}"""

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.provider_type}


def new_client(base_url: str, api_key: str | None, timeout: float) -> httpx.AsyncClient:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
        limits=httpx.Limits(max_keepalive_connections=8),
    )
