"""管理 API：Providers（LLM / Embedding / ReRanker / VectorStore）CRUD 与连接测试。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..providers.embedding import EmbeddingProvider
from ..providers.llm import LLMProvider
from ..providers.reranker import ReRankerProvider
from ..config import is_masked
from .admin_common import get_state, require_admin, save_section, find_index

router = APIRouter()

SECTION_BY_KIND = {
    "llm": "upstreams",
    "embedding": "embeddings",
    "reranker": "rerankers",
}

KIND_BY_SECTION = {v: k for k, v in SECTION_BY_KIND.items()}


def _section(kind: str) -> str:
    sec = SECTION_BY_KIND.get(kind)
    if sec is None:
        raise HTTPException(status_code=400, detail=f"未知 Provider 类型：{kind}")
    return sec


@router.get("/admin/providers/{kind}")
async def list_providers(request: Request, kind: str):
    require_admin(request)
    state = get_state(request)
    sec = _section(kind)
    items = state.config.sanitized().get(sec, [])
    # 标注已被脱敏占位污染（config 里存的不是真实密钥）的条目
    raw_items = state.config.raw().get(sec, [])
    raw_by_id = {i.get("id"): i.get("api_key") for i in raw_items}
    for it in items:
        raw_key = raw_by_id.get(it.get("id"))
        if is_masked(raw_key):
            it["api_key_invalid"] = True
    return {"kind": kind, "items": items}


class ProviderBody(BaseModel):
    item: dict[str, Any] = Field(default_factory=dict)


@router.post("/admin/providers/{kind}")
async def create_provider(request: Request, kind: str, body: ProviderBody):
    require_admin(request)
    state = get_state(request)
    sec = _section(kind)
    items = state.config.raw().get(sec, []) or []
    item = body.item
    if not item.get("id"):
        raise HTTPException(status_code=400, detail="缺少 id")
    if find_index(items, "id", item["id"]) >= 0:
        raise HTTPException(status_code=400, detail=f"id 已存在：{item['id']}")
    if is_masked(item.get("api_key")):
        raise HTTPException(status_code=400, detail="API Key 不能是脱敏占位符，请填写真实密钥")
    items.append(item)
    save_section(state, sec, items)
    return {"ok": True, "item": item}


@router.put("/admin/providers/{kind}/{provider_id}")
async def update_provider(request: Request, kind: str, provider_id: str, body: ProviderBody):
    require_admin(request)
    state = get_state(request)
    sec = _section(kind)
    items = state.config.raw().get(sec, []) or []
    idx = find_index(items, "id", provider_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    item = body.item
    if item.get("id") and item["id"] != provider_id:
        raise HTTPException(status_code=400, detail="不允许修改 id")
    item["id"] = provider_id
    # API Key 处理：脱敏占位（"****" / mask_secret 形态）不得写入配置
    old_key = items[idx].get("api_key")
    new_key = item.get("api_key")
    if is_masked(old_key):
        # 旧值本身已是脱敏占位（历史污染）：必须用新值覆盖，且新值不能仍是脱敏占位
        if is_masked(new_key) or not new_key:
            raise HTTPException(
                status_code=400,
                detail="该 Provider 的 API Key 已损坏（配置中仅存脱敏占位符），请重新输入真实 API Key 后再保存",
            )
        item["api_key"] = new_key
    elif is_masked(new_key) or new_key in (None, ""):
        # 前端回传脱敏占位（**** / sk-****xxx）或空值时保留原真实密钥，防止误覆盖
        if is_masked(new_key) and not old_key:
            raise HTTPException(
                status_code=400,
                detail="API Key 不能是脱敏占位符，请填写真实密钥",
            )
        item["api_key"] = old_key
    items[idx] = item
    save_section(state, sec, items)
    return {"ok": True, "item": item}


@router.delete("/admin/providers/{kind}/{provider_id}")
async def delete_provider(request: Request, kind: str, provider_id: str):
    require_admin(request)
    state = get_state(request)
    sec = _section(kind)
    items = state.config.raw().get(sec, []) or []
    idx = find_index(items, "id", provider_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    items.pop(idx)
    save_section(state, sec, items)
    return {"ok": True}


@router.post("/admin/providers/{kind}/{provider_id}/test")
async def test_provider(request: Request, kind: str, provider_id: str):
    require_admin(request)
    state = get_state(request)
    sec = _section(kind)
    items = state.config.raw().get(sec, []) or []
    idx = find_index(items, "id", provider_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    cfg = items[idx]
    try:
        if kind == "llm":
            provider = LLMProvider(cfg)
        elif kind == "embedding":
            provider = EmbeddingProvider(cfg)
        elif kind == "reranker":
            provider = ReRankerProvider(cfg)
        else:
            raise HTTPException(status_code=400, detail="不支持测试")
        result = await provider.test()
        if hasattr(provider, "aclose"):
            await provider.aclose()
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "message": f"测试异常：{e}"}
    return result


# ---------- VectorStore ----------
@router.get("/admin/providers/vectorstore")
async def get_vectorstore(request: Request):
    require_admin(request)
    state = get_state(request)
    vs = state.config.raw().get("vectorstore", {})
    return {"item": vs}


@router.put("/admin/providers/vectorstore")
async def update_vectorstore(request: Request, body: ProviderBody):
    require_admin(request)
    state = get_state(request)
    cfg = state.config.raw()
    cfg["vectorstore"] = body.item
    from .admin_common import write_config

    write_config(state, cfg)
    return {"ok": True, "item": body.item}
