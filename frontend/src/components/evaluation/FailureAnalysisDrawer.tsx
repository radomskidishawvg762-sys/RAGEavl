/**
 * FailureAnalysisDrawer — 诊断下钻抽屉（产品核心差异化体验 §十一）。
 *
 * 完整证据链：Sample（完整 I/O）→ Metrics（comparison_basis）→ Diagnosis
 * → Evidence（契约 + items，G2）→ Recommendation。
 * 样本 I/O 来自 GET /evaluations/{run}/results/{result_id}（G3 详情端点）；
 * 证据 items 来自 diagnoses.evidence（后端持久化，只读回显，绝不重新生成）。
 * result_id 为 null（Run 级诊断）时如实呈现，不伪造样本。
 */

import { useEffect, useState, type ReactNode } from 'react';
import type { DiagnosisOut } from '../../api/types';
import type { RecommendationView } from '../analysis/types';
import { EvidencePanel } from '../analysis/EvidencePanel';
import { useResultDetail } from '../../hooks/useResultDetail';
import { SeverityBadge } from '../../status/StatusBadge';
import { MetricStatusBadge } from '../../status/StatusBadge';
import { ScoreValue } from '../ScoreValue';
import { BilingualLabel } from '../primitives/Bilingual';
import { diagnosisTextLabel, evidenceTypeLabel } from '../../api/mappers';
import styles from './FailureAnalysisDrawer.module.css';

export interface DrawerTarget {
  runId: string;
  /** evaluation_results.id；null = Run 级诊断（无样本 I/O） */
  resultId: string | null;
  severity: string;
  failureType: string | null;
  relatedMetric: string | null;
  diagnosis: DiagnosisOut | null;
  recommendations: RecommendationView[];
  /** 列表页已知的 question（详情加载前的占位显示，来自后端列表数据） */
  fallbackQuestion?: string | null;
}

