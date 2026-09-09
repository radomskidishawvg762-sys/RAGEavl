"""ProjectStatsRepository (Phase 1B G5) — read-only workspace aggregation.

Deliberately a SEPARATE repository from ProjectRepository: the frozen
invariant (test_15) requires the projects resource itself to never derive
from runs — this module owns the one place where project workspace statistics
legitimately aggregate datasets + runs. Everything is a plain count / latest
row read over existing tables; nothing is computed or cached.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Dataset, EvaluationRun


class ProjectStatsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def summary(self, project_id: str) -> dict:
        """{dataset_count, run_count, latest_run} — latest_run carries its
        dataset name/version (FK join) so the workspace card is traceable."""
        dataset_count = int(self._session.execute(
            select(func.count()).select_from(Dataset)
            .where(Dataset.project_id == project_id)
        ).scalar_one())
        run_count = int(self._session.execute(
            select(func.count()).select_from(EvaluationRun)
            .where(EvaluationRun.project_id == project_id)
        ).scalar_one())

        row = self._session.execute(
            select(EvaluationRun, Dataset.name, Dataset.version)
            .join(Dataset, EvaluationRun.dataset_id == Dataset.id)
            .where(EvaluationRun.project_id == project_id)
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
            .limit(1)
        ).first()

        latest_run = None
        if row is not None:
            run, dataset_name, dataset_version = row
            latest_run = {
                "run_id": run.id,
                "status": run.status,
                "dataset_id": run.dataset_id,
                "dataset_name": dataset_name,
                "dataset_version": dataset_version,
                "overall_score": run.overall_score,
                "total_records": run.total_records,
                "evaluated_records": run.evaluated_records,
                "error_records": run.error_records,
                "evaluation_coverage": run.evaluation_coverage,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
            }
        return {
            "dataset_count": dataset_count,
            "run_count": run_count,
            "latest_run": latest_run,
        }
