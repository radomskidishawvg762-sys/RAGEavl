/**
 * DashboardPage — 项目工作区总览（Phase 3，产品级 UI 验证）。
 *
 * 项目作用域：数据跟随 ProjectProvider 的活动项目。仅消费现有后端 API
 * （evaluations / datasets / report / quality-gate）并映射呈现；前端绝不重算
 * overall_score / metric 聚合 / failure 分类 / quality-gate 语义。
 * null 渲染为「暂无有效分数」，永不显示为 0。
 *
 * 层级：工作区 → 质量概览 → 最近运行 → 指标概览 → 失败概览 → 质量趋势 → 最近运行。
 */

import { useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import type { MetricReportItem, ReportResponse, RunOut, RunStatus } from '../api/types';
import { TERMINAL_STATUSES } from '../api/types';
import { REASON_EXPLAIN, toRunView } from '../api/mappers';
import { useProject } from '../context/ProjectContext';
import { useDashboardData } from '../hooks/useDashboardData';
import { EChart } from '../components/charts/EChart';
import { Grid, Stack } from '../components/primitives/Layout';
import { Card, PageHeader, Panel, Section } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { ScoreValue } from '../components/ScoreValue';
import { MetricStatusBadge, QualityGateBadge, SeverityBadge, StatusBadge } from '../status/StatusBadge';
import { presentRun } from '../status/status';
import { formatPercent, formatTimestamp } from '../utils/format';
import styles from './DashboardPage.module.css';

const CATEGORY_ORDER = ['retrieval', 'generation', 'integrity'] as const;
const CATEGORY_LABEL: Record<string, string> = {
  retrieval: '检索',
  generation: '生成',
  integrity: '一致性',
};
const CATEGORY_EMPTY: Record<string, string> = {
  retrieval: '无检索指标',
  generation: '无生成指标',
  integrity: '无一致性指标',
};
const SEVERITY_RANK: Record<string, number> = { CRITICAL: 0, ERROR: 1, WARNING: 2, INFO: 3 };

const TREND_FALLBACK: Record<string, string> = {
  brand: '#4493f8',
  pass: '#3fb950',
  error: '#f85149',
  warning: '#d29922',
  text: '#8b949e',
  grid: '#21262d',
};

function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function DashboardPage() {
  const { activeProject, loading: projectLoading, error: projectError, reload: projectReload } = useProject();
  const navigate = useNavigate();
  const data = useDashboardData(activeProject?.id ?? null);
  const { state, error, evaluations, totalRuns, datasetCount, report, qualityGate, reload } = data;

  if (projectLoading) {
    return (
      <div className={styles.center}>
        <LoadingState label="正在加载项目…" />
      </div>
    );
  }

  // 项目列表加载失败——如实呈现，而非隐藏为“无项目”。
  if (projectError) {
    return (
      <div className={styles.center}>
        <ErrorState error={projectError} onRetry={projectReload} />
      </div>
    );
  }

  if (!activeProject) {
    return (
      <div className={styles.center}>
        <EmptyState
          title="请选择项目"
          description="从工作区选择器中选择项目以加载其评估概览。"
          icon="spark"
        />
      </div>
    );
  }

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <span>项目</span>
            <span>/</span>
            <span className="mono">{activeProject.name}</span>
          </>
        }
        title={activeProject.name}
        subtitle={`${activeProject.domain || 'general'}`}
        actions={
          <Stack direction="horizontal" gap="sm">
            <Link className={styles.linkBtn} to="/datasets">
              数据集（{datasetCount}）
            </Link>
            <Link className={styles.linkBtn} to="/evaluations/new">
              新建评估
            </Link>
          </Stack>
        }
      />

      {state === 'loading' ? (
        <div className={styles.center}>
          <LoadingState label="正在加载工作区…" />
        </div>
      ) : state === 'error' ? (
        <ErrorState error={error} onRetry={reload} />
      ) : (
        <Stack gap="xl">
          <QualitySummary
            report={report}
            qualityGateStatus={qualityGate?.status ?? null}
            qualityGateReasons={qualityGate?.reasons ?? []}
            totalRuns={totalRuns}
          />

          {state === 'empty' ? (
            <Panel title="工作区">
              <EmptyState
                title="暂无评估"
                description="该项目尚无任何评估运行。发起一次评估以填充工作区。"
                icon="arrow-right"
                action={
                  <Link className={styles.linkBtn} to="/evaluations/new">
                    新建评估
                  </Link>
                }
              />
            </Panel>
          ) : (
            <>
              <LatestRun evaluations={evaluations} />
              <MetricsOverview report={report} runId={data.latestTerminalRunId} />
              <Grid cols={2} gap="xl">
                <FailureOverview
                  report={report}
                  runId={data.latestTerminalRunId}
                  onOpen={navigate}
                />
                <TrendPanel evaluations={evaluations} />
              </Grid>
              <RecentRuns evaluations={evaluations} />
              <RecentDiagnoses report={report} runId={data.latestTerminalRunId} />
            </>
          )}
        </Stack>
      )}
    </>
  );
}

