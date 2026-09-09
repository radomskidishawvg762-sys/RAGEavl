/**
 * RecordExplorer — 记录浏览。表格：行号 / Record ID / 问题 / 状态；点击行打开
 * 详情抽屉。分页由后端支持。前端只展示，不生成状态语义。
 */

import { useState } from 'react';
import type { DatasetRecordItem, DatasetRecordsPage } from '../../api/types';
import { EmptyState } from '../primitives/Feedback';
import { RecordDetailDrawer } from './RecordDetailDrawer';
import styles from './RecordExplorer.module.css';

export function RecordExplorer({
  records,
  page,
  pageSize,
  total,
  onPageChange,
}: {
  records: DatasetRecordsPage | null;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const [selected, setSelected] = useState<DatasetRecordItem | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className={styles.explorer}>
      {!records || records.items.length === 0 ? (
        <EmptyState title="暂无记录" description="该数据集暂无样本记录。" icon="circle" />
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th align="left">行号</th>
                  <th align="left">问题 Question</th>
                  <th align="left">参考答案</th>
                  <th align="left">参考上下文</th>
                  <th align="left">操作</th>
                </tr>
              </thead>
              <tbody>
                {records.items.map((r) => (
                  <tr key={r.row_index} className={styles.row} data-testid="record-row">
                    <td className={`${styles.rowIndex} mono`}>#{r.row_index}</td>
                    <td className={styles.question}>
                      <span className={styles.questionText}>{r.question}</span>
                    </td>
                    <td>{r.reference_answer ? '有' : '—'}</td>
                    <td>{r.reference_contexts?.length ? `${r.reference_contexts.length} 条` : '—'}</td>
                    <td>
                      <button
                        type="button"
                        className={styles.openBtn}
                        onClick={() => setSelected(r)}
                      >
                        查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={styles.pager}>
            <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
              上一页
            </button>
            <span>
              第 {page} / {totalPages} 页 · 共 {total} 条
            </span>
            <button type="button" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
              下一页
            </button>
          </div>
        </>
      )}
      <RecordDetailDrawer record={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
