"""T-14C tests — Report / Aggregation (read-only) + 15 mandated points."""

from __future__ import annotations

import asyncio
import copy

import pytest

from app.core.errors import NotFoundError
from app.engines.base import EvalParams
from app.engines.integrity import IntegrityEngine
from app.main import app
from app.runner.local import LocalAsyncRunner
from app.services.evaluation_service import EvaluationService
from app.services.report_service import ReportService, compute_overall_score, metric_status
from tests.test_evaluation_service import FakeEvaluationRepo, _record_row

META = {
    "dataset_version": "v1", "config_version": "cfg", "metric_version": "integrity-normalization-v1",
    "enabled_metrics": ["temporal_consistency", "numerical_consistency", "entity_consistency"],
    "metric_weights": {"temporal_consistency": 1.0, "numerical_consistency": 2.0,
                       "entity_consistency": 1.0},
}


class _SpyEngine(IntegrityEngine):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def evaluate(self, record, metrics, params):
        self.calls += 1
        return await super().evaluate(record, metrics, params)


class _SpyAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, sample):  # noqa: ANN001
        from app.adapters.rag_input import RagOutput

        self.calls += 1
        return RagOutput(answer="2024年度", contexts=["c"])


def _make_run(repo: FakeEvaluationRepo, *, status: str = "pending", meta: dict | None = None):
    svc = EvaluationService(repo)
    run = svc.create_run(project_id="p1", dataset_id="ds1", config_id="c1",
                         reproducibility_meta=meta or META)
    repo.runs[run.id]["status"] = status
    return run.id


def _add_record(repo: FakeEvaluationRepo, run_id: str, record_id: str, is_failure: bool) -> str:
    """Persist one evaluation_result row and return its id (fake insert)."""
    from app.models import EvaluationResult as Row

    row = Row(run_id=run_id, record_id=record_id, row_index=0, question="q",
              contexts=[], answer="a", reference_answer="2024年度",
              reference_contexts=["ref"], is_failure=is_failure)
    return repo.insert_evaluation_result(row).id


def _add_metric(repo: FakeEvaluationRepo, result_id: str, **kw) -> None:
    from app.models import MetricResult as Row

    defaults = dict(result_id=result_id, metric_name="temporal_consistency",
                    category="integrity", score=1.0, threshold=None, passed=None,
                    comparison_basis=None, metric_version="integrity-normalization-v1", error=None)
    defaults.update(kw)
    repo.insert_metric_results([Row(**defaults)])


def _seed(repo: FakeEvaluationRepo, run_id: str, metrics: list[dict], record: str = "r1",
          is_failure: bool = False) -> None:
    result_id = _add_record(repo, run_id, record, is_failure)
    for m in metrics:
        _add_metric(repo, result_id, **m)


def _report(repo: FakeEvaluationRepo, run_id: str) -> dict:
    return ReportService(repo).build_report(run_id)


# ---- 1-8: overall score & aggregation ----

def test_1_all_scores_weighted_average() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": 1.0},
        {"metric_name": "numerical_consistency", "score": 0.5},
        {"metric_name": "entity_consistency", "score": 0.0},
    ])
    rep = _report(repo, run_id)
    # (1.0*1 + 0.5*2 + 0.0*1) / (1+2+1) = 2.0/4 = 0.5
    assert rep["summary"]["overall_score"] == pytest.approx(0.5)
    assert rep["summary"]["valid_metric_count"] == 3


def test_2_null_score_excluded_from_denominator() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": 1.0},
        {"metric_name": "numerical_consistency", "score": None, "comparison_basis": None},
    ])
    rep = _report(repo, run_id)
    assert rep["summary"]["overall_score"] == pytest.approx(1.0)  # null is NOT 0
    assert rep["summary"]["valid_metric_count"] == 1


def test_3_ambiguous_excluded_and_marked_undetermined() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": 1.0},
        {"metric_name": "numerical_consistency", "score": None,
         "comparison_basis": {"comparison_type": "ambiguous", "method": "deterministic"}},
    ])
    rep = _report(repo, run_id)
    assert rep["summary"]["overall_score"] == pytest.approx(1.0)
    statuses = {m["name"]: m["status"] for m in rep["raw_metrics"]}
    assert statuses["numerical_consistency"] == "undetermined"


def test_4_not_configured_excluded() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": 1.0},
        {"metric_name": "faithfulness", "category": "generation", "score": None,
         "metric_version": "ragas-0.4.3",
         "error": {"code": "BIZ_JUDGE_NOT_CONFIGURED", "reason": "judge_not_configured"}},
    ])
    rep = _report(repo, run_id)
    assert rep["summary"]["overall_score"] == pytest.approx(1.0)
    row = next(m for m in rep["raw_metrics"] if m["name"] == "faithfulness")
    assert row["status"] == "not_configured"
    assert any(e["error_code"] == "BIZ_JUDGE_NOT_CONFIGURED" for e in rep["execution_errors"])


