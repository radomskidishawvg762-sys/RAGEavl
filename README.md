# RAGEval Studio

> **RAG Quality Evaluation & Diagnosis Platform**

面向 RAG 开发者与 AI 应用工程团队的 RAG 质量工程平台：

```text
Evaluation → Failure Detection → Diagnosis → Evidence → Root Cause
     → Recommendation → Version Comparison → Regression
```

不仅告诉用户「分数是多少」，还告诉用户「为什么失败」「基于什么证据」「修改后是否真的变好」。

| 文档 | 说明 |
|------|------|
| [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md) | 项目介绍：真实架构、真实资产、当前实测状态 |
| [`RAGEval_Studio_PRD_v1.0.md`](RAGEval_Studio_PRD_v1.0.md) | 需求规格说明书（v1.3） |
| [`RAGEval_Studio_Spec_v1.0.md`](RAGEval_Studio_Spec_v1.0.md) | 技术设计规格说明书（v2.1，设计事实来源） |
| [`docs/deployment/SUPABASE_CLOUD.md`](docs/deployment/SUPABASE_CLOUD.md) | 数据库部署：本地栈（主）/ Supabase Cloud（可选切换） |
| [`docs/releases/MVP_FINAL_ACCEPTANCE.md`](docs/releases/MVP_FINAL_ACCEPTANCE.md) | MVP Freeze Gate 验收报告（含 2026-09-28 状态复审） |

---

## 部署形态（重要）

**日常开发跑的是本机 Supabase CLI 自建栈**，数据库监听 `127.0.0.1:54322`。应用本身直接跑在本机（`uvicorn`），不依赖容器。

```text
数据库：  本机 Supabase CLI 栈（supabase start），PostgreSQL 17
          → DATABASE_URL 指向 127.0.0.1:54322
应用：    本机 Python 进程（uvicorn app.main:app --reload）
前端：    本机 Vite dev server（端口 5173，/api 代理到 :8000）
```

默认端口（来自 [`supabase/supabase/config.toml`](supabase/supabase/config.toml)）：

| 服务 | 端口 |
|------|------|
| API（PostgREST） | 54321 |
| **PostgreSQL** | **54322** ← 应用连这个 |
| shadow DB | 54320 |
| Studio | 54323 |

> **本地栈需要 Docker Desktop 在运行**（Supabase CLI 通过 Docker 拉起容器）。栈未启动时，`/api/health` 会如实返回 `database: "unavailable"`，PostgreSQL 集成测试会显式 skip（见「测试」节）。

**切换到 Supabase Cloud（可选）**：把 `.env.local` 的 `DATABASE_URL` 换成云端连接串即可，代码零改动。见 [`docs/deployment/SUPABASE_CLOUD.md`](docs/deployment/SUPABASE_CLOUD.md)。

`docker/docker-compose.yml` 仍然存在，作用是**只把 app 打包成一个容器**，数据库不在其中。

---

## 快速启动

### 0. 前置条件

```text
[ ] Python ≥ 3.12（本项目实测 3.12.10）
[ ] Node.js + npm（前端 Vite / vitest）
[ ] Supabase CLI（本项目实测 v2.116.0）
[ ] Docker Desktop 处于运行状态 —— 供 Supabase CLI 拉起本地栈
[ ] 无需 Supabase 账号：本地栈是自建的
```

### 1. 启动数据库（本地栈）

```bash
supabase start          # 在仓库根目录执行（配置位于 supabase/）
supabase status         # 打印 API URL / DB URL / Studio URL
```

首次 `start` 需要拉取镜像，耗时较长；之后是秒级启动。

### 2. 写入 DATABASE_URL

```bash
cp .env.example .env.local
```

编辑 `.env.local`（**不提交 Git**）：

```env
# 本地栈（默认）
DATABASE_URL=postgresql+psycopg://postgres:<password>@127.0.0.1:54322/postgres
ALEMBIC_DATABASE_URL=
TEST_DATABASE_URL=
RAGEVAL_ENV=dev
```

