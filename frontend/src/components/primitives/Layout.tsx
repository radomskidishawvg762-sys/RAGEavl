/**
 * Layout primitives (Phase 2, decision §九): the shared composition layer.
 * Components use the spacing/typography/radius tokens — no hardcoded values.
 */

import type { CSSProperties, ReactNode } from 'react';
import styles from './Layout.module.css';

type Gap = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'none';
const GAP: Record<Gap, string> = {
  xs: 'var(--sp-1)',
  sm: 'var(--sp-2)',
  md: 'var(--sp-3)',
  lg: 'var(--sp-4)',
  xl: 'var(--sp-5)',
  none: '0',
};

function gapStyle(gap: Gap): CSSProperties {
  return gap === 'none' ? ({ gap: 0 } as CSSProperties) : ({ gap: GAP[gap] } as CSSProperties);
}

export function Stack({
  children,
  gap = 'md',
  direction = 'vertical',
  style,
  className,
}: {
  children: ReactNode;
  gap?: Gap;
  direction?: 'vertical' | 'horizontal';
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <div
      className={[styles.stack, direction === 'horizontal' ? styles.row : '', className].join(' ')}
      style={{ ...gapStyle(gap), ...style }}
    >
      {children}
    </div>
  );
}

export function Grid({
  children,
  cols = 2,
  gap = 'lg',
  style,
  className,
}: {
  children: ReactNode;
  cols?: number;
  gap?: Gap;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <div
      className={[styles.grid, className].join(' ')}
      // cols via custom property (NOT inline grid-template-columns) so the
      // stylesheet can clamp column count on small screens (mobile acceptance)
      style={{ ['--grid-cols' as string]: String(cols), ...gapStyle(gap), ...style }}
    >
      {children}
    </div>
  );
}

export function Divider({ style }: { style?: CSSProperties }) {
  return <div className={styles.divider} style={style} />;
}
