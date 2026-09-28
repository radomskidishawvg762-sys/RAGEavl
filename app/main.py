from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

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
from app.db.session import open_session
from app.metrics.bootstrap import bootstrap_metrics
from app.repositories.evaluation import EvaluationRepository

logger = logging.getLogger(__name__)


def _reconcile_orphaned_runs() -> None:
    """Fail runs left in flight by a previous process. See the repository method.

    Best-effort on purpose: a database that is unreachable at startup must NOT
    stop the process from booting. /api/health is what reports database state, and
    it can only report it if the app is up.
    """
    try:
        with open_session() as session:
            reconciled = EvaluationRepository(session).reconcile_orphaned_runs(
                finished_at=datetime.now(UTC),
                error_summary={
                    "error_records": 0,
                    "details": [
                        {
                            "code": "SYS_RUN_ORPHANED",
                            "message": (
                                "the process restarted while this run was in flight "
                                "— in-flight runs are not persisted across restarts (ADR-02)"
                            ),
                        }
                    ],
                },
            )
        if reconciled:
            logger.warning(
                "reconciled %d run(s) left running by a previous process", reconciled
            )
    except Exception:  # noqa: BLE001 — startup must not depend on the database
        logger.exception("orphaned-run reconciliation failed; continuing startup")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _reconcile_orphaned_runs()
    yield


def create_app() -> FastAPI:
    setup_logging()
    bootstrap_metrics()
    app = FastAPI(title="RAGEval Studio", version="0.1.0", lifespan=_lifespan)
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
