/**
 * QualityGatePage — 质量门禁（§十五）。数据全部来自
 * GET /api/evaluations/{run}/quality-gate。关键区分（后端 reason 代码）：
 *   QUALITY_THRESHOLD_FAILED          → 有分数但低于阈值（真实质量失败）
 *   REQUIRED_METRIC_NOT_EVALUABLE     → 必需指标无法评估（score=null，绝非 0）
 * NOT_EVALUABLE ≠ FAIL：null 分数永不渲染为 0。
 */

import { useCallback, useEffect, useState } from 'react';

import { api, endpoints } from '../api/client';
import type { QualityGateResponse } from '../api/types';
import { useProject } from '../context/ProjectContext';
import { useRunOptions } from '../hooks/useRunOptions';
import { PageHeader, Panel, Section } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ScoreValue } from '../components/ScoreValue';
import { QualityGateBadge } from '../status/StatusBadge';
import { formatTimestamp } from '../utils/format';
import styles from './QualityGatePage.module.css';

const REASON_EXPLAIN: Record<string, string> = {
  QUALITY_THRESHOLD_FAILED: '必需指标有有效分数，但低于配置阈值（真实质量失败）。',
  REQUIRED_METRIC_NOT_EVALUABLE: '必需指标无法评估（分数为空：未配置 / 执行错误 / 无法判定 / 未执行）。这不是 0 分。',
  QUALITY_GATE_DISABLED: '当前 Profile 的 quality_gate.enabled = false，门禁未启用。',
  QUALITY_GATE_NOT_CONFIGURED: '当前 Profile 未配置 quality_gate，门禁不可用。',
  QUALITY_GATE_CONFIG_INVALID: '门禁配置无效（必需指标未启用或未配置阈值）。',
};

export function QualityGatePage() {
  const { activeProject } = useProject();
  const { state, error, runs } = useRunOptions(activeProject?.id ?? null);
  const [runId, setRunId] = useState('');
  const [gate, setGate] = useState<QualityGateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<unknown>(null);

  const evaluate = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setFetchError(null);
    try {
      const data = await api.get<QualityGateResponse>(endpoints.qualityGate(runId));
      setGate(data);
    } catch (e) {
      setGate(null);
      setFetchError(e);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    setGate(null);
    setFetchError(null);
  }, [runId]);

  if (!activeProject) {
    return (
      <>
        <PageHeader title="质量门禁 Quality Gate" subtitle="单次 Run 的绝对质量标准判定。" />
        <EmptyState title="请选择项目" description="门禁评估在项目内进行：先在右上角选择项目。" icon="circle" />
      </>
    );
  }
  if (state === 'loading') return <LoadingState label="正在加载运行列表…" />;
  if (state === 'error') return <ErrorState error={error} onRetry={() => void 0} />;

  const terminal = runs.filter((r) => !['pending', 'running'].includes(r.status));

  return (
    <>
      <PageHeader
        title="质量门禁 Quality Gate"
        subtitle={`项目 ${activeProject.name} · 判定来自 Profile 的 quality_gate 配置（平台不内置任何默认阈值）`}
      />

      <Panel title="选择 Run" accent="info">
        <div className={styles.selectors}>
          <label className={styles.field}>
            <span>Run</span>
            <select value={runId} onChange={(e) => setRunId(e.target.value)} data-testid="gate-run-select">
              <option value="">— 选择 Run —</option>
              {terminal.map((r) => (
                <option key={r.runId} value={r.runId}>{r.label}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={!runId || loading}
            onClick={() => void evaluate()}
            data-testid="gate-submit"
          >
            {loading ? '评估中…' : '评估门禁'}
          </button>
        </div>
      </Panel>

      {fetchError ? <ErrorState error={fetchError} onRetry={() => void evaluate()} /> : null}
      {gate ? <GateResult gate={gate} /> : null}
    </>
  );
}

function GateResult({ gate }: { gate: QualityGateResponse }) {
  return (
    <>
      <Panel
        title="门禁结果 Gate Status"
        accent={gate.status === 'PASS' ? 'pass' : gate.status === 'FAIL' ? 'error' : 'neutral'}
      >
        <div className={styles.gateRow} data-testid="gate-status">
          <QualityGateBadge status={gate.status} />
          <span className={styles.dim}>评估于 {formatTimestamp(gate.evaluated_at)}</span>
          <span className={styles.dim}>
            总分（仅展示，不参与判定）：<ScoreValue score={gate.overall_score} />
          </span>
        </div>
        {gate.reasons.length ? (
          <ul className={styles.reasons} data-testid="gate-reasons">
            {gate.reasons.map((r) => (
              <li key={r}>
                <code>{r}</code>
                <span className={styles.reasonExplain}>{REASON_EXPLAIN[r] ?? ''}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>

      <Section title="必需指标 Required Metrics" actions={<span className={styles.count}>{gate.metrics.length} 项</span>}>
        {gate.metrics.length === 0 ? (
          <EmptyState title="无必需指标记录" description="后端未返回门禁指标明细。" icon="circle" />
        ) : (
          <Panel padded={false}>
            <table className={styles.table} data-testid="gate-metrics-table">
              <thead>
                <tr>
                  <th align="left">指标</th>
                  <th align="right">分数</th>
                  <th align="right">阈值</th>
                  <th align="left">判定</th>
                  <th align="left">状态</th>
                  <th align="left">原因</th>
                </tr>
              </thead>
              <tbody>
                {gate.metrics.map((m) => (
                  <tr key={m.metric} data-testid={`gate-${m.metric}`}>
                    <td className="mono">{m.metric}</td>
                    <td align="right" className="mono"><ScoreValue score={m.score} /></td>
                    <td align="right" className="mono">{m.threshold === null ? '—' : m.threshold}</td>
                    <td>
                      {m.passed === null ? (
                        <span className={styles.dim}>—</span>
                      ) : m.passed ? (
                        <span className={styles.pass}>通过</span>
                      ) : (
                        <span className={styles.fail}>未通过</span>
                      )}
                    </td>
                    <td><QualityGateBadge status={m.status} /></td>
                    <td className={styles.reasonCell}>{m.reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
        <p className={styles.hint}>
          「无法评估」表示必需指标没有有效分数（score=null），与「有分数但低于阈值」是两类不同原因；覆盖率等执行信息见评估工作台。
        </p>
      </Section>
    </>
  );
}
