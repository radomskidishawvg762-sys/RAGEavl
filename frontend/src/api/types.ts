/**
 * API response types — mirror of the backend Pydantic schemas (T-13/T-14C).
 *
 * Naming rule (§九): the API is snake_case; the frontend works in camelCase.
 * `src/api/mappers.ts` is the ONLY conversion layer — no business model is
 * duplicated here (no metric/diagnosis logic lives in the frontend).
 */

export type RunStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled';

/** Metric execution status emitted by the Report aggregation (T-14C §三). */
export type MetricExecutionStatus =
  | 'completed'
  | 'undetermined'
  | 'not_configured'
  | 'error'
  | 'not_run';

export interface ErrorBody {
  detail: string;
  code: string;
  trace_id: string;
  context?: Record<string, unknown>;
}

/** GET /api/health — app + database status (operational, not business data). */
export interface HealthResponse {
  app: string;
  database: string;
  detail: { db_latency_ms: number } | null;
}

export interface JudgeSettings {
  provider: string;
  model: string;
  model_version: string;
  base_url: string;
  temperature: number;
  max_tokens: number;
  timeout: number;
  retry: number;
  api_key_configured: boolean;
  configured: boolean;
  source: string;
  reachable?: boolean | null;
  message?: string | null;
}

// ---------------- Evaluation list / detail ----------------

export interface RunOut {
  run_id: string;
  project_id: string;
  dataset_id: string;
  config_id: string;
  status: RunStatus;
  total_records: number;
  evaluated_records: number;
  error_records: number;
  evaluation_coverage: number | null;
  overall_score: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  reproducibility_meta: Record<string, unknown>;
  error_summary: Record<string, unknown> | null;
}

export interface RunListResponse {
  items: RunOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  reproducibility_meta: Record<string, unknown>;
}

export interface ProgressResponse {
  status: RunStatus;
  total: number;
  evaluated: number;
  errors: number;
  coverage: number | null;
  cancelled: boolean;
}

// ---------------- Results / diagnoses / recommendations ----------------

export interface ResultOut {
  id: string;
  record_id: string;
  row_index: number | null;
  question: string;
  answer: string;
  reference_answer: string | null;
  is_failure: boolean;
  created_at: string;
}

export interface ResultsPageResponse {
  items: ResultOut[];
  total: number;
  page: number;
  page_size: number;
}

/** Phase 1A G3 — single result detail (full raw I/O + comparison_basis). */
export interface ResultMetricItem {
  name: string;
  category: string | null;
  score: number | null;
  threshold: number | null;
  passed: boolean | null;
  status: MetricExecutionStatus;
  metric_version: string | null;
  comparison_basis: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
}

export interface ResultDetailOut {
  id: string;
  run_id: string;
  record_id: string;
  row_index: number | null;
  question: string;
  answer: string;
  contexts: string[];
  reference_answer: string | null;
  reference_contexts: string[] | null;
  is_failure: boolean;
  created_at: string;
  metric_results: ResultMetricItem[];
  diagnoses: DiagnosisOut[];
}

export interface DiagnosisOut {
  id: string;
  result_id: string | null;
  /** diagnosed | undetermined */
  status: string;
  failure_type: string | null;
  related_metric: string | null;
  root_cause: string | null;
  severity: string;
  evidence_contract: string;
  /** Phase 1A G2: persisted evidence items (read-side echo, never regenerated). */
  evidence: EvidenceItemOut[];
  confidence: string;
  detail: { reason?: string | null; missing_evidence?: string[] } | null;
  created_at: string;
}

/** One persisted Evidence item (ADR-07 contract shape). */
export interface EvidenceItemOut {
  type: string;
  source: string;
  locator: string | null;
  content: unknown;
  metadata: Record<string, unknown> | null;
}

