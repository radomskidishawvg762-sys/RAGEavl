# Faithfulness 补跑与全量复验（2026-10-11）

**目前未满足“全部通过”**：三条负例保护、完整 pytest、全仓库 Ruff 均通过；17 条重复 live 评估中，16 条分数与上次完全一致，HTTP-CONTEXTS 在整批及单独补测时均超时，没有有效分数，无法确认完整 17 条一致。

工作目录为 `E:\workbuddy_data\new_project`。本报告记录提交前的复验状态；复验结束时未提交或 push，未改提示词、评分规则、冻结配置、历史分数或标签。

## 三条负例

只调用指定的三个输入，共 6 次 Judge 请求。阈值保持 0.9；任意负例通过会立即停止，本次没有触发。

| 案例 | 冻结输入来源 | 分数 | 是否通过 | NLI 返回的证据理由 |
|---|---|---:|---|---|
| B2-ret-001 | candidate_baseline | 0.0 | 否 | 证据仅是市场竞争分析，没有营收金额 |
| B4-num-003 | candidate_baseline | 0.0 | 否 | 答案为 1 万元，证据为 1 亿元，数量级不同 |
| G-period | 独立 guardrails | 0.0 | 否 | 答案年份为 2025，证据年份为 2024 |

这是模型的逐声明判定及对应原文证据，不是仅凭低分推定诊断根因。三条标签均保持 0。

产物：[negatives/trace.json](../../exports/faithfulness-followup-2026-10-11/negatives/trace.json)、同目录的 manifest、results、analysis、budget。

## 完整本地检查

- **全量 pytest：627 passed，0 failed，0 error，0 skipped**，耗时 44.14 秒，121 条 warnings。
- PostgreSQL 集成测试 **19/19**，使用既有专用测试库及隔离夹具；执行前确认测试库与开发库为不同目标、表已齐备。
- **`ruff check .`：All checks passed**。
- **`git diff --check`：通过**。

完整日志和机器结果：[pytest.log](../../exports/faithfulness-followup-2026-10-11/pytest.log)、[pytest.xml](../../exports/faithfulness-followup-2026-10-11/pytest.xml)、[ruff.log](../../exports/faithfulness-followup-2026-10-11/ruff.log)。没有排除任何 pytest 标记或文件。

## 同一配置的 17 条重复 live

清单严格来自上一轮历史 fixed 与 candidate_fixed 都有分数的 17 条，不加入上次无分数的案例。新脚本在调用前逐条校验 EvaluationRecord 完全相同，并校验公开 Judge 配置哈希及提示词版本相同；不同则停止。清单保存于 [repeat17-selection.json](../../exports/faithfulness-followup-2026-10-11/repeat17-selection.json)。

使用同一 `faithfulness-claims-v1` 完整内容哈希、RAGAS 0.4.3，以及既有 openai-compatible / deepseek-v4-flash 配置：temperature=0、max_tokens=1024、timeout=60 秒、retry=1。模型仍是供应商别名，没有冒充已冻结不可变的上游模型版本。

| 项目 | 上次 17 条 | 本次完整复跑 |
|---|---:|---:|
| 有效分数覆盖 | 17/17 | 16/17 |
| 有效分数命中 | 17/17 | 16/16 |
| 两次共同有效的 16 条 | 16/16 | 16/16 |
| 逐条分数完全相同 | — | 16 条，均为 1.0 |
| 无法比较 | 0 | HTTP-CONTEXTS：score=null |

完整整批共 34 次请求。HTTP-CONTEXTS 得到 `EXT_JUDGE_TIMEOUT`，scope=metric，score=null，passed=null。整批完成后只对这一条无分数输入额外补测一次，共 2 次请求，配置未变，仍为同一超时错误。没有重跑其他有效分数，没有把补测覆盖回整批结果，也没有把无分数算作命中。

三条负例中的 B2-ret-001 / B4-num-003 使用原 baseline 的错误输入；17 条里同名案例使用第二轮已修正的输入。两组均直接来自各自冻结记录，标签和证据未替换。

结果：[repeat17/repeat-comparison.json](../../exports/faithfulness-followup-2026-10-11/repeat17/repeat-comparison.json)；额外补测：[repeat17-retry/repeat-comparison.json](../../exports/faithfulness-followup-2026-10-11/repeat17-retry/repeat-comparison.json)。两个目录均保留完整原始调用、输出、错误和 manifest。总计本轮 **42 次** Judge 请求。

## 完整性与本轮修改

三个新 capture（3 条负例、17 条整批、1 条超时补测）均已精确离线重放通过，共 21 条记录；重放验证提示词、输出、分数和错误，并禁止网络连接。原始 review 与上一轮 calibration 的 **11 个 JSON 文件 SHA-256 全部一致**，基准哈希保存在 [before-hashes.json](../../exports/faithfulness-followup-2026-10-11/before-hashes.json)。评分代码、提示词及冻结配置的文件哈希也与上轮一致。

本轮仅扩展 `scripts/review_faithfulness.py` 的指定案例、同输入/配置比较、负例通过即停止功能，增加对应选择保护测试 `tests/test_faithfulness_review.py`，以及本报告。未调整任何评分行为或业务服务配置。

## 本地复验命令

以下检查不产生模型费用：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:RAGAS_DO_NOT_TRACK='true'
.venv/Scripts/python.exe -m pytest -q --tb=short --disable-warnings
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe scripts/review_faithfulness.py --replay exports/faithfulness-followup-2026-10-11/negatives
.venv/Scripts/python.exe scripts/review_faithfulness.py --replay exports/faithfulness-followup-2026-10-11/repeat17
.venv/Scripts/python.exe scripts/review_faithfulness.py --replay exports/faithfulness-followup-2026-10-11/repeat17-retry
```

本轮实际 live 命令如下，再执行会重新消耗额度，输出目录需使用新名称：

```powershell
.venv/Scripts/python.exe scripts/review_faithfulness.py --live --only candidate_baseline/B2-ret-001,candidate_baseline/B4-num-003,guardrails/G-period --stop-on-negative-pass --output exports/faithfulness-followup-2026-10-11/negatives
$repeatSelection = Get-Content -Raw exports/faithfulness-followup-2026-10-11/repeat17-selection.json -Encoding UTF8 | ConvertFrom-Json
$repeatOnly = $repeatSelection.selectors -join ','
.venv/Scripts/python.exe scripts/review_faithfulness.py --live --only $repeatOnly --compare-to exports/faithfulness-calibration-2026-10-11 --output exports/faithfulness-followup-2026-10-11/repeat17
.venv/Scripts/python.exe scripts/review_faithfulness.py --live --only candidate_fixed/HTTP-CONTEXTS --compare-to exports/faithfulness-calibration-2026-10-11 --output exports/faithfulness-followup-2026-10-11/repeat17-retry
```

复验结束时保留“HTTP-CONTEXTS 无有效分数”这一未完成项，停止继续 live 调用，未提交或 push。该问题已记录于[已知问题](../known-issues.md)：尚未确认是上下文过长还是上游响应慢，不在本次处理；后续按用户指示仅进行本地提交。
