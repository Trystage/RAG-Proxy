"""管理 API：Dashboard 统计。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .admin_common import get_state, require_admin

router = APIRouter()


@router.get("/admin/dashboard/stats")
async def dashboard_stats(request: Request):
    require_admin(request)
    state = get_state(request)
    return state.reqlog.stats()
