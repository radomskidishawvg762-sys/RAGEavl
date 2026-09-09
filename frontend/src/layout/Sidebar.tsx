/**
 * Sidebar — left navigation (Phase 2 产品闭环 IA). Project-scoped (renders
 * under the active project context). All routes are real pages — no gap tags.
 */

import { NavLink } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { BilingualLabel } from '../components/primitives/Bilingual';
import { useProject } from '../context/ProjectContext';
import { NAV_SECTIONS } from './nav';
import styles from './Sidebar.module.css';

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { activeProject } = useProject();

  return (
    <nav className={styles.sidebar} aria-label="primary">
      <div className={styles.brand}>
        <span className={styles.brandMark}>R</span>
        <span className={styles.brandText}>
          <span className={styles.brandName}>RAGEval</span>
          <span className={styles.brandSub}>Studio</span>
        </span>
      </div>
      {activeProject ? (
        <div className={styles.projectMarker}>
          <span className={styles.markerLabel}>项目</span>
          <span className={styles.markerName}>{activeProject.name}</span>
        </div>
      ) : null}

      {NAV_SECTIONS.map((section) => (
        <div key={section.key} className={styles.section}>
          <div className={styles.sectionLabel}>
            <BilingualLabel zh={section.labelZh} en={section.labelEn} />
          </div>
          <div className={styles.items}>
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onNavigate}
                className={({ isActive }) =>
                  [styles.item, isActive ? styles.active : ''].join(' ')
                }
              >
                <span className={styles.itemIcon}><Icon name={item.icon} /></span>
                <span className={styles.itemLabel}>
                  <BilingualLabel zh={item.labelZh} en={item.labelEn} />
                </span>
              </NavLink>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}
