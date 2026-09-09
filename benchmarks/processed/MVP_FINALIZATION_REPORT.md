# RAGEval Studio — MVP Finalization Report

> 日期：2026-09-07
> 范围：MVP 最终收尾审计（Engineering Complete / Pilot Ready 评估）
> 约束遵守：未新增/删除 Metric；MetricRegistry 保持 code-defined + read-only；未新增 Diagnosis Rule；未实现 LLM Diagnosis；未修改数据库 Schema；未引入 Redis/Celery/ARQ/Multi-Worker；Evidence-first / Deterministic-first 保持；未修改任何 threshold 或 metric semantics 来改善外观；未伪造 Compare / Regression / Quality Gate 结果。

---

## 1. 当前最终状态

| 项 | 状态 |
|---|---|
| 后端分层架构（api/services/repositories/domain/engines/metrics/diagnosis/runner/core/adapters） | ✅ 完整，分层约束（P-8、ADR-03、I-8）由测试锁定 |
| Metric Registry | ✅ 7 个 code-defined 指标（ragas 4 + integrity 3），只读镜像 `metric_definitions`，无 UI Custom Metric |
| Alembic 迁移 | ✅ 3 个 heads-locked revision，未改动 |
| 数据库 | ✅ Supabase Cloud PostgreSQL 17.6 可达（hosting only，无 supabase SDK） |
| 测试 | ✅ **507 passed, 13 skipped**（PostgreSQL 集成测试因未配置独立 `TEST_DATABASE_URL` 显式跳过，不计为通过）；ruff 全绿 |
| 前端 | ✅ typecheck ✓ · 63 tests ✓ · production build ✓ · 桌面+移动视觉验收通过 |
| Judge | ✅ GLM-5.3-flash via OpenAI 兼容网关；错误三态分离（见 §4） |

本阶段代码变更（全部为最小增量，未触碰 Frozen Interface / API Contract / DB Schema）：

```text
app/metrics/integrity/pairing.py            新增：数值多候选 deterministic 配对（§2）
app/engines/integrity.py                    数值 metric 多值记录走配对路径；metric_version -> integrity-normalization-v2
app/metrics/bootstrap.py                    日志引用版本常量（消除漂移）
app/adapters/rag_input.py                   HttpRagAdapter trust_env=False（修复 Windows 系统代理破坏 loopback RAG）
ruff.toml                                   per-file-ignores：alembic 模板风格（迁移文件为冻结产物，不重排）
frontend/src/layout/AppShell.tsx/.module.css 移动端抽屉侧边栏（≤768px），桌面渲染不变
frontend/src/layout/Sidebar.tsx             可选 onNavigate 回调（抽屉内点链接自动收起）
frontend/src/components/primitives/Layout.tsx/.module.css  Grid 列数改为 CSS 变量，移动端钳制 ≤2 列
frontend/src/pages/EvaluationDetailPage.module.css         execGrid 移动端 2×2
tests/test_numerical_pairing.py             新增：配对语义测试（12 项）
tests/test_judge_provider.py                新增：Provider retry->EXT / timeout->EXT 测试；版本断言改用常量
tests/test_fr12_pipeline_audit.py           ctx fixture 钉住未配置 Judge（环境无关的 hermetic 测试）
```

## 2. 多数值对齐结果（numerical_consistency pairing）

**风险确认**：原实现 `IntegrityEngine._primary` 取每侧**第一个 ok 候选**直接比较。当 reference/answer 任一侧含多个数值时，绑定是任意的——真实数据上会产生假 match 与假 mismatch。

**设计（最小 deterministic，value-blind）**：
- 亲和度信号（全部为确定性文本事实，**不使用数值本身**，避免"配对即匹配"的循环偏置）：
  单位 token 相同 +2.0；单位类别相同 +1.0；同句共享 temporal 锚点 +1.5；同句共享 entity 锚点 +1.5；相对位置接近 ≤+0.5。
