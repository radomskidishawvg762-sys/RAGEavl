/**
 * Configuration — 配置工作区（Configuration Lifecycle v1，Phase C）。
 *
 * 工程配置工作台，不是 AI 助手：Table / Tabs / Form / Drawer / Diff / YAML
 * 编辑器 / Toolbar，无装饰性动画。三个页签：
 *   Profiles      —— 项目内全部配置版本（stored imported + 部署 YAML 指针行），
 *                    操作：View / Duplicate / Import New Version / Export YAML。
 *                    不提供「修改现有版本」——副本或新版本是唯一变更路径。
 *   Metrics       —— 代码注册指标目录（只读，MetricRegistry 镜像）。
 *   Import YAML   —— Import → Parse → Validate → Preview → Create New Version。
 *
 * 数据全部来自后端真实接口；前端不派生任何 threshold/severity/version 值。
 * Export YAML 基于后端存储原文（GET /configs/{id}/yaml），前端不拼装。
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, endpoints, ApiError } from '../api/client';
import {
  toConfigDetailView,
  toConfigImportPreview,
} from '../api/mappers';
import type {
  ConfigDetailResponse,
  ConfigDetailView,
  ConfigExportYamlResponse,
  ConfigImportCommitResponse,
  ConfigImportPreviewResponse,
  ConfigImportPreviewView,
  ConfigSummaryView,
  ConfigImportFieldError,
} from '../api/types';
import { useProject } from '../context/ProjectContext';
import { useEvaluationCatalog } from '../hooks/useEvaluationCatalog';
import { useProjectConfigs } from '../hooks/useProjectConfigs';
import { PageHeader, Panel, Tag } from '../components/primitives/Surfaces';
import { Tabs } from '../components/primitives/Tabs';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import styles from './ProfilesPage.module.css';

type TabKey = 'profiles' | 'metrics' | 'import';

export function ProfilesPage() {
  const { activeProject, loading: projectLoading, error: projectError } = useProject();
  const catalog = useEvaluationCatalog();
  const configs = useProjectConfigs(activeProject?.id ?? null);

  const [tab, setTab] = useState<TabKey>('profiles');
  const [tabNonce, setTabNonce] = useState(0);
  const [importMode, setImportMode] = useState<'duplicate' | 'newVersion'>('newVersion');
  const [editorSeed, setEditorSeed] = useState<string | null>(null); // YAML prefill (duplicate / new version)
  const [detailId, setDetailId] = useState<string | null>(null);

  if (projectLoading) return <LoadingState label="正在加载项目…" />;
  if (projectError) return <ErrorState error={projectError} />;
  if (!activeProject) {
    return (
      <>
        <PageHeader title="Configuration" subtitle="配置工作区按项目隔离。" />
        <EmptyState title="请选择项目" description="配置 Profile 按 Project-scoped 管理，请先选择项目。" icon="circle" />
      </>
    );
  }

  function openImportTab(prefillYaml: string | null, mode: 'duplicate' | 'newVersion') {
    setEditorSeed(prefillYaml);
    setImportMode(mode);
    setTab('import');
    // force Tabs remount with the new initial key (Tabs primitive is uncontrolled)
    setTabNonce((n) => n + 1);
  }

  return (
    <>
      <PageHeader
        title="Configuration"
        subtitle={`项目 ${activeProject.name} · System → Domain → Evaluation Profile → Run Override → Effective → Snapshot。旧版本不可变：变更只能通过创建新版本。`}
      />

      <Tabs
        key={`tabs-${tabNonce}`}
        initial={tab}
        onChange={(k) => setTab(k as TabKey)}
        items={[
          { key: 'profiles', label: 'Profiles', count: configs.total },
          { key: 'metrics', label: 'Metrics', count: catalog.metrics.length },
          { key: 'import', label: 'Import YAML' },
        ]}
      >
        {(active) => (
          <>
            {active === 'profiles' ? (
              <ProfilesTab
                configs={configs}
                onView={(id) => setDetailId(id)}
                onDuplicate={async (row) => {
                  const yaml = await fetchExportYaml(row.configId);
                  openImportTab(yaml, 'duplicate');
                }}
                onImportNewVersion={async (row) => {
                  const yaml = await fetchExportYaml(row.configId);
                  openImportTab(yaml, 'newVersion');
                }}
              />
            ) : null}
            {active === 'metrics' ? <MetricsTab /> : null}
            {active === 'import' ? (
              <ImportTab
                projectId={activeProject.id}
                seedYaml={editorSeed}
                mode={importMode}
                onCreated={() => {
                  void configs.reload();
                  setEditorSeed(null);
                  setImportMode('newVersion');
                  setTab('profiles');
                  setTabNonce((n) => n + 1);
                }}
              />
            ) : null}
          </>
        )}
      </Tabs>

      {detailId ? <ConfigDetailDrawer configId={detailId} onClose={() => setDetailId(null)} /> : null}
    </>
  );
}

// ---------------- Profiles tab ----------------

function ProfilesTab({
  configs,
  onView,
  onDuplicate,
  onImportNewVersion,
}: {
  configs: ReturnType<typeof useProjectConfigs>;
  onView: (configId: string) => void;
  onDuplicate: (row: ConfigSummaryView) => void | Promise<void>;
  onImportNewVersion: (row: ConfigSummaryView) => void | Promise<void>;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function guarded(id: string, fn: () => void | Promise<void>) {
    setBusyId(id);
    try {
      await fn();
    } finally {
      setBusyId(null);
    }
  }

  if (configs.loading) return <LoadingState label="正在加载配置版本…" />;
  if (configs.error) return <ErrorState error={configs.error} onRetry={() => void configs.reload()} />;
  if (configs.items.length === 0) {
    return (
      <Panel title="Profiles">
        <EmptyState
          title="该项目还没有配置版本"
          description="通过 Import YAML 导入第一个 Profile，或在新建评估向导中选择部署 Profile。"
          icon="circle"
        />
      </Panel>
    );
  }

  return (
    <Panel title={`Profiles（${configs.total}）`} padded={false}>
      <div className={styles.tableWrap}>
        <table className={styles.configTable} data-testid="config-table">
          <thead>
            <tr>
              <th>Profile</th>
              <th>Domain</th>
              <th>Version</th>
              <th>Status</th>
              <th>Metrics</th>
              <th>Gate</th>
              <th>RAG</th>
              <th>Config Version</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {configs.items.map((row) => (
              <tr key={row.configId} data-testid={`config-row-${row.name}`}>
                <td className="mono">{row.profile}</td>
                <td><Tag>{row.domain}</Tag></td>
                <td className="mono">{row.version ?? '—'}</td>
                <td>
                  {row.source === 'imported'
                    ? <Tag tone="pass">PUBLISHED</Tag>
                    : <Tag>部署文件</Tag>}
                </td>
                <td className={styles.dimCell}>—</td>
                <td className={styles.dimCell}>—</td>
                <td className={styles.dimCell}>—</td>
                <td><code className={styles.hash} title={row.configVersion}>{row.configVersion.slice(0, 8)}</code></td>
                <td>
                  <div className={styles.rowActions}>
                    <button type="button" className={styles.actionBtn} data-testid={`config-view-${row.configId}`}
                            onClick={() => onView(row.configId)}>View</button>
                    <button type="button" className={styles.actionBtn} disabled={busyId === row.configId}
                            data-testid={`config-duplicate-${row.configId}`}
                            onClick={() => void guarded(row.configId, () => onDuplicate(row))}>Duplicate</button>
                    <button type="button" className={styles.actionBtn} disabled={busyId === row.configId}
                            data-testid={`config-import-version-${row.configId}`}
                            onClick={() => void guarded(row.configId, () => onImportNewVersion(row))}>Import New Version</button>
                    <button type="button" className={styles.actionBtn} disabled={busyId === row.configId}
                            data-testid={`config-export-${row.configId}`}
                            onClick={() => void guarded(row.configId, () => downloadExport(row.configId))}>Export YAML</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.hint}>
        Metrics / Gate / RAG 列在 View 详情中展示（列表接口不携带全量 body，避免第二个配置事实来源）。
        Duplicate / Import New Version 均以存储 YAML 原文预填编辑器，提交时创建新版本；旧版本永不修改。
      </p>
    </Panel>
  );
}

async function fetchExportYaml(configId: string): Promise<string> {
  const data = await api.get<ConfigExportYamlResponse>(endpoints.configExportYaml(configId));
  return data.yaml;
}

async function downloadExport(configId: string): Promise<void> {
  const data = await api.get<ConfigExportYamlResponse>(endpoints.configExportYaml(configId));
  const blob = new Blob([data.yaml], { type: 'text/yaml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${data.profile}${data.version ? `-${data.version}` : ''}.yaml`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------- Metrics tab ----------------

function MetricsTab() {
  const catalog = useEvaluationCatalog();
  if (catalog.loading) return <LoadingState label="正在加载指标目录…" />;
  if (catalog.error) return <ErrorState error={catalog.error} onRetry={() => void catalog.reload()} />;
  return (
    <Panel title={`Metrics（${catalog.metrics.length}）`} padded={false}>
      <div className={styles.tableWrap}>
        <table className={styles.configTable} data-testid="metrics-table">
          <thead>
            <tr>
              <th>Name</th><th>Category</th><th>Engine</th><th>Version</th>
              <th>Direction</th><th>Default Severity</th><th>Input</th>
            </tr>
          </thead>
          <tbody>
            {catalog.metrics.map((m) => (
              <tr key={m.name}>
                <td className="mono">{m.name}</td>
                <td><Tag>{m.category}</Tag></td>
                <td className="mono">{m.engine}</td>
                <td className="mono">{m.version}</td>
                <td>{m.direction}</td>
                <td>{m.defaultSeverity ?? '—'}</td>
                <td className={styles.dimCell}>{m.inputRequirements?.join('、') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

// ---------------- Import YAML tab ----------------

function ImportTab({
  projectId,
  seedYaml,
  mode,
  onCreated,
}: {
  projectId: string;
  seedYaml: string | null;
  mode: 'duplicate' | 'newVersion';
  onCreated: () => void;
}) {
  const [yamlText, setYamlText] = useState(seedYaml ?? '');
  const [preview, setPreview] = useState<ConfigImportPreviewView | null>(null);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<unknown>(null);

  const fieldErrors = useMemo(() => extractFieldErrors(commitError), [commitError]);

  async function handlePreview() {
    setCommitError(null);
    try {
      const resp = await api.post<ConfigImportPreviewResponse>(endpoints.configImportPreview(), {
        project_id: projectId,
        domain: 'general',
        yaml: yamlText,
      });
      setPreview(toConfigImportPreview(resp));
    } catch (e) {
      setPreview(null);
      setCommitError(e);
    }
  }

  async function handleCreate() {
    setCommitting(true);
    setCommitError(null);
    try {
      const resp = await api.post<ConfigImportCommitResponse>(endpoints.configImport(), {
        project_id: projectId,
        domain: 'general',
        yaml: yamlText,
      });
      void resp; // created payload confirmed by the parent's list reload
      setPreview(null);
      onCreated();
    } catch (e) {
      setCommitError(e);
    } finally {
      setCommitting(false);
    }
  }

  return (
    <Panel
      title={mode === 'duplicate' ? 'Import YAML — Duplicate Profile' : 'Import YAML — 新版本'}
      actions={
        <button type="button" className={styles.primaryBtn} data-testid="yaml-preview-btn"
                disabled={!yamlText.trim()} onClick={() => void handlePreview()}>
          验证并预览
        </button>
      }
    >
      <p className={styles.hint}>
        流程：Import → Parse → Validate → Preview → Create New Version。
        校验失败返回逐字段错误（path + message）。创建的版本不可变；如需修改，请再次创建新版本。
      </p>
      <YamlEditor value={yamlText} onChange={setYamlText} fieldErrors={fieldErrors} />

      {fieldErrors.length > 0 ? (
        <div className={styles.errorBox} data-testid="import-field-errors">
          <div className={styles.detailTitle}>校验错误（{fieldErrors.length}）</div>
          <table className={styles.table}>
            <tbody>
              {fieldErrors.map((e, i) => (
                <tr key={`${e.path}-${i}`}>
                  <td className="mono">{e.path}</td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {preview ? (
        <ImportPreview preview={preview} committing={committing} onCreate={() => void handleCreate()} />
      ) : null}
    </Panel>
  );
}

function extractFieldErrors(err: unknown): ConfigImportFieldError[] {
  if (err instanceof ApiError && err.code === 'BIZ_CONFIG_IMPORT_INVALID') {
    const errors = (err.context as { errors?: ConfigImportFieldError[] } | undefined)?.errors;
    return Array.isArray(errors) ? errors : [];
  }
  return [];
}

/** Phase-1 YAML 编辑器：行号 + 校验错误定位；不引入重量级 IDE（任务 §22）。 */
function YamlEditor({
  value,
  onChange,
  fieldErrors,
}: {
  value: string;
  onChange: (next: string) => void;
  fieldErrors: ConfigImportFieldError[];
}) {
  const lines = useMemo(() => value.split('\n'), [value]);
  const errorLine = useMemo(() => {
    for (const e of fieldErrors) {
      const m = /line (\d+)/.exec(e.message);
      if (e.path === 'yaml' && m) return Number(m[1]);
    }
    return null;
  }, [fieldErrors]);

  return (
    <div className={styles.editorWrap} data-testid="yaml-editor">
      <div className={styles.gutter} aria-hidden="true">
        {lines.map((_, i) => (
          <div key={i} className={[styles.gutterLine, errorLine === i + 1 ? styles.gutterError : ''].join(' ')}>
            {i + 1}
          </div>
        ))}
      </div>
      <textarea
        className={styles.editor}
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="粘贴 Profile YAML（profile / metrics / severity_mapping / pipeline / rag_input / quality_gate）"
        data-testid="yaml-textarea"
      />
    </div>
  );
}

