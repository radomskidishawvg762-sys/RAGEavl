# RAGEval Studio — Architecture Necessity Audit (read-only)

> 日期：2026-09-09
> 方式：全量清单 + 逐符号引用扫描（app/ 65 文件、frontend/src 56 文件、tests/）；未修改任何代码
> 分类：KEEP / KEEP BUT SIMPLIFY / DUPLICATE / SPECULATIVE / DEAD CODE
> 立场：不以“更短/更现代/更优雅/更企业级”为修改理由；只回答“职责是否真实、调用者是否存在、边界是否有价值”

---

## 0. 结论速览

| 发现类别 | 数量 | 严重度 |
|---|---|---|
| 结构性重复（业务规则级） | **0** | — |
| 小型真重复（常量/映射表，2-4 行 × 4 处） | 4 | 低 |
| 未接线的规格预留（表/协议/类） | 5 | 低（有 ADR/Spec 依据或文档价值） |
| 死符号（零调用者，含前端） | 8 | 低（~120 行总量） |
| 纯转发服务层（无职责包装） | **0** | — |
| 需要重构的边界 | **0** | — |

**总判定：NO REFACTOR REQUIRED。** 架构层每一级都有真实职责与被证实的调用者；下列全部发现均为可择机顺手清理的碎屑，不构成任何一次重构的理由。

---

## 1. 逐抽象审查（主要对象）

### 1.1 services（17 个，全部有真实职责与调用者）

| 模块 | 职责（核实） | 调用者 | 判定 |
|---|---|---|---|
| evaluation_service | 运行状态机 + 持久化编排 + `_normalize_result` 错误/判定分离 | api、runner 测试、集成测试 | KEEP |
| report_service | 只读聚合（metric_status 单一函数）| api 5 处、comparison、export | KEEP |
| comparison_service | 可比性门 + 差分（复用 ReportService 聚合，不重算）| api | KEEP |
| regression_service | 纯分析 Comparison 输出 | api | KEEP |
| quality_gate_service | 独立绝对标准评估 | api、useEvaluations（列表页）| KEEP |
| run_planner | 三层 YAML→RunPlan→快照 | api、schemas、catalog | KEEP |
| config_service / catalog / config_resource / config_import | 分别：YAML 合并 / 只读目录（复用 config_service，无第二事实源）/ DB configs CRUD / 导入校验 | api、deps | KEEP ×4（边界清晰，非重复）|
| dataset_service / project_service / project_workspace / project_stats(repo) | 导入解析+校验 / 名称唯一检查 / 摘要组合 / 聚合 SQL | api、deps | KEEP |
| judge_settings_service | SecretStr 叠加层（进程本地）| factory、run_planner、api | KEEP |
| export_service / report_pdf | 新导出组合 + 渲染（只读）| api、tests | KEEP（薄组合非包装：9 节拼装与规则化 summary 是新职责）|

无一个 service 是纯转发。

### 1.2 repositories（唯一数据边界，P-8）

- `base.py`：17 行 marker（`_model`/`session` 属性**零使用**）→ KEEP BUT SIMPLIFY 候选（可删属性保留继承；继承本身是边界模式锚点）。低风险。
- `dataset.py` `get_max_version`：被 `create_version_with_records` 同文件调用 ✓ KEEP。
- `evaluation.py`（493 行）：条件 UPDATE 状态机、行锁、全部聚合读——全部有调用 ✓ KEEP。

### 1.3 engines / metrics / pairing / normalization

| 对象 | 核实 | 判定 |
|---|---|---|
| RagasEngine / IntegrityEngine | 双引擎 + Protocol；`owned` dict 与 defs 单一来源 | KEEP |
| pipeline.py | run_record 组合（22 行，runner 调用）| KEEP |
| factory.py | dict 分发（无引擎名分支，ADR-03 合规）| KEEP |
| registry.py | code-only 注册 + `MetricNotFoundError` 同文件 raise ✓ | KEEP |
| bootstrap.py | 注册入口 ✓ | KEEP |
| metrics/integrity/{numerical,entity,temporal,chinese_number,base,comparison} | 归一化**全局各一处**（单位表、别名折叠、时间解析、中文数字）| KEEP |
| metrics/integrity/pairing.py | 配对单一来源；`_unit_class` **复用 comparison**（非重定义）| KEEP |
| engines/judge.py `build_embeddings` | **零调用**（ragas_bridge 直接构造 Placeholder）| **DEAD** |
| engines/ragas_bridge.py | LLM/embeddings 双桥，测试覆盖 | KEEP |
| engines/providers/{openai,anthropic}.py | build_judge 配置分发可达；anthropic 无直接单测（经 settings 层）| KEEP（注：anthropic 可补 1 测）|
| engines/ragas.py `MetricScorer` | 测试 seam + RagasScorer 实现 | KEEP（协议有第二实现义务）|

