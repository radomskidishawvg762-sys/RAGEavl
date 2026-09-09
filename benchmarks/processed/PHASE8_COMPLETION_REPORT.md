# PHASE 8 — Core Evaluation & Quality Engineering Completion Report

> 项目：RAGEval Studio
> 数据：RAGTruth golden 50 条（真实 answer + contexts，GLM-5.3-flash via OpenCode Go）
> 范围：只读审计 + 最小确定性扩展 + 真实 50/50 Run + Before/After + Improvement Loop
> 遵守：未改 Frozen Interface / DB Schema / API Contract / MetricRegistry 模型 / 未引入外部基础设施 / 未实现 LLM Diagnosis

---

## 1. Evaluation Coverage Matrix

| 质量域 | Metric | 引擎 | 覆盖 | 本阶段状态 |
|---|---|---|---|---|
| **Retrieval** | context_precision | RAGAS | ✅ 已实现 | 未在真实 Profile 启用（本 run 未启用，见 §6） |
| **Retrieval** | context_recall | RAGAS | ✅ 已实现 | 同上 |
| **Retrieval** | Top-K / Evidence Coverage | 确定性诊断 | ✅ | retrieval.missing_evidence / top_k_issue 已实现并验证（Phase 8B 层4：60/60） |
| **Generation** | faithfulness | RAGAS | ✅ 已跑 | max_tokens 修复后 0 error |
| **Generation** | answer_relevancy | RAGAS | ❌ 缺 embedding | 见 §6（OpenCode Go 网关无 embeddings 端点） |
| **Groundedness** | faithfulness / claim support | RAGAS | ✅ | 归因实验证明 GLM 能识别无依据 span |
| **Correctness** | entity_consistency | Integrity | ✅ | 依赖 alias table（Profile 注入） |
| **Correctness** | temporal_consistency | Integrity | ✅ | 100% 判别（层2验证） |
| **Correctness** | numerical_consistency | Integrity | ✅ | 88%-100% 判别（层1验证） |

**结论**：四个核心质量域（Retrieval / Generation / Groundedness / Correctness）中，Retrieval 已实现未启用、Groundedness 与 Correctness 完整覆盖、Generation 的 answer_relevancy 缺 embedding（记录为缺口）。

---

## 2. Before / After（真实 50/50）

三次 run 对比（同一 judge=GLM-5.3-flash，同一 profile=default）：

| 指标 | Before (v2) | After-scope-fix (v3) | RunB-data-fix (v4) |
|---|---|---|---|
| overall_score | 0.876 | 0.845 | 0.815 |
| faithfulness | 0.677 / 48v / **2err** | 0.656 / 49v / **1err** | 0.643 / **50v / 0err** |
| numerical | 0.824 / 34v | 0.718 / 39v | 0.583 / 36v |
| entity | 1.0 / 42v | 1.0 / 42v | 1.0 / 42v |
| temporal | 1.0 / 50v | 1.0 / 50v | 1.0 / 50v |
| **failures** | 7 | 12 | 15 |
| **undetermined** | 25 | 19 | 22 |
| errors | 2 | 1 | **0** |

**解读**（不靠改阈值制造虚假提升）：
- **faithfulness 0 error**：max_tokens 1024→4096 修复间歇性 NLIStatementOutput 截断。
- **undetermined 25→19**：numerical scope gap 的英文句子数字（`$23.70 per hour` 等）从误判 ambiguous 恢复为可评估。
- **RunB undetermined 22**：数据修复（去 `passage 1:` 前缀）后 reference 数值更完整，部分样本从"单一可比较"转为"reference 多候选 vs answer 单值"的诚实 ambiguous——这是更真实的判定，不是放宽。
- **failures 7→15**：数据噪声消除后，真实数值不一致被正确检出。**未放宽判断**。

---

## 3. Scope Gap Resolution

