"""T-13 REST API — /api/evaluations resource.

Router responsibilities are THIN: validate input, resolve config via
service-layer helpers, create the pending run, launch the background runner,
serialize responses. No ORM, no Repository, no Engine, no Diagnosis logic,
no evaluation loop here — all of it stays behind Service (T-13 §一/§四).
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import (
    get_config_service,
    get_evaluation_launcher,
    get_evaluation_service,
    get_export_service,
    get_quality_gate_service,
    get_report_service,
)
from app.api.schemas import (
    CancelResponse,
    CreateRunRequest,
    CreateRunResponse,
    DiagnosesPageResponse,
    DiagnosisOut,
    ProgressResponse,
    QualityGateResponse,
    RecommendationOut,
    RecommendationsResponse,
    ReportResponse,
    ResultDetailOut,
    ResultMetricItem,
    ResultOut,
    ResultsPageResponse,
    RunListResponse,
    RunOut,
)
from app.services.config_resource_service import resolve_config_row
from app.services.config_service import ConfigService
from app.services.evaluation_service import EvaluationService
from app.services.export_service import ExportService
from app.services.quality_gate_service import QualityGateService
from app.services.report_service import ReportService, metric_status
from app.services.run_planner import (
    apply_metric_overrides,
    build_reproducibility_meta,
    resolve_profile,
    validate_reproducibility_meta,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluations"])


@router.post("/evaluations", status_code=202, response_model=CreateRunResponse)
async def create_evaluation(
    body: CreateRunRequest,
    service: EvaluationService = Depends(get_evaluation_service),
    config_service: ConfigService = Depends(get_config_service),
    launch=Depends(get_evaluation_launcher),
) -> CreateRunResponse:
    """Validate -> resolve profile -> create pending run (locks dataset) ->
    start the existing EvaluationRunner in the background -> 202 + run_id.

    The HTTP request never waits for evaluation (Spec 7.1 async flow);
    clients poll GET /api/evaluations/{id}/progress.
    """
    cfg = service.get_config(body.config_id)  # 404 BIZ_NOT_FOUND when missing
    domain, profile_name, profile_body = resolve_config_row(cfg)
    config_version = config_service.config_version(domain, profile_name, profile_body=profile_body)
    merged = config_service.load(domain, profile_name, profile_body=profile_body)

    # G4: Run-level overrides merge into a COPY of the merged config — the
    # effective config drives resolve_profile (-> EvalParams -> engines ->
    # metric_results.threshold) AND the reproducibility snapshot, so executed
    # parameters == persisted results == run snapshot. config_version keeps
    # its sha256(merged YAML) semantics (ADR-05/G6).
    effective, applied = apply_metric_overrides(merged, body.metric_overrides)
    plan = resolve_profile(effective)  # 409 BIZ_CONFIG_INVALID when unparseable
    # The runtime Judge overlay is part of the effective execution config and
    # therefore must be reflected in the immutable Run snapshot as public fields.
    runtime_judge = plan.params.extra.get("judge_config")
    if isinstance(runtime_judge, dict):
        effective.setdefault("system", {}).setdefault("judge", {}).update(runtime_judge)
    dataset = service.get_dataset(body.dataset_id)
    meta = build_reproducibility_meta(
        config_version=config_version,
        dataset_version=dataset.version,
        merged=effective,
        enabled_metrics=plan.enabled_metrics,
        metric_overrides=applied,
        profile=profile_name,
        profile_version=(cfg.profile_config or {}).get("version")
        or (merged.get("profile") or {}).get("version"),
        pipeline_extra=plan.params.extra.get("pipeline"),
    )
    validate_reproducibility_meta(meta)

    run = service.create_run(
        project_id=body.project_id,
        dataset_id=body.dataset_id,
        config_id=body.config_id,
        reproducibility_meta=meta,
    )

    # engines are built inside the launcher — Router never touches the Engine
    # layer; the API merely hands the profile over to the existing execution chain
    launch(run.id, enabled_metrics=plan.enabled_metrics, params=plan.params)
    return CreateRunResponse(run_id=run.id, status="pending", reproducibility_meta=meta)


@router.get("/evaluations", response_model=RunListResponse)
def list_evaluations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    project_id: str | None = Query(None),
    status: str | None = Query(None),
    service: EvaluationService = Depends(get_evaluation_service),
) -> RunListResponse:
    rows, total = service.list_runs(page=page, page_size=page_size,
                                    project_id=project_id, status=status)
    return RunListResponse(
        items=[_run_out(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.get("/evaluations/{run_id}", response_model=RunOut)
def get_evaluation(
    run_id: str, service: EvaluationService = Depends(get_evaluation_service)
) -> RunOut:
    return _run_out(service.get_run(run_id))


@router.get("/evaluations/{run_id}/progress", response_model=ProgressResponse)
def get_progress(
    run_id: str, service: EvaluationService = Depends(get_evaluation_service)
) -> ProgressResponse:
    """Minimal progress from the CURRENT run row — no second state machine."""
    run = service.get_run(run_id)
    return ProgressResponse(
        status=run.status,
        total=run.total_records,
        evaluated=run.evaluated_records,
        errors=run.error_records,
        coverage=run.evaluation_coverage,
        cancelled=run.status == "cancelled",
    )


@router.post("/evaluations/{run_id}/cancel", response_model=CancelResponse)
def cancel_evaluation(
    run_id: str, service: EvaluationService = Depends(get_evaluation_service)
) -> CancelResponse:
    service.cancel_run(run_id)  # 409 BIZ_RUN_NOT_CANCELLABLE on terminal runs
    return CancelResponse(run_id=run_id)


@router.get("/evaluations/{run_id}/results", response_model=ResultsPageResponse)
def list_results(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_failure: bool | None = Query(None),
    service: EvaluationService = Depends(get_evaluation_service),
) -> ResultsPageResponse:
    rows, total = service.list_results(run_id, page=page, page_size=page_size,
                                       is_failure=is_failure)
    return ResultsPageResponse(
        items=[
            ResultOut(
                id=r.id, record_id=r.record_id, row_index=r.row_index,
                question=r.question, answer=r.answer,
                reference_answer=r.reference_answer, is_failure=r.is_failure,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total, page=page, page_size=page_size,
    )


@router.get("/evaluations/{run_id}/diagnoses", response_model=DiagnosesPageResponse)
def list_diagnoses(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    severity: str | None = Query(None),
    failure_type: str | None = Query(None),
    service: EvaluationService = Depends(get_evaluation_service),
) -> DiagnosesPageResponse:
    rows, total = service.list_diagnoses(run_id, page=page, page_size=page_size,
                                         severity=severity, failure_type=failure_type)
    return DiagnosesPageResponse(
        items=[_diagnosis_out(d) for d in rows], total=total, page=page, page_size=page_size
    )


@router.get("/evaluations/{run_id}/results/{result_id}", response_model=ResultDetailOut)
def get_result_detail(
    run_id: str,
    result_id: str,
    service: EvaluationService = Depends(get_evaluation_service),
) -> ResultDetailOut:
    """Full raw I/O for ONE result (Phase 1A G3): contexts bodies, reference
    fields, persisted metric rows WITH comparison_basis, and diagnoses WITH
    evidence items. The list endpoint's trimming contract is untouched."""
    result, metric_rows, diagnosis_rows = service.get_result_detail(run_id, result_id)
    return ResultDetailOut(
        id=result.id,
        run_id=result.run_id,
        record_id=result.record_id,
        row_index=result.row_index,
        question=result.question,
        answer=result.answer,
        contexts=list(result.contexts or []),
        reference_answer=result.reference_answer,
        reference_contexts=list(result.reference_contexts or [])
        if result.reference_contexts is not None
        else None,
        is_failure=result.is_failure,
        created_at=result.created_at,
        metric_results=[
            ResultMetricItem(
                name=m.metric_name,
                category=m.category,
                score=m.score,
                threshold=m.threshold,
                passed=m.passed,
                status=metric_status({"score": m.score, "error": m.error,
                                      "comparison_basis": m.comparison_basis}),
                metric_version=m.metric_version,
                comparison_basis=m.comparison_basis,
                error=m.error,
            )
            for m in metric_rows
        ],
        diagnoses=[_diagnosis_out(d) for d in diagnosis_rows],
    )


