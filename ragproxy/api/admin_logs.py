"""管理 API：请求日志查询与 Dashboard 统计。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .admin_common import get_state, require_admin

router = APIRouter()


@router.get("/admin/logs")
async def list_logs(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    kb: str | None = None,
    status: int | None = None,
    page: int = 1,
    size: int = 20,
):
    require_admin(request)
    state = get_state(request)
    data = state.reqlog.query(start, end, model, kb, status, page, size)
    return data
