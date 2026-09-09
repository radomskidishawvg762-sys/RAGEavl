from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_dataset_service
from app.api.schemas import DatasetListResponse, DatasetOut, DatasetSortField, OrderDirection
from app.services.dataset_service import DatasetService

router = APIRouter()


@router.get("/datasets", response_model=DatasetListResponse)
def list_datasets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    project_id: str | None = Query(None),
    sort: DatasetSortField = Query("created_at"),
    order: OrderDirection = Query("desc"),
    service: DatasetService = Depends(get_dataset_service),
) -> DatasetListResponse:
    rows, total = service.list_datasets(
        page=page, page_size=page_size, project_id=project_id, sort=sort, order=order
    )
    return DatasetListResponse(
        items=[
            DatasetOut(
                id=ds.id,
                project_id=ds.project_id,
                name=ds.name,
                version=ds.version,
                record_count=ds.record_count,
                validation_status=ds.validation_status,
                is_locked=ds.is_locked,
                created_at=ds.created_at,
            )
            for ds in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/projects/{project_id}/datasets:import", status_code=201)
async def import_dataset(
    project_id: str,
    request: Request,
    service: DatasetService = Depends(get_dataset_service),
) -> dict:
    """Import + validate (API-02). Raw bytes are parsed in the adapter so that
    unparseable JSON maps to BIZ_ADAPTER_PARSE_ERROR (400), not FastAPI 422."""
    raw = await request.body()
    ds = await run_in_threadpool(service.import_dataset, project_id, raw)
    return {
        "id": ds.id,
        "project_id": ds.project_id,
        "name": ds.name,
        "version": ds.version,
        "record_count": ds.record_count,
        "validation_status": ds.validation_status,
        "validation_report": ds.validation_report,
        "is_locked": ds.is_locked,
    }


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str, service: DatasetService = Depends(get_dataset_service)
) -> dict:
    ds = service.get(dataset_id)
    return {
        "id": ds.id,
        "project_id": ds.project_id,
        "name": ds.name,
        "version": ds.version,
        "record_count": ds.record_count,
        "validation_status": ds.validation_status,
        "is_locked": ds.is_locked,
        "created_at": ds.created_at,
    }


@router.get("/datasets/{dataset_id}/records")
def list_dataset_records(
    dataset_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    service: DatasetService = Depends(get_dataset_service),
) -> dict:
    rows, total = service.list_records(dataset_id, page, page_size)
    return {
        "items": [
            {
                "row_index": r.row_index,
                "question": r.question,
                "reference_answer": r.reference_answer,
                "reference_contexts": r.reference_contexts,
                "metadata": r.metadata_,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/datasets/{dataset_id}/validation")
def get_validation(
    dataset_id: str, service: DatasetService = Depends(get_dataset_service)
) -> dict:
    return service.validation_report(dataset_id)
