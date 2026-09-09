"""T-16B API tests — GET /api/comparisons.

Router -> ComparisonService chain exercised through dependency_overrides with
the shared FakeEvaluationRepo (no DB, no third-party calls). Architecture
invariants (thin router, wired in main) checked via source reading."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_comparison_service
from app.main import app
from app.services.comparison_service import ComparisonService
from tests.test_comparison_service import _repo, _seed_run

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.clear()


def _use_repo(repo) -> None:
    app.dependency_overrides[get_comparison_service] = lambda: ComparisonService(repo)


def _client() -> TestClient:
    return TestClient(app)


def test_1_comparisons_returns_200_envelope_direct() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.86,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", overall_score=0.91,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    _use_repo(repo)
    resp = _client().get(
        "/api/comparisons",
        params={"baseline_run_id": base, "candidate_run_id": cand},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "baseline_run_id", "candidate_run_id", "comparability", "metrics", "overall", "generated_at",
    }
    assert body["comparability"]["status"] == "DIRECT"
    assert body["metrics"][0]["delta"] == pytest.approx(0.05)
    assert body["overall"]["comparable"] is True


def test_2_missing_run_is_404() -> None:
    _use_repo(_repo())
    resp = _client().get(
        "/api/comparisons",
        params={"baseline_run_id": "nope", "candidate_run_id": "nope"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_3_missing_query_param_is_422() -> None:
    _use_repo(_repo())
    assert _client().get("/api/comparisons").status_code == 422
    assert _client().get("/api/comparisons", params={"baseline_run_id": "b"}).status_code == 422


def test_4_blocked_returns_reasons_and_empty_metrics() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", dataset_id="ds1",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", dataset_id="ds2",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    _use_repo(repo)
    body = _client().get(
        "/api/comparisons",
        params={"baseline_run_id": base, "candidate_run_id": cand},
    ).json()
    assert body["comparability"]["status"] == "BLOCKED"
    assert body["comparability"]["reasons"]
    assert body["metrics"] == []


def test_5_router_is_thin() -> None:
    src = (ROOT / "app/api/comparisons.py").read_text(encoding="utf-8")
    for forbidden in ("from app.models", "from app.repositories", "session.execute"):
        assert forbidden not in src, forbidden


def test_6_wired_into_main() -> None:
    src = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "comparisons" in src
