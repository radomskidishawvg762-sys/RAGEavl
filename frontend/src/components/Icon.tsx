/**
 * Minimal inline SVG icon set (no icon library dependency). Sizes are controlled
 * by the layout module's font-size via `em`, so an icon inherits its container.
 */

import type { IconName } from '../status/status';

const PATHS: Record<IconName, JSX.Element> = {
  circle: <circle cx="12" cy="12" r="8" />,
  check: <path d="M4 12.5l5 5 11-11" />,
  cross: <path d="M6 6l12 12M18 6L6 18" />,
  warning: <path d="M12 3l9 16H3zM12 9v5M12 17.5v.5" />,
  alert: <path d="M12 3l9 16H3zM12 10v4M12 17.5v.5" />,
  question: <path d="M9 9a3 3 0 115.5 2c-.8.9-2.5 1.2-2.5 3M12 18v.5" />,
  minus: <path d="M5 12h14" />,
  'arrow-up': <path d="M12 19V5M6 11l6-6 6 6" />,
  'arrow-down': <path d="M12 5v14M6 13l6 6 6-6" />,
  'arrow-right': <path d="M5 12h14M13 6l6 6-6 6" />,
  lock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 018 0v3" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8v.5" />
    </>
  ),
  spark: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
};

export function Icon({ name, size = 14 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
