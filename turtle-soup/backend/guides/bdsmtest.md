# BDSMTest 游戏说明

实时调用 bdsmtest.org 官方接口算分。通过根 MCP 聚合层的 `play(game="bdsmtest", action=...)` 调用，`cedartoy/server.py` 会在本进程内转换为 JSON-RPC 并调用 BDSMTest handler。

## 流程

`bdsmtest_start` 时后台会调用原站 `init → nextquestions` 拉取全部 93 题并缓存；作答完成后调用 `score → getresult` 取得各原型百分比。整局仅靠 rauth 串联，逐题作答期间不必保持连接。

## 认同度量表（score 1-7）

`7=完全同意 6=同意 5=较同意 4=中立 3=较不同意 2=不同意 1=完全不同意`

（原站算分要求每题都有 1-7 的答案，不接受 0；拿不准时填 4。）

## 可用 action

- `tools/list`：查看原始 BDSMTest MCP 工具列表。
- `bdsmtest_start`：开始或重置测试。
  - 参数：`player_id`，1-10 位字母数字。
  - 参数：`mode`，`normal`（逐题，先返回第 1 题）或 `fast`（一次性返回全部题）。
- `bdsmtest_answer`：逐题模式提交当前题认同度。
  - 参数：`player_id`。
  - 参数：`score`，1-7 整数。返回下一题；答完最后一题自动算分。
- `bdsmtest_answer_batch`：快速模式一次性提交全部答案。
  - 参数：`player_id`。
  - 参数：`answers`，`{题号id: 1-7}` 对象，键为 start 返回的题号 id，须覆盖全部题。
- `bdsmtest_get_result`：查询最近一次已完成测试结果（账号结果永久保留，游客结果保留 48 小时）。
  - 参数：`player_id`。

## 结果

各原型按百分比降序返回（题目与原型名称均为原站中文，如 `臣服者 56%`），并附原站结果链接 `https://bdsmtest.org/r/{rid}`。

## 示例

```json
{"game":"bdsmtest","action":"bdsmtest_start","player_id":"u123","mode":"normal"}
```

```json
{"game":"bdsmtest","action":"bdsmtest_answer","player_id":"u123","score":5}
```

```json
{"game":"bdsmtest","action":"bdsmtest_answer_batch","player_id":"u123","answers":{"3":7,"98":1,"2":4}}
```

```json
{"game":"bdsmtest","action":"bdsmtest_get_result","player_id":"u123"}
```

## 来源
本测试基于 bdsmtest.org（https://bdsmtest.org），调用原站接口计算结果。
我们将其做成了 MCP 工具版，让小机可以直接通过 CedarToy 答题测试。
该玩法灵感来自圈内同好分享，未能找到最初制作者。如果你是原作者，请联系我们，想当面感谢！
