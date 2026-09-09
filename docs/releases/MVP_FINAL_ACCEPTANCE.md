# RAGEval Studio — MVP Final Acceptance Report（MVP Freeze Gate）

- 日期：2026-09-01
- Gate 性质：FINAL MVP FREEZE GATE（功能冻结，仅证明，不扩展）
- 审查对象：PRD v1.3 / Spec v2.1 + Phase 1A/1B/2/3 交付物
- 结论：**MVP FREEZE READY**（见 §16）

---

## 0. Post-Freeze Status Re-audit（2026-09-04 — Phase 3 文档同步）

> 本节为 2026-09-04 依据**当前源码 + 测试运行结果**做出的复审。
> §1–§16 保留为 2026-09-01 Freeze Gate 的**历史快照**，不据今日状态改写；
> 与当前事实冲突之处以本节为准。

### 0.1 FR-12 复审结论：PARTIAL → **DONE**

复审依据（代码位置 + 运行时测试证据，非文档声明）：

| 能力 | 实现位置 | 运行时证据 |
|---|---|---|
| `pipeline.engines` 白名单参与执行 | `app/services/run_planner.py` `resolve_effective_pipeline()`（白名单过滤 enabled_metrics，排除项显式记录）→ `app/api/deps.py` launcher `build_engines(enabled_metrics)` → `app/engines/pipeline.py::run_record` | `tests/test_phase_d_effective_config.py::test_7_pipeline_engine_whitelist_filters_metrics`（snapshot.engines == 实际引擎 == 执行指标；被排除指标零 MetricResult）；`tests/test_fr12_pipeline_audit.py::test_a / test_b / test_d` |
| `diagnosis.enabled` 执行语义 | `app/services/evaluation_service.py::execute_run`（effective pipeline 的 `diagnosis_enabled` 为权威开关；false → 不创建 DiagnosisEngine、零 diagnosis 行，指标照常持久化） | `tests/test_phase_d_effective_config.py::test_8_diagnosis_enabled_true_executes` / `test_9_diagnosis_enabled_false_skips_orchestration`；`tests/test_fr12_pipeline_audit.py::test_c_diagnosis_disabled_profile_is_now_honored` |
| enabled_metrics 真实生效 | Profile → Effective Config → Run Snapshot → Runner → Engine → MetricResult 全链 | `tests/test_phase_d_effective_config.py::test_3_override_disable_metric`（禁用指标零行）；`tests/test_fr12_pipeline_audit.py::test_b / test_d`（选择变化 → 执行集合精确变化）；`tests/test_metric_overrides_api.py` |

原 PARTIAL 三项缺口的当前定性：

1. `diagnosis.enabled` 执行语义 —— **已实现并有运行时测试锁定**（上表）。
2. `evaluation_configs.pipeline_config` DB 列存而不执行 —— **刻意不变量，非缺口**：
   执行路径只读 merged effective config，源码级锁定于
   `tests/test_fr12_pipeline_audit.py::test_9_pipeline_config_is_stored_but_never_executed`，
   目的是防止出现第二配置事实来源（ADR-05）。该列内容不进入执行链。
3. 引擎级 Pipeline 选择 **UI** —— 仍缺（前端，Post-MVP Backlog P1）。FR-12 的 P0
   要求（配置驱动的引擎装配 + 诊断开关、业务层零引擎分支）已由后端全链满足。

### 0.2 Run Snapshot / Quality Gate 复审

- Run 创建时固化（reproducibility_meta）：profile / profile_version /
  config_version(sha256 of merged effective config) / enabled_metrics /
  metric_weights / effective_metrics / metric_overrides / pipeline（engines、
  selected_engines、excluded_metrics、diagnosis）/ rag_input / quality_gate /
  severity_mapping / judge 公共指纹（无密钥）/ 8 复现字段。
  证据：`test_phase_d_effective_config.py::test_5_snapshot_completeness`、
  `::test_14_snapshot_contains_no_secrets`；实现于 `app/services/run_planner.py::build_reproducibility_meta`。
- 历史免疫：Profile 新版本或部署 YAML 事后变更，不影响已完成 Run 的结果、
  诊断与门禁判定。证据：`test_phase_d_effective_config.py::test_12_historical_run_immune_to_new_profile_version`、
  `::test_13_quality_gate_uses_snapshot_not_current_yaml`（YAML 事后禁用门禁，
  历史 Run 判定不变）。
