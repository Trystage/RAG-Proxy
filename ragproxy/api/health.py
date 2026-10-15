"""健康检查。"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    state = request.app.state.app  # type: ignore[attr-defined]
    return state.health()