### 1.4 diagnosis（8 文件）

- **taxonomy 是唯一映射源**：rules/recommendations/engine 全部 import（COMPARISON_TO_RULE / INTEGRITY_RULES / RETRIEVAL_RULES / GENERATION_RULES / severity hints）——无诊断映射重复 ✓ KEEP。
- classifier / evidence / generation / retrieval / run_aggregator / engine：各有调用者，契约验证返回式 ✓ KEEP。
- `taxonomy.rule_for_code`：**零调用者**便利访问器 → DEAD 候选。
- `evidence.InsufficientEvidenceError`（类定义）：**全库零 raise 零 catch**（真实路径是 engine 返回 undetermined + missing_evidence，且被 test_11 锁定）→ 与 `domain/invariants.InsufficientEvidenceError` **重复定义**，双双未接线。注意 CLAUDE.md/Spec 文本引用该异常名——删除需同步文档；行为不受影响（测试为证）。

### 1.5 adapters / datasets

- `adapters/rag_input.py`（RAG 运行时）与 `datasets/adapters.py`（文件解析）同名不同域，无职责重叠；`AdapterParseError` 同文件多处 raise ✓ KEEP ×2。命名易混淆仅记录（移动文件=结构变更，不值）。
- `db/health.py check_db` ✓ 被 health 用；`db/session.py reset_engine` **零引用（含测试）** → DEAD 候选。
- `core/errors.py SysError` / `ExtDbUnavailableError`：无 raise 点，但二者是 Spec 错误码表成员（`SYS_INTERNAL`/`EXT_DB_UNAVAILABLE`），health 以响应体降级而非抛错 → **SPECULATIVE-KEEP**（契约面，删了反而违约）。

### 1.6 mappers / helpers / protocols / factories（前端 + API 层）

- `api/mappers.ts`：4 个"零外部引用"函数实为**同文件组合调用**（toProfileView 调 toProfileMetricView 等）→ 假阳性，KEEP；仅 `toRagInputView` 是恒等透传（1 行，无害）。
- `api/types.ts` 24 个"无引用"接口 = 后端 Out 模型的**类型级镜像**（经 ReportResponse 等组合间接使用）——设计如此（snake→camel 边界文档），KEEP。
- 真死（前端）：`utils/format.ts formatScore`+`Presentation`（被 ScoreValue 组件取代）、`primitives Divider`/`BiTitle`/`Spinner`、`analysis/types.ts DiagnosisView` —— 各零引用 → DEAD 候选（~60 行）。
- `hooks/`：11 个 hook 全部有消费方（workspace=详情 6 端点并行；evaluations=列表分页；职责不同无重复轮询——轮询只在 useProgressPolling 一处）✓ KEEP。
- `frontend_scan.py`（后端测试）= CI 强制的 **FE 不得派生判定**守卫 → 这是"前后端业务逻辑重复"风险已被制度化封死的直接证据 ✓ KEEP，审计加分项。
- `api/deps.py`：FastAPI DI 惯用法，非包装层。

### 1.7 status / 状态逻辑重复核查

- run 终态集合：`evaluation_service._RUN_TERMINAL_STATES`（4 态）与 `report_service._RUN_TERMINAL_STATES`（同 4 态）**逐字重复**；`comparison._TERMINAL_STATES`（2 态）**语义不同**（可比性只认有数据的终态）——不是重复是子集，合理。→ 前者 2 处合并候选。
- 前端 `TERMINAL_STATUSES`（types.ts）：仅门控轮询启停（Spec 规定 pending|running 轮询），展示/控制逻辑，无判定派生（scan 守卫）→ 可接受。
- `_STATUS_REASON`：comparison 与 quality_gate **各一份**（同 4 项）→ 合并候选。
- `UNDETERMINED_TYPES`（classifier）与 `_UNDETERMINED_COMPARISON_TYPES`（report）：同 {ambiguous, missing_reference} → 合并候选。

### 1.8 SPECULATIVE（有意预留，均有出处）

| 对象 | 依据 | 处置建议 |
|---|---|---|
| `metric_definitions` 表 | Spec 定义为 registry 的 UI 镜像；**当前无 writer/reader**（云库实测 0 行）| Schema 冻结不可删；记录"未接线"即可，UI 已用 registry 路径 |
| EmbeddingsClient/PlaceholderEmbeddings | answer_relevancy 缺口（Phase 8/9 记录）| KEEP |
| EvaluationRunner 协议（仅 LocalAsyncRunner）| ADR-09 明示为 QueueRunner P1 预留 | KEEP（ADR 背书）|
| RagInputAdapter ×2 / 报告 export 端点 / judge runtime overlay | 全部当前在用 | 非 speculative |

