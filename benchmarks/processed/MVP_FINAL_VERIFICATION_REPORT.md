# RAGEval Studio — MVP Final Verification Report

> 日期：2026-09-09
> 范围：Final MVP Verification（纯验证，不新增能力）
> 前置：`MVP_FINALIZATION_REPORT.md`（含 §11 本地迁移与性能优化追加）

---

## 结果总览

| 检查项 | 命令 | 结果 | 门槛 |
|---|---|---|---|
| Backend pytest | `python -m pytest` | ✅ **520 passed** / 0 failed / **0 skipped**（30 warnings，均为 ragas/langchain DeprecationWarning，非失败） | 520 |
| PostgreSQL integration | （pytest 内） | ✅ **executed: 13 / passed: 13**（真实本地 PG，含行锁竞态 I-1b） | 真实执行 |
| Ruff | `ruff check .` | ✅ **All checks passed** | clean |
| TypeScript | `tsc --noEmit` | ✅ **clean**（零错误） | clean |
| Vitest | `vitest --run` | ✅ **63 passed**（7 files） | 63 |
| Production build | `vite build` | ✅ **PASS**（3.58s，dist 产物正常，charts 249KB/gzip 82KB） | PASS |

**本轮变更未引入任何回归：首跑全绿，无失败需要修复，无 regression test 需要追加**（本轮变更自身所带测试——13 项配对测试、3 项 Judge provider 重试测试、13 项真实 PG 集成——已包含在 520 内并通过）。

## 本轮全部变更清单（回归审查基准）

| 变更 | 验证证据 |
|---|---|
| 数值多候选确定性配对（`pairing.py`，metric_version → `integrity-normalization-v2`） | 12 项 `test_numerical_pairing.py` + 既有 integrity 套件全绿；真实 golden_50 离线复核（32 多值记录 → 29 诚实 ambiguous，3 唯一绑定判定不变） |
| `HttpRagAdapter trust_env=False`（系统代理破坏 loopback 的生产缺陷修复） | `test_rag_input_adapter.py` 14 点全绿 |
| FR-12 ctx fixture 钉住未配置 Judge（hermetic） | `test_fr12_pipeline_audit.py` 5/5 全绿（此前 3 个环境性失败清零） |
| Provider retry→EXT / timeout→EXT 测试 | `test_judge_provider.py` 20/20 |
| PG 集成测试夹具债修复（显式 ID + 逐层 flush，仅测试层） | 13/13 首次真实执行全过 |
| 报告查询列修剪（去 `comparison_basis`/重复 `question`，SQL 抽 `comparison_type` 标量；API 响应形状不变） | 520 全绿（含 report/comparison/quality-gate 契约测试）+ UI 渲染复核全 true |
| GZipMiddleware + 终态报告弱 ETag（`Cache-Control: private, must-revalidate`；非终态 no-store） | 实测：8,357 B（-92%）、304/0B/18ms |
| 前端移动抽屉侧边栏 / KPI 2×2 / Grid CSS 变量钳制（桌面渲染不变） | 63 tests + typecheck + build + 移动/桌面截图验收 |
| ruff per-file-ignores（alembic 模板风格；迁移文件未改） | ruff clean |
| 数据：云→本地仅迁 golden_50 家族（6/6/300/1250/234/128 全链路），e2e 测试垃圾未迁；卷重置后幂等重迁校验一致 | 本地库计数逐项 OK |

## 冻结约束合规确认

本轮**零**：新 Metric / 新 Diagnosis Rule / LLM Diagnosis / Embedding Provider / RAG Adapter v2 / Redis / Celery / Multi-Worker / Custom Metric / DB Schema 变更 / API Contract 变更 / Frozen Interface 变更。无需停止报告的事项出现。

## Remaining Known Gaps（原样继承，非回归）

| 缺口 | 性质 | 去向 |
|---|---|---|
| 严格同版本 Improvement Loop（Run B → Compare DIRECT） | 冻结不变量冲突（一版本一 Run + 同 dataset_id 门），已取证（409 + BLOCKED + NOT_COMPARABLE，无伪造） | 等待设计决策（FINALIZATION_REPORT §3 三选一） |
| `answer_relevancy` | 外部依赖（无 embeddings provider） | 等待决策 A |
| 重排专项诊断（如 rerank_degraded） | 冻结期不加规则 | 等待决策 C |
| 查询合并 4→2 / 前端骨架屏 / 路由懒加载 | 本地化后收益趋零 | 云端部署时再评估 |
| `git init`（目录仍无版本控制；.gitignore/secrets 已审计通过） | 建议项 | 用户决定 |

## 结论

**✅ MVP FREEZE — READY**

Backend 520 / 13-integration / ruff clean / tsc clean / Vitest 63 / build PASS，全部达标，无回归。

---

## 追加（同日，Post-freeze enhancement）：一键报告导出

冻结验证后新增一项用户请求的小功能（read-only 导出，不改变冻结语义）：

- `GET /api/evaluations/{run_id}/export?format=pdf|json`（默认 PDF），Evaluation Detail 两个按钮（导出报告 PDF / 导出报告 JSON）
- 路径：**Persisted Run → ReportService → ExportService → PDF/JSON**；全程只读（测试 `_ReadOnlyRepo` 守卫任何写即失败），不重跑 Engine/Judge/RAG/Diagnosis，不写库
- 导出面（ExportService）：9 节全部来自持久化数据——Executive Summary / Quality Dimensions / Metrics（仅本次实际执行）/ Failure Analysis（+证据透传 + comparison_basis 透传）/ Diagnoses（原 taxonomy，不新增规则）/ Undetermined（单列 + “≠ Failure” 说明 + 比例 + 原因分布 + 代表样本，不转诊断）/ Recommendations（仅系统已有）/ 规则化 Summary Recommendations（无 LLM、不臆断业务原因）/ Reproducibility（8 字段快照 + Profile + RAG mode + judge）
- 诚实契约：null 不填 0、registered-not-enabled 不伪装已执行、execution errors 单列（真实 50 条 Run：15 failures / 65 undetermined / 43 条 429 配额错误全部单列，无一混入质量结论）、凭据不入文档（测试 + 真实 PDF 文本层断言 api_key/sk-/postgresql 均不存在）
- PDF：reportlab STSong-Light CID 字体（Docker slim 无系统字体也可中英同页渲染），Platypus 自动分页（真实 Run = 17 页 / 54KB），长证据 Paragraph 换行不截断，ASCII 下载文件名
- 新增测试 `tests/test_report_export.py` 14 项；**重跑全量：Backend 534 passed（520+14）/ 13 integration executed+passed / 0 skipped，ruff clean；Frontend typecheck clean / Vitest 63 passed / production build PASS**
- Live 验证：`export` 200 application/pdf（54,378 B，%PDF 头，attachment 文件名）；`export?format=json` 200 全节透传；`format=exe` 422；未知 run 404 BIZ_NOT_FOUND

**MVP FREEZE — READY（含导出增强）维持。**

---

*STOP。不自动进入 A / B / C 后续事项。*
