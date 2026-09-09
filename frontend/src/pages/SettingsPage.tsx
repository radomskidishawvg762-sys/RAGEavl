/**
 * SettingsPage — System Settings（§十六，诚实降级）。
 * 能拿到的真实数据：GET /api/health（app + database + 延迟）。
 * Judge 公共参数支持当前进程内编辑；API Key 仍只显示 Configured / Not
 * Configured，不回显、不落盘。Embedding / Reranker 依赖状态如实展示。
 */

import { useCallback, useEffect, useState } from 'react';

import { api, endpoints } from '../api/client';
import type { HealthResponse, JudgeSettings } from '../api/types';
import { PageHeader, Panel, Section, Tag } from '../components/primitives/Surfaces';
import { LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import styles from './SettingsPage.module.css';

export function SettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<unknown>(null);
  const [judge, setJudge] = useState<JudgeSettings | null>(null);
  const [judgeForm, setJudgeForm] = useState<JudgeSettings | null>(null);
  const [judgeBusy, setJudgeBusy] = useState(false);
  const [judgeMessage, setJudgeMessage] = useState<string | null>(null);
  const [apiKeyDraft, setApiKeyDraft] = useState('');

  const load = useCallback(async () => {
    setState('loading');
    try {
      const h = await api.get<HealthResponse>(endpoints.health());
      let j: JudgeSettings | null = null;
      try {
        j = await api.get<JudgeSettings>(endpoints.judgeSettings());
      } catch {
        // Judge settings are optional for the health page; older deployments
        // must still render database health while the new endpoint is absent.
      }
      setHealth(h);
      setJudge(j);
      setJudgeForm(j);
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

  return (
    <>
      <PageHeader
        title="系统设置 Settings"
        subtitle="运行时与系统健康状态。配置项按分层展示：能读取的如实读取；仅环境变量可配置的明确标注，不提供无效表单。"
        actions={
          <button type="button" className={styles.refreshBtn} onClick={() => void load()} data-testid="settings-refresh">
            刷新
          </button>
        }
      />

      {state === 'loading' ? <LoadingState label="正在读取系统状态…" /> : null}
      {state === 'error' ? <ErrorState error={error} onRetry={load} /> : null}

      {state === 'ready' ? (
        <div className={styles.sections}>
          <Section title="1. Runtime">
            <Panel padded={false}>
              <table className={styles.table} data-testid="settings-runtime">
                <tbody>
                  <tr>
                    <td>Environment</td>
                    <td><Tag tone="info">{import.meta.env.MODE}</Tag></td>
                  </tr>
                  <tr>
                    <td>API URL</td>
                    <td className="mono">同源 /api（前端与后端同源部署）</td>
                  </tr>
                  <tr>
                    <td>Secret 管理</td>
                    <td>DATABASE_URL / Judge API Key 仅存在于环境变量；日志经 RedactingFilter 脱敏；前端永不展示 secret 值。</td>
                  </tr>
                </tbody>
              </table>
            </Panel>
          </Section>

          <Section title="2. Database">
            <Panel padded={false}>
              <table className={styles.table} data-testid="settings-database">
                <tbody>
                  <tr>
                    <td>Database Status</td>
                    <td>
                      {health ? (
                        health.database === 'healthy' ? (
                          <span className={styles.ok}>healthy（{health.detail?.db_latency_ms ?? '—'} ms）</span>
                        ) : (
                          <span className={styles.bad}>unavailable</span>
                        )
                      ) : (
                        <span className={styles.dim}>—</span>
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>App Status</td>
                    <td>{health ? <span className={styles.ok}>{health.app}</span> : <span className={styles.dim}>—</span>}</td>
                  </tr>
                  <tr>
                    <td>连接方式</td>
                    <td className="mono">由环境变量 DATABASE_URL 管理（连接串永不展示）</td>
                  </tr>
                </tbody>
              </table>
            </Panel>
          </Section>

          <Section title="3. Judge（LLM-as-a-Judge）">
            {judgeForm ? (
              <JudgeSettingsPanel
                value={judgeForm}
                busy={judgeBusy}
                message={judgeMessage}
                onChange={setJudgeForm}
                onSave={async () => {
                  setJudgeBusy(true);
                  setJudgeMessage(null);
                  try {
                    const payload: Record<string, unknown> = {
                      provider: judgeForm.provider,
                      model: judgeForm.model,
                      model_version: judgeForm.model_version,
                      base_url: judgeForm.base_url,
                      temperature: judgeForm.temperature,
                      max_tokens: judgeForm.max_tokens,
                      timeout: judgeForm.timeout,
                      retry: judgeForm.retry,
                    };
                    if (apiKeyDraft.trim()) payload.api_key = apiKeyDraft.trim();
                    const saved = await api.put<JudgeSettings>(endpoints.judgeSettings(), payload);
                    setJudge(saved);
                    setJudgeForm(saved);
                    setApiKeyDraft('');
                    setJudgeMessage('已保存。新建评估将使用此进程内配置；重启后回到环境变量配置。');
                  } catch (e) {
                    setJudgeMessage(e instanceof Error ? e.message : '保存失败');
                  } finally {
                    setJudgeBusy(false);
                  }
                }}
                onTest={async () => {
                  setJudgeBusy(true);
                  setJudgeMessage(null);
                  try {
                    const result = await api.post<JudgeSettings>(endpoints.judgeSettingsTest());
                    setJudge(result);
                    setJudgeMessage(result.message ?? (result.configured ? '本地配置有效。' : 'Judge 尚未配置完整。'));
                  } catch (e) {
                    setJudgeMessage(e instanceof Error ? e.message : '测试失败');
                  } finally {
                    setJudgeBusy(false);
                  }
                }}
                apiKeyDraft={apiKeyDraft}
                onApiKeyChange={setApiKeyDraft}
              />
            ) : (
              <Panel padded={false}>
                <div className={styles.unavailable} data-testid="settings-judge">
                  Judge 配置接口暂不可用，请确认后端已更新。旧部署仍由环境变量 JUDGE_PROVIDER / JUDGE_MODEL 管理。
                </div>
              </Panel>
            )}
          </Section>

          <Section title="4. RAG Input">
            <EnvManagedTable
              rows={[
                ['Default Endpoint', 'RAG_INPUT_URL（留空 → GoldenRunMetadataAdapter 测试回放，非生产 RAG）'],
                ['Timeout / Retry', 'system.yaml system.rag_input'],
              ]}
              testId="settings-rag"
            />
          </Section>

          <Section title="5. Evaluation Dependencies">
            <Panel padded={false}>
              <table className={styles.table} data-testid="settings-evaluation-dependencies">
                <tbody>
                  <tr>
                    <td className={styles.keyCell}>RAGAS Embedding</td>
                    <td>
                      <Tag tone="warning">未配置</Tag>
                      <span className={styles.envHint}>answer_relevancy 需要真正的向量 Embedding 模型</span>
                    </td>
                  </tr>
                  <tr>
                    <td className={styles.keyCell}>本地模型</td>
                    <td>
                      <Tag tone="info">Reranker</Tag>
                      <span className={styles.envHint}>bge-reranker-large：用于 query-document 重排序，不是 Embedding，不能直接替代向量模型</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </Panel>
          </Section>

          <Section title="6. Limits">
            <EnvManagedTable
              rows={[
                ['Max Dataset Records', 'system.yaml system.limits.max_records_per_dataset'],
                ['Default Concurrency', '执行器内置（MVP 单进程串行，ADR-02）'],
                ['阈值 / 权重 / 严重度', 'Evaluation Profile（见 Evaluation Profiles 页，只读）'],
              ]}
              testId="settings-limits"
            />
          </Section>

          <Section title="7. Health">
            <div className={styles.healthGrid} data-testid="settings-health">
              <HealthItem name="Database" ok={health?.database === 'healthy'} detail={health?.database === 'healthy' ? `${health.detail?.db_latency_ms ?? '—'} ms` : health ? 'unavailable' : '—'} />
              <HealthItem name="API" ok={Boolean(health)} detail={health ? 'healthy' : 'unreachable'} />
              <HealthItem name="Judge" ok={judge?.configured ?? null} detail={judge ? (judge.configured ? `${judge.provider} / ${judge.model}` : '配置不完整') : '—'} />
              <HealthItem name="RAG Input" ok={null} detail="由 Run 执行时的适配器调用验证" />
            </div>
          </Section>
        </div>
      ) : null}
    </>
  );
}

function EnvManagedTable({ rows, testId }: { rows: Array<[string, string]>; testId: string }) {
  return (
    <Panel padded={false}>
      <table className={styles.table} data-testid={testId}>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td className={styles.keyCell}>{k}</td>
              <td>
                <Tag tone="neutral">由环境变量 / 部署配置管理</Tag>
                <span className={styles.envHint}>{v}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function JudgeSettingsPanel({
  value,
  busy,
  message,
  onChange,
  onSave,
  onTest,
  apiKeyDraft,
  onApiKeyChange,
}: {
  value: JudgeSettings;
  busy: boolean;
  message: string | null;
  onChange: (value: JudgeSettings) => void;
  onSave: () => void;
  onTest: () => void;
  apiKeyDraft: string;
  onApiKeyChange: (value: string) => void;
}) {
  const set = (key: keyof JudgeSettings, raw: string) => {
    const numeric = ['temperature', 'max_tokens', 'timeout', 'retry'].includes(key);
    onChange({ ...value, [key]: numeric ? Number(raw) : raw });
  };
  return (
    <Panel>
      <div className={styles.settingsNote}>
        仅公共配置会被保存到当前进程。API Key 只保留现有环境变量状态，不在页面回显，也不会写入 YAML、数据库或报告。
      </div>
      <div className={styles.formGrid} data-testid="judge-settings-form">
        {([
          ['provider', 'Provider'],
          ['model', 'Model'],
          ['model_version', 'Model Version'],
          ['base_url', 'Base URL'],
        ] as const).map(([key, label]) => (
          <label key={key} className={styles.formField}>
            <span>{label}</span>
            <input value={value[key]} onChange={(e) => set(key, e.target.value)} disabled={busy} />
          </label>
        ))}
        {([
          ['temperature', 'Temperature'],
          ['max_tokens', 'Max Tokens'],
          ['timeout', 'Timeout (s)'],
          ['retry', 'Retry'],
        ] as const).map(([key, label]) => (
          <label key={key} className={styles.formField}>
            <span>{label}</span>
            <input type="number" value={value[key]} onChange={(e) => set(key, e.target.value)} disabled={busy} />
          </label>
        ))}
        <label className={styles.formField}>
          <span>临时 API Key</span>
          <input
            type="password"
            value={apiKeyDraft}
            placeholder={value.api_key_configured ? '已配置，留空表示保持不变' : '仅本次进程使用'}
            onChange={(e) => onApiKeyChange(e.target.value)}
            disabled={busy}
            autoComplete="new-password"
          />
        </label>
      </div>
      <div className={styles.judgeMeta}>
        <Tag tone={value.configured ? 'pass' : 'warning'}>{value.configured ? 'Configured' : 'Not Configured'}</Tag>
        <span>API Key: {value.api_key_configured ? 'Configured' : 'Not Configured'}</span>
        <span>来源: {value.source}</span>
      </div>
      {message ? <div className={styles.formMessage}>{message}</div> : null}
      <div className={styles.formActions}>
        <button type="button" className={styles.refreshBtn} onClick={onTest} disabled={busy}>本地校验</button>
        <button type="button" className={styles.saveBtn} onClick={onSave} disabled={busy}>保存 Judge 配置</button>
        <a
          className={styles.persistentBtn}
          href={endpoints.judgePersistentConfig()}
          target="_blank"
          rel="noreferrer"
        >
          打开长期配置文件
        </a>
      </div>
      <div className={styles.persistentHint}>
        长期修改请编辑部署目录中的 <code>config/system.yaml</code>，并重启应用；长期文件只引用环境变量，不保存 API Key。
      </div>
    </Panel>
  );
}

function HealthItem({ name, ok, detail }: { name: string; ok: boolean | null; detail: string }) {
  return (
    <div className={styles.healthItem} data-testid={`health-${name.toLowerCase()}`}>
      <div className={styles.healthName}>
        {name}
        {ok === true ? <span className={styles.ok}> ● Healthy</span> : ok === false ? <span className={styles.bad}> ● Unavailable</span> : <span className={styles.dim}> ● 无数据</span>}
      </div>
      <div className={styles.healthDetail}>{detail}</div>
    </div>
  );
}
