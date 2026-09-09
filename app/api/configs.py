"""T-18 REST API — config catalog + save (read: profiles/metrics; write: config_id).

Router responsibilities are THIN (goal #9): validate input, delegate to the
service layer, serialize. No ORM, no Repository, no Engine here.

Routes:
  GET  /configs/profiles            Profile 列表（FR-10）
  GET  /configs/profiles/{name}     Profile 内容（FR-11）
  GET  /metrics                     已注册指标（FR-14）
  POST /projects/{project_id}/configs   保存配置 → config_id（FR-11/12）

The read catalog (profiles/metrics) never touches the database — it reads the
same three-layer YAML + code-registered MetricRegistry the run planner uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_catalog_service, get_config_resource_service
from app.api.schemas import (
    MetricListResponse,
    MetricOut,
    ProfileListResponse,
    ProfileOut,
    SaveConfigRequest,
    SaveConfigResponse,
)
from app.services.catalog_service import CatalogService
from app.services.config_resource_service import ConfigResourceService

router = APIRouter(tags=["configs"])


@router.get("/configs/profiles", response_model=ProfileListResponse)
def list_profiles(
    catalog: CatalogService = Depends(get_catalog_service),
) -> ProfileListResponse:
    items = catalog.list_profiles()
    return ProfileListResponse(items=[ProfileOut(**i) for i in items], total=len(items))


@router.get("/configs/profiles/{name}", response_model=ProfileOut)
def get_profile(
    name: str,
    catalog: CatalogService = Depends(get_catalog_service),
) -> ProfileOut:
    return ProfileOut(**catalog.get_profile(name))


@router.get("/metrics", response_model=MetricListResponse)
def list_metrics(
    catalog: CatalogService = Depends(get_catalog_service),
) -> MetricListResponse:
    items = catalog.list_metrics()
    return MetricListResponse(items=[MetricOut(**i) for i in items], total=len(items))


@router.post("/projects/{project_id}/configs", status_code=201, response_model=SaveConfigResponse)
def save_config(
    project_id: str,
    body: SaveConfigRequest,
    config: ConfigResourceService = Depends(get_config_resource_service),
) -> SaveConfigResponse:
    row, created, config_version = config.save(
        project_id=project_id,
        name=body.name,
        domain=body.domain,
        profile=body.profile,
        pipeline_config=body.pipeline_config,
    )
    return SaveConfigResponse(
        config_id=row.id,
        config_version=config_version,
        name=row.name,
        domain=(row.domain_config or {}).get("domain", body.domain),
        profile=(row.profile_config or {}).get("profile", body.profile),
        created=created,
    )
