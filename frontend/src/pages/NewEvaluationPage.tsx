/**
 * NewEvaluationPage — 新建评估向导（5 步，真实后端）。
 * Step 1 数据集 → Step 2 评估配置（Profile）→ Step 3 指标（Run Configuration，
 * 走 G4 metric_overrides：仅本次运行生效，Profile 只读）→ Step 4 RAG 输入 →
 * Step 5 Review 全景 → 202 → /evaluations/{run_id}。
 *
 * Step 3 编辑语义（G4）：字段未修改 = 保留 Profile 值（请求中省略该字段）；
 * 阈值清空 = null（本次运行无 PASS/FAIL）；weight ≥ 0。
 * Step 4 RAG 输入：HTTP Endpoint（生产路径）与 Golden Metadata Replay
 * （仅测试回放）由 Profile/部署配置决定 —— Run 级 RAG 配置属 Backend Gap，
 * 如实标注，不提供不生效的输入框。
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, endpoints, humanizeError } from '../api/client';
import { DIRECTION_LABEL } from '../api/mappers';
import type {
  ConfigDetailResponse,
  ConfigSummaryView,
  CreateRunResponse,
  DatasetOut,
  MetricCatalogView,
  ProfileView,
  SaveConfigResponse,
} from '../api/types';
import { useProject } from '../context/ProjectContext';
import { useDatasets } from '../hooks/useDatasets';
import { useEvaluationCatalog } from '../hooks/useEvaluationCatalog';
import { useProjectConfigs } from '../hooks/useProjectConfigs';
import { PageHeader, Panel, Tag } from '../components/primitives/Surfaces';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import styles from './NewEvaluationPage.module.css';

const STEPS: Array<[number, string]> = [
  [1, '选择数据集'],
  [2, '评估配置'],
  [3, '指标覆盖'],
  [4, 'RAG 输入'],
  [5, '确认并启动'],
];

/** 运行级指标覆盖：字段缺省 = 保留 Profile 值；threshold null = 本次运行无 PASS/FAIL。 */
interface MetricOverrideState {
  enabled?: boolean;
  threshold?: number | null;
  weight?: number;
}

type Overrides = Record<string, MetricOverrideState>;

