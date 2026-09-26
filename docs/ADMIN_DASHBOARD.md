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

- `data/sessions.db`（沿用 `SESSIONS_DB_PATH` / `SESSIONS_DB`）：通用游戏活动事件；
- `vendor/duel/data/duel.db`：双弈房间、参与者、system NPC、筹码结算、签到、互动请求、借款、成就和当前钱包聚合；
- `turtle-soup/backend/turtle_soup.db`：海龟汤房间、presence、game logs 和 player 类型聚合。

页面依次设置“全站游戏活跃”“双弈”和“海龟汤”三个一级模块。新增总览使用通用事件，不混用下方两模块的历史口径。顶部范围选择统一控制整张看板：全站总览的活跃账号与成功操作，以及双弈模块内的活跃、开房、开始、完成、参与者、NPC、筹码与互动，以及海龟汤模块内的活跃、开房、完成、参与者、状态和时长，全部使用同一个所选 `range`。API 为兼容现有调用仍把两模块的活跃聚合放在各自的 `realtime` 字段中，但该字段不再代表固定 10 分钟：

`chips.wallets_current` 是无时间字段的当前快照，仅为 API 兼容保留，不在运营看板渲染；页面因此不会把不受范围控制的破产徽章或负余额钱包混入所选区间。

- 双弈活跃房要求当前仍在进行或等待，且 `updated_at` 或 `last_move_at` 落在所选范围；活跃人/机是这些房间中已加入的 `human/bound_machine` 去重，`system_npc` 单列。
- 海龟汤活跃房要求尚未结束，且房间创建、`room_presence.last_active_at` 或 `game_logs.created_at` 落在所选范围；活跃人/机只按有时间证据的 player 去重。
- 双弈“开始过”是所选范围新开房中 `revision>0`、已有 `terminal_at` 或当前为 `finished/archived`；参与者、游戏分布和 NPC 占比使用同一开始房 cohort。
- 筹码结算按 `chip_settlement_batches.reference_id` 去重到房间，stake 读取 `rooms.stake`，不累加 ledger 正负流水。
- 海龟汤完成按 `finished_at` 落入范围，`winner_id` 非空记为答出；参与者由范围内 presence、日志、创建者和胜者证据合并。

## 全站游戏活跃

API 新增 `overview: {ok, games, error?}`。每行只包含 `game/name/active_users/human_users/ai_users/operations/save_count`，按活跃账号数降序，再按范围内最近活动降序排列；零活跃游戏保留。页面在双弈之上渲染紧凑列表，窄屏可横向滚动，沿用同一个范围选择与 30 秒刷新。

游戏目录复用 `index.html` 的 `const games`，名称与首页完全一致（`soup` 映射成统一 MCP 的 `turtle_soup`，排除管理入口）。再并入 `IDENTITY_GAMES` 中的游戏；目前没有首页卡片的塔罗沿用 `RITUAL_DISPLAY_NAME`，`bdsmtest` 沿用 MCP 名称。新增首页游戏自动出现；仅接 MCP 的新游戏先显示其 game 标识。首页“属性测试”是站外链接，独立保留零活跃行，不冒充本站 `bdsmtest` 活动。没有手写逐游戏聚合 SQL。

### 通用事件口径

**通用活跃从功能上线后开始积累**，没有历史回填，也不从旧日志、mtime、当前存档或双弈/海龟汤专属表推算活动。总览与下方两张详细模块因此可能不同，尤其是上线初期和海龟汤人类端。

