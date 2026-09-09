/**
 * EvaluationDetailPage — 评估工作台（产品核心页面，UX Polish §九）。
 * Run Header（状态/进度/导出）→ 六 Tab：
 *   Overview   质量概览 + 失败概览 + 执行概览
 *   Metrics    指标按 检索/生成/一致性 分组
 *   Failures   失败样本 DataTable（点击 → 下钻抽屉）
 *   Diagnostics  诊断卡片（diagnosed + undetermined，按严重度）
 *   Evidence   证据明细表（diagnoses.evidence 持久化项，G2）
 *   Configuration Run 快照（复现字段 / effective_metrics / overrides / judge）
 * 数据全部来自后端；导出即原样下载后端 JSON；前端不重算任何业务语义。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import type { DiagnosisOut, MetricReportItem, ReportResponse, RunStatus } from '../api/types';
import { useEvaluationWorkspace } from '../hooks/useEvaluationWorkspace';
import { useProgressPolling } from '../hooks/useProgressPolling';
import { PageHeader, Panel, Section, Card } from '../components/primitives/Surfaces';
import { Grid } from '../components/primitives/Layout';
import { Tabs } from '../components/primitives/Tabs';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ScoreValue } from '../components/ScoreValue';
import { RunStatusBadge, QualityGateBadge, MetricStatusBadge, SeverityBadge } from '../status/StatusBadge';
import { FailureAnalysisDrawer } from '../components/evaluation/FailureAnalysisDrawer';
import type { DrawerTarget } from '../components/evaluation/FailureAnalysisDrawer';
import type { RecommendationView } from '../components/analysis/types';
import { formatPercent, formatTimestamp } from '../utils/format';
import { safeReproducibility } from '../api/mappers';
import styles from './EvaluationDetailPage.module.css';

const CATEGORY_ORDER = ['retrieval', 'generation', 'integrity'] as const;
const CATEGORY_LABEL: Record<string, string> = { retrieval: '检索 Retrieval', generation: '生成 Generation', integrity: '一致性 Integrity' };
const CATEGORY_EMPTY: Record<string, string> = { retrieval: '无检索指标', generation: '无生成指标', integrity: '无一致性指标' };

/** 原样下载后端 JSON（机器报告 / 失败清单）— 不做任何数据加工。 */
function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function EvaluationDetailPage({ runId: runIdProp }: { runId?: string }) {
  const params = useParams<{ runId: string }>();
  const runId = runIdProp ?? params.runId ?? '';
  const ws = useEvaluationWorkspace(runId || null);
  const { report, qualityGate, failureResults, diagnoses, recommendations } = ws;
  const [selected, setSelected] = useState<DrawerTarget | null>(null);

  const initialStatus = (report?.summary.status ?? 'pending') as RunStatus;
  const failureGroups = useMemo(() => aggregateFailures(report?.failures ?? []), [report?.failures]);
  const evidenceRows = useMemo(() => collectEvidenceRows(diagnoses), [diagnoses]);
  const { status: polledStatus, progress, isPolling } = useProgressPolling(runId, initialStatus);
  useEffect(() => {
    if (!isPolling && report && polledStatus !== report.summary.status) {
      void ws.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPolling, polledStatus]);

  const openFailure = useCallback(
    (failure: ReportResponse['failures'][number]) => {
      const diag = diagnoses.find((d) => d.id === failure.diagnosis_id) ?? null;
      const recs: RecommendationView[] = recommendations
        .filter((r) => r.diagnosis_id === failure.diagnosis_id)
        .map((r) => ({ action: r.action, priority: r.priority, source: r.source }));
      setSelected({
        runId,
        resultId: diag?.result_id ?? null,
        severity: failure.severity ?? diag?.severity ?? 'INFO',
        failureType: failure.failure_type ?? diag?.failure_type ?? null,
        relatedMetric: failure.related_metric ?? diag?.related_metric ?? null,
        diagnosis: diag,
        recommendations: recs,
        fallbackQuestion: failure.question,
      });
    },
    [runId, diagnoses, recommendations],
  );

  const openDiagnosis = useCallback(
    (diag: DiagnosisOut) => {
      const recs: RecommendationView[] = recommendations
        .filter((r) => r.diagnosis_id === diag.id)
        .map((r) => ({ action: r.action, priority: r.priority, source: r.source }));
      setSelected({
        runId,
        resultId: diag.result_id,
        severity: diag.severity,
        failureType: diag.failure_type,
        relatedMetric: diag.related_metric,
        diagnosis: diag,
        recommendations: recs,
      });
    },
    [runId, recommendations],
  );

  if (ws.state === 'loading') return <LoadingState label="正在加载评估结果…" />;
  if (ws.state === 'error') return <ErrorState error={ws.error} onRetry={ws.reload} />;
  if (!report) return <EmptyState title="未找到评估" description="该运行可能已被删除。" icon="circle" />;

  const summary = report.summary;
  const meta = (report.reproducibility ?? {}) as Record<string, unknown>;
  const byCategory = groupByCategory(report.metrics);
  const diagnosed = report.failures;
  const undetermined = report.undetermined;

  return (
    <>
      <PageHeader
        breadcrumb={<><span>评估</span><span>/</span><span className="mono">{runId.slice(0, 8)}…</span></>}
        title={`评估运行 ${runId.slice(0, 8)}`}
        subtitle={runId}
        actions={
          <>
            <a
              className={styles.linkBtn}
              href={`/api/evaluations/${runId}/export?format=pdf`}
              data-testid="export-report-pdf"
              title="一键导出完整评估报告 PDF（只读快照，双语；不重跑评估）"
            >
              导出报告 PDF Export
            </a>
            <a
              className={styles.linkBtn}
              href={`/api/evaluations/${runId}/export?format=json`}
              data-testid="export-report-json"
              title="导出机读报告 JSON（含 run/dimensions/metrics/failures/diagnoses/evidence/recommendations/summary）"
            >
              导出报告 JSON
            </a>
            <button
              type="button"
              className={styles.linkBtn}
              onClick={() => downloadJson(`failures-${runId.slice(0, 8)}.json`, failureResults)}
              data-testid="export-failures"
              title="原样下载失败样本清单 JSON（FR-31）"
            >
              导出失败清单
            </button>
            <Link className={styles.linkBtn} to="/compare">Compare</Link>
            <Link className={styles.linkBtn} to={`/failures`}>Failure Explorer</Link>
          </>
        }
      />

      <RunHeader summary={summary} status={polledStatus} isPolling={isPolling} progress={progress} meta={meta} />

      <Tabs
        items={[
          { key: 'overview', label: 'Overview' },
          { key: 'metrics', label: 'Metrics', count: report.metrics.length },
          { key: 'failures', label: 'Failures', count: report.failures.length },
          { key: 'diagnostics', label: 'Diagnostics', count: diagnosed.length + undetermined.length },
          { key: 'evidence', label: 'Evidence', count: evidenceRows.length },
          { key: 'configuration', label: 'Configuration' },
        ]}
        initial="overview"
      >
        {(active) => (
          <>
            {active === 'overview' ? (
              <>
                <Section title="质量概览">
                  <Grid cols={4} gap="lg">
                    <Card label="总体质量"><ScoreValue score={summary.overall_score} /></Card>
                    <Card label="评估覆盖率" value={formatPercent(summary.evaluation_coverage)} />
                    <Card label="评估错误" value={String(summary.error_records)} />
                    <Card label="质量门禁" accent={gateTone(qualityGate?.status ?? null)}>
                      {qualityGate ? <QualityGateBadge status={qualityGate.status} /> : <ScoreValue score={null} />}
                    </Card>
                  </Grid>
                </Section>

                <Section title="质量维度（Overall Score 不代表单维度质量）Quality Dimensions">
                  {report.quality_dimensions.length === 0 ? (
                    <div className={styles.metricEmpty}>无维度数据。</div>
                  ) : (
                    <Panel padded={false}>
                      <table className={styles.failureTable} data-testid="quality-dimensions">
                        <thead>
                          <tr>
                            <th align="left">维度 Dimension</th>
                            <th align="right">分数 Score</th>
                            <th align="right">失败 Failures</th>
                            <th align="right">无法判定 Undetermined</th>
                            <th align="right">诊断覆盖 Diagnosis Coverage</th>
                            <th align="right">证据覆盖 Evidence Coverage</th>
                            <th align="left">指标 Metrics</th>
                          </tr>
                        </thead>
                        <tbody>
                          {report.quality_dimensions.map((d) => (
                              <tr key={d.dimension} data-testid={`dimension-${d.dimension}`}>
                                <td className={d.is_weakest ? styles.weakest : ''}>
                                  <strong>{d.dimension}</strong>
                                  {d.is_weakest ? <span className={styles.weakestTag}>最弱维度 Weakest</span> : null}
                                </td>
                                <td align="right" className={styles.num}><ScoreValue score={d.score} /></td>
                                <td align="right" className={styles.num}>{d.failure_count}</td>
                                <td align="right" className={styles.num}>{d.undetermined_count}</td>
                                <td align="right" className={styles.num}>{formatPercent(d.diagnosis_coverage)}</td>
                                <td align="right" className={styles.num}>{formatPercent(d.evidence_coverage)}</td>
                                <td className="mono">{d.metrics.join(', ')}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </Panel>
                  )}
                </Section>

                <Section title="失败概览">
                  {failureGroups.length === 0 ? (
                    <div className={styles.metricEmpty}>未发现失败。</div>
                  ) : (
                    <table className={styles.failureTable}>
                      <thead>
                        <tr><th align="left">严重度</th><th align="left">失败类型</th><th align="right">数量</th></tr>
                      </thead>
                      <tbody>
                        {failureGroups.map((g) => (
                          <tr key={`${g.severity}-${g.failureType}`}>
                            <td><SeverityBadge severity={g.severity} /></td>
                            <td className="mono">{g.failureType}</td>
                            <td align="right" className={styles.num}>{g.count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Section>

                <Section title="执行概览">
                  <div className={styles.execGrid}>
                    <div className={styles.execItem}><span className={styles.execLabel}>记录</span><span className={styles.execVal}>{summary.evaluated_records}/{summary.total_records}</span></div>
                    <div className={styles.execItem}><span className={styles.execLabel}>评估错误</span><span className={styles.execVal}>{summary.error_records}</span></div>
                    <div className={styles.execItem}><span className={styles.execLabel}>无法判定</span><span className={styles.execVal}>{undetermined.length}</span></div>
                    <div className={styles.execItem}><span className={styles.execLabel}>有效指标</span><span className={styles.execVal}>{summary.valid_metric_count}/{summary.total_enabled_metric_count}</span></div>
                    {summary.message ? <div className={styles.execNote}>{summary.message}</div> : null}
                  </div>
                </Section>
              </>
            ) : null}

            {active === 'metrics' ? (
              <Section title="指标概览">
                <Grid cols={3} gap="lg">
                  {CATEGORY_ORDER.map((cat) => (
                    <MetricGroup key={cat} category={cat} metrics={byCategory[cat] ?? []} />
                  ))}
                </Grid>
              </Section>
            ) : null}

            {active === 'failures' ? (
              <Section title="失败样本 Failure Samples" actions={<span className={styles.count}>{report.failures.length} 条</span>}>
                {report.failures.length === 0 ? (
                  <EmptyState title="无失败样本" description="该运行未发现质量失败样本。" icon="check" />
                ) : (
                  <Panel padded={false}>
                    <table className={styles.failureDataTable} data-testid="failure-data-table">
                      <thead>
                        <tr>
                          <th align="left">严重度</th>
                          <th align="left">失败类型</th>
                          <th align="left">指标</th>
                          <th align="left">Question</th>
                          <th align="right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.failures.map((f, i) => (
                          <tr
                            key={`${f.diagnosis_id}-${i}`}
                            data-testid="failure-sample"
                            onClick={() => openFailure(f)}
                          >
                            <td><SeverityBadge severity={f.severity ?? 'INFO'} /></td>
                            <td className="mono">{f.failure_type ?? '—'}</td>
                            <td className="mono">{f.related_metric ?? '—'}</td>
                            <td className={styles.sampleQ}>{f.question ?? (f.record_id ? `样本 ${f.record_id.slice(0, 8)}…` : '—')}</td>
                            <td align="right"><span className={styles.sampleArrow}>分析 →</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Panel>
                )}
              </Section>
            ) : null}

            {active === 'diagnostics' ? (
              <>
                <Section title="诊断 Diagnosed" actions={<span className={styles.count}>{diagnosed.length} 条</span>}>
                  {diagnosed.length === 0 ? (
                    <div className={styles.metricEmpty}>无已诊断记录。</div>
                  ) : (
                    <div className={styles.diagCards}>
                      {diagnosed.map((f, i) => (
                        <button
                          key={`d-${f.diagnosis_id}-${i}`}
                          type="button"
                          className={styles.diagCard}
                          data-testid="diagnosis-card"
                          onClick={() => openFailure(f)}
                        >
                          <div className={styles.diagHead}>
                            <SeverityBadge severity={f.severity ?? 'INFO'} />
                            <span className={`mono ${styles.diagType}`}>{f.failure_type ?? '—'}</span>
                          </div>
                          <div className={styles.diagCause}>{f.root_cause ?? '—'}</div>
                          <div className={styles.diagMeta}>
                            <span className="mono">{f.related_metric ?? '—'}</span>
                            <span>{f.question ?? '—'}</span>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </Section>
                <Section title="无法判定 Undetermined" actions={<span className={styles.count}>{undetermined.length} 条</span>}>
                  {undetermined.length === 0 ? (
                    <div className={styles.metricEmpty}>无无法判定记录。</div>
                  ) : (
                    <ul className={styles.undeterminedList}>
                      {undetermined.map((u) => (
                        <li key={u.diagnosis_id} className={styles.undeterminedItem} data-testid="undetermined-item">
                          <span className="mono">{u.related_metric ?? '—'}</span>
                          <span className={styles.dim}>{u.reason ?? '—'}</span>
                          <span className={styles.dim}>缺失证据：{u.missing_evidence.join(', ') || '—'}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>
                <Section title="执行错误 Execution Errors" actions={<span className={styles.count}>{report.execution_errors.length} 条</span>}>
                  {report.execution_errors.length === 0 ? (
                    <div className={styles.metricEmpty}>无执行错误记录。</div>
                  ) : (
                    <ul className={styles.undeterminedList}>
                      {report.execution_errors.map((e, i) => (
                        <li key={`${e.error_code}-${i}`} className={styles.unlexecItem} data-testid="error-item">
                          <code>{e.error_code}</code>
                          <span className="mono">{e.metric ?? '(记录级)'}</span>
                          <span className={styles.dim}>{e.message}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>
              </>
            ) : null}

            {active === 'evidence' ? (
              <Section title="证据明细 Evidence" actions={<span className={styles.count}>{evidenceRows.length} 项</span>}>
                {evidenceRows.length === 0 ? (
                  <EmptyState
                    title="无持久化证据项"
                    description="本运行没有已诊断的诊断携带证据项；无法判定的记录不产生证据（契约未满足）。"
                    icon="circle"
                  />
                ) : (
                  <Panel padded={false}>
                    <table className={styles.evidenceTable} data-testid="evidence-table">
                      <thead>
                        <tr>
                          <th align="left">失败类型</th>
                          <th align="left">指标</th>
                          <th align="left">Type</th>
                          <th align="left">Source</th>
                          <th align="left">Locator</th>
                          <th align="left">Content</th>
                          <th align="right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {evidenceRows.map((row, i) => (
                          <tr key={`${row.diagnosisId}-${i}`} data-testid="evidence-row">
                            <td className="mono">{row.failureType}</td>
                            <td className="mono">{row.metric}</td>
                            <td><span className={styles.evType}>{row.item.type}</span></td>
                            <td className={styles.dim}>{row.item.source}</td>
                            <td><code className={styles.evLocator}>{row.item.locator ?? '—'}</code></td>
                            <td className={styles.evContent}>{preview(row.item.content)}</td>
                            <td align="right">
                              <button type="button" className={styles.openBtn} onClick={() => openDiagnosis(row.diagnosis)}>
                                诊断链 →
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Panel>
                )}
              </Section>
            ) : null}

            {active === 'configuration' ? (
              <>
                <Section title="Run 快照 Configuration">
                  <div className={styles.repro}>
                    {safeReproducibility(report.reproducibility).map((entry) => (
                      <div key={entry.key} className={styles.reproRow}>
                        <span className={styles.reproKey}>{entry.key}</span>
                        <span className={styles.reproVal}>{entry.value || '—'}</span>
                      </div>
                    ))}
                  </div>
                </Section>
                <EffectiveMetricsPanel meta={meta} />
              </>
            ) : null}
          </>
        )}
      </Tabs>

      <FailureAnalysisDrawer target={selected} onClose={() => setSelected(null)} />
    </>
  );
}

/** Run Snapshot 的指标级 effective config + overrides（全部来自 reproducibility_meta）。 */
function EffectiveMetricsPanel({ meta }: { meta: Record<string, unknown> }) {
  const effective = (meta.effective_metrics ?? null) as Record<string, { threshold: number | null; weight: number }> | null;
  const overrides = (meta.metric_overrides ?? null) as Record<string, Record<string, unknown>> | null;
  const overrideNames = overrides ? Object.keys(overrides) : [];
  return (
    <Section title="Run 级指标覆盖 Metric Overrides" actions={<span className={styles.count}>{overrideNames.length} 项</span>}>
      {!overrides || overrideNames.length === 0 ? (
        <div className={styles.metricEmpty}>无 Run 级覆盖 —— 本次运行完全按 Profile 配置执行。</div>
      ) : (
        <Panel padded={false}>
          <table className={styles.failureTable}>
            <thead>
              <tr><th align="left">指标</th><th align="left">覆盖字段</th><th align="left">覆盖值</th></tr>
            </thead>
            <tbody>
              {overrideNames.map((name) =>
                Object.entries(overrides[name]).map(([field, value]) => (
                  <tr key={`${name}-${field}`}>
                    <td className="mono">{name}</td>
                    <td className="mono">{field}</td>
                    <td><code>{value === null ? 'null（无 PASS/FAIL）' : String(value)}</code></td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </Panel>
      )}
      {effective && Object.keys(effective).length > 0 ? (
        <Panel padded={false}>
          <table className={styles.failureTable} data-testid="effective-metrics">
            <thead>
              <tr><th align="left">指标（实际执行）</th><th align="right">阈值</th><th align="right">权重</th></tr>
            </thead>
            <tbody>
              {Object.entries(effective).map(([name, cfg]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td align="right" className="mono">{cfg.threshold === null ? '—' : cfg.threshold}</td>
                  <td align="right" className="mono">{cfg.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}
    </Section>
  );
}

interface EvidenceRowView {
  diagnosisId: string;
  diagnosis: DiagnosisOut;
  failureType: string;
  metric: string;
  item: { type: string; source: string; locator: string | null; content: unknown };
}

/** 汇总各诊断的持久化证据项（只读展开，不做任何语义判断）。 */
function collectEvidenceRows(diagnoses: DiagnosisOut[]): EvidenceRowView[] {
  const rows: EvidenceRowView[] = [];
  for (const d of diagnoses) {
    for (const item of d.evidence ?? []) {
      rows.push({
        diagnosisId: d.id,
        diagnosis: d,
        failureType: d.failure_type ?? '—',
        metric: d.related_metric ?? '—',
        item,
      });
    }
  }
  return rows;
}

function preview(content: unknown): string {
  const text = typeof content === 'string' ? content : JSON.stringify(content) ?? '';
  return text.length > 80 ? `${text.slice(0, 80)}…` : text || '—';
}

function RunHeader({ summary, status, isPolling, progress, meta }: {
  summary: ReportResponse['summary'];
  status: RunStatus;
  isPolling: boolean;
  progress: { total: number; evaluated: number; errors: number; coverage: number | null } | null;
  meta: Record<string, unknown>;
}) {
  return (
    <Panel title="运行信息" accent="info">
      <div className={styles.runHeader}>
        <div className={styles.runMain}>
          <RunStatusBadge status={status} />
          {isPolling ? <em className={styles.running}>评估进行中…</em> : null}
          <span className="mono">{summary.run_id}</span>
        </div>
        <div className={styles.runMeta}>
          <span>数据集版本 <code>{String(meta.dataset_version ?? '—')}</code></span>
          <span>配置 <code>{meta.config_version ? String(meta.config_version).slice(0, 8) + '…' : '—'}</code></span>
          <span>创建 {formatTimestamp(summary.created_at)}</span>
          <span>耗时 {summary.started_at && summary.finished_at ? formatDuration(summary.started_at, summary.finished_at) : '—'}</span>
        </div>
        {isPolling && progress ? (
          <div className={styles.progress} data-testid="run-progress">
            进度 {progress.evaluated}/{progress.total} · 错误 {progress.errors} · 覆盖 {formatPercent(progress.coverage)}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function formatDuration(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (Number.isNaN(ms) || ms < 0) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function groupByCategory(metrics: MetricReportItem[]): Record<string, MetricReportItem[]> {
  const out: Record<string, MetricReportItem[]> = {};
  for (const m of metrics) {
    const cat = m.category ?? 'generation';
    (out[cat] ??= []).push(m);
  }
  return out;
}

function MetricGroup({ category, metrics }: { category: string; metrics: MetricReportItem[] }) {
  return (
    <Panel title={CATEGORY_LABEL[category] ?? category} padded={false}>
      {metrics.length === 0 ? (
        <div className={styles.metricEmpty}>{CATEGORY_EMPTY[category] ?? '无指标'}</div>
      ) : (
        <table className={styles.metricTable}>
          <thead>
            <tr><th align="left">指标</th><th align="left">分数</th><th align="left">阈值</th><th align="left">状态</th></tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.name} data-testid={`metric-${m.name}`}>
                <td className="mono">{m.name}</td>
                <td><ScoreValue score={m.score} /></td>
                <td>{m.threshold === null ? '—' : m.threshold}</td>
                <td><MetricStatusBadge status={m.status} passed={m.passed} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

interface FailureGroup { severity: string; failureType: string; count: number }
function aggregateFailures(failures: ReportResponse['failures']): FailureGroup[] {
  const map = new Map<string, FailureGroup>();
  for (const f of failures) {
    const sev = f.severity ?? 'INFO';
    const type = f.failure_type ?? 'unknown';
    const key = `${sev}|${type}`;
    const cur = map.get(key) ?? { severity: sev, failureType: type, count: 0 };
    cur.count += 1;
    map.set(key, cur);
  }
  return Array.from(map.values()).sort((a, b) => b.count - a.count);
}

function gateTone(status: string | null): 'pass' | 'error' | 'warning' | 'neutral' {
  if (status === 'PASS') return 'pass';
  if (status === 'FAIL') return 'error';
  if (status === 'NOT_EVALUABLE') return 'neutral';
  return 'neutral';
}
