"""Phase 1A — real PostgreSQL integration tests (G1/G2/G3).

Runs the FULL real chain Router -> Service -> Repository -> PostgreSQL against
TEST_DATABASE_URL (shared pg fixtures from conftest: dedicated test database,
TRUNCATE isolation, honest skip when unconfigured). The production DATABASE_URL
is never used for tests (conftest hard-isolation).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.main import app
from app.models import Project

pytestmark = pytest.mark.postgres


@pytest.fixture()
def db(pg_db) -> Iterator[tuple[object, Callable[..., None]]]:
    """(session factory, no-op tracker) — cleanup is pg_db's TRUNCATE."""

    def track(table: str, *ids: str) -> None:
        pass  # per-test full truncate replaces id tracking

    yield pg_db, track


@pytest.fixture()
def client(db) -> TestClient:
    factory, _track = db

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_g1_create_project_roundtrip_in_postgres(client, db) -> None:
    _factory, track = db
    resp = client.post("/api/projects", json={"name": "it-g1-project", "domain": "general"})
    assert resp.status_code == 201
    body = resp.json()
    track("projects", body["id"])

    with _factory() as s:
        row = s.get(Project, body["id"])
    assert row is not None
    assert row.name == "it-g1-project" and row.domain == "general" and row.status == "active"

    dup = client.post("/api/projects", json={"name": "it-g1-project"})
    assert dup.status_code == 409 and dup.json()["code"] == "BIZ_PROJECT_NAME_EXISTS"


def test_g2_g3_evidence_and_detail_roundtrip_in_postgres(client, db) -> None:
    factory, track = db
    from app.models import (
        Dataset,
        DatasetRecord,
        Diagnosis,
        EvaluationConfig,
        EvaluationResult,
        EvaluationRun,
        MetricResult,
        Project,
    )

    EVIDENCE = [{
        "type": "reference_evidence", "source": "reference_contexts",
        "locator": "record.reference_contexts[0]", "content": "ref 1000亿元",
        "metadata": {"normalized": {"raw": "1000亿元"}},
    }]
    BASIS = {
        "reference": {"primary": {"raw": "1000亿元", "value": 1000.0}},
        "answer": {"primary": {"raw": "1200亿元", "value": 1200.0}},
        "comparison_type": "value_mismatch", "method": "deterministic",
        "tolerance_applied": None, "diff": {"absolute": 200.0},
    }

    with factory() as s:
        proj = Project(name="it-g23-project")
        s.add(proj)
        s.flush()  # materialize proj.id (models use bare FKs, no relationship())
        cfg = EvaluationConfig(project_id=proj.id, name="it-cfg", domain_config={"domain": "general"},
                               profile_config={"profile": "default"}, pipeline_config=None,
                               config_version="it-version")
        ds = Dataset(project_id=proj.id, name="it-ds", version="v1", record_count=1,
                     validation_status="valid", validation_report=None, is_locked=False)
        s.add_all([cfg, ds])
        s.flush()
        rec = DatasetRecord(dataset_id=ds.id, row_index=0, question="q?",
                            reference_answer="ref 1000亿元", reference_contexts=["ref 1000亿元"],
                            metadata_={})
        s.add(rec)
        s.flush()
        run = EvaluationRun(project_id=proj.id, dataset_id=ds.id, config_id=cfg.id,
                            status="completed", total_records=1,
                            reproducibility_meta={"dataset_version": "v1"})
        s.add(run)
        s.flush()
        result = EvaluationResult(run_id=run.id, record_id=rec.id, row_index=0,
                                  question="q?", contexts=["ctx-1", "ctx-2"],
                                  answer="ans 1200亿元", reference_answer="ref 1000亿元",
                                  reference_contexts=["ref 1000亿元"], is_failure=True)
        s.add(result)
        s.flush()
        metric = MetricResult(result_id=result.id, metric_name="numerical_consistency",
                              category="integrity", score=0.0, threshold=0.98, passed=False,
                              comparison_basis=BASIS, metric_version="integrity.v1", error=None)
        diag = Diagnosis(run_id=run.id, result_id=result.id, status="diagnosed",
                         failure_type="integrity.numerical_mismatch",
                         related_metric="numerical_consistency",
                         root_cause="数值不一致", severity="CRITICAL",
                         evidence=EVIDENCE, evidence_contract="integrity.numerical_mismatch.v1",
                         confidence="high", detail=None)
        s.add_all([metric, diag])
        s.commit()
        track("projects", proj.id, "evaluation_configs", cfg.id, "datasets", ds.id,
              "dataset_records", rec.id, "evaluation_runs", run.id,
              "evaluation_results", result.id, "metric_results", metric.id,
              "diagnoses", diag.id)
        run_id, result_id = run.id, result.id

    # G2: evidence round-trips identically DB -> API
    diagnoses = client.get(f"/api/evaluations/{run_id}/diagnoses").json()
    (item,) = diagnoses["items"]
    assert item["evidence"] == EVIDENCE
    assert item["evidence_contract"] == "integrity.numerical_mismatch.v1"

    # G3: full I/O + comparison_basis round-trip identically DB -> API
    detail = client.get(f"/api/evaluations/{run_id}/results/{result_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["contexts"] == ["ctx-1", "ctx-2"]
    assert body["reference_contexts"] == ["ref 1000亿元"]
    (m,) = body["metric_results"]
    assert m["comparison_basis"] == BASIS
    (d,) = body["diagnoses"]
    assert d["evidence"] == EVIDENCE


def test_g5_project_summary_roundtrip_in_postgres(client, db) -> None:
    factory, track = db
    from app.models import Dataset, EvaluationConfig, EvaluationRun

    created = client.post("/api/projects", json={"name": "it-g5-project"})
    assert created.status_code == 201
    pid = created.json()["id"]
    track("projects", pid)

    with factory() as s:
        cfg = EvaluationConfig(project_id=pid, name="it-g5-cfg",
                               domain_config={"domain": "general"},
                               profile_config={"profile": "default"},
                               pipeline_config=None, config_version="it-g5-version")
        ds = Dataset(project_id=pid, name="it-g5-ds", version="v1", record_count=4,
                     validation_status="valid", validation_report=None, is_locked=True)
        s.add_all([cfg, ds])
        s.flush()  # materialize cfg.id / ds.id before the run references them
        run = EvaluationRun(project_id=pid, dataset_id=ds.id, config_id=cfg.id,
                            status="completed", total_records=4, evaluated_records=4,
                            overall_score=0.9, evaluation_coverage=1.0,
                            reproducibility_meta={"dataset_version": "v1",
                                                  "enabled_metrics": ["numerical_consistency"],
                                                  "metric_weights": {"numerical_consistency": 1.0}})
        s.add(run)
        s.commit()
        track("evaluation_configs", cfg.id, "datasets", ds.id, "evaluation_runs", run.id)

    summary = client.get(f"/api/projects/{pid}")
    assert summary.status_code == 200
    body = summary.json()
    assert body["project"]["id"] == pid
    assert body["dataset_count"] == 1 and body["run_count"] == 1
    latest = body["latest_run"]
    assert latest["run_id"] and latest["overall_score"] == 0.9
    assert latest["dataset_name"] == "it-g5-ds" and latest["dataset_version"] == "v1"
    # gate comes from the real QualityGateService over the real profile YAML
    # ("default" ships quality_gate: null -> honest NOT_EVALUABLE, never fake)
    gate = body["latest_quality_gate"]
    assert gate is not None
    assert gate["status"] in ("PASS", "FAIL", "NOT_EVALUABLE")
    assert gate["run_id"] == latest["run_id"]
