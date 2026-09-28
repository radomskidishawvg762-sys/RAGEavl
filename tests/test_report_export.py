"""Report export (one-click PDF + JSON) — read-only contract tests.

Locks:
  - canonical JSON payload shape (run/quality_dimensions/metrics/failures/
    diagnoses/evidence/recommendations/summary);
  - honesty: null never 0; registered-not-enabled absent; undetermined kept
    separate from failures; evidence passed through unchanged (never
    regenerated); execution errors never become quality failures;
  - READ-ONLY: export performs zero writes (guarded repo);
  - secrets never reach the payload;
  - PDF renders (bytes, %PDF, paginated); endpoints: default pdf, json,
    bad format 422, unknown run 404.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_export_service
from app.main import app
from app.models import Diagnosis, EvaluationResult, MetricResult, Recommendation
from app.services.export_service import ExportService
from app.services.report_service import ReportService
from tests.test_evaluation_service import FakeEvaluationRepo

BASIS = {
    "reference": {"primary": {"raw": "1亿元", "value": 1e8, "unit": "亿元"}, "candidates": [],
                  "side_status": "ok"},
    "answer": {"primary": {"raw": "2亿元", "value": 2e8, "unit": "亿元"}, "candidates": [],
               "side_status": "ok"},
    "comparison_type": "value_mismatch", "method": "deterministic",
    "tolerance_applied": None, "diff": {"base": {"reference": 1e8, "answer": 2e8, "abs_diff": 1e8}},
}
EVIDENCE = [
    {"type": "reference_evidence", "source": "reference_answer",
     "locator": "record.reference_answer", "content": "1亿元", "metadata": None},
    {"type": "answer_claim", "source": "answer", "locator": "claim[0]",
     "content": "2亿元", "metadata": None},
]
META = {
    "dataset_version": "v1", "config_version": "cfg-1", "metric_version": "integrity-normalization-v2",
    "enabled_metrics": ["numerical_consistency", "temporal_consistency", "entity_consistency"],
    "metric_weights": {"numerical_consistency": 1.0, "temporal_consistency": 1.0,
                       "entity_consistency": 1.0},
    "judge_provider": "openai", "judge_model": "glm-5.3-flash", "judge_model_version": "glm-2026",
    "prompt_version": None, "model_version": None, "timestamp": "2026-09-01T00:00:00+00:00",
    "rag_input": {"mode": "golden_replay"}, "profile": "default", "quality_gate": None,
    "pipeline": {"engines": ["integrity"], "diagnosis": {"enabled": True}},
}


# ---- export read-surface extensions on the shared fake (same pattern as T-14C) ----

def _fake_list_metric_basis_rows_for_run(self, run_id: str) -> list[dict]:
    by_result = {r["id"]: r for r in self.eval_results if r["run_id"] == run_id}
    return [
        {"metric_name": m["metric_name"], "comparison_basis": m["comparison_basis"],
         "record_id": by_result[m["result_id"]]["record_id"]}
        for m in self.metric_results
        if m["result_id"] in by_result and m["comparison_basis"] is not None
    ]


def _fake_get_dataset_with_name(self, dataset_id: str):
    ds = self.datasets.get(dataset_id)
    if ds is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"dataset {dataset_id} not found")
    return SimpleNamespace(id=dataset_id, name=ds.get("name", "it-ds"),
                           version=ds.get("version", "v1"))


FakeEvaluationRepo.list_metric_basis_rows_for_run = _fake_list_metric_basis_rows_for_run
FakeEvaluationRepo.get_dataset = _fake_get_dataset_with_name


class _ReadOnlyRepo(FakeEvaluationRepo):
    """Any mutation attempt breaks the test — export must be pure read."""

    def _fail(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(f"export attempted a repository mutation: {args} {kwargs}")

    insert_evaluation_result = _fail
    insert_metric_results = _fail
    insert_diagnosis_with_recommendations = _fail
    update_run = _fail
    update_run_progress = _fail
    start_run_if_pending = _fail
    finish_run_if_running = _fail
    cancel_run_if_active = _fail
    fail_run_if_active = _fail
    create_run_with_lock = _fail


def _seed_repo(*, completed: bool = True) -> tuple[FakeEvaluationRepo, str]:
    repo = FakeEvaluationRepo(
        {"ds1": {"is_locked": False, "record_count": 2, "version": "v1", "name": "golden50"}}, []
    )
    run = repo.create_run_with_lock(project_id="p1", dataset_id="ds1", config_id="c1",
                                    reproducibility_meta=META)
    repo.datasets["ds1"]["is_locked"] = True  # the real run creation locks the version
    if completed:
        repo.start_run_if_pending(run.id, started_at=run.created_at)
        repo.finish_run_if_running(run.id, status="completed", overall_score=0.6667,
                                   evaluated_records=2, error_records=0,
                                   evaluation_coverage=1.0, finished_at=run.created_at)

    res1 = repo.insert_evaluation_result(EvaluationResult(
        run_id=run.id, record_id="r1", row_index=0, question="营收多少",
        contexts=["c"], answer="2亿元", reference_answer="1亿元",
        reference_contexts=None, is_failure=True))
    repo.insert_metric_results([
        MetricResult(result_id=res1.id, metric_name="numerical_consistency",
                     category="integrity", score=0.0, threshold=None, passed=None,
                     comparison_basis=BASIS, metric_version=META["metric_version"], error=None),
        # judgment with evidence is on the diagnosis; an ERROR row stays an error:
        MetricResult(result_id=res1.id, metric_name="entity_consistency",
                     category="integrity", score=None, threshold=None, passed=None,
                     comparison_basis=None, metric_version=META["metric_version"],
                     error={"code": "EXT_JUDGE_UNAVAILABLE", "message": "429 weekly limit"}),
    ])
    repo.insert_diagnosis_with_recommendations(
        Diagnosis(run_id=run.id, result_id=res1.id, status="diagnosed",
                  failure_type="integrity.numerical_mismatch",
                  related_metric="numerical_consistency",
                  root_cause="数值不一致 (abs_diff=100000000.0)", severity="CRITICAL",
                  evidence=EVIDENCE, evidence_contract="integrity.numerical_mismatch.v1",
                  confidence="high", detail=None),
        [Recommendation(action="统一数值单位归一化", priority=1, source="rule")],
    )

    res2 = repo.insert_evaluation_result(EvaluationResult(
        run_id=run.id, record_id="r2", row_index=1, question="去年情况",
        contexts=["c"], answer="今年增长", reference_answer="去年增长",
        reference_contexts=None, is_failure=False))
    repo.insert_metric_results([MetricResult(
        result_id=res2.id, metric_name="temporal_consistency", category="integrity",
        score=None, threshold=None, passed=None,
        comparison_basis={"reference": {"primary": {"raw": "去年"}, "candidates": [],
                                        "side_status": "ambiguous"},
                          "answer": {"primary": {"raw": "今年"}, "candidates": [],
                                     "side_status": "ambiguous"},
                          "comparison_type": "ambiguous", "method": "deterministic",
                          "tolerance_applied": None, "diff": None},
        metric_version=META["metric_version"], error=None)])
    repo.insert_diagnosis_with_recommendations(
        Diagnosis(run_id=run.id, result_id=res2.id, status="undetermined",
                  failure_type=None, related_metric="temporal_consistency",
                  root_cause=None, severity="INFO", evidence=[], evidence_contract="",
                  confidence="low",
                  detail={"reason": "relative time without anchor", "missing_evidence": []}),
        [],
    )
    return repo, run.id


def _service(repo: FakeEvaluationRepo) -> ExportService:
    return ExportService(repo, ReportService(repo))


# ---- canonical JSON shape ----

def test_json_export_canonical_sections() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    for key in ("run", "quality_dimensions", "metrics", "failures", "diagnoses",
                "evidence", "recommendations", "summary"):
        assert key in payload, key
    assert payload["run"]["run_id"] == run_id
    assert payload["run"]["dataset_name"] == "golden50"
    assert payload["run"]["dataset_version"] == "v1"


def test_metrics_only_actual_run_registered_not_enabled_absent() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    names = {m["name"] for m in payload["metrics"]}
    assert names == {"numerical_consistency", "temporal_consistency", "entity_consistency"}
    # answer_relevancy is REGISTERED in the code registry but was not enabled —
    # it must not appear as if executed:
    assert "answer_relevancy" not in names
    assert "context_recall" not in names


def test_null_is_never_zero_filled() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    by = {m["name"]: m for m in payload["metrics"]}
    assert by["temporal_consistency"]["score"] is None  # not 0.0
    assert by["entity_consistency"]["status"] == "error"  # error stays error
    assert payload["failures"]  # only the confirmed numerical mismatch
    assert all(f["related_metric"] == "numerical_consistency" for f in payload["failures"])


def test_evidence_is_pass_through_never_regenerated() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    (f,) = payload["failures"]
    assert f["evidence"] == EVIDENCE  # exact persisted JSONB
    assert f["comparison_basis"] == BASIS  # persisted basis, not recomputed
    (ev,) = payload["evidence"]
    assert ev["items"] == EVIDENCE
    assert ev["record_id"] == "r1"


def test_undetermined_separate_with_explanation() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    assert len(payload["undetermined"]) == 1
    assert payload["undetermined"][0]["record_id"] == "r2"
    assert payload["summary"]["undetermined"]["count"] == 1
    assert "≠ Failure" in payload["summary"]["undetermined"]["explanation"]
    assert payload["summary"]["undetermined"]["ratio_of_metric_rows"] == pytest.approx(1 / 3, abs=0.01)


def test_summary_is_rule_based_over_persisted_rows() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    s = payload["summary"]
    assert s["most_failed_metric"] == {"metric": "numerical_consistency", "count": 1}
    assert s["most_frequent_diagnosis"]["failure_type"] == "integrity.numerical_mismatch"
    assert s["highest_impact_recommendation"]["affected_records"] == 1
    assert any("最弱" in line or "weakest" in line for line in s["assessment_lines"])
    (rec,) = payload["recommendations"]
    assert rec["related_failure_type"] == "integrity.numerical_mismatch"
    assert rec["affected_record_id"] == "r1"


def test_reproducibility_fields_present() -> None:
    repo, run_id = _seed_repo()
    rp = _service(repo).build_export(run_id)["reproducibility"]
    assert rp["dataset_version"] == "v1" and rp["profile"] == "default"
    assert rp["config_version"] == "cfg-1"
    assert rp["metric_version"] == "integrity-normalization-v2"
    assert rp["judge_provider"] == "openai" and rp["judge_model"] == "glm-5.3-flash"
    assert rp["rag_input"] == "golden_replay"
    assert rp["timestamp"] == "2026-09-01T00:00:00+00:00"


def test_export_carries_no_secrets() -> None:
    repo, run_id = _seed_repo()
    payload = _service(repo).build_export(run_id)
    blob = json.dumps(payload, default=str)
    for needle in ("api_key", "sk-", "postgresql+psycopg", "password"):
        assert needle not in blob


# ---- read-only guard ----

def test_export_performs_no_writes() -> None:
    repo, run_id = _seed_repo()
    guard = _ReadOnlyRepo(repo.datasets, repo.records)
    # replicate the seeded rows into the guarded repo
    guard.runs.update(repo.runs)
    guard.eval_results.extend(repo.eval_results)
    guard.metric_results.extend(repo.metric_results)
    guard.diagnoses.extend(repo.diagnoses)
    guard.recommendations.extend(repo.recommendations)
    payload = _service(guard).build_export(run_id)
    assert payload["run"]["status"] == "completed"
    _service(guard).render_pdf(run_id)  # must not touch writes either


# ---- PDF ----

def test_pdf_render_paginates_and_headers() -> None:
    repo, run_id = _seed_repo()
    pdf = _service(repo).render_pdf(run_id)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 3000
    assert pdf.count(b"/Type /Page") >= 1
    from app.services.report_pdf import export_filename

    assert export_filename(run_id, "pdf").endswith(".pdf")
    assert " " not in export_filename(run_id, "pdf")  # ASCII-safe download name


# ---- endpoints ----

@pytest.fixture()
def client_with_export():
    repo, run_id = _seed_repo()
    service = ExportService(repo, ReportService(repo))
    app.dependency_overrides[get_export_service] = lambda: service
    with TestClient(app) as client:
        yield client, run_id
    app.dependency_overrides.clear()


def test_endpoint_default_is_pdf(client_with_export) -> None:
    client, run_id = client_with_export
    resp = client.get(f"/api/evaluations/{run_id}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment;" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")


def test_endpoint_json_canonical(client_with_export) -> None:
    client, run_id = client_with_export
    resp = client.get(f"/api/evaluations/{run_id}/export?format=json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["run_id"] == run_id
    assert body["summary"]["undetermined"]["count"] == 1
    assert "diagnoses" in body and "evidence" in body


def test_endpoint_rejects_unknown_format(client_with_export) -> None:
    client, run_id = client_with_export
    resp = client.get(f"/api/evaluations/{run_id}/export?format=exe")
    assert resp.status_code == 422


def test_endpoint_unknown_run_404() -> None:
    repo, _ = _seed_repo()
    service = ExportService(repo, ReportService(repo))
    app.dependency_overrides[get_export_service] = lambda: service
    try:
        with TestClient(app) as client:
            resp = client.get("/api/evaluations/missing/export?format=json")
        assert resp.status_code == 404
        assert resp.json()["code"] == "BIZ_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


# ---------- PDF escaping: _s() is the boundary between data and reportlab markup ----------


def test_s_escapes_tag_like_data() -> None:
    """_s() must escape: reportlab's Paragraph parses mini-HTML."""
    from app.services.report_pdf import _s

    assert _s("a < b <c") == "a &lt; b &lt;c"
    assert _s("<br>") == "&lt;br&gt;"
    assert _s("<font color=red>x") == "&lt;font color=red&gt;x"
    assert _s("A&B") == "A&amp;B"
    # non-strings keep their formatting contract
    assert _s(None) == "—"
    assert _s(1.5) == "1.5"


