/**
 * useResultDetail — 单条结果详情（Phase 1A G3 端点）。
 * GET /api/evaluations/{run}/results/{result_id}：完整 I/O（contexts /
 * reference_*）+ metric_results（含 comparison_basis）+ diagnoses（含 evidence）。
 * 前端只消费，不重组任何后端语义。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import type { ResultDetailOut } from '../api/types';
import { toResultDetailView } from '../api/mappers';
import type { ResultDetailView } from '../api/types';

type State = 'idle' | 'loading' | 'ready' | 'error';

export function useResultDetail(
  runId: string | null,
  resultId: string | null,
): { state: State; error: unknown; detail: ResultDetailView | null; reload: () => void } {
  const [state, setState] = useState<State>('idle');
  const [error, setError] = useState<unknown>(null);
  const [detail, setDetail] = useState<ResultDetailView | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!runId || !resultId) {
      setDetail(null);
      setState('idle');
      return;
    }
    let cancelled = false;
    setState('loading');
    api
      .get<ResultDetailOut>(endpoints.resultDetail(runId, resultId))
      .then((raw) => {
        if (cancelled) return;
        setDetail(toResultDetailView(raw));
        setError(null);
        setState('ready');
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e);
        setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [runId, resultId, tick]);

  return { state, error, detail, reload };
}
