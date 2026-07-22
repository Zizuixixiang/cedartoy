# 依恋类型测试（ecr）

通过根 MCP 聚合层的 `play(game="ecr", action=...)` 调用。量表共 36 题，每题按 1（非常不同意）到 7（非常同意）评分。

## 接口

- `ecr_start`：参数 `player_id`、`mode`。`full` 逐题作答；`full_fast` 一次提交 36 题。
- `ecr_answer`：逐题模式提交 `answer`，只能为 1 到 7。
- `ecr_answer_batch`：快速模式提交长度恰为 36 的 `answers` 数组，每项只能为 1 到 7。
- `ecr_get_result`：参数 `player_id`，查询最近一次已完成结果。账号结果永久保留，游客结果保留 48 小时。
- `ecr_compare`：参数 `player_id_a`、`player_id_b`，均可填写账号用户名、数字账号 id 或 `guest:` 前缀游客 id，读取双方已完成结果生成对测报告。无需双方授权，游客同权。

## 示例

```json
{"game":"ecr","action":"ecr_start","params":{"player_id":"u123","mode":"full_fast"}}
```

```json
{"game":"ecr","action":"ecr_answer_batch","params":{"player_id":"u123","answers":[4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4]}}
```

```json
{"game":"ecr","action":"ecr_compare","params":{"player_id_a":"guest:u123","player_id_b":"guest:u456"}}
```

网页入口：`https://toy.cedarstar.org/ecr`

来源：Experiences in Close Relationships（ECR），Brennan, Clark & Shaver (1998)；中文版修订：李同归、加藤和生 (2006)，《心理学报》38(03), 399-406。
