/**
 * DatasetDetailPage — 数据集详情（UX Polish §6）。
 * Header（Dataset / Version / Records / Validation / Lock 元信息）
 * → Tabs：Overview（概要）· Validation（校验报告）· Records（DataTable + 抽屉）。
 * 校验语义来自后端 validation API；记录浏览来自 records API；?tab= 支持深链
 * （列表页「预览」「验证」动作直达对应 Tab）。锁定数据集不可编辑（后端
 * immutability 保证，前端镜像展示）。
 */

import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useProject } from '../context/ProjectContext';
import { useDatasetDetail } from '../hooks/useDatasetDetail';
import { Card, PageHeader, Tag } from '../components/primitives/Surfaces';
import { Grid } from '../components/primitives/Layout';
import { Tabs } from '../components/primitives/Tabs';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ValidationReport } from '../components/dataset/ValidationReport';
import { RecordExplorer } from '../components/dataset/RecordExplorer';
import { formatTimestamp } from '../utils/format';
import styles from './DatasetDetailPage.module.css';

export function DatasetDetailPage({ datasetId: datasetIdProp }: { datasetId?: string }) {
  const params = useParams<{ datasetId: string }>();
  const datasetId = datasetIdProp ?? params.datasetId;
  const { activeProject } = useProject();
  const detail = useDatasetDetail(datasetId ?? null);
  const [searchParams, setSearchParams] = useSearchParams();

  if (detail.loading && !detail.dataset) return <LoadingState label="正在加载数据集…" />;
  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.reload} />;
  if (!detail.dataset) return <EmptyState title="未找到数据集" description="该数据集可能已被删除。" icon="circle" />;

  const ds = detail.dataset;
  const projectName = activeProject && activeProject.id === ds.project_id ? activeProject.name : ds.project_id;
  const initialTab = searchParams.get('tab') ?? 'overview';

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <span>数据集</span>
            <span>/</span>
            <span className="mono">{ds.name}</span>
          </>
        }
        title={
          <>
            {ds.name} <small className={styles.version}>{ds.version}</small>
          </>
        }
        subtitle={`${ds.record_count} 条记录 · ${ds.validation_status === 'valid' ? '校验有效' : '校验无效'} · ${ds.is_locked ? '已锁定 · 不可编辑' : '未锁定'} · 创建于 ${formatTimestamp(ds.created_at)}`}
        actions={
          <>
            <Link className={styles.linkBtn} to={`/datasets/import?versionOf=${encodeURIComponent(ds.name)}`}
                  data-testid="create-new-version">
              创建新版本
            </Link>
          </>
        }
      />

      <Tabs
        items={[
          { key: 'overview', label: '概览' },
          { key: 'validation', label: '校验' },
          { key: 'records', label: '记录', count: ds.record_count },
        ]}
        initial={initialTab}
        onChange={(key) => setSearchParams(key === 'overview' ? {} : { tab: key }, { replace: true })}
      >
        {(active) => (
          <>
            {active === 'overview' ? (
              <Grid cols={4} gap="lg">
                <Card label="版本 Version" value={ds.version} />
                <Card label="记录数" value={String(ds.record_count)} />
                <Card label="校验状态" accent={ds.validation_status === 'valid' ? 'pass' : 'error'}>
                  <Tag tone={ds.validation_status === 'valid' ? 'pass' : 'error'}>
                    {ds.validation_status === 'valid' ? '有效' : '无效'}
                  </Tag>
                </Card>
                <Card label="锁定状态" value={ds.is_locked ? '已锁定' : '未锁定'} />
                <div className={styles.metaWide}>
                  <span>项目 {projectName}</span>
                  <span>创建时间 {formatTimestamp(ds.created_at)}</span>
                  <span>数据集 ID <code>{ds.id}</code></span>
                </div>
              </Grid>
            ) : null}
            {active === 'validation' ? <ValidationReport validation={detail.validation} /> : null}
            {active === 'records' ? (
              <RecordExplorer
                records={detail.records}
                page={detail.recordPage}
                pageSize={detail.recordPageSize}
                total={detail.records?.total ?? 0}
                onPageChange={detail.setRecordPage}
              />
            ) : null}
          </>
        )}
      </Tabs>
    </>
  );
}
