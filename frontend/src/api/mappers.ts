/**
 * snake_case (API) -> camelCase (frontend) conversion layer (§九).
 * The ONLY place where field names change — components consume camelCase
 * view models and never re-implement backend semantics.
 */

import type {
  ConfigDetailResponse,
  ConfigImportCommitResponse,
  ConfigImportPreviewResponse,
  ConfigSummaryView,
  ConfigDetailView,
  ConfigImportPreviewView,
  MetricCatalogView,
  MetricOut,
  MetricReportItem,
  MetricView,
  ProfileMetricOut,
  ProfileMetricView,
  ProfileOut,
  ProfileView,
  ProjectConfigItem,
  ProjectSummaryResponse,
  ProjectSummaryRunView,
  ProjectSummaryView,
  RagInputConfig,
  ResultDetailOut,
  ResultDetailView,
  ResultMetricItem,
  ResultMetricView,
  RunOut,
  RunView,
} from './types';

export function toRunView(run: RunOut): RunView {
  return {
    runId: run.run_id,
    projectId: run.project_id,
    datasetId: run.dataset_id,
    configId: run.config_id,
    status: run.status,
    totalRecords: run.total_records,
    evaluatedRecords: run.evaluated_records,
    errorRecords: run.error_records,
    evaluationCoverage: run.evaluation_coverage,
    overallScore: run.overall_score,
    createdAt: run.created_at,
    startedAt: run.started_at,
    finishedAt: run.finished_at,
  };
}

export function toMetricView(metric: MetricReportItem): MetricView {
  return {
    name: metric.name,
    category: metric.category,
    score: metric.score,
    threshold: metric.threshold,
    passed: metric.passed,
    status: metric.status,
    validCount: metric.valid_count,
    invalidCount: metric.invalid_count,
    errorCount: metric.error_count,
    metricVersion: metric.metric_version,
    weight: metric.weight,
  };
}

/* T-18 catalog views — the wizard consumes these camelCase shapes only. The
 * frontend never re-derives threshold/weight/direction/enabled semantics; it
 * renders exactly what the backend Profile/MetricCatalog sent. */

export function toProfileView(p: ProfileOut): ProfileView {
  return {
    name: p.name,
    version: p.version,
    domain: p.domain,
    metrics: p.metrics.map(toProfileMetricView),
    severityMapping: p.severity_mapping,
    qualityGate: p.quality_gate,
    ragInput: p.rag_input ? toRagInputView(p.rag_input) : null,
    judge: p.judge ?? null,
    pipeline: p.pipeline ?? null,
  };
}

export function toProfileMetricView(m: ProfileMetricOut): ProfileMetricView {
  return {
    name: m.name,
    enabled: m.enabled,
    threshold: m.threshold,
    weight: m.weight,
  };
}

export function toRagInputView(r: RagInputConfig): RagInputConfig {
  return {
    mode: r.mode,
    url: r.url,
    timeout: r.timeout,
    retry: r.retry,
  };
}

export function toMetricCatalogView(m: MetricOut): MetricCatalogView {
  return {
    name: m.name,
    category: m.category,
    engine: m.engine,
    version: m.version,
    description: m.description,
    inputRequirements: m.input_requirements,
    direction: m.direction,
    defaultSeverity: m.default_severity,
  };
}

/* Phase 1A G3 — result detail (full raw I/O). The frontend renders these
 * values verbatim; comparison_basis / diagnoses are never re-interpreted. */

export function toResultMetricView(m: ResultMetricItem): ResultMetricView {
  return {
    name: m.name,
    category: m.category,
    score: m.score,
    threshold: m.threshold,
    passed: m.passed,
    status: m.status,
    metricVersion: m.metric_version,
    comparisonBasis: m.comparison_basis,
    error: m.error,
  };
}

export function toResultDetailView(d: ResultDetailOut): ResultDetailView {
  return {
    id: d.id,
    runId: d.run_id,
    recordId: d.record_id,
    rowIndex: d.row_index,
    question: d.question,
    answer: d.answer,
    contexts: d.contexts,
    referenceAnswer: d.reference_answer,
    referenceContexts: d.reference_contexts,
    isFailure: d.is_failure,
    createdAt: d.created_at,
    metricResults: d.metric_results.map(toResultMetricView),
    diagnoses: d.diagnoses,
  };
}

/* Phase 1B G5 — project workspace summary. */

export function toProjectSummaryView(s: ProjectSummaryResponse): ProjectSummaryView {
  const latest = s.latest_run;
  const latestRun: ProjectSummaryRunView | null = latest
    ? {
        runId: latest.run_id,
        status: latest.status,
        datasetId: latest.dataset_id,
        datasetName: latest.dataset_name,
        datasetVersion: latest.dataset_version,
        overallScore: latest.overall_score,
        totalRecords: latest.total_records,
        evaluatedRecords: latest.evaluated_records,
        errorRecords: latest.error_records,
        evaluationCoverage: latest.evaluation_coverage,
        createdAt: latest.created_at,
        finishedAt: latest.finished_at,
      }
    : null;
  return {
    project: s.project,
    datasetCount: s.dataset_count,
    runCount: s.run_count,
    latestRun,
    latestQualityGate: s.latest_quality_gate,
  };
}

/** Chinese display label for a metric direction (translation only — the frontend
 * never re-derives direction semantics). Lives here in the api/ conversion layer
 * so wizard components stay free of direction-enum literals. */
export const DIRECTION_LABEL: Record<MetricCatalogView['direction'], string> = {
  higher_is_better: '越高越好',
  lower_is_better: '越低越好',
};

/**
 * Reproducibility view: shows dataset/config/metric/judge fingerprint only.
 * Defence in depth (§六) — anything that looks like a secret is dropped even
 * if the backend ever adds such a key.
 */
const SECRET_KEY_PATTERN = /(key|secret|token|password|credential|authorization)/i;

export function safeReproducibility(meta: Record<string, unknown> | null | undefined) {
  const entries = Object.entries(meta ?? {}).filter(
    ([key, value]) => !SECRET_KEY_PATTERN.test(key) && typeof value !== 'object',
  );
  return entries.map(([key, value]) => ({ key, value: String(value ?? '') }));
}

// -------- Configuration Lifecycle v1 (Phase B/C) --------

export function toConfigSummaryView(c: ProjectConfigItem): ConfigSummaryView {
  return {
    configId: c.config_id,
    name: c.name,
    domain: c.domain,
    profile: c.profile,
    version: c.version,
    source: c.source,
    configVersion: c.config_version,
    createdAt: c.created_at,
  };
}

export function toConfigDetailView(d: ConfigDetailResponse): ConfigDetailView {
  return {
    configId: d.config_id,
    name: d.name,
    source: d.source,
    createdAt: d.created_at,
    profile: d.profile,
    version: d.version,
    domain: d.domain,
    configVersion: d.config_version,
    metrics: d.metrics,
    severityMapping: d.severity_mapping,
    qualityGate: d.quality_gate,
    pipeline: d.pipeline,
    judge: d.judge,
    ragInput: d.rag_input,
    yaml: d.yaml,
  };
}

export function toConfigImportPreview(r: ConfigImportPreviewResponse | ConfigImportCommitResponse): ConfigImportPreviewView {
  return {
    profile: r.profile,
    version: r.version,
    domain: r.domain,
    configVersion: r.config_version,
    metrics: r.metrics,
    severityMapping: r.severity_mapping,
    qualityGate: r.quality_gate,
    pipeline: r.pipeline,
    judge: r.judge,
    ragInput: r.rag_input,
    warnings: r.warnings,
    diff: r.diff,
  };
}
