# PHASE 9 - Evaluation Coverage & Quality Model Completion

> 项目：RAGEval Studio  
> 范围：Coverage Model、真实多指标 Run、维度级报告、Improvement Loop 审计  
> 原则：Evidence-first、Deterministic-first、score != root cause、证据不足保持 undetermined  
> 变更边界：仅批准的 `ReportResponse.quality_dimensions` 增量；未引入新 Metric、Diagnosis Rule、数据库 Schema、队列基础设施或 LLM Diagnosis。

## 1. Executive Summary

本阶段完成了核心 RAG 质量维度模型，并真实运行了包含 Retrieval 指标的 50 条 Run。

本阶段确认：

- `MetricRegistry` 继续是代码注册、只读模型。
- `context_precision` 和 `context_recall` 已进入真实 Run。
- `faithfulness` 在本阶段 Run 中有有效结果，但部分调用受到 OpenCode Go `429 GoUsageLimitError` 影响。
- `answer_relevancy` 仍未启用，因为当前 OpenCode Go 网关的 `/embeddings` 返回 404。
- 新增的维度聚合只读取已经持久化的 metric、failure、diagnosis、undetermined，不重新执行 Engine、RAG 或 Judge。
- Overall Score 仍按既有业务逻辑计算，但 Overview 现在同时展示四个 Quality Dimensions，避免总体分数掩盖局部短板。
- 证据不足没有被转化成 Failure 或 Diagnosis。

本阶段的准确结论是：

> **核心 Coverage Model 已完成，Retrieval 评估已真实接入，维度级报告已生效；但完整的“所有指标无外部依赖错误”以及严格同数据集版本的改善对比，受 Judge 配额和数据版本不可变规则限制，不能宣称全部完成。**

## 2. Quality Coverage Matrix

| Dimension | Metric / Signal | Implemented | Real Run | Failure | Diagnosis | Evidence | Recommendation | Status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Retrieval | `context_precision` | Yes | Yes, partially | Threshold path exists | Retrieval rules exist | Required when diagnosed | Retrieval templates exist | PARTIAL |
| Retrieval | `context_recall` | Yes | Yes, partially | Threshold path exists | Retrieval rules exist | Required when diagnosed | Retrieval templates exist | PARTIAL |
| Retrieval | Evidence coverage | Yes | Yes | Yes when all gold evidence is absent | `retrieval.missing_evidence` | `retrieval.missing_evidence.v1` | Yes | VERIFIED |
| Retrieval | Top-K behavior | Yes | Scenario validation | Yes when cap is saturated and gold evidence is missing | `retrieval.top_k_issue` | `retrieval.top_k_issue.v1` | Yes | VERIFIED |
| Generation | `faithfulness` | Yes | Yes | Profile threshold can trigger | Generation rules are evidence-gated | Evidence required | Unsupported-claim template exists | PARTIAL |
| Generation | `answer_relevancy` | Yes in RAGAS registry | No | Not evaluated | Not evaluated | Not evaluated | Not evaluated | BLOCKED - embedding provider |
| Generation | Completeness | No standalone Metric | No | No standalone signal | No standalone diagnosis | No | No | OUT_OF_CURRENT_SCOPE |
| Generation | Unsupported claim | Diagnosis check exists | Scenario validated | Threshold failure is only a trigger | `generation.unsupported_claim` | Contract v1 | Yes | PARTIAL |
| Groundedness | Faithfulness | Yes | Yes | Profile threshold can trigger | Evidence-gated | Evidence contract | Yes | PARTIAL |
| Groundedness | Claim support | Deterministic numeric path | Yes for numeric claims | Can diagnose supported mismatch cases | Non-numeric claims may remain undetermined | Trace items exist | Yes when rule is conclusive | PARTIAL |
| Correctness | `entity_consistency` | Yes | Yes | Canonical mismatch | Integrity diagnosis | Integrity contract | Yes | VERIFIED WITH ALIAS INPUT |
| Correctness | `temporal_consistency` | Yes | Yes | Interval mismatch | Integrity diagnosis | Integrity contract | Yes | VERIFIED |
| Correctness | `numerical_consistency` | Yes | Yes | Value/unit/scale mismatch | Integrity diagnosis | Comparison basis | Yes | VERIFIED AFTER FIX |

### Registry evidence

当前代码注册了 7 个指标：

