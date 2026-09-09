"""T-16B REST API — /api/comparisons (read-only two-run comparison).

Router responsibilities are THIN: validate query params, delegate to
ComparisonService, serialize. No ORM, no Engine/Judge/RAG/Diagnosis logic
here — all of it stays behind Service (§八)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_comparison_service, get_regression_service
from app.api.schemas import ComparisonResponse, RegressionResponse
from app.services.comparison_service import ComparisonService
from app.services.regression_service import RegressionService

router = APIRouter(tags=["comparisons"])


@router.get("/comparisons", response_model=ComparisonResponse)
def compare_runs(
    baseline_run_id: str = Query(...),
    candidate_run_id: str = Query(...),
    service: ComparisonService = Depends(get_comparison_service),
) -> ComparisonResponse:
    data = service.compare(baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id)
    return ComparisonResponse(**data)


@router.get("/comparisons/regression", response_model=RegressionResponse)
def regression(
    baseline_run_id: str = Query(...),
    candidate_run_id: str = Query(...),
    service: RegressionService = Depends(get_regression_service),
) -> RegressionResponse:
    data = service.compute(baseline_run_id=baseline_run_id, candidate_run_id=candidate_run_id)
    return RegressionResponse(**data)
