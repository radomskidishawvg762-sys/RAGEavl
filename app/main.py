from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    comparisons,
    config_import,
    configs,
    datasets,
    evaluations,
    health,
    judge_settings,
    projects,
)
from app.core.errors import AppError
from app.core.logging import setup_logging
from app.metrics.bootstrap import bootstrap_metrics


def create_app() -> FastAPI:
    setup_logging()
    bootstrap_metrics()
    app = FastAPI(title="RAGEval Studio", version="0.1.0")
    # JSONB-heavy report payloads compress ~5:1 — a direct win on high-RTT
    # links; harmless on loopback (client sends Accept-Encoding: gzip).
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.include_router(health.router, prefix="/api")
    app.include_router(datasets.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(evaluations.router, prefix="/api")
    app.include_router(comparisons.router, prefix="/api")
    app.include_router(configs.router, prefix="/api")
    app.include_router(config_import.router, prefix="/api")
    app.include_router(judge_settings.router, prefix="/api")

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.body())

    return app


app = create_app()
