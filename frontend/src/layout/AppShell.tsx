/**
 * AppShell — the product chrome (Phase 2, decision §一/§八): left sidebar
 * (brand + project-scoped nav) + top workspace header (project selector) +
 * routed content. All pages render inside <Outlet/> so the shell, nav state,
 * and project context are consistent everywhere.
 *
 * Responsive (visual acceptance fix): ≤768px the sidebar becomes an overlay
 * drawer toggled from the mobile bar. Desktop rendering is unchanged.
 */

import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { WorkspaceHeader } from './WorkspaceHeader';
import styles from './AppShell.module.css';

function MenuIcon({ open }: { open: boolean }) {
  return (
    <svg
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
    >
      {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
    </svg>
  );
}

export function AppShell() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();

  // route change closes the mobile drawer
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  return (
    <div className={styles.shell}>
      <div className={[styles.sidebarColumn, navOpen ? styles.sidebarOpen : ''].join(' ')}>
        <Sidebar onNavigate={() => setNavOpen(false)} />
      </div>
      <div
        className={[styles.backdrop, navOpen ? styles.backdropOpen : ''].join(' ')}
        onClick={() => setNavOpen(false)}
        aria-hidden="true"
      />
      <div className={styles.main}>
        <div className={styles.mobileBar}>
          <span className={styles.mobileBrand}>RAGEval Studio</span>
          <button
            type="button"
            className={styles.mobileToggle}
            aria-label={navOpen ? '关闭导航 Close navigation' : '打开导航 Open navigation'}
            aria-expanded={navOpen}
            onClick={() => setNavOpen((v) => !v)}
          >
            <MenuIcon open={navOpen} />
          </button>
        </div>
        <WorkspaceHeader />
        <main className={styles.content}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
