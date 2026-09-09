/**
 * Unified status badge (Phase 2, decision §十).
 *
 * One component renders every status family (run / metric / severity /
 * comparability / regression / quality-gate / category). It always shows
 * text + an icon; colour is never the only channel. `tone` maps to a token
 * colour via the CSS module.
 */

import { Icon } from '../components/Icon';
import { BilingualLabel } from '../components/primitives/Bilingual';
import type { RunStatus, MetricExecutionStatus } from '../api/types';
import {
  presentCategory,
  presentComparability,
  presentMetric,
  presentQualityGate,
  presentRegression,
  presentRun,
  presentSeverity,
} from './status';
import type { IconName, StatusPresentation, Tone } from './status';
import styles from './StatusBadge.module.css';

const TONE_VAR: Record<Tone, string> = {
  pass: 'var(--color-pass)',
  warning: 'var(--color-warning)',
  error: 'var(--color-error)',
  critical: 'var(--color-critical)',
  undetermined: 'var(--color-undetermined)',
  neutral: 'var(--color-neutral)',
  info: 'var(--color-info)',
  active: 'var(--color-active)',
  muted: 'var(--color-text-subtle)',
};

export function StatusBadge({
  presentation,
  iconSize = 12,
}: {
  presentation: StatusPresentation;
  iconSize?: number;
}) {
  const tone = presentation.tone;
  return (
    <span
      className={styles.badge}
      data-tone={tone}
      style={{
        color: TONE_VAR[tone],
        borderColor: TONE_VAR[tone],
        background: `color-mix(in srgb, ${TONE_VAR[tone]} 14%, transparent)`,
      }}
    >
      <Icon name={presentation.icon as IconName} size={iconSize} />
      <span className={styles.label}>
        {presentation.labelEn ? (
          <BilingualLabel zh={presentation.label} en={presentation.labelEn} />
        ) : presentation.label}
      </span>
    </span>
  );
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <StatusBadge presentation={presentRun(status)} />;
}

export function MetricStatusBadge({ status, passed }: { status: MetricExecutionStatus; passed: boolean | null }) {
  return <StatusBadge presentation={presentMetric(status, passed)} />;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <StatusBadge presentation={presentSeverity(severity)} />;
}

export function ComparabilityBadge({ status }: { status: string }) {
  return <StatusBadge presentation={presentComparability(status)} />;
}

export function RegressionBadge({ verdict }: { verdict: string }) {
  return <StatusBadge presentation={presentRegression(verdict)} />;
}

export function QualityGateBadge({ status }: { status: string }) {
  return <StatusBadge presentation={presentQualityGate(status)} />;
}

export function CategoryBadge({ category }: { category: string | null }) {
  return <StatusBadge presentation={presentCategory(category)} />;
}