export interface DiagnosesPageResponse {
  items: DiagnosisOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface RecommendationOut {
  id: string;
  diagnosis_id: string;
  action: string;
  priority: number;
  source: string;
}

export interface RecommendationsResponse {
  items: RecommendationOut[];
}

// ---------------- Report (T-14C) ----------------

export interface ReportSummary {
  run_id: string;
  status: RunStatus;
  is_final: boolean;
  overall_score: number | null;
  total_records: number;
  evaluated_records: number;
  error_records: number;
  evaluation_coverage: number | null;
  valid_metric_count: number;
  total_enabled_metric_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
}

export interface MetricReportItem {
  name: string;
  category: string | null;
  score: number | null;
  threshold: number | null;
  passed: boolean | null;
  status: MetricExecutionStatus;
  valid_count: number;
  invalid_count: number;
  error_count: number;
  metric_version: string | null;
  weight: number;
}

export interface RawMetricItem {
  record_id: string | null;
  name: string;
  category: string | null;
  score: number | null;
  threshold: number | null;
  passed: boolean | null;
  status: MetricExecutionStatus;
  metric_version: string | null;
}

export interface FailureItem {
  diagnosis_id: string;
  record_id: string | null;
  question: string | null;
  related_metric: string | null;
  failure_type: string | null;
  severity: string | null;
  root_cause: string | null;
  status: string;
  confidence: string | null;
}

export interface UndeterminedItem {
  diagnosis_id: string;
  record_id: string | null;
  related_metric: string | null;
  status: string;
  reason: string | null;
  missing_evidence: string[];
}

export interface ExecutionErrorItem {
  record_id: string | null;
  metric: string | null;
  error_code: string | null;
  message: string | null;
}

export interface RunLevelDiagnoses {
  status: 'available' | 'not_available';
  items: Array<Record<string, unknown>>;
}

export interface QualityDimension {
  dimension: string;
  metrics: string[];
  score: number | null;
  metric_count: number;
  failure_count: number;
  undetermined_count: number;
  diagnosed_count: number;
  diagnosis_coverage: number | null;
  evidence_count: number;
  evidence_coverage: number | null;
  evaluated_rows: number;
  total_rows: number;
  is_weakest: boolean;
}

export interface ReportResponse {
  summary: ReportSummary;
  metrics: MetricReportItem[];
  raw_metrics: RawMetricItem[];
  failures: FailureItem[];
  undetermined: UndeterminedItem[];
  execution_errors: ExecutionErrorItem[];
  run_level_diagnoses: RunLevelDiagnoses;
  quality_dimensions: QualityDimension[];
  reproducibility: Record<string, unknown>;
  generated_at: string;
}

// ---------------- Dataset ----------------

export interface ProjectOut {
  id: string;
  name: string;
  domain: string;
  status: string;
  created_at: string;
}

export interface ProjectListResponse {
  items: ProjectOut[];
  total: number;
  page: number;
  page_size: number;
}

/** Phase 1A G1 — POST /api/projects request. */
export interface CreateProjectRequest {
  name: string;
  domain?: string;
  status?: 'active' | 'archived';
}

/** Phase 1B G5 — GET /api/projects/{id} workspace summary. */
export interface ProjectSummaryRun {
  run_id: string;
  status: RunStatus;
  dataset_id: string;
  dataset_name: string | null;
  dataset_version: string | null;
  overall_score: number | null;
  total_records: number;
  evaluated_records: number;
  error_records: number;
  evaluation_coverage: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface ProjectSummaryResponse {
  project: ProjectOut;
  dataset_count: number;
  run_count: number;
  latest_run: ProjectSummaryRun | null;
  latest_quality_gate: QualityGateResponse | null;
}

export interface DatasetOut {
  id: string;
  project_id: string;
  name: string;
  version: string;
  record_count: number;
  validation_status: string;
  is_locked: boolean;
  created_at: string;
}

export interface DatasetListResponse {
  items: DatasetOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface DatasetValidationIssue {
  row_index: number;
  field: string;
  detail: string;
}

export interface DatasetValidationCheck {
  name: string;
  passed: boolean;
  count: number | null;
  issues: DatasetValidationIssue[];
}

export interface DatasetValidationResponse {
  valid: boolean;
  checks: DatasetValidationCheck[];
}

export interface DatasetRecordItem {
  row_index: number;
  question: string;
  reference_answer: string | null;
  reference_contexts: string[] | null;
  metadata: Record<string, unknown>;
}

export interface DatasetRecordsPage {
  items: DatasetRecordItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface DatasetImportOut extends DatasetOut {
  validation_report: DatasetValidationResponse | null;
}

// ---------------- T-17: Quality Gate (read-only) ----------------

export type QualityGateStatus = 'PASS' | 'FAIL' | 'NOT_EVALUABLE';

export interface QualityGateMetric {
  metric: string;
  score: number | null;
  threshold: number | null;
  passed: boolean | null;
  status: QualityGateStatus;
  reason: string | null;
}

export interface QualityGateResponse {
  run_id: string;
  status: QualityGateStatus;
  reasons: string[];
  metrics: QualityGateMetric[];
  overall_score: number | null;
  evaluated_at: string;
}

// ---------------- T-16C: Compare / Regression (read-only) ----------------

export type ComparabilityStatus = 'DIRECT' | 'LIMITED' | 'BLOCKED';

export interface ComparisonMetricItem {
  name: string;
  category: string | null;
  baseline_score: number | null;
  candidate_score: number | null;
  delta: number | null;
  relative_delta: number | null;
  baseline_status: string;
  candidate_status: string;
  metric_version: string | null;
  comparable: boolean;
  incomparable_reason: string | null;
}

export interface ComparisonResponse {
  baseline_run_id: string;
  candidate_run_id: string;
  comparability: { status: ComparabilityStatus; reasons: string[] };
  metrics: ComparisonMetricItem[];
  overall: {
    baseline_overall: number | null;
    candidate_overall: number | null;
    delta: number | null;
    comparable: boolean;
    reason: string | null;
  };
  generated_at: string;
}

export type RegressionVerdict =
  | 'IMPROVEMENT'
  | 'REGRESSION'
  | 'STABLE'
  | 'MIXED'
  | 'NOT_COMPARABLE'
  | 'UNDETERMINED';

export interface RegressionMetricItem {
  metric: string;
  category: string | null;
  direction: 'higher_is_better' | 'lower_is_better';
  baseline_score: number | null;
  candidate_score: number | null;
  delta: number | null;
  relative_delta: number | null;
  epsilon: number;
  epsilon_source: string;
  verdict: RegressionVerdict;
}

export interface RegressionResponse {
  baseline_run_id: string;
  candidate_run_id: string;
  comparability_status: ComparabilityStatus;
  metrics: RegressionMetricItem[];
  categories: Record<
    string,
    {
      improvement_count: number;
      regression_count: number;
      stable_count: number;
      comparable_count: number;
      verdict: RegressionVerdict;
    }
  >;
  trade_off: boolean;
  overall: {
    verdict: RegressionVerdict;
    reason: string | null;
    overall_score_delta: number | null;
  };
  generated_at: string;
}

// ---------------- T-18: Config catalog (profiles / metrics) + save ----------------

export interface ProfileMetricOut {
  name: string;
  enabled: boolean;
  /** null = no PASS/FAIL judgment (threshold: null in the Profile YAML). */
  threshold: number | null;
  weight: number;
}

export interface RagInputConfig {
  mode?: string;
  url: string | null;
  timeout: number | null;
  retry: number | null;
}

export interface ProfileOut {
  name: string;
  version: string;
  domain: string;
  metrics: ProfileMetricOut[];
  severity_mapping: Record<string, string>;
  quality_gate: Record<string, unknown> | null;
  rag_input: RagInputConfig | null;
  /** Phase D: Review inputs — judge carries PUBLIC fields only. */
  judge: JudgePublicView | null;
  pipeline: Record<string, unknown> | null;
}

export interface ProfileListResponse {
  items: ProfileOut[];
  total: number;
}

export interface MetricOut {
  name: string;
  category: string;
  engine: string;
  version: string;
  description: string;
  input_requirements: string[];
  direction: 'higher_is_better' | 'lower_is_better';
  default_severity: string | null;
}

export interface MetricListResponse {
  items: MetricOut[];
  total: number;
}

export interface SaveConfigRequest {
  name: string;
  domain?: string;
  profile: string;
  pipeline_config?: Record<string, unknown> | null;
}

export interface SaveConfigResponse {
  config_id: string;
  config_version: string;
  name: string;
  domain: string;
  profile: string;
  created: boolean;
}

// ---------------- camelCase view models (internal) ----------------

export interface MetricView {
  name: string;
  category: string | null;
  score: number | null;
  threshold: number | null;
  passed: boolean | null;
  status: MetricExecutionStatus;
  validCount: number;
  invalidCount: number;
  errorCount: number;
  metricVersion: string | null;
  weight: number;
}

export interface RunView {
  runId: string;
  projectId: string;
  datasetId: string;
  configId: string;
  status: RunStatus;
  totalRecords: number;
  evaluatedRecords: number;
  errorRecords: number;
  evaluationCoverage: number | null;
  overallScore: number | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface ProfileMetricView {
  name: string;
  enabled: boolean;
  threshold: number | null;
  weight: number;
}

export interface ProfileView {
  name: string;
  version: string;
  domain: string;
  metrics: ProfileMetricView[];
  severityMapping: Record<string, string>;
  qualityGate: Record<string, unknown> | null;
  ragInput: RagInputConfig | null;
  /** Phase D Review inputs — judge carries PUBLIC fields only. */
  judge: JudgePublicView | null;
  pipeline: Record<string, unknown> | null;
}

export interface MetricCatalogView {
  name: string;
  category: string;
  engine: string;
  version: string;
  description: string;
  inputRequirements: string[];
  direction: 'higher_is_better' | 'lower_is_better';
  defaultSeverity: string | null;
}

export interface ResultMetricView {
  name: string;
  category: string | null;
  score: number | null;
  threshold: number | null;
  passed: boolean | null;
  status: MetricExecutionStatus;
  metricVersion: string | null;
  comparisonBasis: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
}

export interface ResultDetailView {
  id: string;
  runId: string;
  recordId: string;
  rowIndex: number | null;
  question: string;
  answer: string;
  contexts: string[];
  referenceAnswer: string | null;
  referenceContexts: string[] | null;
  isFailure: boolean;
  createdAt: string;
  metricResults: ResultMetricView[];
  diagnoses: DiagnosisOut[];
}

export interface ProjectSummaryRunView {
  runId: string;
  status: RunStatus;
  datasetId: string;
  datasetName: string | null;
  datasetVersion: string | null;
  overallScore: number | null;
  totalRecords: number;
  evaluatedRecords: number;
  errorRecords: number;
  evaluationCoverage: number | null;
  createdAt: string;
  finishedAt: string | null;
}

export interface ProjectSummaryView {
  project: ProjectOut;
  datasetCount: number;
  runCount: number;
  latestRun: ProjectSummaryRunView | null;
  latestQualityGate: QualityGateResponse | null;
}

export const TERMINAL_STATUSES: RunStatus[] = [
  'completed',
  'completed_with_errors',
  'failed',
  'cancelled',
];

export const ACTIVE_STATUSES: RunStatus[] = ['pending', 'running'];

// -------- Configuration Lifecycle v1 (Phase B/C): import / version / export --------

export interface ConfigDiffItem {
  path: string;
  change: 'added' | 'removed' | 'changed';
  old: unknown;
  new: unknown;
}

export interface ConfigDiff {
  base: string | null;
  items: ConfigDiffItem[];
}

export interface ImportMetricItem {
  name: string;
  enabled: boolean;
  threshold: number | null;
  weight: number;
  direction: string;
}

/** Judge public fields only — secrets never leave the backend env (invariant 7). */
export interface JudgePublicView {
  provider: string | null;
  model: string | null;
  model_version: string | null;
  temperature: number | null;
  max_tokens: number | null;
  timeout: number | null;
  retry: number | null;
}

export interface RagInputLifecycleView {
  mode?: string;
  url: string | null;
  timeout: number | null;
  retry: number | null;
}

export interface ConfigImportPayload {
  project_id: string;
  profile: string;
  version: string | null;
  domain: string;
  config_version: string;
  metrics: ImportMetricItem[];
  severity_mapping: Record<string, string>;
  quality_gate: Record<string, unknown> | null;
  pipeline: Record<string, unknown> | null;
  judge: JudgePublicView;
  rag_input: RagInputLifecycleView;
  warnings: string[];
  diff: ConfigDiff;
}

export interface ConfigImportPreviewResponse extends ConfigImportPayload {}

export interface ConfigImportCommitResponse extends ConfigImportPayload {
  config_id: string;
  created: boolean;
}

export interface ConfigExportYamlResponse {
  config_id: string;
  profile: string;
  version: string | null;
  domain: string;
  config_version: string;
  yaml: string;
}

export interface ConfigDetailResponse extends ConfigImportPayload {
  config_id: string;
  name: string;
  source: 'imported' | 'yaml_pointer';
  created_at: string | null;
  yaml: string | null;
}

export interface ProjectConfigItem {
  config_id: string;
  name: string;
  domain: string;
  profile: string;
  version: string | null;
  source: 'imported' | 'yaml_pointer';
  config_version: string;
  created_at: string;
}

export interface ProjectConfigListResponse {
  items: ProjectConfigItem[];
  total: number;
}

/** Structured validation errors from BIZ_CONFIG_IMPORT_INVALID (422). */
export interface ConfigImportFieldError {
  path: string;
  message: string;
}

// -------- camelCase views (frontend-only) for the Configuration workspace --------

export interface ConfigSummaryView {
  configId: string;
  name: string;
  domain: string;
  profile: string;
  version: string | null;
  source: 'imported' | 'yaml_pointer';
  configVersion: string;
  createdAt: string;
}

export interface ConfigDetailView {
  configId: string;
  name: string;
  source: 'imported' | 'yaml_pointer';
  createdAt: string | null;
  profile: string;
  version: string | null;
  domain: string;
  configVersion: string;
  metrics: ImportMetricItem[];
  severityMapping: Record<string, string>;
  qualityGate: Record<string, unknown> | null;
  pipeline: Record<string, unknown> | null;
  judge: JudgePublicView;
  ragInput: RagInputLifecycleView;
  yaml: string | null;
}

export interface ConfigImportPreviewView {
  profile: string;
  version: string | null;
  domain: string;
  configVersion: string;
  metrics: ImportMetricItem[];
  severityMapping: Record<string, string>;
  qualityGate: Record<string, unknown> | null;
  pipeline: Record<string, unknown> | null;
  judge: JudgePublicView;
  ragInput: RagInputLifecycleView;
  warnings: string[];
  diff: ConfigDiff;
}
