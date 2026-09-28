# RAGEval Studio 项目介绍

> 本文档描述项目的**真实状态**。文中所有数字都标注了来源；实测的注明实测日期与命令，未验证的明确写「未验证」。不写没有依据的数字。
>
> 最后核对日期：**2026-09-28**

---

## 1. 这是什么

一个 RAG 质量评估与诊断平台。输入是 RAG 系统的问答记录（问题 / 检索到的上下文 / 生成的答案 / 参考答案），输出不只是「 faithfulness = 0.5」这样的分数，而是：

```text
这条为什么失败 → 基于哪几条证据 → 根因是什么 → 该怎么改 → 改完是不是真的变好了
```

完整闭环：

```text
Evaluation → Failure Detection → Diagnosis → Evidence → Root Cause
     → Recommendation → Version Comparison → Regression
```

定位是**单机工具**，不是 SaaS。本地跑，自己用。

---

## 2. 两条设计主线

这两条贯穿整个代码库，也是理解这个项目为什么长这样的关键。

### 2.1 诊断必须走证据链，不能从分数倒推

绝大多数评估工具止步于「分数低」。本项目强制要求：任何一条 Diagnosis 都必须由 **Evidence 契约**支撑。

- 诊断是 **规则优先（rules-first）**，LLM 只在规则不可判定时兜底。
- Evidence 必须是结构化的 `{contract: "<type>.v1", items: [{type, source, locator, content}]}`，**禁止扁平字符串数组**。
- 契约不满足 → 抛 `InsufficientEvidenceError` → 该记录落 `undetermined`。
- **禁止 `evidence.length >= 1` 这类兜底规则**，也禁止任何「分数低所以根因是 X」的推导。
- 规则不可判定且 Judge 不可用时，输出 `comparison_type: "ambiguous"`、`score: null`、`passed: null` —— **绝不编一个看起来合理的分数**。Run 因此标记为 `completed_with_errors`。

这条主线的直接后果：`undetermined` 与「失败」在数据模型里是两种东西，报告里也分开展示，不会混同。

### 2.2 业务层不许按引擎名字分支

引擎通过 `EvaluationEngine` Protocol + `MetricRegistry` 注册，装配由 `engines/factory.build_engines()` 依据 `MetricSpec.engine` 分组完成，逐记录执行由 `engines/pipeline.run_record()` 合并各引擎结果。

```text
✗ if engine == "ragas": ...        # 明确禁止（ADR-03）
✓ 按注册表 spec 结构性分派
```

这条是 spec 明文规定的静态检查项（I-8 之外的 ADR-03）。**注意：本仓库没有 CI pipeline，所以它是 review 时人工保证的，不是自动化门禁。**

同理，加权总分 `overall_score` **不是一个引擎**：它在 Run 完成时由 `services/report_service.compute_overall_score()` 依据快照里的 `metric_weights` 算一次，之后从持久化的 Run 读回，不参与引擎执行。

---

## 3. 技术架构

### 3.1 分层

分层单体 + 可插拔引擎注册表。强制访问链：

```text
FastAPI → Service → Repository → SQLAlchemy 2.0 → psycopg[binary] → PostgreSQL 17
```

| 目录 | 职责 |
|------|------|
| `app/api/` | 8 个路由（projects / datasets / evaluations / comparisons / configs / config_import / judge_settings / health） |
| `app/services/` | 业务编排，**永不直接碰 Session/ORM** |
| `app/domain/` | Pydantic schema、invariants |
| `app/adapters/` | 外部 RAG 输入边界（`rag_input.py`） |
| `app/datasets/` | 数据集解析与校验 |
| `app/repositories/` | **唯一数据访问边界**（ORM 查询 + 事务） |
| `app/models/` | SQLAlchemy 声明式模型 |
| `app/db/` | Engine / SessionLocal / 连接池 / 健康检查 |
| `app/runner/` | `EvaluationRunner` Protocol + `LocalAsyncRunner` |
| `app/engines/` | 引擎实现与装配（ragas / integrity / pipeline / factory / judge） |
| `app/metrics/` | 注册表 + Integrity 指标实现 |
| `app/diagnosis/` | 引擎 / 分类 / 规则 / 证据 / 建议 |
| `app/core/` | 配置 / 日志 / 错误 |

禁止方向：Engine→Service、Domain→FastAPI、Engine A→Engine B、Service→Session/ORM、Service→supabase SDK。

规模（2026-09-28 实测 `find`）：`app/` 下 89 个 `.py`；`frontend/src/` 下 56 个 `.ts/.tsx`。

### 3.2 数据模型

10 张业务表，由 Alembic 管理：