```text
RAGAS:
  faithfulness
  answer_relevancy
  context_precision
  context_recall

Integrity:
  entity_consistency
  temporal_consistency
  numerical_consistency
```

没有新增 Metric，也没有把数据库表变成注册入口。

## 3. Retrieval Coverage

本阶段使用 RAGTruth golden 数据运行了 6 个指标：

```text
context_precision
context_recall
faithfulness
entity_consistency
temporal_consistency
numerical_consistency
```

`answer_relevancy` 被明确禁用。

真实 Run：

```text
run_id: 7be009fe-7ed5-48e3-a632-daaeb9f6c83f
dataset_version: v5
run status: completed
record progress: 50/50
```

报告中的 Retrieval 结果：

```text
context_precision: 0.942857
valid rows: 35
metric errors: 15

context_recall: 1.000000
valid rows: 32
metric errors: 18
```

这里的 `metric errors` 来自外部 Judge 配额 `429 GoUsageLimitError`，不是 Retrieval 算法本身的质量结论。由于 RAGAS Retrieval 指标依赖 Judge，不能把有效样本均值解释成无条件的完整数据集均值。

此前 MIRACL 场景验证已经确认：

```text
gold evidence 全命中 -> undetermined，不错误归因
gold evidence 全缺失 -> retrieval.missing_evidence
top_k 饱和且 gold evidence 缺失 -> retrieval.top_k_issue
```

结果：60/60 场景判定正确。

## 4. Generation Coverage

### Faithfulness

本阶段真实 Run 的结果：

```text
faithfulness: 0.685655
valid rows: 40
metric errors: 10
```

错误原因是外部 OpenCode Go 套餐在 Run 期间触发：

```text
429 GoUsageLimitError
Weekly usage limit reached
```

这意味着：

- RAGAS faithfulness 的代码路径已真实执行。
- 有效样本可以产生分数。
- 该次 Run 不能作为“50 条完整无误 faithfulness benchmark”。
- 配额错误必须继续作为 `EXT_JUDGE_UNAVAILABLE`，不能改成低分或 undetermined 伪装成质量判断。

### Answer Relevancy

当前没有启用，原因已经验证：

```text
OpenCode Go /embeddings -> 404
```

代码中已有 `EmbeddingsClient` Protocol 和 `PlaceholderEmbeddings`，但没有真实 embedding provider。没有引入新的 Provider，因为这会超出本阶段最小修改边界。

状态：

```text
BLOCKED - 缺独立 Embedding Provider
```

## 5. Groundedness Coverage

Groundedness 当前主要由以下信号组成：

```text
faithfulness
确定性 numerical claim support
retrieval evidence coverage
```

层 3 受控实验结果：

```text
同一上下文、保留 bear 无依据句：faithfulness = 0.75
删除 bear 无依据句：faithfulness = 1.0
```

这证明 Judge + RAGAS 在该配置下能够感知目标非数值幻觉，但不能推导出所有幻觉类型都能被稳定检测。

当前边界：

- 数值 claim 可以通过确定性路径比较。
- 非数值 claim 仍依赖 Judge。
- Evidence 不充分时保持 undetermined。
- 不能仅凭低 faithfulness 直接决定具体参数。

## 6. Correctness Coverage

### Entity

实体通过 Profile 的 `alias_table` 提供 canonical 映射。

真实开放域英文数据没有统一 alias table 时：

```text
unknown entity -> ambiguous
```

这是正确的不确定性处理，不是要通过内置知识图谱强行补全。

层 2 实验：5/5 正确。

### Temporal

时间表达先解析成闭区间，再比较：

```text
年份错位 / 季度错位 / 半年错位 / FY 错位 -> temporal_mismatch
年份与季度包含 -> ambiguous
无锚点的去年/今年 -> ambiguous
```

层 2 实验：9/9 正确。

### Numerical

本阶段之前已修复：

- 负号被正则丢失。
- 英文数量级单位不识别。
- 英文句子中普通单词首字母被误判为未知单位。

层 1 修复后污染对照：

```text
clean accuracy: 100%
polluted detection rate: 85%
overall accuracy: 88%
```

当前真实 Run 的 numerical 结果：

```text
numerical_consistency: 0.583333
valid rows: 36
undetermined rows: 14
diagnosed failures: 15
```

