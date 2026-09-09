"""Freeze Gate DB audit — READ-ONLY. Lists test-data cleanup candidates.
No DROP / TRUNCATE / DELETE (§八: 用户另行批准前不动任何数据)."""

from collections import Counter

from dotenv import load_dotenv
from sqlalchemy import func, select

load_dotenv(".env.local")

from app.db.session import open_session  # noqa: E402
from app.models import (  # noqa: E402
    Dataset,
    DatasetRecord,
    Diagnosis,
    EvaluationResult,
    EvaluationRun,
    MetricResult,
    Project,
    Recommendation,
)

TEST_PREFIXES = ("chrome-e2e-", "probe-", "probe2-", "dbg-", "e2e-", "bench-", "h1", "h2")

with open_session() as s:
    total_projects = s.execute(select(func.count()).select_from(Project)).scalar_one()
    candidates = s.execute(
        select(Project).where(
            Project.name.like("chrome-e2e-%")
            | Project.name.like("probe-%")
            | Project.name.like("probe2-%")
            | Project.name.like("dbg-%")
            | Project.name.like("e2e-%")
            | Project.name.like("bench-%")
        )
    ).scalars().all()
    print(f"projects total={total_projects}  cleanup-candidates={len(candidates)}")
    rows_counter: Counter = Counter()
    lines = []
    for p in candidates:
        ds_ids = [
            r for r in s.execute(
                select(Dataset.id).where(Dataset.project_id == p.id)
            ).scalars().all()
        ]
        run_ids = [
            r for r in s.execute(
                select(EvaluationRun.id).where(EvaluationRun.project_id == p.id)
            ).scalars().all()
        ]
        rows_counter["datasets"] += len(ds_ids)
        rows_counter["runs"] += len(run_ids)
        if ds_ids:
            rows_counter["dataset_records"] += s.execute(
                select(func.count()).select_from(DatasetRecord)
                .where(DatasetRecord.dataset_id.in_(ds_ids))
            ).scalar_one()
            rows_counter["validation_reported"] += s.execute(
                select(func.count()).select_from(Dataset)
                .where(Dataset.id.in_(ds_ids), Dataset.validation_report.is_not(None))
            ).scalar_one()
        if run_ids:
            res_ids = [
                r for r in s.execute(
                    select(EvaluationResult.id).where(EvaluationResult.run_id.in_(run_ids))
                ).scalars().all()
            ]
            rows_counter["evaluation_results"] += len(res_ids)
            rows_counter["metric_results"] += s.execute(
                select(func.count()).select_from(MetricResult)
                .where(MetricResult.result_id.in_(res_ids))
            ).scalar_one()
            rows_counter["diagnoses"] += s.execute(
                select(func.count()).select_from(Diagnosis).where(Diagnosis.run_id.in_(run_ids))
            ).scalar_one()
            rows_counter["recommendations"] += s.execute(
                select(func.count()).select_from(Recommendation)
                .where(Recommendation.diagnosis_id.in_(
                    s.execute(select(Diagnosis.id).where(Diagnosis.run_id.in_(run_ids))).scalars().all()
                ))
            ).scalar_one() if res_ids else 0
        lines.append(f"  project {p.name} id={p.id} datasets={len(ds_ids)} runs={len(run_ids)}")
    print("\n".join(lines))
    print("\ncascade row counts:", dict(rows_counter))
    print("\nNO DATA WAS MODIFIED — cleanup is a user-approved post-freeze decision.")
