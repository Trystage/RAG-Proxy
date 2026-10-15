"""管理 API：模型映射 CRUD（model 名 → 知识库 + upstream）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..i18n import locale_from_request, translate
from .admin_common import get_state, require_admin, save_section, find_index

router = APIRouter()

VALID_MERGE = {"priority", "concat", "rrf"}
VALID_INJECT = {"system_prepend", "system_append", "new_message"}


def _validate(m: dict[str, Any], locale: str = "zh") -> None:
    if not m.get("name"):
        raise HTTPException(status_code=400, detail=translate("model_required", locale))
    merge = m.get("kb_merge") or m.get("kb-merge")
    if merge is not None and merge not in VALID_MERGE:
        raise HTTPException(status_code=400, detail=translate("merge_invalid", locale, values=sorted(VALID_MERGE)))
    inject = m.get("inject_mode") or m.get("inject-mode")
    if inject is not None and inject not in VALID_INJECT:
        raise HTTPException(status_code=400, detail=translate("inject_invalid", locale, values=sorted(VALID_INJECT)))
    kb = m.get("kb")
    if kb is not None and not (isinstance(kb, str) or isinstance(kb, list)):
        raise HTTPException(status_code=400, detail=translate("kb_invalid", locale))
    if not m.get("upstream"):
        raise HTTPException(status_code=400, detail=translate("upstream_required", locale))


@router.get("/admin/mappings")
async def list_mappings(request: Request):
    require_admin(request)
    state = get_state(request)
    return {"items": state.config.raw().get("models", [])}


class MappingBody(BaseModel):
    item: dict[str, Any] = Field(default_factory=dict)


@router.post("/admin/mappings")
async def create_mapping(request: Request, body: MappingBody):
    require_admin(request)
    state = get_state(request)
    items = state.config.raw().get("models", []) or []
    item = body.item
    _validate(item, locale_from_request(request))
    if find_index(items, "name", item["name"]) >= 0:
        raise HTTPException(status_code=400, detail=translate("model_exists", locale_from_request(request), name=item["name"]))
    items.append(item)
    save_section(request, state, "models", items)
    return {"ok": True, "item": item}


@router.put("/admin/mappings/{model_name}")
async def update_mapping(request: Request, model_name: str, body: MappingBody):
    require_admin(request)
    state = get_state(request)
    items = state.config.raw().get("models", []) or []
    idx = find_index(items, "name", model_name)
    if idx < 0:
        raise HTTPException(status_code=404, detail=translate("mapping_not_found", locale_from_request(request)))
    item = body.item
    _validate(item, locale_from_request(request))
    if item.get("name") and item["name"] != model_name:
        raise HTTPException(status_code=400, detail=translate("immutable_model", locale_from_request(request)))
    item["name"] = model_name
    items[idx] = item
    save_section(request, state, "models", items)
    return {"ok": True, "item": item}


@router.delete("/admin/mappings/{model_name}")
async def delete_mapping(request: Request, model_name: str):
    require_admin(request)
    state = get_state(request)
    items = state.config.raw().get("models", []) or []
    idx = find_index(items, "name", model_name)
    if idx < 0:
        raise HTTPException(status_code=404, detail=translate("mapping_not_found", locale_from_request(request)))
    items.pop(idx)
    save_section(request, state, "models", items)
    return {"ok": True}
