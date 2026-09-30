/**
 * FailureExplorerPage — Failure Explorer（§十二，一级诊断能力）。
 * Run → 过滤（severity / failure_type 走后端查询参数，metric / 状态为展示层过滤）
 * → 失败列表 → 点击打开 Diagnosis Drawer（完整链路：Sample → Metrics →
 * Diagnosis → Evidence → Recommendation）。数据全部来自后端只读 API。
 */

import { useCallback, useEffect, useState } from 'react';

import { api, fetchAllPages } from '../api/client';
import type { DiagnosesPageResponse, RecommendationsResponse, ResultsPageResponse } from '../api/types';
import { useProject } from '../context/ProjectContext';
import { useRunOptions } from '../hooks/useRunOptions';
import { useEvaluationCatalog } from '../hooks/useEvaluationCatalog';
import { PageHeader, Panel, Section } from '../components/primitives/Surfaces';
import { BilingualLabel } from '../components/primitives/Bilingual';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { FailureAnalysisDrawer } from '../components/evaluation/FailureAnalysisDrawer';
import type { DrawerTarget } from '../components/evaluation/FailureAnalysisDrawer';
import type { RecommendationView } from '../components/analysis/types';
import { SeverityBadge } from '../status/StatusBadge';
import { presentCategory, presentSeverity } from '../status/status';
import styles from './FailureExplorerPage.module.css';

/** 展示层可选的严重度档位（后端枚举），label 由 presentSeverity 提供，
 *  与下面表格里的徽标保持同一套中文。 */
const SEVERITY_OPTIONS = ['CRITICAL', 'ERROR', 'WARNING', 'INFO'] as const;

export interface FailureExplorerRecommendation extends RecommendationView {
  diagnosis_id: string;
}

