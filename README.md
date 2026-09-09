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
| `RAGEval_Studio_PRD_v1.0.md` | 需求规格说明书（v1.3） |
| `RAGEval_Studio_Spec_v1.0.md` | 技术设计规格说明书（v2.1） |
| `docs/deployment/SUPABASE_CLOUD.md` | Supabase Cloud 初始化说明 |

---

## ⚠️ 部署形态（重要）

```text
应用容器：本地 Docker Compose（仅 app，一个容器）
数据库：  Supabase Cloud 托管 PostgreSQL 17（外部预置服务，不随 Compose 启动）
```

**数据库是外部预置服务。** `docker compose up` 不会自动创建 PostgreSQL，必须先完成下文第 1–4 步。

---

## 快速启动

### 0. 前置条件

```text
[ ] Docker / Docker Compose
[ ] Python ≥ 3.12
[ ] Supabase 账号（创建 Project，获取 Connection String）
```

### 1. 创建 Supabase Project

在 Supabase 控制台创建 Project，记录：

- 区域（选离你最近的，评估过程写入密集）
- 数据库密码（只在此处设置，不要外传）

详细步骤见 [`docs/deployment/SUPABASE_CLOUD.md`](docs/deployment/SUPABASE_CLOUD.md)。

### 2. 写入 DATABASE_URL

```bash
cp .env.example .env.local
```

编辑 `.env.local`（**不提交 Git**）：

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<database>
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

### 3. 执行 Alembic Migration

**这是建表的唯一途径。禁止在 Supabase Dashboard 手工建表。**

```bash
alembic upgrade head
```

预期创建 10 张业务表：

```text
projects · datasets · dataset_records · evaluation_configs · evaluation_runs
evaluation_results · metric_results · diagnoses · recommendations · metric_definitions
```

### 4. 启动 FastAPI

```bash
# 方式 A：Docker Compose（仅 app 容器）
docker compose up --build

# 方式 B：本机开发
uvicorn app.main:app --reload
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

期望响应（**区分应用与数据库两个状态**）：

```json
{
  "app": "healthy",
  "database": "healthy"
}
```

`database: "unavailable"` 时按 [`docs/deployment/SUPABASE_CLOUD.md`](docs/deployment/SUPABASE_CLOUD.md) 第 5.1 节排查。

---

## 环境变量

| 变量 | 用途 | 是否 Secret |
|------|------|------------|
| `DATABASE_URL` | 应用运行连接 | ✅ |
| `ALEMBIC_DATABASE_URL` | 迁移连接；为空回退 `DATABASE_URL` | ✅ |
| `TEST_DATABASE_URL` | 测试连接；**必须 ≠ 生产 URL** | ✅ |
| `RAGEVAL_ENV` | dev / test / demo | ❌ |
| `JUDGE_*` | Judge LLM 配置 | ✅ |

完整说明见 `.env.example` 与 Spec 附录 B.1.1。

---

## 测试

```bash
# 必须先在 .env.test 中配置 TEST_DATABASE_URL
pytest
```

测试约束：

```text
✗ 不硬编码 localhost:5432 / postgres container / db service
✓ TEST_DATABASE_URL ≠ 生产 DATABASE_URL（pytest 启动即断言，相同直接报错）
✓ 测试库推荐：独立的 Supabase Test Project（与生产同为 PostgreSQL 17）
```

迁移测试要求（空库 → `upgrade head` → 建表 → CRUD → Run 端到端 → 回滚）见 Spec 5.7.4 与附录 E.5。

### 集成测试（真实 PostgreSQL）

测试使用**专用测试数据库**，永不写生产库（`tests/conftest.py` 强制隔离）：

1. 准备独立测试库（推荐单独的 Supabase Test Project，同为 PostgreSQL 17）
2. 在 `.env.test` 中配置（优先级：环境变量 > `.env.test` > `.env.local`）：

   ```env
   TEST_DATABASE_URL=postgresql+psycopg://<user>:<password>@<test-host>:<port>/<test-db>
   ```

3. 运行 `pytest` —— 首次运行会自动对测试库执行 `alembic upgrade head`（Alembic 是唯一迁移体系），每个测试前后 `TRUNCATE` 全部业务表做隔离

隔离规则：

```text
✗ TEST_DATABASE_URL == DATABASE_URL → pytest 启动即报错退出
✓ 未配置/不可达 → 集成测试显式 skip，不计入 passed
```

结果解读（pytest 末尾 PostgreSQL integration tests 汇总）：

```text
TEST_DATABASE_URL configured
Integration tests executed: N
Integration tests passed:   N      ← 真实 PostgreSQL 上执行并通过
Integration tests skipped:  N      ← 未配置/不可达，明确跳过（不是通过）
```

---

## 一键报告导出

Evaluation Detail 页面提供 **导出报告 PDF Export**（人读，双语同页，分页，含质量维度/失败/诊断/证据/无法判定/建议/可复现快照）与 **导出报告 JSON**（机读）。

```bash
curl -o report.pdf  "http://localhost:8000/api/evaluations/{run_id}/export"            # 默认 PDF
curl -o report.json "http://localhost:8000/api/evaluations/{run_id}/export?format=json"
```

严格只读：导出完全基于已持久化数据（ReportService → ExportService），不重跑 Engine / Judge / RAG / Diagnosis；null 不填 0，undetermined 不与 failure 混同，证据原样透传，凭据永不进入导出文件。

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
PostgreSQL 17      数据库事实标准
  ↓
Supabase Cloud     仅提供 Database Hosting
```

- **Supabase 只是基础设施，不是系统中心**；换供应商（RDS / Azure / 自建）只需替换 `DATABASE_URL`
- 不引入 `supabase-py` / Supabase Auth / Storage / Realtime / Self-Hosted
- 不引入 Redis / Celery / MQ / Kubernetes（MVP）

决策依据见 Spec 第 9 章 ADR-01 ~ ADR-09。

---

## 技术栈

| 层 | 选型 |
|----|------|
| 前端 | React + TypeScript + Ant Design + ECharts |
| 后端 | Python + FastAPI + Pydantic v2 + SQLAlchemy 2.0 |
| 驱动 | psycopg[binary] |
| 迁移 | Alembic |
| 存储 | PostgreSQL 17（Supabase Cloud 托管） |
| 评估 | RAGAS + 自研 Integrity Metrics（Deterministic-first Hybrid） |
| 部署 | Docker Compose（仅 app） |
