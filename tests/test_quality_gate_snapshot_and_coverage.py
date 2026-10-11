"""Quality gate: thresholds from the run snapshot, run-status guard, coverage
of required metrics, and unscored deterministic mismatches (unit_mismatch)."""

from __future__ import annotations

import pytest

from app.main import app  # noqa: F401 — ensures bootstrap_metrics registers specs
from app.models import EvaluationResult, MetricResult
from app.services import quality_gate_service as qgs
from app.services.quality_gate_service import (
    QUALITY_THRESHOLD_FAILED,
    QualityGateService,
)
from tests.test_quality_gate_service import _FakeConfig, _merged, _meta, _qg, _repo, _seed_run

UNIT_MISMATCH = {"comparison_type": "unit_mismatch"}


def _add(repo, run_id, record_id, *, metric_name="numerical_consistency", score,
         threshold=None, comparison_basis=None, error=None) -> None:
    result = repo.insert_evaluation_result(EvaluationResult(
        run_id=run_id, record_id=record_id, row_index=0, question="q", contexts=[],
        answer="a", reference_answer=None, reference_contexts=None, is_failure=False,
    ))
    repo.insert_metric_results([MetricResult(
        result_id=result.id, metric_name=metric_name, category="integrity", score=score,
        threshold=threshold, passed=None, comparison_basis=comparison_basis,
        metric_version="integrity-normalization-v3", error=error,
    )])


def _run(repo, *, meta, status="completed", total_records=1) -> None:
    _seed_run(repo, "run1", meta=meta, metric_rows=[])
    repo.runs["run1"]["status"] = status
    repo.runs["run1"]["total_records"] = total_records


def _gate(repo, required=("numerical_consistency",)) -> dict:
    return QualityGateService(repo, _FakeConfig(_merged(_qg(required)))).evaluate("run1")


def _snap_meta(threshold) -> dict:
    meta = _meta(["numerical_consistency"])
    meta["effective_metrics"] = {"numerical_consistency": {"threshold": threshold, "weight": 1.0}}
    return meta


def test_threshold_comes_from_snapshot_not_first_row() -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(0.9), total_records=2)
    # row thresholds disagree with the snapshot (and with each other)
    _add(repo, "run1", "r1", score=0.85, threshold=0.5)
    _add(repo, "run1", "r2", score=0.85, threshold=0.7)
    resp = _gate(repo)
    m = resp["metrics"][0]
    assert m["threshold"] == pytest.approx(0.9)
    assert m["status"] == "FAIL"
    assert resp["status"] == "FAIL" and QUALITY_THRESHOLD_FAILED in resp["reasons"]


def test_snapshot_null_threshold_is_not_configured_even_if_rows_carry_one() -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(None))
    _add(repo, "run1", "r1", score=0.95, threshold=0.5)
    resp = _gate(repo)
    assert resp["status"] == "NOT_EVALUABLE"
    assert resp["metrics"][0]["reason"] == "threshold_not_configured"


def test_legacy_run_with_inconsistent_row_thresholds_is_not_evaluable() -> None:
    repo = _repo()
    _run(repo, meta=_meta(["numerical_consistency"]), total_records=2)
    _add(repo, "run1", "r1", score=0.95, threshold=0.5)
    _add(repo, "run1", "r2", score=0.95, threshold=0.7)
    resp = _gate(repo)
    assert resp["status"] == "NOT_EVALUABLE"
    assert resp["metrics"][0]["reason"] == "threshold_inconsistent"


@pytest.mark.parametrize("status", ["pending", "running", "failed", "cancelled"])
def test_non_completed_run_is_not_evaluable(status: str) -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(0.5), status=status)
    _add(repo, "run1", "r1", score=0.95)
    resp = _gate(repo)
    assert resp["status"] == "NOT_EVALUABLE"
    assert resp["reasons"] == [qgs.RUN_NOT_COMPLETED]


@pytest.mark.parametrize("status", ["completed", "completed_with_errors"])
def test_completed_runs_are_evaluated(status: str) -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(0.5), status=status)
    _add(repo, "run1", "r1", score=0.95)
    assert _gate(repo)["status"] == "PASS"


def test_required_metric_coverage_is_reported() -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(0.5), status="completed_with_errors", total_records=4)
    _add(repo, "run1", "r1", score=0.95)
    _add(repo, "run1", "r2", score=None, error={"code": "SYS_METRIC_ERROR"})
    _add(repo, "run1", "r3", score=None, comparison_basis={"comparison_type": "ambiguous"})
    m = _gate(repo)["metrics"][0]
    assert m["valid_count"] == 1
    assert m["total_records"] == 4
    assert m["coverage"] == pytest.approx(0.25)


def test_unit_mismatch_fails_the_gate_even_when_scored_mean_passes() -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(0.5), total_records=2)
    _add(repo, "run1", "r1", score=1.0)
    _add(repo, "run1", "r2", score=None, comparison_basis=UNIT_MISMATCH,
         error={"reason": "unit class mismatch"})
    resp = _gate(repo)
    m = resp["metrics"][0]
    assert m["unscored_mismatch_count"] == 1
    assert m["status"] == "FAIL" and m["passed"] is False
    assert resp["status"] == "FAIL"
    assert qgs.REQUIRED_METRIC_DETERMINISTIC_FAILURE in resp["reasons"]


def test_all_unit_mismatch_is_a_failure_not_not_evaluable() -> None:
    repo = _repo()
    _run(repo, meta=_snap_meta(0.5))
    _add(repo, "run1", "r1", score=None, comparison_basis=UNIT_MISMATCH,
         error={"reason": "unit class mismatch"})
    resp = _gate(repo)
    assert resp["status"] == "FAIL"
    assert resp["metrics"][0]["status"] == "FAIL"
    assert resp["reasons"] == [qgs.REQUIRED_METRIC_DETERMINISTIC_FAILURE]
