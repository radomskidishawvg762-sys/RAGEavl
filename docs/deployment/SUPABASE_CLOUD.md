# Supabase Cloud 初始化说明

> | 文档 | Supabase Cloud 初始化说明 |
> |------|--------------------------|
> | 适用 | RAGEval Studio MVP |
> | 依据 | 《技术设计规格说明书》v2.1 |
> | 定位 | **部署操作文档**，不属于核心业务 Spec |

> ### 本文档的边界
>
> Supabase Cloud 在本项目中**只作为 PostgreSQL 的托管提供方**，不是 BaaS。
>
> ```text
> 应用仍然通过 SQLAlchemy 2.0 + psycopg 访问标准 PostgreSQL。
> 不引入 supabase-py / Supabase Auth / Storage / Realtime / Self-Hosted。
> ```
>
> 本文档的所有操作目标只有一个：让应用能通过 `DATABASE_URL` 连上一个空的 PostgreSQL，然后用 Alembic 建表。

---

## 0. 前置检查清单

```text
[ ] 已有 Supabase 账号
[ ] 本机已安装 Docker / Docker Compose（仅用于运行 app 容器）
[ ] 本机已安装 Python ≥ 3.12 与项目依赖
[ ] 项目根目录已有 .env.example
```

---

## 1. 创建 Supabase Project

1. 登录 Supabase 控制台，创建新 Project。
2. 选择**区域**：优先选择离你最近的区域，降低数据库往返延迟（本产品评估过程写入密集）。
3. 记录数据库密码 —— **只在此处设置，之后不要在聊天 / 代码 / 文档中传递**。
4. 等待 Project 初始化完成（约 1–2 分钟）。

> ⚠️ 免费档项目可能存在**自动暂停**行为。若长时间无连接后首次请求变慢，属预期现象；生产使用请评估付费档（见「待确认 S-9」）。

---

## 2. 获取数据库连接信息

在 Supabase 控制台获取 **PostgreSQL Connection String**。

Supabase 通常提供两类连接方式：

| 类型 | 端口特征 | 适用场景 |
|------|---------|---------|
| Direct connection | 5432 | Alembic 迁移、长连接、需要 session 级特性的操作 |
| Pooled connection（Supavisor） | 6543 | 应用运行时短连接、高并发 |

**MVP 建议**：

```text
起步阶段：DATABASE_URL 与 ALEMBIC_DATABASE_URL 均可指向 Direct connection
          （先把链路跑通，不要为了「看起来专业」而强制拆两个 Endpoint）

并发上来后：应用侧改指向 Pooled connection，迁移仍用 Direct
```

> 具体选择请结合 Supabase 控制台当前提供的连接信息与项目并发量决定（见 Spec 待确认 S-8）。

---

## 3. 设置 DATABASE_URL

复制环境变量模板并填入**你自己的**连接串：

```bash
cp .env.example .env.local
```

```env
# .env.local —— 不提交 Git
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<database>

# 迁移连接；留空则自动回退到 DATABASE_URL
ALEMBIC_DATABASE_URL=

# 测试连接；必须指向非生产库
TEST_DATABASE_URL=

RAGEVAL_ENV=dev
```

**硬性要求**：

```text
✗ 不要把真实连接串写进代码
✗ 不要把真实连接串写进 config/*.yaml
✗ 不要把真实连接串提交到 Git
✗ 不要把真实连接串贴进日志或 Issue
✓ .env.local / .env.test 必须加入 .gitignore
```

`config/system.yaml` 中通过环境变量引用，不写明文：

```yaml
system:
  database:
    url: ${RAGEVAL_DB_URL}
```

---

## 4. 执行 Alembic Migration

**这是建表的唯一途径。禁止在 Supabase Dashboard 手工一张表一张表创建。**

```bash
# 确认当前迁移状态
alembic current

# 建立 RAGEval 业务表
alembic upgrade head

# 确认已建表
alembic current
```

预期创建 **10 张业务表**：

```text
projects
datasets
dataset_records
evaluation_configs
evaluation_runs
evaluation_results
metric_results
diagnoses
recommendations
metric_definitions
```

> **不纳入业务 migration**：`auth` / `storage` / `realtime` / `supabase_*` 等 Supabase 平台自有 Schema。

