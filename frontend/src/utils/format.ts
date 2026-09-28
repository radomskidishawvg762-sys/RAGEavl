/**
 * Presentation semantics (§三/§四).
 *
 * IMPORTANT: a null score is NEVER rendered as 0. It always renders as
 * "No valid metric score" — the backend distinguishes "did not run" from
 * "ran and scored low", and the UI must preserve that distinction.
 */

import type { MetricExecutionStatus, RunStatus } from '../api/types';
import {
  presentMetric as presentMetricStatus,
  presentRun as presentRunStatus,
} from '../status/status';

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

/**
 * Which presentation BUCKET a metric status falls in (used for colour, never
 * for text). The label is deliberately NOT re-listed here — it is read from
 * `status/status.ts`, the single source of truth for every status family, so
 * the two copies cannot drift apart again.
 */
const METRIC_BUCKET: Record<MetricExecutionStatus, Presentation> = {
  completed: 'pass',
  undetermined: 'undetermined',
  not_configured: 'not_configured',
  error: 'error',
  not_run: 'not_run',
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
  const presentation =
    status === 'completed' && passed === false
      ? 'quality_failure'
      : METRIC_BUCKET[status] ?? 'not_run';
  return { presentation, label: presentMetricStatus(status, passed).label };
}

const RUN_BUCKET: Record<RunStatus, Presentation> = {
  pending: 'active',
  running: 'active',
  completed: 'pass',
  completed_with_errors: 'quality_failure',
  failed: 'error',
  cancelled: 'terminal',
};

export function presentRun(status: RunStatus): StatusPresentation {
  return {
    presentation: RUN_BUCKET[status] ?? 'terminal',
    label: presentRunStatus(status).label,
  };
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
