/**
 * WorkspaceHeader — the top bar. Left: ProductSelector (active project).
 * Right: a real environment chip (from Vite mode) — never fabricated data.
 */

import { ProjectSelector } from './ProjectSelector';
import styles from './WorkspaceHeader.module.css';

export function WorkspaceHeader() {
  const env = import.meta.env.MODE ?? 'unknown';
  return (
    <header className={styles.header}>
      <ProjectSelector />
      <div className={styles.right}>
        <span className={styles.envChip} data-env={env}>
          {env === 'production' ? 'prod' : env}
        </span>
      </div>
    </header>
  );
}
