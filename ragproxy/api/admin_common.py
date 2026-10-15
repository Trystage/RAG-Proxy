"""管理 API 公共：鉴权依赖与配置写回。"""
from __future__ import annotations

from typing import Any

import yaml
from fastapi import HTTPException, Request

from ..config import is_masked
from ..security import bearer_token, verify_token
from ..state import AppState
from ..i18n import locale_from_request, translate


def require_admin(request: Request) -> dict[str, Any]:
    state: AppState = request.app.state.app  # type: ignore[attr-defined]
    token = bearer_token(request.headers.get("authorization"))
    payload = verify_token(state.secret_key, token or "") if token else None
    if payload is None:
        raise HTTPException(status_code=401, detail=translate("auth_required", locale_from_request(request)))
    return payload


def get_state(request: Request) -> AppState:
    return request.app.state.app  # type: ignore[attr-defined]


def write_config(request: Request, state: AppState, cfg: dict[str, Any]) -> None:
    """写回配置并热重载。"""
    cfg.pop("_mtime", None)
    # 防御：配置中不允许出现脱敏占位（**** / sk-****xxx），防止污染写回磁盘
    disk = state.config.raw()
    for section in ("upstreams", "embeddings", "rerankers"):
        disk_items = {i.get("id"): i for i in (disk.get(section) or [])}
        for item in cfg.get(section) or []:
            if isinstance(item, dict) and is_masked(item.get("api_key")):
                old = disk_items.get(item.get("id")) or {}
                if old.get("api_key") and not is_masked(old.get("api_key")):
                    item["api_key"] = old["api_key"]
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=translate("masked_key", locale_from_request(request), provider=item.get("id")),
                    )
    content = yaml.safe_dump(
        cfg, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    state.config.update_yaml(content)
    state.reload()


def save_section(request: Request, state: AppState, section: str, items: list[dict[str, Any]]) -> None:
    cfg = state.config.raw()
    cfg[section] = items
    write_config(request, state, cfg)


def find_index(items: list[dict], key: str, value: str) -> int:
    for i, it in enumerate(items):
        if it.get(key) == value:
            return i
    return -1