@router.get("/evaluations/{run_id}/recommendations", response_model=RecommendationsResponse)
def list_recommendations(
    run_id: str, service: EvaluationService = Depends(get_evaluation_service)
) -> RecommendationsResponse:
    rows = service.list_recommendations(run_id)
    return RecommendationsResponse(
        items=[
            RecommendationOut(
                id=r.id, diagnosis_id=r.diagnosis_id, action=r.action,
                priority=r.priority, source=r.source,
            )
            for r in rows
        ]
    )


# ---- serializers (ORM -> response schema) ----

def _run_out(run) -> RunOut:
    return RunOut(
        run_id=run.id, project_id=run.project_id, dataset_id=run.dataset_id,
        config_id=run.config_id, status=run.status,
        total_records=run.total_records, evaluated_records=run.evaluated_records,
        error_records=run.error_records, evaluation_coverage=run.evaluation_coverage,
        overall_score=run.overall_score, created_at=run.created_at,
        started_at=run.started_at, finished_at=run.finished_at,
        reproducibility_meta=run.reproducibility_meta, error_summary=run.error_summary,
    )


def _diagnosis_out(d) -> DiagnosisOut:
    return DiagnosisOut(
        id=d.id, result_id=d.result_id, status=d.status, failure_type=d.failure_type,
        related_metric=d.related_metric, root_cause=d.root_cause, severity=d.severity,
        evidence_contract=d.evidence_contract,
        # G2: read-side echo of the persisted evidence JSONB — items are NEVER
        # regenerated here (ADR-07: the contract chain ends at persistence)
        evidence=list(d.evidence or []),
        confidence=d.confidence,
        detail=d.detail, created_at=d.created_at,
    )