规则：

```text
✗ 不写入代码 / config/*.yaml / 日志 / Issue
✓ .env.local、.env.test 必须加入 .gitignore
✓ 连接串含密码，等同 Secret
```

> 实测：`.env.local` 中 `DATABASE_URL` 指向 `127.0.0.1:54322`，`ALEMBIC_DATABASE_URL` 与 `TEST_DATABASE_URL` 为空（回退语义见下）。`.env.test` 中 `TEST_DATABASE_URL` 已配置，指向同一本地栈的 `rageval_test` 库。

### 3. 执行 Alembic Migration

**这是建表的唯一途径。禁止在 Supabase Dashboard / Studio 手工建表。**

```bash
alembic upgrade head
```

预期创建 10 张业务表：

```text
projects · datasets · dataset_records · evaluation_configs · evaluation_runs
evaluation_results · metric_results · diagnoses · recommendations · metric_definitions
```

当前迁移链为 3 个 revision，线性无分叉：`0001 → 0002 → 0003 (head)`。

### 4. 启动 FastAPI

```bash
# 方式 A：本机开发（日常主路径）
uvicorn app.main:app --reload

# 方式 B：Docker Compose（仅 app 容器，数据库仍需外部可达）
docker compose -f docker/docker-compose.yml up --build
```

### 5. 启动前端

```bash
# 方式 A：在项目根目录启动（自动转发到 frontend）
npm run dev

# 方式 B：进入前端目录启动
cd frontend
npm install
npm run dev
```

### 6. 健康检查

```bash
curl http://localhost:8000/api/health
```

响应**区分应用与数据库两个状态**，数据库不健康时返回 **503**：

```json
{
  "app": "healthy",
  "database": "healthy",
  "detail": { "db_latency_ms": 12 }
}
```

`database: "unavailable"` 时按 [`docs/deployment/SUPABASE_CLOUD.md`](docs/deployment/SUPABASE_CLOUD.md) 第 5.1 节排查。

---

## 环境变量

| 变量 | 用途 | 是否 Secret |
|------|------|------------|
| `DATABASE_URL` | 应用运行连接 | ✅ |
| `ALEMBIC_DATABASE_URL` | 迁移连接；为空回退 `DATABASE_URL` | ✅ |
| `TEST_DATABASE_URL` | 测试连接；**必须 ≠ `DATABASE_URL`** | ✅ |
| `RAGEVAL_ENV` | dev / test / demo | ❌ |
| `JUDGE_*` | Judge LLM 配置（provider / model / model_version / api_key） | ✅ |
| `RAG_INPUT_URL` | RAG 输入 Adapter；留空则只走 Golden Replay | ❌ |

完整说明见 [`.env.example`](.env.example) 与 Spec 附录 B.1.1。

配置来源优先级：环境变量 > `.env.test` > `.env.local`。

---

## 测试

### 后端

```bash
pytest
```

**2026-09-28 实测**（本机，本地栈未启动）：

```text
534 tests collected
521 passed, 13 skipped, 30 warnings in 52.73s
ruff check .: All checks passed
```

测试约束：

```text
✗ 不硬编码 localhost:5432 / postgres container / db service
✓ TEST_DATABASE_URL ≠ DATABASE_URL（pytest 启动即断言，相同直接报错退出）
✓ 测试库使用独立的 rageval_test 数据库（同一 PostgreSQL 实例上的独立 database），与开发库分离
```

### 前端

```bash
cd frontend && npx vitest run
```

**2026-09-28 实测**：

```text
Test Files  7 passed (7)
     Tests  63 passed (63)
```

用例位于 `frontend/tests/`（**不是** `frontend/src/`）。

### 集成测试（真实 PostgreSQL）

测试使用**专用测试库**，永不写开发库（`tests/conftest.py` 强制隔离）：

