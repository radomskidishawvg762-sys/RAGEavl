from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.main import app


def test_health_no_db_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """App boots and reports DB unavailable when no engine is configured (no DB needed)."""
    monkeypatch.setattr(health, "get_engine", lambda: None)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["app"] == "healthy"
    assert body["database"] == "unavailable"
    assert body["detail"]["db_latency_ms"] is None
