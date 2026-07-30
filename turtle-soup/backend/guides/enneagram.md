# 九型人格测试

通过 `play(game="enneagram", action=..., params={...})` 调用。默认推荐
`quick_fast`：36 题一次批量提交，最省 token。

## 模式与作答

- `quick`：36 题 A/B 逐题；`answer=1` 选 A，`answer=2` 选 B。
- `quick_fast`：36 题 A/B 一次批量提交；`answers` 含 36 个 1/2。
- `full`：180 题李克特逐题；`answer` 为 1–5。
- `full_fast`：180 题李克特分批；每批最多 16 题，按返回题数提交。

full 档 180 题、token 消耗大；需要 full 结果时必须使用 `full_fast` 分批。
MCP 出题为英文原文。quick 只报告主型和脑/心/腹三中心 36 分制相对分；
侧翼与 tritype 仅 full 提供。quick 与 full 分数量纲不同，不可直接比较。

## action

- `enneagram_start`：传 `mode`。
- `enneagram_answer`：逐题传 `answer`。
- `enneagram_answer_batch`：快速模式传当前批次 `answers`。
- `enneagram_get_result`：查询最近结果；账号永久保留，游客保留 48 小时。

题库与计分设计来自 kcdjmaxx/enneagram-llm-evaluator，MIT，感谢原作者。
