# Phase 8B — Evaluation Coverage Audit & Threshold Calibration Analysis

> 项目：RAGEval Studio
> 阶段：只读审计 + 阈值校准分析（**未修改任何 Metric / Diagnosis Rule / Registry / Adapter / Schema / API**）
> 数据：50/50 真实 RAG run（RAGTruth golden，GLM-5.3-flash，faithfulness + 3 integrity）
> 产出文件：`benchmarks/processed/phase8b_coverage_classification.json`

---

## 一、当前评估体系覆盖的 RAG Quality Dimensions

| 维度 | Metric | 引擎 | 判定方式 | 现状 |
|---|---|---|---|---|
| 生成忠实性 | `faithfulness` | RAGAS (LLM) | claim vs contexts 支持度 | 覆盖；配置敏感，2 条执行错误 |
| 回答相关性 | `answer_relevancy` | RAGAS (LLM+emb) | 语义相关 | **未覆盖**（缺 embedding，本 run 未启用） |
| 检索精确度 | `context_precision` | RAGAS (LLM) | 相关上下文排序 | 覆盖（未在本 run 启用） |
| 检索召回 | `context_recall` | RAGAS (LLM) | 参考证据召回完整度 | 覆盖（未在本 run 启用） |
| 数值一致性 | `numerical_consistency` | Integrity（确定性） | 归一化 base 比较 | ✅ 覆盖且有效 |
| 时间一致性 | `temporal_consistency` | Integrity（确定性） | 区间语义等价 | ✅ 覆盖且有效 |
| 实体一致性 | `entity_consistency` | Integrity（确定性） | 别名→canonical | ✅ 覆盖；依赖 alias table |

**诊断覆盖**：retrieval（missing_evidence / top_k_issue）、generation（unsupported_claim / partial_answer）、integrity（4 类）。

**结论**：当前体系覆盖 4 个 RAG 质量维度 + 8 个诊断类型。未覆盖：answer_relevancy（缺 embedding）、检索维度的 RAGAS 侧分数（本 run 未启用）。

---

## 二、25 条 Undetermined 分类（核心答案）

报告口径 25 条 undetermined + 7 条 failure + 2 条 faithfulness error，逐条归因到 metric 级 comparison_basis 后：

| 类别 | 数量 | 含义 | 归因 |
|---|---|---|---|
| **① 引擎能检测但当前作用域覆盖不到** | **13** | entity 无 alias table / numerical 英文单位不在 MVP 词典 | **scope gap**，非数据问题 |
| **② 真正证据不足** | **10** | comparison_type=ambiguous，两侧无可靠数值/时间可比较 | **真 undetermined**，正确行为 |
| **③ 执行错误（非判定）** | **2** | faithfulness SYS_METRIC_ERROR | **not evaluated** |
| **④ 阈值/Profile 未触发** | **0** | — | 见下文说明 |
| **⑤ 可测但无法解释（无规则）** | **0** | — | 见下文说明 |

### 关键澄清：threshold 与 undetermined 的关系

**Integrity 失败不依赖 threshold**：7 条 failure 全是 `score=0.0`（确定性 mismatch），`threshold=None, passed=None` 但**仍判为 failure**——因为 Integrity 的失败信号是 `comparison_type != match`，不是阈值比较。所以"阈值未触发"在本 run 中**不产生 undetermined**（integrity 维度）。

**faithfulness 的 threshold=null** 才是真正的"未触发"区：faithfulness 的分数（0.677 均值）在 threshold=null 时 `passed=None`，**不会触发 failure 判定**，也就不会进入 diagnosis。这意味着：

> faithfulness 的"低分"在默认 Profile 下**静默不报失败** —— 这是阈值校准的真正缺口，但它在当前 run 中**不表现为 undetermined**，而是表现为"scored 但无判定"。

### 用户四类问题的直接回答