```text
projects · datasets · dataset_records · evaluation_configs · evaluation_runs
evaluation_results · metric_results · diagnoses · recommendations · metric_definitions
```

迁移链 **3 个 revision，线性无分叉**：`0001 → 0002 → 0003 (head)`（实测 `alembic/versions/` 目录）。

`POST /api/evaluations` 的核心是一次单事务：

```text
SELECT ... FOR UPDATE（锁定 dataset 行）
  → 校验 is_locked == false
  → 插入 run（status=pending + 8 个复现字段）
  → 置 datasets.is_locked = true
  → COMMIT → 202 {run_id, status, reproducibility_meta}
```

Run 状态：`pending, running, completed, completed_with_errors, failed, cancelled`（无百分比阈值）。8 个强制复现字段：`dataset_version, config_version, metric_version, prompt_version, judge_model, judge_model_version, model_version, timestamp`。

数据集版本不可变（ADR-06）：`is_locked` 是硬约束，改数据只能建新版本。

### 3.3 指标与失败分类

指标共 **7 个**（2 个引擎）：

| 引擎 | 指标 |
|------|------|
| RagasEngine | `faithfulness`、`answer_relevancy`、`context_recall`、`context_precision` |
| IntegrityEngine | `entity_consistency`、`temporal_consistency`、`numerical_consistency` |

Integrity 走 **Deterministic-first Hybrid**（ADR-04）：抽取 → 归一化（别名 / 单位 / 日期解析）→ 确定性比较 → 容差与等价判定 → **仅在不确定时才调 LLM**。每个结果必须带 `comparison_basis`；时间必须解析成区间（不做原始字符串比较）；数值必须抽数 → 单位归一 → 容差（不做字符串相等）。

失败分类法共 **14 个 code**（实测 `app/diagnosis/taxonomy.py`）：

```text
retrieval.*   (7)  top_k_issue / ranking_issue / chunking_issue / query_rewrite_issue
                   metadata_filter_issue / knowledge_coverage / missing_evidence
generation.*  (3)  unsupported_claim / irrelevant_answer / partial_answer
integrity.*   (4)  entity_mismatch / numerical_mismatch / temporal_mismatch / unit_mismatch
```

**「Financial Hallucination」不是指标**，它是 `integrity.*` + `generation.unsupported_claim` 的集合称呼。

### 3.4 三层配置

优先级（后者覆盖前者）：

```text
config/system.yaml  <  config/domains/<domain>.yaml  <  config/evaluations/<profile>.yaml  <  Run 级覆盖
```

- Domain YAML 只**推荐**指标（`recommended_metrics`），**绝不含阈值**。
- Profile YAML 才有 `threshold` / `weight`；`threshold: null` 表示不做 PASS/FAIL 判定。
- Run 创建时快照 `config_version = sha256(yaml_dump(merged config))`。
- 历史免疫：Profile 之后改版，不影响已完成 Run 的结果与门禁判定（有测试锁定）。
- **无运行时热重载**（ADR-05）；YAML 运行时持久化 = by design 不实现。

实测 `config/` 下现有 5 个文件：`system.yaml`、`domains/general.yaml`、`evaluations/{default,e2e,e2e_gate_disabled}.yaml`。

平台**不内置任何行业默认阈值或严重度**——前端也不许硬编码，只能从当前 Profile 渲染。

---

## 4. 部署形态（真实情况）

**日常跑的是本机 Supabase CLI 自建栈。**

```text
数据库   supabase start → 本机 Docker 容器 → PostgreSQL 17
         实际连接：127.0.0.1:54322
应用     本机 uvicorn app.main:app --reload
前端     本机 Vite dev server（5173，/api 代理到 8000）
```

端口来自 `supabase/supabase/config.toml`（实测）：api `54321`、db `54322`、shadow `54320`、studio `54323`，`major_version = 17`。

`.env.local` 实测状态：

| 键 | 状态 |
|---|---|
| `DATABASE_URL` | 指向 `127.0.0.1:54322`（本地栈） |
| `ALEMBIC_DATABASE_URL` | 空 → 回退 `DATABASE_URL` |
| `TEST_DATABASE_URL` | 空（测试配置在 `.env.test` 里） |
| `RAGEVAL_ENV` | `dev` |
| `JUDGE_PROVIDER` / `JUDGE_MODEL` | `openai` / `deepseek-v4-flash` |

云端连接串以注释形式保留在 `.env.local`（标记 `CLOUD-BACKUP`），属备份，不参与运行。

**切换到 Supabase Cloud 是可选路径**：改 `.env.local` 的 `DATABASE_URL` 即可，代码零改动。见 `docs/deployment/SUPABASE_CLOUD.md`。

