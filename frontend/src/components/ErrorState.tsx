/**
 * Error state (§十九): 用户界面只显示人话（资源不存在 / RAG 服务请求超时…），
 * 原始 code / trace_id 收进「技术详情」折叠区，供工程排查，不主导视觉。
 * Keeps `role="alert"` so the test contract (friendly message, no raw code at
 * primary level) holds.
 */

import { humanizeError, ApiError } from '../api/client';
import { Icon } from './Icon';
import styles from './ErrorState.module.css';

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className={styles.alert}>
      <Icon name="alert" size={16} />
      <div className={styles.body}>
        <strong>加载失败</strong>
        <p className={styles.message}>{humanizeError(error)}</p>
        {onRetry ? (
          <button type="button" className={styles.retry} onClick={onRetry}>
            重试
          </button>
        ) : null}
        {api ? (
          <details className={styles.tech}>
            <summary>技术详情</summary>
            <div className={styles.techBody}>
              <div><span>code</span><code>{api.code}</code></div>
              <div><span>trace_id</span><code>{api.traceId || '—'}</code></div>
              {api.detail ? <div><span>detail</span><code>{api.detail}</code></div> : null}
            </div>
          </details>
        ) : null}
      </div>
    </div>
  );
}