- 跨单位类别（currency vs ratio）**永不配对**（比较器无法判定，绑定只会给歧义换名字）。
- **严格唯一才比较**：每个 answer 值必须取到严格最优 reference（并列仅允许内容相同的候选间按最低下标确定性取舍）；answer 值之间不得争用同一 reference；全部 answer 值都必须被配对。
- 配对成功 → 逐对 `compare_numerical`（Profile tolerance 原样传入），首个 0.0 决定记录级 mismatch。
- 配对失败 → (a) 双侧 (unit_class, base) 多重集精确相等 → match（`multiset-equality-v1`，仅处理无法绑定标签的重排）；(b) 否则 **ambiguous**（score=null，reason+亲和度矩阵进 `comparison_basis.diff`）。
- 单 ok 候选 1v1 记录走原快速路径，`comparison_basis` 形状不变。
- Reference-only 的遗漏值属于完整性问题，不在本 metric 范围内判定（P1 completeness 信号），不做静默宽恕。

**真实数据验证（loop_fix_v1 golden 50，只读离线运行，零写入零配额）**：
- 50 条中 **32 条**在至少一侧存在多 ok 候选；其中 18 条 1v1 快速路径不受影响。
- 旧语义在这 32 条上全部"已判定"——但绑定是任意的（16 match + 13 mismatch 为列表序号巧合，如 reference "1 Take… 2 Replace…" vs answer 重编号 "1. 2. 3."）。
- 新语义：**29 条诚实 ambiguous**（28 条多配对合理、1 条无正信号），**3 条获得唯一绑定**（row 5 match、row 30/31 value_mismatch——判定与旧语义一致，但带完整 couple 证据链）。
- 结论：多值记录的旧"分数"多数是不可靠绑定；新语义宁可 undetermined 也不猜测。未来 Run 的 numerical 有效行数会下降、undetermined 上升——这是 Evidence-first 的正确代价，**未放宽任何判定**。

## 3. Improvement Loop 结果：结构受限 BLOCKED（如实报告）

严格可比条件（同 Dataset Version / 同 Judge / 同 Profile / 同 Metrics / 仅一个 RAG 变量）**无法在不修改冻结行为的情况下满足**，因此未执行 Run A→Fix→Run B 的 Compare/Regression/Quality Gate，且**未伪造任何结果**。两条阻塞均已现场取证：

1. **同版本第二次 Run 被永久锁拒绝**（ADR-06 + 不变量族 I-1，测试 `test_evaluation_service.py::test_12c_second_run_on_same_dataset_blocked` 锁定）。
   现场探针：对 v5 数据集（locked=True）调用 `create_run_with_lock` → `BIZ_DATASET_LOCKED` / 409，`evaluation_runs` 行数 70→70（无任何持久化）。
2. **Compare 的 DIRECT 门要求同 `dataset_id`**（T-16B §三）。现存最接近的可比对：run `7be009fe`（v5）与 run `3b8cb849`（loop_fix_v1 v1）——**数据集内容逐行哈希完全一致**（question+reference md5 = `ba84f7b8a23ffc5d259f71b964b05900`）、同 Judge、同 config_version、同 6 指标——但 dataset 行不同 → 现场调用 ComparisonService（只读）：
   `comparability = BLOCKED ["runs target different datasets", "dataset version differs (v5 vs v1)"]`；overall delta = null, comparable=false；RegressionService → `NOT_COMPARABLE`。前端如实展示（§6）。

**解除阻塞需设计决策（超出本阶段授权，等待批准，三选一）**：
- A. 允许对已锁定版本顺序重跑（锁仅保护内容不被修改）——需修改 `create_run_with_lock` 语义 + 不变量测试 `test_12c`；
- B. 引入 dataset 内容哈希快照（reproducibility_meta 增列 `dataset_content_sha256`），Compare 门按内容一致性放行 LIMITED/DIRECT——需改 run 创建快照 + 比较门语义；
- C. 提供 API 级"重评副本"概念（同内容新版本的显式可比通道）。
- 备注：数值配对修复属于**评估器**变更（metric_version v1→v2），Compare 门会给出 LIMITED（metric version differs）而非阻断 BLOCKED 的唯一原因；严格同数据集版本的 Run A/B 在两种门下都被数据集身份检查阻断。

**Quality Gate 最终状态（诚实）**：`NOT_EVALUABLE`——Profile 未配置 `quality_gate`，且比较被 BLOCKED。前端如实显示"无法评估"。未依据 overall_score 伪造 PASS。

## 4. Judge 稳定性

