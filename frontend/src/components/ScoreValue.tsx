/**
 * Score cell (Phase 2, decision §十一): score=null renders "No valid metric
 * score" — NEVER 0. `data-testid="no-score"` / `data-score` are preserved as a
 * test and debug contract (they carry no styling semantics).
 */

import { NO_SCORE_LABEL } from '../utils/format';
import styles from './ScoreValue.module.css';

export function ScoreValue({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return (
      <span data-testid="no-score" className={styles.null}>
        {NO_SCORE_LABEL}
      </span>
    );
  }
  const text = score.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') || '0';
  return <span data-score={score}>{text}</span>;
}
