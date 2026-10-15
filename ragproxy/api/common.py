"""开放接口公共流程：模型解析 → 检索 → 注入 → 转发。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import ProviderError
from ..providers.llm import LLMProvider
from ..state import AppState

log = logging.getLogger("ragproxy.pipeline")


class ModelNotFoundError(RuntimeError):
    pass


class FormatMismatchError(RuntimeError):
    pass


@dataclass
class PipelineResult:
    messages: list[dict[str, Any]]
    mapping: dict[str, Any]
    provider: LLMProvider
    hit: bool = False
    chunks: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    inject_info: dict[str, Any] = field(default_factory=dict)
    query: str = ""


async def run_pipeline(
    state: AppState,
    messages: list[dict[str, Any]],
    model_name: str,
    fmt: str,
) -> PipelineResult:
    """执行 RAG 流水线；检索/向量库故障时降级为透传，不阻断对话。"""
    orch = state.orch
    if orch is None:
        raise ProviderError("RAG 服务未就绪（缺少 Embedding Provider 配置）")

    mapping = orch.resolve_model(model_name)
    if mapping is None:
        raise ModelNotFoundError(f"未知模型：{model_name}")

    upstream_cfg = orch.upstream_of(str(mapping.get("upstream", "")))
    if upstream_cfg is None:
        raise ProviderError(f"模型 {model_name} 关联的 upstream 不存在")
    provider = state.get_llm(str(upstream_cfg.get("id", "")))
    if provider.fmt != fmt:
        raise FormatMismatchError(
            f"模型 {model_name} 的 upstream 类型为 {provider.fmt}，"
            f"与请求格式 {fmt} 不匹配（本服务不做格式转换）"
        )

    result = PipelineResult(
        messages=list(messages), mapping=mapping, provider=provider
    )

    kb_names = orch.kb_names(mapping)
    if not kb_names:
        return result

    rag_cfg = state.config.get("rag", {}) or {}
    query = orch.extract_query(messages, rag_cfg.get("query_source", "last_user"))
    result.query = query

    try:
        chunks, stats = await orch.retrieve(query, mapping)
        if chunks:
            injected_messages, inject_info = orch.build_injected(
                messages, query, chunks, mapping
            )
            result.messages = injected_messages
            result.inject_info = inject_info
            result.chunks = chunks
        result.stats = stats
        result.hit = bool(chunks)
    except Exception as e:  # noqa: BLE001
        # 检索链路故障 → 降级透传
        log.warning("检索链路故障，降级透传：%s", e)
        result.stats = {"degraded": str(e)}

    return result


def query_preview(query: str, state: AppState, limit: int = 100) -> str:
    """按安全配置决定 query 记录长度。"""
    sec = state.config.get("security", {}) or {}
    full = bool(sec.get("log_query", False)) or state.settings.log_level == "DEBUG"
    if full:
        return query
    q = query.replace("\r", " ").replace("\n", " ")
    return q[:limit]


def should_log_response(state: AppState) -> bool:
    """默认不记录完整响应，仅 security.log_response=true 或 DEBUG 级别时记录。"""
    sec = state.config.get("security", {}) or {}
    return bool(sec.get("log_response", False)) or state.settings.log_level == "DEBUG"


def extract_openai_response(data: dict[str, Any]) -> str:
    try:
        parts = []
        for ch in data.get("choices", []) or []:
            msg = ch.get("message") or {}
            c = msg.get("content")
            if isinstance(c, str) and c:
                parts.append(c)
        return "\n".join(parts)[:4000]
    except Exception:  # noqa: BLE001
        return ""


def extract_anthropic_response(data: dict[str, Any]) -> str:
    try:
        parts = []
        for block in data.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)[:4000]
    except Exception:  # noqa: BLE001
        return ""
