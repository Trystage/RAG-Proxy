"""Admin API key management."""
from __future__ import annotations

import copy
import hashlib
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..i18n import locale_from_request, translate
from .admin_common import get_state, require_admin, write_config

router = APIRouter()


def _key_value(item: str | dict[str, Any]) -> str | None:
    if isinstance(item, str):
        return item
    value = item.get("key")
    return value if isinstance(value, str) else None


def _key_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _key_preview(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _normalize_item(item: str | dict[str, Any]) -> dict[str, Any] | None:
    value = _key_value(item)
    if not value:
        return None
    if isinstance(item, str):
        item = {"key": item}
    allowed_models = item.get("allowed_models") or []
    return {
        "id": _key_id(value),
        "name": str(item.get("name") or ""),
        "preview": _key_preview(value),
        "enabled": item.get("enabled", True) is not False,
        "expires_at": item.get("expires_at"),
        "allowed_models": [str(model) for model in allowed_models if model],
        "created_at": item.get("created_at"),
    }


def _items(values: list[Any]) -> list[dict[str, Any]]:
    result = []
    for value in values:
        if isinstance(value, (str, dict)):
            item = _normalize_item(value)
            if item:
                result.append(item)
    return result


def _config_with_keys(request: Request) -> tuple[object, dict[str, Any]]:
    state = get_state(request)
    cfg = copy.deepcopy(state.config.raw())
    cfg.pop("_mtime", None)
    return state, cfg


def _find_key(values: list[Any], key_id: str) -> tuple[int, str | dict[str, Any]] | None:
    for index, value in enumerate(values):
        key = _key_value(value) if isinstance(value, (str, dict)) else None
        if key and _key_id(key) == key_id:
            return index, value
    return None


def _validate_expiry(value: str | None) -> str | None:
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid expires_at") from exc
    return value


class ApiAuthBody(BaseModel):
    enabled: bool


class ApiKeyCreateBody(BaseModel):
    name: str = ""
    expires_at: str | None = None
    allowed_models: list[str] = Field(default_factory=list)


class ApiKeyUpdateBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    expires_at: str | None = None
    allowed_models: list[str] | None = None


@router.get("/admin/api")
async def get_api_settings(request: Request):
    require_admin(request)
    state = get_state(request)
    values = state.config.get("api_keys", []) or []
    return {
        "enabled": state.config.get("api_key_auth", True) is not False,
        "keys": _items(values),
    }


@router.get("/admin/api/models")
async def get_api_models(request: Request):
    require_admin(request)
    state = get_state(request)
    models = state.config.get("models", []) or []
    return {
        "models": sorted({str(item.get("name")) for item in models if isinstance(item, dict) and item.get("name")}),
    }


@router.put("/admin/api")
async def update_api_settings(request: Request, body: ApiAuthBody):
    require_admin(request)
    state, cfg = _config_with_keys(request)
    cfg["api_key_auth"] = body.enabled
    write_config(request, state, cfg)
    return {"ok": True, "enabled": body.enabled}


@router.post("/admin/api/keys")
async def create_api_key(request: Request, body: ApiKeyCreateBody | None = None):
    require_admin(request)
    body = body or ApiKeyCreateBody()
    expires_at = _validate_expiry(body.expires_at)
    state, cfg = _config_with_keys(request)
    values = list(cfg.get("api_keys", []) or [])
    key = f"rp_{secrets.token_urlsafe(32)}"
    values.append({
        "key": key,
        "name": body.name.strip(),
        "enabled": True,
        "expires_at": expires_at,
        "allowed_models": sorted(set(body.allowed_models)),
        "created_at": datetime.now().astimezone().isoformat(),
    })
    cfg["api_keys"] = values
    write_config(request, state, cfg)
    return {
        "ok": True,
        "key": key,
        "id": _key_id(key),
        "preview": _key_preview(key),
        "name": body.name.strip(),
        "expires_at": expires_at,
        "allowed_models": sorted(set(body.allowed_models)),
        "message": translate("api_key_created", locale_from_request(request)),
    }


@router.put("/admin/api/keys/{key_id}")
async def update_api_key(request: Request, key_id: str, body: ApiKeyUpdateBody):
    require_admin(request)
    state, cfg = _config_with_keys(request)
    values = list(cfg.get("api_keys", []) or [])
    found = _find_key(values, key_id)
    if found is None:
        raise HTTPException(status_code=404, detail=translate("api_key_not_found", locale_from_request(request)))
    index, old = found
    value = _key_value(old)
    if value is None:
        raise HTTPException(status_code=404, detail=translate("api_key_not_found", locale_from_request(request)))
    item = {"key": value} if isinstance(old, str) else dict(old)
    if body.name is not None:
        item["name"] = body.name.strip()
    if body.enabled is not None:
        item["enabled"] = body.enabled
    if "expires_at" in body.model_fields_set:
        item["expires_at"] = _validate_expiry(body.expires_at)
    if body.allowed_models is not None:
        item["allowed_models"] = sorted(set(body.allowed_models))
    values[index] = item
    cfg["api_keys"] = values
    write_config(request, state, cfg)
    return {"ok": True, "key": _normalize_item(item)}


@router.delete("/admin/api/keys/{key_id}")
async def delete_api_key(request: Request, key_id: str):
    require_admin(request)
    state, cfg = _config_with_keys(request)
    values = list(cfg.get("api_keys", []) or [])
    found = _find_key(values, key_id)
    if found is None:
        raise HTTPException(status_code=404, detail=translate("api_key_not_found", locale_from_request(request)))
    values.pop(found[0])
    cfg["api_keys"] = values
    write_config(request, state, cfg)
    return {"ok": True, "keys": _items(values)}