- Quality Gate 快照来源：`app/services/quality_gate_service.py::evaluate` 采用
  **key-presence 语义**——Run Snapshot 携带 `quality_gate` 时一律以快照为准，
  当前 YAML 永不覆盖历史 Run；仅 legacy（快照无此键）回退到 config 解析。
- Report / Compare / Regression 为只读聚合（`app/services/report_service.py`、
  `comparison_service.py`、`regression_service.py`），不重新执行任何 Engine。

### 0.3 集成测试状态（Phase 2 后）

- 单元/行为套件：**417 passed**，ruff 全绿（2026-09-04 实测）。
- PostgreSQL 集成测试：**13 个存在（3 既有 + Phase 2 新增 10），当前
  TEST_DATABASE_URL 未配置 → 全部显式 skip —— NOT VERIFIED，不计入 passed**。
- 隔离守卫已实测：`TEST_DATABASE_URL == DATABASE_URL` 时 pytest 启动即报错退出
  （`tests/conftest.py`；配置来源优先级 os.environ > `.env.test` > `.env.local`；
  已配置时首跑自动对专用测试库执行 `alembic upgrade head`，每测试前后 TRUNCATE 隔离）。
- Cancel 一致性（Post-Freeze Phase 1）：Run 状态全部改为数据库条件更新裁决
  （`cancel_run_if_active` / `finish_run_if_running` / `start_run_if_pending` /
  `fail_run_if_active`），`cancelled` 终态不可被完成态覆盖；行为测试与真实 PG
  集成测试（`tests/test_postgres_integration.py`，含双线程 cancel/finish 竞态）
  均已就位，后者同样待 TEST_DATABASE_URL 生效后执行。

---

## 1. Executive Summary

本轮 Freeze Gate 对 MVP 进行了行为级验收：真实 Chrome 驱动 25 个检查点 + 50 项全局守卫（75/75 通过），真实 Supabase + 真实 Judge 的 API E2E 50/50 通过，Benchmark v0.1 全维度 100%，前后端测试/构建/lint 全绿。

Gate 过程中**发现并修复了 3 个真实缺陷**（均为既有测试体系盲区，由真实浏览器 E2E 首次暴露）：

| # | 缺陷 | 严重度 | 修复 |
|---|---|---|---|
| B1 | `DatasetDetailPage` 崩溃：hook 未解包 validation 端点的包装形状 `{dataset_id, validation_status, validation_report}` → `checks.length` TypeError，整页白屏 | **P0**（数据集详情页不可用） | hook 解包 + 组件防御 + vitest fixture 修正为真实端点形状（回归锁定） |
| B2 | `ProjectContext` 竞态：新建项目后立即「进入 Workspace」，自动纠偏 effect 用过期列表把显式选择重置回旧项目，整个 Workspace 跑偏到错误项目上下文 | **P0**（数据错置） | 显式选择信任窗口：selection 晚于已加载快照时不纠偏，下次加载完成后验证 |
| B3 | `/favicon.ico` 404（index.html 未声明 icon） | P3 | 内联 data-URI favicon |

FR-12 专项审计结论：**PARTIAL**（指标级 Pipeline 选择全链生效并有测试锁定；引擎级选择 UI、`diagnosis.enabled`、`pipeline_config` 执行语义未实现 → Post-MVP Backlog，详见 §10）。*（2026-09-04 复审翻案为 DONE，见 §0.1；下文保留 Gate 当时判定作为历史记录。）*

## 2. Architecture Status

- 分层链路 `API → Service → Repository → SQLAlchemy → PostgreSQL` 全程未被破坏；本轮新增代码（G1-G5、前端 8 页）均走既有边界。
- `frontend_scan` 源码守护随 Phase 2 演进：按其自身文档意图（"forbid the operators, not the strings a renderer legitimately switches on"）精化为「禁止派生（赋值+算术）、允许渲染读取」——`m.delta` / `c.improvement_count` 等**读取**合法，`delta = a - b` 类**派生**仍被禁止（test_8/test_16 372 项全绿验证）。
- ADR-05/G6：config YAML 只读语义未破坏；G4 metric_overrides 只进 Run Snapshot（`reproducibility_meta.metric_overrides/effective_metrics`），`config_version` 保持 sha256(merged YAML)。

## 3. Frontend Status

