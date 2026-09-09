/**
 * useDatasets — 项目作用域的数据集分页列表。仅消费 GET /api/datasets（分页 +
 * project_id 过滤 + 排序）。前端不生成 record_count / validation 语义。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import type { DatasetListResponse } from '../api/types';

const PAGE_SIZE = 20;

export function useDatasets(projectId: string | null) {
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('created_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [items, setItems] = useState<DatasetListResponse['items']>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const data = await api.get<DatasetListResponse>(
        endpoints.datasets(page, PAGE_SIZE, projectId, sort, order),
      );
      setItems(data.items);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [projectId, page, sort, order]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    page,
    setPage,
    sort,
    setSort,
    order,
    setOrder,
    items,
    total,
    loading,
    error,
    reload: load,
    pageSize: PAGE_SIZE,
  };
}
