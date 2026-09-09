/**
 * useProjects — loads the project list from GET /api/projects and exposes a
 * lightweight reload. The workspace/selector and project-scoped pages consume
 * this. Project count comes from the API, never derived from runs.
 */

import { useCallback, useEffect, useState } from 'react';
import { api, endpoints } from '../api/client';
import type { ProjectListResponse } from '../api/types';

export function useProjects() {
  const [projects, setProjects] = useState<ProjectListResponse['items']>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<ProjectListResponse>(endpoints.projects(1, 100));
      setProjects(data.items);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { projects, total, loading, error, reload };
}
