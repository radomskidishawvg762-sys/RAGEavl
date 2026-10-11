# 已知问题

## HTTP-CONTEXTS 在 timeout=60s 下连续超时

- 记录日期：2026-10-11。
- 状态：待排查，不在本次 faithfulness 修复范围内处理。
- 现象：使用相同的 `faithfulness-claims-v1` 提示词内容及冻结 Judge 配置，HTTP-CONTEXTS 在第二轮 17 条整批复跑和随后一次独立补测中均发生 `EXT_JUDGE_TIMEOUT`（`scope=metric`），配置均为 `timeout=60s`、`retry=1`；两次结果的 `score` 和 `passed` 均为 `null`。
- 原因：尚未确认是上下文过长还是上游响应慢；现有超时证据不足以归因。
- 影响：重复测试只有 16/17 条得到有效分数；这 16 条与上次分数完全一致，但不能宣称完整 17/17 条重复一致。无分数不计为命中，也不据此判断 unsupported。
- 本次处理：保留现有超时配置、原始结果和标签，不再进行 live 调用或超时排查。
- 证据：见[补跑与全量复验报告](reviews/2026-10-11-faithfulness-followup.md)，以及本地保留的 [整批 trace](../exports/faithfulness-followup-2026-10-11/repeat17/trace.json) 和 [独立补测 trace](../exports/faithfulness-followup-2026-10-11/repeat17-retry/trace.json)。原始产物位于 Git 忽略的 `exports/`，仅在本地保留。
