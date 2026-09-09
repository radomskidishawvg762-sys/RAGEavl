/**
 * API client + error normalisation (§十).
 *
 * Backend errors carry {detail, code, trace_id, context}. Raw stacks or codes
 * are never surfaced: every BIZ_/EXT_/SYS_ code becomes a human-readable
 * message, and trace_id is kept for support (not displayed prominently).
 */

import type { ErrorBody } from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId: string;
  readonly detail: string;
  readonly context?: Record<string, unknown>;

  constructor(status: number, body: ErrorBody) {
    super(body.detail ?? 'request failed');
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code ?? 'SYS_INTERNAL';
    this.traceId = body.trace_id ?? '';
    this.detail = body.detail ?? '';
    this.context = body.context;
  }
}

const CODE_MESSAGES: Record<string, string> = {
  // BIZ_
  BIZ_NOT_FOUND: '未找到对应资源，可能已被删除或 ID 不正确。',
  BIZ_VALIDATION_FAILED: '数据集校验未通过，请检查导入文件。',
  BIZ_ADAPTER_PARSE_ERROR: '无法解析上传文件，请确认 JSON/JSONL 格式。',
  BIZ_DATASET_LOCKED: '该数据集已被某个评估运行锁定，请创建新版本后再评估。',
  BIZ_CONFIG_INVALID: '评估配置无效，请检查 Profile 中的指标与参数。',
  BIZ_CONFIG_IMPORT_INVALID: 'Profile YAML 未通过导入校验，请按错误列表逐项修正。',
  BIZ_RUN_STATE_TRANSITION: '当前运行状态不允许该操作。',
  BIZ_RUN_NOT_CANCELLABLE: '运行已进入终态，无法取消。',
  BIZ_METRIC_INPUT_MISSING: '样本缺少该指标所需字段，指标未参与计算。',
  BIZ_JUDGE_NOT_CONFIGURED: 'Judge 模型未配置，该指标未运行（不是 0 分）。',
  BIZ_PROJECT_NAME_EXISTS: '项目名称已存在（含已归档项目），请更换名称。',
  // EXT_
  EXT_DB_UNAVAILABLE: '数据库暂时不可用，请稍后重试。',
  EXT_JUDGE_UNAVAILABLE: 'Judge 模型服务暂时不可用，相关指标未运行。',
  EXT_RAG_INPUT_NOT_FOUND: 'RAG 系统未找到该样本。',
  EXT_RAG_ADAPTER_TIMEOUT: 'RAG 系统响应超时，该样本未评估。',
  EXT_RAG_ADAPTER_HTTP_ERROR: 'RAG 系统返回错误，该样本未评估。',
  EXT_RAG_ADAPTER_PARSE_ERROR: 'RAG 系统返回内容不符合约定格式。',
  // SYS_
  SYS_INTERNAL: '服务内部错误，请稍后重试或联系管理员。',
  SYS_RECORD_EVALUATION_ERROR: '该样本评估执行失败，其余样本继续处理。',
  SYS_METRIC_ERROR: '指标计算内部错误。',
};

const STATUS_PREFIX: Record<number, string> = {
  404: '资源不存在',
  409: '当前状态不允许该操作',
  422: '请求参数不正确',
  503: '依赖服务不可用',
};

/** Backend error code -> user-understandable text (never a raw stack). */
export function humanizeError(error: unknown): string {
  if (error instanceof ApiError) {
    const known = CODE_MESSAGES[error.code];
    const prefix = STATUS_PREFIX[error.status];
    if (known) return prefix ? `${prefix}：${known}` : known;
    if (error.code.startsWith('BIZ_')) return `请求无法处理（${error.code}）：${error.detail}`;
    if (error.code.startsWith('EXT_')) return `外部依赖异常（${error.code}）：${error.detail}`;
    if (error.code.startsWith('SYS_')) return `服务内部错误（${error.code}），请稍后重试。`;
    return error.detail || '请求失败';
  }
  if (error instanceof Error) return '网络或服务不可用，请检查后端是否启动。';
  return '未知错误。';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
      ...init,
    });
  } catch {
    throw new ApiError(0, {
      detail: 'network unreachable',
      code: 'SYS_INTERNAL',
      trace_id: '',
    });
  }
  if (!response.ok) {
    let body: ErrorBody = { detail: response.statusText, code: 'SYS_INTERNAL', trace_id: '' };
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      /* non-JSON error body keeps the fallback */
    }
    throw new ApiError(response.status, body);
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};