- `game_activity.py` 是同一个 recorder，事件仅保存 UTC epoch 秒、game、平台账号 ID、`human/ai` 类型和固定动作名。按 `(identity_type, identity_id)` 去重，一个账号的多个槽仍是一个身份；绑定的人类与小机分开。未登录游客不计入“活跃账号”，系统 NPC 不计入。
- 成功操作次数是所选 `[start, end)` 内事件数，一次接受的批量调用算一次，不按内含的题目、步数或指令条数展开。活跃账号只来自这些操作；仅围观或查状态的账号不记活跃。
- 统一 MCP `play` 在 `_finalize_play_response` 收口；同步、Operit 和双弈异步网关共用，异步只在完成时记录。鉴权/校验抛错、顶层或 MCP `result` 的 `error/isError/ok=false/success=false`、文本块中的结构化错误及显式 `duplicate=true` 不记。答错题、游戏输局本身是有效游戏行为，不是接口失败。
- 排除 guide、list、状态、进度、目录、历史、结果读取、导入导出、公告和投票。双弈 state（包括 wait 挂等）不计，move 计；chips 只计签到、破产申请及互动/借款的写操作。eco 的 observe/wait 会推进时间，计；gaze/look/info 不计。解谜盲盒 draw、首次 open、submit、check_step 计，重复 open 只是回看，不计。
- 传统 cmd 会先排除已核对的只读命令（如 status、help、背包/图鉴、花园收藏/信件、买菜的只读页面等）。`VendorCmdGame.run` 在原有玩家存档锁内比较 JSON 存档摘要；eco/词与物在原有事务内比较引擎存档。纯文字引擎没有统一成功标志，因此采用保守口径：没有持久进度变化不计，即使文字返回正常。这会少计不落档的叙事互动/重复设置，不能把它理解为所有 HTTP 200 请求数。摘要只在内存比较，不保存存档内容，也不改作者源码。批量命令只要有实际进度变化且没有明确失败标志，作为一次调用计入。
- 原始 JSON-RPC `method` 透传兼容入口没有明确业务动作，不计；新游戏应使用统一 `play(game, action, params)` 的正式动作。新游戏如添加特殊轮询或纯文本协议，应在 `business_action` / 适配边界补充语义，不能只凭动作名猜测。

### 人类端已接入与边界

所有已接入入口使用服务端认证的人类 ID，而非浏览器自报 ID 或绑定机存档 ID。无需侵入 vendor 项目。

| 入口 | 计入的动作 |
| --- | --- |
| `/api/{mbti,enneagram,dnd,love,ecr,humanity,sins_virtues}` | start 和成功完成 answer_batch；结果读取、比较和已完成提交重试不计 |
| `/eco/api/human_action` | 通过绑定校验后的成功业务操作 |
| `/forest/api/action` | start / choose，冲突或校验失败不计 |
| `/duel/api/rooms` 及房间 POST | 开房、邀请处理、加入、move、resign、leave；不记 GET 轮询、聊天、通知、房间保留/删除 |
| `/garden-cat/web/*` | cmd、新花园、带猫搬家、写便签；注册、状态刷新、已读回执不计 |
| `/workkk/*` | shop/buy、reset；围观与礼物弹窗 ack 不计 |
| `/camping-plaza/api/*` | 玩家命名、回合推进/计划、日终/下一天、action；session 页面初始化、观察提示已读不计 |
| `/detroit/api/*` | sessions 建档、action 推进；列表、备份/导出、导入、删档不计 |
| 塔罗会话 POST | draw / reveal / return / stop；不记 bootstrap、历史与后台解读轮询 |

尚未接入通用事件：海龟汤人类端 `/soup/*` 由独立服务认证并流式代理，保留现有详细模块统计；旧测评 GET/MCP 独立入口不经过统一 play；双弈人类筹码/借款/兑换入口、塔罗邀请确认/新会话/异步解读、解谜盲盒人类揭晓页暂不记。AI 人生、月幕及其他只有围观/选择/攻略/站外链接的页面不会伪造人类活跃；露营进入页面自动建档也不算操作。后台调用独立游戏服务、绕过统一 MCP 的请求不在本层口径内。

### 当前存档数

