import { useEffect, useRef, useState } from 'react';

import { api, endpoints } from '../api/client';
import { ACTIVE_STATUSES, TERMINAL_STATUSES, type RunStatus } from '../api/types';

/**
 * Polling contract (§八): while a run is pending/running, poll
 * GET /api/evaluations/{id}/progress. As soon as a terminal status
 * (completed / completed_with_errors / failed / cancelled) is observed, stop
 * polling and notify the caller once so it can re-fetch the report.
 * No SSE / WebSocket (explicitly out of scope this round).
 */
export function useProgressPolling(
  runId: string,
  initialStatus: RunStatus,
  intervalMs = 1500,
) {
  const [status, setStatus] = useState<RunStatus>(initialStatus);
  const [progress, setProgress] = useState<{
    total: number;
    evaluated: number;
    errors: number;
    coverage: number | null;
  } | null>(null);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;
    setStatus(initialStatus);
    if (!ACTIVE_STATUSES.includes(initialStatus)) return undefined;

    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const data = await api.get<{
          status: RunStatus;
          total: number;
          evaluated: number;
          errors: number;
          coverage: number | null;
        }>(endpoints.progress(runId));
        setProgress({
          total: data.total,
          evaluated: data.evaluated,
          errors: data.errors,
          coverage: data.coverage,
        });
        setStatus(data.status);
        if (TERMINAL_STATUSES.includes(data.status)) {
          stopped.current = true;
          return; // stop: terminal reached
        }
      } catch {
        // transient network errors must not kill polling
      }
      if (!stopped.current) {
        timer = setTimeout(poll, intervalMs);
      }
    };
    timer = setTimeout(poll, intervalMs);
    return () => {
      stopped.current = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, initialStatus, intervalMs]);

  const isPolling = ACTIVE_STATUSES.includes(status);
  return { status, progress, isPolling, stopped: !isPolling };
}
