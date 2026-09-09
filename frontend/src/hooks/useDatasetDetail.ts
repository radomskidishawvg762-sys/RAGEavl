/**
 * useDatasetDetail — 数据集详情：元数据 + 校验报告 + 分页记录。仅消费
 * GET /api/datasets/{id}、GET /api/datasets/{id}/validation、
 * GET /api/datasets/{id}/records。校验语义与记录数均由后端提供。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import type {
  DatasetOut,
  DatasetRecordsPage,
  DatasetValidationResponse,
} from '../api/types';

interface State {
  loading: boolean;
  error: unknown;
  dataset: DatasetOut | null;
  validation: DatasetValidationResponse | null;
  records: DatasetRecordsPage | null;
}

export function useDatasetDetail(datasetId: string | null) {
  const [state, setState] = useState<State>({
    loading: false,
    error: null,
    dataset: null,
    validation: null,
    records: null,
  });
  const [recordPage, setRecordPage] = useState(1);
  const recordPageSize = 20;

  const load = useCallback(async () => {
    if (!datasetId) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const [ds, valRaw, recs] = await Promise.all([
        api.get<DatasetOut>(endpoints.dataset(datasetId)),
        // 真实端点形状：{dataset_id, validation_status, validation_report:{valid,checks}}
        // （Chrome E2E Freeze Gate 抓到的真实 bug：此前未解包导致详情页崩溃）
        api.get<{ validation_report: DatasetValidationResponse | null }>(
          endpoints.datasetValidation(datasetId),
        ),
        api.get<DatasetRecordsPage>(endpoints.datasetRecords(datasetId, 1, recordPageSize)),
      ]);
      setState({
        loading: false,
        error: null,
        dataset: ds,
        validation: valRaw.validation_report ?? null,
        records: recs,
      });
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: e }));
    }
  }, [datasetId]);

  const loadRecords = useCallback(async (page: number) => {
    if (!datasetId) return;
    try {
      const recs = await api.get<DatasetRecordsPage>(
        endpoints.datasetRecords(datasetId, page, recordPageSize),
      );
      setState((s) => ({ ...s, records: recs }));
    } catch (e) {
      setState((s) => ({ ...s, error: e }));
    }
  }, [datasetId]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    ...state,
    recordPage,
    recordPageSize,
    setRecordPage: (p: number) => {
      setRecordPage(p);
      void loadRecords(p);
    },
    reload: load,
  };
}
