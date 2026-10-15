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

        self.base_url = str(cfg.get("base_url", "http://127.0.0.1:8000")).rstrip("/")
        self.tenant = str(cfg.get("tenant", "default_tenant"))
        self.database = str(cfg.get("database", "default_database"))
        self.prefix = str(cfg.get("collection_prefix", "ragproxy_"))
        self._client = httpx.AsyncClient(timeout=30)
        self._col_ids: dict[str, str] = {}

    async def _col_path(self, kb: str) -> str:
        return f"/api/v1/tenants/{self.tenant}/databases/{self.database}/collections"

    async def _ensure_col(self, kb: str) -> str:
        col_name = self.prefix + kb
        if col_name in self._col_ids:
            return self._col_ids[col_name]
        path = await self._col_path(kb)
        resp = await self._client.get(path)
        resp.raise_for_status()
        for c in resp.json():
            if c.get("name") == col_name:
                self._col_ids[col_name] = c["id"]
                return c["id"]
        resp = await self._client.post(
            path,
            json={
                "name": col_name,
                "configuration": {"hnsw_configuration": {"space": "cosine"}},
            },
        )
        resp.raise_for_status()
        cid = resp.json()["id"]
        self._col_ids[col_name] = cid
        return cid

    async def create_kb(self, kb_name: str) -> None:
        await self._ensure_col(kb_name)

    async def delete_kb(self, kb_name: str) -> None:
        cid = await self._ensure_col(kb_name)
        resp = await self._client.delete(f"/api/v1/collections/{cid}")
        resp.raise_for_status()
        self._col_ids.pop(self.prefix + kb_name, None)

    async def list_kbs(self) -> list[dict[str, Any]]:
        resp = await self._client.get(
            f"/api/v1/tenants/{self.tenant}/databases/{self.database}/collections"
        )
        resp.raise_for_status()
        out = []
        for c in resp.json():
            name = c.get("name", "")
            if name.startswith(self.prefix):
                out.append({"name": name[len(self.prefix) :], "chunks": c.get("count", 0)})
        return out

    async def count(self, kb_name: str) -> int:
        cid = await self._ensure_col(kb_name)
        resp = await self._client.get(f"/api/v1/collections/{cid}")
        resp.raise_for_status()
        return int(resp.json().get("count", 0))

    async def add(self, kb_name: str, items: list[dict], embeddings: list[list[float]]) -> None:
        cid = await self._ensure_col(kb_name)
        resp = await self._client.post(
            f"/api/v1/collections/{cid}/add",
            json={
                "ids": [it["id"] for it in items],
                "embeddings": embeddings,
                "documents": [it["text"] for it in items],
                "metadatas": [
                    {
                        "file_id": it.get("file_id", ""),
                        "file_name": it.get("file_name", ""),
                        **(it.get("metadata") or {}),
                    }
                    for it in items
                ],
            },
        )
        resp.raise_for_status()

    async def query(
        self, kb_name: str, query_vector: list[float], top_k: int, score_threshold: float
    ) -> list[dict[str, Any]]:
        cid = await self._ensure_col(kb_name)
        resp = await self._client.post(
            f"/api/v1/collections/{cid}/query",
            json={
                "query_embeddings": [query_vector],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        out: list[dict[str, Any]] = []
        ids = data.get("ids", [[]])[0]
        docs = data.get("documents", [[]])[0]
        metas = data.get("metadatas", [[]])[0]
        dists = data.get("distances", [[]])[0]
        for i, did in enumerate(ids):
            dist = float(dists[i])
            sim = 1.0 / (1.0 + max(dist, 0.0))
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
        cid = await self._ensure_col(kb_name)
        resp = await self._client.post(
            f"/api/v1/collections/{cid}/delete",
            json={"where": {"file_id": {"$in": file_ids}}},
        )
        resp.raise_for_status()


def build_vectorstore(cfg: dict[str, Any], data_dir: Path) -> BaseVectorStore:
    vtype = str(cfg.get("vectorstore", {}).get("type", "simple")).lower()
    if vtype == "chroma_http":
        return ChromaHttpStore(cfg.get("vectorstore", {}).get("chroma_http", {}))
    cache_limit = int(cfg.get("vectorstore", {}).get("simple", {}).get("cache_limit", 4))
    return SimpleVectorStore(data_dir / "vectordb", cache_limit=cache_limit)
