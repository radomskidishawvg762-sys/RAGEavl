/**
 * Surface primitives (Phase 2, decision §九): Panel / Card / Section /
 * PageHeader / Tag. A Panel is bordered + elevated; a Card is a compact
 * stat-value tile (used on Dashboard/workspace); a Section is a titled content
 * block.
 */

import type { ReactNode } from 'react';
import styles from './Surfaces.module.css';
import { BilingualLabel } from './Bilingual';

type Accent = 'neutral' | 'info' | 'warning' | 'error' | 'pass';

const ACCENT_BORDER: Record<Accent, string> = {
  neutral: 'var(--color-border)',
  info: 'var(--color-info)',
  warning: 'var(--color-warning)',
  error: 'var(--color-error)',
  pass: 'var(--color-pass)',
};

export function Panel({
  children,
  title,
  accent = 'neutral',
  actions,
  padded = true,
  className,
}: {
  children: ReactNode;
  title?: ReactNode;
  accent?: Accent;
  actions?: ReactNode;
  padded?: boolean;
  className?: string;
}) {
  return (
    <section
      className={[styles.panel, padded ? styles.padded : '', className].join(' ')}
      style={{ borderTopColor: `${ACCENT_BORDER[accent]}55` }}
    >
      {title !== undefined ? (
        <header className={styles.header}>
          <div className={styles.title}>{title}</div>
          {actions ? <div className={styles.actions}>{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function Card({
  label,
  value,
  hint,
  accent = 'neutral',
  children,
}: {
  label: string;
  value?: ReactNode;
  hint?: string;
  accent?: Accent;
  children?: ReactNode;
}) {
  return (
    <div className={styles.card} style={{ borderTopColor: `${ACCENT_BORDER[accent]}55` }}>
      <div className={styles.cardLabel}>{label}</div>
      <div className={styles.cardValue}>{children ?? value ?? '—'}</div>
      {hint ? <div className={styles.cardHint}>{hint}</div> : null}
    </div>
  );
}

export function Section({
  title,
  children,
  actions,
}: {
  title: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>{title}</h2>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function BiTitle({ zh, en }: { zh: ReactNode; en: ReactNode }) {
  return <BilingualLabel zh={zh} en={en} />;
}

export function PageHeader({
  title,
  subtitle,
  breadcrumb,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  breadcrumb?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.pageHeader}>
      <div className={styles.pageMeta}>
        {breadcrumb ? <div className={styles.breadcrumb}>{breadcrumb}</div> : null}
        <h1 className={styles.pageTitle}>{title}</h1>
        {subtitle ? <p className={styles.pageSubtitle}>{subtitle}</p> : null}
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  );
}

export function Tag({ children, tone = 'neutral' }: { children: ReactNode; tone?: Accent }) {
  return (
    <span className={styles.tag} style={{ color: ACCENT_BORDER[tone] === 'var(--color-border)' ? 'var(--color-text-muted)' : ACCENT_BORDER[tone], borderColor: ACCENT_BORDER[tone] }}>
      {children}
    </span>
  );
}