/** Backend pagination caps page_size at 100 and defaults to 20. */
export const MAX_PAGE_SIZE = 100;

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * Fetch every page of a paginated endpoint. The first request uses
 * page_size=100 (the backend cap); later pages are requested until `total`
 * items have been collected, so the caller gets the complete server collection.
 * This does NO business aggregation or recomputation — it only paginates and
 * concatenates the server-returned items. Any page failure (or a page that
 * returns without advancing toward `total`) throws so the caller surfaces a
 * real ErrorState rather than treating partial data as complete.
 */
export async function fetchAllPages<T>(
  endpoint: (page: number, pageSize: number) => string,
  pageSize = MAX_PAGE_SIZE,
): Promise<T[]> {
  const first = await api.get<Paginated<T>>(endpoint(1, pageSize));
  const items = [...first.items];
  let page = 1;
  while (items.length < first.total) {
    page += 1;
    const next = await api.get<Paginated<T>>(endpoint(page, pageSize));
    if (next.items.length === 0) {
      throw new ApiError(0, {
        detail: 'pagination did not reach the reported total',
        code: 'SYS_INTERNAL',
        trace_id: '',
      });
    }
    items.push(...next.items);
  }
  return items;
}

export const endpoints = {
  health: () => '/api/health',
  judgeSettings: () => '/api/settings/judge',
  judgeSettingsTest: () => '/api/settings/judge/test',
  judgePersistentConfig: () => '/api/settings/judge/persistent',
  evaluations: (page = 1, pageSize = 20, projectId?: string, status?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (projectId !== undefined) params.set('project_id', projectId);
    if (status !== undefined) params.set('status', status);
    return `/api/evaluations?${params.toString()}`;
  },
  run: (runId: string) => `/api/evaluations/${runId}`,
  progress: (runId: string) => `/api/evaluations/${runId}/progress`,
  report: (runId: string) => `/api/evaluations/${runId}/report`,
  qualityGate: (runId: string) => `/api/evaluations/${runId}/quality-gate`,
  results: (runId: string, page = 1, pageSize = 20, isFailure?: boolean) => {
    const flag = isFailure === undefined ? '' : `&is_failure=${isFailure}`;
    return `/api/evaluations/${runId}/results?page=${page}&page_size=${pageSize}${flag}`;
  },
  // Phase 1A G3: single-result detail (full I/O + comparison_basis + evidence)
  resultDetail: (runId: string, resultId: string) =>
    `/api/evaluations/${runId}/results/${resultId}`,
  diagnoses: (runId: string, page = 1, pageSize = 50) =>
    `/api/evaluations/${runId}/diagnoses?page=${page}&page_size=${pageSize}`,
  recommendations: (runId: string) => `/api/evaluations/${runId}/recommendations`,
  projects: (page = 1, pageSize = 20, status?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (status !== undefined) params.set('status', status);
    return `/api/projects?${params.toString()}`;
  },
  project: (projectId: string) => `/api/projects/${projectId}`,
  projectCreate: () => '/api/projects',
  datasets: (page = 1, pageSize = 20, projectId?: string, sort = 'created_at', order: 'asc' | 'desc' = 'desc') => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (projectId !== undefined) params.set('project_id', projectId);
    params.set('sort', sort);
    params.set('order', order);
    return `/api/datasets?${params.toString()}`;
  },
  dataset: (datasetId: string) => `/api/datasets/${datasetId}`,
  datasetValidation: (datasetId: string) => `/api/datasets/${datasetId}/validation`,
  datasetRecords: (datasetId: string, page = 1, pageSize = 20) =>
    `/api/datasets/${datasetId}/records?page=${page}&page_size=${pageSize}`,
  datasetImport: (projectId: string) => `/api/projects/${projectId}/datasets:import`,
  // T-16C/T-17 (compare / regression / quality-gate) — read-only analysis APIs
  comparisons: (baselineRunId: string, candidateRunId: string) =>
    `/api/comparisons?baseline_run_id=${baselineRunId}&candidate_run_id=${candidateRunId}`,
  regression: (baselineRunId: string, candidateRunId: string) =>
    `/api/comparisons/regression?baseline_run_id=${baselineRunId}&candidate_run_id=${candidateRunId}`,
  // T-18 config catalog + save (New Evaluation wizard)
  runCreate: () => '/api/evaluations',
  profiles: () => '/api/configs/profiles',
  profile: (name: string) => `/api/configs/profiles/${encodeURIComponent(name)}`,
  metrics: () => '/api/metrics',
  saveConfig: (projectId: string) => `/api/projects/${projectId}/configs`,
  // Configuration Lifecycle v1 (Phase B/C): import / preview / version / export
  configImportPreview: () => '/api/configs/import/preview',
  configImport: () => '/api/configs/import',
  configDetail: (configId: string) => `/api/configs/${configId}`,
  configExportYaml: (configId: string) => `/api/configs/${configId}/yaml`,
  projectConfigs: (projectId: string) => `/api/projects/${projectId}/configs`,
};
