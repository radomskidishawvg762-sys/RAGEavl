# Judge 连接、错误处理与 20 案例验证（2026-10-11）

本轮落实真实连接测试、错误分类与重试修复，并用**本地 HTTP RAG 测试接口 + 真实 Judge**跑通两轮评估、诊断、对比和回归。测试接口按用户选择使用，不代表已接入生产 RAG。

## 已修复

- Settings「测试连接」实际请求当前表单中的模型，返回结果、延迟和可处理的失败原因；测试不保存草稿。单次探测最多 64 输出 token、最长 10 秒。
- 区分密钥、模型权限、请求协议/参数、额度耗尽、临时限流、网络、超时和返回格式错误。错误只保存安全消息和分类，不保留上游响应正文。
- OpenAI SDK 关闭额外重试，RAGAS 关闭默认的十次传输重试。Provider 统一重试暂时性故障；权限、额度和格式问题立即返回。数值型 Retry-After 超过 5 秒直接返回限流原因。
- 本地 Judge 的 localhost / 回环 IP 绕过环境代理，远程地址保留用户代理。实测修复前本地 fixture 收到 0 次请求，UI 得到代理 502；修复后权限、额度、格式三种错误各只请求一次并正确展示。
- 局部更新 Judge 配置保留未提交的超时、温度和重试参数，避免只改模型名时重置其他参数。
- RAGAS 整个指标超时和结构化输出解析失败分别保留超时、格式分类，分数保持 null。
- 诊断不再丢弃无法解析的数值证据后强行归因。不支持的单位出现在答案、检索或有效参考来源时返回 undetermined，并说明缺少哪一侧可可靠解析的证据。

## 实测结果

| 验证 | 结果 |
|---|---|
| 后端完整 pytest | **611 passed，0 skipped**；其中真实 PostgreSQL **19/19** |
| 前端 Vitest | **86/86**，11 个文件 |
| TypeScript / 生产构建 / Ruff / git diff --check | 通过 |
| Chrome 真实设置页 + 实际后端 + 本地模型错误接口 | **10/10**；无浏览器异常、草稿未保存、上游秘密不展示 |
| HTTP RAG 两轮流程断言 | **15/15** |
| 数值比较与诊断对照基准 | **15/15**；包含数值、倍率、单位错误与不可判定情况 |
| 真实模型连接 | openai-compatible / deepseek-v4-flash，探测成功，**5567 ms** |

本地 fixture：15 个仓库数值基准 + HTTP 404、无效 JSON、错误 contexts 类型、HTTP 500、空 contexts，共 20 个案例。第二轮返回参考答案与参考证据，复用同一数据集、配置及接口地址。

| 项目 | baseline | fixed fixture |
|---|---:|---:|
| 总输入 | 20 | 20 |
| 已持久化结果 | 16 | 20 |
| 输入接口失败 | 4（准确保留对应错误码） | 0 |
| 输入覆盖率 | 80% | 100% |
| 可用 faithfulness 分数 | 13/16 | 19/20 |
| 其他指标问题 | 网络失败 2、缺少 contexts 1 | 指标超时 1 |
| overall_score（原样记录） | 0.4347826087 | 0.7428571429 |

两个 Run：`918c63b7-0e10-4e86-a239-bae2f1f7b45a` / `0bda4bc5-33af-486f-8443-4b6558a8b2f9`。对比状态 DIRECT，回归报告 IMPROVEMENT；金标记录内容未修改。这里的 fixed 指**测试 RAG 输出修正**，不表示评分模型已经校准。

## 评分质量尚未达标

连通与流程检查通过，不能替代判分准确性检查。逐条人工对照后，baseline 可用分数中 **12/13** 符合标签；第二轮答案都由参考内容生成，可用分数中仅 **10/19** 符合标签，出现 9 次低分误报。这是小规模测试结果，不能当作生产准确率。

额外追踪同一条案例，真实模型先将「营收为1亿元」拆成「公司的营收为1亿元」，随后因为证据只写「营收为1亿元」、没有明写主体而返回 verdict=0。原始调用与输出已保存。定位结果指向**拆句补充信息与严格证据判定之间的不一致**，没有把分数改写为通过。

下一步应固定提示词修订号，校准短答案、隐含主体、中文数值等场景；同时保留真实不支持声明的负例，比较误报与漏报后再更新评分口径。模型版本目前也未显式冻结；不要只凭本轮总分决定上线。

本轮 live Run 捕获于数值证据保守性及指标超时分类的后续修复之前，原始记录保持原样。使用冻结的评分输入离线重放当前诊断规则，`B5-num-002` 的 faithfulness 归因由高置信度 unsupported_claim 变为 undetermined；15 个数值基准仍全部一致。离线重放没有再次请求模型、改分或修改数据库。旧 Run 中的超时仍保留原始 SYS_METRIC_ERROR；新分类已由回归测试验证。

## 产物与复验

- [两轮原始 API 结果、诊断和对比](../../exports/judge-review-2026-10-11/results.json)
- [人工标签对照与冻结结果诊断重放](../../exports/judge-review-2026-10-11/analysis.json)
- [单案例拆句与判分原始输出](../../exports/judge-review-2026-10-11/trace.json)
- [浏览器验证结果](../../exports/judge-review-2026-10-11/browser-results.json)
- [格式错误页面截图](../../exports/judge-review-2026-10-11/judge-format-error.png)

```powershell
.venv/Scripts/python.exe -m pytest -q --tb=short --disable-warnings
.venv/Scripts/python.exe -m ruff check .
npm --prefix frontend test
npm --prefix frontend run build

# 专用测试库；不要与 PostgreSQL pytest 并行运行
.venv/Scripts/python.exe scripts/serve_review.py --with-judge
.venv/Scripts/python.exe scripts/run_judge_review.py --live
# 上一行会产生真实模型费用；两个 20 记录 Run，不自动重跑
.venv/Scripts/python.exe scripts/analyze_judge_review.py
# 在 frontend 目录运行，Vite :5173 必须已启动
node scripts/judge-review-e2e.mjs
```

最终完整 PostgreSQL 测试按既有夹具清空专用测试库；live 原始结果保留在 exports，开发库未清理。开发库复核仍为 **1 项目 / 6 数据集 / 6 Run**，健康检查正常，应用 :8000、前端 :5173 可用。

本轮还恢复了已停止的 Docker / Supabase。Docker 从应用工具进程启动时遇到 AF_UNIX socket 访问错误，改用隐藏的 WMI 进程启动后恢复；未重置 Docker 或数据库。该现象与 [Codex Windows MSIX AppData 隔离问题报告](https://github.com/openai/codex/issues/52698)一致；关联属于依据日志与恢复行为作出的判断。
