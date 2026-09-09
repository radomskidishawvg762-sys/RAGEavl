/**
 * Presentation semantics (§三/§四).
 *
 * IMPORTANT: a null score is NEVER rendered as 0. It always renders as
 * "No valid metric score" — the backend distinguishes "did not run" from
 * "ran and scored low", and the UI must preserve that distinction.
 */

import type { MetricExecutionStatus, RunStatus } from '../api/types';

export type Presentation =
  | 'pass'
  | 'quality_failure'
  | 'undetermined'
  | 'not_configured'
  | 'error'
  | 'not_run'
  | 'active'
  | 'terminal';

export interface StatusPresentation {
  presentation: Presentation;
  label: string;
}

const METRIC_PRESENTATION: Record<MetricExecutionStatus, StatusPresentation> = {
  completed: { presentation: 'pass', label: '已完成' },
  undetermined: { presentation: 'undetermined', label: '无法判定' },
  not_configured: { presentation: 'not_configured', label: '未配置' },
  error: { presentation: 'error', label: '执行错误' },
  not_run: { presentation: 'not_run', label: '未执行' },
};

/**
 * §四: score colour is NOT execution state. A completed metric that failed the
 * threshold is a QUALITY FAILURE, while a null score is undetermined /
 * not_configured / error — three different things, never "0".
 */
export function presentMetric(
  status: MetricExecutionStatus,
  passed: boolean | null,
): StatusPresentation {
  if (status === 'completed') {
    return passed === false
      ? { presentation: 'quality_failure', label: '质量失败' }
      : METRIC_PRESENTATION.completed;
  }
  return METRIC_PRESENTATION[status] ?? { presentation: 'not_run', label: status };
}

const RUN_PRESENTATION: Record<RunStatus, StatusPresentation> = {
  pending: { presentation: 'active', label: '等待执行' },
  running: { presentation: 'active', label: '运行中' },
  completed: { presentation: 'pass', label: '已完成' },
  completed_with_errors: { presentation: 'quality_failure', label: '部分完成' },
  failed: { presentation: 'error', label: '执行失败' },
  cancelled: { presentation: 'terminal', label: '已取消' },
};

export function presentRun(status: RunStatus): StatusPresentation {
  return RUN_PRESENTATION[status] ?? { presentation: 'terminal', label: status };
}

export const NO_SCORE_LABEL = '暂无有效分数';

/** null/invalid scores render as NO_SCORE_LABEL, never 0. */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return NO_SCORE_LABEL;
  return score.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') || '0';
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MB`;
}
