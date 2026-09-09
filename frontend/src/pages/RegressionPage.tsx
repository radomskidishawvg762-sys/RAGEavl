/**
 * RegressionPage — 回归分析（§十四）。全部来自 GET /api/comparisons/regression：
 * overall verdict、分类别 summary（retrieval/generation/integrity）、逐指标
 * verdict（含 epsilon + epsilon_source）、trade-off。前端不判定回归；
 * NOT_COMPARABLE / UNDETERMINED 如实呈现。
 */

import { useCallback, useEffect, useState } from 'react';

import { api, endpoints } from '../api/client';
import type { RegressionResponse } from '../api/types';
import { useProject } from '../context/ProjectContext';
import { useRunOptions } from '../hooks/useRunOptions';
import { PageHeader, Panel, Section } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ScoreValue } from '../components/ScoreValue';
import { RegressionBadge, SeverityBadge } from '../status/StatusBadge';
import { presentRegression, presentCategory } from '../status/status';
import { formatPercent } from '../utils/format';
import styles from './RegressionPage.module.css';

const CATEGORY_ORDER = ['retrieval', 'generation', 'integrity'] as const;

export function RegressionPage() {
  const { activeProject } = useProject();
  const { state, error, runs } = useRunOptions(activeProject?.id ?? null);
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const [result, setResult] = useState<RegressionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<unknown>(null);

  const compute = useCallback(async () => {
    if (!baseline || !candidate) return;
    setLoading(true);
    setFetchError(null);
    try {
      const data = await api.get<RegressionResponse>(endpoints.regression(baseline, candidate));
      setResult(data);
    } catch (e) {
      setResult(null);
      setFetchError(e);
    } finally {
      setLoading(false);
    }
  }, [baseline, candidate]);

  useEffect(() => {
    setResult(null);
    setFetchError(null);
  }, [baseline, candidate]);

  if (!activeProject) {
    return (
      <>
        <PageHeader title="回归分析 Regression" subtitle="在项目上下文中检测两个 Run 之间的质量回归。" />
        <EmptyState title="请选择项目" description="回归检测在项目内进行：先在右上角选择项目。" icon="circle" />
      </>
    );
  }
  if (state === 'loading') return <LoadingState label="正在加载运行列表…" />;
  if (state === 'error') return <ErrorState error={error} onRetry={() => void 0} />;

  const terminal = runs.filter((r) => !['pending', 'running'].includes(r.status));

  return (
    <>
      <PageHeader
        title="回归分析 Regression"
        subtitle={`项目 ${activeProject.name} · 判定全部来自后端（epsilon 与 epsilon_source 可追溯）`}
      />

      <Panel title="选择 Run" accent="info">
        <div className={styles.selectors}>
          <label className={styles.field}>
            <span>Baseline Run</span>
            <select value={baseline} onChange={(e) => setBaseline(e.target.value)} data-testid="regression-baseline-select">
              <option value="">— 选择 Baseline —</option>
              {terminal.map((r) => (
                <option key={r.runId} value={r.runId}>{r.label}</option>
              ))}
            </select>
          </label>
          <span className={styles.vs}>→</span>
          <label className={styles.field}>
            <span>Candidate Run</span>
            <select value={candidate} onChange={(e) => setCandidate(e.target.value)} data-testid="regression-candidate-select">
              <option value="">— 选择 Candidate —</option>
              {terminal.map((r) => (
                <option key={r.runId} value={r.runId}>{r.label}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={!baseline || !candidate || loading || baseline === candidate}
            onClick={() => void compute()}
            data-testid="regression-submit"
          >
            {loading ? '分析中…' : '开始回归分析'}
          </button>
        </div>
      </Panel>

      {fetchError ? <ErrorState error={fetchError} onRetry={() => void compute()} /> : null}
      {result ? <RegressionResult result={result} /> : null}
    </>
  );
}

function RegressionResult({ result }: { result: RegressionResponse }) {
  const overall = presentRegression(result.overall.verdict);
  return (
    <>
      {result.trade_off ? (
        <Panel title="Trade-off 提示" accent="warning">
          <div className={styles.tradeOff} data-testid="trade-off-banner">
            <SeverityBadge severity="WARNING" />
            <span>
              检测到指标间权衡（后端 trade_off=true）。
              {result.overall.reason ? ` ${result.overall.reason}` : ''}
            </span>
          </div>
        </Panel>
      ) : null}

      <Panel title="总体判定 Overall Verdict" accent={overall.tone === 'pass' ? 'pass' : overall.tone === 'error' ? 'error' : 'neutral'}>
        <div className={styles.overallRow} data-testid="regression-overall">
          <RegressionBadge verdict={result.overall.verdict} />
          {result.overall.overall_score_delta !== null ? (
            <span className={styles.dim}>
              总分变化（辅助信息）：<span className="mono">{result.overall.overall_score_delta > 0 ? '+' : ''}{result.overall.overall_score_delta.toFixed(4)}</span>
            </span>
          ) : null}
          {result.overall.reason ? <span className={styles.dim}>{result.overall.reason}</span> : null}
        </div>
      </Panel>

      <Section title="分类别 Summary">
        <div className={styles.categories} data-testid="regression-categories">
          {CATEGORY_ORDER.map((cat) => {
            const c = result.categories[cat];
            const label = presentCategory(cat).label;
            if (!c) {
              return (
                <div key={cat} className={styles.categoryCard}>
                  <div className={styles.categoryTitle}>{label}</div>
                  <div className={styles.dim}>无该类别指标</div>
                </div>
              );
            }
            return (
              <div key={cat} className={styles.categoryCard} data-testid={`regression-category-${cat}`}>
                <div className={styles.categoryTitle}>{label}</div>
                <RegressionBadge verdict={c.verdict} />
                <div className={styles.categoryCounts}>
                  <span>改进 {c.improvement_count}</span>
                  <span>回归 {c.regression_count}</span>
                  <span>稳定 {c.stable_count}</span>
                  <span className={styles.dim}>可比 {c.comparable_count}</span>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="逐指标判定" actions={<span className={styles.count}>{result.metrics.length} 项</span>}>
        {result.metrics.length === 0 ? (
          <EmptyState title="无可分析指标" description="后端未返回可分析的指标。" icon="circle" />
        ) : (
          <Panel padded={false}>
            <table className={styles.table} data-testid="regression-table">
              <thead>
                <tr>
                  <th align="left">指标</th>
                  <th align="left">方向</th>
                  <th align="right">Baseline</th>
                  <th align="right">Candidate</th>
                  <th align="right">Delta</th>
                  <th align="right">Relative</th>
                  <th align="left">Epsilon</th>
                  <th align="left">判定</th>
                </tr>
              </thead>
              <tbody>
                {result.metrics.map((m) => (
                  <tr key={m.metric} data-testid={`reg-${m.metric}`}>
                    <td className="mono">{m.metric}</td>
                    <td>{m.direction === 'lower_is_better' ? '越低越好' : '越高越好'}</td>
                    <td align="right" className="mono"><ScoreValue score={m.baseline_score} /></td>
                    <td align="right" className="mono"><ScoreValue score={m.candidate_score} /></td>
                    <td align="right" className="mono">
                      {m.delta === null ? '—' : `${m.delta > 0 ? '+' : ''}${m.delta.toFixed(4)}`}
                    </td>
                    <td align="right" className="mono">
                      {m.relative_delta === null ? '—' : formatPercent(m.relative_delta)}
                    </td>
                    <td className={styles.epsilon}>
                      <span className="mono">{m.epsilon}</span>
                      <span className={styles.dim}>（{m.epsilon_source === 'config' ? '配置' : '临时默认'}）</span>
                    </td>
                    <td><RegressionBadge verdict={m.verdict} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
      </Section>
    </>
  );
}
