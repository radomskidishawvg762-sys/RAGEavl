/**
 * ComparePage — 版本对比（§十三）。Baseline/Candidate 两个 Run 的对比全部来自
 * GET /api/comparisons：comparability（DIRECT/LIMITED/BLOCKED + reasons）、
 * 逐指标 delta / relative_delta、overall。前端只渲染，绝不自行计算或判断
 * 可比性；BLOCKED 时严格遵循后端返回（metrics = []，只展示原因）。
 */

import { useCallback, useEffect, useState } from 'react';

import { api, endpoints } from '../api/client';
import type { ComparisonResponse, MetricExecutionStatus } from '../api/types';
import { useProject } from '../context/ProjectContext';
import { useRunOptions } from '../hooks/useRunOptions';
import { PageHeader, Panel, Section } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ScoreValue } from '../components/ScoreValue';
import { ComparabilityBadge } from '../status/StatusBadge';
import { presentCategory, presentMetric } from '../status/status';
import { formatPercent } from '../utils/format';
import styles from './ComparePage.module.css';

export function ComparePage() {
  const { activeProject } = useProject();
  const { state, error, runs } = useRunOptions(activeProject?.id ?? null);
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const [result, setResult] = useState<ComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<unknown>(null);

  const compare = useCallback(async () => {
    if (!baseline || !candidate) return;
    setLoading(true);
    setFetchError(null);
    try {
      const data = await api.get<ComparisonResponse>(endpoints.comparisons(baseline, candidate));
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
        <PageHeader title="版本对比 Compare" subtitle="在项目上下文中对比两个运行的逐指标表现。" />
        <EmptyState title="请选择项目" description="对比在项目内进行：先在右上角选择项目。" icon="circle" />
      </>
    );
  }
  if (state === 'loading') return <LoadingState label="正在加载运行列表…" />;
  if (state === 'error') return <ErrorState error={error} onRetry={() => void 0} />;

  const terminal = runs.filter((r) => !['pending', 'running'].includes(r.status));

  return (
    <>
      <PageHeader
        title="版本对比 Compare"
        subtitle={`项目 ${activeProject.name} · 两个运行的逐指标对比（变化量 delta 全部来自后端）`}
      />

      <Panel title="选择运行" accent="info">
        <div className={styles.selectors}>
          <label className={styles.field}>
            <span>基线运行 Baseline</span>
            <select value={baseline} onChange={(e) => setBaseline(e.target.value)} data-testid="baseline-select">
              <option value="">— 选择基线运行 —</option>
              {terminal.map((r) => (
                <option key={r.runId} value={r.runId}>{r.label}</option>
              ))}
            </select>
          </label>
          <span className={styles.vs}>VS</span>
          <label className={styles.field}>
            <span>候选运行 Candidate</span>
            <select value={candidate} onChange={(e) => setCandidate(e.target.value)} data-testid="candidate-select">
              <option value="">— 选择候选运行 —</option>
              {terminal.map((r) => (
                <option key={r.runId} value={r.runId}>{r.label}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={!baseline || !candidate || loading || baseline === candidate}
            onClick={() => void compare()}
            data-testid="compare-submit"
          >
            {loading ? '对比中…' : '开始对比'}
          </button>
        </div>
        {baseline && baseline === candidate ? (
          <p className={styles.hint}>基线运行与候选运行不能是同一次运行。</p>
        ) : null}
      </Panel>

      {fetchError ? <ErrorState error={fetchError} onRetry={() => void compare()} /> : null}

      {result ? <ComparisonResult result={result} /> : null}
      {!result && !fetchError && baseline && candidate && !loading ? (
        <EmptyState title="尚无结果" description="点击「开始对比」获取后端对比结果。" icon="circle" />
      ) : null}
    </>
  );
}

function ComparisonResult({ result }: { result: ComparisonResponse }) {
  return (
    <>
      <Panel
        title="可比性 Comparability"
        accent={result.comparability.status === 'DIRECT' ? 'pass' : result.comparability.status === 'LIMITED' ? 'warning' : 'error'}
      >
        <div className={styles.banner} data-testid="comparability-banner">
          <ComparabilityBadge status={result.comparability.status} />
        </div>
        {result.comparability.reasons.length ? (
          <ul className={styles.reasons} data-testid="comparability-reasons">
            {result.comparability.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : null}
        {result.comparability.status === 'BLOCKED' ? (
          <p className={styles.blockedNote} data-testid="blocked-note">
            两次运行不可比较：以上原因由后端判定。以下仅展示后端返回的数据（通常为空）。
          </p>
        ) : null}
      </Panel>

      <Section title="总体对比 Overall">
        <div className={styles.overall} data-testid="overall-comparison">
          <div className={styles.overallCell}>
            <span className={styles.cellLabel}>基线总体</span>
            <ScoreValue score={result.overall.baseline_overall} />
          </div>
          <div className={styles.overallCell}>
            <span className={styles.cellLabel}>候选总体</span>
            <ScoreValue score={result.overall.candidate_overall} />
          </div>
          <div className={styles.overallCell}>
            <span className={styles.cellLabel}>变化量 Delta</span>
            {/* What is unavailable is the DELTA, not the two scores beside it —
                rendering ScoreValue's "暂无有效分数" here implied those were
                invalid, which they are not. */}
            {result.overall.comparable ? (
              <DeltaValue delta={result.overall.delta} />
            ) : (
              <span>不可比</span>
            )}
          </div>
          {result.overall.comparable ? null : (
            <div className={styles.overallReason}>{result.overall.reason ?? '不可比'}</div>
          )}
        </div>
      </Section>

      <Section title="指标对比 Metrics" actions={<span className={styles.count}>{result.metrics.length} 项</span>}>
        {result.metrics.length === 0 ? (
          <EmptyState
            title="无可对比指标"
            description="后端未返回可对比的指标（常见于不可比较或两侧均无有效分数）。"
            icon="circle"
          />
        ) : (
          <Panel padded={false}>
            <table className={styles.table} data-testid="comparison-table">
              <thead>
                <tr>
                  <th align="left">指标</th>
                  <th align="left">类别</th>
                  <th align="right">基线</th>
                  <th align="right">候选</th>
                  <th align="right">变化量 Delta</th>
                  <th align="right">相对变化 Relative</th>
                  <th align="left">状态</th>
                </tr>
              </thead>
              <tbody>
                {result.metrics.map((m) => (
                  <tr key={m.name} data-testid={`cmp-${m.name}`}>
                    <td className="mono">{m.name}</td>
                    <td>{presentCategory(m.category).label}</td>
                    <td align="right" className="mono"><ScoreValue score={m.baseline_score} /></td>
                    <td align="right" className="mono"><ScoreValue score={m.candidate_score} /></td>
                    <td align="right" className="mono"><DeltaValue delta={m.delta} /></td>
                    <td align="right" className="mono">
                      {m.relative_delta === null ? '—' : formatPercent(m.relative_delta)}
                    </td>
                    <td>
                      {m.comparable ? (
                        <span className={styles.ok}>可比</span>
                      ) : (
                        <span className={styles.incomparable} title={m.incomparable_reason ?? ''}>
                          {incomparableLabel(m.baseline_status, m.candidate_status)}
                        </span>
                      )}
                    </td>
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

export function DeltaValue({ delta }: { delta: number | null }) {
  if (delta === null || Number.isNaN(delta)) return <ScoreValue score={null} />;
  const text = `${delta > 0 ? '+' : ''}${delta.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')}`;
  return (
    <span className={delta > 0 ? styles.deltaUp : delta < 0 ? styles.deltaDown : ''} data-delta={delta}>
      {text}
    </span>
  );
}

function incomparableLabel(baselineStatus: string, candidateStatus: string): string {
  if (baselineStatus === 'missing') return '基线缺失该指标';
  if (candidateStatus === 'missing') return '候选缺失该指标';
  // 状态本身不再原样露出英文枚举 —— 走的还是徽标用的那一套中文。
  if (baselineStatus !== 'completed') {
    return `基线：${presentMetric(baselineStatus as MetricExecutionStatus, null).label}`;
  }
  if (candidateStatus !== 'completed') {
    return `候选：${presentMetric(candidateStatus as MetricExecutionStatus, null).label}`;
  }
  return '不可比';
}
