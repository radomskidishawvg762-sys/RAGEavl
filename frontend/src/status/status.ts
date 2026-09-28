/**
 * Unified status system (Phase 2, decision §十).
 *
 * Every status family in the product maps to ONE presentation model:
 *   { label, tone, icon, text }
 * where tone -> a token colour via CSS, icon -> a graphics marker, text -> the
 * explicit label. A status is NEVER conveyed by colour alone (no "green means
 * good"): text + icon are always present, and the badge is keyboard-accessible.
 *
 * This is a presentation layer ONLY. The frontend never computes a status —
 * it receives the status/verdict/severity from the backend and renders it.
 * A null score / delta / verdict is a distinct state, never coerced to 0.
 */

import type {
  MetricExecutionStatus,
  RunStatus,
} from '../api/types';

export type Tone =
  | 'pass'
  | 'warning'
  | 'error'
  | 'critical'
  | 'undetermined'
  | 'neutral'
  | 'info'
  | 'active'
  | 'muted';

export type IconName =
  | 'circle'
  | 'check'
  | 'cross'
  | 'warning'
  | 'alert'
  | 'question'
  | 'minus'
  | 'arrow-up'
  | 'arrow-down'
  | 'arrow-right'
  | 'lock'
  | 'info'
  | 'spark';

export interface StatusPresentation {
  label: string;
  labelEn?: string;
  tone: Tone;
  icon: IconName;
}

// ---------------- Run ----------------

export function presentRun(status: RunStatus): StatusPresentation {
  switch (status) {
    case 'pending':
      return { label: '等待执行', labelEn: 'Pending', tone: 'active', icon: 'circle' };
    case 'running':
      return { label: '运行中', labelEn: 'Running', tone: 'active', icon: 'circle' };
    case 'completed':
      return { label: '已完成', labelEn: 'Completed', tone: 'pass', icon: 'check' };
    case 'completed_with_errors':
      return { label: '部分完成', labelEn: 'Completed with Errors', tone: 'warning', icon: 'alert' };
    case 'failed':
      return { label: '执行失败', labelEn: 'Failed', tone: 'error', icon: 'cross' };
    case 'cancelled':
      return { label: '已取消', labelEn: 'Cancelled', tone: 'muted', icon: 'minus' };
    default:
      return { label: status, tone: 'muted', icon: 'circle' };
  }
}

// ---------------- Metric execution ----------------

/**
 * Metric status: execution state. `passed` refines a *completed* metric into a
 * quality_failure when the threshold was violated. A null score is never
 * rendered as 0 — its status is undetermined / not_configured / error / not_run.
 */
export function presentMetric(
  status: MetricExecutionStatus,
  passed: boolean | null,
): StatusPresentation {
  switch (status) {
    case 'completed':
      return passed === false
        ? { label: '质量失败', tone: 'warning', icon: 'alert' }
        : { label: '已完成', tone: 'pass', icon: 'check' };
    case 'undetermined':
      return { label: '无法判定', tone: 'undetermined', icon: 'question' };
    case 'not_configured':
      return { label: '未配置', tone: 'muted', icon: 'minus' };
    case 'error':
      return { label: '执行错误', tone: 'error', icon: 'cross' };
    case 'not_run':
      return { label: '未执行', tone: 'muted', icon: 'minus' };
    default:
      return { label: status, tone: 'muted', icon: 'circle' };
  }
}

// ---------------- Diagnosis severity ----------------

export function presentSeverity(severity: string): StatusPresentation {
  switch (severity) {
    case 'CRITICAL':
      return { label: '严重', tone: 'critical', icon: 'alert' };
    case 'ERROR':
      return { label: '错误', tone: 'error', icon: 'cross' };
    case 'WARNING':
      return { label: '警告', tone: 'warning', icon: 'warning' };
    case 'INFO':
      return { label: '信息', tone: 'info', icon: 'info' };
    default:
      return { label: severity || '—', tone: 'muted', icon: 'circle' };
  }
}

// ---------------- Comparability ----------------

export function presentComparability(status: string): StatusPresentation {
  switch (status) {
    case 'DIRECT':
      return { label: '可直接比较', tone: 'pass', icon: 'check' };
    case 'LIMITED':
      return { label: '有限比较', tone: 'warning', icon: 'alert' };
    case 'BLOCKED':
      return { label: '不可比较', tone: 'error', icon: 'lock' };
    default:
      return { label: status || '—', tone: 'muted', icon: 'circle' };
  }
}

// ---------------- Regression verdict ----------------

export function presentRegression(verdict: string): StatusPresentation {
  switch (verdict) {
    case 'IMPROVEMENT':
      return { label: '改进', tone: 'pass', icon: 'arrow-up' };
    case 'REGRESSION':
      return { label: '回归', tone: 'error', icon: 'arrow-down' };
    case 'STABLE':
      return { label: '稳定', tone: 'neutral', icon: 'minus' };
    case 'MIXED':
      return { label: '混合变化', tone: 'warning', icon: 'warning' };
    case 'NOT_COMPARABLE':
      return { label: '不可比较', tone: 'muted', icon: 'minus' };
    case 'UNDETERMINED':
      return { label: '无法判定', tone: 'undetermined', icon: 'question' };
    default:
      return { label: verdict || '—', tone: 'muted', icon: 'circle' };
  }
}

/** 该判定是否「没有比较过」。
 *
 *  为真时，改进/回归/稳定计数必然全为 0 —— 直接把 0 渲染出来会被读成「什么都没
 *  变」，而事实是「什么都没比较」，两者意思相反。
 *
 *  以函数暴露而不是让页面比较字面量：判定枚举只允许出现在本文件，页面可以渲染
 *  判定但不能派生它（见 tests/test_regression_service.py 的前端扫描不变量）。 */
export function regressionNotCompared(verdict: string): boolean {
  return verdict === 'NOT_COMPARABLE';
}

// ---------------- Quality Gate ----------------
export function presentQualityGate(status: string): StatusPresentation {
  switch (status) {
    case 'PASS':
      return { label: '通过', tone: 'pass', icon: 'check' };
    case 'FAIL':
      return { label: '未通过', tone: 'error', icon: 'cross' };
    case 'NOT_EVALUABLE':
      return { label: '无法评估', tone: 'neutral', icon: 'question' };
    default:
      return { label: status || '—', tone: 'muted', icon: 'circle' };
  }
}

// ---------------- Metric category ----------------

export function presentCategory(category: string | null): StatusPresentation {
  switch (category) {
    case 'retrieval':
      return { label: '检索', tone: 'info', icon: 'spark' };
    case 'generation':
      return { label: '生成', tone: 'undetermined', icon: 'spark' };
    case 'integrity':
      return { label: '一致性', tone: 'warning', icon: 'spark' };
    default:
      return { label: category || '—', tone: 'muted', icon: 'spark' };
  }
}
