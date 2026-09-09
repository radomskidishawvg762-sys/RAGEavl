"""Phase 1A G2/G3 — evidence exposure + single-result detail contract tests.

Fake repository + dependency_overrides (existing convention, no DB). Verifies
the DB -> Repository -> Service -> API chain shape: evidence items and
comparison_basis are echoed from persisted rows, never regenerated.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_evaluation_service
from app.core.errors import NotFoundError
from app.main import app
from app.models import Diagnosis, EvaluationResult
from app.models import MetricResult as MetricResultRow
from app.services.evaluation_service import EvaluationService

EVIDENCE_ITEMS = [
    {
        "type": "reference_evidence",
        "source": "reference_contexts",
        "locator": "record.reference_contexts[0]",
        "content": "2024年度公司营业收入为1000亿元",
        "metadata": {"normalized": {"raw": "2024年度公司营业收入为1000亿元"}},
    },
    {
        "type": "answer_claim",
        "source": "answer",
        "locator": "claim[0]",
        "content": "2024年公司营业收入为1200亿元",
        "metadata": {"normalized": {"raw": "1200亿元", "value": 1200.0, "unit": "亿元"}},
    },
]

COMPARISON_BASIS = {
    "reference": {"primary": {"raw": "1000亿元", "value": 1000.0, "unit": "亿元"}},
    "answer": {"primary": {"raw": "1200亿元", "value": 1200.0, "unit": "亿元"}},
    "comparison_type": "value_mismatch",
    "method": "deterministic",
    "tolerance_applied": None,
    "diff": {"absolute": 200.0},
}


def _utc() -> datetime:
    return datetime.now(UTC)


def _result_row(result_id: str = "res1", run_id: str = "run1") -> EvaluationResult:
    r = EvaluationResult(
        run_id=run_id,
        record_id="rec1",
        row_index=3,
        question="2024 年某公司营业收入是多少？",
        contexts=["ctx-1", "ctx-2"],
        answer="2024年公司营业收入为1200亿元",
        reference_answer="2024年度公司营业收入为1000亿元",
        reference_contexts=["2024年度公司营业收入为1000亿元"],
        is_failure=True,
    )
    r.id = result_id
    r.created_at = _utc()
    return r


def _metric_row(result_id: str = "res1") -> MetricResultRow:
    m = MetricResultRow(
        result_id=result_id,
        metric_name="numerical_consistency",
        category="integrity",
        score=0.0,
        threshold=0.98,
        passed=False,
        comparison_basis=COMPARISON_BASIS,
        metric_version="integrity.v1",
        error=None,
    )
    m.id = "mr1"
    m.created_at = _utc()
    return m


def _diagnosis_row(result_id: str = "res1", run_id: str = "run1",
                   status: str = "diagnosed") -> Diagnosis:
    d = Diagnosis(
        run_id=run_id,
        result_id=result_id,
        status=status,
        failure_type="integrity.numerical_mismatch" if status == "diagnosed" else None,
        related_metric="numerical_consistency",
        root_cause="生成阶段数值与参考上下文不一致" if status == "diagnosed" else None,
        severity="CRITICAL" if status == "diagnosed" else "INFO",
        evidence=EVIDENCE_ITEMS if status == "diagnosed" else [],
        evidence_contract="integrity.numerical_mismatch.v1" if status == "diagnosed" else "",
        confidence="high" if status == "diagnosed" else "low",
        detail=None if status == "diagnosed" else {"reason": "ambiguous", "missing_evidence": ["comparison_basis"]},
    )
    d.id = f"diag-{status}"
    d.created_at = _utc()
    return d


class FakeDetailRepo:
    def __init__(self) -> None:
        self.results: dict[str, EvaluationResult] = {}
        self.metrics: dict[str, list[MetricResultRow]] = {}
        self.diagnoses: dict[str, list[Diagnosis]] = {}

    # G3 surface
    def get_result(self, run_id: str, result_id: str) -> EvaluationResult:
        row = self.results.get(result_id)
        if row is None or row.run_id != run_id:
            raise NotFoundError(f"result {result_id} not found in run {run_id}")
        return row

    def list_result_metric_rows(self, result_id: str) -> list[MetricResultRow]:
        return list(self.metrics.get(result_id, []))

    def list_diagnoses_for_result(self, result_id: str) -> list[Diagnosis]:
        return list(self.diagnoses.get(result_id, []))

    # G2 surface (run-level diagnoses listing)
    def list_diagnoses(self, run_id, *, page, page_size, severity=None, failure_type=None):
        rows = [d for ds in self.diagnoses.values() for d in ds if d.run_id == run_id]
        return rows[(page - 1) * page_size : page * page_size], len(rows)

    # list endpoint (trimming contract check)
    def list_results(self, run_id, *, page, page_size, is_failure=None):
        rows = [r for r in self.results.values() if r.run_id == run_id]
        return rows[(page - 1) * page_size : page * page_size], len(rows)


@pytest.fixture(autouse=True)
def _env():
    repo = FakeDetailRepo()
    repo.results["res1"] = _result_row()
    repo.results["res-other"] = _result_row("res-other", run_id="run2")
    repo.metrics["res1"] = [_metric_row()]
    repo.diagnoses["res1"] = [
        _diagnosis_row(),
        _diagnosis_row(status="undetermined"),
    ]
    app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(repo)
    yield repo
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app)


# ---------------- G2: evidence exposure ----------------


def test_1_diagnoses_return_evidence_items() -> None:
    body = _client().get("/api/evaluations/run1/diagnoses").json()
    diagnosed = next(d for d in body["items"] if d["status"] == "diagnosed")
    assert diagnosed["evidence_contract"] == "integrity.numerical_mismatch.v1"
    # round-trip identity: API items == persisted items (no regeneration)
    assert diagnosed["evidence"] == EVIDENCE_ITEMS
    for item in diagnosed["evidence"]:
        assert set(item) == {"type", "source", "locator", "content", "metadata"}


def test_2_undetermined_diagnosis_has_empty_evidence() -> None:
    body = _client().get("/api/evaluations/run1/diagnoses").json()
    und = next(d for d in body["items"] if d["status"] == "undetermined")
    assert und["evidence"] == []
    assert und["root_cause"] is None
    assert und["detail"]["missing_evidence"] == ["comparison_basis"]


def test_3_evidence_shapes_match_evidence_contract() -> None:
    body = _client().get("/api/evaluations/run1/diagnoses").json()
    diagnosed = next(d for d in body["items"] if d["status"] == "diagnosed")
    types = {e["type"] for e in diagnosed["evidence"]}
    assert types <= {
        "reference_evidence", "retrieved_evidence", "answer_claim", "query_evidence",
        "metadata_evidence", "ranking_evidence", "configuration_evidence", "execution_evidence",
    }


# ---------------- G3: result detail ----------------


def test_4_result_detail_returns_full_io() -> None:
    resp = _client().get("/api/evaluations/run1/results/res1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contexts"] == ["ctx-1", "ctx-2"]
    assert body["reference_contexts"] == ["2024年度公司营业收入为1000亿元"]
    assert body["reference_answer"] == "2024年度公司营业收入为1000亿元"
    assert body["answer"] == "2024年公司营业收入为1200亿元"
    assert body["question"] == "2024 年某公司营业收入是多少？"
    assert body["is_failure"] is True
    assert body["row_index"] == 3


def test_5_result_detail_carries_comparison_basis() -> None:
    body = _client().get("/api/evaluations/run1/results/res1").json()
    (metric,) = body["metric_results"]
    assert metric["comparison_basis"] == COMPARISON_BASIS
    assert metric["name"] == "numerical_consistency"
    assert metric["threshold"] == 0.98
    assert metric["passed"] is False


def test_6_metric_status_derivation_matches_report() -> None:
    from app.services.report_service import metric_status

    body = _client().get("/api/evaluations/run1/results/res1").json()
    (metric,) = body["metric_results"]
    assert metric["status"] == metric_status(
        {"score": metric["score"], "error": metric["error"],
         "comparison_basis": metric["comparison_basis"]}
    )
    assert metric["status"] == "completed"  # real score -> completed execution


def test_7_result_detail_includes_diagnoses_with_evidence() -> None:
    body = _client().get("/api/evaluations/run1/results/res1").json()
    assert len(body["diagnoses"]) == 2
    diagnosed = next(d for d in body["diagnoses"] if d["status"] == "diagnosed")
    assert diagnosed["evidence"] == EVIDENCE_ITEMS


def test_8_unknown_result_404() -> None:
    resp = _client().get("/api/evaluations/run1/results/nope")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BIZ_NOT_FOUND"


def test_9_result_of_other_run_404() -> None:
    resp = _client().get("/api/evaluations/run1/results/res-other")
    assert resp.status_code == 404


def test_10_list_endpoint_stays_trimmed() -> None:
    """The G3 detail endpoint must not leak back into the list contract."""
    body = _client().get("/api/evaluations/run1/results").json()
    item = body["items"][0]
    for forbidden in ("contexts", "reference_contexts", "metric_results", "diagnoses"):
        assert forbidden not in item


def test_11_router_does_not_regenerate_evidence_or_metrics() -> None:
    """Frozen-interface guard: the router echoes persisted rows — no diagnosis
    engine, no collector, no metric execution in the API layer."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/api/evaluations.py").read_text(encoding="utf-8")
    for forbidden in ("EvidenceCollector", "DiagnosisEngine", "build_engines",
                      "FailureClassifier", "diagnose("):
        assert forbidden not in src, forbidden
