"""RAG 检索编排：模型解析、查询提取、多知识库合并（priority/concat/rrf）、重排、注入。"""
from __future__ import annotations

import time
from typing import Any

from ..providers.base import ProviderError
from ..providers.embedding import EmbeddingProvider
from ..providers.reranker import ReRankerProvider
from ..providers.vectorstore import BaseVectorStore
from .inject import apply_inject_mode, build_context_text

RRF_K = 60


class RAGOrchestrator:
    def __init__(
        self,
        config,
        embeds: dict[str, EmbeddingProvider],
        default_embed: EmbeddingProvider | None,
        reranker: ReRankerProvider | None,
        vectorstore: BaseVectorStore,
        embed_concurrency: int = 4,
    ):
        self.config = config
        self.embeds = embeds
        self.embed = default_embed
        self.reranker = reranker
        self.vs = vectorstore
        self.embed_concurrency = embed_concurrency

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

    # ---------- 模型映射 ----------
    def resolve_model(self, model_name: str) -> dict[str, Any] | None:
        models = self.config.get("models", []) or []
        for m in models:
            if m.get("name") == model_name:
                return dict(m)
        return None

    def list_models(self) -> list[dict[str, Any]]:
        models = self.config.get("models", []) or []
        return [
            {"id": m["name"], "object": "model", "owned_by": "rag-proxy"}
            for m in models
            if m.get("name")
        ]

    def upstream_of(self, upstream_id: str) -> dict[str, Any] | None:
        for u in self.config.get("upstreams", []) or []:
            if u.get("id") == upstream_id:
                return dict(u)
        return None

    def kb_names(self, mapping: dict[str, Any]) -> list[str]:
        kb = mapping.get("kb")
        if kb is None:
            return []
        if isinstance(kb, str):
            return [kb] if kb.strip() else []
        return [k for k in kb if isinstance(k, str) and k.strip()]

    # ---------- 查询提取 ----------
    def extract_query(self, messages: list[dict[str, Any]], source: str = "last_user") -> str:
        source = source or "last_user"
        if source == "all_user":
            return "\n".join(
                str(m.get("content", ""))
                for m in messages
                if m.get("role") == "user" and m.get("content")
            )
        if source == "last_message":
            if messages:
                return str(messages[-1].get("content", ""))
            return ""
        # last_user
        for m in reversed(messages):
            if m.get("role") == "user":
                return str(m.get("content", ""))
        return ""

    # ---------- 检索 ----------
    async def retrieve(
        self, query: str, mapping: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """返回 (chunks, stats)。stats 含各 kb 命中数、耗时、是否 rerank。"""
        started = time.perf_counter()
        kb_list = self.kb_names(mapping)
        if not kb_list or not query.strip():
            return [], {"kb_hits": {}, "retrieve_ms": 0.0, "reranked": False}

        rag_cfg = self.config.get("rag", {}) or {}
        top_k = int(mapping.get("top_k") or rag_cfg.get("top_k", 5))
        threshold = float(mapping.get("score_threshold") or rag_cfg.get("score_threshold", 0.7))
        merge = str(mapping.get("kb_merge") or mapping.get("kb-merge") or "priority").lower()
        rerank_enabled = bool(mapping.get("rerank", False))
        rerank_top_k = int(rag_cfg.get("rerank_top_k", 10))

        query_vec = None
        kb_hits: dict[str, int] = {}
        per_kb: list[list[dict[str, Any]]] = []

        for kb in kb_list:
            try:
                embed = self._embed_for(kb)
                if embed is None:
                    raise ProviderError("该知识库无可用 Embedding Provider")
                query_vec = await embed.embed_one(query)
                results = await self.vs.query(kb, query_vec, top_k=rerank_top_k if rerank_enabled else top_k, score_threshold=threshold)
            except Exception as e:  # noqa: BLE001 - 单个 kb 故障不阻断其他 kb
                results = []
                kb_hits[kb] = -1
                import logging

                logging.getLogger("ragproxy.rag").warning("Knowledge base %s retrieval failed: %s", kb, e)
            per_kb.append(results)
            kb_hits[kb] = len(results)
            if merge == "priority" and len(results) >= top_k:
                break

        if not any(per_kb):
            elapsed = (time.perf_counter() - started) * 1000
            return [], {"kb_hits": kb_hits, "retrieve_ms": round(elapsed, 2), "reranked": False}

        if merge == "rrf":
            merged = self._rrf_merge(per_kb, top_k)
        elif merge == "concat":
            merged = self._concat_merge(per_kb, top_k)
        else:  # priority
            merged = self._priority_merge(per_kb, top_k)

        reranked = False
        if rerank_enabled and self.reranker is not None and merged:
            try:
                docs = [c.get("text", "") for c in merged]
                scored = await self.reranker.rerank(query, docs, top_n=top_k)
                ordered = [merged[idx] for idx, _ in scored if idx < len(merged)]
                # 保留 rerank 分数
                for idx, score in scored:
                    if idx < len(merged):
                        merged[idx]["score"] = round(score, 4)
                merged = ordered[:top_k]
                reranked = True
            except ProviderError as e:
                import logging

                logging.getLogger("ragproxy.rag").warning("ReRanker call failed; using original order: %s", e)

        elapsed = (time.perf_counter() - started) * 1000
        stats = {
            "kb_hits": kb_hits,
            "retrieve_ms": round(elapsed, 2),
            "reranked": reranked,
        }
        return merged, stats

    def _priority_merge(self, per_kb: list[list[dict]], top_k: int) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for results in per_kb:
            for r in results:
                if r.get("id") in seen:
                    continue
                seen.add(r.get("id"))
                out.append(r)
            if len(out) >= top_k:
                break
        return out[:top_k]

    def _concat_merge(self, per_kb: list[list[dict]], top_k: int) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for results in per_kb:
            for r in results:
                if r.get("id") in seen:
                    continue
                seen.add(r.get("id"))
                out.append(r)
        out.sort(key=lambda x: x.get("score", 0), reverse=True)
        return out[:top_k]

    def _rrf_merge(self, per_kb: list[list[dict]], top_k: int) -> list[dict]:
        scores: dict[str, float] = {}
        items: dict[str, dict] = {}
        for results in per_kb:
            for rank, r in enumerate(results, 1):
                key = r.get("id")
                if key is None:
                    continue
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
                items[key] = r
        ranked = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
        out = [items[k] for k in ranked[:top_k]]
        for r in out:
            r["score"] = round(scores.get(r.get("id"), 0.0), 4)
            r["rrf"] = True
        return out

    # ---------- 注入 ----------
    def build_injected(
        self,
        messages: list[dict[str, Any]],
        query: str,
        chunks: list[dict[str, Any]],
        mapping: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rag_cfg = self.config.get("rag", {}) or {}
        template = rag_cfg.get("inject_template", "")
        max_tokens = int(rag_cfg.get("max_context_tokens", 2000))
        injected, context = build_context_text(chunks, template, query, max_tokens)
        mode = str(
            (mapping or {}).get("inject_mode")
            or (mapping or {}).get("inject-mode")
            or rag_cfg.get("inject_mode")
            or "system_prepend"
        ).lower()
        return apply_inject_mode(messages, injected, mode), {
            "injected": injected,
            "context": context,
            "chunk_count": len(chunks),
            "inject_mode": mode,
        }
