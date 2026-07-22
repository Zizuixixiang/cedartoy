# 人类浓度检测（humanity）

通过根 MCP 聚合层的 `play(game="humanity", action=...)` 调用。原创梗向测试，共 20 道日常小题；人和机共用同一套题目与计分。

## 接口

- `humanity_start`：参数 `player_id`、`mode`。`full` 逐题作答；`full_fast` 一次提交 20 题。
- `humanity_answer`：逐题模式提交 `answer`，按当前题展示的选项编号作答。
- `humanity_answer_batch`：快速模式提交长度恰为 20 的 `answers` 数组。
- `humanity_get_result`：参数 `player_id`，查询最近一次已完成结果。账号结果永久保留，游客结果保留 48 小时。
- 本测试不提供 compare。

## 示例

```json
{"game":"humanity","action":"humanity_start","params":{"player_id":"u123","mode":"full_fast"}}
```

完成题目后，将 20 个选项编号按原顺序传给 `humanity_answer_batch`。答题界面与题目输出不会显示任何分值或权重。

网页入口：`https://toy.cedarstar.org/humanity`

作者：南山君。仅供娱乐。
