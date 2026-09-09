/**
 * RecordDetailDrawer — 记录详情右侧抽屉。展示 question / reference_answer /
 * reference_contexts / metadata。长文本默认截断，可展开；不撑爆页面、不横向滚动。
 * 纯展示，未改动任何后端语义。
 */

import { useEffect, useState } from 'react';
import type { DatasetRecordItem } from '../../api/types';
import styles from './RecordDetailDrawer.module.css';

const EXPAND_THRESHOLD = 200;

export function RecordDetailDrawer({
  record,
  onClose,
}: {
  record: DatasetRecordItem | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!record) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [record, onClose]);

  if (!record) return null;
  return (
    <div className={styles.overlay} onClick={onClose}>
      <aside className={styles.drawer} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="记录详情">
        <header className={styles.header}>
          <span className={styles.title}>记录详情</span>
          <span className={styles.rowTag}>#{record.row_index}</span>
          <button type="button" className={styles.close} onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>
        <div className={styles.body}>
          <Field label="问题 Question" data-testid="record-question">
            <ExpandableText text={record.question} mono={false} />
          </Field>
          <Field label="参考答案 Reference Answer">
            <ExpandableText text={record.reference_answer ?? '—'} mono={false} />
          </Field>
          <Field label="参考上下文 Reference Contexts">
            {record.reference_contexts && record.reference_contexts.length ? (
              <div className={styles.contextList}>
                {record.reference_contexts.map((c, i) => (
                  <ExpandableText key={i} text={c} mono />
                ))}
              </div>
            ) : (
              <span className={styles.emptyValue}>—</span>
            )}
          </Field>
          <Field label="元数据 Metadata">
            {record.metadata && Object.keys(record.metadata).length ? (
              <pre className={styles.json}>{safeJson(record.metadata)}</pre>
            ) : (
              <span className={styles.emptyValue}>—</span>
            )}
          </Field>
        </div>
      </aside>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.field}>
      <div className={styles.fieldLabel}>{label}</div>
      <div className={styles.fieldValue}>{children}</div>
    </div>
  );
}

function ExpandableText({ text, mono }: { text: string; mono: boolean }) {
  const [open, setOpen] = useState(false);
  const long = text.length > EXPAND_THRESHOLD;
  return (
    <div className={styles.textWrap}>
      <div className={[styles.text, mono ? styles.mono : ''].join(' ')}>
        {long && !open ? text.slice(0, EXPAND_THRESHOLD) + '…' : text}
      </div>
      {long ? (
        <button type="button" className={styles.toggle} onClick={() => setOpen((v) => !v)}>
          {open ? '收起' : '展开全部'}
        </button>
      ) : null}
    </div>
  );
}

function safeJson(value: Record<string, unknown>): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
