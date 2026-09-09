/**
 * DatasetsPage — 数据集列表（§五）。项目作用域：数据跟随 activeProject。
 * 筛选：名称 / 版本 / 校验状态 / 锁定状态（当前页内展示层过滤，注明范围）；
 * 排序走后端。仅消费 GET /api/datasets?project_id=...，禁止 mock。
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useProject } from '../context/ProjectContext';
import { useDatasets } from '../hooks/useDatasets';
import { PageHeader, Panel, Tag } from '../components/primitives/Surfaces';
import { BilingualLabel } from '../components/primitives/Bilingual';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { formatTimestamp } from '../utils/format';
import styles from './DatasetsPage.module.css';

const SORT_OPTIONS = [
  { value: 'created_at', label: '创建时间' },
  { value: 'name', label: '名称' },
  { value: 'version', label: '版本' },
  { value: 'record_count', label: '记录数' },
];

export function DatasetsPage() {
  const { activeProject, loading: projectLoading, error: projectError } = useProject();
  const list = useDatasets(activeProject?.id ?? null);
  const [nameQuery, setNameQuery] = useState('');
  const [versionQuery, setVersionQuery] = useState('');
  const [validation, setValidation] = useState('');
  const [lock, setLock] = useState('');

  const filtered = useMemo(() => {
    const q = nameQuery.trim().toLowerCase();
    const v = versionQuery.trim().toLowerCase();
    return list.items.filter((d) => {
      if (q && !d.name.toLowerCase().includes(q)) return false;
      if (v && !d.version.toLowerCase().includes(v)) return false;
      if (validation && d.validation_status !== validation) return false;
      if (lock && d.is_locked !== (lock === 'locked')) return false;
      return true;
    });
  }, [list.items, nameQuery, versionQuery, validation, lock]);

  if (projectLoading) return <LoadingState label="正在加载项目…" />;
  if (projectError) return <ErrorState error={projectError} />;
  if (!activeProject) {
    return (
      <EmptyState
        title="请选择项目"
        description="从工作区选择器中选择项目以查看其数据集。"
        icon="circle"
      />
    );
  }

  const totalPages = Math.max(1, Math.ceil(list.total / list.pageSize));
  const isFiltering = Boolean(nameQuery || versionQuery || validation || lock);

  return (
    <>
      <PageHeader
        breadcrumb={<><span>项目</span><span>/</span><span className="mono">{activeProject.name}</span></>}
         title={<BilingualLabel zh="数据集" en="Datasets" />}
        subtitle={`共 ${list.total} 个数据集 · 项目 ${activeProject.name}`}
        actions={
          <Link className={styles.linkBtn} to="/datasets/import">
             <BilingualLabel zh="导入数据集" en="Import Dataset" />
          </Link>
        }
      />

      {list.loading && list.items.length === 0 ? (
        <LoadingState label="正在加载数据集…" />
      ) : list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : list.items.length === 0 ? (
         <Panel title={<BilingualLabel zh="数据集" en="Datasets" />}>
          <EmptyState
            title="暂无数据集"
            description="该项目还没有数据集。导入一个数据集以开始评估。"
            icon="circle"
            action={
              <Link className={styles.linkBtn} to="/datasets/import">
                导入数据集
              </Link>
            }
          />
        </Panel>
      ) : (
         <Panel title={<BilingualLabel zh="数据集" en="Datasets" />}>
          <div className={styles.toolbar}>
            <label className={styles.sortLabel}>名称
              <input
                className={styles.select}
                placeholder="搜索名称…"
                value={nameQuery}
                onChange={(e) => setNameQuery(e.target.value)}
                data-testid="dataset-filter-name"
              />
            </label>
            <label className={styles.sortLabel}>版本
              <input
                className={`${styles.select} ${styles.narrowInput}`}
                placeholder="v1…"
                value={versionQuery}
                onChange={(e) => setVersionQuery(e.target.value)}
                data-testid="dataset-filter-version"
              />
            </label>
            <label className={styles.sortLabel}>校验
              <select
                className={styles.select}
                value={validation}
                onChange={(e) => setValidation(e.target.value)}
                data-testid="dataset-filter-validation"
              >
                <option value="">全部</option>
                <option value="valid">有效</option>
                <option value="invalid">无效</option>
              </select>
            </label>
            <label className={styles.sortLabel}>锁定
              <select
                className={styles.select}
                value={lock}
                onChange={(e) => setLock(e.target.value)}
                data-testid="dataset-filter-lock"
              >
                <option value="">全部</option>
                <option value="locked">已锁定</option>
                <option value="unlocked">未锁定</option>
              </select>
            </label>
            <span className={styles.toolbarSpacer} />
            <label className={styles.sortLabel}>排序
              <select
                className={styles.select}
                value={list.sort}
                onChange={(e) => list.setSort(e.target.value)}
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <button type="button" className={styles.orderBtn} onClick={() => list.setOrder(list.order === 'desc' ? 'asc' : 'desc')}>
              {list.order === 'desc' ? '降序 ↓' : '升序 ↑'}
            </button>
          </div>
          {isFiltering ? (
            <p className={styles.filterNote}>筛选作用于当前页（第 {list.page} 页，服务端已按项目过滤）。</p>
          ) : null}
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th align="left">名称</th>
                  <th align="left">版本</th>
                  <th align="right">记录数</th>
                  <th align="left">校验状态</th>
                  <th align="left">锁定状态</th>
                  <th align="left">创建时间</th>
                  <th align="left">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={7} className={styles.filterNote}>当前页无匹配数据集（调整筛选条件）。</td></tr>
                ) : (
                  filtered.map((d) => (
                  <tr key={d.id} data-testid="dataset-row">
                    <td className={styles.name}>{d.name}</td>
                    <td className="mono">{d.version}</td>
                    <td align="right" className={styles.num}>{d.record_count}</td>
                    <td>
                      <Tag tone={d.validation_status === 'valid' ? 'pass' : 'error'}>
                        {d.validation_status === 'valid' ? '有效' : '无效'}
                      </Tag>
                    </td>
                    <td>
                      <Tag tone="neutral">{d.is_locked ? '已锁定' : '未锁定'}</Tag>
                    </td>
                    <td>{formatTimestamp(d.created_at)}</td>
                    <td>
                      <span className={styles.rowActions}>
                        <Link className={styles.linkBtn} to={`/datasets/${d.id}`}>查看</Link>
                        <Link className={styles.rowBtn} to={`/datasets/${d.id}?tab=records`} data-testid={`preview-${d.name}`}>预览</Link>
                        <Link className={styles.rowBtn} to={`/datasets/${d.id}?tab=validation`} data-testid={`validate-${d.name}`}>验证</Link>
                      </span>
                    </td>
                  </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className={styles.pager}>
            <button
              type="button"
              disabled={list.page <= 1}
              onClick={() => list.setPage(list.page - 1)}
            >
              上一页
            </button>
            <span>第 {list.page} / {totalPages} 页 · 共 {list.total} 条</span>
            <button
              type="button"
              disabled={list.page >= totalPages}
              onClick={() => list.setPage(list.page + 1)}
            >
              下一页
            </button>
          </div>
        </Panel>
      )}
    </>
  );
}
