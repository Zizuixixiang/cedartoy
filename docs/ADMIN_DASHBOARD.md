# CEDAR TOY 管理员运营看板

## 入口与权限

页面入口是 `/admin` 的「运营看板」页签。数据接口为：

```text
GET /api/admin/activity?range=1h
Authorization: Bearer <cedartoy_token>
```

接口严格复用平台账号的管理员鉴权：未登录返回 401，`toy_users.is_admin != 1` 返回 403。页面本身不另建登录态或权限系统。

主统计区间仅允许以下五档，顺序也是管理页展示顺序：

| range | 页面标签 | 起点 |
| --- | --- | --- |
| `10m` | 10分钟 | 请求时刻减 10 分钟 |
| `1h` | 1小时 | 请求时刻减 1 小时（默认） |
| `6h` | 6小时 | 请求时刻减 6 小时 |
| `12h` | 12小时 | 请求时刻减 12 小时 |
| `24h` | 24小时 | 请求时刻减 24 小时 |

未知值返回 400，不接受任意 SQL 时间片段。响应的 `generated_at`、`range.start_at/end_at` 均为 ISO UTC；前端按浏览器本地时间显示。

## 数据与口径

实现位于 `admin_dashboard.py`。它以 `mode=ro`、`query_only` 和 2 秒 busy timeout 分别短连接：

- `vendor/duel/data/duel.db`：双弈房间、参与者、system NPC、筹码结算、签到、互动请求、借款、成就和当前钱包聚合；
- `turtle-soup/backend/turtle_soup.db`：海龟汤房间、presence、game logs 和 player 类型聚合。

页面只设置“双弈”和“海龟汤”两个一级数据模块，不设置跨游戏实时总览。顶部范围选择统一控制整张看板：双弈模块内的活跃、开房、开始、完成、参与者、NPC、筹码与互动，以及海龟汤模块内的活跃、开房、完成、参与者、状态和时长，全部使用同一个所选 `range`。API 为兼容现有调用仍把两模块的活跃聚合放在各自的 `realtime` 字段中，但该字段不再代表固定 10 分钟：

`chips.wallets_current` 是无时间字段的当前快照，仅为 API 兼容保留，不在运营看板渲染；页面因此不会把不受范围控制的破产徽章或负余额钱包混入所选区间。

- 双弈活跃房要求当前仍在进行或等待，且 `updated_at` 或 `last_move_at` 落在所选范围；活跃人/机是这些房间中已加入的 `human/bound_machine` 去重，`system_npc` 单列。
- 海龟汤活跃房要求尚未结束，且房间创建、`room_presence.last_active_at` 或 `game_logs.created_at` 落在所选范围；活跃人/机只按有时间证据的 player 去重。
- 双弈“开始过”是所选范围新开房中 `revision>0`、已有 `terminal_at` 或当前为 `finished/archived`；参与者、游戏分布和 NPC 占比使用同一开始房 cohort。
- 筹码结算按 `chip_settlement_batches.reference_id` 去重到房间，stake 读取 `rooms.stake`，不累加 ledger 正负流水。
- 海龟汤完成按 `finished_at` 落入范围，`winner_id` 非空记为答出；参与者由范围内 presence、日志、创建者和胜者证据合并。

## 隐私与故障边界

运营接口不选择或返回聊天内容、`room_messages` 文本、海龟汤汤面/答案/题名、提问/回答正文、互动 `request_note`、借款条款、用户名或个人余额排行。接口和页面也不提供 `recent_rooms`、`room_id` 或任何单个房间的状态、人数、NPC/AI、stake、时间等明细，只保留聚合统计。

Duel 与 turtle-soup 分模块查询；任一数据库打不开、被锁超时或 schema 查询失败时，HTTP 响应仍保留另一模块，并把失败模块返回为 `ok=false` 的稳定空结构。权限和 range 错误不降级，仍分别返回 401/403/400。

## 验证与排障

本地回归不需要启动服务：

```bash
python3 -m py_compile server.py admin_dashboard.py tests_toy/test_admin_dashboard.py
python3 -m unittest -v tests_toy.test_account_security_round1 tests_toy.test_admin_dashboard
sed -n '/<script>/,/<\/script>/p' admin.html | sed '1d;$d' | node --check -
git diff --check
```

生产数据 smoke 必须直接调用 `admin_dashboard.build_activity_dashboard(...)`；该模块以 SQLite `mode=ro` 打开真实数据库。不要为 smoke 生成登录 token、请求写接口或手工插入统计行。正常情况下五档都应满足 `duel.ok=true`、`turtle.ok=true`，且序列化响应中不应出现逐房明细字段。

页面只在运营看板可见且浏览器处于前台时每 30 秒刷新；手动刷新或自动刷新失败会保留上次成功数据。若只有一个区块报错，先看 `/var/log/cedartoy.err.log` 中对应的 `Duel admin dashboard metrics` 或 `Turtle Soup admin dashboard metrics` 日志，不要检查根目录的历史 `server.log`。
