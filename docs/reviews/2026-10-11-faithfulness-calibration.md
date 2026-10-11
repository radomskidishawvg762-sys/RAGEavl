# Faithfulness 拆句与证据一致性修复（2026-10-11）

工作目录：`E:\workbuddy_data\new_project`（RAGEavl），基线提交 `1aee109`。修改保留在本地，等待确认。本轮未写数据库，未改历史评分或金标，未做 CI、Docker、鉴权相关工作。

## 证据与修复边界

先读取 RAGAS 0.4.3 的拆句/NLI 实现、Judge 配置与 Provider、`diagnosis/evidence.py` 和 generation 证据合同，再用原始 trace 驱动真实 RAGAS 路径离线重放，稳定得到错误分数 0。

旧拆句要求补全句子且消除代词。已定位的实际调用把 `营收为1亿元` 改为 `公司的营收为1亿元。`，后续 NLI 因上下文没有“公司”给 0。新调用保留声明 `营收为1亿元`，NLI 引用相同证据，给 verdict=1、score=1。这个因果判断有两阶段原始调用证据；没有把其他低分案例都推定为同一个根因。

修复只调整 faithfulness 的两份提示词：拆句完整保留答案声明、主体具体程度、否定、时间、单位和数值，不从问题补充事实；短答案允许保持片段，代词只在证据明确时解释。NLI 仍仅使用检索上下文，检查完整声明；允许严格等价的中文/阿拉伯数字和单位倍率转换，实体、时间、币种、单位、否定、额外声明不一致仍不支持。没有增加“原文包含就强制给满分”等捷径，没有修改阈值、分数聚合、数值解析或 diagnosis 规则。

## 版本与配置冻结

- 提示词修订：`faithfulness-claims-v1`。
- 提示词内容 SHA-256：`a36a58e048b6c3239b2ec4651882121fac5b9ab989598b815e8f0fbfa2e6e13f`。
- faithfulness 的 `metric_version` 现在同时包含 RAGAS 版本、提示词修订及完整哈希；其他三个 RAGAS 指标版本不变。Run snapshot 从 Registry 记录实际执行的 `prompt_version` / `metric_prompts`，不再依赖未生效的配置标签。旧、新指标口径在现有比较流程中会因版本差异被标为 LIMITED。
- 冻结配置文件：[faithfulness-2026-10-11.json](../../benchmarks/judge/faithfulness-2026-10-11.json)：openai-compatible / `deepseek-v4-flash`，既有 Go 地址；temperature=0、max_tokens=1024、timeout=60 秒、retry=1。这是本次复验专用配置，没有改全局设置。密钥和额外请求头只从本地环境读取，不进入配置快照或产物。
- **上游不可变模型版本尚不可得**：本地 `model_version` 原本为空，不能把模型别名冒充固定版本。manifest 明确记录 `model_revision_status=unavailable`；已捕获的完整输入/输出可精确离线重放，未来 live 输出仍可能因别名变化或模型随机性不同。
- 本地验证依赖：RAGAS 0.4.3、langchain-core 1.6.2、langchain-community 0.3.31、openai 2.54.0、pydantic 2.13.5。复验脚本后续捕获会自动记录这些版本。

## 改前 / 改后实测

使用既有 review 的冻结输入与人工标签，另加独立正负例。所有新分数来自真实 Judge 调用，未从人工标签生成分数。使用现有 analyze 流程统计，并为重复问题增加案例 ID 匹配，避免不同答案被错配标签。

| 对照 | 改前 | 改后 |
|---|---:|---:|
| live 第二轮全部可用分数命中率 | 10/19（52.6%） | 18/18（100%） |
| 同一批、两次都有分数的 17 条 | 8/17（47.1%） | 17/17（100%） |
| 第二轮低分误报 | 9 条 | 0 条；原 9 条均已纠正 |
| 第二轮评分覆盖率 | 19/20 | 18/20 |
| 原 baseline 可用分数命中 | 12/13 | 12/12 |
| 原 baseline 共同可用的 10 条 | 9/10 | 10/10 |

上述是小样本结果，不能作为生产准确率或每次调用都正确的保证。网络失败不计为命中，覆盖率单独列出。

负例保护：原 baseline 有新分数的 **7/7** 负例继续未通过；新增独立负例有分数的 **6/6** 未通过，覆盖不同实体、显式新增主体、币种、单位、额外声明、否定。额外声明案例正确得到 **0.5**，没有丢弃不支持的声明。新增短数值答案、隐含代词、中文数字换算 **3/3** 通过。新增集合整体为 **9/9 可用分数一致，1 条网络失败**。

