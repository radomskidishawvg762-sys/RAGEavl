/**
 * useEvaluations — 评估运行列表（项目作用域）。消费 GET /api/evaluations（分页 +
 * project_id + status）、GET /api/datasets（数据集名称映射）、以及每条终态运行的
 * GET /api/evaluations/{id}/quality-gate（列表页懒加载）。
 * 前端不重算 overall_score / quality_gate。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints, MAX_PAGE_SIZE } from '../api/client';
import type {
  DatasetListResponse,
  QualityGateResponse,
  RunListResponse,
  RunStatus,
} from '../api/types';
import { TERMINAL_STATUSES } from '../api/types';

const PAGE_SIZE = 20;

export type RunSort = 'created_at' | 'overall_score';

export interface EvaluationRow {
  run_id: string;
  dataset_id: string;
  dataset_name: string;
  dataset_version: string;
  config_id: string;
  status: RunStatus;
  overall_score: number | null;
  evaluation_coverage: number | null;
  error_records: number;
  created_at: string;
  finished_at: string | null;
  gateStatus: string | null;
}

export function useEvaluations(projectId: string | null) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<RunStatus | ''>('');
  const [rows, setRows] = useState<EvaluationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const runsPromise = api.get<RunListResponse>(
        endpoints.evaluations(page, PAGE_SIZE, projectId, status || undefined),
      );
      // Datasets are only a name/version lookup for the current page of runs
      // (the evaluations list is itself paginated), so a legal page_size within
      // the backend cap is used rather than a full-collection fetch.
      const datasetsPromise = api.get<DatasetListResponse>(
        endpoints.datasets(1, MAX_PAGE_SIZE, projectId),
      );
      const [runData, dsData] = await Promise.all([runsPromise, datasetsPromise]);
      const nameById = new Map(dsData.items.map((d) => [d.id, d.name]));
      const versionById = new Map(dsData.items.map((d) => [d.id, d.version]));

      const base: EvaluationRow[] = runData.items.map((r) => ({
        run_id: r.run_id,
        dataset_id: r.dataset_id,
        dataset_name: nameById.get(r.dataset_id) ?? r.dataset_id,
        dataset_version: (r.reproducibility_meta as Record<string, unknown>)?.dataset_version
          ? String((r.reproducibility_meta as Record<string, unknown>).dataset_version)
          : versionById.get(r.dataset_id) ?? '—',
        config_id: r.config_id,
        status: r.status,
        overall_score: r.overall_score,
        evaluation_coverage: r.evaluation_coverage,
        error_records: r.error_records,
        created_at: r.created_at,
        finished_at: r.finished_at,
        gateStatus: null,
      }));

      // Lazy quality-gate enrichment for terminal runs on the current page.
      // A gate fetch failure is NEVER coerced into a fake status — it stays
      // null and the list renders an explicit "获取失败" state instead.
      const terminal = base.filter((r) => TERMINAL_STATUSES.includes(r.status));
      await Promise.all(
        terminal.map(async (r) => {
          try {
            const gate = await api.get<QualityGateResponse>(endpoints.qualityGate(r.run_id));
            r.gateStatus = gate.status;
          } catch {
            r.gateStatus = null;
          }
        }),
      );

      setRows(base);
      setTotal(runData.total);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [projectId, page, status]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    page,
    setPage,
    status,
    setStatus: (s: RunStatus | '') => {
      setStatus(s);
      setPage(1);
    },
    rows,
    total,
    loading,
    error,
    reload: load,
    pageSize: PAGE_SIZE,
  };
}
