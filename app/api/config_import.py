"""Configuration Lifecycle v1 (Phase B) REST API — import / preview / export.

Routes:
  POST /configs/import/preview          Validate + preview, NO persistence (§6)
  POST /configs/import                  Validate + create NEW immutable version (§5/§7)
  GET  /configs/{config_id}/yaml        Export stored configuration YAML (§18)
  GET  /projects/{project_id}/configs   List config rows (pointer + stored versions)

Router responsibilities stay THIN: validate input shape, delegate to
ConfigImportService, serialize. Errors carry structured [{path, message}]
entries via BIZ_CONFIG_IMPORT_INVALID (422) — never a bare "YAML invalid".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_config_import_service
from app.api.schemas import (
    ConfigDetailResponse,
    ConfigDiff,
    ConfigDiffItem,
    ConfigExportYamlResponse,
    ConfigImportCommitResponse,
    ConfigImportPreviewResponse,
    ConfigImportRequest,
    ImportMetricItem,
    ProjectConfigItem,
    ProjectConfigListResponse,
)
from app.services.config_import_service import ConfigImportService

router = APIRouter(tags=["configs"])


@router.post("/configs/import/preview", response_model=ConfigImportPreviewResponse)
def import_preview(
    body: ConfigImportRequest,
    service: ConfigImportService = Depends(get_config_import_service),
) -> ConfigImportPreviewResponse:
    """Import -> Parse -> Validate -> Preview (§6). Nothing is persisted here;
    the caller creates the version explicitly via POST /configs/import."""
    payload = service.preview(project_id=body.project_id, domain=body.domain, yaml_text=body.yaml)
    return ConfigImportPreviewResponse(
        profile=payload["profile"],
        version=payload["version"],
        domain=payload["domain"],
        config_version=payload["config_version"],
        metrics=[ImportMetricItem(**m) for m in payload["metrics"]],
        severity_mapping=payload["severity_mapping"],
        quality_gate=payload["quality_gate"],
        pipeline=payload["pipeline"],
        judge=payload["judge"],
        rag_input=payload["rag_input"],
        warnings=payload["warnings"],
        diff=ConfigDiff(
            base=payload["diff"]["base"],
            items=[ConfigDiffItem(**i) for i in payload["diff"]["items"]],
        ),
        project_id=payload["project_id"],
    )


@router.post("/configs/import", status_code=201, response_model=ConfigImportCommitResponse)
def import_config(
    body: ConfigImportRequest,
    service: ConfigImportService = Depends(get_config_import_service),
) -> ConfigImportCommitResponse:
    """Formal import entry (§5). Re-validates the FULL YAML (stateless — no
    server-side pending state) and creates a new immutable version row.
    Creating a version NEVER modifies existing versions (invariant 2) and
    never touches historical runs (invariant 1)."""
    row, created, payload = service.import_version(
        project_id=body.project_id, domain=body.domain, yaml_text=body.yaml
    )
    return ConfigImportCommitResponse(
        config_id=row.id,
        created=created,
        profile=payload["profile"],
        version=payload["version"],
        domain=payload["domain"],
        config_version=payload["config_version"],
        metrics=[ImportMetricItem(**m) for m in payload["metrics"]],
        severity_mapping=payload["severity_mapping"],
        quality_gate=payload["quality_gate"],
        pipeline=payload["pipeline"],
        judge=payload["judge"],
        rag_input=payload["rag_input"],
        warnings=payload["warnings"],
        diff=ConfigDiff(
            base=payload["diff"]["base"],
            items=[ConfigDiffItem(**i) for i in payload["diff"]["items"]],
        ),
        project_id=payload["project_id"],
    )


@router.get("/configs/{config_id}", response_model=ConfigDetailResponse)
def get_config_detail(
    config_id: str,
    service: ConfigImportService = Depends(get_config_import_service),
) -> ConfigDetailResponse:
    """Profile Detail (§11) — one payload for both row shapes."""
    d = service.config_detail(config_id)
    return ConfigDetailResponse(
        config_id=d["config_id"],
        name=d["name"],
        source=d["source"],
        created_at=d["created_at"],
        profile=d["profile"],
        version=d["version"],
        domain=d["domain"],
        config_version=d["config_version"],
        metrics=[ImportMetricItem(**m) for m in d["metrics"]],
        severity_mapping=d["severity_mapping"],
        quality_gate=d["quality_gate"],
        pipeline=d["pipeline"],
        judge=d["judge"],
        rag_input=d["rag_input"],
        yaml=d.get("yaml"),
    )


@router.get("/configs/{config_id}/yaml", response_model=ConfigExportYamlResponse)
def export_yaml(
    config_id: str,
    service: ConfigImportService = Depends(get_config_import_service),
) -> ConfigExportYamlResponse:
    """Export based on the STORED configuration (§18): persisted raw YAML for
    imported rows, deployment YAML file content for legacy pointer rows.
    Import -> Export structure stability is covered by roundtrip tests."""
    data = service.export_yaml(config_id)
    return ConfigExportYamlResponse(**data)


@router.get("/projects/{project_id}/configs", response_model=ProjectConfigListResponse)
def list_project_configs(
    project_id: str,
    service: ConfigImportService = Depends(get_config_import_service),
) -> ProjectConfigListResponse:
    rows = service.list_configs(project_id)
    items = [
        ProjectConfigItem(
            config_id=r.id,
            name=r.name,
            domain=(r.domain_config or {}).get("domain", "general"),
            profile=(r.profile_config or {}).get("profile", ""),
            version=(r.profile_config or {}).get("version"),
            source=(r.profile_config or {}).get("source", "yaml_pointer"),
            config_version=r.config_version,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return ProjectConfigListResponse(items=items, total=len(items))