这里的 15 个 mismatch 是基于实际 answer/reference 的确定性比较，不应为了降低失败数量而放宽。

## 7. Dimension Model

后端新增了 presentation-only 的 `quality_dimensions` 聚合，维度映射如下：

```text
Retrieval:
  context_precision
  context_recall

Generation:
  faithfulness
  answer_relevancy

Groundedness:
  faithfulness

Correctness:
  entity_consistency
  temporal_consistency
  numerical_consistency
```

`faithfulness` 同时属于 Generation 和 Groundedness，因为它既反映回答生成质量，也反映回答是否被召回上下文支撑。

真实报告返回的维度结果：

```text
Retrieval:
  score = 0.9714
  undetermined = 33

Generation:
  score = 0.6857
  undetermined = 10

Groundedness:
  score = 0.6857
  undetermined = 10

Correctness:
  score = 0.8611
  failures = 15
  undetermined = 22
  diagnosed = 15
```

这已经可以避免 `overall_score` 单独掩盖局部问题：用户会直接看到 Retrieval、Generation、Groundedness、Correctness 的差异。

前端 Evaluation Overview 已增加：

```text
维度 Dimension
分数 Score
失败 Failures
无法判定 Undetermined
指标 Metrics
```

Correctness 当前是最需要关注的维度，虽然总体分数仍可能较高。

## 8. Undetermined / Error Semantics

本阶段没有把以下状态混为一种：

```text
ambiguous / missing_reference
  -> evidence insufficient / undetermined

EXT_JUDGE_UNAVAILABLE / SYS_METRIC_ERROR
  -> execution error

deterministic mismatch
  -> confirmed failure

score + threshold=null
  -> scored but no PASS/FAIL judgment
```

特别说明：本阶段真实 Run 的报告中出现了大量 Judge `429` 相关的 metric errors。它们属于执行错误，不能计入生成质量差，也不能计入“真正证据不足”。

## 9. Diagnosis Coverage

当前已实现并验证的 Diagnosis 类型：

```text
Integrity:
  integrity.numerical_mismatch
  integrity.temporal_mismatch
  integrity.entity_mismatch
  integrity.unit_mismatch

Retrieval:
  retrieval.missing_evidence
  retrieval.top_k_issue

Generation:
  generation.unsupported_claim
  generation.partial_answer
```

规则仍然遵守：

```text
score 不直接生成 root cause
metric 只提供触发信号
Evidence Contract 满足后才能 diagnosed
否则 undetermined
```

已有场景验证结果：

```text
Integrity entity/temporal：14/14
Retrieval diagnosis：60/60
```

## 10. Evidence Coverage

已诊断结果携带 Evidence Contract。当前真实报告中的 15 个 correctness failures 均产生了诊断和证据；证据包含比较依据，例如：

```text
reference value
answer value
base value
absolute difference
unit class
comparison type
```

Retrieval 和 Generation 诊断也已经定义对应 v1 contract，但只有在确定性证据满足时才会生成诊断。

没有因为“希望报告更完整”而为 22 条 correctness undetermined 强行生成 Evidence 或 root cause。

## 11. Recommendation Coverage

规则到 Recommendation 的映射已经存在：

```text
integrity numerical mismatch -> 检查数值抽取/单位归一化/生成约束
integrity temporal mismatch -> 检查报告期识别/时间约束
integrity entity mismatch -> 检查实体消歧/Metadata/Context
retrieval missing evidence -> 检查召回/top_k/filter/排序
retrieval top_k issue -> 提高 top_k 或优化召回
generation unsupported claim -> 检查 grounding 约束
generation partial answer -> 检查答案要点覆盖
```

Recommendation 是规则驱动的行动参考，不宣称能够在所有场景自动确定唯一参数。

## 12. Improvement Loop

本阶段实际完成了：

```text
Run A
  -> Audit
  -> 定位 reference 中的 passage N: 数字噪声
  -> 删除该数据构造噪声
  -> Run B
```

并验证：

- 修复后的 Run 能够完成 50/50 record progress。
- `faithfulness` 相关执行错误的主要外部原因是 Judge 配额 429，而不是把错误改成分数。
- 修复数据噪声后，数值 mismatch 更真实地暴露出来。

但严格的 `Compare / Regression / Quality Gate` 结论需要说明限制：

