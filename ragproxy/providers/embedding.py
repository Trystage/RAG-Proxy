"""Embedding Provider：OpenAI 兼容远程接口 + 磁盘缓存。

硬性约束：不加载本地模型，向量全部来自远程 API。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, new_client


class EmbeddingProvider(BaseProvider):
    provider_type = "embedding"

    def __init__(self, config: dict[str, Any], cache_dir: Path | None = None):
        super().__init__(config)
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = config.get("api_key") or None
        self.model = str(config.get("model", "text-embedding-3-small"))
        self.timeout = float(config.get("timeout", 60))
        self.dimensions = int(config.get("dimensions") or 0)
        self._client: httpx.AsyncClient | None = None
        self._cache_dir = cache_dir
        self._cache_lock = threading.Lock()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = new_client(self.base_url, self.api_key, self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def endpoint(self) -> str:
        base = self.base_url
        if base.endswith("/embeddings"):
            return base
        if base.endswith("/v1"):
            return base + "/embeddings"
        return base + "/embeddings"

    def _cache_path(self, text: str) -> Path | None:
        if not self._cache_dir:
            return None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.json"

    def _read_cache(self, text: str) -> list[float] | None:
        path = self._cache_path(text)
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("model") == self.model and data.get("v") == 1:
                return data["vector"]
        except Exception:  # noqa: BLE001
            pass
        return None

    def _write_cache(self, text: str, vector: list[float]) -> None:
        path = self._cache_path(text)
        if path is None:
            return
        try:
            with self._cache_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {"v": 1, "model": self.model, "vector": vector},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
        except Exception:  # noqa: BLE001
            pass

    async def embed_one(self, text: str) -> list[float]:
        cached = self._read_cache(text)
        if cached is not None:
            return cached
        vec = await self._embed_remote([text])
        self._write_cache(text, vec[0])
        return vec[0]

    async def embed_many(
        self, texts: list[str], concurrency: int = 4
    ) -> list[list[float]]:
        """批量向量化，带缓存与并发限制。"""
        results: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, t in enumerate(texts):
            cached = self._read_cache(t)
            if cached is not None:
                results[i] = cached
            else:
                missing.append(i)

        sem = asyncio.Semaphore(max(1, concurrency))

        async def fetch(idx: int) -> None:
            async with sem:
                vec = await self._embed_remote([texts[idx]])
                results[idx] = vec[0]
                self._write_cache(texts[idx], vec[0])

        if missing:
            await asyncio.gather(*(fetch(i) for i in missing))
        return [r for r in results if r is not None]  # type: ignore[misc]

    async def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self.client.post(
                self.endpoint(),
                json={"model": self.model, "input": texts},
            )
        except httpx.TimeoutException as e:
            raise ProviderError(f"Embedding 服务超时：{e}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"Embedding 服务连接失败：{e}") from e
        if resp.status_code >= 400:
            raise ProviderError(
                f"Embedding 服务返回 {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
            items = data["data"]
            items.sort(key=lambda x: int(x.get("index", 0)))
            vectors = [list(it["embedding"]) for it in items]
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Embedding 响应解析失败：{e}") from e
        if self.dimensions and vectors and len(vectors[0]) != self.dimensions:
            raise ProviderError(
                f"Embedding 维度 {len(vectors[0])} 与配置维度 {self.dimensions} 不一致"
            )
        return vectors

    async def test(self) -> dict[str, Any]:
        try:
            vec = await self._embed_remote(["ping"])
            return {
                "ok": True,
                "message": f"连接成功（模型 {self.model}，维度 {len(vec[0])}）",
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"连接失败：{e}"}
