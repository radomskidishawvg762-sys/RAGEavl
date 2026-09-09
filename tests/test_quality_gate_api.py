"""T-17 API tests — GET /api/evaluations/{run_id}/quality-gate.

Router -> QualityGateService chain via dependency_overrides with the shared
FakeEvaluationRepo + a fake Profile config (no DB, no third-party calls)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_quality_gate_service
from app.main import app
from app.services.quality_gate_service import QualityGateService
from tests.test_quality_gate_service import _FakeConfig, _merged, _meta, _repo, _seed_run

ROOT = Path(__file__).resolve().parents[1]

QG = {"enabled": True, "required_metrics": ["faithfulness", "context_recall"]}


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.clear()


def _use_repo(repo, merged) -> None:
    app.dependency_overrides[get_quality_gate_service] = lambda: QualityGateService(
        repo, _FakeConfig(merged)
    )


def _client() -> TestClient:
    return TestClient(app)


def test_1_quality_gate_returns_200_envelope() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness", "context_recall"]), overall_score=0.9,
              metric_rows=[
                  {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
                  {"metric_name": "context_recall", "category": "retrieval", "score": 0.85, "threshold": 0.8},
              ])
    _use_repo(repo, _merged(QG))
    resp = _client().get("/api/evaluations/run1/quality-gate")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"run_id", "status", "reasons", "metrics", "overall_score", "evaluated_at"}
    assert body["status"] == "PASS"
    assert len(body["metrics"]) == 2


def test_2_missing_run_is_404() -> None:
    _use_repo(_repo(), _merged(QG))
    resp = _client().get("/api/evaluations/nope/quality-gate")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_3_not_configured_is_not_evaluable() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
    ])
    _use_repo(repo, _merged(None))  # quality_gate: null in profile
    body = _client().get("/api/evaluations/run1/quality-gate").json()
    assert body["status"] == "NOT_EVALUABLE"
    assert "QUALITY_GATE_NOT_CONFIGURED" in body["reasons"]


def test_4_fail_case_through_api() -> None:
    repo = _repo()
    _seed_run(repo, "run1", meta=_meta(["faithfulness", "context_recall"]), metric_rows=[
        {"metric_name": "faithfulness", "category": "generation", "score": 0.91, "threshold": 0.8},
        {"metric_name": "context_recall", "category": "retrieval", "score": 0.70, "threshold": 0.8},
    ])
    _use_repo(repo, _merged(QG))
    body = _client().get("/api/evaluations/run1/quality-gate").json()
    assert body["status"] == "FAIL"
    assert "QUALITY_THRESHOLD_FAILED" in body["reasons"]


def test_5_router_is_thin() -> None:
    src = (ROOT / "app/api/evaluations.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "session.execute"):
        assert forbidden not in src, forbidden