1. **真正证据不足** → 10 条（ambiguous 无可靠比较依据）
2. **threshold/profile 未触发** → 0 条 undetermined（但 faithfulness 存在"低分未触发失败"的隐藏缺口，13 条数值 score 正常但 integrity 已按 mismatch 处理）
3. **metric 能检测但 diagnosis 不能解释** → 0 条（integrity mismatch 都能归因到 taxonomy；13 条 scope gap 属 metric 侧检测不到，不是 diagnosis 解释不了）
4. **体系确实无法判断** → 10 条（真 ambiguous）+ 13 条（scope gap，当前配置下无法判断）

---

## 三、失败明细（7 条，全部 integrity 确定性）

| 问题 | failure_type | 证据 |
|---|---|---|
| in word, how do i remove a row | numerical_mismatch | abs_diff=2006 |
| single cream vs double cream | unit_mismatch | unit 不兼容 |
| how to plough a field | numerical_mismatch | abs_diff=2 |
| how to grow petunia indoor | numerical_mismatch | abs_diff=64 |
| how to set up jailbroken apple tv 2 | numerical_mismatch | abs_diff=1 |
| how to slow and tender cook chuck roast | numerical_mismatch | abs_diff=349 |
| munchkin song lyrics | numerical_mismatch | abs_diff=1 |

**全部带确定性数值证据，可归因，无模棱两可。**

---

## 四、Threshold Calibration 分析

### 4.1 现状

```text
默认 Profile：所有 metric threshold=null（无 PASS/FAIL 判定）
→ integrity：仍判失败（mismatch 即 failure）✓
→ faithfulness：分数正常产出但 passed=None，低分不触发 failure ✗
```

### 4.2 校准建议（仅分析，不改代码）

| Metric | 建议 | 依据 |
|---|---|---|
| `faithfulness` | 若需"低分报警"，在 Profile 层设 `threshold: 0.6~0.7` | 50 条均值 0.677，RAGTruth 有幻觉样本集中在低分 |
| `numerical_consistency` | **不建议加阈值**——mismatch 即失败已覆盖 | 当前 7 条 failure 全走 mismatch，无需阈值 |
| `entity/temporal_consistency` | 不需要阈值 | 确定性 match/mismatch 语义已充分 |

### 4.3 校准的边界（必须诚实标注）

1. **阈值是 Profile 数据，不是平台默认**：PRD Q3 规定平台不内置行业默认阈值；校准应发生在 Profile 层（用户/业务侧），平台只提供能力。
2. **0.677 不能直接当阈值**：这是单一数据源（RAGTruth）、单一 judge（GLM）的结果，换数据/换 judge 会漂移。
3. **给 faithfulness 设阈值会改变 25 条 undetermined 的构成吗**：不会直接——faithfulness 的 low 分设阈值后会触发 failure→进入 generation diagnosis（unsupported_claim 需证据），但当前 generation diagnosis 覆盖的是**数值断言**证据，对非数值幻觉仍会 undetermined。

### 4.4 真正值得校准的缺口

> **faithfulness 的"低分静默不报"** 是当前最大的判定缺口：50 条里 48 条 faithfulness 有分数，但 threshold=null 使它们全部 `passed=None`，即使 0.25 分的记录也不报失败。校准方向是**在 Profile 层为 faithfulness 设阈值**，使低分能进入 failure→diagnosis 链路。

---

## 五、结论

```text
覆盖维度：4 个（生成忠实性 / 数值 / 时间 / 实体），检索相关 3 项已实现未在本 run 启用，answer_relevancy 未覆盖
25 条 undetermined：
  13 条 = 作用域缺口（英文实体无 alias、英文单位不在词典）—— 引擎能力边界，非数据问题
  10 条 = 真证据不足（ambiguous）—— 诚实判定，正确行为
   2 条 = faithfulness 执行错误 —— 运行层问题
  0 条 = 阈值未触发 / 可测不可解释

校准建议：faithfulness 在 Profile 层设阈值（0.6~0.7 参考），integrity 无需阈值（mismatch 即失败）
```

---

## 六、产物

```text
benchmarks/phase8b_audit.py                       记录级粗分类（开发用）
benchmarks/phase8b_classify.py                    报告口径精确分类（正式）
benchmarks/processed/phase8b_audit.json
benchmarks/processed/phase8b_coverage_classification.json
benchmarks/processed/FULL_CHAIN_DEMO_50.json      50/50 run 汇总
```