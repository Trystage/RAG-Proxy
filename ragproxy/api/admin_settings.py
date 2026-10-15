"""管理 API：设置（查看 / 编辑配置 YAML，热重载）。"""
from __future__ import annotations

import copy

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .admin_common import get_state, require_admin
from ..config import is_masked

router = APIRouter()


@router.get("/admin/settings")
async def get_settings(request: Request):
    require_admin(request)
    state = get_state(request)
    return {"config": state.config.sanitized()}


@router.get("/admin/settings/raw")
async def get_settings_raw(request: Request):
    require_admin(request)
    state = get_state(request)
    # 必须深拷贝：直接返回 raw() 引用并原地脱敏会污染服务内存配置，
    # 导致后续保存把 "****" 写回磁盘（API Key 全部失效的根因）
    raw = copy.deepcopy(state.config.raw())
    raw.pop("_mtime", None)
    # api_key 脱敏
    for section in ("upstreams", "embeddings", "rerankers"):
        for item in raw.get(section, []) or []:
            if isinstance(item, dict) and item.get("api_key"):
                item["api_key"] = "****"
    text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return {"content": text}


def _restore_secrets(state, old: dict, new: dict) -> None:
    """api_key 为脱敏占位（**** / sk-****xxx）或缺失时，保留旧密钥；旧密钥已损坏时强制要求新值。"""
    for section in ("upstreams", "embeddings", "rerankers"):
        old_items = {i.get("id"): i for i in (old.get(section) or [])}
        for item in new.get(section) or []:
            if not isinstance(item, dict):
                continue
            new_key = item.get("api_key")
            old_item = old_items.get(item.get("id"))
            old_key = (old_item or {}).get("api_key") if old_item else None
            if is_masked(old_key):
                # 旧密钥已损坏（历史写入过脱敏占位）：不允许继续写脱敏/空值
                if is_masked(new_key) or not new_key:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider「{item.get('id')}」的 API Key 已损坏（配置中仅存脱敏占位符），请重新输入真实 API Key 后再保存",
                    )
                continue  # new_key 为真实值，直接使用
            if is_masked(new_key) or new_key in (None, ""):
                if old_key:
                    item["api_key"] = old_key
                elif is_masked(new_key):
                    # 无旧值可恢复且提交的是脱敏占位：拒绝入库
                    raise HTTPException(
                        status_code=400,
                        detail=f"Provider「{item.get('id')}」未填写真实 API Key（不能保存脱敏占位符）",
                    )


class SettingsBody(BaseModel):
    content: str


@router.put("/admin/settings")
async def update_settings(request: Request, body: SettingsBody):
    require_admin(request)
    state = get_state(request)
    try:
        parsed = yaml.safe_load(body.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败：{e}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="配置必须是 YAML 映射")
    _restore_secrets(state, state.config.raw(), parsed)
    try:
        state.config.update_yaml(yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False, default_flow_style=False))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"配置写入失败：{e}")
    try:
        state.reload()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"配置热重载失败：{e}")
    return {"ok": True, "message": "配置已保存并热重载"}
