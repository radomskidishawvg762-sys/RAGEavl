"""T-13 API response schemas.

ORM -> Repository -> Service -> API schema. SQLAlchemy models are NEVER
returned to the client (Spec §8 / T-13 §八). snake_case per API contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.run_planner import MetricOverride

RunStatusOut = Literal[
    "pending", "running", "completed", "completed_with_errors", "failed", "cancelled",
]

OrderDirection = Literal["asc", "desc"]

ProjectSortField = Literal["id", "name", "domain", "status", "created_at"]

DatasetSortField = Literal[
    "id", "project_id", "name", "version",
    "record_count", "validation_status", "is_locked", "created_at",
]


class CreateRunRequest(BaseModel):
    project_id: str
    dataset_id: str
    config_id: str
    # Phase 1B G4: Run-level metric overrides (Spec 6.3). Field-absent = keep
    # Profile value; threshold null = clear PASS/FAIL for this run. Merged by
    # run_planner.apply_metric_overrides — the Profile YAML is never mutated.
    metric_overrides: dict[str, MetricOverride] = Field(default_factory=dict)


class CreateProjectRequest(BaseModel):
    """FR-01 project creation. status defaults to active (model contract
    active|archived); domain must reference an existing layer-2 YAML."""

    name: str
    domain: str = "general"
    status: Literal["active", "archived"] = "active"


class RunOut(BaseModel):
    run_id: str
    project_id: str
    dataset_id: str
    config_id: str
    status: RunStatusOut
    total_records: int
    evaluated_records: int
    error_records: int
    evaluation_coverage: float | None
    overall_score: float | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reproducibility_meta: dict[str, Any]
    error_summary: dict[str, Any] | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    status: str = "pending"
    reproducibility_meta: dict[str, Any] = Field(default_factory=dict)


class RunListResponse(BaseModel):
    items: list[RunOut]
    total: int
    page: int
    page_size: int


class ProjectOut(BaseModel):
    id: str
    name: str
    domain: str
    status: str
    created_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectOut]
    total: int
    page: int
    page_size: int


class DatasetOut(BaseModel):
    id: str
    project_id: str
    name: str
    version: str
    record_count: int
    validation_status: str
    is_locked: bool
    created_at: datetime


class DatasetListResponse(BaseModel):
    items: list[DatasetOut]
    total: int
    page: int
    page_size: int


class ProgressResponse(BaseModel):
    status: RunStatusOut
    total: int
    evaluated: int
    errors: int
    coverage: float | None
    cancelled: bool = False


class CancelResponse(BaseModel):
    run_id: str
    status: str = "cancelled"


class ResultOut(BaseModel):
    """Evaluation result row. contexts bodies are intentionally NOT included
    in list responses (T-13: 列表接口不返回过大的 contexts 正文)."""

    id: str
    record_id: str
    row_index: int | None
    question: str
    answer: str
    reference_answer: str | None
    is_failure: bool
    created_at: datetime


class ResultsPageResponse(BaseModel):
    items: list[ResultOut]
    total: int
    page: int
    page_size: int


class ResultMetricItem(BaseModel):
    """Per-metric row of one result, WITH the persisted comparison_basis
    (FR-18) — the list endpoint stays trimmed; only the detail endpoint
    carries these bodies (Phase 1A G3)."""

    name: str
    category: str | None = None
    score: float | None = None
    threshold: float | None = None
    passed: bool | None = None
    status: str  # report_service.metric_status — same derivation as the report
    metric_version: str | None = None
    comparison_basis: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ResultDetailOut(BaseModel):
    """Single result detail (Phase 1A G3): full raw I/O + metric results +
    diagnoses (evidence included). Replaces nothing — the list endpoint's
    trimming contract is untouched."""

    id: str
    run_id: str
    record_id: str
    row_index: int | None
    question: str
    answer: str
    contexts: list[str]
    reference_answer: str | None
    reference_contexts: list[str] | None
    is_failure: bool
    created_at: datetime
    metric_results: list[ResultMetricItem] = Field(default_factory=list)
    diagnoses: list[DiagnosisOut] = Field(default_factory=list)


class EvidenceItemOut(BaseModel):
    """One persisted Evidence item (ADR-07 contract shape, read-side echo of
    diagnoses.evidence JSONB — Phase 1A G2). Never regenerated here."""

    type: str
    source: str
    locator: str | None = None
    content: Any = None
    metadata: dict[str, Any] | None = None


class DiagnosisOut(BaseModel):
    id: str
    result_id: str | None
    status: str
    failure_type: str | None
    related_metric: str | None
    root_cause: str | None
    severity: str
    evidence_contract: str
    evidence: list[EvidenceItemOut] = Field(default_factory=list)
    confidence: str
    detail: dict[str, Any] | None = None
    created_at: datetime


class DiagnosesPageResponse(BaseModel):
    items: list[DiagnosisOut]
    total: int
    page: int
    page_size: int


class RecommendationOut(BaseModel):
    id: str
    diagnosis_id: str
    action: str
    priority: int
    source: str


class RecommendationsResponse(BaseModel):
    items: list[RecommendationOut]


# ---------------- T-14C: Report / Aggregation (read-only) ----------------

class MetricReportItem(BaseModel):
    """Per-metric aggregation. `status` keeps execution state explicit so a
    null score is never read as 0."""

    name: str
    category: str | None = None
    score: float | None = None
    threshold: float | None = None
    passed: bool | None = None
    status: str  # completed|failed|undetermined|not_configured|error|not_run
    valid_count: int
    invalid_count: int
    error_count: int
    metric_version: str | None = None
    weight: float = 1.0


class RawMetricItem(BaseModel):
    record_id: str | None = None
    name: str
    category: str | None = None
    score: float | None = None
    threshold: float | None = None
    passed: bool | None = None
    status: str
    metric_version: str | None = None


class FailureItem(BaseModel):
    diagnosis_id: str
    record_id: str | None = None
    question: str | None = None
    related_metric: str | None = None
    failure_type: str | None = None
    severity: str | None = None
    root_cause: str | None = None
    status: str = "failure"
    confidence: str | None = None


class UndeterminedItem(BaseModel):
    diagnosis_id: str
    record_id: str | None = None
    related_metric: str | None = None
    status: str = "undetermined"
    reason: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)


class ExecutionErrorItem(BaseModel):
    record_id: str | None = None
    metric: str | None = None
    error_code: str | None = None
    message: str | None = None  # sanitized (no secrets)


class ReportSummary(BaseModel):
    run_id: str
    status: RunStatusOut
    is_final: bool
    overall_score: float | None = None
    total_records: int
    evaluated_records: int
    error_records: int
    evaluation_coverage: float | None = None
    valid_metric_count: int
    total_enabled_metric_count: int
    # RAG input mode from the run snapshot, flattened to a scalar. Declared here
    # because the response_model filters to the declared fields — without this the
    # service's value is silently dropped and the run detail page shows nothing.
    # None when the snapshot predates the field.
    input_mode: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None


class RunLevelDiagnoses(BaseModel):
    status: str  # available | not_available
    items: list[dict[str, Any]] = Field(default_factory=list)
    # Post-MVP Phase 4 (additive): run-level aggregation of PERSISTED
    # sample-level diagnoses. items are RunFailureBucket dicts.
    total_diagnoses: int = 0
    total_failure_records: int = 0
    undetermined_count: int = 0


class QualityDimension(BaseModel):
    """Phase 9: per-dimension rollup over persisted metrics/diagnoses.
    Presentation-only; no business recomputation."""

    dimension: str
    metrics: list[str] = Field(default_factory=list)
    score: float | None = None
    metric_count: int = 0
    failure_count: int = 0
    undetermined_count: int = 0
    diagnosed_count: int = 0
    diagnosis_coverage: float | None = None
    evidence_count: int = 0
    evidence_coverage: float | None = None
    evaluated_rows: int = 0
    total_rows: int = 0
    is_weakest: bool = False


class ReportResponse(BaseModel):
    summary: ReportSummary
    metrics: list[MetricReportItem]
    raw_metrics: list[RawMetricItem]
    failures: list[FailureItem]
    undetermined: list[UndeterminedItem]
    execution_errors: list[ExecutionErrorItem]
    run_level_diagnoses: RunLevelDiagnoses
    quality_dimensions: list[QualityDimension] = Field(default_factory=list)
    reproducibility: dict[str, Any]
    generated_at: datetime


# ---------------- T-16B: Comparison (read-only) ----------------

ComparabilityStatus = Literal["DIRECT", "LIMITED", "BLOCKED"]


class Comparability(BaseModel):
    """§三: {status: DIRECT|LIMITED|BLOCKED, reasons: [...]}. reasons is
    non-empty for LIMITED/BLOCKED and serves as the warning."""

    status: ComparabilityStatus
    reasons: list[str] = Field(default_factory=list)


class MetricComparisonItem(BaseModel):
    """§四: per-metric delta. A null score is NEVER coerced to 0 —
    comparable=false + incomparable_reason explains why instead."""

    name: str
    category: str | None = None
    baseline_score: float | None = None
    candidate_score: float | None = None
    delta: float | None = None  # candidate - baseline (only when both sides valid)
    relative_delta: float | None = None  # delta / baseline; None when baseline is 0/None
    baseline_status: str  # completed|error|undetermined|not_configured|not_run|missing
    candidate_status: str
    metric_version: str | None = None
    comparable: bool = False
    incomparable_reason: str | None = None
    #   undetermined|not_configured|execution_error|not_run  (a side has no valid score)
    #   not_in_baseline|not_in_candidate                    (metric absent in one run)


class OverallComparison(BaseModel):
    """§五: overall delta only when DIRECT + both overall_score valid + same
    enabled metrics + same weights. overall_score is read from the persisted
    run row — never recomputed."""

    baseline_overall: float | None = None
    candidate_overall: float | None = None
    delta: float | None = None
    comparable: bool = False
    reason: str | None = None


class ComparisonResponse(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    comparability: Comparability
    metrics: list[MetricComparisonItem]
    overall: OverallComparison
    generated_at: datetime


# ---------------- T-16C: Regression (read-only analysis) ----------------

MetricDirection = Literal["higher_is_better", "lower_is_better"]
RegressionVerdict = Literal["REGRESSION", "IMPROVEMENT", "STABLE", "NOT_COMPARABLE", "UNDETERMINED"]
OverallVerdict = Literal["IMPROVEMENT", "REGRESSION", "STABLE", "MIXED", "NOT_COMPARABLE"]


class MetricRegressionItem(BaseModel):
    """§五: per-metric regression verdict. score=null NEVER becomes REGRESSION —
    it is NOT_COMPARABLE / UNDETERMINED instead."""

    metric: str
    direction: MetricDirection
    baseline_score: float | None = None
    candidate_score: float | None = None
    delta: float | None = None
    relative_delta: float | None = None
    epsilon: float
    epsilon_source: str  # config | provisional_default
    verdict: RegressionVerdict


class CategoryRegression(BaseModel):
    """§七: per-category verdict counts. Derived here, never in the UI."""

    improvement_count: int = 0
    regression_count: int = 0
    stable_count: int = 0
    comparable_count: int = 0
    verdict: OverallVerdict = "NOT_COMPARABLE"


class OverallRegression(BaseModel):
    """§八: overall verdict from metric-level directions, NOT from the overall
    score delta (which is only auxiliary)."""

    verdict: OverallVerdict = "NOT_COMPARABLE"
    reason: str | None = None
    overall_score_delta: float | None = None


class RegressionResponse(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    comparability_status: str  # DIRECT|LIMITED|BLOCKED
    metrics: list[MetricRegressionItem]
    categories: dict[str, CategoryRegression]
    trade_off: bool = False
    overall: OverallRegression
    generated_at: datetime


# ---------------- T-17: Quality Gate (read-only evaluation) ----------------

QualityGateStatus = Literal["PASS", "FAIL", "NOT_EVALUABLE"]


class QualityGateMetric(BaseModel):
    """Per-required-metric gate result. score=null is NEVER a quality FAIL —
    it becomes status NOT_EVALUABLE with a reason instead (§五)."""

    metric: str
    score: float | None = None
    threshold: float | None = None
    passed: bool | None = None
    status: QualityGateStatus
    reason: str | None = None


class QualityGateResponse(BaseModel):
    run_id: str
    status: QualityGateStatus
    reasons: list[str] = Field(default_factory=list)
    metrics: list[QualityGateMetric] = Field(default_factory=list)
    overall_score: float | None = None  # display auxiliary only — never the verdict (§十)
    evaluated_at: datetime


# ---------------- T-18: Catalog (profiles / metrics) + Config save ----------------

class ProfileMetricOut(BaseModel):
    """Per-metric config from the merged Profile. threshold: null = no PASS/FAIL."""

    name: str
    enabled: bool
    threshold: float | None = None
    weight: float = 1.0


class RagInputConfig(BaseModel):
    """RAG input wiring surfaced to the wizard (safe fields only — no secrets).
    Phase D: explicit mode; legacy profiles infer it server-side."""

    mode: str | None = None
    url: str | None = None
    timeout: int | None = None
    retry: int | None = None


class ProfileOut(BaseModel):
    name: str
    version: str
    domain: str = "general"
    metrics: list[ProfileMetricOut]
    severity_mapping: dict[str, str]
    quality_gate: dict[str, Any] | None = None
    rag_input: RagInputConfig | None = None
    # Phase D: Review-page display inputs — judge carries PUBLIC fields only
    # (api_key lives in env, never in catalog responses).
    judge: dict[str, Any] | None = None
    pipeline: dict[str, Any] | None = None


class ProfileListResponse(BaseModel):
    items: list[ProfileOut]
    total: int


class MetricOut(BaseModel):
    """Code-registered metric metadata (MetricRegistry -> MetricSpec)."""

    name: str
    category: str
    engine: str
    version: str
    description: str
    input_requirements: list[str]
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"
    default_severity: str | None = None


class MetricListResponse(BaseModel):
    items: list[MetricOut]
    total: int


class SaveConfigRequest(BaseModel):
    name: str
    domain: str = "general"
    profile: str
    pipeline_config: dict[str, Any] | None = None


class SaveConfigResponse(BaseModel):
    config_id: str
    config_version: str
    name: str
    domain: str
    profile: str
    created: bool


# -------- Configuration Lifecycle v1 (Phase B): import / preview / export --------

class ConfigImportRequest(BaseModel):
    """D3-A: project-scoped import — project_id is REQUIRED (invariant 8).
    Same body serves both /configs/import/preview (no persistence) and
    /configs/import (create new immutable version)."""

    project_id: str
    domain: str = "general"
    yaml: str


class ConfigDiffItem(BaseModel):
    path: str
    change: str  # added | removed | changed
    old: Any = None
    new: Any = None


class ConfigDiff(BaseModel):
    base: str | None = None  # base version id, "yaml" (file profile) or None
    items: list[ConfigDiffItem] = Field(default_factory=list)


class ImportMetricItem(BaseModel):
    name: str
    enabled: bool
    threshold: float | None
    weight: float
    direction: str


class ConfigImportPreviewResponse(BaseModel):
    """§6 Preview payload — profile identity, metrics, severity, gate,
    pipeline, judge (public fields only), rag_input, structured diff."""

    project_id: str
    profile: str
    version: str | None
    domain: str
    config_version: str
    metrics: list[ImportMetricItem]
    severity_mapping: dict[str, str]
    quality_gate: Any = None
    pipeline: Any = None
    judge: dict[str, Any]
    rag_input: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    diff: ConfigDiff


class ConfigImportCommitResponse(ConfigImportPreviewResponse):
    config_id: str
    created: bool


class ConfigExportYamlResponse(BaseModel):
    config_id: str
    profile: str
    version: str | None
    domain: str
    config_version: str
    yaml: str


class ConfigDetailResponse(BaseModel):
    """§11 Profile Detail: stored/imported rows resolve from the immutable
    persisted body; legacy pointer rows from the deployment YAML file."""

    config_id: str
    name: str
    source: str  # imported | yaml_pointer
    created_at: str | None = None
    profile: str
    version: str | None
    domain: str
    config_version: str
    metrics: list[ImportMetricItem]
    severity_mapping: dict[str, str]
    quality_gate: Any = None
    pipeline: Any = None
    judge: dict[str, Any]
    rag_input: dict[str, Any]
    yaml: str | None = None


class ProjectConfigItem(BaseModel):
    """evaluation_configs row view for the Profiles workspace (§10)."""

    config_id: str
    name: str
    domain: str
    profile: str
    version: str | None = None
    source: str  # imported | yaml_pointer
    config_version: str
    created_at: datetime


class ProjectConfigListResponse(BaseModel):
    items: list[ProjectConfigItem]
    total: int


# ---------------- Phase 1B G5: Project workspace summary ----------------

class ProjectSummaryRun(BaseModel):
    """Latest run of the project (FK-joined dataset identity)."""

    run_id: str
    status: RunStatusOut
    dataset_id: str
    dataset_name: str | None = None
    dataset_version: str | None = None
    overall_score: float | None = None
    total_records: int
    evaluated_records: int
    error_records: int
    evaluation_coverage: float | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ProjectSummaryOut(BaseModel):
    """GET /api/projects/{id} — FR-02 workspace aggregation. The quality gate
    is the SAME evaluate() output as GET /evaluations/{run}/quality-gate
    (latest_quality_gate=None when the latest run has no meaningful gate)."""

    project: ProjectOut
    dataset_count: int
    run_count: int
    latest_run: ProjectSummaryRun | None = None
    latest_quality_gate: QualityGateResponse | None = None