总览复用 `_public_game_stats(strict=True)` 和它已有的统计函数：eco 按 session 行，词与物取 `save_count`（不把历史 runs 当存档），解谜盲盒按 `distinct ai_user_id` 一份进度，塔罗只计已有持久抽牌回执的会话。文件档按玩家/槽目录中实际存在的 `SAVE_FILES` 统计，空目录和仅锁文件不算，bar 仅选择版本但未开局不算；workkk 用 `game_state.json`，花园用平台目录 `state.json`。底特律复用既有安全映射 `has_save`。所有数字均是当前快照，不受 range 限制，包含现存游客档。

双弈、海龟汤、测评及没有明确存档计数的游戏返回 `null`，页面显示 `—`。塔罗读库失败或露营统计失败在严格模式也返回 `null`；普通公共统计保留原有兼容回退。露营复用原有 internal stats，管理员查询将该调用超时限制为 2 秒。其他总览查询失败时整个 overview 降级，避免把未知伪装成零。

### 初始化与保留

新增一张 additive 表：

```sql
CREATE TABLE IF NOT EXISTS game_activity_events (
    occurred_at REAL NOT NULL,
    game TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    identity_type TEXT NOT NULL CHECK(identity_type IN ('human', 'ai')),
    action TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_game_activity_time
    ON game_activity_events(occurred_at, game);
```

第一次成功记录时自动初始化；也可在已确认的数据库副本上调用 `game_activity.init_db(conn)` 连续两次并执行 `PRAGMA integrity_check`。无 ALTER、旧表更新、重建或历史回填。看板读取不会初始化表：已存在数据库尚无事件表时返回全目录零活动；数据库无法打开/表结构损坏则返回错误。

事件保留 60 天，每个服务进程至多每天在一次成功记录时清理过期行，不新增 cron，不 VACUUM。写连接超时 200ms，写入失败只输出固定日志 `Game activity event could not be recorded`，不影响原游戏结果；因此数据库繁忙/不可写期间可能漏记，不能作为计费或审计流水。停用 recorder 不影响旧存档，事件表可原样保留。

## 隐私与故障边界

运营接口不选择或返回聊天内容、`room_messages` 文本、海龟汤汤面/答案/题名、提问/回答正文、互动 `request_note`、借款条款、用户名或个人余额排行。接口和页面也不提供 `recent_rooms`、`room_id` 或任何单个房间的状态、人数、NPC/AI、stake、时间等明细，只保留聚合统计。

Overview、Duel 与 turtle-soup 分模块查询；任一数据库打不开、被锁超时或 schema 查询失败时，HTTP 响应仍保留其他模块，并把失败模块返回为 `ok=false` 的稳定空结构。权限和 range 错误不降级，仍分别返回 401/403/400。

## 验证与排障

本地回归不需要启动服务：

```bash
python3 -m py_compile server.py admin_dashboard.py game_activity.py tests_toy/test_admin_dashboard.py tests_toy/test_game_activity.py
python3 -m unittest -v tests_toy.test_account_security_round1 tests_toy.test_admin_dashboard tests_toy.test_game_activity
node scripts/check_admin_activity_ui.js
sed -n '/<script>/,/<\/script>/p' admin.html | sed '1d;$d' | node --check -
git diff --check
```

生产数据 smoke 必须直接调用 `admin_dashboard.build_activity_dashboard(...)`；该模块以 SQLite `mode=ro` 打开真实数据库。不要为 smoke 生成登录 token、请求写接口或手工插入统计行。验证全站模块时额外传入 `sessions_db_path`、`catalog_provider`、`save_stats_provider`；生产 smoke 的存档 provider 应使用预先读取的聚合或空字典，不调用可能初始化存档的游戏服务。正常情况下五档都应满足 `overview.ok=true`、`duel.ok=true`、`turtle.ok=true`，且序列化响应中不应出现逐房明细字段。

页面只在运营看板可见且浏览器处于前台时每 30 秒刷新；手动刷新或自动刷新失败会保留上次成功数据。若只有一个区块报错，先看 `/var/log/cedartoy.err.log` 中对应的 `game activity overview`、`Duel admin dashboard metrics` 或 `Turtle Soup admin dashboard metrics` 日志，不要检查根目录的历史 `server.log`。
