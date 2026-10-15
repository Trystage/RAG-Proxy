"""FastAPI 应用组装：开放接口 + 管理 API + 前端静态托管。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    admin_auth,
    admin_dashboard,
    admin_kb,
    admin_logs,
    admin_mappings,
    admin_providers,
    admin_settings,
    anthropic_api,
    health,
    openai_api,
)
from .config import AppSettings
from .logging_setup import setup_logging
from .state import AppState

log = logging.getLogger("ragproxy.main")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: AppSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.data_dir, settings.log_level)
        state = AppState(settings)
        app.state.app = state
        # 首次启动输出面板登录密钥（环境变量未设置时自动生成并落盘）
        log.info(
            "RAG Proxy started | Data directory: %s | Config file: %s",
            settings.data_dir,
            settings.config_path,
        )
        log.info(
            "Management panel login key: %s (can also be set via environment variable RAGPROXY_SECRET_KEY)",
            state.secret_key,
        )
        yield
        for p in state.llm_providers.values():
            try:
                await p.aclose()
            except Exception:  # noqa: BLE001
                pass
        if state.embed is not None:
            try:
                await state.embed.aclose()
            except Exception:  # noqa: BLE001
                pass
        if state.reranker is not None:
            try:
                await state.reranker.aclose()
            except Exception:  # noqa: BLE001
                pass

    app = FastAPI(
        title="RAG Proxy",
        description="知识库中间层代理：按模型名注入检索结果，不训练 LoRA 的专项能力增强",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/admin/docs",
        openapi_url="/admin/openapi.json",
        redoc_url=None,
    )

    # 跨域：面板与下游 API 默认放行，生产可按需收紧
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 开放接口
    app.include_router(health.router)
    app.include_router(openai_api.router)
    app.include_router(anthropic_api.router)

    # 管理 API
    app.include_router(admin_auth.router)
    app.include_router(admin_dashboard.router)
    app.include_router(admin_providers.router)
    app.include_router(admin_kb.router)
    app.include_router(admin_mappings.router)
    app.include_router(admin_logs.router)
    app.include_router(admin_settings.router)

    # 前端静态托管
    if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            candidate = (STATIC_DIR / full_path).resolve()
            if full_path and candidate.is_file() and str(candidate).startswith(str(STATIC_DIR.resolve())):
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse(
                {
                    "message": "Frontend is not built. Run npm install && npm run build in frontend/, "
                    "or build it in the Docker image. Admin API docs: /admin/docs.",
                    "health": "/health",
                }
            )

    return app