export function FailureExplorerPage() {
  const { activeProject } = useProject();
  const { state, error, runs } = useRunOptions(activeProject?.id ?? null);
  const [runId, setRunId] = useState('');
  const [severity, setSeverity] = useState('');
  const [failureType, setFailureType] = useState('');
  const [metric, setMetric] = useState('');
  const [category, setCategory] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const catalog = useEvaluationCatalog();
  // 指标 → 类别映射来自后端 MetricRegistry 目录（GET /api/metrics），纯渲染映射
  const categoryByMetric = new Map(catalog.metrics.map((m) => [m.name, m.category]));

  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<unknown>(null);
  const [diagnoses, setDiagnoses] = useState<DiagnosesPageResponse['items']>([]);
  const [runResults, setRunResults] = useState<ResultsPageResponse['items']>([]);
  const [recommendations, setRecommendations] = useState<FailureExplorerRecommendation[]>([]);
  const [selected, setSelected] = useState<DrawerTarget | null>(null);

  const load = useCallback(async () => {
    if (!runId) {
      setDiagnoses([]);
      setRunResults([]);
      setRecommendations([]);
      return;
    }
    setLoading(true);
    setFetchError(null);
    try {
      const severityParam = severity || undefined;
      const typeParam = failureType || undefined;
      const [dia, res] = await Promise.all([
        fetchAllPages<DiagnosesPageResponse['items'][number]>(
          (p, ps) => {
            const params = new URLSearchParams({ page: String(p), page_size: String(ps) });
            if (severityParam) params.set('severity', severityParam);
            if (typeParam) params.set('failure_type', typeParam);
            return `/api/evaluations/${runId}/diagnoses?${params.toString()}`;
          },
        ),
        fetchAllPages<ResultsPageResponse['items'][number]>(
          // 不按 is_failure 过滤：undetermined 诊断指向 is_failure=false 的结果，
          // 过滤会让它们的题面空白。results 每记录一行，量级与 diagnoses 相当。
          (p, ps) => `/api/evaluations/${runId}/results?page=${p}&page_size=${ps}`,
        ),
      ]);
      const recs = await api.get<RecommendationsResponse>(
        `/api/evaluations/${runId}/recommendations`,
      );
      setDiagnoses(dia);
      setRunResults(res);
      setRecommendations(recs.items as FailureExplorerRecommendation[]);
    } catch (e) {
      setFetchError(e);
      setDiagnoses([]);
      setRunResults([]);
    } finally {
      setLoading(false);
    }
  }, [runId, severity, failureType]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!activeProject) {
    return (
      <>
        <PageHeader title={<BilingualLabel zh="失败浏览器" en="Failure Explorer" />} subtitle="跨指标浏览失败样本并下钻诊断。" />
        <EmptyState title="请选择项目" description="失败浏览器在项目内工作：先在右上角选择项目。" icon="circle" />
      </>
    );
  }
  if (state === 'loading') return <LoadingState label="正在加载运行列表…" />;
  if (state === 'error') return <ErrorState error={error} onRetry={() => void 0} />;

  const terminal = runs.filter((r) => !['pending', 'running'].includes(r.status));
  const questionByResult = new Map(runResults.map((r) => [r.id, r.question]));

  // 展示层过滤选项（来自当前 Run 的诊断数据，非业务计算）
  const metricOptions = Array.from(
    new Set(diagnoses.map((d) => d.related_metric).filter((m): m is string => Boolean(m))),
  ).sort();
  const failureTypeOptions = Array.from(
    new Set(diagnoses.map((d) => d.failure_type).filter((t): t is string => Boolean(t))),
  ).sort();

  const rows = diagnoses.filter((d) => {
    if (metric && d.related_metric !== metric) return false;
    if (category) {
      const cat = d.related_metric ? categoryByMetric.get(d.related_metric) : undefined;
      if (cat !== category) return false;
    }
    if (statusFilter === 'diagnosed' && d.status !== 'diagnosed') return false;
    if (statusFilter === 'undetermined' && d.status !== 'undetermined') return false;
    return true;
  });

  const openDrawer = (d: DiagnosesPageResponse['items'][number]) => {
    setSelected({
      runId,
      resultId: d.result_id,
      severity: d.severity,
      failureType: d.failure_type,
      relatedMetric: d.related_metric,
      diagnosis: d,
      recommendations: recommendations
        .filter((r) => r.diagnosis_id === d.id)
        .map((r) => ({ action: r.action, priority: r.priority, source: r.source })),
      fallbackQuestion: d.result_id ? questionByResult.get(d.result_id) ?? null : null,
    });
  };

  return (
    <>
      <PageHeader
        title={<BilingualLabel zh="失败浏览器" en="Failure Explorer" />}
        subtitle={`项目 ${activeProject.name} · 指标 → 失败 → 诊断 → 证据 → 建议`}
      />

      <Panel title="过滤" accent="info">
        <div className={styles.filters}>
          <label className={styles.field}>
            <span>运行</span>
            <select value={runId} onChange={(e) => setRunId(e.target.value)} data-testid="failure-run-select">
              <option value="">— 选择运行 —</option>
              {terminal.map((r) => (
                <option key={r.runId} value={r.runId}>{r.label}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>严重度</span>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)} data-testid="failure-severity-filter">
              <option value="">全部</option>
              {SEVERITY_OPTIONS.map((s) => (
                <option key={s} value={s}>{presentSeverity(s).label}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>失败类型</span>
            <select value={failureType} onChange={(e) => setFailureType(e.target.value)} data-testid="failure-type-filter">
              <option value="">全部</option>
              {failureTypeOptions.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>指标</span>
            <select value={metric} onChange={(e) => setMetric(e.target.value)} data-testid="failure-metric-filter">
              <option value="">全部</option>
              {metricOptions.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>类别</span>
            <select value={category} onChange={(e) => setCategory(e.target.value)} data-testid="failure-category-filter">
              <option value="">全部</option>
              <option value="retrieval">检索 Retrieval</option>
              <option value="generation">生成 Generation</option>
              <option value="integrity">一致性 Integrity</option>
            </select>
          </label>
          <label className={styles.field}>
            <span>状态</span>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} data-testid="failure-status-filter">
              <option value="all">全部</option>
              <option value="diagnosed">已诊断</option>
              <option value="undetermined">无法判定</option>
            </select>
          </label>
        </div>
      </Panel>

      {fetchError ? <ErrorState error={fetchError} onRetry={() => void load()} /> : null}

      {runId ? (
        <Section title="失败与诊断" actions={<span className={styles.count}>{rows.length} 条</span>}>
          {loading ? (
            <LoadingState label="正在加载诊断…" />
          ) : rows.length === 0 ? (
            <EmptyState
              title="无匹配记录"
              description="该运行（或当前过滤条件）下没有失败/诊断记录。"
              icon="check"
            />
          ) : (
            <Panel padded={false}>
              <table className={styles.table} data-testid="failure-table">
                <thead>
                  <tr>
                    <th align="left">严重度</th>
                    <th align="left">状态</th>
                    <th align="left">失败类型</th>
                    <th align="left">指标</th>
                    <th align="left">类别</th>
                    <th align="left">问题</th>
                    <th align="right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((d) => (
                    <tr key={d.id} data-testid="failure-row">
                      <td><SeverityBadge severity={d.severity} /></td>
                      <td>
                        {d.status === 'undetermined' ? (
                          <span className={styles.undetermined}>无法判定</span>
                        ) : (
                          <span className={styles.diagnosed}>已诊断</span>
                        )}
                      </td>
                      <td className="mono">{d.failure_type ?? '—'}</td>
                      <td className="mono">{d.related_metric ?? '—'}</td>
                      <td>{d.related_metric ? presentCategory(categoryByMetric.get(d.related_metric) ?? null).label : '—'}</td>
                      <td className={styles.question}>
                        {d.result_id ? questionByResult.get(d.result_id) ?? '—' : '（运行级诊断）'}
                      </td>
                      <td align="right">
                        <button type="button" className={styles.openBtn} onClick={() => openDrawer(d)} data-testid="open-diagnosis">
                          诊断 →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
        </Section>
      ) : (
        <EmptyState title="选择一个运行" description="选择一个运行后浏览其失败样本与诊断。" icon="circle" />
      )}

      <FailureAnalysisDrawer target={selected} onClose={() => setSelected(null)} />
    </>
  );
}
