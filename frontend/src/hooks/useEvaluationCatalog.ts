/**
 * useEvaluationCatalog — 新建评估向导的只读目录数据（Profile + 指标元数据）。
 *
 * 并行消费 GET /api/configs/profiles + GET /api/metrics。这两个只读接口不依赖
 * 数据库（读三层 YAML + 代码注册的资源），因此即使 DB 未启动也能浏览。前端在此
 * 只做 snake→camel 映射，绝不重算 threshold/weight/direction/enabled。
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import { toMetricCatalogView, toProfileView } from '../api/mappers';
import type { MetricCatalogView, MetricListResponse, ProfileListResponse, ProfileView } from '../api/types';

export interface EvaluationCatalog {
  profiles: ProfileView[];
  metrics: MetricCatalogView[];
  loading: boolean;
  error: unknown;
  reload: () => Promise<void>;
}

export function useEvaluationCatalog(): EvaluationCatalog {
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [metrics, setMetrics] = useState<MetricCatalogView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [profileResp, metricResp] = await Promise.all([
        api.get<ProfileListResponse>(endpoints.profiles()),
        api.get<MetricListResponse>(endpoints.metrics()),
      ]);
      setProfiles(profileResp.items.map(toProfileView));
      setMetrics(metricResp.items.map(toMetricCatalogView));
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { profiles, metrics, loading, error, reload: load };
}
