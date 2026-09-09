from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db.health import check_db
from app.db.session import get_engine

router = APIRouter()


@router.get("/health")
def health() -> JSONResponse:
    """App vs database health. No secrets emitted — only booleans and a latency int."""
    engine = get_engine()
    healthy, latency_ms = check_db(engine)
    body = {
        "app": "healthy",
        "database": "healthy" if healthy else "unavailable",
        "detail": {"db_latency_ms": latency_ms},
    }
    return JSONResponse(content=body, status_code=200 if healthy else 503)