// ---------------- 最近诊断（来自最近一次完成运行的报告，纯渲染） ----------------

function RecentDiagnoses({ report, runId }: { report: ReportResponse | null; runId: string | null }) {
  const failures = (report?.failures ?? []).slice(0, 8);
  const undetermined = (report?.undetermined ?? []).slice(0, 4);
  return (
    <Section
      title="最近诊断 Recent Diagnoses"
      actions={
        runId ? (
          <Link className={styles.linkBtn} to={`/evaluations/${runId}`}>
            查看运行详情
          </Link>
        ) : null
      }
    >
      {failures.length === 0 && undetermined.length === 0 ? (
        <EmptyState
          title="暂无诊断记录"
          description="最近一次完成运行没有失败或无法判定记录。"
          icon="check"
        />
      ) : (
        <Panel padded={false}>
          <table className={styles.runTable} data-testid="recent-diagnoses">
            <thead>
              <tr>
                <th align="left">严重度</th>
                <th align="left">失败类型 / 状态</th>
                <th align="left">指标</th>
                <th align="left">说明</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((f, i) => (
                <tr key={`f-${f.diagnosis_id}-${i}`}>
                  <td><SeverityBadge severity={f.severity ?? 'INFO'} /></td>
                  <td className="mono">{f.failure_type ?? '—'}</td>
                  <td className="mono">{f.related_metric ?? '—'}</td>
                  <td className={styles.diagQuestion}>{f.root_cause ?? f.question ?? '—'}</td>
                </tr>
              ))}
              {undetermined.map((u) => (
                <tr key={`u-${u.diagnosis_id}`}>
                  <td><SeverityBadge severity="INFO" /></td>
                  <td className={styles.dim}>无法判定</td>
                  <td className="mono">{u.related_metric ?? '—'}</td>
                  <td className={styles.diagQuestion}>{u.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </Section>
  );
}

// ---------------- 质量概览 ----------------

function QualitySummary({
  report,
  qualityGateStatus,
  qualityGateReasons,
  totalRuns,
}: {
  report: ReportResponse | null;
  qualityGateStatus: string | null;
  qualityGateReasons: string[];
  totalRuns: number;
}) {
  const summary = report?.summary ?? null;
  return (
    <Section title="质量概览">
      <Grid cols={4} gap="lg">
        <Card label="总体质量" hint="最近一次完成运行">
          <ScoreValue score={summary?.overall_score ?? null} />
        </Card>
        <Card label="评估覆盖率" value={formatPercent(summary?.evaluation_coverage ?? null)} />
        <Card label="记录错误" value={String(summary?.error_records ?? '—')} />
        <Card label="质量门禁" accent={qualityGateTone(qualityGateStatus)}>
          {qualityGateStatus ? (
            <QualityGateBadge status={qualityGateStatus} />
          ) : (
            <ScoreValue score={null} />
          )}
        </Card>
      </Grid>
      {qualityGateReasons.length > 0 ? (
        <div className={styles.gateReasons}>
          {qualityGateReasons.slice(0, 2).map((r) => (
            // 展示中文说明，而不是原因码本身：入口页面上挂着一个黄色的
            // `QUALITY_GATE_NOT_CONFIGURED` 对用户毫无意义。未收录的码原样显示，不猜。
            <code key={r} className={styles.reason}>{REASON_EXPLAIN[r] ?? r}</code>
          ))}
        </div>
      ) : null}
      <div className={styles.workspaceStats}>
        <span>{totalRuns} 次运行</span>
      </div>
    </Section>
  );
}

function qualityGateTone(status: string | null): 'pass' | 'error' | 'warning' | 'neutral' {
  if (status === 'PASS') return 'pass';
  if (status === 'FAIL') return 'error';
  if (status === 'NOT_EVALUABLE') return 'neutral';
  return 'neutral';
}

// ---------------- 最近运行 ----------------

function LatestRun({ evaluations }: { evaluations: RunOut[] }) {
  const latest = evaluations[0];
  if (!latest) return null;
  const run = toRunView(latest);
  return (
    <Panel
      title="最近运行"
      accent="info"
      actions={
        <Link className={styles.linkBtn} to={`/evaluations/${run.runId}`}>
          查看详情 →
        </Link>
      }
    >
      <div className={styles.latestRun}>
        <div className={styles.latestMain}>
          <span className="mono">{run.runId}</span>
          <StatusBadge presentation={runStatusPresentation(run.status)} />
        </div>
        <div className={styles.latestMeta}>
          <span>数据集 {run.datasetId.slice(0, 8)}…</span>
          <span>总体 <ScoreValue score={run.overallScore} /></span>
          <span>覆盖率 {formatPercent(run.evaluationCoverage)}</span>
          <span>{formatTimestamp(run.createdAt)}</span>
        </div>
      </div>
    </Panel>
  );
}

function runStatusPresentation(status: string) {
  return presentRun(status as RunStatus);
}

// ---------------- 指标概览 ----------------

function MetricsOverview({ report, runId }: { report: ReportResponse | null; runId: string | null }) {
  if (!report) {
    return (
      <Section title="指标概览">
        <EmptyState title="暂无指标概览" description="尚无完成运行可供汇总。" icon="circle" />
      </Section>
    );
  }
  const byCategory = groupByCategory(report.metrics);
  return (
    <Section title="指标概览" actions={runId ? <MetricNav runId={runId} /> : null}>
      <Grid cols={3} gap="lg">
        {CATEGORY_ORDER.map((cat) => (
          <MetricGroup key={cat} category={cat} metrics={byCategory[cat] ?? []} />
        ))}
      </Grid>
    </Section>
  );
}

function MetricNav({ runId }: { runId: string }) {
  return (
    <Link className={styles.linkBtn} to={`/evaluations/${runId}`}>
      查看运行详情 →
    </Link>
  );
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
            <tr>
              <th align="left">指标</th>
              <th align="left">分数</th>
              <th align="left">阈值</th>
              <th align="left">状态</th>
            </tr>
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

// ---------------- 失败概览 ----------------

function FailureOverview({
  report,
  runId,
  onOpen,
}: {
  report: ReportResponse | null;
  runId: string | null;
  onOpen: (to: string) => void;
}) {
  const rows = useMemo(() => aggregateFailures(report?.failures ?? []), [report]);
  return (
    <Panel
      title="失败概览"
      accent="warning"
      actions={runId ? <Link className={styles.linkBtn} to={`/evaluations/${runId}`}>查看详情 →</Link> : null}
    >
      {rows.length === 0 ? (
        <div className={styles.metricEmpty}>最近一次运行未记录失败。</div>
      ) : (
        <table className={styles.failureTable}>
          <thead>
            <tr>
              <th align="left">严重度</th>
              <th align="left">失败类型</th>
              <th align="right">数量</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={`${row.severity}-${row.failureType}`}
                className={styles.clickable}
                onClick={() => runId && onOpen(`/evaluations/${runId}`)}
                data-testid="failure-row"
              >
                <td><SeverityBadge severity={row.severity} /></td>
                <td className="mono">{row.failureType}</td>
                <td align="right" className={styles.count}>{row.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

interface FailureRow {
  severity: string;
  failureType: string;
  count: number;
}

function aggregateFailures(failures: ReportResponse['failures']): FailureRow[] {
  const map = new Map<string, FailureRow>();
  for (const f of failures) {
    const sev = f.severity ?? 'INFO';
    const type = f.failure_type ?? 'unknown';
    const key = `${sev}|${type}`;
    const cur = map.get(key) ?? { severity: sev, failureType: type, count: 0 };
    cur.count += 1;
    map.set(key, cur);
  }
  return Array.from(map.values()).sort((a, b) => {
    const rank = (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4);
    return rank !== 0 ? rank : b.count - a.count;
  });
}

// ---------------- 质量趋势 ----------------

function TrendPanel({ evaluations }: { evaluations: RunOut[] }) {
  const { option, points } = useMemo(() => buildTrend(evaluations), [evaluations]);
  return (
    <Panel title="质量趋势" accent="info">
      {points >= 2 ? (
        <EChart option={option} height={200} />
      ) : (
        <div className={styles.trendEmpty}>
          <EmptyState
            title="暂无足够的历史运行"
            description="至少需要 2 次已完成评估且记录总体质量，才能生成趋势。"
            icon="minus"
            action={
              <Link className={styles.linkBtn} to="/evaluations">
                前往评估列表
              </Link>
            }
          />
        </div>
      )}
    </Panel>
  );
}

 function buildTrend(evaluations: RunOut[]): { option: import('echarts/core').EChartsCoreOption; points: number } {
  const withScore = evaluations
    .filter(
      (r): r is RunOut & { overall_score: number } =>
        TERMINAL_STATUSES.includes(r.status) && r.overall_score != null,
    )
    .slice()
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  const brand = token('--color-brand', TREND_FALLBACK.brand);
  const pass = token('--color-pass', TREND_FALLBACK.pass);
  const grid = token('--color-border', TREND_FALLBACK.grid);
  const text = token('--color-text-subtle', TREND_FALLBACK.text);
  const option: import('echarts/core').EChartsCoreOption = {
    grid: { left: 44, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: withScore.map((r) => formatTimestamp(r.created_at)),
      axisLine: { lineStyle: { color: grid } },
      axisLabel: { color: text, fontSize: 10, formatter: (v: string) => v.slice(5, 16) },
    },
    yAxis: {
      type: 'value',
      // 数据驱动坐标轴（不预设分数范围假设；null 分数已被过滤）
      scale: true,
      splitLine: { lineStyle: { color: grid } },
      axisLabel: { color: text, fontSize: 10 },
    },
    series: [
      {
        type: 'line',
        smooth: true,
        symbolSize: 6,
        data: withScore.map((r) => r.overall_score),
        lineStyle: { color: brand, width: 2 },
        itemStyle: { color: pass },
        areaStyle: { color: 'transparent' },
      },
    ],
  };
  return { option, points: withScore.length };
}

// ---------------- 最近运行 ----------------

function RecentRuns({ evaluations }: { evaluations: RunOut[] }) {
  return (
    <Section title="最近运行">
      {evaluations.length === 0 ? (
        <EmptyState title="暂无评估" description="该项目暂无评估。" icon="circle" />
      ) : (
        <table className={styles.runTable}>
          <thead>
            <tr>
              <th align="left">运行</th>
              <th align="left">数据集</th>
              <th align="left">状态</th>
              <th align="left">总体</th>
              <th align="left">覆盖率</th>
              <th align="left">创建时间</th>
            </tr>
          </thead>
          <tbody>
            {evaluations.slice(0, 10).map((r) => {
              const run = toRunView(r);
              return (
                <tr key={run.runId}>
                  <td>
                    <Link to={`/evaluations/${run.runId}`} className={styles.runLink}>
                      {run.runId.slice(0, 8)}…
                    </Link>
                  </td>
                  <td>
                    <Link to={`/datasets/${run.datasetId}`} className={styles.runLink}>
                      {run.datasetId.slice(0, 8)}…
                    </Link>
                  </td>
                  <td>
                    <StatusBadge presentation={runStatusPresentation(run.status)} />
                  </td>
                  <td><ScoreValue score={run.overallScore} /></td>
                  <td>{formatPercent(run.evaluationCoverage)}</td>
                  <td>{formatTimestamp(run.createdAt)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Section>
  );
}
