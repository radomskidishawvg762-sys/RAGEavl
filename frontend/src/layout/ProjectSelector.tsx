/**
 * ProjectSelector — the active-project switcher (decision §一). Reading the
 * project list from useProject; selecting a project sets the global active
 * context. Renders the project name + domain; shows "No project" when the list
 * is empty (no fake project).
 */

import { useEffect, useRef, useState } from 'react';
import { useProject } from '../context/ProjectContext';
import { Icon } from '../components/Icon';
import styles from './ProjectSelector.module.css';

export function ProjectSelector() {
  const { projects, activeProject, setActiveProjectId, loading } = useProject();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div className={styles.wrap} ref={ref}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setOpen((v) => !v)}
        data-testid="project-selector"
      >
        <span className={styles.projectIcon}><Icon name="spark" size={14} /></span>
        <span className={styles.meta}>
          <span className={styles.name}>
            {loading ? '加载中…' : activeProject?.name ?? '未选择项目'}
          </span>
          <span className={styles.domain}>
            {activeProject ? (activeProject.domain || 'general') : '请选择项目'}
          </span>
        </span>
        <span className={styles.caret}><Icon name="arrow-down" size={12} /></span>
      </button>

      {open ? (
        <div className={styles.menu} role="listbox">
          {projects.length === 0 ? (
            <div className={styles.empty}>暂无项目</div>
          ) : (
            projects.map((p) => (
              <button
                key={p.id}
                type="button"
                role="option"
                aria-selected={p.id === activeProject?.id}
                className={styles.option}
                onClick={() => {
                  setActiveProjectId(p.id);
                  setOpen(false);
                }}
              >
                <span className={styles.optionName}>{p.name}</span>
                <span className={styles.optionDomain}>{p.domain || 'general'}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
