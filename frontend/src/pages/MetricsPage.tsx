/**
 * MetricsPage — Metric Catalog（§九）。数据来自 GET /api/metrics
 * （代码注册的 MetricRegistry 只读镜像）。按 Retrieval / Generation /
 * Integrity 分组展示，支持分类过滤 + 搜索。不提供 UI 自定义指标（PRD Q8）。
 */

import { useMemo, useState } from 'react';

import { useEvaluationCatalog } from '../hooks/useEvaluationCatalog';
import { DIRECTION_LABEL } from '../api/mappers';
import { PageHeader, Panel, Section, Tag } from '../components/primitives/Surfaces';
import { BilingualLabel } from '../components/primitives/Bilingual';
import { EmptyState, LoadingState } from '../components/primitives/Feedback';
import { ErrorState } from '../components/ErrorState';
import { presentCategory, presentSeverity } from '../status/status';
import styles from './MetricsPage.module.css';

const CATEGORY_ORDER = ['retrieval', 'generation', 'integrity'] as const;
const CATEGORY_LABEL: Record<string, string> = {
  retrieval: '检索 Retrieval',
  generation: '生成 Generation',
  integrity: '一致性 Integrity',
};

export function MetricsPage() {
  const { metrics, loading, error, reload } = useEvaluationCatalog();
  const [category, setCategory] = useState('');
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return metrics.filter((m) => {
      if (category && m.category !== category) return false;
      if (!q) return true;
      return (
        m.name.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q) ||
        m.engine.toLowerCase().includes(q)
      );
    });
  }, [metrics, category, query]);

  if (loading) return <LoadingState label="正在加载指标目录…" />;
  if (error) return <ErrorState error={error} onRetry={() => void reload()} />;

  return (
    <>
      <PageHeader
         title={<BilingualLabel zh="指标" en="Metrics" />}
        subtitle="平台内置的指标目录（只读）。当前版本不支持自定义指标。"
      />

      <div className={styles.toolbar}>
        <label className={styles.field}>
           <BilingualLabel zh="分类" en="Category" />
          <select value={category} onChange={(e) => setCategory(e.target.value)} data-testid="metrics-category-filter">
            <option value="">全部</option>
            {CATEGORY_ORDER.map((c) => (
              <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
           <BilingualLabel zh="搜索" en="Search" />
          <input
            className={styles.search}
            placeholder="名称 / 描述 / 引擎…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="metrics-search"
          />
        </label>
        <span className={styles.count}>{filtered.length} / {metrics.length} 个指标</span>
      </div>

      {metrics.length === 0 ? (
        <EmptyState title="暂无注册指标" description="指标目录中没有任何指标（属异常状态，请检查后端）。" icon="alert" />
      ) : (
        <div className={styles.groups}>
          {CATEGORY_ORDER.map((cat) => {
            const items = filtered.filter((m) => m.category === cat);
            if (items.length === 0) return null;
            return (
              <Section key={cat} title={`${CATEGORY_LABEL[cat]}（${items.length}）`}>
                <div className={styles.grid}>
                  {items.map((m) => (
                    <Panel key={m.name} className={styles.metricCard}>
                      <div data-testid={`metric-card-${m.name}`}>
                        <div className={styles.metricHead}>
                          <span className={styles.metricName}>{m.name}</span>
                          <Tag tone={m.category === 'integrity' ? 'warning' : m.category === 'generation' ? 'info' : 'neutral'}>
                            {presentCategory(m.category).label}
                          </Tag>
                        </div>
                        <p className={styles.desc}>{m.description}</p>
                        <dl className={styles.props}>
                          <div><dt>引擎</dt><dd className="mono">{m.engine}</dd></div>
                          <div><dt>版本</dt><dd className="mono">{m.version}</dd></div>
                          <div><dt>方向</dt><dd>{DIRECTION_LABEL[m.direction]}</dd></div>
                          <div><dt>默认严重度</dt><dd>{m.defaultSeverity ? presentSeverity(m.defaultSeverity).label : '—（无默认，由配置 Profile 的严重度映射决定）'}</dd></div>
                        </dl>
                        <div className={styles.reqs}>
                          <div className={styles.reqsTitle}>输入要求</div>
                          <ul>
                            {m.inputRequirements.map((r) => (
                              <li key={r} className="mono">{r}</li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </Panel>
                  ))}
                </div>
              </Section>
            );
          })}
          {filtered.length === 0 ? (
            <EmptyState title="无匹配指标" description="调整分类或搜索条件。" icon="circle" />
          ) : null}
        </div>
      )}
    </>
  );
}