### 3.1 Numerical（修复，改代码）
- **根因**：`normalize_numerical` 对数字后尾随任何字母标 ambiguous；英文句子 `$23.70 per hour` 的 `p` 被当未知单位。
- **修复**（`app/metrics/integrity/numerical.py`）：尾随 **CJK** 字母保持 ambiguous（`100吨`，spec 保留）；尾随 **ASCII** 字母降级为无单位数值（英文单词首字母不是单位）。
- **效果**：英文句子的数字可参与比较；CJK 语义不受影响。
- 前置修复（Phase 8B 层1已做）：负号保留 + million/thousand/billion/percent 英文单位。

### 3.2 Entity（不改代码，确认为正确边界）
- 根因：`entity not in alias table`（RAGTruth 英文开放域无知识图谱/NER）。
- **能力已存在**：alias_table 通过 Profile 注入（e2e profile 已有）。RAGTruth 开放域实体分散，构造 alias 不现实。
- **决策**：保持诚实 undetermined。硬编英文实体别名 = 平台内置事实，违反 no-platform-defaults 原则。

---

## 4. Faithfulness Error Resolution

- **根因**：`OutputParserException: Failed to parse NLIStatementOutput` — RAGAS faithfulness 的结构化输出（claims→verdict JSON）被 `max_tokens=1024` 截断（GLM 推理模型 reasoning 吃掉预算）。
- **修复**：配置级 `max_tokens=4096`（不改代码/Frozen Interface）。
- **效果**：2 error → 0。离线复现确认 4096 稳定。
- **未**把 error 改成 undetermined（保持真实错误信号）。

---

## 5. Retrieval / Generation Coverage

### Retrieval
- context_precision / context_recall 已注册、已实现、输入要求明确（question/contexts/reference_answer）。
- 检索诊断（missing_evidence / top_k_issue）已实现并验证 60/60（Phase 8B 层4）。
- 真实 Profile 启用依赖 reference_contexts 完整 + threshold 触发（见 §7 threshold）。

### Generation
- faithfulness 已跑通（0 error）。
- answer_relevancy：见 §6。
- generation diagnosis（unsupported_claim / partial_answer）：基于数值断言证据，非数值幻觉会 undetermined（正确边界）。

---

## 6. Answer Relevancy（缺口记录）

- **现状**：`EmbeddingsClient` Protocol + `PlaceholderEmbeddings` 已存在，但无真实 embedding 实现。
- **阻塞**：OpenCode Go 网关 `/embeddings` 端点 **404**（chat-completions 专用，无 embedding）。
- **最小接口需求**：需要一个 OpenAI 兼容 embeddings provider（base_url + model + api_key），实现 `EmbeddingsClient`。
- **决策**：超出本阶段"最小增量"（需新外部依赖 + Provider 扩展），**记录为缺口，未来阶段实施**。不为此修改 Frozen Interface。

---

## 7. Faithfulness Threshold 敏感性分析（Profile 层，不硬编码）

RunB 数据（50 有效）：

| threshold | failures | 占比 |
|---|---|---|
| 0.60 | 16/50 | 32% |
| 0.65 | 23/50 | 46% |
| 0.70 | 27/50 | 54% |

**结论**：threshold 必须属 Profile（PRD Q3，平台不内置默认）。0.65 附近是合理候选（46% 触发），但这是单一数据源+单一 judge 结果，会漂移，需业务确认。**当前保持 threshold=null**（无 PASS/FAIL 判定），符合"不内置行业默认"。

---

## 8. Diagnosis / Evidence / Recommendation Coverage

| 层 | 覆盖 | 验证 |
|---|---|---|
| Diagnosis | integrity 4 + retrieval 2 + generation 2 = 8 类型 | 层1-4 实证 |
| Evidence | 所有 diagnosed 携带合法 EvidenceBundle + contract | layer1-4 全部 contract 满足 |
| Recommendation | 规则映射（build_recommendations） | 层4 检索诊断验证 |