def test_5_execution_error_excluded() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": 1.0},
        {"metric_name": "numerical_consistency", "score": None,
         "error": {"code": "SYS_METRIC_ERROR", "message": "internal bug"}},
    ])
    rep = _report(repo, run_id)
    assert rep["summary"]["overall_score"] == pytest.approx(1.0)
    row = next(m for m in rep["raw_metrics"] if m["name"] == "numerical_consistency")
    assert row["status"] == "error"


def test_6_no_valid_score_overall_is_null() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": None,
         "comparison_basis": {"comparison_type": "ambiguous", "method": "deterministic"}},
        {"metric_name": "numerical_consistency", "score": None,
         "error": {"code": "EXT_JUDGE_UNAVAILABLE", "message": "down"}},
    ])
    rep = _report(repo, run_id)
    assert rep["summary"]["overall_score"] is None
    assert rep["summary"]["valid_metric_count"] == 0


def test_7_coverage_and_counters_reported() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 2, "version": "v1"}}, [
        _record_row("r1", "q", "2024年度", "2024年"),
        _record_row("r2", "q", "2023年", "2024年"),
    ])
    run_id = _make_run(repo)
    engine = _SpyEngine()
    adapter = _SpyAdapter()
    asyncio.run(EvaluationService(repo).execute_run(
        run_id, engines=[engine], enabled_metrics=["temporal_consistency"],
        params=EvalParams(), rag_adapter=adapter, runner=LocalAsyncRunner(concurrency=1),
    ))
    rep = _report(repo, run_id)
    assert rep["summary"]["total_records"] == 2
    assert rep["summary"]["evaluation_coverage"] == 1.0
    assert rep["summary"]["overall_score"] is not None


def test_8_raw_metrics_and_overall_coexist() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [{"metric_name": "temporal_consistency", "score": 0.75}])
    rep = _report(repo, run_id)
    assert rep["summary"]["overall_score"] == pytest.approx(0.75)
    assert len(rep["raw_metrics"]) == 1
    assert rep["raw_metrics"][0]["score"] == 0.75
    assert rep["metrics"][0]["weight"] == 1.0


# ---- 9-11: report never re-executes / never mutates / never calls RAG ----

def test_9_report_does_not_reexecute_metrics() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [
        _record_row("r1", "q", "2024年度", "2024年"),
    ])
    run_id = _make_run(repo)
    engine = _SpyEngine()
    adapter = _SpyAdapter()
    asyncio.run(EvaluationService(repo).execute_run(
        run_id, engines=[engine], enabled_metrics=["temporal_consistency"],
        params=EvalParams(), rag_adapter=adapter, runner=LocalAsyncRunner(concurrency=1),
    ))
    calls_after_run = engine.calls
    _report(repo, run_id)
    _report(repo, run_id)
    assert engine.calls == calls_after_run  # report executed nothing


def test_10_report_does_not_modify_metric_results() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    _seed(repo, run_id, [
        {"metric_name": "temporal_consistency", "score": 1.0},
        {"metric_name": "numerical_consistency", "score": None,
         "error": {"code": "SYS_METRIC_ERROR", "message": "boom"}},
    ])
    before = copy.deepcopy(repo.metric_results)
    _report(repo, run_id)
    assert repo.metric_results == before


def test_11_report_triggers_no_rag_calls() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 1, "version": "v1"}}, [
        _record_row("r1", "q", "2024年度", "2024年"),
    ])
    run_id = _make_run(repo)
    adapter = _SpyAdapter()
    asyncio.run(EvaluationService(repo).execute_run(
        run_id, engines=[IntegrityEngine()], enabled_metrics=["temporal_consistency"],
        params=EvalParams(), rag_adapter=adapter, runner=LocalAsyncRunner(concurrency=1),
    ))
    fetched = adapter.calls
    _report(repo, run_id)
    assert adapter.calls == fetched  # no new RAG traffic


# ---- 12-13: missing / unfinished runs ----

def test_12_missing_run_is_404() -> None:
    from fastapi.testclient import TestClient as _TC

    from app.api.deps import get_report_service as _grs
    from app.services.report_service import ReportService as _RS

    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    app.dependency_overrides[_grs] = lambda: _RS(repo)
    try:
        with _TC(app) as client:
            resp = client.get("/api/evaluations/nope/report")
            assert resp.status_code == 404
            assert resp.json()["code"] == "BIZ_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
    with pytest.raises(NotFoundError):
        _RS(repo).build_report("nope")


