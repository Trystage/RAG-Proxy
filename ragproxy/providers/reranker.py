"""ReRanker Provider：OpenAI 兼容 / Cohere / Jina 远程接口（带磁盘缓存）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, new_client

RERANK_BODY_SCHEMA = {"model": str, "query": str, "documents": list, "top_n": int}


class ReRankerProvider(BaseProvider):
    provider_type = "reranker"

    def __init__(
        self,
        config: dict[str, Any],
        cache_dir: Path | None = None,
        max_cache_files: int = 10000,
    ):
        super().__init__(config)
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = config.get("api_key") or None
        self.model = str(config.get("model", "rerank"))
        self.timeout = float(config.get("timeout", 60))
        self.kind = str(config.get("type", "openai")).lower()
        self._client: httpx.AsyncClient | None = None
        self._cache_dir = cache_dir
        self.max_cache_files = max_cache_files

    def _cache_path(self, query: str, documents: list[str]) -> Path | None:
        if not self._cache_dir:
            return None
        key = hashlib.sha256(
            json.dumps([self.model, query, documents], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return self._cache_dir / f"{key}.json"

    def _read_cache(self, query: str, documents: list[str]) -> list[tuple[int, float]] | None:
        path = self._cache_path(query, documents)
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("v") == 1 and data.get("model") == self.model:
                return [(int(i), float(s)) for i, s in data["scored"]]
        except Exception:  # noqa: BLE001
            pass
        return None

    def _write_cache(
        self, query: str, documents: list[str], scored: list[tuple[int, float]]
    ) -> None:
        path = self._cache_path(query, documents)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"v": 1, "model": self.model, "scored": [[i, s] for i, s in scored]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if self._cache_dir and self.max_cache_files:
                files = sorted(
                    self._cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime
                )
                while len(files) > self.max_cache_files:
                    f = files.pop(0)
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        break
        except Exception:  # noqa: BLE001
            pass

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.api_key:
                if self.kind == "cohere":
                    headers["Authorization"] = f"Bearer {self.api_key}"
                elif self.kind == "jina":
                    headers["Authorization"] = f"Bearer {self.api_key}"
                else:
                    headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def endpoint(self) -> str:
        base = self.base_url
        if self.kind == "cohere":
            return base if base.endswith("/v1/rerank") else base + "/v1/rerank"
        if self.kind == "jina":
            return base if base.endswith("/v1/rerank") else base + "/v1/rerank"
        # openai 兼容
        if base.endswith("/rerank"):
            return base
        if base.endswith("/v1"):
            return base + "/rerank"
        return base + "/rerank"

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """返回 [(index, score)]，按 score 降序。"""
        if not documents:
            return []
        cached = self._read_cache(query, documents)
        if cached is not None:
            return cached
        body: dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
        }
        if top_n:
            body["top_n"] = top_n
        try:
            resp = await self.client.post(self.endpoint(), json=body)
        except httpx.TimeoutException as e:
            raise ProviderError(f"ReRanker 服务超时：{e}") from e
        except httpx.HTTPError as e:
            raise ProviderError(f"ReRanker 服务连接失败：{e}") from e
        if resp.status_code >= 400:
            raise ProviderError(
                f"ReRanker 服务返回 {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
            results = data.get("results", [])
            scored = [
                (int(r.get("index", i)), float(r.get("relevance_score", r.get("score", 0.0))))
                for i, r in enumerate(results)
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            self._write_cache(query, documents, scored)
            return scored
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"ReRanker 响应解析失败：{e}") from e

    async def test(self) -> dict[str, Any]:
        try:
            scored = await self.rerank("ping", ["hello world"])
            return {"ok": True, "message": f"Connection successful (model {self.model}, {len(scored)} results)"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"Connection failed: {e}"}
