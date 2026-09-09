/**
 * EvaluationListPage — 评估运行 Runs 工作区。项目作用域；状态筛选 + 分页 +
 * 质量门禁列。数据全部来自后端（evaluations / quality-gate），前端不重算。
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { useProject } from '../context/ProjectContext';
import { useEvaluations } from '../hooks/useEvaluations';
import type { RunSort } from '../hooks/useEvaluations';
import type { RunStatus } from '../api/types';
import { PageHeader, Panel } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ScoreValue } from '../components/ScoreValue';
import { RunStatusBadge, QualityGateBadge } from '../status/StatusBadge';
import { formatPercent, formatTimestamp } from '../utils/format';
import styles from './EvaluationListPage.module.css';

const STATUS_OPTIONS: Array<{ value: RunStatus | ''; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '等待中' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '已完成' },
  { value: 'completed_with_errors', label: '完成但有错误' },
  { value: 'failed', label: '执行失败' },
  { value: 'cancelled', label: '已取消' },
];

export function EvaluationListPage() {
  const { activeProject, loading: projectLoading, error: projectError } = useProject();
  const list = useEvaluations(activeProject?.id ?? null);
  const [sort, setSort] = useState<RunSort>('created_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');

  const rows = useMemo(() => {
    const sorted = [...list.rows];
    if (sort === 'overall_score') {
      sorted.sort((a, b) => {
        const av = a.overall_score ?? -1;
        const bv = b.overall_score ?? -1;
        return order === 'desc' ? bv - av : av - bv;
      });
    } else {
      sorted.sort((a, b) =>
        order === 'desc'
          ? new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
          : new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
    }
    return sorted;
  }, [list.rows, sort, order]);

  if (projectLoading) return <LoadingState label="正在加载项目…" />;
  if (projectError) return <ErrorState error={projectError} />;
  if (!activeProject) {
    return <EmptyState title="请选择项目" description="从工作区选择器中选择项目以查看其评估。" icon="circle" />;
  }

  const totalPages = Math.max(1, Math.ceil(list.total / list.pageSize));

  return (
    <>
      <PageHeader
        breadcrumb={<><span>评估</span><span>/</span><span>运行</span></>}
        title="评估运行"
        subtitle={`${activeProject.name} · 共 ${list.total} 次运行`}
        actions={
          <Link className={styles.linkBtn} to="/evaluations/new">
            新建评估
          </Link>
        }
      />

      {list.loading && list.rows.length === 0 ? (
        <LoadingState label="正在加载评估运行…" />
      ) : list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : list.rows.length === 0 ? (
        <Panel title="评估运行">
          <EmptyState
            title="暂无评估运行"
            description="该项目尚无评估。发起一次评估以开始分析。"
            icon="circle"
            action={
              <Link className={styles.linkBtn} to="/evaluations/new">
                新建评估
              </Link>
            }
          />
        </Panel>
      ) : (
        <Panel title="评估运行">
          <div className={styles.toolbar}>
            <label className={styles.filterLabel}>状态
              <select
                className={styles.select}
                value={list.status}
                onChange={(e) => list.setStatus(e.target.value as RunStatus | '')}
              >
                {STATUS_OPTIONS.map((o) => (
                  <option key={o.value || 'all'} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <label className={styles.filterLabel}>排序
              <select className={styles.select} value={sort} onChange={(e) => setSort(e.target.value as RunSort)}>
                <option value="created_at">创建时间</option>
                <option value="overall_score">总体质量</option>
              </select>
            </label>
            <button type="button" className={styles.orderBtn} onClick={() => setOrder(order === 'desc' ? 'asc' : 'desc')}>
              {order === 'desc' ? '降序 ↓' : '升序 ↑'}
            </button>
          </div>

          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th align="left">运行</th>
                  <th align="left">数据集</th>
                  <th align="left">配置</th>
                  <th align="left">状态</th>
                  <th align="left">总体</th>
                  <th align="left">覆盖率</th>
                  <th align="left">错误</th>
                  <th align="left">质量门禁</th>
                  <th align="left">创建时间</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.run_id} data-testid="run-row">
                    <td>
                      <Link className={styles.runLink} to={`/evaluations/${r.run_id}`}>
                        {r.run_id.slice(0, 8)}…
                      </Link>
                    </td>
                    <td>
                      <span className={styles.datasetName}>{r.dataset_name}</span>
                      <span className="mono">{r.dataset_version}</span>
                    </td>
                    <td className="mono">{r.config_id.slice(0, 8)}…</td>
                    <td><RunStatusBadge status={r.status} /></td>
                    <td><ScoreValue score={r.overall_score} /></td>
                    <td>{formatPercent(r.evaluation_coverage)}</td>
                    <td className={styles.num}>{r.error_records}</td>
                    <td>
                      {r.gateStatus ? (
                        <QualityGateBadge status={r.gateStatus} />
                      ) : r.status === 'pending' || r.status === 'running' ? (
                        <span className={styles.gateLoading}>…</span>
                      ) : (
                        <span className={styles.gateLoading} title="门禁状态获取失败或不可用">—</span>
                      )}
                    </td>
                    <td>{formatTimestamp(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={styles.pager}>
            <button type="button" disabled={list.page <= 1} onClick={() => list.setPage(list.page - 1)}>上一页</button>
            <span>第 {list.page} / {totalPages} 页 · 共 {list.total} 次</span>
            <button type="button" disabled={list.page >= totalPages} onClick={() => list.setPage(list.page + 1)}>下一页</button>
          </div>
        </Panel>
      )}
    </>
  );
}