| 项 | 结果 |
|---|---|
| vitest | **60/60 passed**（6 files；含真实端点形状 fixture、G4 overrides 端到端 POST body 断言） |
| `tsc --noEmit` | 干净 |
| `vite build` | 成功（主包 293.74 kB / gzip 93.15 kB；ECharts 1.04 MB chunk 懒加载隔离） |
| 页面 | 16 页产品闭环全部真实实现（无占位页；Chrome E2E GUARD 断言零 placeholder 文本） |
| 硬编码阈值/指标名 | 0（扫描 0 命中；阈值均来自 Profile/快照渲染） |
| null→0 | 0（`ScoreValue` 统一「暂无有效分数」；Chrome E2E 实测 count≥1） |

## 4. Backend Status

| 项 | 结果 |
|---|---|
| pytest | **372 passed / 3 skipped**（skip=3 个集成测试，见 §11） |
| ruff | All checks passed |
| 本轮新增 | G1 `POST /api/projects`、G2 evidence 只读暴露、G3 单条结果详情、G4 metric_overrides、G5 项目摘要、FR-12 特征化测试 ×5 |

## 5. Supabase Status

- **PASS**。`GET /api/health` → `{app: healthy, database: healthy, detail.db_latency_ms}`（Chrome E2E 实测）。
- API E2E（`scripts/run_e2e.py`）：**50/50 PASS** —— 真实建库链路：import → run（Golden Replay + 真实 Judge）→ 指标/诊断/证据/建议持久化 → Compare/Regression/Gate 全链在真实 PostgreSQL 上断言（含 row-lock 409、8 复现字段、ambiguous 不造假分数、read-only 服务零执行）。
- Alembic：current = **0003 (head)**，唯一 head（§13）。

## 6. Judge Status

- **PASS**。真实 Judge（opencode.ai OpenAI-compatible 网关）对 faithfulness / context_recall 真实打分（Chrome E2E：faithfulness=0.5、context_recall=1.0 级别的真实分数进入 UI 与 Compare）。
- answer_relevancy / context_precision：embeddings 未配置 → `BIZ_JUDGE_NOT_CONFIGURED` → score=null，UI 显示「暂无有效分数」而非 0（区分行为符合规范）。
- 密钥零泄漏（§12）。

## 7. Browser E2E（Real Chrome）

- 驱动：playwright-core + `channel: 'chrome'`（本机真实 Chrome 二进制），1440×900，Real Vite dev（/api 代理 → Real FastAPI :8000 → Real Supabase + Real Judge）。
- 结果：**75/75 PASS**（脚本 `frontend/scripts/chrome-e2e.mjs`，本轮最终运行）。

25 检查点全部通过，关键行为级证据：

| 检查点 | 真实证据 |
|---|---|
| 1 Project | UI 创建 → 201 → 行出现 → 进入 Workspace 上下文正确 |
| 2 Dashboard | 空项目诚实空态 → 有数据后展示真实 0.8333 / 覆盖 100% / 门禁原因 |
| 3-6 Dataset | 导入 4 条 → 5 类校验全过 → v1 → 详情（B1 修复后）→ Record Explorer 抽屉 |
| 8-12 New Evaluation | 选数据集 → Profile → **指标覆盖 0.9（Run Configuration）** → RAG Input（Golden Replay 标注）→ Review 全景（覆盖标记）→ 202 |
| 13 Polling | 真实 Judge 执行至终态 completed |
| 14 Workbench | overall 0.8261（UI）== 0.826086956504348（API，4 位小数展示）；numerical 阈值 0.9 == API；null 分数 =「暂无有效分数」 |
| 15-18 Failure→Diagnosis→Evidence→Recommendation | 失败行 integrity.numerical_mismatch → 抽屉完整 I/O（Retrieved Contexts）→ **evidence items（持久化 JSONB 回显）** → comparison_basis → P1/P2 建议 |
| FR-03 新版本 | v2 创建 + versionOf 引导 + 锁定 v1 不可再跑 |
| run2 | UI 停用 4 个 RAGAS 指标 → 完成于 4 项 integrity（G4 覆盖真实生效） |
| 19 Compare | BLOCKED + 后端 reasons（v1/v2 不同数据集行——**严格透传，前端不自行判定**） |
| 20 Regression | NOT_COMPARABLE（后端 verdict） |
| 21 Quality Gate | NOT_EVALUABLE + QUALITY_GATE_NOT_CONFIGURED（≠ QUALITY_THRESHOLD_FAILED） |
| 22 Export | 下载真实机器报告 JSON（run_id 匹配，无密钥） |
| 23-25 Settings/Profiles/Metrics | 真实 /api/health；只读声明；无假保存按钮 |