### 关于 Supabase 的边界（不变）

改部署形态**不等于**推翻架构约束：

- Supabase **只作为 PostgreSQL 的托管/运行载体**，不是 BaaS。
- 不引入 `supabase-py` / `supabase-js` / Auth / Storage / Realtime / Self-Hosted。
- **Alembic 是唯一建表途径**，禁止在 Dashboard/Studio 手工建表、禁止 `supabase db push`。
- 不碰 Supabase 平台 schema（`auth` / `storage` / `realtime` / `supabase_*`）。
- 不引入 Redis / Celery / ARQ / Kafka / Kubernetes / OpenTelemetry SDK；异步用 `BackgroundTasks` + asyncio。**同一数据集**由 `datasets.is_locked` 行锁互斥（ADR-06：锁上后不可重跑，需新版本），**不同数据集可并发**，记录级并发是 `LocalAsyncRunner` 的 `asyncio.Semaphore`。**没有进程级的 Run 锁** —— 因此 per-run 状态绝不可挂在模块级共享对象上。

`docker/docker-compose.yml` 只有一个 `app` service，没有 db service、没有 `depends_on: db`、没有 pgdata 卷。

---

## 5. 真实资产

### 5.1 前端

15 个真实页面（`frontend/src/pages/*.tsx`），路由一一对应（实测 `App.tsx`）：

```text
/                      /projects              /evaluations
/evaluations/new       /evaluations/:runId    /compare
/regression            /quality-gate          /datasets
/datasets/import       /datasets/:datasetId   /failures
/profiles              /metrics               /settings
```

技术栈：React 18 + TypeScript + Ant Design + ECharts + Axios（Vite，target `es2020`）。

进度轮询：`status ∈ {pending, running}` 时每 1.5s 打 `/api/evaluations/{id}/progress`，到终态停止并拉完整报告。

### 5.2 评测与基准工作

`benchmarks/` 里有真实的数据集构建与分析脚本，不是占位目录：

| 来源 | 用途 |
|------|------|
| RAGTruth | 生成式幻觉标注（`build_ragtruth_golden*.py`、`analyze_ragtruth_*.py`） |
| MIRACL（zh） | 检索侧诊断（`run_miracl_retrieval_diagnosis_l4.py`） |
| TAT-QA | 数值/表格类污染与一致性（`run_tatqa_contamination_l1.py`） |

外加 `run_integrity_l2_contamination.py`、`run_faithfulness_bear_attribution_l3.py`、`phase8b_audit.py` 等分层实验脚本。原始下载保留在 `benchmarks/raw/`，产物在 `benchmarks/processed/`。数据来源与许可见 `benchmarks/PUBLIC_DATASETS.md`。

`benchmarks/v0.1/cases.json` 是确定性回归基准（Compare / Regression / Quality Gate 的精确复现基准）。

### 5.3 E2E harness

`scripts/run_e2e.py`（约 32 KB）是真实端到端验收脚本，走真实 uvicorn + httpx，三个阶段：

```text
A. 真实 API 流程
B. 库内审计，与 API 响应交叉核对
C. no-re-execution spy —— 只读服务在真实库上必须仍然成功
```

另有两个只读运维脚本：`scripts/run_benchmark.py`、`scripts/final_db_audit.py`。

### 5.4 测试

| 套件 | 位置 | 规模（2026-09-28 实测） |
|------|------|------|
| 后端 | `tests/`（47 个 `.py`） | 540 collected → 540 passed / 0 skipped |
| 前端 | `frontend/tests/`（7 个文件） | 63 passed |

后端含 13 个 PostgreSQL 集成测试；本地栈启动后**全部真跑通过**（13/13，见 §6.1）。

---

## 6. 当前实测状态（2026-09-28）

命令与原始结果：

```bash
.venv/Scripts/python.exe -m pytest -q
#   534 tests collected
#   521 passed, 13 skipped, 30 warnings in 52.73s

.venv/Scripts/python.exe -m ruff check .
#   All checks passed!

cd frontend && npx vitest run
#   Test Files  7 passed (7)
#        Tests  63 passed (63)
```

### 6.1 集成测试为什么 skip —— 原因已经变了

| | 早期 | 现在 |
|---|---|---|
| `TEST_DATABASE_URL` | 未配置 | **已在 `.env.test` 中配置**（指向本地栈的 `rageval_test` 库） |
| skip 原因 | 「未配置」 | **「已配置，但本地栈没启动 → unreachable」** |

skip 数量碰巧仍是 13，但**语义完全不同**了。汇总行会打印真实原因，例如
`Integration tests skipped: 13 (TEST_DATABASE_URL unreachable: OperationalError)`，
不再用「not configured or unreachable」这种二选一的含糊措辞。