剩余未验证的负例：`B2-ret-001`（无关证据）、`B4-num-003`（倍率错误）、`G-period`（年份错误）本次遇到网络错误，未改标签或强行判定。第二轮 `HTTP-JSON` 指标超时、`HTTP-500` 网络错误也保留无分数，没有为补齐数字自动重跑。旧数值基准与证据合同离线对照仍为 **15/15**。

## 本地检查与产物

相关本地测试覆盖提示词隔离、真实 RAGAS bridge/schema/聚合、Run 版本快照、预算与重放保护、比较、generation 证据合同及数值基准。脚本化模型回复的单元测试只验证调用合同，不冒充模型准确率。

- 相关 pytest：**126 passed**（命令见下）。
- Ruff、`git diff --check`：通过。
- 新 capture 的 **46 条**记录全部精确离线重放通过；请求提示词、输出、分数、错误都核对。重放禁用 RAGAS 遥测，并用 socket guard 验证无网络连接。
- 原 `results.json`、`analysis.json`、`trace.json` 三个文件 SHA-256 与捕获前一致，哈希保存在新 manifest。
- live 共 **81 次**请求；累计提示词 **283,152 UTF-8 bytes**，最多 **82,944 输出 token**。实际账单未读取。按[官方 Go 单价](https://docs.opencode.ai/docs/go/)的高峰价格和保守输入换算估计，本次额度消耗上界约 **0.185 美元**；实际取决于 token 用量及缓存。

三个跨 baseline / fixed 的完全相同 question、answer、contexts 输入复用本次已捕获输出，避免重复收费；各次调用的原始记录仍可核对。

新产物另存于 [exports/faithfulness-calibration-2026-10-11](../../exports/faithfulness-calibration-2026-10-11/)：`manifest.json`（配置、提示词与来源哈希）、`results.json`（旧结果和独立的新结果）、`trace.json`（完整提示词/输出）、`analysis.json`（对照与负例统计）、`budget.json`。原历史 exports 保持原样。

## 修改文件

- `app/engines/faithfulness_prompts.py`：版本化提示词、双阶段示例、内容哈希及私有实例构造。
- `app/engines/ragas.py`：仅 faithfulness 接入新提示词；成功/错误结果均记录新版本。
- `app/metrics/base.py`、`app/services/run_planner.py`：从注册信息记录实际提示词版本。
- `benchmarks/judge/faithfulness-2026-10-11.json`：无密钥的冻结复验配置与独立保护案例。
- `scripts/review_faithfulness.py`：默认离线，显式 live、调用预算、完整捕获与精确重放；不覆盖旧 capture。
- `scripts/analyze_judge_review.py`：复用分析函数，支持指定输入/输出、案例 ID、同样本比较及误报/漏报统计；默认另存分析，拒绝覆盖三个原始产物。
- `tests/test_faithfulness_prompts.py`、`tests/test_faithfulness_review.py`、`tests/test_ragas_engine.py`：对应回归检查；以及本报告。

## 本地复验

在 `E:\workbuddy_data\new_project` 执行，以下命令不会产生模型费用或写数据库：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:RAGAS_DO_NOT_TRACK='true'
.venv/Scripts/python.exe scripts/review_faithfulness.py --replay exports/faithfulness-calibration-2026-10-11
.venv/Scripts/python.exe scripts/analyze_judge_review.py --source exports/faithfulness-calibration-2026-10-11/results.json --output exports/faithfulness-calibration-2026-10-11/analysis.replayed.json
.venv/Scripts/python.exe -m pytest -q tests/test_faithfulness_review.py tests/test_faithfulness_prompts.py tests/test_ragas_engine.py tests/test_judge_provider.py tests/test_comparison_service.py tests/test_phase_d_effective_config.py tests/test_generation_diagnosis.py tests/test_benchmark_smoke.py tests/test_numerical_pairing.py --tb=short --disable-warnings
.venv/Scripts/python.exe -m ruff check app/engines/faithfulness_prompts.py app/engines/ragas.py app/metrics/base.py app/services/run_planner.py scripts/analyze_judge_review.py scripts/review_faithfulness.py tests/test_faithfulness_prompts.py tests/test_faithfulness_review.py tests/test_ragas_engine.py
git diff --check
```

不带 `--live`、不带 `--replay` 时，review 脚本只在新目录输出历史分析和预检，明确提示尚未测得新的模型命中率。只有显式运行 `--live` 才重新调用 Judge；套餐凭据必须仍在本地环境中，模型/地址/版本不符冻结配置会停止。

下一步建议先确认本轮提示词口径；再取得可固定的上游模型版本，针对三个未验证负例补做小批复验，并扩大独立样本。当前停在本地修改与报告，等待确认。
