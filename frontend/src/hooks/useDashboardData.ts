/**
 * useDashboardData — loads the Project-Workspace data for the active project:
 * evaluations list, dataset count, and (for the latest terminal run) its report
 * + quality-gate. Purely API consumption + view mapping; the frontend never
 * recomputes overall_score / metric aggregation / failure / gate semantics.
 * A null overall_score shows as "no valid score", never 0.
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import type {
  DatasetListResponse,
  QualityGateResponse,
  ReportResponse,
  RunListResponse,
} from '../api/types';
import { TERMINAL_STATUSES } from '../api/types';

type DashboardState = 'idle' | 'loading' | 'ready' | 'empty' | 'error';

const LATEST_PAGE_SIZE = 20;

export interface DashboardData {
  state: DashboardState;
  error: unknown;
  evaluations: RunListResponse['items'];
  totalRuns: number;
  datasetCount: number;
  latestTerminalRunId: string | null;
  report: ReportResponse | null;
  qualityGate: QualityGateResponse | null;
  reload: () => Promise<void>;
}

export function useDashboardData(projectId: string | null): DashboardData {
  const [state, setState] = useState<DashboardState>('idle');
  const [error, setError] = useState<unknown>(null);
  const [evaluations, setEvaluations] = useState<RunListResponse['items']>([]);
  const [totalRuns, setTotalRuns] = useState(0);
  const [datasetCount, setDatasetCount] = useState(0);
  const [latestTerminalRunId, setLatestTerminalRunId] = useState<string | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [qualityGate, setQualityGate] = useState<QualityGateResponse | null>(null);

  const load = useCallback(async () => {
    if (!projectId) {
      setState('idle');
      return;
    }
    setState('loading');
    try {
      const [runData, datasetData] = await Promise.all([
        api.get<RunListResponse>(endpoints.evaluations(1, LATEST_PAGE_SIZE, projectId)),
        api.get<DatasetListResponse>(endpoints.datasets(1, 1, projectId)),
      ]);
      setEvaluations(runData.items);
      setTotalRuns(runData.total);
      setDatasetCount(datasetData.total);

      const terminal = runData.items.find((r) => TERMINAL_STATUSES.includes(r.status));
      const latestTerminal = terminal?.run_id ?? null;
      setLatestTerminalRunId(latestTerminal);

      if (latestTerminal) {
        const [rep, gate] = await Promise.all([
          api.get<ReportResponse>(endpoints.report(latestTerminal)),
          api.get<QualityGateResponse>(endpoints.qualityGate(latestTerminal)),
        ]);
        setReport(rep);
        setQualityGate(gate);
      } else {
        setReport(null);
        setQualityGate(null);
      }

      setState(runData.total === 0 ? 'empty' : 'ready');
    } catch (e) {
      setError(e);
      setState('error');
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    state,
    error,
    evaluations,
    totalRuns,
    datasetCount,
    latestTerminalRunId,
    report,
    qualityGate,
    reload: load,
  };
}
