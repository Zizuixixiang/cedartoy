# 解谜盲盒接入说明

作者 Runsheng_（小红书 `_Sssonnet0220`）原创并授权收录。22 道独立解码题，题面、作者前置提示、权威谜底、简洁攻略和自然多步题的 checkpoint 位于服务端 `puzzle_box_data.py`。不依赖 LLM 判题，无积分、排行、连胜或重置。

## MCP

沿用根 MCP 的 `list_games`、`get_guide(game="puzzle_box")`、`play(game="puzzle_box", action=..., params={...})`，不增加顶层工具。

| action | params | 行为 |
| --- | --- | --- |
| draw | 无 | 从未拆题随机取一道，原子标为 opened |
| open | puzzle_id | 指定打开/历史回看；数字 1–22 或 N01–N16、H01–H06 |
| list / progress | 无 | 仅题号、标题、挑战标记、状态与统计 |
| submit | puzzle_id, answer | 仅正确最终谜底将 opened 变为 solved |
| check_step | puzzle_id, checkpoint_id, answer | 只返回 correct 布尔值；不改变进度 |

只接受已鉴权 AI 账号，路径 token 与 Bearer 均走统一鉴权。用户自报 player_id / ai_user_id 不用于选档。按 AI 账号记录，存档槽参数不产生第二份进度。未打开的题须先 open，错误答案不泄露标准结果。draw/open 只返回当前一题与作者原文提示，总 guide/schema 没有题面或谜底。

英文按大小写、空格、常规标点和撇号规范化后精确比较；中文忽略空格、标点，保留汉字。接受短“答案是 / Answer:”包装，不抽取长篇回应中的某个猜测。N03 支持字符串数组或多行文本，要求原句中的两种不同断句同时存在。开放回应留在工具调用之外。

## 网页

复用首页游戏卡和 Memoria 详情/攻略层，22 题同一列表，挑战题仅加标记。图标为 `assets/icons/puzzle_box.png`。没有独立网页游戏或第二入口。

选择首页卡片后的桌面详情与手机抽屉，共用 `/api/games/stats` 返回的 `puzzle_box.metric_label/metric`。`loadGameStats()` 在页面初始化时请求，响应后更新游戏数据再重绘；响应前或请求失败时保留 `--`，数字 0 也是有效结果。若一直显示 `--`，先检查该请求是否发出、响应是否成功及浏览器脚本异常，不能仅凭后端接口有值认定页面已收到响应。

小机选择复用 `me.bindings` / `aiBindings()` 与原有 `bankPicker` 弹层，后台使用 `_require_bound_ai` 校验真实绑定。无登录或绑定时仍能查看题目标题，不能查看账号进度或攻略。切换账号/小机清空展开内容并丢弃旧请求；可以手动刷新进度。

- `GET /api/puzzle-box/progress`：公开无答案的目录，status/summary 为 null。
- `GET /api/puzzle-box/progress?ai_user_id=...`：仅绑定人类可读对应小机状态。
- `POST /api/puzzle-box/reveal`：Bearer 人类身份，JSON 含 ai_user_id / puzzle_id。已解可直接取该题攻略；未解须 `confirm_spoiler: true`（严格布尔值），否则只返回确认提示。

网页确认通过后才请求该题答案，同时只展开一题。初始 HTML、目录、MCP guide/schema 都不包含答案。reveal 不创建 opened 或 solved，不影响随机池。响应禁止缓存。

## 存储和验证

沿用 server 的 `SESSIONS_DB_PATH`（`SESSIONS_DB` 环境变量）。`puzzle_box_progress` 主键为 `(ai_user_id, puzzle_id)`；未拆题不存行，行仅含 opened/solved 与时间。首次访问延迟执行 `CREATE TABLE IF NOT EXISTS`，无 import 时数据库写入、不改旧表。抽题在 `BEGIN IMMEDIATE` 事务内选池并写入，防止并发重复抽取。

定向验证（全部新游戏用例使用临时库；DOM 测试依赖仓库已有 jsdom）：

```sh
python3 -m unittest tests_toy.test_puzzle_box tests_toy.test_root_mcp_protocol tests_toy.test_list_games tests_toy.test_homepage_order -v
python3 -m py_compile puzzle_box.py puzzle_box_data.py server.py
node scripts/check_home_stats_ui.js
```

重复初始化、旧数据不变、`PRAGMA integrity_check`、并发抽题、两种鉴权通道、绑定边界、单题剧透与浏览器 DOM 操作均有覆盖。本开发任务仅交付修改及验证，未执行提交、推送、重启或部署。

首页统计回归执行完整 `index.html` 初始化和 fetch 链路，覆盖响应前打开卡片、延迟返回后的详情/抽屉更新、0 值、未登录/已登录，以及登录响应晚到和搜索重绘不覆盖统计值。
