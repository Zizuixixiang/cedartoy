# 七宗罪 VS 七美德

这是一个原创中文测试。仅供娱乐；不是心理诊断，也不代表道德评价。

它借用「七宗罪 / 七美德」十四个老词做轻松比喻，观察欲望与调节如何同时出现；分数高低不代表好坏、品格或现实行为。

## 玩法

- `sins_virtues_start`：开始或重置测试。参数：`player_id`、`mode`。`mode` 可选 `full`（逐题）或 `full_fast`（35 题一次提交）。
- `sins_virtues_answer`：逐题模式提交当前题。参数：`player_id`、`answer`，其中 1=非常不同意，2=不同意，3=不确定/看情况，4=同意，5=非常同意。
- `sins_virtues_answer_batch`：快速模式一次提交 35 个 1–5 整数。参数：`player_id`、`answers`。
- `sins_virtues_get_result`：读取最近一次完成结果。参数：`player_id`。

推荐先用快速模式：

```text
play(game="sins_virtues", action="sins_virtues_start", params={"mode":"full_fast"})
play(game="sins_virtues", action="sins_virtues_answer_batch", params={"answers":[35个1-5整数]})
play(game="sins_virtues", action="sins_virtues_get_result", params={})
```

持久 MCP 地址会自动注入 `player_id`；游客调用时需要自行提供一个字母数字 `player_id`。

## 计分

每组包含两条正向陈述、两条反向陈述和一条「两侧可以共存」陈述。十四个维度各自使用三项指标取均值，再从 1–5 线性映射为 0–100。每一对不做互补归一，因此可以同时高、同时低，或一高一低。

题库与结果文案均为 CedarToy 原创。本测试没有加入「第八宗罪：虚伪」彩蛋，避免把一次作答风格误当成人格结论。
