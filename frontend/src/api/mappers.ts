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
  RunStatus,
  RunView,
} from './types';
// 原因串里嵌着运行状态 / 可比性枚举 —— 走已有 presenter，不另造一套中文。
import { presentComparability, presentRun } from '../status/status';

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

// -------- 后端标记 / 句子 → 中文（纯展示，绝不改写数据） --------
//
// 这些串都来自后端，**只用于显示**：原始值仍原样留在 view model 里，翻不出来
// 的一律回退成原文，绝不猜（后端加枚举值时应显眼，而不是变成空白或错译）。

/** 证据项 `type` → 中文。后端 `EvidenceType` 的完整取值都在这里。
 *
 *  注意 `locator` **不在此列、也不翻译** —— 它是可核对的机器定位
 *  （`record.reference_contexts[0]` / `answer[34:38]`），翻成中文就不再是
 *  一个能被定位的指针了。 */
export const EVIDENCE_TYPE_LABEL: Record<string, string> = {
  reference_evidence: '参考证据',
  answer_claim: '答案声明',
  query_evidence: '查询证据',
  retrieved_evidence: '检索证据',
  metadata_evidence: '元数据证据',
  ranking_evidence: '排序证据',
  configuration_evidence: '配置证据',
  execution_evidence: '执行证据',
};

export function evidenceTypeLabel(type: string): string {
  return EVIDENCE_TYPE_LABEL[type] ?? type;
}

/** 证据项 `source` → 中文。未收录的值原样显示。 */
export const EVIDENCE_SOURCE_LABEL: Record<string, string> = {
  reference_contexts: '参考上下文',
  reference_answer: '参考答案',
  answer: '答案',
  question: '问题',
  contexts: '检索上下文',
  'metadata.rag': 'RAG 元数据',
  diagnosis_rule: '诊断规则',
};

export function evidenceSourceLabel(source: string): string {
  return EVIDENCE_SOURCE_LABEL[source] ?? source;
}

/** Judge 公共字段 → 中文标签（Settings 表单与新建评估确认页共用同一份，
 *  避免两处各写一套而漂移）。 */
export const JUDGE_FIELD_LABEL: Record<string, string> = {
  provider: '服务商 Provider',
  model: '模型 Model',
  model_version: '模型版本 Model Version',
  base_url: '接入地址 Base URL',
  temperature: '采样温度 Temperature',
  max_tokens: '最大生成长度 Max Tokens',
  timeout: '超时（秒）Timeout',
  retry: '重试次数 Retry',
};

export function judgeFieldLabel(field: string): string {
  return JUDGE_FIELD_LABEL[field] ?? field;
}

/* ---- 可比性 / 回归原因：这些是后端**句子**（不是术语），整句翻译；
 *      句子里的比较值（如 `(v5 vs v1)`）原样保留在中文句子里。 ---- */

const RUN_STATUS_LABEL = (status: string) => presentRun(status as RunStatus).label;

type ReasonRule = [RegExp, (...matched: string[]) => string];

/** 后端把指标列表塞进原因串时用的是 Python 字面量：`['a', 'b']`、`[]`。
 *  直接透传会让 `['…']` 夹在中文里像渲染 bug（且空列表显示成 `[]`），
 *  比原来的英文更难读。解析成「a、b」，空列表留空。未识别时原样返回。 */
function metricListText(raw: string): string {
  const quoted = raw.match(/'[^']*'|"[^"]*"/g);
  if (!quoted) return raw.trim() === '[]' ? '' : raw;
  return quoted.map((q) => q.slice(1, -1)).join('、');
}

const COMPARISON_REASON_RULES: ReasonRule[] = [
  [/^run (.+) not finalized \(status=(.+)\)$/, (_m, id, st) => `运行 ${id} 尚未结束（状态：${RUN_STATUS_LABEL(st)}）`],
  [/^runs target different datasets$/, () => '两次运行的目标数据集不同'],
  [/^dataset version differs \((.+)\)$/, (_m, v) => `数据集版本不同（${v}）`],
  [/^dataset version not snapshotted$/, () => '未记录数据集版本快照'],
  [/^enabled metrics not snapshotted$/, () => '未记录启用指标快照'],
  [/^no common enabled metrics$/, () => '两次运行没有共同启用的指标'],
  [/^enabled metrics differ \(baseline-only: (.*), candidate-only: (.*)\)$/, (_m, b, c) => `启用指标不同（仅基线：${metricListText(b)}；仅候选：${metricListText(c)}）`],
  [/^metric version differs \((.+)\)$/, (_m, v) => `指标版本不同（${v}）`],
  [/^metric version not snapshotted$/, () => '未记录指标版本快照'],
  [/^config version differs \(profile key semantics\)$/, () => '配置指纹不同（Profile 键语义）'],
  [/^comparability is not DIRECT$/, () => `可比性不是「${presentComparability('DIRECT').label}」`],
  [/^overall score unavailable for run (.+)$/, (_m, id) => `运行 ${id} 没有总体分数`],
  [/^enabled metrics differ$/, () => '启用指标不同'],
  [/^metric weights differ$/, () => '指标权重不同'],
  [/^no comparable metric verdicts$/, () => '没有任何可比对的指标判定'],
  [/^metrics moved in opposing directions$/, () => '指标变化方向相反'],
  // 兜底：judge 差异句作为多原因串的一段出现时，至少把前缀中文化。
  [/^judge configuration differs: (.*)$/, (_m, bits) => `Judge 配置不同：${bits}`],
];