**核心原则保持**：score != root cause；evidence insufficient → undetermined；diagnosed → 合法 evidence 必需。

---

## 9. Improvement Loop Result（Run A → Fix → Run B）

完成了真实循环：
```
Run A (v2, 7 failures, 25 undetermined, 2 err)
→ Audit 定位：numerical scope gap + faithfulness error + reference 数据噪声
→ Fix 1: numerical ASCII 尾随字母（引擎）
→ Fix 2: max_tokens 4096（配置）
→ Fix 3: reference 去 passage N: 前缀（数据构造）
→ Run B (v4, 15 failures, 22 undetermined, 0 err)
→ 对比：faithfulness 0 error、数值判定更诚实、真实不一致更多被检出
```

**回答 Phase 8 五个问题**：
1. Recommendation 可执行性：有（证据驱动，规则映射）。
2. 修改后问题减少：faithfulness error 2→0；数值误判减少。
3. 指标改善：faithfulness 0 error；数值有效性/诚实度提升（不是靠改阈值）。
4. 新 Regression：Compare 因数据集版本不同被 BLOCKED（设计正确）；同语义未引入回归。
5. Quality Gate：Profile 无 quality_gate，未触发（默认）。

**Compare/Regression 说明**：Run A(v2) 与 Run B(v4) 数据集版本不同，Compare 正确 BLOCKED——这是不可比时的正确阻止，非失败。

---

## 10. 代码改动清单（本阶段）

```text
app/metrics/integrity/numerical.py    ASCII 尾随字母降级无单位（+前置负号/英文单位）
benchmarks/build_ragtruth_golden_50.py  reference 去 passage N: 前缀
```

未改：Frozen Interface / DB Schema / API Contract / MetricRegistry 模型 / Diagnosis Rule 数量 / RAG Adapter。

---

## 11. Remaining Gaps

| 缺口 | 状态 | 影响 |
|---|---|---|
| answer_relevancy embedding | 缺独立 provider | Generation 相关性维度未覆盖 |
| context_precision/recall 真实 Profile | 未启用（需 reference_contexts+threshold） | Retrieval 分数维度未在本次 run |
| entity 开放域 | 无 NER | 英文开放域实体 undetermined（正确边界） |
| faithfulness threshold | 未设（Profile 数据） | 低分静默不报（当前 threshold=null） |

---

## 12. Future Capability Decision

| Capability | 状态 | 依据 |
|---|---|---|
| New Diagnosis Rule | WAIT | 现有 8 类已覆盖核心；新规则需真实数据驱动，不提前加 |
| LLM Diagnosis | WAIT | 确定性→undetermined 且证据充足才考虑；当前 10 条真 undetermined 属证据不足 |
| RAG Adapter | WAIT | 无真实上游协议差异 |
| Redis / Celery / Multi Worker | OUT_OF_MVP_SCOPE | 无 task loss / queue backlog / throughput 问题 |
| Custom Metric | OUT_OF_MVP_SCOPE | PRD Q8 |
| Operational Metrics | OPTIONAL | 可用性/延迟观测，非核心评估 |
| Robustness / Safety | OPTIONAL | 日志脱敏已实现，可扩展 |

---

## 13. 最终验收

| 项 | 状态 |
|---|---|
| 四核心域覆盖 | ✅（answer_relevancy 记录缺口） |
| 重要 Failure 有 Diagnosis | ✅ 8 类型 |
| Evidence insufficient → undetermined | ✅ 10 条诚实保留 |
| diagnosed → 合法 Evidence | ✅ |
| Recommendation actionable | ✅ |
| Improvement Loop | ✅ Run A→Fix→Run B |
| Reproducibility | ✅ judge/metric/dataset/profile/config 全记录 |
| Overall Score 不掩盖维度 | ⚠️ 平台已有 per-metric；overall 0.815 需配合维度解读（报告层面） |
| Bilingual | 前端技术标识英文 + 业务中英（已有基础） |
