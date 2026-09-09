"""T-16B tests — ComparisonService (read-only, fake-repository based).

The 16 mandated points + a read-only runtime guard. FakeEvaluationRepo
mirrors EvaluationRepository's read surface, so the whole Service -> Repository
chain is exercised without a real DB and without Engine/Judge/RAG (test 14-16).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models import EvaluationResult, MetricResult
from app.services.comparison_service import ComparisonService
from tests.test_evaluation_service import FakeEvaluationRepo

ROOT = Path(__file__).resolve().parents[1]

META = {
    "dataset_version": "v3",
    "config_version": "cfg-abc",
    "metric_version": "ragas-0.4.3",
    "enabled_metrics": ["faithfulness", "answer_relevancy"],
    "metric_weights": {"faithfulness": 1.0, "answer_relevancy": 1.0},
    "judge_provider": "openai",
    "judge_model": "gpt-4o",
}


def _meta_with(**overrides) -> dict:
    meta = dict(META)
    meta.update(overrides)
    return meta


def _repo():
    return FakeEvaluationRepo(
        {
            "ds1": {"is_locked": False, "record_count": 2, "version": "v3"},
            "ds2": {"is_locked": False, "record_count": 2, "version": "v3"},
        },
        [],
    )


def _add_metric(repo, run_id, *, metric_name, category="generation", score=1.0,
                metric_version="ragas-0.4.3", error=None, comparison_basis=None):
    result = repo.insert_evaluation_result(EvaluationResult(
        run_id=run_id, record_id="r1", row_index=0, question="q", contexts=[],
        answer="a", reference_answer=None, reference_contexts=None, is_failure=False,
    ))
    repo.insert_metric_results([MetricResult(
        result_id=result.id, metric_name=metric_name, category=category, score=score,
        threshold=None, passed=None, comparison_basis=comparison_basis,
        metric_version=metric_version, error=error,
    )])


def _seed_run(repo, run_id, *, dataset_id="ds1", status="completed", overall_score=None,
              meta=None, metric_specs=None):
    repo.runs[run_id] = {
        "id": run_id, "project_id": "p1", "dataset_id": dataset_id, "config_id": "c1",
        "status": status, "total_records": 2, "evaluated_records": 2, "error_records": 0,
        "evaluation_coverage": 1.0, "overall_score": overall_score,
        "reproducibility_meta": meta or dict(META), "started_at": None, "finished_at": None,
        "error_summary": None, "created_at": datetime.now(UTC),
    }
    for spec in (metric_specs or []):
        _add_metric(repo, run_id, **spec)
    return run_id


def _compare(repo, baseline_run_id, candidate_run_id) -> dict:
    return ComparisonService(repo).compare(
        baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id
    )


def _metric(resp: dict, name: str) -> dict:
    return next(m for m in resp["metrics"] if m["name"] == name)


# ---- 1-2: DIRECT + delta ----

def test_1_same_everything_direct_and_positive_delta() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.86,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", overall_score=0.91,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "DIRECT"
    assert resp["comparability"]["reasons"] == []
    m = _metric(resp, "faithfulness")
    assert m["baseline_score"] == pytest.approx(0.86)
    assert m["candidate_score"] == pytest.approx(0.91)
    assert m["delta"] == pytest.approx(0.05)  # candidate - baseline
    assert m["relative_delta"] == pytest.approx(0.05 / 0.86)
    assert m["comparable"] is True
    assert resp["overall"]["delta"] == pytest.approx(0.05)
    assert resp["overall"]["comparable"] is True


def test_2_delta_is_candidate_minus_baseline_signed() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.90,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.90}])
    cand = _seed_run(repo, "cand", overall_score=0.84,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.84}])
    m = _metric(_compare(repo, base, cand), "faithfulness")
    assert m["delta"] == pytest.approx(-0.06)
    assert m["relative_delta"] == pytest.approx(-0.06 / 0.90)


# ---- 3: delta vs relative_delta ----

def test_3_delta_and_relative_delta_are_distinct() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.50,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.50}])
    cand = _seed_run(repo, "cand", overall_score=0.60,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.60}])
    m = _metric(_compare(repo, base, cand), "faithfulness")
    assert m["delta"] == pytest.approx(0.10)  # absolute change
    assert m["relative_delta"] == pytest.approx(0.20)  # 0.10 / 0.50 — a ratio, not "10%"
    assert m["relative_delta"] != pytest.approx(m["delta"])  # never conflated


# ---- 4-7: null score is NEVER coerced to 0 ----

def test_4_null_score_not_zero_reason_undetermined() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.80,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.80}])
    cand = _seed_run(repo, "cand", overall_score=0.80, metric_specs=[{
        "metric_name": "faithfulness", "score": None,
        "comparison_basis": {"comparison_type": "ambiguous", "method": "deterministic"},
    }])
    m = _metric(_compare(repo, base, cand), "faithfulness")
    assert m["candidate_score"] is None  # NOT 0.0
    assert m["delta"] is None
    assert m["relative_delta"] is None
    assert m["comparable"] is False
    assert m["incomparable_reason"] == "undetermined"


def test_5_not_configured_produces_no_fake_delta() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.80,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.80}])
    cand = _seed_run(repo, "cand", overall_score=0.80, metric_specs=[{
        "metric_name": "faithfulness", "score": None,
        "error": {"code": "BIZ_JUDGE_NOT_CONFIGURED", "reason": "judge_not_configured"},
    }])
    m = _metric(_compare(repo, base, cand), "faithfulness")
    assert m["comparable"] is False
    assert m["delta"] is None
    assert m["incomparable_reason"] == "not_configured"


def test_6_undetermined_on_baseline_side() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.80, metric_specs=[{
        "metric_name": "faithfulness", "score": None,
        "comparison_basis": {"comparison_type": "ambiguous", "method": "deterministic"},
    }])
    cand = _seed_run(repo, "cand", overall_score=0.80,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.80}])
    m = _metric(_compare(repo, base, cand), "faithfulness")
    assert m["comparable"] is False
    assert m["delta"] is None
    assert m["incomparable_reason"] == "undetermined"


def test_7_execution_error_produces_no_fake_delta() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.80,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.80}])
    cand = _seed_run(repo, "cand", overall_score=0.80, metric_specs=[{
        "metric_name": "faithfulness", "score": None,
        "error": {"code": "SYS_METRIC_ERROR", "message": "boom"},
    }])
    m = _metric(_compare(repo, base, cand), "faithfulness")
    assert m["comparable"] is False
    assert m["delta"] is None
    assert m["incomparable_reason"] == "execution_error"


# ---- 8-12: comparability status ----

def test_8_different_datasets_blocked_no_metrics() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", dataset_id="ds1",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", dataset_id="ds2",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "BLOCKED"
    assert any("different datasets" in r for r in resp["comparability"]["reasons"])
    assert resp["metrics"] == []
    assert resp["overall"]["comparable"] is False


def test_9_enabled_metrics_differ_not_direct() -> None:
    repo = _repo()
    base = _seed_run(repo, "base",
                     meta=_meta_with(enabled_metrics=["faithfulness"],
                                     metric_weights={"faithfulness": 1.0}),
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "LIMITED"
    assert any("enabled metrics differ" in r for r in resp["comparability"]["reasons"])
    assert _metric(resp, "faithfulness")["delta"] == pytest.approx(0.05)


def test_10_metric_version_differ_not_direct() -> None:
    repo = _repo()
    base = _seed_run(repo, "base",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand",
                     meta=_meta_with(metric_version="ragas-0.4.4"),
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91,
                                    "metric_version": "ragas-0.4.4"}])
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "LIMITED"
    assert any("metric version differs" in r for r in resp["comparability"]["reasons"])


def test_11_weights_differ_overall_not_comparable() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.86,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", overall_score=0.91,
                     meta=_meta_with(metric_weights={"faithfulness": 2.0, "answer_relevancy": 1.0}),
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "DIRECT"  # weights do NOT gate status
    assert _metric(resp, "faithfulness")["comparable"] is True
    assert resp["overall"]["comparable"] is False
    assert "metric weights differ" in resp["overall"]["reason"]
    assert resp["overall"]["delta"] is None


def test_12_judge_differ_limited_with_warning() -> None:
    repo = _repo()
    base = _seed_run(repo, "base",
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand",
                     meta=_meta_with(judge_model="gpt-4o-mini"),
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "LIMITED"
    assert any("judge configuration differs" in r for r in resp["comparability"]["reasons"])
    assert _metric(resp, "faithfulness")["delta"] == pytest.approx(0.05)


# ---- 13: overall is read, never recomputed ----

def test_13_overall_score_read_from_persisted_value() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.42,  # persisted != metric aggregation
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", overall_score=0.55,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    resp = _compare(repo, base, cand)
    assert resp["overall"]["baseline_overall"] == pytest.approx(0.42)  # NOT 0.86
    assert resp["overall"]["candidate_overall"] == pytest.approx(0.55)
    assert resp["overall"]["delta"] == pytest.approx(0.13)


# ---- 14-16: Comparison never calls Engine / Judge / RAG ----

def test_14_comparison_never_calls_engine() -> None:
    src = (ROOT / "app/services/comparison_service.py").read_text(encoding="utf-8")
    for forbidden in ("from app.engines", "build_engines", "async def"):
        assert forbidden not in src, forbidden


def test_15_comparison_never_calls_judge() -> None:
    src = (ROOT / "app/services/comparison_service.py").read_text(encoding="utf-8")
    for forbidden in ("JudgeClient", "from app.engines.judge", "JudgeEngine"):
        assert forbidden not in src, forbidden


def test_16_comparison_never_calls_rag() -> None:
    src = (ROOT / "app/services/comparison_service.py").read_text(encoding="utf-8")
    for forbidden in ("from app.adapters", "rag_adapter", "RagOutput", "HttpRagAdapter"):
        assert forbidden not in src, forbidden


# ---- 17: runtime read-only guard ----

def test_17_comparison_is_read_only() -> None:
    repo = _repo()
    base = _seed_run(repo, "base", overall_score=0.86,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.86}])
    cand = _seed_run(repo, "cand", overall_score=0.91,
                     metric_specs=[{"metric_name": "faithfulness", "score": 0.91}])
    repo.update_run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    repo.insert_evaluation_result = lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    repo.insert_metric_results = lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    repo.insert_diagnosis_with_recommendations = lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    resp = _compare(repo, base, cand)
    assert resp["comparability"]["status"] == "DIRECT"
