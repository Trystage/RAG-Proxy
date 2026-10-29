"""Vector Store 抽象与实现。

- SimpleVectorStore：内置纯 Python 实现（numpy 余弦相似度 + JSONL/npy 持久化），
  不引入 chromadb/onnxruntime 等任何本地推理依赖，满足硬性约束。
- ChromaHttpStore：连接独立部署的 Chroma Server（REST API），
  避免在本进程内加载 chromadb 及其 onnxruntime 依赖。
"""
from __future__ import annotations

import abc
import json
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from .base import ProviderError


class VectorStoreError(RuntimeError):
    pass


class BaseVectorStore(abc.ABC):
    name = "base"

    @abc.abstractmethod
    async def create_kb(self, kb_name: str) -> None: ...

    @abc.abstractmethod
    async def delete_kb(self, kb_name: str) -> None: ...

    @abc.abstractmethod
    async def list_kbs(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def count(self, kb_name: str) -> int: ...

    @abc.abstractmethod
    async def add(self, kb_name: str, items: list[dict], embeddings: list[list[float]]) -> None: ...

    @abc.abstractmethod
    async def query(
        self, kb_name: str, query_vector: list[float], top_k: int, score_threshold: float
    ) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def delete_documents(self, kb_name: str, file_ids: list[str]) -> None: ...

    async def dim(self, kb_name: str) -> int:
        """知识库已索引向量维度；无法获取时返回 0（表示未知/未索引）。"""
        return 0


def _norm(v: list[float]) -> np.ndarray:
    arr = np.asarray(v, dtype=np.float32).reshape(1, -1)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr


class SimpleVectorStore(BaseVectorStore):
    name = "simple"

    def __init__(self, root: Path, cache_limit: int = 4):
        self.root = root
        self.cache_limit = cache_limit
        self._cache: dict[str, tuple[list[dict], np.ndarray | None]] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- 路径 ----------
    def _kb_dir(self, kb: str) -> Path:
        return self.root / kb

    def _kb_meta_path(self, kb: str) -> Path:
        return self._kb_dir(kb) / "kb.json"

    def _chunks_path(self, kb: str) -> Path:
        return self._kb_dir(kb) / "chunks.jsonl"

    def _vectors_path(self, kb: str) -> Path:
        return self._kb_dir(kb) / "vectors.npy"

    def _ensure_kb(self, kb: str) -> None:
        d = self._kb_dir(kb)
        d.mkdir(parents=True, exist_ok=True)
        meta_path = self._kb_meta_path(kb)
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps(
                    {
                        "name": kb,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "documents": [],
                        "chunk_count": 0,
                        "dim": 0,  # 向量维度（首次写入时记录，用于切换 Provider 维度冲突检测）
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _load_meta(self, kb: str) -> dict:
        self._ensure_kb(kb)
        return json.loads(self._kb_meta_path(kb).read_text(encoding="utf-8"))

    def _save_meta(self, kb: str, meta: dict) -> None:
        self._kb_meta_path(kb).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_chunks(self, kb: str) -> list[dict]:
        path = self._chunks_path(kb)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save_chunks(self, kb: str, chunks: list[dict]) -> None:
        self._chunks_path(kb).write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks),
            encoding="utf-8",
        )

    def _load_vectors(self, kb: str) -> np.ndarray | None:
        path = self._vectors_path(kb)
        if not path.exists():
            return None
        return np.load(path)

    # ---------- 缓存 ----------
    def _evict(self) -> None:
        if len(self._cache) > self.cache_limit:
            # 移除最早插入的
            key = next(iter(self._cache))
            self._cache.pop(key, None)

    # ---------- CRUD ----------
    async def create_kb(self, kb_name: str) -> None:
        self._ensure_kb(kb_name)

    async def delete_kb(self, kb_name: str) -> None:
        import shutil

        d = self._kb_dir(kb_name)
        if d.exists():
            shutil.rmtree(d)
        self._cache.pop(kb_name, None)

    async def list_kbs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and (d / "kb.json").exists():
                meta = json.loads((d / "kb.json").read_text(encoding="utf-8"))
                out.append(
                    {
                        "name": meta.get("name", d.name),
                        "documents": len(meta.get("documents", [])),
                        "chunks": meta.get("chunk_count", 0),
                        "created_at": meta.get("created_at", ""),
                    }
                )
        return out

    async def count(self, kb_name: str) -> int:
        meta = self._load_meta(kb_name)
        return int(meta.get("chunk_count", 0))

    async def dim(self, kb_name: str) -> int:
        meta = self._load_meta(kb_name)
        return int(meta.get("dim", 0) or 0)

    async def add(
        self, kb_name: str, items: list[dict], embeddings: list[list[float]]
    ) -> None:
        if len(items) != len(embeddings):
            raise VectorStoreError("items 与 embeddings 数量不一致")
        self._ensure_kb(kb_name)
        chunks = self._load_chunks(kb_name)
        vectors = self._load_vectors(kb_name)
        vec_list = [np.asarray(v, dtype=np.float32).reshape(1, -1) for v in embeddings]
        # 维度一致性校验：切换 Embedding Provider 后维度变化必须重建索引
        meta = self._load_meta(kb_name)
        old_dim = int(meta.get("dim") or 0)
        new_dim = vec_list[0].shape[1] if vec_list else 0
        if old_dim and new_dim and old_dim != new_dim:
            raise VectorStoreError(
                f"向量维度 {new_dim} 与知识库已索引维度 {old_dim} 不一致："
                f"切换 Embedding Provider 后请重建索引（知识库内如需更换维度）"
            )
        for c, v in zip(items, vec_list):
            chunks.append(c)
            if vectors is None:
                vectors = v
            else:
                vectors = np.vstack([vectors, v])
        self._save_chunks(kb_name, chunks)
        if vectors is not None:
            np.save(self._vectors_path(kb_name), vectors)
        if new_dim:
            meta["dim"] = new_dim
        meta["chunk_count"] = len(chunks)
        # 更新 documents 记录
        by_file: dict[str, dict] = {d.get("file_id"): d for d in meta.get("documents", [])}
        for c in items:
            fid = c.get("file_id")
            if fid and fid not in by_file:
                by_file[fid] = {
                    "file_id": fid,
                    "file_name": c.get("file_name", ""),
                    "chunks": 0,
                    "status": "indexed",
                    "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            if fid:
                by_file[fid]["chunks"] = by_file[fid].get("chunks", 0) + 1
        meta["documents"] = list(by_file.values())
        self._save_meta(kb_name, meta)
        self._cache.pop(kb_name, None)

    async def query(
        self, kb_name: str, query_vector: list[float], top_k: int, score_threshold: float
    ) -> list[dict[str, Any]]:
        chunks = self._load_chunks(kb_name)
        vectors = self._load_vectors(kb_name)
        if not chunks or vectors is None or len(vectors) == 0:
            return []
        q = _norm(query_vector)
        if q.shape[1] != vectors.shape[1]:
            raise VectorStoreError(
                f"查询向量维度 {q.shape[1]} 与知识库已索引维度 {vectors.shape[1]} 不一致："
                f"请确认知识库使用的 Embedding Provider 与索引时一致，或重建索引"
            )
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        sims = (vectors / norms) @ q.T  # (n,1)
        sims = sims.reshape(-1)
        order = np.argsort(-sims)
        out: list[dict[str, Any]] = []
        for idx in order[:top_k]:
            score = float(sims[idx])
            if score < score_threshold:
                continue
            chunk = chunks[int(idx)]
            out.append(
                {
                    "id": chunk.get("id"),
                    "text": chunk.get("text", ""),
                    "file_id": chunk.get("file_id"),
                    "file_name": chunk.get("file_name", ""),
                    "metadata": chunk.get("metadata") or {},
                    "score": round(score, 4),
                }
            )
        return out

    async def delete_documents(self, kb_name: str, file_ids: list[str]) -> None:
        chunks = self._load_chunks(kb_name)
        vectors = self._load_vectors(kb_name)
        target = set(file_ids)
        keep = [c for c in chunks if c.get("file_id") not in target]
        if len(keep) == len(chunks):
            return
        self._save_chunks(kb_name, keep)
        if vectors is not None and keep:
            keep_idx = [i for i, c in enumerate(chunks) if c.get("file_id") not in target]
            np.save(self._vectors_path(kb_name), vectors[keep_idx])
        else:
            vp = self._vectors_path(kb_name)
            if vp.exists():
                vp.unlink()
        meta = self._load_meta(kb_name)
        meta["documents"] = [d for d in meta.get("documents", []) if d.get("file_id") not in target]
        meta["chunk_count"] = len(keep)
        self._save_meta(kb_name, meta)
        self._cache.pop(kb_name, None)

    async def documents(self, kb_name: str) -> list[dict]:
        meta = self._load_meta(kb_name)
        return meta.get("documents", [])

    async def chunks_of_file(self, kb_name: str, file_id: str, page: int = 1, size: int = 20) -> dict:
        chunks = self._load_chunks(kb_name)
        hits = [c for c in chunks if c.get("file_id") == file_id]
        total = len(hits)
        start = (page - 1) * size
        return {"total": total, "page": page, "size": size, "chunks": hits[start : start + size]}


class ChromaHttpStore(BaseVectorStore):
    """最小 Chroma Server REST 客户端（独立部署，进程内无 chromadb 依赖）。"""

    name = "chroma_http"

    def __init__(self, cfg: dict[str, Any]):
        import httpx

        from urllib.parse import quote

        self.base_url = str(cfg.get("base_url", "http://127.0.0.1:8000")).strip().rstrip("/")
        try:
            url = httpx.URL(self.base_url)
        except httpx.InvalidURL as e:
            raise ProviderError("Chroma base_url 无效：必须是包含主机的 http:// 或 https:// 地址") from e
        if url.scheme not in ("http", "https") or not url.host or url.query or url.fragment:
            raise ProviderError("Chroma base_url 无效：必须使用 http:// 或 https://，且不能包含查询参数或片段")
        self.tenant = str(cfg.get("tenant", "default_tenant"))
        self.database = str(cfg.get("database", "default_database"))
        self.prefix = str(cfg.get("collection_prefix", "ragproxy_"))
        self._collections_path = (
            f"api/v2/tenants/{quote(self.tenant, safe='')}/databases/"
            f"{quote(self.database, safe='')}/collections"
        )
        self._client = httpx.AsyncClient(base_url=self.base_url + "/", timeout=30)
        self._col_ids: dict[str, str] = {}

    def _col_path(self, name_or_id: str) -> str:
        from urllib.parse import quote

        return f"{self._collections_path}/{quote(name_or_id, safe='')}"

    async def _request(self, method: str, path: str, **kwargs):
        import httpx

        try:
            resp = await self._client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.TimeoutException as e:
            raise ProviderError("Chroma 服务超时") from e
        except httpx.RequestError as e:
            raise ProviderError("Chroma 服务连接失败，请检查 base_url 和服务状态") from e
        except httpx.HTTPStatusError as e:
            raise ProviderError(
                f"Chroma 服务返回 HTTP {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e

    async def _ensure_col(self, kb: str) -> str:
        col_name = self.prefix + kb
        if col_name in self._col_ids:
            return self._col_ids[col_name]
        try:
            resp = await self._request("GET", self._col_path(col_name))
        except ProviderError as e:
            if e.status_code != 404:
                raise
            resp = await self._request(
                "POST",
                self._collections_path,
                json={
                    "name": col_name,
                    "configuration": {"hnsw": {"space": "cosine"}},
                    "get_or_create": True,
                },
            )
        cid = resp.json()["id"]
        self._col_ids[col_name] = cid
        return cid

    async def create_kb(self, kb_name: str) -> None:
        await self._ensure_col(kb_name)

    async def delete_kb(self, kb_name: str) -> None:
        col_name = self.prefix + kb_name
        try:
            await self._request("DELETE", self._col_path(col_name))
        except ProviderError as e:
            if e.status_code != 404:
                raise
        self._col_ids.pop(col_name, None)

    async def list_kbs(self) -> list[dict[str, Any]]:
        out = []
        offset = 0
        limit = 100
        while True:
            resp = await self._request(
                "GET", self._collections_path, params={"limit": limit, "offset": offset}
            )
            collections = resp.json()
            for c in collections:
                name = c.get("name", "")
                if name.startswith(self.prefix):
                    count_resp = await self._request("GET", self._col_path(c["id"]) + "/count")
                    out.append({"name": name[len(self.prefix) :], "chunks": int(count_resp.json())})
            if len(collections) < limit:
                break
            offset += len(collections)
        return out

    async def count(self, kb_name: str) -> int:
        cid = await self._ensure_col(kb_name)
        resp = await self._request("GET", self._col_path(cid) + "/count")
        return int(resp.json())

    async def dim(self, kb_name: str) -> int:
        await self._ensure_col(kb_name)
        resp = await self._request("GET", self._col_path(self.prefix + kb_name))
        return int(resp.json().get("dimension") or 0)

    async def add(self, kb_name: str, items: list[dict], embeddings: list[list[float]]) -> None:
        if len(items) != len(embeddings):
            raise VectorStoreError("items 与 embeddings 数量不一致")
        if not items:
            return
        cid = await self._ensure_col(kb_name)
        await self._request(
            "POST", self._col_path(cid) + "/add",
            json={
                "ids": [it["id"] for it in items],
                "embeddings": embeddings,
                "documents": [it["text"] for it in items],
                "metadatas": [
                    {
                        **(it.get("metadata") or {}),
                        "file_id": it.get("file_id", ""),
                        "file_name": it.get("file_name", ""),
                    }
                    for it in items
                ],
            },
        )

    async def query(
        self, kb_name: str, query_vector: list[float], top_k: int, score_threshold: float
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        cid = await self._ensure_col(kb_name)
        resp = await self._request(
            "POST", self._col_path(cid) + "/query",
            json={
                "query_embeddings": [query_vector],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            },
        )
        data = resp.json()
        out: list[dict[str, Any]] = []
        ids = data.get("ids", [[]])[0]
        docs = data.get("documents", [[]])[0]
        metas = data.get("metadatas", [[]])[0]
        dists = data.get("distances", [[]])[0]
        for i, did in enumerate(ids):
            dist = float(dists[i])
            sim = 1.0 - dist
            if sim < score_threshold:
                continue
            meta = metas[i] or {}
            out.append(
                {
                    "id": did,
                    "text": docs[i],
                    "file_id": meta.get("file_id"),
                    "file_name": meta.get("file_name", ""),
                    "metadata": meta,
                    "score": round(sim, 4),
                }
            )
        return out

    async def delete_documents(self, kb_name: str, file_ids: list[str]) -> None:
        if not file_ids:
            return
        cid = await self._ensure_col(kb_name)
        await self._request(
            "POST", self._col_path(cid) + "/delete",
            json={"where": {"file_id": {"$in": file_ids}}},
        )

    async def _get_chunks(self, kb_name: str, file_id: str | None = None) -> list[dict]:
        cid = await self._ensure_col(kb_name)
        chunks = []
        limit = 1000
        offset = 0
        while True:
            payload = {"limit": limit, "offset": offset, "include": ["documents", "metadatas"]}
            if file_id is not None:
                payload["where"] = {"file_id": file_id}
            resp = await self._request("POST", self._col_path(cid) + "/get", json=payload)
            data = resp.json()
            ids = data["ids"]
            docs = data.get("documents") or [""] * len(ids)
            metas = data.get("metadatas") or [None] * len(ids)
            for i, did in enumerate(ids):
                meta = metas[i] or {}
                chunks.append({
                    "id": did,
                    "text": docs[i] or "",
                    "file_id": meta.get("file_id"),
                    "file_name": meta.get("file_name", ""),
                    "metadata": meta,
                })
            if len(ids) < limit:
                break
            offset += len(ids)
        return chunks

    async def documents(self, kb_name: str) -> list[dict]:
        by_file: dict[str, dict] = {}
        for chunk in await self._get_chunks(kb_name):
            fid = chunk["file_id"]
            if not fid:
                continue
            if fid not in by_file:
                by_file[fid] = {
                    "file_id": fid, "file_name": chunk["file_name"],
                    "chunks": 0, "status": "indexed", "indexed_at": "",
                }
            by_file[fid]["chunks"] += 1
        return list(by_file.values())

    async def chunks_of_file(self, kb_name: str, file_id: str, page: int = 1, size: int = 20) -> dict:
        chunks = await self._get_chunks(kb_name, file_id)
        chunks.sort(key=lambda c: c["metadata"].get("chunk_index", 0))
        start = (page - 1) * size
        return {"total": len(chunks), "page": page, "size": size, "chunks": chunks[start : start + size]}


def build_vectorstore(cfg: dict[str, Any], data_dir: Path) -> BaseVectorStore:
    vtype = str(cfg.get("vectorstore", {}).get("type", "simple")).lower()
    if vtype == "chroma_http":
        return ChromaHttpStore(cfg.get("vectorstore", {}).get("chroma_http", {}))
    cache_limit = int(cfg.get("vectorstore", {}).get("simple", {}).get("cache_limit", 4))
    return SimpleVectorStore(data_dir / "vectordb", cache_limit=cache_limit)
