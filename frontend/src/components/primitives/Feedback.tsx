/**
 * Feedback primitives (Phase 2): EmptyState / Spinner / LoadingState.
 * Consistent loading and empty semantics across every page.
 */

import type { ReactNode } from 'react';
import { Icon } from '../Icon';
import type { IconName } from '../../status/status';
import styles from './Feedback.module.css';
import { BilingualLabel } from './Bilingual';

export function Spinner({ size = 18 }: { size?: number }) {
  return <span className={styles.spinner} style={{ width: size, height: size }} aria-label="loading" />;
}

export function LoadingState({ label = '加载中…' }: { label?: string }) {
  return (
    <div className={styles.state} role="status">
      <Spinner />
       <span className={styles.stateText}><BilingualLabel zh={label} en="Loading" /></span>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  icon = 'circle',
  action,
}: {
  title: string;
  description?: string;
  icon?: IconName;
  action?: ReactNode;
}) {
  return (
    <div className={styles.state}>
      <div className={styles.emptyIcon}>
        <Icon name={icon} size={22} />
      </div>
      <div className={styles.stateText}>
         <strong><BilingualLabel zh={title} en="Status" /></strong>
        {description ? <p className={styles.emptyDesc}>{description}</p> : null}
      </div>
      {action ? <div className={styles.emptyAction}>{action}</div> : null}
    </div>
  );
}
