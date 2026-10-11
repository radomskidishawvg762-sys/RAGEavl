"""Persist one explicitly synthetic diagnosis for rare UI-path verification.

Only the dedicated TEST_DATABASE_URL and review-ui-* projects are accepted.
Never a production evaluation result; the fixture marker is kept in detail.
"""
from __future__ import annotations

import sys
import uuid

import serve_review  # noqa: F401 — enforces test-DB isolation before app imports
from sqlalchemy import select

from app.db.session import open_session
from app.models import Diagnosis, EvaluationResult, EvaluationRun, Project

run_id = str(uuid.UUID(sys.argv[1]))
with open_session() as session:
    run = session.get(EvaluationRun, run_id)
    project = session.get(Project, run.project_id) if run else None
    if project is None or not project.name.startswith("review-ui-"):
        raise SystemExit("Only isolated review-ui fixtures may be written")
    result = session.scalar(select(EvaluationResult).where(
        EvaluationResult.run_id == run_id).order_by(EvaluationResult.row_index).limit(1))
    if result is None:
        raise SystemExit("A completed review run with persisted results is required")
    row = Diagnosis(run_id=run_id, result_id=result.id, status="undetermined",
                    failure_type=None, related_metric="numerical_consistency", root_cause=None,
                    severity="INFO", evidence=[], evidence_contract="", confidence="low",
                    detail={"reason": "cannot reliably judge (no forced diagnosis)",
                            "missing_evidence": ["reference_evidence.comparable",
                                                 "answer_claim.checkable", "comparison_basis.diff"],
                            "fixture": "review-missing-evidence"})
    session.add(row)
    session.commit()
    print(row.id)