```text
✗ TEST_DATABASE_URL == DATABASE_URL → pytest 启动即报错退出
✓ 未配置 或 不可达 → 集成测试显式 skip，不计入 passed
```

结果解读（pytest 末尾的 `PostgreSQL integration tests` 汇总）：

```text
TEST_DATABASE_URL configured
Integration tests executed: N
Integration tests passed:   N      ← 真实 PostgreSQL 上执行并通过
Integration tests skipped:  N      ← 未配置/不可达，明确跳过（不是通过）
```

**关于当前 13 个 skip —— 原因已经变了，不只是数字：**

| | 早期状态 | **当前状态（2026-09-28）** |
|---|---|---|
| `TEST_DATABASE_URL` | 未配置 | **已在 `.env.test` 中配置** |
| skip 原因 | 「没配置」 | **「配了，但本地 Supabase 栈没启动（unreachable）」** |

汇总行打印的是 `not configured or unreachable` 这个二选一的措辞，容易误读；**判定当前属于哪一种，看 pytest 汇总行上一行的 `TEST_DATABASE_URL configured` 是否出现**——出现了，就说明是「不可达」而非「未配置」。

所以这 13 个集成测试当前状态是 **NOT VERIFIED**，不是「跳过所以没事」。要真正验证：

```bash
supabase start      # 先让本地栈起来
pytest              # 首次运行会自动对 rageval_test 执行 alembic upgrade head
```

已配置时，conftest 会在首跑自动对测试库执行 `alembic upgrade head`（Alembic 是唯一迁移体系），并在每个测试前后 `TRUNCATE` 全部业务表做隔离。

迁移测试要求（空库 → `upgrade head` → 建表 → CRUD → Run 端到端 → 回滚）见 Spec 5.7.4 与附录 E.5。

---

## 一键报告导出

Evaluation Detail 页面提供 **导出报告 PDF Export**（人读，双语同页，分页，含质量维度/失败/诊断/证据/无法判定/建议/可复现快照）与 **导出报告 JSON**（机读）。

```bash
curl -o report.pdf  "http://localhost:8000/api/evaluations/{run_id}/export"            # 默认 PDF
curl -o report.json "http://localhost:8000/api/evaluations/{run_id}/export?format=json"
```

严格只读：导出完全基于已持久化数据（ReportService → ExportService），不重跑 Engine / Judge / RAG / Diagnosis；null 不填 0，undetermined 不与 failure 混同，证据原样透传，凭据永不进入导出文件。

---

## 架构一览

```text
FastAPI
  ↓
Service            业务编排
  ↓
Repository         数据访问唯一边界
  ↓
SQLAlchemy 2.0 + psycopg
  ↓
PostgreSQL 17      本机 Supabase CLI 栈（默认）或 Supabase Cloud（可选）
```

- **数据库供应商只是基础设施，不是系统中心**；换供应商只需替换 `DATABASE_URL`
- 不引入 `supabase-py` / Supabase Auth / Storage / Realtime / Self-Hosted
- 不引入 Redis / Celery / MQ / Kubernetes（MVP 用 `BackgroundTasks` + asyncio）

决策依据见 Spec 第 9 章 ADR-01 ~ ADR-09。

---

## 技术栈

| 层 | 选型 |
|----|------|
| 前端 | React 18 + TypeScript + Ant Design + ECharts（Vite） |
| 后端 | Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2.0 |
| 驱动 | psycopg[binary] |
| 迁移 | Alembic（唯一建表途径） |
| 存储 | PostgreSQL 17（本机 Supabase CLI 栈 / 可选 Cloud） |
| 评估 | RAGAS + 自研 Integrity Metrics（Deterministic-first Hybrid） |
| 测试 | pytest + pytest-asyncio（后端） / vitest + Testing Library（前端） |
| 部署 | 本机进程为主；`docker/docker-compose.yml` 仅打包 app 容器 |
