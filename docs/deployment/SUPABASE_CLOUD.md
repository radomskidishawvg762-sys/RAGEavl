# 数据库部署说明（本地栈为主，Supabase Cloud 为可选切换）

> | 文档 | 数据库部署说明 |
> |------|---------------|
> | 适用 | RAGEval Studio MVP |
> | 依据 | 《技术设计规格说明书》v2.1 |
> | 定位 | **部署操作文档**，不属于核心业务 Spec |
> | 状态 | 2026-09-28 重新定位：**本地栈是主路径，云端是可选切换** |

> ### 关于文件名
>
> 本文件历史上叫「Supabase Cloud 初始化说明」，因为它当时假设云端是唯一形态。
> **实际日常跑的是本机 Supabase CLI 自建栈**，所以内容已按真实情况重写。
> 文件名保留不改，是为了不破坏 README / CLAUDE.md 中已有的链接。
> 内容以本文件为准，不以文件名为准。

> ### 本文档的边界
>
> Supabase（无论本地栈还是 Cloud）在本项目中**只作为 PostgreSQL 的运行载体**，不是 BaaS。
>
> ```text
> 应用仍然通过 SQLAlchemy 2.0 + psycopg 访问标准 PostgreSQL。
> 不引入 supabase-py / supabase-js / Supabase Auth / Storage / Realtime / Self-Hosted。
> ```
>
> 所有操作目标只有一个：让应用能通过 `DATABASE_URL` 连上一个空的 PostgreSQL，然后用 Alembic 建表。
> **换形态 = 换 `DATABASE_URL`，代码零改动**（ADR-08）。

---

## 0. 默认形态：本机 Supabase CLI 自建栈（主路径）

### 0.1 前置检查清单

```text
[ ] Docker Desktop 处于运行状态（Supabase CLI 通过 Docker 拉起容器）
[ ] Supabase CLI 已安装（本项目实测 v2.116.0）
[ ] Python ≥ 3.12 与项目依赖已安装
[ ] 仓库根目录已有 .env.example
```

**不需要 Supabase 账号** —— 本地栈是自建的。

### 0.2 端口（来自 `supabase/supabase/config.toml`）

| 服务 | 端口 | 说明 |
|------|------|------|
| API（PostgREST） | 54321 | 本项目不使用 |
| **PostgreSQL** | **54322** | **应用连这个** |
| shadow DB | 54320 | 迁移 diff 用 |
| Studio | 54323 | 浏览器查看数据 |
| Pooler | 54329 | 默认 `enabled = false` |

`major_version = 17` —— 与云端保持同版本，避免迁移语义差异。

### 0.3 启动与配置

```bash
# 1. 拉起本地栈（首次需拉镜像，较慢；之后秒级）
supabase start

# 2. 查看实际连接信息
supabase status
```

编辑 `.env.local`（**不提交 Git**）：

```env
# 本地栈
DATABASE_URL=postgresql+psycopg://postgres:<password>@127.0.0.1:54322/postgres

# 迁移连接；留空则自动回退到 DATABASE_URL
ALEMBIC_DATABASE_URL=

# 测试连接（实际配置在 .env.test 中；见 §3）
TEST_DATABASE_URL=

RAGEVAL_ENV=dev
```

`config/system.yaml` 中通过环境变量引用，不写明文：

```yaml
system:
  database:
    url: ${RAGEVAL_DB_URL}
```

### 0.4 建表

**这是建表的唯一途径。禁止在 Studio / Dashboard 手工一张表一张表创建，禁止 `supabase db push`。**

```bash
alembic current        # 确认当前状态
alembic upgrade head   # 建立业务表
alembic current        # 确认已到 head
```

预期创建 **10 张业务表**：

```text
projects · datasets · dataset_records · evaluation_configs · evaluation_runs
evaluation_results · metric_results · diagnoses · recommendations · metric_definitions
```

当前链为 3 个 revision、线性无分叉：`0001 → 0002 → 0003 (head)`。

> **不纳入业务 migration**：`auth` / `storage` / `realtime` / `supabase_*` 等 Supabase 平台自有 Schema，**永不触碰**。

### 0.5 验证

```bash
curl http://localhost:8000/api/health
```

期望响应（数据库不健康时返回 **503**）：

