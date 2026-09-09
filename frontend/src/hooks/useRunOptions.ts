/**
 * useRunOptions — 供 Compare / Regression / Quality Gate / Failure Explorer
 * 使用的 Run 选择数据。仅消费 GET /api/evaluations（项目过滤 + 分页第一页），
 * 不做任何业务聚合；label 由后端字段拼接（展示层格式化）。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import type { RunListResponse } from '../api/types';

export interface RunOption {
  runId: string;
  status: string;
  datasetVersion: string;
  overallScore: number | null;
  createdAt: string;
  label: string;
}

type State = 'idle' | 'loading' | 'ready' | 'error';

export function useRunOptions(projectId: string | null): {
  state: State;
  error: unknown;
  runs: RunOption[];
  reload: () => Promise<void>;
} {
  const [state, setState] = useState<State>('idle');
  const [error, setError] = useState<unknown>(null);
  const [runs, setRuns] = useState<RunOption[]>([]);

  const load = useCallback(async () => {
    if (!projectId) {
      setRuns([]);
      setState('ready');
      return;
    }
    setState('loading');
    try {
      const page = await api.get<RunListResponse>(
        endpoints.evaluations(1, 100, projectId),
      );
      setRuns(
        page.items.map((r) => {
          const meta = (r.reproducibility_meta ?? {}) as Record<string, unknown>;
          const version = String(meta.dataset_version ?? '—');
          const short = r.run_id.slice(0, 8);
          return {
            runId: r.run_id,
            status: r.status,
            datasetVersion: version,
            overallScore: r.overall_score,
            createdAt: r.created_at,
            label: `${short}… · ${version} · ${r.status}`,
          };
        }),
      );
      setError(null);
      setState('ready');
    } catch (e) {
      setError(e);
      setState('error');
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { state, error, runs, reload: load };
}