架构与语义（既有设计，本阶段验证并补测试）：
- **三态错误永不混淆**：`BIZ_JUDGE_NOT_CONFIGURED`（配置缺失，RagasEngine 入口预检短路）/ `EXT_JUDGE_UNAVAILABLE`（429、超时、传输失败——Provider 层 retry=3 + 退避后包装，异常链 recovery 兜底）/ `SYS_METRIC_ERROR`（内部代码缺陷）。
- 错误 → `MetricResult.error`（score=null），**绝不改为低分或 undetermined**；Service 落库边界 `_normalize_result` 强制：`comparison_basis != null` 的判定结果 error 必须为 null，执行错误与质量判定在 DB 层即分离。
- Run 状态语义：全记录失败 → `failed`；部分错误 → `completed_with_errors`（Phase 9 真实 429 Run 已实证）。
- 本阶段新增测试：retry 后成功（2 次调用）、3 次重试耗尽 → `EXT_JUDGE_UNAVAILABLE`（消息含 429 根因）、timeout → `EXT_JUDGE_UNAVAILABLE`。
- 未引入 Redis / Celery / ARQ / Multi-Worker（ADR-02 保持）。

## 5. 测试结果

| 套件 | 结果 |
|---|---|
| 后端 pytest | **507 passed, 13 skipped**（集成测试无 `TEST_DATABASE_URL` 显式跳过，未计入 passed；`TEST_DATABASE_URL != DATABASE_URL` 硬约束在 conftest 强制） |
| ruff | All checks passed（含新增 pairing/pairing tests；alembic 模板风格经 per-file-ignores 豁免，迁移文件未改动） |
| 前端 typecheck (tsc) | 通过 |
| 前端 vitest | **63 passed**（7 files） |
| 前端 production build | 通过（3.4s） |
| 环境修复 | 此前 3 个依赖外部环境的旧测试失败（Windows 系统代理 127.0.0.1:7890 被 httpx 透明采用 → loopback RAG mock 502/超时；`.env.local` 真实 Judge 使 FR-12 真链路测试发真实网络请求超时）已根治：adapter `trust_env=False`（真实生产缺陷修复——MVP 本地单用户工具的 RAG 多在 loopback）+ FR-12 fixture 钉住未配置 Judge。现有测试与宿主环境（代理/Judge 配置）完全解耦。 |

## 6. 前端验收（桌面 1440px + 移动 390px，真实数据）

| 验收项 | 结果 |
|---|---|
| Quality Dimensions | ✅ Overview 表格：Retrieval 0.9714 / Generation 0.6857 / Groundedness 0.6857 / Correctness 0.8611，含各维度 Failures / Undetermined / Diagnosis Coverage / Evidence Coverage / 指标清单；**最弱维度 Weakest 高亮**（generation/groundedness 标红） |
| Overall Score 不掩盖局部弱项 | ✅ 维度表标题即注明"OVERALL SCORE 不代表单维度质量"；0.8742 总分旁直接暴露 0.6857 弱项与 15 个 correctness failures |
| Failures / Diagnostics / Evidence | ✅ Failures 15 条（严重度徽章+失败类型+指标+问题+分析入口）；Diagnostics：15 diagnosed（根因卡，如 "Numerical Mismatch (abs_diff=…, reference=…)"）与 65 undetermined **分列展示**（"cannot reliably judge (no forced diagnosis)"）；Evidence：contract 结构表（TYPE/SOURCE/LOCATOR/CONTENT，reference_evidence + answer_claim） |
| Compare 诚实性 | ✅ BLOCKED 呈现：不可比较徽章 + 后端原因逐条展示（"runs target different datasets"、"dataset version differs (v5 vs v1)"）+ 明确提示"以下仅展示后端返回的数据"；DELTA 置空"暂无有效分数"；指标 0 项——无任何捏造数字 |
| 质量门禁 | ✅ 如实显示"无法评估"（未配置 + 比较 BLOCKED） |
| 中英同页 | ✅ 全站双语（导航、KPI、表头、状态、空态均为中英并列） |
| 移动端 | ✅（本阶段修复后）≤768px 抽屉式侧边栏（汉堡开合、遮罩、路由点击自动收起）；KPI 网格 2×2，数值不再截断；报告/对比/数据集/仪表盘均可用 |
| Metrics 标签 | ✅ 按维度分组（检索/生成/一致性）；阈值列 "—" 忠实渲染 `threshold=null`（无捏造 PASS/FAIL） |