def test_paragraph_builds_with_tag_like_dataset_content() -> None:
    """Regression: unescaped "<" in dataset content made Paragraph raise inside
    doc.build(), turning the PDF export into a 500 with no structured error code
    while format=json succeeded for the same run."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    from app.services.report_pdf import FONT, _s

    st = ParagraphStyle("probe", fontName=FONT, fontSize=9)
    hostile = [
        "答案 <br> 换行",
        "<font color=red>x",
        "a < b <c",
        "A&B <tag>",
        "</para>",
        "<b>unclosed",
    ]
    for value in hostile:
        Paragraph(f"问题 Question: {_s(value)}", st)  # must not raise


def test_summary_exposes_input_mode_as_a_scalar() -> None:
    """The run detail page needs RAG input mode as a SCALAR: the frontend's
    safeReproducibility drops object values, so the snapshot's `rag_input` (an
    object) never rendered — a run answering from golden-replay metadata was
    indistinguishable from one hitting a real RAG."""
    repo, run_id = _seed_repo()

    report = ReportService(repo).build_report(run_id)

    assert report["summary"]["input_mode"] == "golden_replay"


def test_input_mode_is_none_when_the_snapshot_has_no_rag_input() -> None:
    repo, run_id = _seed_repo()
    repo.runs[run_id]["reproducibility_meta"] = {"enabled_metrics": ["temporal_consistency"]}

    report = ReportService(repo).build_report(run_id)

    assert report["summary"]["input_mode"] is None