@router.get("/evaluations/{run_id}/report", response_model=ReportResponse)
def get_report(
    run_id: str,
    request: Request,
    response: Response,
    service: ReportService = Depends(get_report_service),
) -> ReportResponse:
    """Read-only aggregation (T-14C). No Metric / RAGAS / Integrity / Judge /
    RAG-Adapter execution happens here — everything comes from persisted rows.
    overall_score is read from the run (computed once at completion).

    Terminal runs are immutable: the response carries a weak ETag over the
    immutable summary fingerprint, so repeated report views round-trip a 304
    instead of the full JSONB-aggregated payload. Non-terminal runs are
    explicitly no-store (the report is not final yet)."""
    data = service.build_report(run_id)
    summary = data["summary"]
    if summary["is_final"]:
        etag = 'W/"' + hashlib.md5(
            f"{summary['run_id']}|{summary['status']}|{summary['overall_score']}|"
            f"{summary['evaluated_records']}|{summary['error_records']}|"
            f"{summary['finished_at']}".encode()
        ).hexdigest() + '"'
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "private, must-revalidate"
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=dict(response.headers))
    else:
        response.headers["Cache-Control"] = "no-store"
    return ReportResponse(**data)


@router.get("/evaluations/{run_id}/quality-gate", response_model=QualityGateResponse)
def quality_gate(
    run_id: str,
    service: QualityGateService = Depends(get_quality_gate_service),
) -> QualityGateResponse:
    """Absolute quality standard vs a single Run (T-17). Independent of
    Regression; reads persisted results + Profile quality_gate config only."""
    data = service.evaluate(run_id)
    return QualityGateResponse(**data)


@router.get("/evaluations/{run_id}/export")
def export_report(
    run_id: str,
    service: ExportService = Depends(get_export_service),
    format: str = Query(default="pdf", pattern="^(pdf|json)$"),
):
    """One-click report export (post-freeze enhancement).

    READ-ONLY: built exclusively from persisted rows via ExportService ->
    ReportService — never re-executes Engine / Judge / RAG / Diagnosis and
    never writes. `pdf` (default) is the human bilingual document;
    `json` is the machine-readable canonical payload (FR-30 lineage)."""
    from app.services.report_pdf import export_filename

    payload = service.build_export(run_id)
    if format == "json":
        return JSONResponse(
            content=jsonable_encoder(payload),
            headers={
                "Content-Disposition": f'attachment; filename="{export_filename(run_id, "json")}"',
            },
        )
    pdf_bytes = service.render_pdf(run_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename(run_id, "pdf")}"',
            "Cache-Control": "no-store",  # PDF freshness follows run state
        },
    )
