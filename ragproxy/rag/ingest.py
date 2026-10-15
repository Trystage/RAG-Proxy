"""文档摄取：保存上传文件 → 提取文本 → 分块 → 远程向量化 → 写入向量库。

- 文件名一律 uuid 重命名，原始文件名记入 manifest（安全要求）
- 支持按知识库重建索引
- 上传/索引任务进度管理
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..providers.embedding import EmbeddingProvider
from ..providers.vectorstore import BaseVectorStore
from .chunking import chunk_text
from ..utils.text_extract import TextExtractError, extract_text

log = logging.getLogger("ragproxy.ingest")

SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff.-]+")


class IngestService:
    def __init__(
        self,
        data_dir: Path,
        config,
        embeds: dict[str, EmbeddingProvider],
        default_embed: EmbeddingProvider | None,
        vs: BaseVectorStore,
        embed_concurrency: int = 4,
    ):
        self.data_dir = data_dir
        self.config = config
        self.embeds = embeds
        self.embed = default_embed
        self.vs = vs
        self.doc_root = data_dir / "documents"
        self.doc_root.mkdir(parents=True, exist_ok=True)
        self.embed_sem = asyncio.Semaphore(max(1, embed_concurrency))
        self.tasks: dict[str, dict[str, Any]] = {}
        self._task_lock = asyncio.Lock()

    # ---------- 知识库级 Embedding Provider ----------
    def _kb_provider_id(self, kb: str) -> str | None:
        for item in self.config.get("knowledge_bases", []) or []:
            if isinstance(item, dict) and item.get("name") == kb:
                pid = item.get("embed_provider")
                return pid if isinstance(pid, str) and pid else None
        return None

    def _embed_for(self, kb: str) -> EmbeddingProvider | None:
        pid = self._kb_provider_id(kb)
        if pid and pid in self.embeds:
            return self.embeds[pid]
        return self.embed

    # ---------- 任务 ----------
    def new_task(self, kb: str, kind: str, total: int = 0) -> str:
        tid = uuid.uuid4().hex[:12]
        self.tasks[tid] = {
            "task_id": tid,
            "kb": kb,
            "kind": kind,
            "status": "running",
            "total": total,
            "done": 0,
            "failed": 0,
            "current": "",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "finished_at": None,
            "error": None,
        }
        return tid

    def update_task(self, tid: str, **kw: Any) -> None:
        t = self.tasks.get(tid)
        if t:
            t.update(kw)

    def finish_task(self, tid: str, status: str = "done", error: str | None = None) -> None:
        t = self.tasks.get(tid)
        if t:
            t["status"] = status
            t["error"] = error
            t["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def get_task(self, tid: str) -> dict[str, Any] | None:
        t = self.tasks.get(tid)
        return dict(t) if t else None

    # ---------- 目录 ----------
    def kb_doc_dir(self, kb: str) -> Path:
        d = self.doc_root / kb
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _manifest_path(self, kb: str) -> Path:
        return self.kb_doc_dir(kb) / "manifest.json"

    def _load_manifest(self, kb: str) -> dict:
        p = self._manifest_path(kb)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_manifest(self, kb: str, manifest: dict) -> None:
        self._manifest_path(kb).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 保存上传文件 ----------
    async def save_upload(self, kb: str, original_name: str, data: bytes) -> dict[str, Any]:
        ext = Path(original_name).suffix.lower()
        file_id = uuid.uuid4().hex
        stored = file_id + ext
        manifest = self._load_manifest(kb)
        manifest.setdefault("files", {})
        manifest["files"][file_id] = {
            "file_id": file_id,
            "original_name": original_name,
            "stored_name": stored,
            "size": len(data),
            "status": "uploaded",
            "chunks": 0,
            "indexed_at": None,
            "error": None,
        }
        self._save_manifest(kb, manifest)
        (self.kb_doc_dir(kb) / stored).write_bytes(data)
        return manifest["files"][file_id]

    def record_file(
        self, kb: str, file_id: str, **kw: Any
    ) -> None:
        manifest = self._load_manifest(kb)
        manifest.setdefault("files", {})
        rec = manifest["files"].setdefault(file_id, {})
        rec.update(kw)
        self._save_manifest(kb, manifest)

    def list_files(self, kb: str) -> list[dict[str, Any]]:
        manifest = self._load_manifest(kb)
        files = manifest.get("files", {})
        return sorted(
            files.values(), key=lambda f: f.get("indexed_at") or f.get("file_id", "")
        )

    def list_all_files(self) -> list[dict[str, Any]]:
        out = []
        if not self.doc_root.exists():
            return out
        for d in sorted(self.doc_root.iterdir()):
            if d.is_dir():
                for f in self.list_files(d.name):
                    f = dict(f)
                    f["kb"] = d.name
                    out.append(f)
        return out

    async def delete_file(self, kb: str, file_id: str) -> bool:
        manifest = self._load_manifest(kb)
        rec = manifest.get("files", {}).get(file_id)
        if rec is None:
            return False
        stored = self.kb_doc_dir(kb) / rec.get("stored_name", "")
        if stored.exists():
            stored.unlink()
        manifest["files"].pop(file_id, None)
        self._save_manifest(kb, manifest)
        await self.vs.delete_documents(kb, [file_id])
        return True

    # ---------- 索引 ----------
    async def index_file(self, kb: str, file_id: str) -> int:
        """对单个已保存文件执行 提取→分块→向量化→入库，返回 chunk 数。"""
        manifest = self._load_manifest(kb)
        rec = manifest.get("files", {}).get(file_id)
        if rec is None:
            raise FileNotFoundError(f"文件记录不存在：{file_id}")
        self.record_file(kb, file_id, status="indexing", error=None)
        path = self.kb_doc_dir(kb) / rec["stored_name"]
        # 文本提取可能耗时，放到线程池
        text = await asyncio.to_thread(extract_text, path)

        rag_cfg = self.config.get("rag", {}) or {}
        chunk_cfg = self.config.get("chunking", {}) or {}
        strategy = str(chunk_cfg.get("strategy", "token"))
        chunk_size = int(chunk_cfg.get("chunk_size", 800))
        overlap = int(chunk_cfg.get("chunk_overlap", 100))

        chunks = chunk_text(text, strategy, chunk_size, overlap)
        if not chunks:
            self.record_file(kb, file_id, status="indexed", chunks=0)
            return 0

        items = []
        for i, c in enumerate(chunks):
            items.append(
                {
                    "id": f"{file_id}:{i}",
                    "text": c,
                    "file_id": file_id,
                    "file_name": rec.get("original_name", rec["stored_name"]),
                    "metadata": {
                        "source": rec.get("original_name", rec["stored_name"]),
                        "chunk_index": i,
                    },
                }
            )
        # 分批向量化（受 embedding 并发限制；按知识库配置的 provider）
        embed = self._embed_for(kb)
        if embed is None:
            raise RuntimeError("该知识库无可用 Embedding Provider")
        batch = 32
        for i in range(0, len(items), batch):
            part = items[i : i + batch]
            async with self.embed_sem:
                vectors = await embed.embed_many(
                    [p["text"] for p in part], concurrency=1
                )
            await self.vs.add(kb, part, vectors)
        self.record_file(
            kb, file_id, status="indexed", chunks=len(chunks),
            indexed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        return len(chunks)

    async def index_files(self, kb: str, file_ids: list[str]) -> dict[str, Any]:
        """后台任务：批量索引。返回任务统计。"""
        stats = {"total": len(file_ids), "ok": 0, "failed": 0, "errors": []}
        for fid in file_ids:
            try:
                n = await self.index_file(kb, fid)
                stats["ok"] += 1
                stats.setdefault("chunks", 0)
                stats["chunks"] = stats.get("chunks", 0) + n
            except TextExtractError as e:
                stats["failed"] += 1
                stats["errors"].append({"file_id": fid, "error": str(e)})
                self.record_file(kb, fid, status="failed", error=str(e))
            except Exception as e:  # noqa: BLE001
                log.exception("索引文件 %s 失败", fid)
                stats["failed"] += 1
                stats["errors"].append({"file_id": fid, "error": str(e)})
                self.record_file(kb, fid, status="failed", error=str(e))
        return stats

    async def run_index_task(self, tid: str, kb: str, file_ids: list[str]) -> None:
        """包装任务进度。"""
        total = len(file_ids)
        self.update_task(tid, total=total)
        done = 0
        for fid in file_ids:
            rec = self._load_manifest(kb).get("files", {}).get(fid, {})
            self.update_task(
                tid, current=rec.get("original_name", fid), done=done
            )
            try:
                await self.index_file(kb, fid)
            except Exception as e:  # noqa: BLE001
                self.update_task(tid, failed=self.tasks[tid]["failed"] + 1)
                self.record_file(kb, fid, status="failed", error=str(e))
            done += 1
            self.update_task(tid, done=done)
        status = "done" if self.tasks[tid]["failed"] == 0 else "partial"
        self.finish_task(tid, status)

    # ---------- 重建索引 ----------
    async def rebuild_kb(self, kb: str) -> str:
        """清空向量库后按 documents/<kb>/ 目录全量重建。"""
        await self.vs.delete_kb(kb)
        await self.vs.create_kb(kb)
        manifest = self._load_manifest(kb)
        file_ids = [fid for fid, rec in manifest.get("files", {}).items()
                    if (self.kb_doc_dir(kb) / rec.get("stored_name", "")).exists()]
        tid = self.new_task(kb, "reindex", len(file_ids))
        asyncio.create_task(self._run_rebuild(tid, kb, file_ids))
        return tid

    async def _run_rebuild(self, tid: str, kb: str, file_ids: list[str]) -> None:
        manifest = self._load_manifest(kb)
        for rec in manifest.get("files", {}).values():
            rec["status"] = "uploaded"
            rec["chunks"] = 0
        self._save_manifest(kb, manifest)
        await self.run_index_task(tid, kb, file_ids)

    # ---------- 检索测试 ----------
    async def test_query(self, kb: str, query: str, top_k: int = 5, threshold: float = 0.0) -> list[dict]:
        embed = self._embed_for(kb)
        if embed is None:
            raise RuntimeError("该知识库无可用 Embedding Provider")
        vec = await embed.embed_one(query)
        return await self.vs.query(kb, vec, top_k=top_k, score_threshold=threshold)
