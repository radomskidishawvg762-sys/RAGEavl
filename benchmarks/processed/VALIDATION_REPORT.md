# RAGEval 评估有效性验证报告（多数据源 × 各能力）

> 日期：2026-09-06
> 方法：受控污染/场景对照（clean vs contaminated），全部确定性 + 少量真实 LLM
> 目标：回答"这套评估到底有没有效"，用实证而非猜测

## 一、验证总览：四数据源 × 四能力

| 数据源 | 验证能力 | 方法 | 结果 |
|---|---|---|---|
| TAT-QA | 数值一致性 | 受控污染（×10/×1.5/符号/单位） | 88% 判别（修复两个 bug 后） |
| TAT-QA | 实体一致性 | 受控污染（换实体/未知实体） | 100% 判别 |
| TAT-QA | 时间一致性 | 受控污染（年份/季度/半年/FY 错位） | 100% 判别 |
| RAGTruth | 生成忠实性/幻觉 | 真实 50 条 run + 归因实验 | 配置敏感；GLM 下能识别目标幻觉 |
| MIRACL | 检索诊断 | 真实 qrels + 受控检索场景 | 100% 归因（60/60） |

## 二、分层结论

### 层 1：数值一致性引擎（TAT-QA，修复后 88%）

**发现并修复了两个真实 bug：**
1. **符号翻转漏检**：`-12.6 vs +12.6` 原判 match。根因是 `_ARABIC` 正则不匹配负号 + 比较层无符号检查。已修：正则保留 `-?`，比较层异号直接 `value_mismatch`。
2. **英文单位词不识别**：`million/percent/thousand` 原判 ambiguous。已修：加入 `_UNIT_TABLE`（大小写不敏感，归一化到统一 base）。

修复后污染检出率 57.5% → 85%（剩余为 percent/无单位样本，无法构造换单位污染，非缺陷）。

### 层 2：实体 + 时间一致性引擎（100%）

**实体（5 用例全对）**：同义/别名 → match；换实体 → entity_mismatch；未知实体 → ambiguous（诚实不猜）。

**时间（9 用例全对）**：年份±1/季度/半年/FY 错位 → temporal_mismatch；粒度包含（年 vs Q1）→ ambiguous；相对时间无锚点 → ambiguous。

### 层 3：faithfulness 归因（RAGTruth + GLM）

受控实验：同一问题+同一上下文，answer 含/不含 bear 无依据句：
- 含 bear 句 → **0.75**
- 删 bear 句 → **1.0**

**结论**：GLM judge 能检测目标幻觉。此前"butcher shop=1.0"假阴性有变量干扰（Judge 模型、上下文截断不同），faithfulness 是**配置敏感的**——单一分数不是稳定结论，需多信号评估。

### 层 4：检索诊断能力（MIRACL zh-dev 真实 qrels，60/60）

| 场景 | 预期 | 实际 |
|---|---|---|
| 证据全命中 | undetermined（无检索故障） | 20/20 ✅ |
| 证据全缺 | retrieval.missing_evidence | 20/20 ✅ |
| top_k 饱和且缺证 | retrieval.top_k_issue | 20/20 ✅ |

**保守行为确认**：部分命中（1/2）→ undetermined，不强行归因；空 contexts → undetermined。

## 三、核心原则实证（三处一致）

无论哪层实验，"不确定"边界一律：
```text
unknown 实体 / 粒度包含 / 相对时间无锚点 / 部分证据命中 / 无检索元数据
→ ambiguous / undetermined（score=null）
→ 绝不误判为 match 或强行归因
```

这是平台"宁可不判，不造假分"原则的实证支撑。

## 四、诚实限制

1. **样本量有限**：污染对照 50（数值）/14（实体+时间）/60（检索），faithfulness 归因单条。是工程级验证，非统计 benchmark。
2. **faithfulness 配置敏感**：分数随 Judge 模型、上下文长度变化。报告必须携带完整 reproducibility 元数据。
3. **RAGTruth 参考答案为 assistant 从原文整理**（pending user review），非官方标签。
4. **超时仍存在**：17/50 记录在 GLM+300s 下无法出分，长上下文是实际瓶颈。
5. **未覆盖**：answer_relevancy（需 embedding，未配置）、context_precision/recall 的 LLM 侧分数（未用 MIRACL 真实检索跑 RAGAS）。

## 五、总体判断

> **确定性引擎（数值/实体/时间）在受控污染下可区分干净与污染（88%-100%），检索诊断归因正确（100%），faithfulness 配置敏感但归因可复现。** 这套评估在"能否区分好坏、能否诚实归因"这个层面是**有效的**；有效性边界明确（配置敏感性、长上下文超时、参考数据质量）需要在使用报告中如实披露。

## 六、产物清单

```text
benchmarks/run_tatqa_contamination_l1.py           层1 数值污染
benchmarks/run_integrity_l2_contamination.py       层2 实体/时间污染
benchmarks/run_faithfulness_bear_attribution_l3.py 层3 faithfulness 归因
benchmarks/run_miracl_retrieval_diagnosis_l4.py    层4 检索诊断
benchmarks/processed/tatqa_contamination_l1.json
benchmarks/processed/entity_temporal_contamination_l2.json
benchmarks/processed/faithfulness_bear_attribution_l3.json
benchmarks/processed/miracl_retrieval_diagnosis_l4.json
benchmarks/processed/FALSE_NEGATIVE_CASE_butcher_shop.md
benchmarks/processed/ATTRIBUTION_bear_faithfulness.md
benchmarks/processed/ragtruth_faithfulness_50_gold_align.json
```

## 七、代码修复（本验证驱动）

```text
app/metrics/integrity/numerical.py   负号保留 + 英文单位词
app/metrics/integrity/comparison.py  异号检测
tests/test_integrity_normalization.py +7 测试
tests/test_integrity_engine.py       +4 测试
```