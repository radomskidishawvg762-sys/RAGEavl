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
| [`docs/reviews/2026-10-11-judge-connectivity.md`](docs/reviews/2026-10-11-judge-connectivity.md) | 最新 Judge 连接修复、611 项后端检查及 20 案例实测；含评分误报分析 |

---

## 部署形态（重要）

**日常开发跑的是本机 Supabase CLI 自建栈**，数据库监听 `127.0.0.1:54322`。应用本身直接跑在本机（`uvicorn`），不依赖容器。

```text
数据库：  本机 Supabase CLI 栈（supabase --workdir supabase start），PostgreSQL 17
          → DATABASE_URL 指向 127.0.0.1:54322
应用：    本机 Python 进程（127.0.0.1:8000）
前端：    本机 Vite dev server（127.0.0.1:5173，/api 代理到 :8000）
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
[ ] Node.js 24 LTS + npm（前端 Vite / vitest；CI 使用 Node 24）
[ ] Supabase CLI（本项目实测 v2.116.0）
[ ] Docker Desktop 处于运行状态 —— 供 Supabase CLI 拉起本地栈
[ ] 无需 Supabase 账号：本地栈是自建的
```

### 1. 启动数据库（本地栈）

```bash
supabase --workdir supabase start
supabase --workdir supabase status
```

以上命令在仓库根目录执行，Windows PowerShell 与 macOS/Linux 通用。配置实际位于 `supabase/supabase/config.toml`，因此需指定 `--workdir supabase`。首次 `start` 需要拉取镜像，耗时较长。

### 2. 安装后端依赖并写入 DATABASE_URL

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,eval]"
Copy-Item .env.example .env.local
```

macOS/Linux：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,eval]"
cp .env.example .env.local
```

首次创建 `.env.local` 后编辑（**不提交 Git**；已有文件时直接编辑，勿再次复制覆盖）：

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

需要真实 Judge 时再填写 `JUDGE_*`；仅运行本地测试和 CI 时保持 Judge 未配置。测试库另在 `.env.test` 配置，必须与开发库隔离，见「集成测试」。

### 3. 执行 Alembic Migration

**这是建表的唯一途径。禁止在 Supabase Dashboard / Studio 手工建表。**

```bash
python -m alembic upgrade head
```

预期创建 10 张业务表：

```text
projects · datasets · dataset_records · evaluation_configs · evaluation_runs
evaluation_results · metric_results · diagnoses · recommendations · metric_definitions
```

当前迁移链为 3 个 revision，线性无分叉：`0001 → 0002 → 0003 (head)`。

### 4. 启动 FastAPI

激活虚拟环境后，Windows PowerShell、macOS/Linux 均执行：

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Docker Compose 为可选的 app 容器打包路径，数据库需从容器可达，见 `docker/docker-compose.yml`。

### 5. 启动前端

Windows PowerShell、macOS/Linux 均执行：

```bash
cd frontend
npm ci
npm run dev
```

依赖安装后也可在仓库根目录运行 `npm run dev`（自动转发到 `frontend`）。默认仅监听 `127.0.0.1:5173`。

### 仅本机访问与局域网访问

后端 Uvicorn 原生默认监听 `127.0.0.1`，上面的启动命令也显式指定该地址；前端由 `frontend/vite.config.ts` 显式指定 `127.0.0.1`。默认从本机访问 `http://127.0.0.1:5173` 和 `http://127.0.0.1:8000`。

需要局域网访问时，临时在两个终端使用以下命令（Windows PowerShell、macOS/Linux 通用）：

```bash
# 仓库根目录，已激活后端虚拟环境
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 另一个终端，在 frontend 目录
npm run dev -- --host 0.0.0.0
```

局域网客户端打开 `http://<本机局域网 IP>:5173`，前端 `/api` 仍通过 Vite 代理到本机后端。重启并恢复默认命令即可恢复仅本机监听。以上监听规则针对本机开发进程，Docker 容器网络单独配置。

### 6. 健康检查

Windows PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

macOS/Linux：