## 7. Secrets 审计

- 目录**尚不是 git 仓库**（无 .git）；`.gitignore` 已正确覆盖 `.env.local` / `.env.test` / `.env.*`（`!.env.example` 白名单）。
- 全库扫描（排除 .venv/node_modules/raw 数据）：无 `sk-`/JWT/Supabase key 形态字面量；无含凭据的连接串（唯一命中为 `config.py` 格式说明 docstring）；`.env.example` 仅占位符。
- 本地日志（.e2e_out.txt / .e2e_server.log）无 api_key / 授权头 / 连接串密码泄漏（日志脱敏过滤器生效）。
- 本次所有现场探针脚本仅输出主机/库名与哈希，未回显任何 Secret。

## 8. Remaining Gaps

| 缺口 | 状态 | 阻塞点 |
|---|---|---|
| 严格同版本 Improvement Loop（Compare/Regression/Quality Gate 实跑） | **BLOCKED** | 冻结不变量（一版本一 Run + DIRECT 同 dataset_id）；需 §3 设计决策之一 |
| `answer_relevancy` | BLOCKED | 网关 `/embeddings` 404，缺独立 Embedding Provider（超出本阶段边界） |
| PostgreSQL 集成测试 | SKIP | 未配置独立 `TEST_DATABASE_URL`（诚实跳过，未计入通过） |
| 开放域 entity 一致性 | 正确边界 | 无 NER/alias table → ambiguous（不内置知识图谱） |
| faithfulness threshold | Profile 数据 | 保持 `threshold=null`（平台零内置阈值）；Phase 8 敏感性分析（0.60/0.65/0.70）供业务确认 |
| 多值 numerical 覆盖率下降 | 已知代价 | 诚实 ambiguous 替代任意绑定；需更多锚点（单位词表扩展属语义变更，未做） |
| 版本控制 | 建议 | 目录未 `git init`（.gitignore 已就绪） |

## 9. Pilot Readiness

**结论：Pilot Ready（有条件）**——作为 MVP 本地单用户评估与诊断工具可以进入试点：

- 就绪面：评估管线（6 指标真实 Run）、8 类诊断 + Evidence Contract、维度级报告、可复现性快照（judge/profile/config/dataset 版本）、错误与质量判定严格分离、双语前端（桌面+移动）、测试全绿。
- 试点须知（必须告知用户）：
  1. 同一数据集版本仅能运行一次评估；改进对比需要新版本（内容相同的新版本当前被 Compare 正确判为 BLOCKED）——严格 A/B 等待 §3 决策；
  2. Judge 使用共享配额时，429 会以执行错误呈现（不污染分数），批量评估前建议确认配额；
  3. 多值数值记录将诚实呈现 undetermined（v2 语义），undetermined 偏高不是缺陷而是边界声明；
  4. Quality Gate 需先在 Profile 配置 `quality_gate` 才会产生判定。

## 10. 最终评分

| 维度 | 评分 | 依据 |
|---|---|---|
| 评估正确性（Deterministic-first / Evidence-first） | 9 / 10 | 配对修复消除任意绑定；歧义保持诚实；无阈值/语义游戏 |
| 工程完整度（管线/诊断/报告/回归） | 8.5 / 10 | 全链路真实数据验证；Compare DIRECT 通道因设计不可达（-1.5） |
| 可复现性与诚实性 | 9.5 / 10 | 8 项快照齐全；BLOCKED/NOT_EVALUABLE/undetermined 全部如实呈现 |
| Judge 稳定性 | 9 / 10 | 三态错误分离 + 重试/包装/链路恢复测试；embedding 缺口未解（-1） |
| 前端体验 | 8.5 / 10 | 桌面+移动验收通过、双语、弱项不掩盖；移动端为本阶段补齐（-1.5） |
| 工程卫生（测试/lint/构建/密钥） | 9.5 / 10 | 507+63 全绿、ruff/build 干净、secrets 零泄漏；集成测试未在真实 PG 执行（-0.5） |
| **总评** | **9.0 / 10 — Engineering Complete（Pilot Ready，附 §9 条件）** | |