```json
{
  "app": "healthy",
  "database": "healthy",
  "detail": { "db_latency_ms": 12 }
}
```

数据层验证：

```bash
psql "postgresql://postgres:<password>@127.0.0.1:54322/postgres" -c "\dt"   # 应见 10 张业务表
alembic downgrade -1 && alembic upgrade head                              # 回滚演练（仅开发环境）
```

### 0.6 停止

```bash
supabase stop            # 保留数据卷
supabase stop --no-backup  # 丢弃数据（谨慎）
```

---

## 1. 可选形态：切换到 Supabase Cloud

只有在需要「多处访问 / 长期托管 / 脱离本机 Docker」时才需要。**默认不要走这条路。**

### 1.1 创建 Project

1. 登录 Supabase 控制台，创建新 Project。
2. 选择**区域**：优先离你最近的，降低往返延迟（本产品评估过程写入密集）。
3. 记录数据库密码 —— **只在此处设置，之后不要在聊天 / 代码 / 文档中传递**。
4. 等待初始化完成（约 1–2 分钟）。

> ⚠️ 免费档项目可能存在**自动暂停**行为。若长时间无连接后首次请求变慢，属预期现象。

### 1.2 获取连接串

| 类型 | 端口特征 | 适用场景 |
|------|---------|---------|
| Direct connection | 5432 | Alembic 迁移、长连接、需要 session 级特性的操作 |
| Pooled connection（Supavisor） | 6543 | 应用运行时短连接、高并发 |

```text
起步阶段：DATABASE_URL 与 ALEMBIC_DATABASE_URL 均可指向 Direct connection
并发上来后：应用侧改指向 Pooled connection，迁移仍用 Direct
```

### 1.3 切换

只改 `.env.local` 一个键，**代码零改动**：

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<database>
```

然后按 §0.4 执行 `alembic upgrade head`，按 §0.5 验证。

> 从本地切到云端时要注意：**本地栈的 `rageval_test` 与云端不是同一个库**，集成测试需要单独准备云端测试库并更新 `.env.test`（见 §3）。

---

## 2. 共通：备份

| 场景 | 策略 |
|------|------|
| 本地栈日常 | 数据在 Docker 卷中；`supabase stop`（不带 `--no-backup`）保留 |
| 云端日常 | 依赖平台提供的快照 / PITR（按档位而定） |
| **任何迁移前** | **必须手动触发一次备份 / 快照** |
| 额外保障（可选） | 定期 `pg_dump` 到 `./exports` 或对象存储 |

迁移前流程（Spec 5.7.3）：

```text
Schema Inspection
  ↓
Migration Compatibility Check
  ↓
Backup / Snapshot   ← 不可跳过
  ↓
Alembic Upgrade
  ↓
