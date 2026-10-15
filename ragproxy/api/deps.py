"""FastAPI 依赖。"""
from __future__ import annotations

from fastapi import Request

from ..state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.app  # type: ignore[attr-defined]
