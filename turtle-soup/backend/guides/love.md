# 爱之语测试（love）

通过根 MCP 聚合层的 `play(game="love", action=...)` 调用。人和机使用同一套 30 题原创中文题库；题目里的场景按你们的相处方式代入即可。

## 接口

- `love_start`：参数 `player_id`、`mode`。`full` 逐题作答；`full_fast` 一次提交 30 题。
- `love_answer`：逐题模式提交 `answer`，只能为 1 或 2。
- `love_answer_batch`：快速模式提交长度恰为 30 的 `answers` 数组，每项只能为 1 或 2。
- `love_get_result`：参数 `player_id`，查询最近一次已完成结果。账号结果永久保留，游客结果保留 48 小时。
- `love_compare`：参数 `player_id_a`、`player_id_b`，均可填写账号用户名、数字账号 id 或 `guest:` 前缀游客 id，读取双方已完成结果生成对测报告。无需双方授权，游客同权。

## 示例

```json
{"game":"love","action":"love_start","params":{"player_id":"u123","mode":"full_fast"}}
```

```json
{"game":"love","action":"love_answer_batch","params":{"player_id":"u123","answers":[1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2,1,2]}}
```

```json
{"game":"love","action":"love_compare","params":{"player_id_a":"guest:u123","player_id_b":"guest:u456"}}
```

网页入口：`https://toy.cedarstar.org/love`

作者：南山君。概念框架来自 Gary Chapman 的 Five Love Languages；题库为原创中文题目。
