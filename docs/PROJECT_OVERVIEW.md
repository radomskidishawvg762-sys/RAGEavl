# RAGEval Studio 项目介绍

> 本文档描述项目的**真实状态**。文中所有数字都标注了来源；实测的注明实测日期与命令，未验证的明确写「未验证」。不写没有依据的数字。
>
> 最后核对日期：**2026-10-12**

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

- 诊断是 **规则优先（rules-first）**。完整性比较的 LLM 兜底尚未实现：不确定时记 `ambiguous`，不调用模型。
- Evidence 必须是结构化的 `{contract: "<type>.v1", items: [{type, source, locator, content}]}`，**禁止扁平字符串数组**。
- 契约不满足时，`DiagnosisEngine` 返回 `undetermined` 并带上 `missing_evidence`，服务把这条诊断落库。`InsufficientEvidenceError` 只由 `assert_evidence_present` 在证据包没有任何条目时抛出；诊断引擎不抛它。`app/diagnosis/evidence.py` 里曾经有一份同名类，没有被抛出过，已删除。
- **禁止 `evidence.length >= 1` 这类兜底规则**，也禁止任何「分数低所以根因是 X」的推导。
- 确定性比较无法下结论时，输出 `comparison_type: "ambiguous"`、`score: null`、`passed: null` —— **绝不编一个看起来合理的分数**。这不是执行错误。执行错误（指标行带 `error` 且没有 `comparison_basis`）只出现在 `execution_errors`，不再另写一条 `undetermined` 诊断。

这条主线的直接后果：`undetermined` 与「失败」在数据模型里是两种东西，报告里也分开展示，不会混同。

### 2.2 业务层不许按引擎名字分支

引擎通过 `EvaluationEngine` Protocol + `MetricRegistry` 注册，装配由 `engines/factory.build_engines()` 依据 `MetricSpec.engine` 分组完成，逐记录执行由 `engines/pipeline.run_record()` 合并各引擎结果。

```text
✗ if engine == "ragas": ...        # 明确禁止（ADR-03）
✓ 按注册表 spec 结构性分派
```

这条是 spec 明文规定的静态检查项（I-8 之外的 ADR-03）。仓库有 CI（`.github/workflows/ci.yml`，push / pull request 到 `main` 时跑后端 pytest 和前端检查）。ADR-03 与 I-8（禁止在 `api/`、`services/`、`domain/` 下 import supabase）仍然没有单独的自动化测试，review 时仍要看。

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

规模（2026-10-12 实测）：`app/` 下 92 个 `.py`；`frontend/src/` 下 56 个 `.ts/.tsx`。

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
  → 校验数据集有效且非空、数据集/配置归属当前项目（已锁定版本可复用）
  → 插入 run（status=pending + 8 个复现字段）
  → 置 datasets.is_locked = true
  → COMMIT → 202 {run_id, status, reproducibility_meta}
```

Run 状态：`pending, running, completed, completed_with_errors, failed, cancelled`（无百分比阈值）。任一指标行带执行错误时，Run 记为 `completed_with_errors`，`error_summary.metric_errors` 记下条数。8 个复现字段：`dataset_version, config_version, metric_version, prompt_version, judge_model, judge_model_version, model_version, timestamp`。其中 `model_version` 允许为 null，含义是 **unavailable**（上游模型修订号拿不到，不拿模型别名冒充）。

数据集版本不可变（ADR-06）：`is_locked` 保护内容，不阻止对同一版本再跑评估。`DatasetLockedError`（409 `BIZ_DATASET_LOCKED`）是「若有人去改锁定内容」的错误契约；当前服务没有修改已锁定记录的入口，再导入会新建版本，这个异常类没有被服务层抛出。

### 3.3 指标与失败分类

注册表里有 **7 个**指标（2 个引擎）。默认 Profile 启用其中 6 个：

| 引擎 | 指标 |
|------|------|
| RagasEngine | 默认启用 `faithfulness`、`context_recall`、`context_precision`。`answer_relevancy` 需要 embeddings；仓库里没有真实 embeddings 实现，默认关闭。配置里显式启用而 embeddings 不可用时，创建 Run 会被拒绝 |
| IntegrityEngine | `entity_consistency`、`temporal_consistency`、`numerical_consistency` |

Integrity 走确定性比较（ADR-04）：抽取 → 归一化（别名 / 单位 / 日期解析）→ 确定性比较 → 容差与等价判定。**LLM 兜底尚未实现**。两侧都抽不到实体、数值或时间时，`comparison_type` 为 `not_applicable`，分数为 null，不进均值。没有 alias table 的新 Run 会在实体一致性上标明「该指标未生效」。每个结果必须带 `comparison_basis`；时间必须解析成区间（不做原始字符串比较）；数值必须抽数 → 单位归一 → 容差（不做字符串相等）。`comparison_basis.tolerance_applied` 记录数值比较实际使用的绝对/相对容差。

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
- 没有文件监视器，也没有进程内配置缓存。`ConfigService.load()` 每次调用都从磁盘重新读取 YAML。ADR-05 不把配置放进数据库、也不做热替换守护进程；下一次请求会看到磁盘上的新文件。

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
- 不引入 Redis / Celery / ARQ / Kafka / Kubernetes / OpenTelemetry SDK；异步用 `BackgroundTasks` + asyncio。数据集行锁仅串行创建事务；**同一或不同数据集的 Run 均可并发执行**（2026-10-11：锁定保护内容，不阻止再次评估），记录级并发是 `LocalAsyncRunner` 的 `asyncio.Semaphore`。**没有进程级的 Run 锁** —— 因此 per-run 状态绝不可挂在模块级共享对象上。

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

技术栈：React 18 + TypeScript + Vite + ECharts（target `es2020`）。HTTP 用 `fetch`。没有 Ant Design，没有 Axios。后端没有 CORS 中间件。

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

| 套件 | 位置 | 规模（2026-10-12 实测） |
|------|------|------|
| 后端 | `tests/`（67 个 `.py`） | 689 passed，1 skipped |
| 前端 | `frontend/tests/`（vitest 11 个文件） | 90 passed |

后端里有 19 个 PostgreSQL 集成测试；`TEST_DATABASE_URL` 已配置时这 19 个全部执行并通过。

---

## 6. 当前实测状态（2026-10-12）

命令与原始结果：

```bash
.venv/Scripts/python.exe -m pytest -q
#   689 passed, 1 skipped, 153 warnings in 14.42s
#   PostgreSQL integration tests executed: 19
#   Integration tests passed: 19

