/**
 * Evidence presentation (Phase 2, decision §九/§十): renders the evidence items
 * backing a diagnosis with a clear visual hierarchy (type chip + collapsible
 * content). Long content collapses; the type/contract is always visible.
 */

import { useState } from 'react';
import type { EvidenceItem } from './types';
import { BilingualLabel } from '../primitives/Bilingual';
import styles from './Analysis.module.css';

export function EvidencePanel({
  contract,
  items,
  empty = '暂无证据记录',
}: {
  contract?: string | null;
  items: EvidenceItem[];
  empty?: string;
}) {
  return (
    <div className={styles.evidence}>
      {contract ? (
        <div className={styles.contract} data-testid="evidence-contract">
          <BilingualLabel zh="证据契约" en="Evidence Contract" className={styles.contractLabel} />
          <code>{contract}</code>
        </div>
      ) : null}
      {items.length === 0 ? (
        <div className={styles.evidenceEmpty}>{empty}</div>
      ) : (
        <div className={styles.evidenceList}>
          {items.map((item, i) => (
            <EvidenceRow key={`${item.type}-${i}`} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const [open, setOpen] = useState(false);
  const text = stringify(item.content);
  const long = text.length > 160;

  return (
    <div className={styles.evidenceRow}>
      <div className={styles.evidenceHeader}>
        <span className={styles.evidenceType} data-testid={`evidence-${item.type}`}>
          {item.type}
        </span>
        <span className={styles.evidenceSource}>{item.source}</span>
        {item.locator ? <code className={styles.evidenceLocator}>{item.locator}</code> : null}
        {long ? (
          <button
            type="button"
            className={styles.toggle}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? '收起 / Collapse' : '展开 / Expand'}
          </button>
        ) : null}
      </div>
      {(long ? open : true) ? (
        <pre className={styles.evidenceContent}>{text || '—'}</pre>
      ) : (
        <pre className={[styles.evidenceContent, styles.truncated].join(' ')}>{text.slice(0, 160)}…</pre>
      )}
    </div>
  );
}

function stringify(content: unknown): string {
  if (content === null || content === undefined) return '';
  if (typeof content === 'string') return content;
  try {
    return typeof content === 'object' ? JSON.stringify(content, null, 2) : String(content);
  } catch {
    return String(content);
  }
}
