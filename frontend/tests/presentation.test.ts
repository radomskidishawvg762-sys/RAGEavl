import { expect, it } from 'vitest';
import {
  comparisonReasonLabel,
  diagnosisTextLabel,
  evidenceTypeLabel,
  REASON_EXPLAIN,
  scrubProviderText,
} from '../src/api/mappers';

it('报错详情脱敏 URL、独立密钥、Bearer 和账号字段', () => {
  const raw = '连接 https://test.invalid/api 失败 sk-reviewSecret123456 authorization: Bearer reviewBearer123456 api_key=reviewKey123456 account_id=reviewAccount123456';
  const text = scrubProviderText(raw);
  for (const secret of ['https://test.invalid', 'sk-reviewSecret123456', 'reviewBearer123456', 'reviewKey123456', 'reviewAccount123456']) {
    expect(text).not.toContain(secret);
  }
  expect(text).toContain('连接');
  expect(text).toContain('失败');
});

it.each([
  ['reference_evidence.comparable', '参考证据的可比较性'],
  ['answer_claim.checkable', '可核验的答案声明'],
  ['comparison_basis.diff', '比较依据的差异记录'],
  ['retrieval_metadata', '检索元数据'],
  ['semantic_evidence', '语义证据'],
  ['comparison_basis', '比较依据'],
  ['comparison_basis.deterministic_comparison_type', '确定性比较类型'],
  ['reference_contexts', '参考上下文'],
])('已知缺失证据路径使用中文：%s', (key, label) => {
  expect(evidenceTypeLabel(key)).toBe(label);
});

it('未知证据类型保留原始值，便于定位新增后端字段', () => {
  expect(evidenceTypeLabel('future_evidence.path')).toBe('future_evidence.path');
});


it('JSON 格式的鉴权字段和账号字段也脱敏', () => {
  const text = scrubProviderText(JSON.stringify({ Authorization: 'Bearer reviewBearer123456', api_key: 'reviewKey123456', account_id: 'reviewAccount123456' }));
  expect(text).not.toMatch(/reviewBearer|reviewKey|reviewAccount/);
});


it('只有参考答案时的检索无法判定原因使用中文', () => {
  expect(diagnosisTextLabel(
    'only reference_answer on record (no reference_contexts) — retrieval attribution forbidden',
  )).toBe('记录上只有参考答案、没有参考上下文（reference_contexts）—— 禁止检索归因');
});

it('指标覆盖不一致的可比性原因使用中文', () => {
  expect(comparisonReasonLabel(
    "metric coverage differs (paired on common scored records only): ['faithfulness']",
  )).toBe('指标有效覆盖不一致（仅按两侧都有分数的记录配对计算）：faithfulness');
});

it('新增门禁原因码有中文说明', () => {
  expect(REASON_EXPLAIN.RUN_NOT_COMPLETED).toBeTruthy();
  expect(REASON_EXPLAIN.REQUIRED_METRIC_DETERMINISTIC_FAILURE).toBeTruthy();
});

it('根因中的事实字段按词边界转换为中文', () => {
  expect(diagnosisTextLabel('Numerical Mismatch (abs_diff=100)')).toBe('数值不一致 (绝对差=100)');
});