当前本机实测（2026-09-28 晚）：**本地栈已启动** —— `supabase start` 拉起，54321-54327 均监听，`rageval_test` 可达（PostgreSQL 17.6，11 张表），Docker Desktop 运行中。所以这 13 个集成测试**已真跑并全部通过**（13/13）。此前的「栈未启动 → unreachable → NOT VERIFIED」是同一会话早些时候的状态。

要真正验证：

```bash
supabase start
pytest
```

### 6.2 已复现的告警（30 条 warning 中的真实项）

| 来源 | 内容 |
|------|------|
| `app/engines/ragas.py:121` | 从 `ragas.metrics` 导入指标已弃用，RAGAS v1.0 将移除，应改用 `ragas.metrics.collections` |
| 同上（间接） | `ragas.embeddings.LangchainEmbeddingsWrapper` 已弃用 |
| `alembic/config.py:604` | 未配置 `path_separator`，回退到 legacy 分隔逻辑 |

这些都是**依赖侧的弃用预警，当前不影响功能**，但升级 RAGAS 前需要处理。

---

## 7. 已知限制（诚实清单）

1. ~~**13 个 PostgreSQL 集成测试当前未验证**（§6.1），因为本地栈没起。~~ **已解除（2026-09-28 晚）**：本地栈启动后 13/13 真跑通过。
2. **没有 CI pipeline**。ADR-03（业务层不许按引擎名分支）、I-8（`api/`/`services/`/`domain/` 下不许 import supabase）这类 spec 静态检查，在本仓库是 **review 时人工保证**，不是自动化门禁。
3. **引擎级 Pipeline 选择 UI 缺失**（FR-12 剩余项）。指标级的启用/阈值/权重覆盖是全链生效的；引擎级选择只有后端能力，没有 UI。
4. **Run 级 RAG Input 配置无后端 API**，当前由 `system.yaml` 决定，UI 如实标注为 Backend Gap。
5. **Settings 页的 Judge / RAG / Evaluation 参数是只读声明**（「由环境变量管理」），没有对应的写入端点。
6. **PDF 导出、ARGUS 引擎、LLM 诊断展示** 属于 P1/P2，未实现。
7. **YAML 运行时持久化 by design 不做**（ADR-05），Profiles 页只读。
8. 本机代理会拦截 `127.0.0.1` 回环，跑 `test_14_api_chain_uses_real_http_rag_adapter` 需要先清空代理环境变量（环境问题，非代码缺陷）。

更完整的验收矩阵与历史判定见 `docs/releases/MVP_FINAL_ACCEPTANCE.md`。

---

## 8. 数字来源说明

| 数字 | 来源 | 状态 |
|------|------|------|
| 534 / 521 / 13 / 30 warnings | `pytest -q` 实测 | ✅ 实测 2026-09-28 |
| 63 / 7 files | `npx vitest run` 实测 | ✅ 实测 2026-09-28 |
| ruff All checks passed | `ruff check .` 实测 | ✅ 实测 2026-09-28 |
| 15 页面 / 15 路由 | `ls frontend/src/pages/*.tsx`、`App.tsx` 实测 | ✅ 实测 2026-09-28 |
| 10 张表 / 3 个 revision | `app/models/__init__.py`、`alembic/versions/` 实测 | ✅ 实测 2026-09-28 |
| 14 个失败 code | `app/diagnosis/taxonomy.py` 实测 | ✅ 实测 2026-09-28 |
| 89 / 56 个源文件 | `find` 实测 | ✅ 实测 2026-09-28 |
| 端口 54320-54323 / PG 17 | `supabase/supabase/config.toml` 实测 | ✅ 实测 2026-09-28 |
| `DATABASE_URL` → `127.0.0.1:54322` | `.env.local` 实测（只看 host，密码打码） | ✅ 实测 2026-09-28 |
| 7 个指标 | 代码注册表（`ragas.py` / `integrity.py` 的 `MetricSpec`） | ✅ 代码可查 |
| 13 个集成测试**通过与否** | pytest 实测 | ✅ **13/13 通过**（2026-09-28，本地栈已启动） |
| 前端 / 后端 E2E 通过数（75/75、50/50） | `MVP_FINAL_ACCEPTANCE.md` §7/§5 记录 | ⚠️ 历史记录（2026-09-01），本次未重跑 |
| Benchmark v0.1 全维度 100% | `MVP_FINAL_ACCEPTANCE.md` §8 | ⚠️ 历史记录（2026-09-01），本次未重跑 |

**凡标 ⚠️ 的都是历史记录，不是本次实测。** 引用前请自行重跑对应脚本。