const JUDGE_BIT_RULES: ReasonRule[] = [
  [/^provider present on one side only$/, () => '仅一侧有服务商'],
  [/^model present on one side only \((.+)\)$/, (_m, v) => `仅一侧有模型（${v}）`],
  [/^provider \((.+)\)$/, (_m, v) => `服务商（${v}）`],
  [/^model \((.+)\)$/, (_m, v) => `模型（${v}）`],
];

function applyRules(rules: ReasonRule[], segment: string): string | null {
  for (const [pattern, render] of rules) {
    const matched = pattern.exec(segment);
    if (matched) return render(...matched);
  }
  return null;
}

/** Judge 指纹差异整句（内部 bits 也翻译）。不是该形状时返回 null。 */
function judgeMismatchLabel(reason: string): string | null {
  const head = /^judge configuration differs: (.*)$/.exec(reason);
  if (!head) return null;
  const bits = head[1].split('; ').map((b) => applyRules(JUDGE_BIT_RULES, b) ?? b);
  return `Judge 配置不同：${bits.join('；')}`;
}

/** 可比性 / 总体可比性 / 回归的原因串 → 中文。
 *
 *  后端会把若干原因用 `"; "` 拼成一句；全部片段都认得才翻译并改成中文分号，
 *  只要有一段不认识就整句原样返回 —— 半句中文半句英文比原样更难读。 */
export function comparisonReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '';
  const judge = judgeMismatchLabel(reason);
  if (judge !== null) return judge;
  const parts = reason.split('; ');
  const mapped = parts.map((p) => applyRules(COMPARISON_REASON_RULES, p));
  return mapped.every((m): m is string => m !== null) ? mapped.join('；') : reason;
}

/* ---- 诊断原因 / 根因：只替换内部标记前缀与已知根因标签，句子的其余部分
 *      （后端生成的散文）保持原样 —— 不做机器翻译，也不猜。 ---- */

/** `comparison_type` 枚举 → 中文（与 taxonomy 的表述一致）。 */
const COMPARISON_TYPE_LABEL: Record<string, string> = {
  match: '一致',
  value_mismatch: '数值不一致',
  scale_mismatch: '量级不一致',
  unit_mismatch: '单位不一致',
  temporal_mismatch: '时间不一致',
  entity_mismatch: '实体不一致',
  missing_reference: '参考缺失',
  ambiguous: '无法判定',
};

/** 根因标题 → 中文（后端 `_root_cause` 由规则码 Title Case 生成）。 */
const ROOT_CAUSE_TITLE_LABEL: Record<string, string> = {
  'Numerical Mismatch': '数值不一致',
  'Temporal Mismatch': '时间不一致',
  'Entity Mismatch': '实体不一致',
  'Unit Mismatch': '单位不一致',
  'Top-K Issue': 'Top-K 不足',
  'Missing Evidence': '检索缺失证据',
  'Unsupported Claim': '无据声明',
  'Partial Answer': '部分回答',
};

/** 根因括号里的 `键=` 内部字段名 → 中文（值一律原样）。 */
const ROOT_CAUSE_FACT_LABEL: Record<string, string> = {
  abs_diff: '绝对差',
  relation: '关系',
  reference: '参考值',
  configured_top_k: '配置 Top-K',
};

const ROOT_CAUSE_FACT_PATTERN = new RegExp(
  `\\b(${Object.keys(ROOT_CAUSE_FACT_LABEL).join('|')})=`,
  'g',
);

function rootCauseLabel(text: string): string {
  for (const [title, zh] of Object.entries(ROOT_CAUSE_TITLE_LABEL)) {
    if (text === title) return zh;
    if (text.startsWith(`${title} (`) && text.endsWith(')')) {
      const facts = text
        .slice(title.length + 2, -1)
        .replace(ROOT_CAUSE_FACT_PATTERN, (_all, key: string) => `${ROOT_CAUSE_FACT_LABEL[key]}=`);
      return `${zh} (${facts})`;
    }
  }
  return text;
}

/** 诊断的根因 / 无法判定原因 → 中文呈现。
 *
 *  只做两件事：把内部标记前缀（`comparison_type=ambiguous:`、`metric error (X):`）
 *  换成中文，以及把已知的根因标签（`Numerical Mismatch`）换成中文。其余散文原样
 *  保留 —— 「不要机器翻译后端生成的自然语言」是硬规则。 */
export function diagnosisTextLabel(text: string): string {
  const comparisonType = /^comparison_type=([a-z_]+):\s*([\s\S]*)$/.exec(text);
  if (comparisonType) {
    const [, token, rest] = comparisonType;
    return `比较类型「${COMPARISON_TYPE_LABEL[token] ?? token}」：${rest}`;
  }
  const metricError = /^metric error \(([^)]+)\):\s*([\s\S]*)$/.exec(text);
  if (metricError) {
    const [, code, rest] = metricError;
    return `指标执行错误（${code}）：${rest}`;
  }
  return rootCauseLabel(text);
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
