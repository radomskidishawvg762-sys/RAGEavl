"""Comparison / regression: record_id pairing on the scored intersection,
coverage inconsistency -> LIMITED, and unscored deterministic mismatches
(unit_mismatch) feeding the regression verdict."""

from __future__ import annotations

import pytest

from app.main import app  # noqa: F401 — ensures bootstrap_metrics registers specs
from app.models import EvaluationResult, MetricResult
from app.services.comparison_service import ComparisonService
from app.services.regression_service import RegressionService
from app.services.report_service import ReportService
from tests.test_comparison_service import META, _repo, _seed_run

UNIT_MISMATCH = {"comparison_type": "unit_mismatch"}


def _add(repo, run_id, record_id, score, *, metric_name="faithfulness",
         comparison_basis=None, error=None) -> None:
    result = repo.insert_evaluation_result(EvaluationResult(
        run_id=run_id, record_id=record_id, row_index=0, question="q", contexts=[],
        answer="a", reference_answer=None, reference_contexts=None, is_failure=False,
    ))
    repo.insert_metric_results([MetricResult(
        result_id=result.id, metric_name=metric_name, category="generation", score=score,
        threshold=None, passed=None, comparison_basis=comparison_basis,
        metric_version="ragas-0.4.3", error=error,
    )])


def _runs(baseline: dict, candidate: dict, *, metric_name="faithfulness") -> tuple[dict, dict]:
    repo = _repo()
    meta = {**META, "enabled_metrics": [metric_name], "metric_weights": {metric_name: 1.0}}
    _seed_run(repo, "base", meta=meta)
    _seed_run(repo, "cand", meta=meta)
    for run_id, rows in (("base", baseline), ("cand", candidate)):
        for record_id, row in rows.items():
            if isinstance(row, dict):
                _add(repo, run_id, record_id, None, metric_name=metric_name, **row)
            else:
                _add(repo, run_id, record_id, row, metric_name=metric_name)
    service = ComparisonService(repo)
    comparison = service.compare(baseline_run_id="base", candidate_run_id="cand")
    regression = RegressionService(service).analyze(comparison)
    return comparison, regression


def _item(comparison: dict, name: str = "faithfulness") -> dict:
    return next(m for m in comparison["metrics"] if m["name"] == name)


ERROR = {"error": {"code": "SYS_METRIC_ERROR", "message": "boom"}}


def test_delta_uses_only_records_scored_on_both_sides() -> None:
    # candidate failed on r2 (the baseline's weakest record): run means would
    # be 0.6 vs 0.9 = +0.3 "improvement" that no record actually shows
    comparison, regression = _runs({"r1": 0.9, "r2": 0.3}, {"r1": 0.9, "r2": ERROR})
    m = _item(comparison)
    assert m["comparable"] is True
    assert m["paired_count"] == 1
    assert (m["baseline_valid_count"], m["candidate_valid_count"]) == (2, 1)
    assert m["baseline_score"] == pytest.approx(0.9)
    assert m["candidate_score"] == pytest.approx(0.9)
    assert m["delta"] == pytest.approx(0.0)
    assert regression["metrics"][0]["verdict"] == "STABLE"


def test_pairing_is_by_record_id_not_row_order() -> None:
    comparison, _ = _runs({"r1": 0.2, "r2": 0.8}, {"r2": 0.8, "r1": 0.4})
    m = _item(comparison)
    assert m["paired_count"] == 2
    assert m["delta"] == pytest.approx(0.1)


def test_coverage_inconsistency_marks_comparison_limited() -> None:
    comparison, regression = _runs({"r1": 0.9, "r2": 0.3}, {"r1": 0.9, "r2": ERROR})
    assert comparison["comparability"]["status"] == "LIMITED"
    assert any("coverage differs" in r for r in comparison["comparability"]["reasons"])
    assert comparison["overall"]["comparable"] is False
    assert regression["comparability_status"] == "LIMITED"


def test_identical_coverage_stays_direct() -> None:
    comparison, _ = _runs({"r1": 0.9, "r2": ERROR}, {"r1": 0.8, "r2": ERROR})
    assert comparison["comparability"]["status"] == "DIRECT"
    assert _item(comparison)["paired_count"] == 1


def test_disjoint_scored_records_are_not_comparable() -> None:
    comparison, regression = _runs({"r1": 0.9, "r2": ERROR}, {"r1": ERROR, "r2": 0.1})
    m = _item(comparison)
    assert m["comparable"] is False
    assert m["paired_count"] == 0
    assert m["incomparable_reason"] == "no_paired_records"
    assert comparison["comparability"]["status"] == "LIMITED"
    assert regression["metrics"][0]["verdict"] == "NOT_COMPARABLE"


def test_aggregation_counts_unscored_deterministic_mismatches() -> None:
    rows = [
        {"metric_name": "numerical_consistency", "score": 1.0, "comparison_type": "match"},
        {"metric_name": "numerical_consistency", "score": None, "comparison_type": "unit_mismatch",
         "error": {"reason": "unit class mismatch"}},
        {"metric_name": "numerical_consistency", "score": None, "comparison_type": "ambiguous"},
    ]
    (m,) = ReportService._aggregate_metrics(rows, {})
    assert m["unscored_mismatch_count"] == 1
    assert m["score"] == pytest.approx(1.0)


def test_new_unit_mismatch_is_a_regression_even_if_scored_mean_is_stable() -> None:
    comparison, regression = _runs(
        {"r1": 1.0, "r2": 1.0}, {"r1": 1.0, "r2": {"comparison_basis": UNIT_MISMATCH}},
        metric_name="numerical_consistency",
    )
    m = _item(comparison, "numerical_consistency")
    assert (m["new_unscored_mismatches"], m["resolved_unscored_mismatches"]) == (1, 0)
    (verdict,) = regression["metrics"]
    assert verdict["unscored_mismatch_delta"] == 1
    assert verdict["verdict"] == "REGRESSION"
    assert regression["overall"]["verdict"] == "REGRESSION"


def test_resolved_unit_mismatch_is_an_improvement() -> None:
    comparison, regression = _runs(
        {"r1": 1.0, "r2": {"comparison_basis": UNIT_MISMATCH}}, {"r1": 1.0, "r2": 1.0},
        metric_name="numerical_consistency",
    )
    (verdict,) = regression["metrics"]
    assert verdict["unscored_mismatch_delta"] == -1
    assert verdict["verdict"] == "IMPROVEMENT"


def test_all_records_unit_mismatch_in_candidate_is_regression_not_not_comparable() -> None:
    _, regression = _runs(
        {"r1": 1.0}, {"r1": {"comparison_basis": UNIT_MISMATCH}},
        metric_name="numerical_consistency",
    )
    assert regression["metrics"][0]["verdict"] == "REGRESSION"
