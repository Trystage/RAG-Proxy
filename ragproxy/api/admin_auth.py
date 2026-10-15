"""管理 API：登录鉴权。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..security import check_secret, create_token
from ..i18n import locale_from_request, translate
from .admin_common import require_admin

router = APIRouter()


class LoginBody(BaseModel):
    key: str


@router.post("/admin/auth/login")
async def login(request: Request, body: LoginBody):
    state = request.app.state.app  # type: ignore[attr-defined]
    if not check_secret(state.secret_key, body.key):
        raise HTTPException(status_code=401, detail=translate("invalid_secret", locale_from_request(request)))
    ttl = int((state.config.get("security", {}) or {}).get("session_ttl_hours", 24))
    return {"token": create_token(state.secret_key, ttl), "expires_hours": ttl}


@router.get("/admin/auth/me")
async def me(request: Request):
    require_admin(request)
    return {"user": "admin"}


@router.get("/admin/status")
async def status(request: Request):
    require_admin(request)
    state = request.app.state.app  # type: ignore[attr-defined]
    return {"ok": True, "health": state.health(), "secret_key_file": "config/secret_key.yaml"}