export function NewEvaluationPage() {
  const navigate = useNavigate();
  const { activeProject, loading: projectLoading, error: projectError } = useProject();
  const datasets = useDatasets(activeProject?.id ?? null);
  const catalog = useEvaluationCatalog();

  const [step, setStep] = useState(1);
  const [dataset, setDataset] = useState<DatasetOut | null>(null);
  const [profile, setProfile] = useState<ProfileView | null>(null);
  // Configuration Lifecycle v1 (Phase C): selecting a stored imported version
  // uses its OWN config_id directly — no pointer re-save, no second config source.
  const [storedConfigId, setStoredConfigId] = useState<string | null>(null);
  const [storedConfigVersion, setStoredConfigVersion] = useState<string | null>(null);
  const storedConfigs = useProjectConfigs(activeProject?.id ?? null);
  const [overrides, setOverrides] = useState<Overrides>({});
  const [submitting, setSubmitting] = useState(false);
  const [launchError, setLaunchError] = useState<unknown>(null);

  const selectableDatasets = useMemo(() => datasets.items, [datasets.items]);

  // Profile 切换时重置 Run 级覆盖（覆盖只属于本次 Run 配置）
  useEffect(() => {
    setOverrides({});
  }, [profile?.name, storedConfigId, storedConfigVersion]);

  const overrideCount = useMemo(
    () => Object.values(overrides).filter((o) => Object.keys(o).length > 0).length,
    [overrides],
  );
  const invalidWeight = useMemo(
    () => Object.entries(overrides).some(([, o]) => o.weight !== undefined && o.weight < 0),
    [overrides],
  );

  if (projectLoading) return <LoadingState label="正在加载项目…" />;
  if (projectError) return <ErrorState error={projectError} />;
  if (!activeProject) {
    return <EmptyState title="请选择项目" description="新建评估需要指定项目。请先选择项目。" icon="circle" />;
  }

  const canStart = Boolean(dataset) && Boolean(profile) && !dataset?.is_locked && !invalidWeight;

  async function handleStart() {
    if (!dataset || !profile || !activeProject) return;
    setSubmitting(true);
    setLaunchError(null);
    try {
      let configId: string;
      if (storedConfigId) {
        configId = storedConfigId; // stored imported version: immutable row, reused as-is
      } else {
        const saved = await api.post<SaveConfigResponse>(endpoints.saveConfig(activeProject.id), {
          name: profile.name,
          domain: profile.domain,
          profile: profile.name,
        });
        configId = saved.config_id;
      }
      const body: Record<string, unknown> = {
        project_id: activeProject.id,
        dataset_id: dataset.id,
        config_id: configId,
      };
      // G4: Run 级指标覆盖 —— 只提交用户实际修改过的字段（字段缺省 = 保留 Profile 值）
      const metricOverrides = buildOverrides(overrides);
      if (Object.keys(metricOverrides).length > 0) {
        body.metric_overrides = metricOverrides;
      }
      const created = await api.post<CreateRunResponse>(endpoints.runCreate(), body);
      navigate(`/evaluations/${created.run_id}`);
    } catch (e) {
      setLaunchError(e);
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageHeader
        breadcrumb={<><span>评估</span><span>/</span><span>新建评估</span></>}
        title="新建评估"
        subtitle={`项目 ${activeProject.name} · 五步向导`}
      />

      <StepBar current={step} />

      <div className={styles.content}>
        {/* Step 1: 数据集 */}
        {step === 1 && (
          <Panel title="选择数据集">
            {datasets.loading ? (
              <LoadingState label="正在加载数据集…" />
            ) : datasets.error ? (
              <ErrorState error={datasets.error} onRetry={datasets.reload} />
            ) : selectableDatasets.length === 0 ? (
              <EmptyState
                title="暂无可选数据集"
                description="该项目还没有数据集。请先导入数据集。"
                icon="circle"
                action={<Link className={styles.btn} to="/datasets/import">导入数据集</Link>}
              />
            ) : (
              <div className={styles.datasetList}>
                {selectableDatasets.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    className={[styles.datasetCard, dataset?.id === d.id ? styles.selected : ''].join(' ')}
                    onClick={() => setDataset(d)}
                    data-testid="select-dataset"
                  >
                    <div className={styles.datasetName}>{d.name} <span className="mono">{d.version}</span></div>
                    <div className={styles.datasetMeta}>
                      <span>记录数 {d.record_count}</span>
                      <Tag tone={d.validation_status === 'valid' ? 'pass' : 'error'}>
                        {d.validation_status === 'valid' ? '有效' : '无效'}
                      </Tag>
                      {d.is_locked ? <Tag tone="warning">已锁定</Tag> : null}
                    </div>
                  </button>
                ))}
              </div>
            )}
            <div className={styles.actions}>
              <button type="button" className={styles.btn} disabled={!dataset} onClick={() => setStep(2)}>
                下一步
              </button>
            </div>
          </Panel>
        )}

        {/* Step 2: 评估配置（Profile + 已导入的存储版本） */}
        {step === 2 && (
          <Panel title="评估配置">
            <StoredVersionPicker
              configs={storedConfigs}
              selectedId={storedConfigId}
              onSelect={async (row) => {
                try {
                  const d = await api.get<ConfigDetailResponse>(endpoints.configDetail(row.configId));
                  setStoredConfigId(d.config_id);
                  setStoredConfigVersion(d.config_version);
                  setProfile(storedDetailToProfileView(d));
                } catch (e) {
                  setLaunchError(e);
                }
              }}
            />
            <StepTwoCatalog
              catalog={catalog}
              profile={profile}
              disabled={storedConfigId !== null}
              onSelect={(p) => {
                setStoredConfigId(null); // YAML Profile 走原指针保存链路
                setStoredConfigVersion(null);
                setProfile(p);
              }}
            />
            <div className={styles.actions}>
              <button type="button" className={styles.btn} onClick={() => setStep(1)}>上一步</button>
              <button type="button" className={styles.btn} disabled={!profile} onClick={() => setStep(3)}>
                下一步
              </button>
            </div>
          </Panel>
        )}

        {/* Step 3: 指标（本次运行覆盖） */}
        {step === 3 && profile && (
          <Panel title="指标覆盖">
            <div className={styles.gapHint} data-testid="metric-config-note">
              这是本次运行的指标配置：仅对本次运行生效，不修改原配置 Profile（只读）。
              未修改的字段保留 Profile 值；阈值清空 = 本次运行无 PASS/FAIL 判定。
            </div>
            <MetricEditor
              profile={profile}
              metrics={catalog.metrics}
              overrides={overrides}
              onChange={setOverrides}
            />
            {invalidWeight ? (
              <div className={styles.gapHint} data-testid="metric-invalid-hint">
                权重不能为负数，请修正后再继续。
              </div>
            ) : null}
            <div className={styles.actions}>
              <button type="button" className={styles.btn} onClick={() => setStep(2)}>上一步</button>
              <button type="button" className={styles.btn} disabled={invalidWeight} onClick={() => setStep(4)}>
                下一步{overrideCount > 0 ? `（${overrideCount} 项覆盖）` : ''}
              </button>
            </div>
          </Panel>
        )}

        {/* Step 4: RAG 输入 */}
        {step === 4 && profile && (
          <Panel title="RAG 输入">
            <RagReview profile={profile} />
            <div className={styles.actions}>
              <button type="button" className={styles.btn} onClick={() => setStep(3)}>上一步</button>
              <button type="button" className={styles.btn} onClick={() => setStep(5)}>下一步</button>
            </div>
          </Panel>
        )}

        {/* Step 5: 确认并启动（Review 全景） */}
        {step === 5 && dataset && profile && (
          <Panel title="确认并启动">
            <div className={styles.review}>
              <ReviewRow label="项目" value={activeProject.name} />
              <ReviewRow
                label="数据集"
                value={`${dataset.name} ${dataset.version} · ${dataset.record_count} 条 · ${dataset.validation_status === 'valid' ? '有效' : '无效'}${dataset.is_locked ? ' · 已锁定' : ''}`}
              />
              <ReviewRow label="评估配置" value={`${profile.name}（${profile.version || '—'} · ${profile.domain}）`} />
              <ReviewRow
                label="配置指纹"
                value={storedConfigVersion
                  ? <code className="mono" title={storedConfigVersion}>{storedConfigVersion.slice(0, 16)}…</code>
                  : '运行创建时计算（内容哈希，写入运行快照）'}
              />
              <ReviewRow
                label="执行流水线"
                value={pipelineReviewText(profile)}
              />
              <ReviewRow
                label="指标"
                value={metricsReviewText(profile, overrides)}
              />
              <ReviewRow
                label="质量门禁"
                value={profile.qualityGate ? JSON.stringify(profile.qualityGate) : '未配置'}
                muted={!profile.qualityGate}
              />
              <ReviewRow label="RAG 输入" value={ragInputLabel(profile)} />
              <ReviewRow
                label="诊断"
                value={diagnosisReviewText(profile)}
              />
              <ReviewRow label="Judge（公共字段）" value={judgeReviewText(profile)} />
            </div>

            {launchError ? (
              <div className={styles.gapHint} data-testid="launch-error">{humanizeError(launchError)}</div>
            ) : null}

            {dataset.is_locked ? (
              <div className={styles.gapHint} data-testid="locked-hint">
                该数据集已被某个评估运行锁定（版本不可变）。请创建新版本后再启动评估。
              </div>
            ) : null}

            <div className={styles.actions}>
              <button type="button" className={styles.btn} onClick={() => setStep(4)}>上一步</button>
              <button
                type="button"
                className={[styles.btn, styles.primary].join(' ')}
                data-testid="start-evaluation"
                disabled={!canStart || submitting}
                onClick={handleStart}
              >
                {submitting ? '启动中…' : '开始评估'}
              </button>
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}

/** 收集「用户实际修改过的字段」——字段缺省 = 保留 Profile 值（G4 语义）。 */
function buildOverrides(overrides: Overrides): Record<string, Record<string, boolean | number | null>> {
  const out: Record<string, Record<string, boolean | number | null>> = {};
  for (const [name, ov] of Object.entries(overrides)) {
    const fields: Record<string, boolean | number | null> = {};
    if (ov.enabled !== undefined) fields.enabled = ov.enabled;
    if (ov.threshold !== undefined) fields.threshold = ov.threshold;
    if (ov.weight !== undefined) fields.weight = ov.weight;
    if (Object.keys(fields).length > 0) out[name] = fields;
  }
  return out;
}

function metricsReviewText(profile: ProfileView, overrides: Overrides): string {
  const enabled = profile.metrics.filter((m) => effectiveEnabled(profile, m.name, overrides));
  const parts = enabled.map((m) => {
    const ov = overrides[m.name];
    const threshold = ov?.threshold !== undefined ? ov.threshold : m.threshold;
    const weight = ov?.weight !== undefined ? ov.weight : m.weight;
    const marked = ov && Object.keys(ov).length > 0 ? ' *' : '';
    return `${m.name}${marked}(${threshold === null ? '无阈值' : `阈值${threshold}`}/权重${weight})`;
  });
  const disabled = profile.metrics.length - enabled.length;
  const suffix = disabled > 0 ? ` · 停用 ${disabled} 项` : '';
  const note = Object.keys(overrides).length > 0 ? ' · * = 本次运行覆盖' : '';
  return `${enabled.length} 项启用（共 ${profile.metrics.length} 项）${suffix}${note}：${parts.join('、') || '—'}`;
}

function effectiveEnabled(profile: ProfileView, name: string, overrides: Overrides): boolean {
  const ov = overrides[name];
  if (ov?.enabled !== undefined) return ov.enabled;
  return profile.metrics.find((m) => m.name === name)?.enabled ?? true;
}

function ragInputLabel(profile: ProfileView | null): string {
  if (!profile?.ragInput?.url) return '金标回放（测试回放，非生产 RAG）';
  const mode = profile.ragInput.mode === 'http' || profile.ragInput.url ? 'HTTP' : '金标回放';
  return `${mode} · ${profile.ragInput.url}（超时 ${profile.ragInput.timeout ?? '—'}s / 重试 ${profile.ragInput.retry ?? '—'}）`;
}

function pipelineReviewText(profile: ProfileView | null): string {
  const pipe = profile?.pipeline as { engines?: unknown; diagnosis?: { enabled?: boolean } } | null;
  const engines = Array.isArray(pipe?.engines) && pipe.engines.length
    ? pipe.engines.join('、')
    : '由启用指标推导';
  return `引擎：${engines}`;
}

function diagnosisReviewText(profile: ProfileView | null): string {
  const pipe = profile?.pipeline as { diagnosis?: { enabled?: boolean } } | null;
  const diag = pipe?.diagnosis?.enabled;
  const legacy = (profile as unknown as { diagnosisEnabled?: boolean } | null)?.diagnosisEnabled;
  const enabled = diag ?? legacy ?? true; // 与后端 resolve_effective_pipeline 默认一致
  const severities = Object.keys(profile?.severityMapping ?? {}).length;
  return `${enabled ? '执行' : '跳过（配置中已关闭诊断）'} · 严重度映射 ${severities} 类`;
}

function judgeReviewText(profile: ProfileView | null): string {
  const j = profile?.judge;
  if (!j) return '未配置（依赖 Judge 的指标会标记为未配置，不会静默跳过）';
  const parts = [
    `provider ${j.provider ?? '—'}`,
    `model ${j.model ?? '—'}`,
    `temperature ${j.temperature ?? '—'}`,
    `max_tokens ${j.max_tokens ?? '—'}`,
    `timeout ${j.timeout ?? '—'}`,
    `retry ${j.retry ?? '—'}`,
  ];
  return parts.join(' · ');
}

/** stored imported 版本 → ProfileView（仅映射展示字段，不派生任何配置值）。 */
function storedDetailToProfileView(d: ConfigDetailResponse): ProfileView {
  return {
    name: d.profile,
    version: d.version ?? '',
    domain: d.domain,
    metrics: d.metrics.map((m) => ({
      name: m.name,
      enabled: m.enabled,
      threshold: m.threshold,
      weight: m.weight,
    })),
    severityMapping: d.severity_mapping,
    qualityGate: d.quality_gate,
    ragInput: d.rag_input
      ? { mode: d.rag_input.mode, url: d.rag_input.url, timeout: d.rag_input.timeout, retry: d.rag_input.retry }
      : null,
    judge: d.judge,
    pipeline: d.pipeline,
  };
}

/** 已导入的存储版本（不可变行，直接以其 config_id 启动 Run）。 */
function StoredVersionPicker({
  configs,
  selectedId,
  onSelect,
}: {
  configs: ReturnType<typeof useProjectConfigs>;
  selectedId: string | null;
  onSelect: (row: ConfigSummaryView) => void | Promise<void>;
}) {
  const imported = configs.items.filter((c) => c.source === 'imported');
  if (configs.loading) return <div className={styles.gapHint}>正在加载已导入的配置版本…</div>;
  if (imported.length === 0) {
    return (
      <div className={styles.gapHint} data-testid="stored-versions-empty">
        该项目还没有导入的配置版本。可在「配置」页的「导入 YAML」中导入；
        下方为部署配置（只读 YAML）。
      </div>
    );
  }
  return (
    <>
      <div className={styles.gapHint}>已导入的配置版本（不可变，选择后直接使用该版本）：</div>
      <div className={styles.datasetList}>
        {imported.map((row) => (
          <button
            key={row.configId}
            type="button"
            className={[styles.datasetCard, selectedId === row.configId ? styles.selected : ''].join(' ')}
            onClick={() => void onSelect(row)}
            data-testid={`select-stored-${row.configId}`}
          >
            <div className={styles.datasetName}>
              {row.profile} <span className="mono">{row.version}</span>
            </div>
            <div className={styles.datasetMeta}>
              <span className="mono">{row.configVersion.slice(0, 8)}</span>
              <Tag tone="pass">已发布</Tag>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}

function StepTwoCatalog({
  catalog,
  profile,
  onSelect,
  disabled = false,
}: {
  catalog: ReturnType<typeof useEvaluationCatalog>;
  profile: ProfileView | null;
  disabled?: boolean;
  onSelect: (p: ProfileView) => void;
}) {
  if (catalog.loading) return <LoadingState label="正在加载评估配置…" />;
  if (catalog.error) return <ErrorState error={catalog.error} onRetry={catalog.reload} />;
  if (catalog.profiles.length === 0) {
    return <EmptyState title="暂无评估配置" description="没有可用的评估配置。" icon="circle" />;
  }
  return (
    <div className={styles.datasetList}>
      {catalog.profiles.map((p) => {
        const enabled = p.metrics.filter((m) => m.enabled).length;
        return (
          <button
            key={p.name}
            type="button"
            disabled={disabled}
            className={[styles.datasetCard, profile?.name === p.name ? styles.selected : ''].join(' ')}
            onClick={() => onSelect(p)}
            data-testid="select-profile"
          >
            <div className={styles.datasetName}>{p.name} <span className="mono">{p.version}</span></div>
            <div className={styles.datasetMeta}>
              <span>{enabled} 项启用指标</span>
              <Tag tone="neutral">{p.domain}</Tag>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/** Step 3 — Run Configuration 编辑器（G4 metric_overrides）。 */
function MetricEditor({
  profile,
  metrics,
  overrides,
  onChange,
}: {
  profile: ProfileView;
  metrics: MetricCatalogView[];
  overrides: Overrides;
  onChange: (next: Overrides) => void;
}) {
  const metaBy = new Map(metrics.map((m) => [m.name, m]));

  function update(name: string, patch: MetricOverrideState) {
    const current = overrides[name] ?? {};
    const next = { ...current, ...patch };
    // 剔除与 Profile 值相同的字段（未修改 = 不提交该字段）
    const profileMetric = profile.metrics.find((m) => m.name === name);
    const cleaned: MetricOverrideState = {};
    if (next.enabled !== undefined) {
      if (profileMetric && next.enabled === profileMetric.enabled) delete next.enabled;
      else cleaned.enabled = next.enabled;
    }
    if (next.threshold !== undefined) {
      if (profileMetric && next.threshold === profileMetric.threshold) delete next.threshold;
      else cleaned.threshold = next.threshold;
    }
    if (next.weight !== undefined) {
      if (profileMetric && next.weight === profileMetric.weight) delete next.weight;
      else cleaned.weight = next.weight;
    }
    const nextOverrides = { ...overrides };
    if (Object.keys(cleaned).length > 0) nextOverrides[name] = cleaned;
    else delete nextOverrides[name];
    onChange(nextOverrides);
  }

  return (
    <div className={styles.metricTable} data-testid="metric-editor">
      <div className={styles.metricRow}>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>指标</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>类别</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>方向 / 版本</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>启用</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>配置默认值</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>本次运行覆盖</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>生效值</span>
        <span className={[styles.metricCell, styles.metricHead].join(' ')}>状态</span>
      </div>
      {profile.metrics.map((m) => {
        const meta = metaBy.get(m.name);
        const ov = overrides[m.name] ?? {};
        const dirty = Object.keys(ov).length > 0;
        const effEnabled = ov.enabled !== undefined ? ov.enabled : m.enabled;
        const effThreshold = ov.threshold !== undefined ? ov.threshold : m.threshold;
        const effWeight = ov.weight !== undefined ? ov.weight : m.weight;
        return (
          <div key={m.name} className={[styles.metricRow, dirty ? styles.metricDirty : ''].join(' ')} data-testid={`metric-row-${m.name}`}>
            <span className={styles.metricCell}>
              {m.name}
              {meta?.description ? <span className={styles.metricHint}>{meta.description}</span> : null}
            </span>
            <span className={styles.metricCell}>{meta?.category ?? '—'}</span>
            <span className={styles.metricCell}>
              {meta ? (DIRECTION_LABEL[meta.direction] ?? meta.direction) : '—'}
              {meta ? <span className={styles.metricHint}>{meta.version}</span> : null}
            </span>
            <span className={styles.metricCell}>
              <input
                type="checkbox"
                checked={effEnabled}
                onChange={(e) => update(m.name, { enabled: e.target.checked })}
                data-testid={`metric-enabled-${m.name}`}
                aria-label={`启用 ${m.name}`}
              />
            </span>
            <span className={styles.metricCell}>
              <span className={styles.profileDefault}>
                阈值 {m.threshold === null ? '无 PASS/FAIL' : m.threshold} · 权重 {m.weight}
              </span>
            </span>
            <span className={styles.metricCell}>
              <div className={styles.overrideInputs}>
                <input
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  placeholder="阈值（留空=无判定）"
                  value={ov.threshold !== undefined ? (ov.threshold === null ? '' : ov.threshold) : ''}
                  onChange={(e) => {
                    const raw = e.target.value;
                    update(m.name, { threshold: raw === '' ? null : Number(raw) });
                  }}
                  data-testid={`metric-threshold-${m.name}`}
                  aria-label={`覆盖阈值 ${m.name}`}
                />
                <input
                  type="number"
                  step="0.05"
                  min="0"
                  placeholder="权重"
                  value={ov.weight !== undefined ? ov.weight : ''}
                  onChange={(e) => update(m.name, { weight: Number(e.target.value) })}
                  data-testid={`metric-weight-${m.name}`}
                  aria-label={`覆盖权重 ${m.name}`}
                />
              </div>
            </span>
            <span className={styles.metricCell}>
              <span className={[styles.effective, dirty ? styles.effectiveOverridden : ''].join(' ')} data-testid={`metric-effective-${m.name}`}>
                {effEnabled ? '阈值 ' : '停用 · '}
                {effEnabled ? (effThreshold === null ? '无 PASS/FAIL' : effThreshold) : null}
                {effEnabled ? <span className={styles.metricHint}>权重 {effWeight}</span> : null}
              </span>
            </span>
            <span className={styles.metricCell}>
              {effEnabled ? <Tag tone="pass">启用</Tag> : <Tag tone="neutral">停用</Tag>}
              {dirty ? <Tag tone="warning">已覆盖</Tag> : null}
            </span>
          </div>
        );
      })}
      <p className={styles.gapDesc}>
        「配置默认值」来自所选配置 Profile（只读）；「本次运行覆盖」仅对本次运行生效，
        留空的字段保留 Profile 值；「生效值」= 本次运行实际生效值。原配置 YAML 永不被修改。
      </p>
    </div>
  );
}

function RagReview({ profile }: { profile: ProfileView }) {
  const rag = profile.ragInput;
  if (!rag?.url) {
    return (
      <div className={styles.gapHint} data-testid="rag-not-configured">
        <strong>金标元数据回放（测试回放）</strong>：未配置 RAG Endpoint。
        评估将以样本 metadata 中的 answer / contexts 作为输入 —— 仅适用于
        测试回放，<strong>不是生产 RAG</strong>。
        <br />
        运行级 RAG 输入配置需后端支持（当前由部署配置 system.yaml 决定）。
      </div>
    );
  }
  return (
    <div className={styles.review}>
      <div className={styles.gapHint} data-testid="rag-http-configured">
        <strong>HTTP Endpoint（生产路径）</strong>：评估将通过 POST 调用该 RAG Endpoint 获取 answer / contexts。
        <br />
        运行级 RAG 输入配置需后端支持（当前由部署配置 system.yaml 决定）。
      </div>
      <ReviewRow label="RAG 接入地址" value={rag.url} />
      <ReviewRow label="超时" value={`${rag.timeout ?? '—'} s`} />
      <ReviewRow label="重试" value={`${rag.retry ?? '—'}`} />
    </div>
  );
}

function ReviewRow({ label, value, muted }: { label: string; value: React.ReactNode; muted?: boolean }) {
  return (
    <div className={styles.reviewRow}>
      <span className={styles.reviewLabel}>{label}</span>
      <span className={muted ? styles.reviewMuted : styles.reviewValue}>{value}</span>
    </div>
  );
}

function StepBar({ current }: { current: number }) {
  return (
    <div className={styles.steps} data-testid="new-eval-steps">
      {STEPS.map(([n, label]) => {
        const state = n < current ? 'done' : n === current ? 'active' : 'todo';
        return (
          <div key={n} className={styles.step} data-state={state}>
            <span className={styles.stepDot}>{n < current ? '✓' : n}</span>
            <span className={styles.stepLabel}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