Smoke Test
```

> **禁止 `Drop Database → Create Again`**，除非明确这是一次性全新 MVP 数据库。

---

## 3. 测试库隔离

`tests/conftest.py` 强制隔离，**测试永不写开发库**：

```text
✗ TEST_DATABASE_URL == DATABASE_URL → pytest 启动即报错退出
✓ 未配置 或 不可达 → 集成测试显式 skip，不计入 passed
✓ 已配置且可达 → 首跑自动对测试库执行 alembic upgrade head，每测试前后 TRUNCATE 隔离
```

当前 `.env.test` 中 `TEST_DATABASE_URL` **已配置**，指向本地栈的 `rageval_test` 库。
配置来源优先级：**环境变量 > `.env.test` > `.env.local`**。

> **注意当前的真实状态（2026-09-28）**：本地栈未启动时，13 个集成测试会 skip，
> 但**原因不是「未配置」而是「不可达」**。汇总行会直接打印真实原因，例如
> `Integration tests skipped: 13 (TEST_DATABASE_URL unreachable: OperationalError)`。
> 跑集成测试前先 `supabase start`。

---

## 4. 环境变量安全

| 文件 | 内容 | 是否提交 |
|------|------|---------|
| `.env.example` | 仅占位符模板 | ✅ 提交 |
| `.env.local` | 本地实际配置 | ❌ 不提交 |
| `.env.test` | 测试配置 | ❌ 不提交 |
| 生产 Secret | 通过 Secret Manager / 部署环境注入 | ❌ 不提交 |

硬性要求：

```text
✗ 不要把真实连接串写进代码 / config/*.yaml / 日志 / Issue
✓ .env.local、.env.test、.env 必须在 .gitignore 中
✓ 连接串含密码，等同 Secret
✓ 连接串格式保持 postgresql+psycopg://<user>:<password>@<host>:<port>/<database>（不换驱动）
```

---

## 5. 不使用本地 PostgreSQL 容器（仍然成立）

无论走本地栈还是云端，**应用自己的编排里都没有数据库容器**：

```text
✗ docker-compose.yml 中的 db service
✗ postgres:xx 镜像
✗ pgdata 数据卷
✗ 5432:5432 端口映射
✗ depends_on: db
✗ wait-for-postgres / 等待 db 容器启动的脚本
✗ 测试中对 localhost:5432 的硬依赖
✗ 健康检查中的 pg_isready / db 容器名
```

> **本地栈 ≠ 数据库容器。** 本地栈由 Supabase CLI 管理（`supabase start`），
> 不由 `docker-compose.yml` 管理，应用编排也不感知它。
> `docker/docker-compose.yml` 只有 `app` 一个 service。

```yaml
services:
  app:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - ../.env.local
    volumes:
      - ../config:/app/config:ro
      - ../exports:/app/exports
    # 注意：没有 db service，没有 depends_on: db
```

---

## 6. 启动顺序

```bash
# 1. 准备环境变量
cp .env.example .env.local       # 填入 DATABASE_URL

# 2. 拉起数据库（本地栈；走云端则跳过，改为确认云端可达）
supabase start

# 3. 建表
alembic upgrade head

# 4. 启动应用 —— 日常路径是本机进程
uvicorn app.main:app --reload
#    或容器化：docker compose -f docker/docker-compose.yml up --build

# 5. 验证
curl http://localhost:8000/api/health
```

> README 中**不要**写「`docker compose up` 然后假定 PostgreSQL 会自动出现」。
> 数据库要么由 `supabase start` 拉起，要么是外部可达的云端实例。

---

## 7. 端到端验证

按 Spec 第 43 节的顺序执行：

```text
1. 数据库连接          → /api/health
2. Alembic upgrade head → 10 张表
3. Project CRUD
4. Dataset Import
5. Evaluation Run
6. Metric Result
7. Diagnosis
8. Report
9. Compare
```

仓库内有真实 harness：`scripts/run_e2e.py`（真实 uvicorn + httpx + 真实 Judge）。

---

## 8. 常见问题

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| `supabase start` 失败 | Docker Desktop 没起来 | 启动 Docker Desktop 后重试 |
| `database: "unavailable"`（本地栈） | 栈没启动 / 端口被占 | `supabase status` 确认；确认 54322 无冲突 |
| `database: "unavailable"`（云端） | 连接串错误 / 网络不可达 / 项目暂停 | 按 §0.5 逐项验证；确认防火墙与代理 |
| 集成测试全部 skip | 本地栈未启动（不可达） | `supabase start` 后重跑；见 §3 |
| `ssl required`（云端） | 未启用 TLS | 连接串加 `sslmode=require` |
| 密码含特殊字符导致连接失败 | 未做 URL 编码 | 对密码做 percent-encoding |
| 迁移报「relation already exists」 | 库里已有历史表 | 走 Spec 5.7.3 的兼容检查流程，**不要 drop** |
| 首次请求很慢（云端） | 免费档自动暂停后恢复 | 预热连接；评估是否需要付费档 |
| 连接池耗尽 | 并发超过 Pooler 配额 | 降低 Judge 并发度；评估 pooled/direct 切换（S-8 / S-9） |

---

## 9. 与 Spec 的对应关系

| 本文章节 | Spec 位置 |
|---------|----------|
| 3. 环境变量 | 附录 B.1.1、4.3.1 |
| 0.4 / 1.3 Alembic | 5.7 迁移策略 |
| 0.5 健康检查 | 8.5 可观测、附录 C |
| 2. 备份 | 5.7.3、8.2 安全 |
| 5. 不使用本地 db 容器 | 4.2 部署图、4.3 容器与配置 |
| 8. 连接池 | 8.3 性能、待确认 S-8 / S-9 |
| 全文 | ADR-08（换供应商 = 换 `DATABASE_URL`） |
