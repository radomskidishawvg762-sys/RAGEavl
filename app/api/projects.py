from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_project_service, get_project_workspace_service
from app.api.schemas import (
    CreateProjectRequest,
    OrderDirection,
    ProjectListResponse,
    ProjectOut,
    ProjectSortField,
    ProjectSummaryOut,
    ProjectSummaryRun,
)
from app.services.project_service import ProjectService
from app.services.project_workspace_service import ProjectWorkspaceService

router = APIRouter(tags=["projects"])


@router.post("/projects", status_code=201, response_model=ProjectOut)
def create_project(
    body: CreateProjectRequest,
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    """FR-01 project creation (Phase 1A G1). Router is thin: validation and
    persistence live behind ProjectService -> ProjectRepository. 201 on create;
    400 BIZ_VALIDATION_FAILED (empty name / unknown domain); 409
    BIZ_PROJECT_NAME_EXISTS (duplicate)."""
    p = service.create_project(name=body.name, domain=body.domain, status=body.status)
    return ProjectOut(id=p.id, name=p.name, domain=p.domain, status=p.status,
                      created_at=p.created_at)


@router.get("/projects/{project_id}", response_model=ProjectSummaryOut)
def get_project_summary(
    project_id: str,
    service: ProjectWorkspaceService = Depends(get_project_workspace_service),
) -> ProjectSummaryOut:
    """FR-02 workspace summary (Phase 1B G5): identity + dataset/run counts +
    latest run + latest quality gate (computed by the same QualityGateService
    the /quality-gate endpoint uses — numbers cannot drift between surfaces).
    404 BIZ_NOT_FOUND for an unknown project."""
    s = service.summary(project_id)
    p = s["project"]
    latest = s["latest_run"]
    return ProjectSummaryOut(
        project=ProjectOut(id=p.id, name=p.name, domain=p.domain, status=p.status,
                           created_at=p.created_at),
        dataset_count=s["dataset_count"],
        run_count=s["run_count"],
        latest_run=ProjectSummaryRun(**latest) if latest else None,
        latest_quality_gate=s["latest_quality_gate"],
    )


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: ProjectSortField = Query("created_at"),
    order: OrderDirection = Query("desc"),
    status: str | None = Query(None),
    service: ProjectService = Depends(get_project_service),
) -> ProjectListResponse:
    rows, total = service.list_projects(
        page=page, page_size=page_size, sort=sort, order=order, status=status
    )
    return ProjectListResponse(
        items=[
            ProjectOut(
                id=p.id, name=p.name, domain=p.domain, status=p.status, created_at=p.created_at
            )
            for p in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