全局守卫（GUARD）：无 console error、无 page error、无 API 4xx/5xx（236 个 JSON 响应体扫描）、零 placeholder 文本、零密钥泄漏。

## 8. Benchmark

- **PASS — benchmark v0.1 全维度 100%**：integrity 三指标（entity 4/4、numerical 15/15、temporal 8/8）、diagnosis 100%、evidence-contract 100%、compare 100%、regression 100%、quality-gate 100%（CMP-001 精确复现）。

## 9. FR-01 ~ FR-40 最终矩阵

Status 判据 = 真实行为（Chrome E2E + API E2E + 测试），「页面存在」≠ DONE。

| FR | Requirement | Backend | Frontend | Real E2E | Status | Evidence |
|---|---|---|---|---|---|---|
| FR-01 | 项目创建/编辑/归档 | POST ✓（编辑/归档缺） | 创建+Workspace ✓ | Chrome E2E #1 | **DONE**（编辑/归档=P1） | Chrome E2E 201+上下文切换 |
| FR-02 | 项目聚合 | GET /projects/{id} ✓ | 摘要面板 ✓ | Chrome E2E（摘要） | **DONE** | counts/latest/gate 渲染 |
| FR-03 | 版本化/锁定 | 导入建版✓；独立 versions 端点缺 | v2 入口✓ | Chrome E2E v2+锁定 | **DONE** | v2 创建 + 409 语义（API E2E） |
| FR-04 | JSON 导入 | ✓ | 4 步向导 ✓ | Chrome E2E #5 | **DONE** | 4 条导入成功 |
| FR-05 | 5 类校验+定位 | ✓ | #row_index 渲染 ✓ | Chrome E2E（5 checks 全过 + 400 场景实测） | **DONE** | domain_metadata 400 真实触发并正确定位 |
| FR-06 | 预览/记录数 | ✓ | ✓ | Chrome E2E #6 | **DONE** | Record Explorer 抽屉 |
| FR-07/08 | 标准模型/JSON Adapter | ✓ | — | API E2E | **DONE** | — |
| FR-09 | HTTP Adapter (P1) | HttpRagAdapter ✓ | 只读展示+Gap 标注 | 未在 Chrome E2E 驱动真实 RAG 端点 | PARTIAL | Golden Replay 为本轮全部 E2E 路径 |
| FR-10 | 三层配置 | ✓ | Profiles 页+向导 ✓ | Chrome E2E #24 | **DONE** | 只读声明+配置渲染 |
| FR-11 | Profile 阈值/权重/覆盖 | 读✓ + G4 覆盖✓；YAML 写=设计不做 | Run Configuration ✓ | Chrome E2E #9/14 | **DONE**（YAML 编辑=Config Lifecycle 任务） | override 0.9 全链生效 |
| FR-12 | Pipeline 装配 | 指标级✓；引擎级/diagnosis.enabled/pipeline_config ✗ | 指标级✓ | Chrome E2E run2（停用 RAGAS→仅 integrity 执行） | **PARTIAL**（历史）→ 2026-09-04 复审 **DONE**（§0.1） | §10 |
| FR-13 | 8 复现字段 | ✓+effective_metrics | 渲染✓（secret 过滤） | API E2E + Chrome E2E | **DONE** | 8 字段非空断言 |
| FR-14 | Metric Registry | ✓ | Catalog 页 ✓ | Chrome E2E #25 | **DONE** | 三分类+搜索 |
| FR-15/16 | 通用/Integrity 指标 | ✓ | ✓ | API E2E + Chrome E2E #14 | **DONE** | 真实分数渲染 |
| FR-17 | 未来指标 (P1) | ✗ | ✗ | ✗ | NOT_IMPLEMENTED | Post-MVP |
| FR-18 | 逐记录+comparison_basis | 持久化+G3 暴露 ✓ | 抽屉渲染 ✓ | Chrome E2E #17 | **DONE** | comparison_basis 显示 |
| FR-19/20 | 执行/进度/状态 | ✓ | 轮询+进度 ✓ | Chrome E2E #13 | **DONE** | 终态到达 |
| FR-21 | 失败完整 I/O 下钻 | G3 详情端点 ✓ | 抽屉 ✓ | Chrome E2E #15-17 | **DONE** | Retrieved Contexts 全文 |
| FR-22 | 错误隔离 | ✓ | 覆盖率/错误/undetermined ✓ | API E2E | **DONE** | completed_with_errors 语义 |
| FR-23 | 规则诊断 | ✓ | ✓ | Chrome E2E #16 | **DONE** | root cause 渲染 |
| FR-24 | 证据 RCA（契约） | 持久化+G2 暴露 ✓ | EvidencePanel ✓ | Chrome E2E #17 | **DONE** | evidence items==DB（契约测试） |
| FR-25 | 严重度来自 Profile | ✓ | ✓ | Chrome E2E | **DONE** | CRITICAL 渲染 |
| FR-26 | 建议 | ✓ | ✓ | Chrome E2E #18 | **DONE** | P1/P2+source |
| FR-27 | LLM 诊断 (P1) | ✗ | ✗ | ✗ | NOT_IMPLEMENTED | Post-MVP |
| FR-28/29 | 报告/总分+原始同屏 | ✓ | ✓ | Chrome E2E #14 | **DONE** | 禁止只显示总分 |
| FR-30/31 | 机器报告/JSON 导出 | report JSON ✓ | 下载按钮 ✓ | Chrome E2E #22 | **DONE** | 下载件 run_id 匹配 |
| FR-32 | PDF (P1) | ✗ | ✗ | ✗ | NOT_IMPLEMENTED | Post-MVP |
| FR-33 | 双 Run 对比 | ✓（可比性判定） | ✓（严格透传） | Chrome E2E #19 | **DONE** | BLOCKED+reasons |
| FR-34 | Delta+回归警告 | ✓ | ✓ | Chrome E2E #20 + API E2E CMP-001 | **DONE** | verdict/epsilon/trade_off |
| FR-35 | 趋势 (P1) | — | ✓（轴修复） | Chrome E2E 2b | **DONE** | 最近运行/趋势 |
| FR-36 | Dashboard | — | ✓ | Chrome E2E #2/2b | **DONE** | 诚实空态+真实数据 |
| FR-37 | Dataset 页 | — | ✓+筛选 | Chrome E2E #3 | **DONE** | — |
| FR-38 | Evaluation+Detail | — | ✓ | Chrome E2E #7/14 | **DONE** | — |
| FR-39 | Compare 页 | — | ✓ | Chrome E2E #19 | **DONE** | — |
| FR-40 | 容器化部署 | app-only compose ✓ | — | 未在本轮重跑 compose | **DONE** | docker-compose 结构审计 |

