"""全局应用状态：装配 Provider、编排器、摄取服务与请求日志。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .config import AppSettings, ConfigManager, load_or_create_secret_key
from .providers.base import ProviderError  # noqa: F401
from .providers.embedding import EmbeddingProvider
from .providers.llm import LLMProvider
from .providers.reranker import ReRankerProvider
from .providers.vectorstore import BaseVectorStore, build_vectorstore
from .rag.ingest import IngestService
from .rag.orchestrator import RAGOrchestrator
from .services.request_log import RequestLogger

log = logging.getLogger("ragproxy.state")


class AppState:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.config = ConfigManager(settings)
        self.secret_key = load_or_create_secret_key(settings, settings.secret_key)
        self.llm_providers: dict[str, LLMProvider] = {}
        self.embeds: dict[str, EmbeddingProvider] = {}
        self.embed: EmbeddingProvider | None = None
        self.reranker: ReRankerProvider | None = None
        self.vs: BaseVectorStore | None = None
        self.orch: RAGOrchestrator | None = None
        self.ingest: IngestService | None = None
        self.reqlog: RequestLogger | None = None
        self.upload_sem: asyncio.Semaphore | None = None
        self.ready = False
        self._init_components()

    # ---------- 初始化 ----------
    def _init_components(self) -> None:
        cfg = self.config.raw()
        data_dir: Path = self.settings.data_dir

        # 热重载不关闭旧连接，已开始的请求仍持有原 provider。
        self.llm_providers = {}

        upstreams = cfg.get("upstreams", []) or []
        for u in upstreams:
            try:
                self.llm_providers[u["id"]] = LLMProvider(u)
            except Exception as e:  # noqa: BLE001
                log.warning("LLM provider %s initialization failed: %s", u.get("id"), e)

        rag_cfg = cfg.get("rag", {}) or {}
        embeddings = cfg.get("embeddings", []) or []
        # 装配全部 embedding provider，支持知识库级指定
        self.embeds: dict[str, EmbeddingProvider] = {}
        for e in embeddings:
            if not e.get("id"):
                continue
            try:
                self.embeds[e["id"]] = EmbeddingProvider(
                    e, data_dir / "cache" / "embedding"
                )
            except Exception as ex:  # noqa: BLE001
                log.warning("Embedding provider %s initialization failed: %s", e.get("id"), ex)
        want = rag_cfg.get("embed_provider")
        self.embed = (
            self.embeds.get(want)
            or (next(iter(self.embeds.values())) if self.embeds else None)
        )

        rerankers = cfg.get("rerankers", []) or []
        rerank_cfg = None
        if rerankers:
            want = rag_cfg.get("rerank_provider")
            rerank_cfg = next((r for r in rerankers if r.get("id") == want), rerankers[0])
        self.reranker = (
            ReRankerProvider(rerank_cfg, data_dir / "cache" / "rerank")
            if rerank_cfg
            else None
        )

        self.vs = build_vectorstore(cfg, data_dir)

        embed_conc = int(cfg.get("upload", {}).get("max_embed_concurrency", 4))
        self.orch = RAGOrchestrator(
            self.config, self.embeds, self.embed, self.reranker, self.vs, embed_conc
        ) if self.embed else None

        self.ingest = IngestService(
            data_dir, self.config, self.embeds, self.embed, self.vs, embed_conc
        ) if self.embed else None

        logging_cfg = cfg.get("logging", {}) or {}
        db_rel = str(logging_cfg.get("request_log_db", "logs/requests.db"))
        self.reqlog = RequestLogger(
            data_dir / db_rel, int(logging_cfg.get("keep_days", 90))
        )

        self.upload_sem = asyncio.Semaphore(
            max(1, int(cfg.get("upload", {}).get("max_upload_concurrency", 4)))
        )
        self.ready = True

    # ---------- 热重载 ----------
    def reload(self) -> dict[str, Any]:
        cfg = self.config.load(force=True)
        self._init_components()
        return {"ok": True, "message": "Configuration reloaded"}

    # ---------- Provider 查找 ----------
    def get_llm(self, upstream_id: str) -> LLMProvider:
        p = self.llm_providers.get(upstream_id)
        if p is None:
            raise ProviderError(f"未找到 upstream：{upstream_id}")
        return p

    def get_embed(self, provider_id: str | None = None) -> EmbeddingProvider | None:
        """按 provider id 取 Embedding；未指定或不存在时回退到全局默认。"""
        if provider_id:
            p = self.embeds.get(provider_id)
            if p is not None:
                return p
        return self.embed

    # ---------- 运行期指标 ----------
    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "models": len(self.config.get("models", []) or []),
            "embedding": self.embed is not None,
            "vectorstore": self.vs.name if self.vs else None,
            "ready": self.ready,
        }
