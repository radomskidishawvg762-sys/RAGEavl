/**
 * ProjectsPage — Project 一级工作区（§四）。真实能力：
 *  列表 GET /api/projects · 创建 POST /api/projects（G1）·
 *  摘要 GET /api/projects/{id}（G5：dataset/run 计数、latest run、latest gate）。
 * 前端不计算任何统计；创建失败（400/409）如实展示后端错误。
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, endpoints } from '../api/client';
import { toProjectSummaryView } from '../api/mappers';
import type { CreateProjectRequest, ProjectListResponse, ProjectOut } from '../api/types';
import type { ProjectSummaryView } from '../api/types';
import { PageHeader, Panel, Section, Tag } from '../components/primitives/Surfaces';
import { BilingualLabel } from '../components/primitives/Bilingual';
import { Grid } from '../components/primitives/Layout';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { humanizeError } from '../api/client';
import { RunStatusBadge, QualityGateBadge } from '../status/StatusBadge';
import { ScoreValue } from '../components/ScoreValue';
import { formatPercent, formatTimestamp } from '../utils/format';
import { useProject } from '../context/ProjectContext';
import styles from './ProjectsPage.module.css';

export function ProjectsPage() {
  const navigate = useNavigate();
  const { activeProjectId, setActiveProjectId, reload: reloadProjects } = useProject();
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<unknown>(null);
  const [projects, setProjects] = useState<ProjectOut[]>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: '', domain: 'general' });
  const [summaryOf, setSummaryOf] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState('loading');
    try {
      const page = await api.get<ProjectListResponse>(endpoints.projects(1, 100));
      setProjects(page.items);
      setError(null);
      setState('ready');
    } catch (e) {
      setError(e);
      setState('error');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = useCallback(async () => {
    setCreateError(null);
    const body: CreateProjectRequest = { name: form.name.trim(), domain: form.domain.trim() || 'general' };
    if (!body.name) {
      setCreateError('请输入项目名称。');
      return;
    }
    setCreating(true);
    try {
      const created = await api.post<ProjectOut>(endpoints.projectCreate(), body);
      setForm({ name: '', domain: 'general' });
      setCreating(false);
      await load();
      await reloadProjects();
      setSummaryOf(created.id);
    } catch (e) {
      setCreating(false);
      setCreateError(humanizeError(e));
    }
  }, [form, load, reloadProjects]);

  if (state === 'loading') return <LoadingState label="正在加载项目…" />;
  if (state === 'error') return <ErrorState error={error} onRetry={load} />;

  return (
    <>
      <PageHeader
         title={<BilingualLabel zh="项目" en="Projects" />}
        subtitle="项目是整个工作区的一级上下文：数据集、评估配置与运行都归属项目。"
        actions={
          <button type="button" className={styles.primaryBtn} onClick={() => setSummaryOf(null)} data-testid="projects-refresh">
            刷新
          </button>
        }
      />

       <Panel title={<BilingualLabel zh="新建项目" en="New Project" />} accent="info">
        <div className={styles.createRow}>
          <input
            className={styles.input}
            placeholder="项目名称（必填）"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            data-testid="project-name-input"
          />
          <input
            className={`${styles.input} ${styles.domainInput}`}
            placeholder="domain（如 general）"
            value={form.domain}
            onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
            data-testid="project-domain-input"
          />
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={creating}
            onClick={() => void create()}
            data-testid="project-create-submit"
          >
            {creating ? '创建中…' : '新建项目'}
          </button>
        </div>
        <p className={styles.hint}>
          domain 需为部署环境中已配置的领域标识（默认 general）；重复名称将被拒绝。
        </p>
        {createError ? (
          <div className={styles.createError} role="alert" data-testid="project-create-error">
            {createError}
          </div>
        ) : null}
      </Panel>

      <Section title={<BilingualLabel zh={`项目列表（${projects.length}）`} en="Projects" />}>
        {projects.length === 0 ? (
          <EmptyState
            title="暂无项目"
            description="先创建一个项目，再导入数据集并启动评估。"
            icon="circle"
          />
        ) : (
          <div className={styles.list}>
            {projects.map((p) => (
              <ProjectRow
                key={p.id}
                project={p}
                active={p.id === activeProjectId}
                expanded={summaryOf === p.id}
                onToggle={() => setSummaryOf((cur) => (cur === p.id ? null : p.id))}
                onEnter={async () => {
                  setActiveProjectId(p.id);
                  navigate('/');
                }}
              />
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

function ProjectRow({
  project,
  active,
  expanded,
  onToggle,
  onEnter,
}: {
  project: ProjectOut;
  active: boolean;
  expanded: boolean;
  onToggle: () => void;
  onEnter: () => void;
}) {
  const [summary, setSummary] = useState<ProjectSummaryView | null>(null);
  const [sumState, setSumState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [sumError, setSumError] = useState<unknown>(null);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    setSumState('loading');
    api
      .get<ProjectSummaryResponseRaw>(endpoints.project(project.id))
      .then((raw) => {
        if (cancelled) return;
        setSummary(toProjectSummaryView(raw));
        setSumState('ready');
      })
      .catch((e) => {
        if (cancelled) return;
        setSumError(e);
        setSumState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, project.id]);

  return (
    <Panel className={styles.row} padded={false}>
      <div className={styles.rowMain}>
        <div className={styles.rowTitle}>
          <span className={styles.name}>{project.name}</span>
          {active ? <Tag tone="info">当前项目</Tag> : null}
          <Tag tone={project.status === 'active' ? 'pass' : 'neutral'}>{projectStatusLabel(project.status)}</Tag>
          <Tag>{project.domain}</Tag>
        </div>
        <div className={styles.rowMeta}>
          <span className="mono">{project.id.slice(0, 8)}…</span>
          <span>创建 {formatTimestamp(project.created_at)}</span>
        </div>
        <div className={styles.rowActions}>
          <button type="button" className={styles.linkBtn} onClick={onEnter} data-testid={`enter-${project.name}`}>
            进入工作区
          </button>
          <button type="button" className={styles.linkBtn} onClick={onToggle} data-testid={`summary-${project.name}`}>
            {expanded ? '收起摘要' : '查看摘要'}
          </button>
        </div>
      </div>
      {expanded ? (
        <div className={styles.summaryBody} data-testid="project-summary">
          {sumState === 'loading' ? <LoadingState label="正在加载摘要…" /> : null}
          {sumState === 'error' ? <ErrorState error={sumError} onRetry={onToggle} /> : null}
          {sumState === 'ready' && summary ? (
            <>
              <Grid cols={4} gap="md">
                <div className={styles.stat}><span className={styles.statLabel}>数据集</span><span className={styles.statValue}>{summary.datasetCount}</span></div>
                <div className={styles.stat}><span className={styles.statLabel}>评估运行</span><span className={styles.statValue}>{summary.runCount}</span></div>
                <div className={styles.stat}>
                  <span className={styles.statLabel}>最近总体质量</span>
                  <span className={styles.statValue}><ScoreValue score={summary.latestRun?.overallScore ?? null} /></span>
                </div>
                <div className={styles.stat}>
                  <span className={styles.statLabel}>最近质量门禁</span>
                  <span className={styles.statValue}>
                    {summary.latestQualityGate ? (
                      <QualityGateBadge status={summary.latestQualityGate.status} />
                    ) : (
                      <span className={styles.dim}>暂无（运行未完成或未配置门禁）</span>
                    )}
                  </span>
                </div>
              </Grid>
              {summary.latestRun ? (
                <div className={styles.latestRun} data-testid="project-latest-run">
                  <RunStatusBadge status={summary.latestRun.status} />
                  <span className="mono">{summary.latestRun.runId.slice(0, 8)}…</span>
                  <span>{summary.latestRun.datasetName ?? '—'} <code>{summary.latestRun.datasetVersion ?? '—'}</code></span>
                  <span>覆盖 {formatPercent(summary.latestRun.evaluationCoverage)}</span>
                  <span className={styles.dim}>{formatTimestamp(summary.latestRun.createdAt)}</span>
                </div>
              ) : (
                <div className={styles.dim}>该项目还没有评估运行。</div>
              )}
            </>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}

/** raw API mirror for the summary fetch (mapped via toProjectSummaryView). */
type ProjectSummaryResponseRaw = Parameters<typeof toProjectSummaryView>[0];

/** 项目状态 → 中文（后端取值 active | archived）。未收录的值原样显示，
 *  后端加了新状态时应该显眼，而不是变成空白。 */
function projectStatusLabel(status: string): string {
  const known: Record<string, string> = {
    active: '启用',
    archived: '已归档',
  };
  return known[status] ?? status;
}