## 10. FR-12 专项审计（Freeze Gate §二）

审计链：UI Selection → Create Request → Run Snapshot → Run Planner → EvalParams → Pipeline Assembly → Engine Execution。

| # | 问题 | 结论 |
|---|---|---|
| 1 | UI 能选择什么？ | 仅**指标级** enabled/threshold/weight（G4 Run Configuration）；无引擎级选择 UI |
| 2 | 进入 API？ | 是——`POST /api/evaluations.metric_overrides` |
| 3 | API 保存？ | 是——`reproducibility_meta.{enabled_metrics, effective_metrics, metric_overrides}` |
| 4 | Snapshot 记录 Pipeline？ | 指标级完整记录；引擎级装配结果不作为独立字段 |
| 5 | Service 读取？ | 是——execute_run 仅对 enabled_metrics 求值 |
| 6 | Pipeline 按配置组装？ | **部分**——`build_engines` 由注册表 spec.engine 从 enabled_metrics 推导（metric-driven assembly） |
| 7 | Engine 与选择一致？ | 是（指标粒度；Chrome E2E run2 实证：停用 4 RAGAS → 仅 integrity 引擎执行） |
| 8 | Diagnosis 开关生效？ | **否——Profile `diagnosis.enabled` 在 app/ 中零引用，诊断总是执行** |

