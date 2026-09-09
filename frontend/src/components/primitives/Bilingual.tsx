import type { ReactNode } from 'react';
import styles from './Bilingual.module.css';

export function BilingualLabel({ zh, en, className = '' }: {
  zh: ReactNode;
  en: ReactNode;
  className?: string;
}) {
  return (
    <span className={[styles.label, className].filter(Boolean).join(' ')}>
      <span className={styles.zh} lang="zh-CN">{zh}</span>
      <span className={styles.separator} aria-hidden="true"> </span>
      <span className={styles.en} lang="en">{en}</span>
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
