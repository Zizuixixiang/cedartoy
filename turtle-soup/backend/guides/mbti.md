# MBTI 游戏说明

通过根 MCP 聚合层的 `play(game="mbti", action=...)` 调用。`cedartoy/server.py` 会在本进程内转换为 JSON-RPC 并调用 MBTI handler。

## 可用 action

- `tools/list`：查看原始 MBTI MCP 工具列表。
- `mbti_start`：开始或重置测试。
  - 参数：`player_id`，1-10 位字母数字。
  - 参数：`mode`，可选 `short`、`full`、`short_fast`、`full_fast`。
- `mbti_answer`：逐题模式提交当前题答案。
  - 参数：`player_id`。
  - 参数：`a_score`，0-5 整数。5=完全偏向 A，0=完全偏向 B。
- `mbti_answer_batch`：快速模式提交一批答案。
  - 参数：`player_id`。
  - 参数：`a_scores`，按题目顺序填写 0-5 整数数组。
- `mbti_get_result`：查询最近一次已完成测试结果。
  - 参数：`player_id`。

## 示例

```json
{"game":"mbti","action":"mbti_start","player_id":"u123","mode":"short_fast"}
```

```json
{"game":"mbti","action":"mbti_answer_batch","player_id":"u123","a_scores":[5,4,3,2,1,0,5,4,3,2,1,0,5,4,3,2]}
```

```json
{"game":"mbti","action":"mbti_get_result","player_id":"u123"}
```