.venv/Scripts/python.exe -m ruff check .
#   All checks passed!

cd frontend && npm test
#   Test Files  11 passed (11)
#        Tests  90 passed (90)
```

### 6.1 集成测试为什么 skip —— 原因已经变了

| | 早期 | 现在 |
|---|---|---|
| `TEST_DATABASE_URL` | 未配置 | **已在 `.env.test` 中配置**（指向本地栈的 `rageval_test` 库） |
| skip 原因 | 「未配置」 | **「已配置，但本地栈没启动 → unreachable」** |

skip 数量碰巧仍是 13，但**语义完全不同**了。汇总行会打印真实原因，例如
`Integration tests skipped: 13 (TEST_DATABASE_URL unreachable: OperationalError)`，
不再用「not configured or unreachable」这种二选一的含糊措辞。

2026-09-28 晚那次本地栈启动后，当时的 13 个集成测试全部通过。2026-10-12 再测：集成测试已是 19 个，`TEST_DATABASE_URL` 已配置，19/19 通过。上面表格里的「13」是当时的数量，不是现在的数量。

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

1. ~~**PostgreSQL 集成测试未验证**~~ **已解除**：2026-10-12 实测 19/19 通过。
2. **CI 已有**（`.github/workflows/ci.yml`）。ADR-03（业务层不许按引擎名分支）和 I-8（`api/`/`services/`/`domain/` 下不许 import supabase）仍然没有单独的自动化测试，review 时还要看。
3. **引擎级 Pipeline 选择 UI 缺失**（FR-12 剩余项）。指标级的启用/阈值/权重覆盖是全链生效的；引擎级选择只有后端能力，没有 UI。
4. **Run 级 RAG Input 配置无后端 API**，当前由 `system.yaml` 决定，UI 如实标注为 Backend Gap。
5. **Settings 页 Judge 支持当前进程内修改与真实连接测试**；测试当前草稿且不保存，重启恢复环境变量配置。RAG / Evaluation 的系统默认参数仍只读。详见 README「Judge 连接测试与重试」。
6. **PDF/JSON 报告导出已实现**（2026-10-11 真实浏览器下载复验通过）；ARGUS 引擎、LLM 诊断展示仍待后续实现。
7. **部署 YAML 不由网页回写**（ADR-05）；Profiles 可导入 YAML 创建数据库中的配置版本，长期部署文件仍需本地编辑。
8. 本机代理会拦截 `127.0.0.1` 回环，跑 `test_14_api_chain_uses_real_http_rag_adapter` 需要先清空代理环境变量（环境问题，非代码缺陷）。

更完整的验收矩阵与历史判定见 `docs/releases/MVP_FINAL_ACCEPTANCE.md`。

---

## 8. 数字来源说明

| 数字 | 来源 | 状态 |
|------|------|------|
| 689 passed / 1 skipped / 153 warnings；其中 19 个集成测试通过 | `pytest -q` 实测 | ✅ 实测 2026-10-12 |
| 90 / 11 files | `npm test`（vitest）实测 | ✅ 实测 2026-10-12 |
| ruff All checks passed | `ruff check .` 实测 | ✅ 实测 2026-10-12 |
| 15 页面 / 15 路由 | `ls frontend/src/pages/*.tsx`、`App.tsx` 实测 | ✅ 实测 2026-09-28 |
| 10 张表 / 3 个 revision | `app/models/__init__.py`、`alembic/versions/` 实测 | ✅ 实测 2026-09-28 |
| 14 个失败 code | `app/diagnosis/taxonomy.py` 实测 | ✅ 实测 2026-09-28 |
| 92 / 56 个源文件 | `app/` 的 `.py`、`frontend/src/` 的 `.ts/.tsx` 计数 | ✅ 实测 2026-10-12 |
| 端口 54320-54323 / PG 17 | `supabase/supabase/config.toml` 实测 | ✅ 实测 2026-09-28 |
| `DATABASE_URL` → `127.0.0.1:54322` | `.env.local` 实测（只看 host，密码打码） | ✅ 实测 2026-09-28 |
| 7 个指标 | 代码注册表（`ragas.py` / `integrity.py` 的 `MetricSpec`） | ✅ 代码可查 |
| 19 个集成测试**通过与否** | pytest 实测 | ✅ **19/19 通过**（2026-10-12，`TEST_DATABASE_URL` 已配置） |
| 前端 / 后端 E2E 通过数（75/75、50/50） | `MVP_FINAL_ACCEPTANCE.md` §7/§5 记录 | ⚠️ 历史记录（2026-09-01），本次未重跑 |
| Benchmark v0.1 全维度 100% | `MVP_FINAL_ACCEPTANCE.md` §8 | ⚠️ 历史记录（2026-09-01），本次未重跑 |

**凡标 ⚠️ 的都是历史记录，不是本次实测。** 引用前请自行重跑对应脚本。