特征化测试（`tests/test_fr12_pipeline_audit.py`，5/5 通过）：
- A 默认 Pipeline：registry 驱动装配 integrity+ragas ✓
- B Integrity-only：UI 覆盖停用全部 RAGAS → 装配与执行仅 integrity、snapshot 一致 ✓
- C Diagnosis disabled：**特征化锁定现状缺口**（配置键无消费者 + 诊断仍产生）✓
- D 选择变化 → 执行集合精确变化 ✓
- 9 `pipeline_config` 存而不执行（源码断言）✓

**最终判定：FR-12 = PARTIAL**（不包装为 DONE）。指标粒度全链真实生效；引擎级选择 UI、`diagnosis.enabled` 执行语义、`pipeline_config` 执行语义 → **Post-MVP Backlog**（不破坏 Frozen Interface 的前提下本轮不修）。

> **2026-09-04 复审（Phase 3）**：上表 #6/#7/#8 的"部分/否"结论已过时——
> `pipeline.engines` 白名单与 `diagnosis.enabled` 开关均已进入执行链并有运行时
> 测试锁定，`pipeline_config` 列存而不执行经复审定性为刻意不变量（防止第二配置
> 事实来源，ADR-05）。完整证据链见 §0.1。本节其余内容保留为 Gate 历史判定。

## 11. TEST_DATABASE_URL Status

- **TEST_DATABASE_URL = NOT CONFIGURED**（`.env.local` 中该键存在但为空，T-22 前启用）。
- 未秘密配置为 DATABASE_URL（conftest.py 硬隔离守卫在位）。
- 因此无法执行的集成测试（3 个，诚实 skip，不视为通过）：
  1. `test_phase1a_integration.py::test_g1_create_project_roundtrip_in_postgres`
  2. `test_phase1a_integration.py::test_g2_g3_evidence_and_detail_roundtrip_in_postgres`
  3. `test_phase1a_integration.py::test_g5_project_summary_roundtrip_in_postgres`
- 缓解：同一链路已由真实 Supabase 上的 API E2E（50/50）与 Chrome E2E（75/75）覆盖——但 fixture 级隔离集成测试仍应随 T-22 激活。

> **2026-09-04 更新（Phase 2）**：测试隔离已落地并增强——`tests/conftest.py`
> 统一解析（os.environ > `.env.test` > `.env.local`），equality 时启动即报错
> （已实测），已配置时自动 `alembic upgrade head` + 每测试 TRUNCATE 隔离；
> 集成测试扩至 **13 个**（3 既有 + 新增 10：事务 / JSONB / 行锁 / Config /
> Run / Cancel 竞态（含双线程）/ Diagnosis，见 `tests/test_postgres_integration.py`）。
> 当前 TEST_DATABASE_URL 仍未配置 → 13 个集成测试显式 skip，**NOT VERIFIED**，
> 不计入 passed。当前基线：pytest 417 passed / 13 skipped。

## 12. Security Audit

| 面 | 结果 | 证据 |
|---|---|---|
| Frontend DOM | 无密钥 | Chrome E2E 全页面渲染均来自已扫描的 API 数据；`safeReproducibility` 过滤 secret 形状键 |
| API response | 无密钥 | Chrome E2E GUARD：236 个 JSON 响应体扫描 judge key / api_key 模式 → 零命中 |
| 下载的 JSON（机器报告） | 无密钥 | Chrome E2E #22 对下载件断言 |
| reproducibility_meta | 无密钥 | 仅 judge 公共指纹字段；E2E 断言 8 字段 + 无 key |
| 前端构建产物 | 无密钥 | `dist/assets/*.js` 扫描：judge key / DATABASE_URL 凭据 / `sk-` / 连接串 → CLEAN |
| 日志 | 无密钥 | 后端 RedactingFilter + 套件内日志断言测试通过（372 项内） |

## 13. Migration Status

- `alembic current` = **0003 (head)**；`alembic heads` = **0003**（唯一 head，无分叉）。
- 本轮（Phase 1A/1B/2/3 + Freeze Gate）**零 migration**——G2/G3/G4 全部为既有 JSONB 列的读侧暴露与请求参数扩展。
- 未执行任何 DROP / TRUNCATE / DELETE。
- **Test Data Cleanup Candidates**（只读审计，`scripts/final_db_audit.py`）：20 个候选项目（e2e-acceptance-* ×7、chrome-e2e-* ×7、probe/probe2 ×3、dbg-import ×1 等），级联行数：datasets 21 / runs 53 / dataset_records 84 / evaluation_results 109 / metric_results 531 / diagnoses 112 / recommendations 72。**清理与否留待用户批准**；另有 2 个非测试项目未列入候选。

