/**
 * useEvaluationWorkspace — 评估工作台数据。消费现有只读 API：
 * GET /evaluations/{id}、/report、/quality-gate、/results?is_failure=true、
 * /diagnoses、/recommendations。前端只呈现，不重算任何后端语义。
 * null 分数永不显示为 0。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints, fetchAllPages } from '../api/client';
import type {
  DiagnosesPageResponse,
  QualityGateResponse,
  RecommendationsResponse,
  ReportResponse,
  ResultsPageResponse,
  RunOut,
} from '../api/types';

type State = 'idle' | 'loading' | 'ready' | 'error';

export interface WorkspaceData {
  state: State;
  error: unknown;
  run: RunOut | null;
  report: ReportResponse | null;
  qualityGate: QualityGateResponse | null;
  failureResults: ResultsPageResponse['items'];
  diagnoses: DiagnosesPageResponse['items'];
  recommendations: RecommendationsResponse['items'];
  reload: () => Promise<void>;
}

export function useEvaluationWorkspace(runId: string | null): WorkspaceData {
  const [state, setState] = useState<State>('idle');
  const [error, setError] = useState<unknown>(null);
  const [run, setRun] = useState<RunOut | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [qualityGate, setQualityGate] = useState<QualityGateResponse | null>(null);
  const [failureResults, setFailureResults] = useState<ResultsPageResponse['items']>([]);
  const [diagnoses, setDiagnoses] = useState<DiagnosesPageResponse['items']>([]);
  const [recommendations, setRecommendations] = useState<RecommendationsResponse['items']>([]);

  const load = useCallback(async () => {
    if (!runId) return;
    setState('loading');
    try {
      const [runData, rep, gate, res, dia, rec] = await Promise.all([
        api.get<RunOut>(endpoints.run(runId)),
        api.get<ReportResponse>(endpoints.report(runId)),
        api.get<QualityGateResponse>(endpoints.qualityGate(runId)),
        fetchAllPages<ResultsPageResponse['items'][number]>((p, ps) => endpoints.results(runId, p, ps, true)),
        fetchAllPages<DiagnosesPageResponse['items'][number]>((p, ps) => endpoints.diagnoses(runId, p, ps)),
        api.get<RecommendationsResponse>(endpoints.recommendations(runId)),
      ]);
      setRun(runData);
      setReport(rep);
      setQualityGate(gate);
      setFailureResults(res);
      setDiagnoses(dia);
      setRecommendations(rec.items);
      setError(null);
      setState('ready');
    } catch (e) {
      setError(e);
      setState('error');
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { state, error, run, report, qualityGate, failureResults, diagnoses, recommendations, reload: load };
}