---

## 2. Must Keep（结构骨架，冻结或实质在用）

分层链（api→service→repo→ORM）、EvaluationRepository（唯一数据边界）、run 状态机、三配置 service 边界、taxonomy 单源诊断映射、classifier/evidence 返回式契约、registry+bootstrap、双引擎+工厂 dict 分发、deterministic comparators+pairing、judge 三态错误家族 + 2 Provider、RagInputAdapter 双实现、runner 抽象、frontend hooks/mappers/types 三层、frontend_scan 不变量守卫、ExportService/ReportService 复用链。

## 3. Safe Simplifications（若某日顺手；每项独立、零行为变化）

1. `_RUN_TERMINAL_STATES` 双字面 → 单常量（放 domain/schemas 或 evaluation 层共享处）。
2. `_STATUS_REASON` 双映射 → 单常量（comparison/quality_gate 共享）。
3. `UNDETERMINED_TYPES` 双集合 → comparison.py 单点定义（comparison_type 语义的家）。
4. repositories/base.py 删未用的 `_model`/`session` 属性（保留基类本身=边界锚）。
5. engines/judge.build_embeddings、taxonomy.rule_for_code、db/session.reset_engine 删除（各 2-6 行零调用）。
6. 前端 formatScore/Presentation、Divider、BiTitle、Spinner、DiagnosisView 删除（~60 行零引用）。
7. `toRagInputView` 恒等函数并入调用点。
8. 两个 `InsufficientEvidenceError`：保留其一并接 doc 引用，或删双份并改 CLAUDE.md 措辞为"undetermined + missing_evidence 返回式契约"（当前实现真值）。

（3.9 潜在方向性缺陷备忘：ragas/integrity 的 `passed = score >= threshold` 忽略 MetricSpec.direction，quality_gate 方向感知——当前 7 个指标全部 higher_is_better，无现实错误；仅在未来出现 lower_is_better 指标时需统一为方向感知 helper。**现在不动**。）

## 4. Potential Duplicates（结论清单）

| 重复 | 位置 | 风险 |
|---|---|---|
| 终态集合字面 | evaluation_service:65 / report_service:40 | 漂移可能，现一致 |
| 状态→原因映射 | comparison:27 / quality_gate:36 | 4 项，现一致 |
| undetermined 类型集 | classifier:35 / report:35 | 2 项，现一致 |
| passed 阈值式 | ragas:266 / integrity:247 /（gate 方向感知版）gate:96 | 见 3.9 备忘 |
| FE/BE 业务规则 | **无**（frontend_scan.py 制度禁止） | — |
| normalization / metric / diagnosis / report transformation 重复 | **均未发现**（各自单源，逐一验证） | — |

## 5. Speculative Abstractions

metric_definitions 表（未接线、冻结）、Embeddings 协议栈（缺口记录）、EvaluationRunner 第二实现位（ADR-09）、`SysError`/`ExtDbUnavailableError`（错误码表契约面）。全部**有文档出处**，属计划内预留，非投机堆叠。

## 6. Dead Code Candidates

build_embeddings / reset_engine / rule_for_code / assert_evidence_present / 两个同名 InsufficientEvidenceError 类 / SysError+ExtDbUnavailableError（文档面，建议保留）/ formatScore+Presentation / Divider / BiTitle / Spinner / DiagnosisView / 前端 types.ts 的未用名导出（类型镜像，建议保留）。**纯删除价值 ≈ 100-120 行，无行为影响，无测试需要改（引用为零即为证明）。**

## 7. Frozen Boundaries（不可动，本审计确认全部合规）

10 表 schema 与 Alembic 链、API 响应形状（含 metric_status 单源派生）、三态错误家族、run 状态机、ADR-01..09、no-platform-defaults、Supabase-hosting-only、repo 唯一边界、MetricRegistry code-only。本审计未发现任何冻结边界被违反或空转的层。

## 8. 是否值得实际瘦身

**不值得现在动手。** 理由：
1. 全部发现是常量合并/删零调用符号级别，**无结构性冗余**、无行为风险、无维护痛点（520+63 测试全绿、ruff 已清除未用 import）；
2. 冻结态下任何文件变更都要求重跑全套验证，收益/扰动比不成立；
3. 唯一值得记录的真实风险是 3.9（方向性），它**不是瘦身能解决的**，而是未来注册 lower_is_better 指标时的设计检查项。

**若未来某次变更恰好触碰同文件**，顺手执行 §3 清单即可（各项独立、零行为变化、有测试网兜底）。

---

# NO REFACTOR REQUIRED

*审计完成，STOP。未修改任何代码。*