function ImportPreview({
  preview,
  committing,
  onCreate,
}: {
  preview: ConfigImportPreviewView;
  committing: boolean;
  onCreate: () => void;
}) {
  return (
    <div className={styles.previewBox} data-testid="import-preview-panel">
      <div className={styles.previewNotice} data-testid="preview-version-notice">
        将创建<strong>新版本</strong>（{preview.profile}:{preview.version ?? '—'}），不修改任何旧版本；历史 Run 继续绑定旧版本。
      </div>

      <div className={styles.kvGrid}>
        <KV k="Profile" v={preview.profile} />
        <KV k="Version（将创建）" v={preview.version ?? '—'} />
        <KV k="Domain" v={preview.domain} />
        <KV k="Config Version" v={<code className={styles.hash}>{preview.configVersion.slice(0, 16)}…</code>} />
      </div>

      <SectionTitle>Metrics</SectionTitle>
      <table className={styles.configTable} data-testid="preview-metrics">
        <thead><tr><th>Name</th><th>Enabled</th><th>Threshold</th><th>Weight</th><th>Direction</th></tr></thead>
        <tbody>
          {preview.metrics.map((m) => (
            <tr key={m.name}>
              <td className="mono">{m.name}</td>
              <td>{m.enabled ? <Tag tone="pass">启用</Tag> : <Tag>停用</Tag>}</td>
              <td className="mono">{m.threshold === null ? '无 PASS/FAIL' : m.threshold}</td>
              <td className="mono">{m.weight}</td>
              <td>{m.direction}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <SectionTitle>Severity Mapping</SectionTitle>
      {Object.keys(preview.severityMapping).length ? (
        <table className={styles.table}>
          <tbody>
            {Object.entries(preview.severityMapping).map(([k, v]) => (
              <tr key={k}><td className="mono">{k}</td><td><Tag tone={v === 'CRITICAL' ? 'error' : v === 'ERROR' ? 'warning' : 'neutral'}>{v}</Tag></td></tr>
            ))}
          </tbody>
        </table>
      ) : <span className={styles.dim}>未配置（使用规则默认建议）</span>}

      <SectionTitle>Quality Gate / Pipeline / Judge / RAG Input</SectionTitle>
      <div className={styles.kvGrid}>
        <KV k="Quality Gate" v={preview.qualityGate ? <pre className={styles.pre}>{JSON.stringify(preview.qualityGate, null, 2)}</pre> : '未配置（quality_gate: null → 门禁不可用）'} />
        <KV k="Pipeline" v={preview.pipeline ? <pre className={styles.pre}>{JSON.stringify(preview.pipeline, null, 2)}</pre> : '未配置（引擎由启用指标推导）'} />
        <KV k="Judge（仅公共字段）" v={<pre className={styles.pre}>{JSON.stringify(preview.judge, null, 2)}</pre>} />
        <KV k="RAG Input" v={<pre className={styles.pre}>{JSON.stringify(preview.ragInput, null, 2)}</pre>} />
      </div>

      <SectionTitle>Config Diff{preview.diff.base ? `（基线：${preview.diff.base === 'yaml' ? '部署 YAML 同名 Profile' : preview.diff.base}）` : '（无基线，全部为新增）'}</SectionTitle>
      {preview.diff.items.length ? (
        <table className={styles.configTable} data-testid="diff-table">
          <thead><tr><th>Path</th><th>Change</th><th>Old</th><th>New</th></tr></thead>
          <tbody>
            {preview.diff.items.map((i) => (
              <tr key={`${i.path}-${i.change}`} data-testid={`diff-${i.change}`}>
                <td className="mono">{i.path}</td>
                <td>
                  <Tag tone={i.change === 'added' ? 'pass' : i.change === 'removed' ? 'error' : 'warning'}>
                    {i.change}
                  </Tag>
                </td>
                <td className="mono">{formatDiffValue(i.old)}</td>
                <td className="mono">{formatDiffValue(i.new)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <span className={styles.dim}>与基线无差异（identical）</span>
      )}

      {preview.warnings.length ? (
        <div className={styles.warnBox}>
          {preview.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
        </div>
      ) : null}

      <div className={styles.actions}>
        <button type="button" className={styles.primaryBtn} data-testid="create-version-btn"
                disabled={committing} onClick={onCreate}>
          {committing ? '创建中…' : 'Create New Version'}
        </button>
      </div>
    </div>
  );
}

function formatDiffValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

// ---------------- Profile Detail drawer (§11) ----------------

const DETAIL_SECTIONS: Array<[string, string]> = [
  ['overview', 'Overview'],
  ['metrics', 'Metrics'],
  ['severity', 'Severity'],
  ['pipeline', 'Pipeline'],
  ['judge', 'Judge'],
  ['rag', 'RAG Input'],
  ['gate', 'Quality Gate'],
  ['yaml', 'Raw YAML'],
];

function ConfigDetailDrawer({ configId, onClose }: { configId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ConfigDetailView | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.get<ConfigDetailResponse>(endpoints.configDetail(configId));
      setDetail(toConfigDetailView(resp));
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [configId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function copyYaml() {
    if (detail?.yaml) {
      await navigator.clipboard.writeText(detail.yaml);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true" data-testid="config-detail-drawer">
      <div className={styles.drawer}>
        <header className={styles.drawerHeader}>
          <div>
            <div className={styles.drawerTitle} data-testid="detail-profile">
              <span className="mono">{detail?.profile ?? '…'}</span>
              {detail?.version ? <span className="mono">{detail.version}</span> : null}
              {detail ? (detail.source === 'imported' ? <Tag tone="pass">PUBLISHED</Tag> : <Tag>部署文件</Tag>) : null}
            </div>
            {detail ? (
              <div className={styles.dim}>
                config_version <code className={styles.hash} title={detail.configVersion}>{detail.configVersion.slice(0, 12)}</code>
                {' · '}domain {detail.domain}
                {detail.createdAt ? ` · ${detail.createdAt.slice(0, 19).replace('T', ' ')}` : ''}
              </div>
            ) : null}
          </div>
          <button type="button" className={styles.actionBtn} onClick={onClose} data-testid="detail-close">关闭</button>
        </header>

        {loading ? <LoadingState label="正在加载配置详情…" /> : null}
        {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

        {detail ? (
          <Tabs items={DETAIL_SECTIONS.map(([key, label]) => ({ key, label }))} initial="overview">
            {(active) => (
              <div className={styles.drawerBody}>
                {active === 'overview' ? (
                  <>
                    <KV k="Profile" v={`${detail.profile}${detail.version ? `:${detail.version}` : ''}`} />
                    <KV k="Row Name" v={detail.name} />
                    <KV k="来源" v={detail.source === 'imported' ? '导入的存储版本（不可变）' : '部署 YAML 文件（config/ 挂载）'} />
                    <KV k="Config Version" v={<code className={styles.hash}>{detail.configVersion}</code>} />
                    <KV k="启用的指标" v={`${detail.metrics.filter((m) => m.enabled).length} / ${detail.metrics.length}`} />
                  </>
                ) : null}
                {active === 'metrics' ? (
                  <table className={styles.configTable} data-testid="detail-metrics">
                    <thead><tr><th>Name</th><th>Enabled</th><th>Threshold</th><th>Weight</th><th>Direction</th></tr></thead>
                    <tbody>
                      {detail.metrics.map((m) => (
                        <tr key={m.name}>
                          <td className="mono">{m.name}</td>
                          <td>{m.enabled ? '✓' : '✗'}</td>
                          <td className="mono">{m.threshold === null ? '无 PASS/FAIL' : m.threshold}</td>
                          <td className="mono">{m.weight}</td>
                          <td>{m.direction}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : null}
                {active === 'severity' ? (
                  Object.keys(detail.severityMapping).length ? (
                    <table className={styles.table}>
                      <tbody>
                        {Object.entries(detail.severityMapping).map(([k, v]) => (
                          <tr key={k}><td className="mono">{k}</td><td><Tag tone={v === 'CRITICAL' ? 'error' : v === 'ERROR' ? 'warning' : 'neutral'}>{v}</Tag></td></tr>
                        ))}
                      </tbody>
                    </table>
                  ) : <span className={styles.dim}>未配置（使用规则默认建议）</span>
                ) : null}
                {active === 'pipeline' ? (
                  detail.pipeline ? <pre className={styles.pre}>{JSON.stringify(detail.pipeline, null, 2)}</pre>
                    : <span className={styles.dim}>未配置（引擎由启用指标推导）</span>
                ) : null}
                {active === 'judge' ? (
                  <>
                    <p className={styles.hint}>仅公共字段；api_key 等秘密只存在于后端环境变量（从不进入 Profile / DB / 前端响应）。</p>
                    <pre className={styles.pre}>{JSON.stringify(detail.judge, null, 2)}</pre>
                  </>
                ) : null}
                {active === 'rag' ? (
                  <pre className={styles.pre}>{JSON.stringify(detail.ragInput, null, 2)}</pre>
                ) : null}
                {active === 'gate' ? (
                  detail.qualityGate ? <pre className={styles.pre}>{JSON.stringify(detail.qualityGate, null, 2)}</pre>
                    : <span className={styles.dim}>未配置（quality_gate: null → 门禁不可用）</span>
                ) : null}
                {active === 'yaml' ? (
                  <>
                    <div className={styles.actions}>
                      <button type="button" className={styles.actionBtn} data-testid="yaml-copy" onClick={() => void copyYaml()}>
                        {copied ? '已复制' : '复制'}
                      </button>
                      <button type="button" className={styles.actionBtn} data-testid="yaml-export"
                              onClick={() => void downloadExport(detail.configId)}>导出 YAML</button>
                    </div>
                    <pre className={[styles.pre, styles.yamlPre].join(' ')} data-testid="detail-yaml">{detail.yaml ?? '（无存储原文）'}</pre>
                  </>
                ) : null}
              </div>
            )}
          </Tabs>
        ) : null}
      </div>
    </div>
  );
}

// ---------------- shared bits ----------------

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className={styles.kv}>
      <span className={styles.detailTitle}>{k}</span>
      <span>{v}</span>
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <div className={[styles.detailTitle, styles.sectionTitle].join(' ')}>{children}</div>;
}