---

## 5. 验证数据库

### 5.1 应用层验证

```bash
curl http://localhost:8000/api/health
```

期望响应：

```json
{
  "app": "healthy",
  "database": "healthy",
  "detail": {
    "db_latency_ms": 12
  }
}
```

若返回 `database: "unavailable"`，按以下顺序排查：

```text
1. DATABASE_URL 是否正确（含 sslmode 要求）
2. 网络是否可达 Supabase（防火墙 / 代理）
3. Supabase Project 是否处于暂停状态
4. 密码是否包含需要 URL 编码的特殊字符
```

### 5.2 数据层验证

```bash
# 应看到 10 张业务表
psql "$DATABASE_URL" -c "\dt"

# 基础 CRUD + 迁移回滚（仅限开发环境）
alembic downgrade -1 && alembic upgrade head
```

### 5.3 端到端验证

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

---

## 6. 配置备份

| 场景 | 策略 |
|------|------|
| 日常 | 依赖 Supabase 平台提供的快照 / PITR（按所选档位而定） |
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

## 7. 环境变量安全

| 文件 | 内容 | 是否提交 |
|------|------|---------|
| `.env.example` | 仅占位符模板 | ✅ 提交 |
| `.env.local` | 本地实际配置 | ❌ 不提交 |
| `.env.test` | 测试配置 | ❌ 不提交 |
| 生产 Secret | 通过 Secret Manager / 部署环境注入 | ❌ 不提交 |

`.gitignore` 必须包含：

```gitignore
.env.local
.env.test
.env
```

**测试隔离强制要求**：

```text
TEST_DATABASE_URL ≠ 生产 DATABASE_URL
```

`conftest.py` 中应有断言：检测到 pytest 使用生产 URL 时**直接报错退出**。

---

## 8. 停止使用本地数据库容器

本次迁移后，以下全部移除：

```text
✗ docker-compose.yml 中的 db service
✗ postgres:16-alpine（或任何 postgres 镜像）
✗ pgdata 数据卷
✗ 5432:5432 端口映射
✗ depends_on: db
✗ wait-for-postgres / 等待 db 容器启动的脚本
✗ 测试中对 localhost:5432 的硬依赖
✗ 健康检查中的 pg_isready / db 容器名
```

`docker-compose.yml` 只保留 app：

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env.local
    volumes:
      - ./config:/app/config:ro
      - ./exports:/app/exports
    # 注意：没有 db service，没有 depends_on: db
```

---

## 9. 启动顺序

```bash
# 1. 准备环境变量
cp .env.example .env.local
#    填入 DATABASE_URL

# 2. 建表
alembic upgrade head

# 3. 启动应用（仅 app 容器）
docker compose up --build

# 4. 验证
curl http://localhost:8000/api/health
```

> README 中**不要**再写「`docker compose up` 然后假定 PostgreSQL 会自动出现」。
> 数据库是外部预置服务，必须先完成第 1–2 步。

---

## 10. 常见问题

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| `database: "unavailable"` | 连接串错误 / 网络不可达 / 项目暂停 | 按 5.1 排查顺序逐项验证 |
| `SSL required` | 未启用 TLS | 连接串加 `sslmode=require` |
| 密码中的特殊字符导致连接失败 | 未做 URL 编码 | 对密码做 percent-encoding |
| 迁移报「relation already exists」 | 库里已有历史表 | 走 Spec 5.7.3 的兼容检查流程，**不要 drop** |
| 首次请求很慢 | 免费档项目自动暂停后恢复 | 预热连接；评估是否需要付费档 |
| 连接池耗尽 | 并发超过 Pooler 配额 | 降低 Judge 并发度；评估 pooled/direct 切换（S-8 / S-9） |

---

## 11. 与 Spec 的对应关系

| 本文章节 | Spec 位置 |
|---------|----------|
| 3. 环境变量 | 附录 B.1.1、4.3.1 |
| 4. Alembic | 5.7 迁移策略 |
| 5.2 健康检查 | 8.5 可观测、附录 C |
| 6. 备份 | 5.7.3、8.2 安全 |
| 8. 移除 db 容器 | 4.2 部署图、4.3 容器与配置 |
| 10. 连接池 | 8.3 性能、待确认 S-8 / S-9 |