def test_13_unfinished_run_reports_explicit_state() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="running")
    _seed(repo, run_id, [{"metric_name": "temporal_consistency", "score": 1.0}])
    rep = _report(repo, run_id)
    assert rep["summary"]["is_final"] is False
    assert rep["summary"]["status"] == "running"
    assert "not final" in (rep["summary"]["message"] or "")


# ---- 14-15: reproducibility & three-way separation ----

def test_14_reproducibility_meta_complete() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    rep = _report(repo, run_id)
    assert rep["reproducibility"]["config_version"] == "cfg"
    assert rep["reproducibility"]["dataset_version"] == "v1"
    assert rep["reproducibility"]["metric_version"] == "integrity-normalization-v1"
    assert rep["reproducibility"]["enabled_metrics"] == META["enabled_metrics"]


def test_15_failure_undetermined_error_not_conflated() -> None:
    repo = FakeEvaluationRepo({"ds1": {"is_locked": False, "record_count": 0, "version": "v1"}}, [])
    run_id = _make_run(repo, status="completed")
    result_id = _add_record(repo, run_id, "r1", True)
    _add_metric(repo, result_id, metric_name="numerical_consistency", score=0.0, threshold=0.9,
                passed=False,
                comparison_basis={"comparison_type": "value_mismatch", "method": "deterministic",
                                  "diff": {"base": {"abs_diff": 1e8}}})
    _add_metric(repo, result_id, metric_name="temporal_consistency", score=None,
                comparison_basis={"comparison_type": "ambiguous", "method": "deterministic"})
    _add_metric(repo, result_id, metric_name="entity_consistency", score=None,
                error={"code": "EXT_RAG_ADAPTER_TIMEOUT", "message": "rag slow"})
    # one diagnosed failure + one undetermined diagnosis
    from app.models import Diagnosis as DRow

    d1 = DRow(run_id=run_id, result_id=result_id, status="diagnosed",
              failure_type="integrity.numerical_mismatch", related_metric="numerical_consistency",
              root_cause="Numerical Mismatch", severity="CRITICAL", evidence=[],
              evidence_contract="integrity.numerical_mismatch.v1", confidence="high")
    repo.insert_diagnosis_with_recommendations(d1, [])
    d2 = DRow(run_id=run_id, result_id=result_id, status="undetermined",
              failure_type=None, related_metric="temporal_consistency", root_cause=None,
              severity="INFO", evidence=[], evidence_contract="", confidence="low",
              detail={"reason": "ambiguous", "missing_evidence": ["reference_evidence"]})
    repo.insert_diagnosis_with_recommendations(d2, [])

    rep = _report(repo, run_id)
    assert len(rep["failures"]) == 1 and rep["failures"][0]["status"] == "failure"
    assert rep["failures"][0]["root_cause"]
    assert len(rep["undetermined"]) == 1
    assert rep["undetermined"][0]["missing_evidence"] == ["reference_evidence"]
    codes = {e["error_code"] for e in rep["execution_errors"]}
    assert "EXT_RAG_ADAPTER_TIMEOUT" in codes
    # disjoint: a failure is never listed as undetermined/error
    assert not (set(id(x) for x in rep["failures"]) & set(id(x) for x in rep["undetermined"]))
    assert rep["summary"]["overall_score"] == pytest.approx(0.0)  # only the real score counts
    assert rep["summary"]["valid_metric_count"] == 1


# ---- unit helpers ----

def test_metric_status_mapping() -> None:
    assert metric_status({"score": 0.5}) == "completed"
    assert metric_status({"score": None, "error": {"code": "SYS_METRIC_ERROR"}}) == "error"
    assert metric_status({"score": None, "error": {"code": "BIZ_JUDGE_NOT_CONFIGURED"}}) == "not_configured"
    assert metric_status({"score": None, "comparison_basis": {"comparison_type": "ambiguous"}}) == "undetermined"
    assert metric_status({"score": None, "comparison_basis": {"comparison_type": "match"}}) == "not_run"


def test_compute_overall_score_helper() -> None:
    rows = [
        {"metric_name": "a", "score": 1.0},
        {"metric_name": "b", "score": None},
        {"metric_name": "a", "score": 0.0},
    ]
    score, valid, total = compute_overall_score(rows, {"a": 1.0, "b": 3.0})
    assert (score, valid, total) == (0.5, 2, 3)
    assert compute_overall_score([{"metric_name": "a", "score": None}])[0] is None