```text
Run A 与 Run B 使用不同 dataset version
Compare 正确返回 BLOCKED
```

这是 Dataset Versioning 和历史可复现规则的正确行为。不能把不同数据集版本直接称为可比的 Candidate 改进。

当前 Profile 没有配置 `quality_gate`，因此 Quality Gate 的最终状态是：

```text
NOT_EVALUABLE / 未配置
```

不能仅依据 Overall Score 伪造 PASS。

## 13. Before / After

本阶段可确认的 Before / After：

| 项目 | Before | After | 结论 |
|---|---:|---:|---|
| faithfulness valid/error | 40/10（受 429 影响的 Run） | 40/10（同一配额窗口中的后续报告） | 外部配额仍是限制，不作质量提升结论 |
| context_precision | 未真实启用 | 0.942857，有 15 条外部错误 | Retrieval 指标已进入真实 Run，但有效覆盖不完整 |
| context_recall | 未真实启用 | 1.0，有 18 条外部错误 | 有效样本结果高，不能解释为完整数据集无误 |
| numerical scope | 英文普通单词首字母误判 ambiguous | 可识别更多英文无单位数字 | 作用域误判减少 |
| numerical failures | 7 | 15 | 数据噪声消除后更多真实 mismatch 被发现 |
| quality_dimensions | 无 | 四维度真实报告 | Overall Score masking 已改善 |

## 14. Remaining Gaps

### Required before claiming production-grade coverage

```text
1. 为 Judge 使用独立、稳定且配额可控的评估凭证。
2. 在没有 429 的环境中重新运行 context_precision/context_recall/faithfulness。
3. 为 answer_relevancy 提供独立 Embedding Provider。
4. 使用同一不可变 Dataset Version 完成 Run A/Run B，才能做严格 Compare/Regression。
5. 为 Quality Gate 配置明确的 Profile 级策略，再进行 Gate 验证。
```

### Honest uncertainty

```text
英文开放域实体没有 alias table -> undetermined
非数值 claim 的确定性支撑 -> 不能只依赖 numerical path
RAGAS Judge 结果 -> 对模型、Prompt、配额、版本敏感
```

## 15. Future Capability Decision

| Capability | Decision | Reason |
|---|---|---|
| New Diagnosis Rule | WAIT | 现有核心规则已有真实验证；需要更多真实错误样本后再加 |
| LLM Diagnosis | WAIT | 当前应先解决 Judge 稳定性和证据覆盖；禁止 score-only attribution |
| RAG Adapter v2 | WAIT | 当前接口足够表达已有 RAG 输出，尚无明确上游协议差异 |
| Redis | OUT_OF_MVP_SCOPE | 未发现任务丢失、重启恢复或队列积压证据 |
| Celery | OUT_OF_MVP_SCOPE | 同上，当前单进程模型符合 MVP 约束 |
| Multi Worker | OUT_OF_MVP_SCOPE | 没有多实例需求或可证明的吞吐瓶颈 |
| Custom Metric | OUT_OF_MVP_SCOPE | MetricRegistry 必须保持代码注册、只读 |
| Operational Metrics | OPTIONAL | 可增加耗时、配额、失败率观测，但不改变质量语义 |
| Robustness / Safety | OPTIONAL | 已有 Secret 脱敏和显式错误语义，可继续增强 |

## 16. Verification

### Backend

```text
核心后端测试：通过
Phase 9 维度测试：通过
Ruff：通过
```

### Frontend

```text
TypeScript typecheck：通过
Vitest：63 passed
Production build：通过
```

### Known test caveat

完整后端测试中有 3 个旧的外部依赖测试在当前环境失败：

```text
```

原因是它们依赖真实 Judge/RAG 外部调用，当前网络/外部服务环境不稳定；这不是本阶段 `quality_dimensions` 聚合逻辑的失败。PostgreSQL integration tests 因未配置独立 `TEST_DATABASE_URL` 被正确跳过，不计为通过。

## 17. Stop Condition

Phase 9 已完成本阶段允许的范围：

```text
Audit
-> Minimal Implementation
-> Tests
-> Real Multi-Metric Run
-> Dimension Report
-> Improvement Loop Audit
-> Completion Report
```

到此停止。不自动进入：

```text
LLM Diagnosis
RAG Adapter v2
Redis
Celery
Multi Worker
Custom Metric
```
