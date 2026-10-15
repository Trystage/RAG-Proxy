"""管理 API：模型映射 CRUD（model 名 → 知识库 + upstream）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .admin_common import get_state, require_admin, save_section, find_index

router = APIRouter()

VALID_MERGE = {"priority", "concat", "rrf"}
VALID_INJECT = {"system_prepend", "system_append", "new_message"}


def _validate(m: dict[str, Any]) -> None:
    if not m.get("name"):
        raise HTTPException(status_code=400, detail="模型名不能为空")
    merge = m.get("kb_merge") or m.get("kb-merge")
    if merge is not None and merge not in VALID_MERGE:
        raise HTTPException(status_code=400, detail=f"kb_merge 仅支持 {sorted(VALID_MERGE)}")
    inject = m.get("inject_mode") or m.get("inject-mode")
    if inject is not None and inject not in VALID_INJECT:
        raise HTTPException(status_code=400, detail=f"inject_mode 仅支持 {sorted(VALID_INJECT)}")
    kb = m.get("kb")
    if kb is not None and not (isinstance(kb, str) or isinstance(kb, list)):
        raise HTTPException(status_code=400, detail="kb 必须是字符串或字符串数组")
    if not m.get("upstream"):
        raise HTTPException(status_code=400, detail="必须指定 upstream")


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
    _validate(item)
    if find_index(items, "name", item["name"]) >= 0:
        raise HTTPException(status_code=400, detail=f"模型名已存在：{item['name']}")
    items.append(item)
    save_section(state, "models", items)
    return {"ok": True, "item": item}


@router.put("/admin/mappings/{model_name}")
async def update_mapping(request: Request, model_name: str, body: MappingBody):
    require_admin(request)
    state = get_state(request)
    items = state.config.raw().get("models", []) or []
    idx = find_index(items, "name", model_name)
    if idx < 0:
        raise HTTPException(status_code=404, detail="映射不存在")
    item = body.item
    _validate(item)
    if item.get("name") and item["name"] != model_name:
        raise HTTPException(status_code=400, detail="不允许修改模型名（请删除后重建）")
    item["name"] = model_name
    items[idx] = item
    save_section(state, "models", items)
    return {"ok": True, "item": item}


@router.delete("/admin/mappings/{model_name}")
async def delete_mapping(request: Request, model_name: str):
    require_admin(request)
    state = get_state(request)
    items = state.config.raw().get("models", []) or []
    idx = find_index(items, "name", model_name)
    if idx < 0:
        raise HTTPException(status_code=404, detail="映射不存在")
    items.pop(idx)
    save_section(state, "models", items)
    return {"ok": True}
