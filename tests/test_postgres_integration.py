"""Real PostgreSQL integration tests (Phase 2 — TEST_DATABASE_URL isolation).

Covers the mandated scenarios against a DEDICATED test database
(TEST_DATABASE_URL ≠ DATABASE_URL, enforced in conftest):
  transaction · JSONB roundtrip · dataset row lock · config persistence ·
  run persistence · cancel race (incl. concurrent) · diagnosis persistence.

Skipped honestly (never counted as passed) when TEST_DATABASE_URL is not
configured or unreachable. Production DATABASE_URL is never written.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import OperationalError

from app.core.errors import AppError, RunNotCancellableError
from app.models import (
    Dataset,
    DatasetRecord,
    Diagnosis,
    EvaluationConfig,
    EvaluationResult,
    EvaluationRun,
    MetricResult,
    Project,
    Recommendation,
)
from app.repositories.evaluation import EvaluationRepository
from app.services.evaluation_service import EvaluationService

pytestmark = pytest.mark.postgres

_TEN_BUSINESS_TABLES = {
    "projects", "datasets", "dataset_records", "evaluation_configs",
    "evaluation_runs", "evaluation_results", "metric_results",
    "diagnoses", "recommendations", "metric_definitions",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(factory, *, locked: bool = False) -> tuple[str, str, str]:
    """Minimal (project, config, dataset) chain; returns their ids.

    IDs are assigned EXPLICITLY: column defaults (`default=_uuid`) apply at
    flush time, so `proj.id` would still be None when the dependent rows are
    constructed — these tests run against real FK constraints (first executed
    2026-09-07 after local TEST_DATABASE_URL became available)."""
    pid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    dsid = str(uuid.uuid4())
    with factory() as s:
        proj = Project(id=pid, name=f"it-pg-{uuid.uuid4().hex[:10]}")
        # flush each parent BEFORE adding its dependent row: the models declare
        # bare FK columns (no relationship()), so one-shot add_all does not
        # guarantee parent-before-child INSERT ordering under real FKs.
        s.add(proj)
        s.flush()
        cfg = EvaluationConfig(
            id=cid, project_id=pid, name="it-cfg", domain_config={"domain": "general"},
            profile_config={"profile": "default"}, pipeline_config=None,
            config_version="it-version",
        )
        s.add(cfg)
        s.flush()
        ds = Dataset(id=dsid, project_id=pid, name="it-ds", version="v1", record_count=2,
                     validation_status="valid", validation_report=None, is_locked=locked)
        s.add(ds)
        s.commit()
        return pid, cid, dsid


def _add_record(factory, dsid: str) -> str:
    with factory() as s:
        rec = DatasetRecord(dataset_id=dsid, row_index=0, question="q?",
                            reference_answer="1亿元", reference_contexts=None, metadata_={})
        s.add(rec)
        s.commit()
        return rec.id


# ---------------- migration ----------------


def test_migration_creates_all_ten_business_tables(pg_engine) -> None:
    present = set(inspect(pg_engine).get_table_names())
    assert _TEN_BUSINESS_TABLES.issubset(present)


# ---------------- transaction ----------------


def test_create_run_on_locked_dataset_reuses_immutable_version(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db, locked=True)
    record_id = _add_record(pg_db, dsid)
    with pg_db() as s:
        repo = EvaluationRepository(s)
        first = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                          reproducibility_meta={"revision": "first"})
        second = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                           reproducibility_meta={"revision": "second"})
        assert first.id != second.id
    with pg_db() as s:
        assert s.scalar(select(func.count()).select_from(EvaluationRun)) == 2
        assert s.get(EvaluationRun, first.id).reproducibility_meta == {"revision": "first"}
        assert s.get(EvaluationRun, second.id).reproducibility_meta == {"revision": "second"}
        assert s.get(Dataset, dsid).is_locked is True
        assert s.get(DatasetRecord, record_id).reference_answer == "1亿元"


def test_create_run_and_dataset_lock_commit_atomically(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    with pg_db() as s:
        run = EvaluationRepository(s).create_run_with_lock(
            project_id=pid, dataset_id=dsid, config_id=cid,
            reproducibility_meta={"dataset_version": "v1"},
        )
        run_id = run.id
    with pg_db() as s:
        row = s.get(EvaluationRun, run_id)
        assert row is not None
        assert row.status == "pending" and row.total_records == 2
        assert row.reproducibility_meta == {"dataset_version": "v1"}
        assert s.get(Dataset, dsid).is_locked is True


@pytest.mark.parametrize(
    ("status", "count", "foreign_resource", "expected_code"),
    [
        ("invalid", 2, None, "BIZ_DATASET_NOT_EVALUABLE"),
        ("valid", 0, None, "BIZ_DATASET_NOT_EVALUABLE"),
        ("valid", 2, "dataset", "BIZ_PROJECT_MISMATCH"),
        ("valid", 2, "config", "BIZ_PROJECT_MISMATCH"),
    ],
)
def test_rejected_creation_never_locks_or_persists(
    pg_db, status, count, foreign_resource, expected_code,
) -> None:
    pid, cid, dsid = _seed(pg_db)
    with pg_db() as s:
        dataset = s.get(Dataset, dsid)
        dataset.validation_status = status
        dataset.record_count = count
        if foreign_resource:
            other_pid = str(uuid.uuid4())
            s.add(Project(id=other_pid, name="other-project"))
            s.flush()
            if foreign_resource == "dataset":
                dataset.project_id = other_pid
            else:
                s.get(EvaluationConfig, cid).project_id = other_pid
        s.commit()
    with pg_db() as s, pytest.raises(AppError) as exc:
        EvaluationService(EvaluationRepository(s)).create_run(
            project_id=pid, dataset_id=dsid, config_id=cid, reproducibility_meta={},
        )
    assert exc.value.code == expected_code
    with pg_db() as s:
        assert s.scalar(select(func.count()).select_from(EvaluationRun)) == 0
        assert s.get(Dataset, dsid).is_locked is False


def test_creation_refreshes_validation_changed_after_preload(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    with pg_db() as stale_session:
        repo = EvaluationRepository(stale_session)
        previously_loaded = repo.get_dataset(dsid)
        assert previously_loaded.validation_status == "valid"
        with pg_db() as writer:
            writer.get(Dataset, dsid).validation_status = "invalid"
            writer.commit()
        with pytest.raises(AppError) as exc:
            repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                      reproducibility_meta={})
        assert exc.value.code == "BIZ_DATASET_NOT_EVALUABLE"
    with pg_db() as s:
        assert s.scalar(select(func.count()).select_from(EvaluationRun)) == 0
        assert s.get(Dataset, dsid).is_locked is False


def test_concurrent_creators_both_reuse_dataset_with_separate_runs(pg_db) -> None:
    from concurrent.futures import ThreadPoolExecutor

    pid, cid, dsid = _seed(pg_db)
    ready = threading.Barrier(2)

    def create(index):
        with pg_db() as s:
            repo = EvaluationRepository(s)
            dataset = repo.get_dataset(dsid)
            ready.wait(timeout=10)
            run = repo.create_run_with_lock(project_id=pid, dataset_id=dataset.id,
                                            config_id=cid, reproducibility_meta={"index": index})
            return run.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, [0, 1]))
    assert len(set(ids)) == 2
    with pg_db() as s:
        assert s.scalar(select(func.count()).select_from(EvaluationRun)) == 2
        assert s.get(Dataset, dsid).is_locked is True
        assert {s.get(EvaluationRun, rid).reproducibility_meta["index"] for rid in ids} == {0, 1}


# ---------------- JSONB roundtrip ----------------


def test_jsonb_roundtrip_run_meta_and_comparison_basis(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    meta = {
        "dataset_version": "v1", "config_version": "abc", "metric_version": "m1",
        "prompt_version": None, "judge_model": "test", "judge_model_version": "1",
        "model_version": None, "timestamp": "2026-09-04T00:00:00+00:00",
        "enabled_metrics": ["numerical_consistency"],
        "metric_weights": {"numerical_consistency": 1.0},
        "effective_metrics": {"numerical_consistency": {"threshold": 0.9, "weight": 1.0}},
        "metric_overrides": {},
        "pipeline": {"engines": ["integrity"], "selected_engines": None,
                     "excluded_metrics": {}, "diagnosis": {"enabled": True}},
    }
    basis = {
        "reference": {"primary": {"raw": "1亿元", "value": 1e8}, "candidates": [],
                      "side_status": "ok"},
        "answer": {"primary": {"raw": "2亿元", "value": 2e8}, "candidates": [],
                   "side_status": "ok"},
        "comparison_type": "value_mismatch", "method": "deterministic",
        "tolerance_applied": None, "diff": {"base": {"abs_diff": 1e8}},
    }
    record_id = _add_record(pg_db, dsid)
    with pg_db() as s:
        repo = EvaluationRepository(s)
        run = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                        reproducibility_meta=meta)
        result = repo.insert_evaluation_result(EvaluationResult(
            run_id=run.id, record_id=record_id, row_index=0, question="q?",
            contexts=["c1"], answer="2亿元", reference_answer="1亿元",
            reference_contexts=None, is_failure=True))
        repo.insert_metric_results([MetricResult(
            result_id=result.id, metric_name="numerical_consistency", category="integrity",
            score=0.0, threshold=0.9, passed=False, comparison_basis=basis,
            metric_version="integrity-normalization-v1", error=None)])
        result_id, run_id = result.id, run.id
    with pg_db() as s:
        assert s.get(EvaluationRun, run_id).reproducibility_meta == meta
        metric = s.get(MetricResult, s.scalar(
            select(MetricResult.id).where(MetricResult.result_id == result_id)
        ))
        assert metric.comparison_basis == basis


# ---------------- config persistence ----------------


def test_evaluation_config_persistence_roundtrip(pg_db) -> None:
    pid, _cid, _dsid = _seed(pg_db)
    domain = {"domain": "general", "recommended_metrics": ["faithfulness"]}
    profile = {"metrics": {"faithfulness": {"enabled": True, "threshold": None, "weight": 2.0}}}
    pipeline = {"engines": ["ragas", "integrity"], "diagnosis": {"enabled": True}}
    name = f"it-cfg-{uuid.uuid4().hex[:8]}"
    with pg_db() as s:
        cfg = EvaluationRepository(s).create_config(
            project_id=pid, name=name, domain_config=domain, profile_config=profile,
            pipeline_config=pipeline, config_version="sha256-it",
        )
        cfg_id = cfg.id
    with pg_db() as s:
        row = s.get(EvaluationConfig, cfg_id)
        assert row.name == name and row.config_version == "sha256-it"
        assert row.domain_config == domain
        assert row.profile_config == profile
        assert row.pipeline_config == pipeline


# ---------------- dataset row lock ----------------


def test_dataset_row_lock_blocks_concurrent_writer(pg_db) -> None:
    _pid, _cid, dsid = _seed(pg_db)
    with pg_db() as sa:
        held = EvaluationRepository(sa).get_dataset_for_update(dsid)
        assert held is not None
        with pg_db() as sb:
            nowait = select(Dataset).where(Dataset.id == dsid).with_for_update(nowait=True)
            with pytest.raises(OperationalError):
                sb.execute(nowait).scalar_one()  # blocked by A's row lock
            sb.rollback()
            sa.rollback()  # release the lock
            row = sb.execute(nowait).scalar_one()
            assert row.id == dsid


# ---------------- run persistence + conditional state updates (Phase 1) ----------------


def test_run_conditional_state_updates_on_real_pg(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    with pg_db() as s:
        repo = EvaluationRepository(s)
        run = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                        reproducibility_meta={})
        run_id = run.id
        assert repo.start_run_if_pending(run_id, started_at=_now()) is not None
        assert repo.start_run_if_pending(run_id, started_at=_now()) is None
        repo.update_run_progress(run_id, evaluated_records=1, error_records=1,
                                 evaluation_coverage=0.5)
        cancelled = repo.cancel_run_if_active(run_id, finished_at=_now())
        assert cancelled is not None and cancelled.status == "cancelled"
        assert repo.cancel_run_if_active(run_id, finished_at=_now()) is None
        assert repo.finish_run_if_running(
            run_id, status="completed", overall_score=0.9, evaluated_records=2,
            error_records=0, evaluation_coverage=1.0, finished_at=_now(),
        ) is None  # cancelled must never be overwritten by completion
    with pg_db() as s:
        row = s.get(EvaluationRun, run_id)
        assert row.status == "cancelled"
        assert row.evaluated_records == 1 and row.error_records == 1
        assert row.evaluation_coverage == 0.5 and row.finished_at is not None


def test_finish_wins_and_cancel_loses_after_completion(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    with pg_db() as s:
        repo = EvaluationRepository(s)
        run = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                        reproducibility_meta={})
        run_id = run.id
        repo.start_run_if_pending(run_id, started_at=_now())
        finished = repo.finish_run_if_running(
            run_id, status="completed", overall_score=0.8, evaluated_records=2,
            error_records=0, evaluation_coverage=1.0, finished_at=_now(),
        )
        assert finished is not None and finished.status == "completed"
        assert repo.cancel_run_if_active(run_id, finished_at=_now()) is None
        with pytest.raises(RunNotCancellableError):
            EvaluationService(EvaluationRepository(s)).cancel_run(run_id)
    with pg_db() as s:
        assert s.get(EvaluationRun, run_id).status == "completed"


def test_concurrent_cancel_and_finish_exactly_one_wins(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    with pg_db() as s:
        repo = EvaluationRepository(s)
        run = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                        reproducibility_meta={})
        run_id = run.id
        repo.start_run_if_pending(run_id, started_at=_now())

    barrier = threading.Barrier(2)
    outcome: dict[str, object] = {}
    errors: list[str] = []

    def _cancel() -> None:
        try:
            with pg_db() as s:
                barrier.wait()
                outcome["cancel"] = EvaluationRepository(s).cancel_run_if_active(
                    run_id, finished_at=_now())
        except Exception as exc:  # noqa: BLE001 — surfaced via assert below
            errors.append(f"cancel: {type(exc).__name__}: {exc}")

    def _finish() -> None:
        try:
            with pg_db() as s:
                barrier.wait()
                outcome["finish"] = EvaluationRepository(s).finish_run_if_running(
                    run_id, status="completed", overall_score=0.5, evaluated_records=1,
                    error_records=0, evaluation_coverage=0.5, finished_at=_now())
        except Exception as exc:  # noqa: BLE001 — surfaced via assert below
            errors.append(f"finish: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=_cancel), threading.Thread(target=_finish)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert "cancel" in outcome and "finish" in outcome
    winners = [k for k in ("cancel", "finish") if outcome[k] is not None]
    assert len(winners) == 1  # row lock guarantees exactly one conditional UPDATE
    with pg_db() as s:
        final = s.get(EvaluationRun, run_id).status
    assert final == ("cancelled" if winners == ["cancel"] else "completed")


# ---------------- diagnosis persistence ----------------


def test_diagnosis_and_recommendations_persistence(pg_db) -> None:
    pid, cid, dsid = _seed(pg_db)
    evidence = [
        {"type": "reference_evidence", "source": "reference_answer",
         "locator": "record.reference_answer", "content": "1亿元", "metadata": None},
        {"type": "answer_claim", "source": "answer", "locator": "claim[0]",
         "content": "2亿元", "metadata": None},
    ]
    record_id = _add_record(pg_db, dsid)
    with pg_db() as s:
        repo = EvaluationRepository(s)
        run = repo.create_run_with_lock(project_id=pid, dataset_id=dsid, config_id=cid,
                                        reproducibility_meta={})
        result = repo.insert_evaluation_result(EvaluationResult(
            run_id=run.id, record_id=record_id, row_index=0, question="q?",
            contexts=["c1"], answer="2亿元", reference_answer="1亿元",
            reference_contexts=None, is_failure=True))
        drow = Diagnosis(
            run_id=run.id, result_id=result.id, status="diagnosed",
            failure_type="integrity.numerical_mismatch",
            related_metric="numerical_consistency",
            root_cause="数值不一致 (abs_diff=100000000.0)", severity="CRITICAL",
            evidence=evidence, evidence_contract="integrity.numerical_mismatch.v1",
            confidence="high", detail=None,
        )
        recs = [
            Recommendation(action="统一数值单位归一化", priority=1, source="rule"),
            Recommendation(action="检查生成端数值引用", priority=2, source="rule"),
        ]
        repo.insert_diagnosis_with_recommendations(drow, recs)
        result_id, run_id = result.id, run.id
    with pg_db() as s:
        repo = EvaluationRepository(s)
        (d,) = repo.list_diagnoses_for_result(result_id)
        assert d.status == "diagnosed"
        assert d.failure_type == "integrity.numerical_mismatch"
        assert d.evidence == evidence
        assert d.evidence_contract == "integrity.numerical_mismatch.v1"
        rec_rows = repo.list_recommendations(run_id)
        assert [r.priority for r in rec_rows] == [1, 2]
        assert all(r.diagnosis_id == d.id for r in rec_rows)
        assert all(r.source == "rule" for r in rec_rows)