```bash
curl http://127.0.0.1:8000/api/health
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

### Judge 连接测试与重试

系统设置中的「测试连接」会对当前表单发起一次最小模型请求（最多 64 输出 token，最长 10 秒），可能产生少量调用费用。测试不保存表单；空 API Key 沿用已有密钥。只有「保存」才更新当前后端进程的配置，重启后恢复环境变量配置。密钥与额外路由请求头不会回显或写入报告。

`POST /api/settings/judge/test` 可不带请求体，也可提交待测试的配置字段。响应包括 `test_status`（`success` / `failed` / `not_configured`）、`latency_ms`，失败时包括 `error_code`、`error_category`、`upstream_status` 与 `retryable`。`configured` 仅代表配置完整；`reachable` 才表示本次实际请求成功。此测试只验证接口返回文本，结构化评分仍需实际评估验证。

| 错误类别 | 错误码 | 自动重试 |
|---|---|---|
| 密钥无效 / 模型权限不足 | `EXT_JUDGE_AUTHENTICATION` / `EXT_JUDGE_PERMISSION_DENIED` | 否 |
| 请求参数或协议错误 / 返回格式错误 | `EXT_JUDGE_REQUEST_INVALID` / `EXT_JUDGE_RESPONSE_INVALID` | 否 |
| 套餐额度耗尽 | `EXT_JUDGE_QUOTA_EXHAUSTED` | 否 |
| 临时限流 / 超时 / 网络故障 | `EXT_JUDGE_RATE_LIMITED` / `EXT_JUDGE_TIMEOUT` / `EXT_JUDGE_NETWORK` | 是 |
| 上游 5xx 服务故障 | `EXT_JUDGE_UNAVAILABLE` | 是 |

`retry` 表示包含第一次请求在内的最大尝试次数。Provider 统一负责重试，SDK 和 RAGAS 不再叠加请求；服务端要求等待超过 5 秒时直接返回限流信息。评估失败保留 `score: null` 与分类原因。

Judge 的 `localhost` / 回环 IP 地址直接连接，避免本机代理将本地模型请求转发成 502；远程模型地址继续沿用用户的代理设置。

---

## 测试

### 后端

最新完整复验与历史实测见 [2026-10-11 补跑报告](docs/reviews/2026-10-11-faithfulness-followup.md)。CI 不配置真实 Judge，现有 `test_golden_smoke_single_record` 按 `skipif` 跳过；其余测试全部执行，包括真实 PostgreSQL 集成测试。跳过不计为通过。

本机已有真实 `JUDGE_*` 配置时，无费用复验需在**当前终端**清空这些环境变量，覆盖本地 env 文件：

```powershell
# Windows PowerShell
$env:JUDGE_PROVIDER=''
$env:JUDGE_API_KEY=''
$env:RAGAS_DO_NOT_TRACK='true'
python -m ruff check .
python -m pytest -q -ra
```

```bash
# macOS/Linux
python -m ruff check .
JUDGE_PROVIDER='' JUDGE_API_KEY='' RAGAS_DO_NOT_TRACK=true python -m pytest -q -ra
```

测试约束：

```text
✓ 本地使用 Supabase CLI；CI 使用临时 PostgreSQL 17 service，不连接开发库或 Cloud
✓ TEST_DATABASE_URL ≠ DATABASE_URL（pytest 启动即断言，相同直接报错退出）
✓ 测试库使用独立的 rageval_test 数据库（同一 PostgreSQL 实例上的独立 database），与开发库分离
```

### 前端

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
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

`tests/conftest.py` 会分别报告「未配置」和「不可达」的实际原因。先在本地栈创建独立的 `rageval_test` 数据库，再在 `.env.test` 填写其连接串，保留 `.env.local` 的开发库连接不变：

```env
TEST_DATABASE_URL=postgresql+psycopg://postgres:<password>@127.0.0.1:54322/rageval_test
```

```bash
supabase --workdir supabase start
python -m pytest -q -ra
```

已配置时，conftest 会在首跑自动对测试库执行 `alembic upgrade head`（Alembic 是唯一迁移体系），并在每个测试前后 `TRUNCATE` 全部业务表做隔离。

迁移测试要求（空库 → `upgrade head` → 建表 → CRUD → Run 端到端 → 回滚）见 Spec 5.7.4 与附录 E.5。

### GitHub Actions CI

`.github/workflows/ci.yml` 在 push 到 `main` 或以 `main` 为目标的 pull request 时运行：后端 Python 3.12 安装 `.[dev,eval]`，执行 Ruff、专用测试库 Alembic 迁移及全量 pytest；前端 Node 24 LTS 执行 `npm ci`、类型检查、vitest 和 build。

数据库为 Actions 自带的临时 `postgres:17` service：`DATABASE_URL` 指向 `postgres` 库，`TEST_DATABASE_URL` / `ALEMBIC_DATABASE_URL` 指向独立的 `rageval_test` 库。数据库不可达或迁移失败会直接失败。CI 不读取任何真实 API Key，不调用真实 Judge；唯一允许跳过的是上述真实 Judge smoke 测试，其他跳过会使 CI 失败。

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
| 前端 | React 18 + TypeScript + React Router 6 + ECharts 5（Vite 5；原生 CSS） |
| 后端 | Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2.0 |
| 驱动 | psycopg[binary] |
| 迁移 | Alembic（唯一建表途径） |
| 存储 | PostgreSQL 17（本机 Supabase CLI 栈 / 可选 Cloud） |
| 评估 | RAGAS（默认启用 faithfulness、context_recall、context_precision；answer_relevancy 需要 embeddings，默认关闭）+ 自研 Integrity Metrics（Deterministic-first Hybrid） |
| 测试 | pytest + pytest-asyncio（后端） / vitest + Testing Library（前端） |
| 部署 | 本机进程为主；`docker/docker-compose.yml` 仅打包 app 容器 |
