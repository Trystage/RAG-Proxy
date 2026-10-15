"""管理 API：Providers（LLM / Embedding / ReRanker / VectorStore）CRUD 与连接测试。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..providers.embedding import EmbeddingProvider
from ..providers.llm import LLMProvider
from ..providers.reranker import ReRankerProvider
from ..config import is_masked
from ..i18n import locale_from_request, translate
from .admin_common import get_state, require_admin, save_section, find_index

router = APIRouter()

SECTION_BY_KIND = {
    "llm": "upstreams",
    "embedding": "embeddings",
    "reranker": "rerankers",
}

KIND_BY_SECTION = {v: k for k, v in SECTION_BY_KIND.items()}


def _section(request: Request, kind: str) -> str:
    sec = SECTION_BY_KIND.get(kind)
    if sec is None:
        raise HTTPException(status_code=400, detail=translate("unknown_provider_type", locale_from_request(request), kind=kind))
    return sec


@router.get("/admin/providers/{kind}")
async def list_providers(request: Request, kind: str):
    require_admin(request)
    state = get_state(request)
    sec = _section(request, kind)
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
    sec = _section(request, kind)
    items = state.config.raw().get(sec, []) or []
    item = body.item
    if not item.get("id"):
        raise HTTPException(status_code=400, detail=translate("missing_id", locale_from_request(request)))
    if find_index(items, "id", item["id"]) >= 0:
        raise HTTPException(status_code=400, detail=translate("duplicate_id", locale_from_request(request), id=item["id"]))
    if is_masked(item.get("api_key")):
        raise HTTPException(status_code=400, detail=translate("masked_key_input", locale_from_request(request)))
    items.append(item)
    save_section(request, state, sec, items)
    return {"ok": True, "item": item}


@router.put("/admin/providers/{kind}/{provider_id}")
async def update_provider(request: Request, kind: str, provider_id: str, body: ProviderBody):
    require_admin(request)
    state = get_state(request)
    sec = _section(request, kind)
    items = state.config.raw().get(sec, []) or []
    idx = find_index(items, "id", provider_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail=translate("provider_not_found", locale_from_request(request)))
    item = body.item
    if item.get("id") and item["id"] != provider_id:
        raise HTTPException(status_code=400, detail=translate("immutable_id", locale_from_request(request)))
    item["id"] = provider_id
    # API Key 处理：脱敏占位（"****" / mask_secret 形态）不得写入配置
    old_key = items[idx].get("api_key")
    new_key = item.get("api_key")
    if is_masked(old_key):
        # 旧值本身已是脱敏占位（历史污染）：必须用新值覆盖，且新值不能仍是脱敏占位
        if is_masked(new_key) or not new_key:
            raise HTTPException(
                status_code=400,
                detail=translate("masked_key_invalid", locale_from_request(request), provider=provider_id),
            )
        item["api_key"] = new_key
    elif is_masked(new_key) or new_key in (None, ""):
        # 前端回传脱敏占位（**** / sk-****xxx）或空值时保留原真实密钥，防止误覆盖
        if is_masked(new_key) and not old_key:
            raise HTTPException(
                status_code=400,
                detail=translate("masked_key_input", locale_from_request(request)),
            )
        item["api_key"] = old_key
    items[idx] = item
    save_section(request, state, sec, items)
    return {"ok": True, "item": item}


@router.delete("/admin/providers/{kind}/{provider_id}")
async def delete_provider(request: Request, kind: str, provider_id: str):
    require_admin(request)
    state = get_state(request)
    sec = _section(request, kind)
    items = state.config.raw().get(sec, []) or []
    idx = find_index(items, "id", provider_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail=translate("provider_not_found", locale_from_request(request)))
    items.pop(idx)
    save_section(request, state, sec, items)
    return {"ok": True}


@router.post("/admin/providers/{kind}/{provider_id}/test")
async def test_provider(request: Request, kind: str, provider_id: str):
    require_admin(request)
    state = get_state(request)
    sec = _section(request, kind)
    items = state.config.raw().get(sec, []) or []
    idx = find_index(items, "id", provider_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail=translate("provider_not_found", locale_from_request(request)))
    cfg = items[idx]
    try:
        if kind == "llm":
            provider = LLMProvider(cfg)
        elif kind == "embedding":
            provider = EmbeddingProvider(cfg)
        elif kind == "reranker":
            provider = ReRankerProvider(cfg)
        else:
            raise HTTPException(status_code=400, detail=translate("unsupported_test", locale_from_request(request)))
        result = await provider.test()
        locale = locale_from_request(request)
        if result.get("ok"):
            raw = str(result.get("message", ""))
            details = raw[len("Connection successful"):] if raw.startswith("Connection successful") else ""
            result["message"] = translate("test_success", locale, details=details)
        else:
            raw = str(result.get("message", ""))
            error = raw.split(":", 1)[1].strip() if ":" in raw else raw
            result["message"] = translate("test_failure", locale, error=error)
        if hasattr(provider, "aclose"): 
            await provider.aclose()
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "message": translate("test_exception", locale_from_request(request), error=e)}
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

    write_config(request, state, cfg)
    return {"ok": True, "item": body.item}