export function FailureAnalysisDrawer({
  target,
  onClose,
}: {
  target: DrawerTarget | null;
  onClose: () => void;
}) {
  const detail = useResultDetail(target?.runId ?? null, target?.resultId ?? null);

  useEffect(() => {
    if (!target) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [target, onClose]);

  if (!target) return null;
  const { diagnosis } = target;
  const undetermined = diagnosis?.status === 'undetermined';

  return (
    <div className={styles.overlay} onClick={onClose}>
        <aside
        className={styles.drawer}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
         aria-label="失败分析 / Failure Analysis"
        data-testid="diagnosis-drawer"
      >
        <header className={styles.header}>
          <SeverityBadge severity={target.severity} />
          <span className={styles.type}>{target.failureType ?? '未知失败'}</span>
          <button type="button" className={styles.close} onClick={onClose} aria-label="关闭">✕</button>
        </header>

        <ChainStrip
          steps={[
            { label: '指标', done: Boolean(target.relatedMetric || target.diagnosis?.related_metric) },
            { label: '失败', done: Boolean(target.failureType) },
            { label: '诊断', done: Boolean(target.diagnosis && target.diagnosis.status === 'diagnosed') },
            { label: '证据', done: (target.diagnosis?.evidence?.length ?? 0) > 0 },
            { label: '建议', done: target.recommendations.length > 0 },
          ]}
        />

        <div className={styles.body}>
          {/* 1. 样本 Sample（完整 I/O，来自 G3 详情端点） */}
          <Section title={<BilingualLabel zh="样本" en="Sample" />}>
            {target.resultId === null ? (
              <div className={styles.emptyNote}>运行级诊断：无单条样本 I/O。</div>
            ) : detail.state === 'loading' ? (
              <div className={styles.emptyNote}>正在加载样本详情…</div>
            ) : detail.state === 'error' ? (
              <div className={styles.emptyNote}>样本详情加载失败（{detail.error instanceof Error ? detail.error.message : '未知错误'}）。</div>
            ) : detail.detail ? (
              <div className={styles.sample}>
                 <Field label={<BilingualLabel zh="问题" en="Question" />}>
                  <Text value={detail.detail.question} />
                </Field>
                 <Field label={<BilingualLabel zh="答案" en="Answer" />}>
                  <Text value={detail.detail.answer} mono />
                </Field>
                 <Field label={<BilingualLabel zh="参考答案" en="Reference Answer" />}>
                  <Text value={detail.detail.referenceAnswer ?? '—'} mono />
                </Field>
                 <Field label={<BilingualLabel zh={`检索上下文（${detail.detail.contexts.length}）`} />}>
                  {detail.detail.contexts.length ? (
                    <ol className={styles.contextList}>
                      {detail.detail.contexts.map((c, i) => (
                        <li key={i}><Text value={c} mono /></li>
                      ))}
                    </ol>
                  ) : (
                    <span className={styles.emptyNote}>（无检索上下文）</span>
                  )}
                </Field>
                 <Field label={<BilingualLabel zh={`参考上下文（${detail.detail.referenceContexts?.length ?? 0}）`} />}>
                  {detail.detail.referenceContexts?.length ? (
                    <ol className={styles.contextList}>
                      {detail.detail.referenceContexts.map((c, i) => (
                        <li key={i}><Text value={c} mono /></li>
                      ))}
                    </ol>
                  ) : (
                    <span className={styles.emptyNote}>（无参考上下文）</span>
                  )}
                </Field>
              </div>
            ) : null}
            {detail.state !== 'ready' && target.fallbackQuestion ? (
              <Field label="问题（列表）"><Text value={target.fallbackQuestion} /></Field>
            ) : null}
          </Section>

          {/* 2. 指标 Metrics（含 comparison_basis） */}
           <Section title={<BilingualLabel zh="指标" en="Metrics" />}>
            {detail.detail && detail.detail.metricResults.length ? (
              <table className={styles.metricTable}>
                <thead>
                  <tr><th align="left">指标</th><th align="right">分数</th><th align="right">阈值</th><th align="left">状态</th></tr>
                </thead>
                <tbody>
                  {detail.detail.metricResults.map((m) => (
                    <tr key={m.name} data-testid={`drawer-metric-${m.name}`}>
                      <td className="mono">{m.name}</td>
                      <td align="right"><ScoreValue score={m.score} /></td>
                      <td align="right" className="mono">{m.threshold === null ? '—' : m.threshold}</td>
                      <td><MetricStatusBadge status={m.status} passed={m.passed} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : detail.detail ? (
              <div className={styles.emptyNote}>该样本无指标结果记录。</div>
            ) : (
              <div className={styles.emptyNote}>样本详情加载后展示。</div>
            )}
            {detail.detail?.metricResults.some((m) => m.comparisonBasis) ? (
              <ComparisonBasisList basis={detail.detail.metricResults.filter((m) => m.comparisonBasis)} />
            ) : null}
          </Section>

          {/* 3. 诊断 Diagnosis */}
           <Section title={<BilingualLabel zh="诊断" en="Diagnosis" />}>
             <Field label={<BilingualLabel zh="失败类型" en="Failure Type" />}><code>{target.failureType ?? '—'}</code></Field>
             <Field label={<BilingualLabel zh="相关指标" en="Related Metric" />}><code>{target.relatedMetric ?? diagnosis?.related_metric ?? '—'}</code></Field>
            <Field label="状态">
              {undetermined ? (
                <span className={styles.undetermined}>无法判定 undetermined</span>
              ) : (
                <span className={styles.diagnosed}>已诊断 diagnosed</span>
              )}
            </Field>
            {undetermined && diagnosis?.detail ? (
              <div className={styles.undeterminedBox}>
                <div className={styles.label}>原因</div>
                <div>{diagnosisTextLabel(diagnosis.detail.reason ?? '—')}</div>
                <div className={styles.label}>缺失证据</div>
                <div>{diagnosis.detail.missing_evidence?.map(evidenceTypeLabel).join('、') || '—'}</div>
              </div>
            ) : null}
            <div className={styles.rootCause}>
              <div className={styles.label}>根因 Root Cause</div>
              <div className={styles.rootCauseText}>{diagnosisTextLabel(diagnosis?.root_cause ?? '—（未判定，前端不猜测）')}</div>
            </div>
            <Field label="置信度">{diagnosis?.confidence ?? '—'}</Field>
          </Section>

          {/* 4. 证据 Evidence（契约 + items，G2 只读回显） */}
           <Section title={<BilingualLabel zh="证据" en="Evidence" />}>
            <EvidencePanel
              contract={diagnosis?.evidence_contract || null}
              items={diagnosis?.evidence ?? []}
              empty={undetermined ? '无法判定：证据契约未满足，无证据项。' : '该诊断无持久化证据项。'}
            />
          </Section>

          {/* 5. 建议 Recommendation */}
           <Section title={<BilingualLabel zh="修复建议" en="Recommendation" />}>
            {target.recommendations.length ? (
              <div className={styles.recs}>
                {target.recommendations.map((r, i) => (
                  <div key={`${r.action}-${i}`} className={styles.rec} data-testid="recommendation-item">
                    <span className={styles.recPriority}>P{r.priority}</span>
                    <span>{r.action}</span>
                    <span className={styles.recSource}>{r.source}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className={styles.empty}>暂无建议</div>
            )}
          </Section>
        </div>
      </aside>
    </div>
  );
}

function ComparisonBasisList({
  basis,
}: {
  basis: Array<{ name: string; comparisonBasis: Record<string, unknown> | null }>;
}) {
  return (
    <div className={styles.basisList}>
      {basis.map((m) => (
        <details key={m.name} className={styles.basisDetails}>
          <summary>
            comparison_basis · <span className="mono">{m.name}</span>
          </summary>
          <pre className={styles.basisPre}>{JSON.stringify(m.comparisonBasis, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

/** 核心工作流链路（UX Polish §十）：Metric → Failure → Diagnosis → Evidence →
 * Recommendation。步骤点亮状态来自当前抽屉数据的存在性（纯展示，不判定语义）。 */
function ChainStrip({ steps }: { steps: Array<{ label: string; done: boolean }> }) {
  return (
    <div className={styles.chain} data-testid="diagnosis-chain" aria-label="诊断链路">
      {steps.map((s, i) => (
        <span key={s.label} className={styles.chainItem}>
          {i > 0 ? <span className={styles.chainArrow}>→</span> : null}
          <span className={[styles.chainStep, s.done ? styles.chainDone : ''].join(' ')}>
            {s.done ? '●' : '○'} {s.label}
          </span>
        </span>
      ))}
    </div>
  );
}

function Section({ title, children }: { title: ReactNode; children: ReactNode }) {  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>{title}</div>
      <div className={styles.sectionBody}>{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className={styles.field}>
      <div className={styles.label}>{label}</div>
      <div className={styles.value}>{children}</div>
    </div>
  );
}

function Text({ value, mono }: { value: string; mono?: boolean }) {
  const [open, setOpen] = useState(false);
  const long = value.length > 180;
  return (
    <div className={styles.textWrap}>
      <div className={[styles.text, mono ? styles.mono : ''].join(' ')}>
        {long && !open ? value.slice(0, 180) + '…' : value || '—'}
      </div>
      {long ? (
        <button type="button" className={styles.toggle} onClick={() => setOpen((v) => !v)}>
          {open ? '收起' : '展开全部'}
        </button>
      ) : null}
    </div>
  );
}