## 14. Known Limitations（不构成 P0 blocker）

1. FR-12 PARTIAL（§10）——引擎级选择 UI 缺失；`diagnosis.enabled` / `pipeline_config` 不生效（有特征化测试锁定，不会静默变化）。*（2026-09-04 复审：执行语义已落地并翻案为 DONE，仅剩引擎级选择 UI；见 §0.1。）*
2. Run 级 RAG Input 配置无后端 API（UI 如实标注 Backend Gap；当前由 system.yaml 决定）。
3. Settings 中 Judge/RAG/Evaluation 参数仅「由环境变量管理」声明（G7 只读端点未实现）。
4. Compare 的 DIRECT 富对比路径在真实 Chrome E2E 中以 BLOCKED 场景验证（v1/v2 为不同数据集行，后端按 ADR-06 正确判 BLOCKED）；DIRECT 路径由 API E2E（CMP-001 精确复现）+ vitest 覆盖。
5. YAML 运行时持久化 = NOT IMPLEMENTED BY DESIGN（ADR-05/G6-a）；Profiles 页只读。
6. E2E 环境备注：本机代理会拦截 127.0.0.1 回环（`test_14_api_chain_uses_real_http_rag_adapter` 需清空代理变量后运行，环境问题非代码缺陷）。

## 15. Post-MVP Backlog

| 项 | 来源 | 优先级 |
|---|---|---|
| Config Lifecycle 设计任务（YAML 编辑：Draft→Validation→Version→Persistence→config_version→Run Snapshot） | G6 决议 | P1 |
| 引擎级 Pipeline 选择 UI + `diagnosis.enabled` 执行语义 + `pipeline_config` 执行语义 | FR-12 审计 | P1 | *（2026-09-04 复审：后两项已落地（§0.1），仅剩引擎级选择 UI 为 P1 Backlog）* |
| 项目编辑/归档（PATCH/DELETE）+ `POST /datasets/{id}/versions` | PRD Appendix C | P2 |
| Settings 只读端点（G7：Judge/RAG/limits 安全字段） | Settings 页 | P2 |
| T-22：配置 `TEST_DATABASE_URL` → 激活 3 个集成测试 | §11 | P1 |
| Run 级 RAG Input 配置端点 | New Evaluation Step4 | P2 |
| PDF 导出（FR-32）、ARGUS（FR-42）、LLM 诊断展示（FR-27） | PRD P1 | P2 |
| ECharts manualChunks 分包 | build 警告 | P3 |

## 16. Final Freeze Decision

| # | Gate 条件 | 结果 |
|---|---|---|
| 1 | Real Chrome core path | **PASS（75/75）** |
| 2 | Supabase | **PASS（API E2E 50/50 + health healthy + DB 审计只读完成）** |
| 3 | Judge | **PASS（真实打分进入 UI/Compare；未配置指标 null 语义正确）** |
| 4 | Benchmark v0.1 | **PASS（全维度 100%）** |
| 5 | Frontend tests | **PASS（vitest 60/60）** |
| 6 | Backend tests | **PASS（pytest 372 passed / 3 skip）** |
| 7 | ruff | **PASS** |
| 8 | build | **PASS（tsc + vite build）** |
| 9 | 无未解决 P0 产品阻塞 | **PASS（Gate 发现的 B1/B2 已修复并经完整 E2E 复跑验证；B3 已修）** |
| 10 | FR-12 明确最终判定 | **PASS（PARTIAL + Post-MVP Backlog，特征化测试锁定）** |

> ## **MVP FREEZE READY**

- 冻结范围：FR-01~FR-40 中全部 DONE 项的行为以本轮测试矩阵锁定；PARTIAL/NOT_IMPLEMENTED 项全部显式登记于 §9/§14/§15，无任何以 mock/假数据/假保存掩盖的缺口。
- 冻结期内：不新增产品功能；仅允许缺陷修复、T-22 集成测试激活、及 §15 Backlog 的独立立项评审。
- 数据处置：§13 清理候选保持原样，等待用户另行批准。

*RAGEval Studio — Evaluation → Failure Detection → Diagnosis → Evidence → Root Cause → Recommendation → Version Comparison → Regression 的产品闭环，已由真实环境端到端证明。*
