/**
 * Tabs primitive (UX Polish §3/§9) — engineering-tool style: compact underline
 * tabs, keyboard accessible (roving tabindex via native buttons), count badges
 * supported. Tabs control VISIBILITY of already-fetched data only — they never
 * gate data fetching or compute anything.
 */

import { useState, type ReactNode } from 'react';
import styles from './Tabs.module.css';

export interface TabItem {
  key: string;
  label: string;
  /** Optional counter shown next to the label (rendered from backend data). */
  count?: number;
}

export function Tabs({
  items,
  initial,
  onChange,
  children,
}: {
  items: TabItem[];
  initial?: string;
  /** Controlled-ish: parent may react to tab changes (e.g. sync ?tab=). */
  onChange?: (key: string) => void;
  /** Render function receives the active key. */
  children: (activeKey: string) => ReactNode;
}) {
  const [active, setActive] = useState(initial ?? items[0]?.key ?? '');

  return (
    <div className={styles.root}>
      <div className={styles.bar} role="tablist" data-testid="tabs">
        {items.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={active === t.key}
            className={[styles.tab, active === t.key ? styles.active : ''].join(' ')}
            data-testid={`tab-${t.key}`}
            onClick={() => {
              setActive(t.key);
              onChange?.(t.key);
            }}
          >
            {t.label}
            {t.count !== undefined ? <span className={styles.count}>{t.count}</span> : null}
          </button>
        ))}
      </div>
      <div className={styles.panel} role="tabpanel">
        {children(active)}
      </div>
    </div>
  );
}
