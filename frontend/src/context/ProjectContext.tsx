/**
 * ProjectContext — the active project is a first-class UI context (decision §一).
 * The workspace header, sidebar, and every project-scoped page read the active
 * project from here. Selection persists across reloads via localStorage.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useProjects } from '../hooks/useProjects';
import type { ProjectOut } from '../api/types';

const STORAGE_KEY = 'rageval.activeProjectId';

interface ProjectContextValue {
  projects: ProjectOut[];
  total: number;
  loading: boolean;
  error: unknown;
  activeProject: ProjectOut | null;
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
  reload: () => Promise<void>;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const { projects, total, loading, error, reload: reloadProjects } = useProjects();
  const [activeProjectId, setActiveProjectIdState] = useState<string | null>(() =>
    typeof window === 'undefined' ? null : window.localStorage.getItem(STORAGE_KEY),
  );
  // Freeze Gate (Chrome E2E) finding: the auto-correct effect used to validate a
  // JUST-selected id against a STALE project list (reload still in flight after
  // creating a project), silently resetting the workspace to another project.
  // An explicit selection newer than the loaded snapshot is trusted until the
  // next completed load proves it invalid.
  const loadedAtRef = useRef(0);
  const selectedAtRef = useRef(0);

  const reload = useCallback(async () => {
    await reloadProjects();
    loadedAtRef.current = Date.now();
  }, [reloadProjects]);

  // Auto-heal only when the selection is not newer than the loaded snapshot
  // (fresh mount with a stale localStorage id, or a genuinely deleted project).
  useEffect(() => {
    if (projects.length === 0) return;
    if (projects.some((p) => p.id === activeProjectId)) return;
    if (selectedAtRef.current > loadedAtRef.current) return;
    setActiveProjectIdState(projects[0].id);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, projects[0].id);
    }
  }, [projects, activeProjectId]);

  const select = useCallback((id: string | null) => {
    selectedAtRef.current = Date.now();
    setActiveProjectIdState(id);
    if (typeof window !== 'undefined') {
      if (id) window.localStorage.setItem(STORAGE_KEY, id);
      else window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const value = useMemo<ProjectContextValue>(() => {
    const activeProject = projects.find((p) => p.id === activeProjectId) ?? null;
    return {
      projects,
      total,
      loading,
      error,
      activeProject,
      activeProjectId,
      setActiveProjectId: select,
      reload,
    };
  }, [projects, total, loading, error, activeProjectId, select, reload]);

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error('useProject must be used within ProjectProvider');
  return ctx;
}
