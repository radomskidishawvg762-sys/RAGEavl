/**
 * ValidationReport — 数据集校验报告。展示 5 类校验（Schema / 重复 / 缺失字段 /
 * 引用有效性 / 领域元数据）。每个检查：名称、通过/失败、数量、问题列表，
 * 问题定位到 row_index（原始数据定位信息，不可隐藏）。校验语义来自后端，
 * 前端不自行判定。
 */

import type { DatasetValidationCheck, DatasetValidationResponse } from '../../api/types';
import styles from './ValidationReport.module.css';

const CHECK_LABEL: Record<string, string> = {
  schema: 'Schema 校验',
  duplicate: '重复检测',
  missing_field: '缺失字段检测',
  reference: '引用有效性校验',
  domain_metadata: '领域元数据校验',
};

export function ValidationReport({ validation }: { validation: DatasetValidationResponse | null }) {
  if (!validation) {
    return <div className={styles.empty}>暂无校验报告</div>;
  }
  const checks = validation.checks ?? [];
  if (checks.length === 0) {
    return <div className={styles.empty}>该数据集未执行校验。</div>;
  }

  return (
    <div className={styles.report} data-valid={validation.valid}>
      <div className={styles.summary} data-testid="validation-summary">
        <span className={styles.summaryIcon}>{validation.valid ? '✓' : '✕'}</span>
        <span className={styles.summaryText}>
          {validation.valid ? '校验通过' : '校验失败'}
        </span>
      </div>
      <div className={styles.checks} data-testid="validation-checks">
        {checks.map((check) => (
          <CheckRow key={check.name} check={check} />
        ))}
      </div>
    </div>
  );
}

function CheckRow({ check }: { check: DatasetValidationCheck }) {
  const label = CHECK_LABEL[check.name] ?? check.name;
  const passed = check.passed;
  return (
    <div className={styles.check} data-testid={`check-${check.name}`} data-passed={passed}>
      <div className={styles.checkHeader}>
        <span className={styles.checkName}>{label}</span>
        <span className={styles.checkTag} data-tone={passed ? 'pass' : 'fail'}>
          {passed ? '通过' : '失败'}
        </span>
        {check.count !== null && passed ? (
          <span className={styles.checkCount}>{check.count}</span>
        ) : null}
      </div>
      {passed ? null : (
        <div className={styles.problems}>
          <div className={styles.problemHead}>
            问题数量：{check.issues.length}
            {check.count !== null ? ` · 受影响 ${check.count} 条` : ''}
          </div>
          <GroupedIssues issues={check.issues} />
        </div>
      )}
    </div>
  );
}

function GroupedIssues({ issues }: { issues: Array<{ field: string; detail: string; row_index: number }> }) {
  const byField = new Map<string, Array<{ row_index: number; detail: string }>>();
  for (const issue of issues) {
    if (!byField.has(issue.field)) byField.set(issue.field, []);
    byField.get(issue.field)!.push(issue);
  }
  return (
    <div className={styles.grouped}>
      {Array.from(byField.entries()).map(([field, rows]) => (
        <div key={field} className={styles.fieldGroup}>
          <div className={styles.fieldName}>缺少字段：<code>{field}</code></div>
          <div className={styles.rowList}>
            影响记录：
            {rows.map((r) => (
              <span key={`${field}-${r.row_index}`} className={styles.rowChip} data-testid="issue-row-index">
                #{r.row_index}
                {r.detail ? <span className={styles.rowDetail}>（{r.detail}）</span> : null}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