---

## 11. 追加：本地 Supabase 迁移 + 性能优化（2026-09-07，同日）

### 11.1 本地 Supabase 化与数据迁移

- 应用数据库切换为本地 Supabase CLI 栈（PostgreSQL 17.6 @ 127.0.0.1:54322；`docker-compose.yml` 保持仅 app，CLI 栈为独立 dev 基础设施；应用仅用 `public` schema）
- **仅迁移重要数据**：golden_50 家族 6 个数据集版本（v1–v5 + loop_fix_v1）及其 6 个 Run 的全链路子行——1 project / 1 config / 6 datasets / 300 records / 6 runs / 300 results / 1250 metric rows / 234 diagnoses / 128 recommendations，ID 原样保留；36 个早期 e2e/probe 数据集与 64 个测试 Run **未迁移**
- 云库凭据在 `.env.local` 中注释备份（gitignored）
- **事故记录（诚实）**：首轮本地迁移后，用户以另一项目名（`supabase`）重启了 CLI 栈，旧卷连同迁移数据消失；迁移脚本幂等，已重建并重迁，最终校验一致

### 11.2 PostgreSQL 集成测试首次真实执行（本追加的最大质量增量）

13 个集成测试自编写以来首次执行（此前恒 skip）——**全部暴露并修复了真实测试债**：模型主键 `default=_uuid` 在 flush 时才生效且模型无 `relationship()`，测试一次性 `add_all` 多行时 INSERT 顺序不受保证，真实 FK/NOT NULL 立即报错。修复方式（测试层，不动生产语义）：显式 ID + 逐层 flush（`_seed` / phase1a 夹具）。生产路径本就先 flush 父行（`insert_diagnosis_with_recommendations`），无同类隐患。

**当前后端：520 passed / 13 executed / 13 passed（无任何 skip）**，ruff 全绿。

### 11.3 页面加载优化（实测前后对比）

| 项 | 云端基线 | 本地 + 优化后 | 手段 |
|---|---|---|---|
| `GET .../report` 载荷 | 108,976 B | **8,357 B（-92%）** | 查询列修剪（去掉 report 从不消费的 `comparison_basis` JSONB 与重复 `question`，SQL 侧抽 `comparison_type` 标量）+ GZipMiddleware |
| 首次请求 | ~22s | **~0.2s** | 本地 DB（RTT 0.5ms）+ 4 串行查询变轻 |
| 重复请求 | ~22s（全量） | **304 / 0 B / 18ms** | 终态 Run 不可变 → 弱 ETag（summary 指纹）+ `Cache-Control: private, must-revalidate`；非终态 `no-store` |
| UI（vite dev，冷加载） | — | 4.4s（含 dev 编译） | 部署形态为 production build（docker  serve dist） |
| UI（ETag 304 重载） | — | 0.9s | 同上 |

- API **响应形状未变**（`ReportResponse` schema 不变）；`metric_status` 同时兼容标量 `comparison_type` 与原始 `comparison_basis`（旧调用方与结果详情路径不受影响）；前端渲染验收：质量维度表 / 最弱维度高亮 / 总体质量 / 质量门禁全部正常
- **未做**（本地化后收益趋零，留作云端部署优化项）：4→2 查询合并、报告 SQL 侧聚合、前端骨架屏/路由级懒加载
- 部署形态提醒：`DATABASE_URL` 现指向本地 dev 库（`.env.local`，gitignored）；云端换区（原 P0-A）仍是将来上线路径，与 ADR-08 一致（只换 URL）

### 11.4 评分修正

§10「工程卫生」行的"集成测试未在真实 PG 执行（-0.5）"已不成立：**该行修订为 9.5 → 维持，但依据改为 13 个真实 PG 集成测试全执行全通过（含行锁竞态 I-1b）**。总评维持 **9.0 / 10 — Engineering Complete / Pilot Ready（附 §9 条件）**；剩余阻塞不变：严格 A/B Loop（§3 三选一设计决策）、answer_relevancy（embedding provider）。

---

*到此停止。不自动进入下一阶段；§3 设计决策等待批准。*
