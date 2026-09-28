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

/** 质量门禁原因码 → 中文说明。
 *
 *  住在这一层（而非某个页面里）是因为**两个页面都要用**：质量门禁页展示它，
 *  仪表盘也展示门禁状态 —— 而仪表盘此前直接渲染原因码本身，于是入口页面上出现
 *  一个黄色的 `QUALITY_GATE_NOT_CONFIGURED`。 */
export const REASON_EXPLAIN: Record<string, string> = {
  QUALITY_THRESHOLD_FAILED: '必需指标有有效分数，但低于配置阈值。',
  REQUIRED_METRIC_NOT_EVALUABLE: '必需指标无法评估（分数为空：未配置 / 执行错误 / 无法判定 / 未执行）。',
  QUALITY_GATE_DISABLED: '当前配置中已关闭质量门禁，门禁未启用。',
  QUALITY_GATE_NOT_CONFIGURED: '当前配置未设置质量门禁，门禁不可用。',
  QUALITY_GATE_CONFIG_INVALID: '门禁配置无效（必需指标未启用或未配置阈值）。',
};

/** DIRECTION_LABEL for call sites whose `direction` is an untyped string (some
 *  config/import views type it as plain `string`). An unrecognised token falls
 *  back to itself rather than rendering `undefined`, so a new backend enum shows
 *  up visibly instead of blanking the cell. */
export function directionLabel(direction: string | null | undefined): string {
  if (!direction) return '—';
  return (DIRECTION_LABEL as Record<string, string>)[direction] ?? direction;
}

/**
 * Reproducibility view: shows dataset/config/metric/judge fingerprint only.
 * Defence in depth (§六) — anything that looks like a secret is dropped even
 * if the backend ever adds such a key.
 */
const SECRET_KEY_PATTERN = /(key|secret|token|password|credential|authorization)/i;

/** 运行快照的键 → 中文标签。未收录的键原样显示 —— 后端加字段时不该渲染成空白。 */
const REPRO_KEY_LABEL: Record<string, string> = {
  profile: '评估配置',
  profile_version: '配置版本',
  config_version: '配置指纹',
  dataset_version: '数据集版本',
  metric_version: '指标版本',
  model_version: '模型版本',
  prompt_version: '提示词版本',
  judge_provider: 'Judge 服务商',
  judge_model: 'Judge 模型',
  judge_model_version: 'Judge 模型版本',
  judge_temperature: 'Judge 采样温度',
  judge_max_tokens: 'Judge 最大生成长度',
  judge_timeout: 'Judge 超时（秒）',
  judge_retry: 'Judge 重试次数',
  timestamp: '运行时间',
};

export function safeReproducibility(meta: Record<string, unknown> | null | undefined) {
  const entries = Object.entries(meta ?? {}).filter(
    ([key, value]) => !SECRET_KEY_PATTERN.test(key) && typeof value !== 'object',
  );
  return entries.map(([key, value]) => ({
    key,
    label: REPRO_KEY_LABEL[key] ?? key,
    value: String(value ?? ''),
  }));
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
