/**
 * useProjectConfigs — Configuration workspace data (Configuration Lifecycle v1,
 * Phase C). Loads the project's evaluation_configs rows (stored imported
 * versions + legacy YAML pointer rows) via GET /api/projects/{pid}/configs.
 *
 * Read-only aggregation hook: it NEVER derives thresholds/severities/versions
 * client-side — every displayed value comes from the backend rows verbatim.
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import { toConfigSummaryView } from '../api/mappers';
import type { ProjectConfigListResponse } from '../api/types';
import type { ConfigSummaryView } from '../api/types';

export interface ProjectConfigs {
  items: ConfigSummaryView[];
  total: number;
  loading: boolean;
  error: unknown;
  reload: () => Promise<void>;
}

export function useProjectConfigs(projectId: string | null): ProjectConfigs {
  const [items, setItems] = useState<ConfigSummaryView[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(Boolean(projectId));
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!projectId) {
      setItems([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const resp = await api.get<ProjectConfigListResponse>(endpoints.projectConfigs(projectId));
      setItems(resp.items.map(toConfigSummaryView));
      setTotal(resp.total);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { items, total, loading, error, reload: load };
}
