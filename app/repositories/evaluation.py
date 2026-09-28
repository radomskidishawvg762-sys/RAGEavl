"""EvaluationRepository (T-12) — sole data-access boundary for evaluation runs.

Transaction-safety contract (Spec 7.1, user decision §6):
  create_run_with_lock runs in ONE transaction:
    SELECT ... FOR UPDATE on the datasets row
      -> is_locked check (DatasetLockedError -> rollback, nothing persisted)
      -> INSERT evaluation_runs (status=pending, reproducibility snapshot)
      -> datasets.is_locked = True
      -> COMMIT
  The row lock closes the check-then-insert race: a concurrent creator blocks
  on FOR UPDATE and re-reads is_locked AFTER the first transaction commits,
  so two runs can never both pass the is_locked=false check.

Services never touch Session/ORM directly (P-8) — everything goes through
this repository.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import DatasetLockedError, NotFoundError
from app.models import (
    Dataset,
    DatasetRecord,
    Diagnosis,
    EvaluationConfig,
    EvaluationResult,
    EvaluationRun,
    Recommendation,
)
from app.models import (
    MetricResult as MetricResultRow,
)
from app.repositories.base import BaseRepository


class EvaluationRepository(BaseRepository[EvaluationRun]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, EvaluationRun)

    # ---- run creation with dataset lock (one transaction) ----

    def create_run_with_lock(
        self,
        *,
        project_id: str,
        dataset_id: str,
        config_id: str,
        reproducibility_meta: dict,
    ) -> EvaluationRun:
        """Pending run + dataset lock, atomically (Spec 7.1).

        Runs in the caller's (request-scoped) transaction: SELECT ... FOR UPDATE
        -> lock check -> INSERT run -> set is_locked. The commit is explicit;
        on exception the session context rolls back, so nothing persists
        partially. (T-19: previously `with session.begin()` which conflicts
        with the autobegun request transaction.)"""
        dataset = self._session.execute(
            select(Dataset).where(Dataset.id == dataset_id).with_for_update()
        ).scalar_one_or_none()
        if dataset is None:
            raise NotFoundError(f"dataset {dataset_id} not found")
        if dataset.is_locked:
            raise DatasetLockedError(
                f"dataset {dataset_id} is locked by an existing run; "
                "create a new version to re-evaluate"
            )
        run = EvaluationRun(
            project_id=project_id,
            dataset_id=dataset_id,
            config_id=config_id,
            status="pending",
            total_records=dataset.record_count,
            reproducibility_meta=reproducibility_meta,
        )
        self._session.add(run)
        self._session.flush()
        dataset.is_locked = True
        self._session.commit()
        return run

    def get_dataset_for_update(self, dataset_id: str) -> Dataset | None:
        """Row-locked dataset read (SELECT ... FOR UPDATE)."""
        return self._session.execute(
            select(Dataset).where(Dataset.id == dataset_id).with_for_update()
        ).scalar_one_or_none()

    # ---- run state / counters ----

    def get_run(self, run_id: str) -> EvaluationRun:
        run = self._session.get(EvaluationRun, run_id)
        if run is None:
            raise NotFoundError(f"run {run_id} not found")
        return run

    def get_run_status(self, run_id: str) -> str:
        """Status read that bypasses the ORM identity map.

        get_run() is a Session.get() and the session is built with
        expire_on_commit=False, so a session that already loaded this run keeps
        serving the status it loaded. Callers that must observe another
        session's committed write (cancellation) read the column directly.
        """
        status = self._session.execute(
            select(EvaluationRun.status).where(EvaluationRun.id == run_id)
        ).scalar_one_or_none()
        if status is None:
            raise NotFoundError(f"run {run_id} not found")
        return status

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        total_records: int | None = None,
        evaluated_records: int | None = None,
        error_records: int | None = None,
        evaluation_coverage: float | None = None,
        started_at=None,
        finished_at=None,
        error_summary: dict | None = None,
        overall_score: float | None = None,
    ) -> EvaluationRun:
        # overall_score is written once, at run completion (T-14C §七).
        # None is skipped so an in-flight update never clears a final score.
        values: dict = {}
        if status is not None:
            values["status"] = status
        if total_records is not None:
            values["total_records"] = total_records
        if evaluated_records is not None:
            values["evaluated_records"] = evaluated_records
        if error_records is not None:
            values["error_records"] = error_records
        if evaluation_coverage is not None:
            values["evaluation_coverage"] = evaluation_coverage
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        if error_summary is not None:
            values["error_summary"] = error_summary
        if overall_score is not None:
            values["overall_score"] = overall_score
        if values:
            self._session.execute(update(EvaluationRun).where(EvaluationRun.id == run_id).values(**values))
            self._session.commit()
        return self.get_run(run_id)

    def cancel_run_if_active(self, run_id: str, *, finished_at) -> EvaluationRun | None:
        """Cancel only an active run; the database wins cancellation races."""
        result = self._session.execute(
            update(EvaluationRun)
            .where(
                EvaluationRun.id == run_id,
                EvaluationRun.status.in_(("pending", "running")),
            )
            .values(status="cancelled", finished_at=finished_at)
        )
        self._session.commit()
        if result.rowcount != 1:
            return None
        return self.get_run(run_id)

    def start_run_if_pending(self, run_id: str, *, started_at) -> EvaluationRun | None:
        """Start only a pending run; a committed cancellation wins the race."""
        result = self._session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id, EvaluationRun.status == "pending")
            .values(status="running", started_at=started_at)
        )
        self._session.commit()
        if result.rowcount != 1:
            return None
        return self.get_run(run_id)

    def fail_run_if_active(
        self, run_id: str, *, finished_at, error_summary: dict
    ) -> EvaluationRun | None:
        """Mark an active run failed without overwriting cancellation."""
        result = self._session.execute(
            update(EvaluationRun)
            .where(
                EvaluationRun.id == run_id,
                EvaluationRun.status.in_(("pending", "running")),
            )
            .values(status="failed", finished_at=finished_at, error_summary=error_summary)
        )
        self._session.commit()
        if result.rowcount != 1:
            return None
        return self.get_run(run_id)

    def finish_run_if_running(
        self,
        run_id: str,
        *,
        status: str,
        overall_score: float | None,
        evaluated_records: int,
        error_records: int,
        evaluation_coverage: float,
        finished_at,
        error_summary: dict | None = None,
    ) -> EvaluationRun | None:
        """Finish only a currently running run.

        A cancelled run must never be changed back to a terminal completion
        state, even when cancellation races with the executor's final write.
        """
        values = {
            "status": status,
            "evaluated_records": evaluated_records,
            "error_records": error_records,
            "evaluation_coverage": evaluation_coverage,
            "finished_at": finished_at,
        }
        if overall_score is not None:
            values["overall_score"] = overall_score
        if error_summary is not None:
            values["error_summary"] = error_summary
        result = self._session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id, EvaluationRun.status == "running")
            .values(**values)
        )
        self._session.commit()
        if result.rowcount != 1:
            return None
        return self.get_run(run_id)

    def update_run_progress(
        self,
        run_id: str,
        *,
        evaluated_records: int,
        error_records: int,
        evaluation_coverage: float,
    ) -> EvaluationRun:
        """Persist counters without changing the cancellation state."""
        self._session.execute(
            update(EvaluationRun)
            .where(
                EvaluationRun.id == run_id,
                EvaluationRun.status.in_(("running", "cancelled")),
            )
            .values(
                evaluated_records=evaluated_records,
                error_records=error_records,
                evaluation_coverage=evaluation_coverage,
            )
        )
        self._session.commit()
        return self.get_run(run_id)

    # ---- record-level persistence ----

    def list_records(self, dataset_id: str) -> list[DatasetRecord]:
        rows = self._session.execute(
            select(DatasetRecord)
            .where(DatasetRecord.dataset_id == dataset_id)
            .order_by(DatasetRecord.row_index)
        ).scalars().all()
        return list(rows)

    def insert_evaluation_result(self, result: EvaluationResult) -> EvaluationResult:
        self._session.add(result)
        self._session.commit()
        return result

    def insert_metric_results(self, rows: list[MetricResultRow]) -> list[MetricResultRow]:
        self._session.add_all(rows)
        self._session.commit()
        return rows

    def insert_diagnosis_with_recommendations(
        self, diagnosis: Diagnosis, recommendations: list[Recommendation]
    ) -> Diagnosis:
        self._session.add(diagnosis)
        self._session.flush()
        for rec in recommendations:
            rec.diagnosis_id = diagnosis.id
        self._session.add_all(recommendations)
        self._session.commit()
        return diagnosis

    def insert_diagnoses(self, diagnoses: list[Diagnosis]) -> list[Diagnosis]:
        self._session.add_all(diagnoses)
        self._session.commit()
        return diagnoses

    # ---- T-13 read/query surface (Router -> Service -> Repository only) ----

    def get_config(self, config_id: str) -> EvaluationConfig:
        cfg = self._session.get(EvaluationConfig, config_id)
        if cfg is None:
            raise NotFoundError(f"evaluation config {config_id} not found")
        return cfg

    def get_config_by_name(self, project_id: str, name: str) -> EvaluationConfig | None:
        """Existing saved config for (project, name) — used for idempotent reuse
        under the uq_eval_configs_project_name unique constraint."""
        return self._session.execute(
            select(EvaluationConfig).where(
                EvaluationConfig.project_id == project_id,
                EvaluationConfig.name == name,
            )
        ).scalar_one_or_none()

    def list_configs_for_project(self, project_id: str) -> list[EvaluationConfig]:
        """All config rows of a project (Configuration Lifecycle v1 Phase B).

        Read-only listing used by import (version allocation: latest stored
        version of a profile) and the Profiles workspace. Rows are returned in
        stable creation order; callers never mutate them (invariant 2: stored
        versions are immutable once created)."""
        return list(
            self._session.execute(
                select(EvaluationConfig)
                .where(EvaluationConfig.project_id == project_id)
                .order_by(EvaluationConfig.created_at, EvaluationConfig.id)
            ).scalars()
        )

    def create_config(
        self,
        *,
        project_id: str,
        name: str,
        domain_config: dict,
        profile_config: dict,
        pipeline_config: dict | None,
        config_version: str,
    ) -> EvaluationConfig:
        """Save a config row (T-18 / spec Appendix C POST /projects/{pid}/configs).

        The row is a thin pointer to the three-layer profile: POST /api/evaluations
        resolves domain/profile from domain_config/profile_config and re-loads the
        YAML via ConfigService — never a second config source."""
        cfg = EvaluationConfig(
            project_id=project_id,
            name=name,
            domain_config=domain_config,
            profile_config=profile_config,
            pipeline_config=pipeline_config,
            config_version=config_version,
        )
        self._session.add(cfg)
        self._session.commit()
        return cfg

    def get_dataset(self, dataset_id: str) -> Dataset:
        ds = self._session.get(Dataset, dataset_id)
        if ds is None:
            raise NotFoundError(f"dataset {dataset_id} not found")
        return ds

    def list_runs(
        self,
        *,
        page: int,
        page_size: int,
        project_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[EvaluationRun], int]:
        base = select(EvaluationRun)
        if project_id is not None:
            base = base.where(EvaluationRun.project_id == project_id)
        if status is not None:
            base = base.where(EvaluationRun.status == status)
        total = int(
            self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        )
        rows = (
            self._session.execute(
                base.order_by(EvaluationRun.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    def list_results(
        self,
        run_id: str,
        *,
        page: int,
        page_size: int,
        is_failure: bool | None = None,
    ) -> tuple[list[EvaluationResult], int]:
        base = select(EvaluationResult).where(EvaluationResult.run_id == run_id)
        if is_failure is not None:
            base = base.where(EvaluationResult.is_failure == is_failure)
        total = int(
            self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        )
        rows = (
            self._session.execute(
                base.order_by(EvaluationResult.row_index).offset((page - 1) * page_size).limit(page_size)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    def get_result(self, run_id: str, result_id: str) -> EvaluationResult:
        """Single result scoped to its run (Phase 1A G3). Unknown run OR a
        result from a different run -> BIZ_NOT_FOUND."""
        row = self._session.execute(
            select(EvaluationResult).where(
                EvaluationResult.run_id == run_id,
                EvaluationResult.id == result_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"result {result_id} not found in run {run_id}")
        return row

    def list_result_metric_rows(self, result_id: str) -> list[MetricResultRow]:
        """Metric rows of one result, INCLUDING comparison_basis — read-only
        echo of persisted rows (FR-18), never recomputed."""
        rows = self._session.execute(
            select(MetricResultRow)
            .where(MetricResultRow.result_id == result_id)
            .order_by(MetricResultRow.metric_name)
        ).scalars().all()
        return list(rows)

    def list_diagnoses_for_result(self, result_id: str) -> list[Diagnosis]:
        rows = self._session.execute(
            select(Diagnosis)
            .where(Diagnosis.result_id == result_id)
            .order_by(Diagnosis.created_at)
        ).scalars().all()
        return list(rows)

    def list_diagnoses(
        self,
        run_id: str,
        *,
        page: int,
        page_size: int,
        severity: str | None = None,
        failure_type: str | None = None,
    ) -> tuple[list[Diagnosis], int]:
        base = select(Diagnosis).where(Diagnosis.run_id == run_id)
        if severity is not None:
            base = base.where(Diagnosis.severity == severity)
        if failure_type is not None:
            base = base.where(Diagnosis.failure_type == failure_type)
        total = int(
            self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        )
        rows = (
            self._session.execute(
                base.order_by(Diagnosis.created_at).offset((page - 1) * page_size).limit(page_size)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    def list_recommendations(self, run_id: str) -> list[Recommendation]:
        rows = self._session.execute(
            select(Recommendation)
            .join(Diagnosis, Recommendation.diagnosis_id == Diagnosis.id)
            .where(Diagnosis.run_id == run_id)
            .order_by(Recommendation.priority)
        ).scalars().all()
        return list(rows)

    # ---- T-14C: read-only aggregation queries (Report never recomputes) ----

    def list_metric_rows_for_run(self, run_id: str) -> list[dict]:
        """One row per (record, metric) with the record's identity — the raw
        material for Report aggregation. Read-only; scores are never recalculated.

        Payload discipline: full comparison_basis JSONB is NOT selected (the
        aggregation surface only needs `comparison_type` for status
        classification; per-record basis lives behind the result-detail API).
        `question` is likewise not duplicated per metric row."""
        stmt = (
            select(
                MetricResultRow.id,
                MetricResultRow.result_id,
                MetricResultRow.metric_name,
                MetricResultRow.category,
                MetricResultRow.score,
                MetricResultRow.threshold,
                MetricResultRow.passed,
                MetricResultRow.comparison_basis["comparison_type"].astext.label("comparison_type"),
                MetricResultRow.error,
                MetricResultRow.metric_version,
                EvaluationResult.record_id,
                EvaluationResult.is_failure,
            )
            .join(EvaluationResult, MetricResultRow.result_id == EvaluationResult.id)
            .where(EvaluationResult.run_id == run_id)
        )
        rows = self._session.execute(stmt).all()
        return [
            {
                "id": r[0], "result_id": r[1], "metric_name": r[2], "category": r[3],
                "score": r[4], "threshold": r[5], "passed": r[6],
                "comparison_type": r[7], "error": r[8], "metric_version": r[9],
                "record_id": r[10], "is_failure": r[11],
            }
            for r in rows
        ]

    def list_metric_basis_rows_for_run(self, run_id: str) -> list[dict]:
        """Persisted comparison_basis for one run — read-only material for the
        report EXPORT (the interactive report deliberately omits this heavy
        JSONB). Never recomputes: values are the stored judgment basis."""
        stmt = (
            select(
                MetricResultRow.metric_name,
                MetricResultRow.comparison_basis,
                EvaluationResult.record_id,
            )
            .join(EvaluationResult, MetricResultRow.result_id == EvaluationResult.id)
            .where(EvaluationResult.run_id == run_id,
                   MetricResultRow.comparison_basis.isnot(None))
        )
        rows = self._session.execute(stmt).all()
        return [
            {"metric_name": r[0], "comparison_basis": r[1], "record_id": r[2]}
            for r in rows
        ]

    def list_results_for_run(self, run_id: str) -> list:
        """Slim result rows for report joins (id/record_id/question only) —
        answer/contexts payloads are never needed for aggregation and stay
        behind the result-detail API."""
        rows = self._session.execute(
            select(
                EvaluationResult.id,
                EvaluationResult.record_id,
                EvaluationResult.question,
            )
            .where(EvaluationResult.run_id == run_id)
            .order_by(EvaluationResult.row_index)
        ).all()
        return list(rows)

    def list_diagnoses_for_run(self, run_id: str) -> list[Diagnosis]:
        rows = self._session.execute(
            select(Diagnosis).where(Diagnosis.run_id == run_id).order_by(Diagnosis.created_at)
        ).scalars().all()
        return list(rows)
