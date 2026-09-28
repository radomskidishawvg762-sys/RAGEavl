import type { ReactNode } from 'react';
import styles from './Bilingual.module.css';

export function BilingualLabel({ zh, en, className = '' }: {
  zh: ReactNode;
  /** Optional: some call sites only have a zh string. When absent the English
   *  half (and its separator) is omitted rather than substituted with a
   *  placeholder — EmptyState used to hardcode en="Status", so all 36 empty
   *  states rendered "<title> Status". */
  en?: ReactNode;
  className?: string;
}) {
  return (
    <span className={[styles.label, className].filter(Boolean).join(' ')}>
      <span className={styles.zh} lang="zh-CN">{zh}</span>
      {en ? (
        <>
          <span className={styles.separator} aria-hidden="true"> </span>
          <span className={styles.en} lang="en">{en}</span>
        </>
      ) : null}
    </span>
  );
}

export function BilingualText({ zh, en, className = '' }: {
  zh: ReactNode;
  en: ReactNode;
  className?: string;
}) {
  return (
    <span className={[styles.text, className].filter(Boolean).join(' ')}>
      <span className={styles.zh} lang="zh-CN">{zh}</span>
      <span className={styles.enLine} lang="en">{en}</span>
    </span>
  );
}
