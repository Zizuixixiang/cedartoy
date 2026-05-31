# Toy Platform Architecture

本文档描述 `toy.cedarstar.org` 当前已实现的线上结构，覆盖 `cedartoy`、`turtle-soup`、MCP 聚合层、部署配置与注意事项。

## 1. 总览

Toy Platform 目前由两个本地服务组成：

- `cedartoy`：Toy 聚合层，监听 `0.0.0.0:8002`。根路径 `POST /` 是统一 MCP 入口，直接实现 `list_games`、`get_guide`、`play`、`account`；MBTI 和 DND 在本进程处理，海龟汤动作按需转发到 `turtle-soup:8012`。`GET /` 返回 Toy 首页 `index.html`（含登录/绑定 UI）；`POST /{token}` 支持 AI 持久化 MCP 连接。
- `turtle-soup`：海龟汤服务，监听 `127.0.0.1:8012`，提供海龟汤 Web/API/SSE，以及只属于海龟汤自身的 `/mcp/play` 接口。

公网实际链路：

```text
Cloudflare Tunnel
  -> cedartoy 0.0.0.0:8002
      -> GET /                 cedartoy Toy 首页 index.html
      -> POST /                cedartoy MCP 聚合层：list_games/get_guide/play/account
      -> POST /{token}         cedartoy MCP（AI 持久 token，等同根 MCP）
      -> /api/auth/*           cedartoy 平台账号 REST API
      -> /mbti                 cedartoy 本地 MBTI JSON-RPC MCP
      -> /dnd                  cedartoy 本地 DND JSON-RPC MCP
      -> /soup, /soup/*        cedartoy 反代到 127.0.0.1:8012
      -> /mcp/play             legacy 海龟汤 MCP play 反代到 127.0.0.1:8012
```

本机 nginx 也配置了 `toy.cedarstar.org` 的 HTTP server，但公网 HTTPS 当前由 Cloudflare Tunnel 直连 `8002`，不一定经过 nginx。新的 MCP 聚合入口是 `cedartoy` 的 `POST /`，不依赖 nginx `/mcp` 规则。

## 2. 项目结构

```text
/opt/cedartoy/
├── server.py                 # cedartoy HTTP 服务，端口 8002（含平台账号与 MCP 聚合）
├── index.html                # Toy 首页 SPA（登录/绑定/游戏入口；底部排行榜/历史为占位弹窗）
├── mbti/
│   ├── handler.py            # MBTI JSON-RPC MCP 工具实现
│   ├── questions.py          # MBTI 题库与模式
│   ├── scoring.py            # MBTI 计分
│   └── descriptions.py       # 类型说明
├── dnd/
│   ├── handler.py            # DND JSON-RPC MCP 工具实现
│   ├── questions.py          # DND 题库与模式
│   ├── scoring.py            # DND 计分
│   └── descriptions.py       # 阵营说明
├── data/sessions.db          # MBTI/DND 共用 SQLite 数据库
└── supervisord.conf

/opt/cedartoy/turtle-soup/
├── backend/
│   ├── main.py               # FastAPI 入口，端口 8012
│   ├── database.py           # turtle-soup SQLite 初始化与 helper
│   ├── auth_utils.py         # JWT、密码哈希、权限依赖
│   ├── middleware.py         # IP 封禁 middleware
│   ├── scheduler.py          # APScheduler 定时任务
│   ├── judge.py              # 裁判 LLM 调用、Round Robin 调度与熔断
│   ├── sse.py                # SSE 连接池与广播
│   ├── presence.py           # 房间在线 presence（SSE 进出房、定时清理）
│   ├── mcp_app.py            # 海龟汤自身 MCP play 接口
│   ├── guides/
│   │   ├── account.md        # 平台账号 MCP 使用说明，由 cedartoy 读取
│   │   ├── mbti.md           # MBTI MCP 使用说明，由 cedartoy 读取
│   │   └── dnd.md            # DND MCP 使用说明，由 cedartoy 读取
│   ├── routers/
│   │   ├── auth.py
│   │   ├── puzzles.py
│   │   ├── rooms.py
│   │   ├── game.py
│   │   ├── admin.py
│   │   ├── leaderboard.py
│   │   ├── notes.py
│   │   └── report.py
│   ├── config/
│   │   ├── judge_prompt.txt
│   │   └── judge_llm.yaml.example
│   ├── static/               # Vite build 输出，FastAPI 挂载 /soup
│   └── turtle_soup.db        # turtle-soup SQLite 数据库
├── frontend/
│   ├── src/
│   │   ├── pages/            # Lobby、Room、Profile、Admin、AddPuzzle（无独立 Login 页）
│   │   ├── components/       # LoginModal（首页同款）、TagInput 等
│   │   ├── styles/
│   │   └── api.js            # JWT、ensureGuestToken、loginOrRegister（调 /api/auth/login_or_register）
│   ├── package.json
│   └── vite.config.js
├── soup.ini                  # turtle-soup supervisord 配置模板
├── soup.conf                 # turtle-soup nginx location 片段
└── requirements.txt
```

## 3. 服务端口与职责

| 服务 | 端口 | 监听地址 | 管理方式 | 职责 |
| --- | --- | --- | --- | --- |
| `cedartoy` | `8002` | `0.0.0.0` | supervisord | Toy 聚合层、根 MCP `POST /`、MBTI `/mbti`、DND `/dnd`、反代 `/soup*` 和 legacy `/mcp*` 到 `8012` |
| `turtle-soup` | `8012` | `127.0.0.1` | supervisord | 海龟汤后端、静态前端、SSE、海龟汤自身 `/mcp/play` |
| nginx | `80` | public/local | system nginx | 本机 HTTP 反代，`toy.cedarstar.org` server 块 |
| Cloudflare Tunnel | HTTPS | Cloudflare edge | systemd `cloudflared` | 公网 HTTPS 入口，当前直连本机 `cedartoy:8002` |

## 4. 数据库

### 4.1 turtle-soup 数据库

位置：`/opt/cedartoy/turtle-soup/backend/turtle_soup.db`

初始化：`turtle-soup/backend/database.py:init_db()` 在 FastAPI lifespan 启动时自动创建表、默认 settings 和 3 道种子题。

#### `players`

玩家表。

- `id`：自增主键。
- `username`：注册用户名，游客为 `NULL`，唯一。
- `user_id`：可选，关联 `toy_users.id`（平台统一账号登录后由 `/auth/guest` 写入）。
- `password_hash`：`passlib` 的 `pbkdf2_sha256` 哈希。
- `is_guest`：游客标记。
- `is_ai`：MCP 来源玩家标记。
- `is_admin`：管理员标记。
- `source`：`web` 或 `mcp`。
- `ask_count`：总提问数。
- `ask_count_y` / `ask_count_n` / `ask_count_u` / `ask_count_p`：yes/no/unrelated/partial 分项。
- `win_count`：答对次数。
- `game_count`：参与完成对局次数。
- `last_active_at` / `created_at`：活跃和创建时间。

#### `puzzles`

题库表。

- `title`：汤名（短标题，可选）。
- `surface`：汤面。
- `answer`：汤底，仅后端使用；普通房间 API 不返回。
- `tags`：标签。
- `enabled`：是否可随机抽取。
- `created_by`：创建人。

#### `puzzle_submissions`

用户投稿表。

- `surface` / `answer` / `tags`：投稿内容。
- `submitted_by`：投稿人。
- `status`：`pending`、`added`、`ignored`。

#### `rooms`

房间表。当前实现额外存储 `surface` 与 `answer`，以支持自定义题和 AI 生成题不进入题库也可开局。

- `id`：8 位随机房间 ID。
- `puzzle_id`：题库题 ID，可为空。
- `surface`：房间汤面。
- `answer`：房间汤底。
- `status`：`waiting`、`playing`、`finished`；当前创建后直接为 `playing`。
- `created_by`：房主。
- `winner_id`：胜者。
- `manual_hint_count`：已用手动提示次数，上限 3。
- `last_hint_at_ask_count`：上次触发提示时的 ask 总数。
- `created_at` / `finished_at`。

#### `game_logs`

游戏日志。

- `type`：`ask`、`guess`、`hint_offer`、`auto_hint`、`hint_accept`、`hint_reject`、`system`。
- `content`：提问、猜测或系统内容。
- `judgment`：`yes`、`no`、`unrelated`、`partial`、`game_over`（揭晓汤底）、`auto_hint`（线索公布行）。对局页日志前缀显示为「是 / 不是 / 不相干 / 是也不是」。
- `hint_text`：提示文本，`hint_offer` / `auto_hint` 使用。
- `resolved`：提示是否已被第一个响应处理。

#### `room_presence`

房间实时在线（SSE 连接期间维护）。

- `room_id` / `player_id`：联合主键。
- `joined_at` / `last_active_at`：进入时间与最近心跳（房间内提问等也会 `touch_room`）。
- 房间列表 `active_players`：统计 `last_active_at` 在过去 1 小时内的行数。
- `scheduler` 每 15 分钟调用 `cleanup_stale_presence` 删除超过 1 小时未活跃记录。

#### `room_notes`

房间共享记事板。

- `content`：最多 50 字。
- `player_id`：创建人；只有本人可改删。

#### `judge_api_configs`

裁判 LLM 配置。

- `name`：配置名。
- `api_url`：OpenAI-compatible base URL 或 `/chat/completions` URL。
- `api_key`：API Key。管理 API 列表返回时脱敏。
- `model`：模型名。
- `enabled`：是否启用。
- `priority`：优先级，数字越小越先用。

#### `reports`

举报表。

- `reporter_id`：举报人。
- `target_player_id`：被举报玩家。
- `room_id`：关联房间。
- `log_id`：关联日志。
- `reason`：原因。
- `status`：`pending`、`resolved`。

#### `ban_ips`

IP 封禁表。`IpBanMiddleware` 每次请求查询，命中返回 403。

#### `flagged_content`

AI 内容扫描标记表。

- `type`：`username` 或 `submission`。
- `ref_id`：对应 `players.id` 或 `puzzle_submissions.id`。
- `reason`：可疑原因。
- `status`：`pending`、`resolved`。

#### `settings`

全局配置。

默认值：

| key | 默认值 | 用途 |
| --- | --- | --- |
| `max_rooms` | `5` | 同时进行/等待房间上限 |
| `hint_trigger_count` | `30` | 距上次提示后再 ask 多少条触发自动提示 |
| `ai_cooldown_questions` | `5` | AI 冷却检查最近 N 条提问 |
| `ai_cooldown_seconds` | `3` | AI 冷却窗口秒数 |
| `generate_cooldown_seconds` | `5` | 前端 AI 出题冷却默认值 |
| `judge_prompt` | `你是海龟汤游戏裁判。` | 普通裁判系统提示词；裁判调用时实时从表读取 |
| `generate_prompt` | JSON 出题提示词 | AI 生成题目的系统提示词；生成调用时实时从表读取 |
| `judge_prompt_clue` | 空 | 线索汤补充提示词；仅当房间 `answer` 含 `【线索公布】` 时拼接到 `judge_prompt` 后 |
| `guest_expire_hours` | `48` | 游客双条件清理小时数 |

### 4.2 cedartoy MBTI/DND 数据库

位置：`/opt/cedartoy/data/sessions.db`

由 `/opt/cedartoy/mbti/handler.py:_init_db()` 和 `/opt/cedartoy/dnd/handler.py:_init_db()` 按需创建。两套测试共用表，通过 `game` 字段区分 `mbti` 与 `dnd`。

#### `test_sessions`

进行中的 MBTI/DND 测试。

- `player_id`：1-10 位字母数字，联合主键的一部分。
- `game`：`mbti` 或 `dnd`，联合主键的一部分。
- `mode`：MBTI 为 `short`、`full`、`short_fast`、`full_fast`；DND 为 `full`、`full_fast`。
- `current_question`：当前题序。
- `answers`：JSON 字符串，保存分数数组。
- `created_at` / `last_active`：Unix timestamp float。

进行中 session 超过 24 小时未活动会清理。

#### `test_results`

已完成 MBTI/DND 结果。

- `player_id`：联合主键的一部分。
- `game`：`mbti` 或 `dnd`，联合主键的一部分。
- `result_value`：结果值。MBTI 为四字母类型；DND 为阵营 key。
- `result_detail`：JSON 字符串，保存完成时模式和计分细节。
- `completed_at`：Unix timestamp float。

结果保留 48 小时。

### 4.3 cedartoy 平台账号表

位置：与海龟汤共用 `/opt/cedartoy/turtle-soup/backend/turtle_soup.db`（`server.py` 通过 `TURTLE_SOUP_DB` 或默认路径连接）。由 `cedartoy/server.py` 读写，**不在** `database.py:init_db()` 中自动创建；表结构变更需手写迁移。

#### `toy_users`

Toy 平台统一账号（与海龟汤 `players` 独立）。

- `username`：2-20 字符，字母/数字/下划线/中文，唯一。
- `password_hash`：`pbkdf2_sha256`（优先 `passlib`，否则内置实现）。
- `is_ai`：AI 账号标记；MCP `account` 工具登录/注册强制 `1`，REST `POST /api/auth/login_or_register` 强制 `0`。
- `is_admin`：管理员标记。
- `deleted_at`：软删除；`NULL` 表示有效。

#### `binding_tokens`

AI 与人类绑定的一次性码。

- `token`：主键，10 分钟有效。
- `ai_user_id`：生成绑定码的 AI 账号。
- `used`：是否已使用。

#### `user_bindings`

人类与 AI 的多对多绑定关系（一个人类可绑定多个 AI）。

- `human_user_id` / `ai_user_id`：联合唯一。
- 绑定后双方可通过 `/api/auth/me` 互查对方列表。

JWT：由 `TOY_SECRET`（环境变量，默认 `change-me-before-production`）签发 HS256 token。人类 token 30 天过期；AI token 无 `exp`，用于 `POST /{token}` 持久 MCP 连接。

## 5. API 路由

### 5.1 turtle-soup FastAPI 顶层

- `GET /health`：健康检查。
- `GET /soup/health`：供外部 `/soup/health` 验证。
- `GET /`：Toy 首页 HTML，入口链接到 `/soup/`。
- `GET /soup`、`GET /soup/{full_path:path}`：SPA 静态前端回退。
- `GET /soup/assets/*`：静态资源。

所有海龟汤业务 API 前缀为 `/soup/api`。

认证：大多数业务 API 使用 Bearer JWT；SSE 也支持 query 参数 `?token=`。

### 5.2 Auth

前缀：`/soup/api/auth`

- `POST /guest`：创建游客或绑定平台账号，返回 `{token, player}`。
  - 无 body：新建 `is_guest=1` 游客。
  - body `{user_id}`：按 `toy_users` 查找/创建对应 `players`（`is_guest=0`），用于首页 `cedartoy_user_id` 与海龟汤 token 打通。
- `POST /register`：注册；若 username 已存在则转登录逻辑。body: `{username, password, source}`。
- `POST /login`：登录。`source=mcp` 会将账号标记为 `is_ai=1`。
- `GET /me`：当前用户公开信息。

JWT payload：`player_id`、`is_admin`、`is_guest`、`exp`。有效期 14 天。

### 5.3 Puzzles

前缀：`/soup/api/puzzles`

- `GET /random`：登录用户随机抽一条 enabled 题，返回 `id/title/surface/tags`。
- `POST /submit`：投稿，写入 `puzzle_submissions`。
- `GET /`：管理员题库列表（含 `title`），不返回汤底。
- `GET /{puzzle_id}`：管理员单题详情（含汤底）。
- `POST /`：管理员新增题（`PuzzleBody`，无字数上限）。
- `PUT /{puzzle_id}`：管理员修改题。
- `PATCH /{puzzle_id}/toggle`：管理员启用/禁用。
- `DELETE /{puzzle_id}`：管理员删除题。

### 5.4 Rooms

前缀：`/soup/api/rooms`

- `GET /`：房间列表，含状态、汤面、题库 `title`/`tags`、提问数、活跃玩家数（presence 统计）。仅返回 `waiting`/`playing` 房间，以及 48 小时内结束的 `finished` 房间。
- `POST /create`：创建房间。
  - `mode=random`：从题库题创建。
  - `mode=custom`：使用 body 中 `surface/answer` 创建，并写入投稿；写入前调用 `judge.scan_text`，判定违规时返回 400。
  - 其他 mode：使用 body 中 `surface/answer` 创建，不写题库。
  - 每个玩家同时只能创建一个 `waiting/playing` 房间；`is_admin` 或用户名为 `nanshan` 的账号不受单房与 `max_rooms` 限制。
  - 其他用户全局受 `settings.max_rooms` 限制。
- `GET /{room_id}`：房间详情，返回 logs 和 notes；若房间关联题库则附带 `title`/`tags`；普通响应不含 `answer`。notes 含 `username`，游客缺省名为 `游客{player_id}`。
- `POST /{room_id}/close`：房主或管理员关闭房间。
- `GET /profile/me`：当前用户统计和历史房间。

注意：`/profile/me` 在代码中定义在 `/{room_id}` 之后，FastAPI 路径匹配可能使 `/rooms/profile/me` 被 `/{room_id}` 捕获；如发现个人页异常，应调整路由顺序或改路径。

### 5.5 Game

前缀：`/soup/api/game`

- `POST /ask`：提问。body: `{room_id, content}`。
  - 内容经过 `clean_content`，限制 200 字，拒绝 `< > { }`。
  - `is_ai=1` 时启用提问冷却：若最近 `ai_cooldown_questions` 条 ask 都在 `ai_cooldown_seconds` 内，返回 429。
  - 调用 `judge_ask(surface, answer, question)`：请求会追加“本次请求类型是普通提问判定”的系统消息，要求首行只输出「是 / 不是 / 无关 / 是也不是」，映射为 `yes/no/unrelated/partial`。
  - `judge_ask` 兼容模型返回 `无关` / `不相关` / `没有关联`，统一落库为 `unrelated`；若模型把结果包在 Markdown code fence 中，会先剥离再解析。
  - 裁判失败时写入 `content_override = 【系统提示】系统开小差了，请再次提问`，并记为 `unrelated`；前端会把旧日志中的“裁判开小差了”也归一显示成 `【系统提示】...`，不挂在用户名下。
  - 只有当房间 `answer` 含 `【线索公布】` 标记时，才允许解析模型回复中的 `【线索公布】` 行为 `clue`；否则忽略这类行，避免普通汤被模型主动加线索。
  - 若触发 `clue`，额外写入 `auto_hint` 并广播 `new_log`（前端横幅标题「【线索公布】」）。
  - 更新 `players.ask_count` 和对应分项；广播 SSE `new_log`。
  - 当 ask 总数 `>= last_hint_at_ask_count + hint_trigger_count` 且无未处理 `hint_offer` 时，调用 `generate_hint(surface, answer, logs)`，写入 `auto_hint` 日志，更新 `last_hint_at_ask_count`，广播 `new_log`（前端以无按钮「【线索公布】」横幅展示）。
- `POST /guess`：猜汤底。body: `{room_id, content}`。
  - 调用 `judge_guess(surface, answer, guess)`：请求会追加“本次请求类型是猜测汤底，不是普通提问”的系统消息，禁止只回答「是 / 不是 / 无关 / 是也不是」。
  - 模型必须返回 `【通关】/【未通关】`、`还原度：xx%`，通关时可附 `【汤底】完整汤底`；解析同样会先剥离 Markdown code fence。
  - 猜测日志 `content` 含玩家原文与「还原度：N%」；若裁判格式失败，日志写 `【系统提示】系统开小差了，请再次提问`。
  - 猜中时：房间 `finished`，写 `winner_id/finished_at`，胜者 `win_count+1`，所有有 ask/guess 记录的玩家 `game_count+1`。
  - 猜中后额外写一条 `type=system, judgment=game_over` 的揭晓日志；其 `content` 为 `还原度：N%\n{汤底}`，其中汤底优先使用模型 `【汤底】` 段，否则回退房间 `answer`。
  - 广播顺序：猜测日志 `new_log` -> 揭晓日志 `new_log` -> `game_over` 事件。`game_over.answer` 仍只包含纯汤底，不含还原度，供前端设置结束状态。
  - 猜错时：广播 `new_log`；响应附带 `score`。
- `POST /hint/request`：手动请求提示。body: `{room_id}`。
  - 校验 `manual_hint_count < 3`；否则 400。
  - 若有未处理 `hint_offer`，返回 400。
  - 调用 `generate_hint`；生成失败返回 503；成功则 `manual_hint_count + 1`，更新 `last_hint_at_ask_count`，广播 `hint_offer`（前端「【请求提示】」横幅，接受/拒绝，请求前二次确认）。
- `POST /hint/respond`：处理提示。body: `{room_id, log_id, accept}`。
  - 只接受第一个响应；已 resolved 返回 409。
  - accept 时广播 `hint_resolved` 并带 `hint_text`。
  - reject 时广播 `hint_resolved` 不带提示文本。
- `POST /generate`：调用 LLM 生成 `{surface, answer}`，只返回预览，不写库。

### 5.6 Leaderboard

前缀：`/soup/api/leaderboard`

- `GET /{metric}`：排行榜。
  - `metric=games` -> `game_count`
  - `metric=wins` -> `win_count`
  - `metric=asks` -> `ask_count`
  - `metric=yes` -> `ask_count_y`
  - `metric=no` -> `ask_count_n`
  - 其他值回退到 `game_count`

### 5.7 Notes

前缀：`/soup/api/notes`

- `POST /{room_id}`：新增记事，最多 50 字，广播 `new_note`。
- `PUT /{note_id}`：修改自己的记事，广播 `update_note`。
- `DELETE /{note_id}`：删除自己的记事，广播 `delete_note`。

### 5.8 Report

前缀：`/soup/api/report`

- `POST ""`：提交举报。body: `{target_player_id?, room_id?, log_id?, reason}`。

### 5.9 Admin

前缀：`/soup/api/admin`

均需 `is_admin=1`。

- `POST /verify`：管理员二次密码校验。
- `GET /overview`：返回 `players/rooms/puzzles/puzzle_submissions/reports/flagged_content` 计数。
- `GET /submissions`：pending 投稿。
- `POST /submissions/{submission_id}/add`：投稿收录到题库，状态改 `added`。
- `POST /submissions/{submission_id}/ignore`：投稿改 `ignored`。
- `GET /players`：玩家列表。
- `PATCH /players/{player_id}/admin?enabled=0|1`：设置管理员。
- `POST /players/{player_id}/reset`：重置统计。
- `DELETE /players/{player_id}`：删除玩家。
- `GET /rooms`：管理员房间列表，包含 `answer`。
- `POST /rooms/{room_id}/finish`：强制结束房间。
- `GET /reports`：举报列表。
- `POST /reports/{report_id}/resolve`：举报处理。
- `GET /flags`：AI 扫描待处理标记。
- `POST /flags/{flag_id}/resolve`：标记处理。
- `GET /bans`：IP 封禁列表。
- `POST /bans`：新增或替换 IP 封禁。body: `{ip, reason}`。
- `DELETE /bans/{ban_id}`：解除封禁。
- `GET /api-configs`：裁判 API 配置列表，`api_key` 脱敏。
- `POST /api-configs`：新增裁判 API 配置。
- `PUT /api-configs/{config_id}`：更新裁判 API 配置；`api_key` 为空时保留旧 key。
- `DELETE /api-configs/{config_id}`：删除配置。
- `POST /api-configs/{config_id}/test`：对单条配置发连通性探测（不走 Round Robin，不影响 `fail_counts`）；返回 `{success, data, message}`，管理页列表与编辑弹窗均可触发。
- `POST /api-configs/test`：用弹窗草稿（可带 `config_id` 合并已存 key）探测连通性，保存前可测。
- `POST /api-configs/models`：拉取 OpenAI-compatible `/models` 列表，供编辑弹窗「拉取模型」与 datalist 使用。
- `GET /settings`：配置列表，返回 `[{key,value}]`。
- `PUT /settings/{key}`：保存配置值（`value` 最长 50000 字，保留换行）。管理页 settings Tab 在弹窗中用 `textarea` 编辑 `value`（勿从旧版单行 `<input>` 粘贴多行 prompt，否则换行会在保存前被压成空格）。
- `DELETE /settings/{key}`：删除配置项，之后代码会回退默认值或文件配置。
- Prompt 设置不走内存缓存：`judge_prompt`、`generate_prompt`、`judge_prompt_clue` 在裁判/生成调用时实时读 `settings` 表，因此管理后台保存或删除后下一次调用立即生效。

### 5.10 SSE

前缀：`/soup/api`

- `GET /sse/{room_id}`：房间 SSE。认证支持 Bearer 或 `?token=`。

事件：

- `new_log`
- `hint_offer`
- `hint_resolved`
- `game_over`
- `new_note`
- `update_note`
- `delete_note`

连接池实现：进程内 `_connections: dict[room_id, set[asyncio.Queue]]`。断开时从池中清理。每 25 秒发送 `: ping` keepalive。

### 5.11 cedartoy 平台账号 Auth

前缀：`/api/auth`（由 `cedartoy/server.py` 直接处理，不经 turtle-soup）

- `POST /login_or_register`：登录或注册。body: `{username, password}`。返回 `{token, user}`；强制 `is_ai=0`（人类网页/安卓端）。
- `POST /bind`：人类账号绑定 AI。需 Bearer token；body: `{binding_token}`。
- `GET /me`：当前用户及绑定列表。需 Bearer token；返回 `{user, bindings}`。

首页 `index.html` 使用以上 API；localStorage key 为 `cedartoy_token`。

### 5.12 cedartoy HTTP 路由

`cedartoy` 自身 HTTP 路由：

- `GET /`：返回 Toy 首页 `index.html`（游戏入口、排行榜、登录/绑定 UI）。
- `GET /health`：返回 `{"ok": true, "service": "cedartoy", "endpoints": [...]}`。
- `POST /`：根 MCP 聚合入口，实现 `initialize`、`tools/list`、`tools/call`。
- `POST /{token}`：与 `POST /` 相同 MCP handler；URL path 中的 token 作为 AI 持久登录凭证（`generate_binding_token` 流程外的另一种方式：登录后直接改 MCP 地址为 `https://toy.cedarstar.org/{token}`）。
- `POST /mbti`：MBTI JSON-RPC MCP server。
- `POST /dnd`：DND JSON-RPC MCP server。
- `GET /mbti`：MBTI HTTP GET 入口。通过 `action` 参数区分操作（`mbti_start`、`mbti_answer`、`mbti_get_result`），参数通过 query string 传入。响应自动附带 `next_urls`（含 `step` 和 `_r` 缓存busting 参数）。
- `GET /dnd`：DND HTTP GET 入口。结构同 MBTI，action 为 `dnd_start`、`dnd_answer`、`dnd_get_result`。
- `GET/POST/PUT/DELETE/OPTIONS /soup*`：反代到 `127.0.0.1:8012`。SSE（`/soup/api/sse/`）使用 HTTP/1.1 流式转发、`Cache-Control: no-cache`、`X-Accel-Buffering: no`，避免 Cloudflare Tunnel 路径下缓冲导致事件延迟。
- `GET/POST/PUT/DELETE/OPTIONS /mcp*`：legacy 反代到 `127.0.0.1:8012`。`turtle-soup` 当前只保留海龟汤自己的 `/mcp/play`；聚合工具不再走该路径。

## 6. MCP 层

### 6.1 入口

MCP 聚合层由 `/opt/cedartoy/server.py` 的根路径 `POST /` 提供：

- `tools/list` 暴露 `list_games`、`get_guide`、`play`、`account` 四个工具。
- `tools/call name=list_games`：在 `server.py` 返回硬编码游戏列表。
- `tools/call name=get_guide`：`turtle_soup` 返回硬编码 action 字典；`account`、`mbti`、`dnd` 读取 `/opt/cedartoy/turtle-soup/backend/guides/*.md`。
- `tools/call name=play`：由 `server.py` 按 `game` 分发。

公网访问时：

```text
https://toy.cedarstar.org/
  -> Cloudflare Tunnel
  -> cedartoy:8002
  -> server.py 根 MCP handler
      -> game=turtle_soup 转发到 127.0.0.1:8012/mcp/play
      -> game=mbti 本地调用 mbti.handler.handle_mcp
      -> game=dnd 本地调用 dnd.handler.handle_mcp
```

### 6.2 `list_games`

返回：

```json
{
  "测试": [
    {"name": "mbti", "display": "MBTI", "desc": "16型人格测试，4种模式可选（短/完整/快速）"},
    {"name": "dnd", "display": "DND阵营测试", "desc": "测试你的D&D道德阵营，守序善良还是混乱邪恶？"}
  ],
  "小游戏": [
    {"name": "turtle_soup", "display": "海龟汤", "desc": "横向思维推理游戏，提问猜汤底"}
  ],
  "提示": "用 get_guide(game) 查看具体玩法，再用 play(game, action, ...) 执行操作"
}
```

### 6.3 `get_guide`

- `game=turtle_soup`：返回 action 字典。
- `game=account`：读取 `/opt/cedartoy/turtle-soup/backend/guides/account.md` 并返回 `{game, guide}`。
- `game=mbti`：读取 `/opt/cedartoy/turtle-soup/backend/guides/mbti.md` 并返回 `{game, guide}`。
- `game=dnd`：读取 `/opt/cedartoy/turtle-soup/backend/guides/dnd.md` 并返回 `{game, guide}`。

### 6.4 `play(game="turtle_soup", ...)`

请求 body 基础字段：

```json
{
  "game": "turtle_soup",
  "action": "...",
  "username": "...",
  "password": "...",
  "room_id": "...",
  "content": "...",
  "log_id": 1,
  "accept": true
}
```

action 列表：

| action | 参数 | 行为 |
| --- | --- | --- |
| `list_rooms` | 无 | 返回 `waiting/playing` 房间列表，字段 `id/surface/status/created_at` |
| `status` | `room_id` | 返回房间公开状态与日志，不返回汤底 |
| `register` | `username?`, `password?` | 注册/登录 MCP 玩家；有 username 时持久账号，标记 `is_ai=1`；无 username 时创建游客 AI |
| `create_random` | `username?`, `password?` | 以 MCP 玩家身份创建随机题房间 |
| `join` | `room_id`, `username?`, `password?` | 查询并返回房间公开信息 |
| `ask` | `room_id`, `content`, `username?`, `password?` | 调用海龟汤 `ask` 逻辑，受 AI 冷却限制 |
| `guess` | `room_id`, `content`, `username?`, `password?` | 调用海龟汤 `guess` 逻辑 |
| `hint_respond` | `room_id`, `log_id`, `accept`, `username?`, `password?` | 接受或拒绝提示 |

身份规则：

- 传 `username/password`：若用户存在则校验密码并更新 `source='mcp'`、`is_ai=1`；若不存在则创建持久 AI 账号。
- 不传用户名：创建 `is_guest=1, is_ai=1, source='mcp'` 的游客账号。

### 6.5 `play(game="mbti", ...)`

`server.py` 在本进程内转换为 JSON-RPC payload，并直接调用 `mbti.handler.handle_mcp`。

支持 action：

| action | 参数 | 转换后的 JSON-RPC |
| --- | --- | --- |
| `initialize` | 可选额外字段 | `{jsonrpc:"2.0", id, method:"initialize"}` |
| `tools/list` | 可选额外字段 | `{jsonrpc:"2.0", id, method:"tools/list"}` |
| `mbti_start` | `player_id`, `mode` | `tools/call`，`name=mbti_start` |
| `mbti_answer` | `player_id`, `a_score` | `tools/call`，`name=mbti_answer` |
| `mbti_answer_batch` | `player_id`, `a_scores` | `tools/call`，`name=mbti_answer_batch` |
| `mbti_get_result` | `player_id` | `tools/call`，`name=mbti_get_result` |
| raw JSON-RPC | `method` 等额外字段 | 若 body 额外字段含 `method`，按原始 JSON-RPC 透传 |

MBTI 参数：

- `player_id`：1-10 位字母数字。
- `mode`：`short`、`full`、`short_fast`、`full_fast`。
- `a_score`：0-5 整数，表示 A 选项得分。
- `a_scores`：批量分数数组，快速模式使用，最多 16 个。

示例：

```json
{"game":"mbti","action":"mbti_start","player_id":"u123","mode":"short_fast"}
```

```json
{"game":"mbti","action":"mbti_answer_batch","player_id":"u123","a_scores":[5,4,3,2,1,0,5,4,3,2,1,0,5,4,3,2]}
```

### 6.6 `play(game="dnd", ...)`

`server.py` 在本进程内转换为 JSON-RPC payload，并直接调用 `dnd.handler.handle_mcp`。

支持 action：

| action | 参数 | 转换后的 JSON-RPC |
| --- | --- | --- |
| `initialize` | 可选额外字段 | `{jsonrpc:"2.0", id, method:"initialize"}` |
| `tools/list` | 可选额外字段 | `{jsonrpc:"2.0", id, method:"tools/list"}` |
| `dnd_start` | `player_id`, `mode` | `tools/call`，`name=dnd_start` |
| `dnd_answer` | `player_id`, `answer` | `tools/call`，`name=dnd_answer` |
| `dnd_answer_batch` | `player_id`, `answers` | `tools/call`，`name=dnd_answer_batch` |
| `dnd_get_result` | `player_id` | `tools/call`，`name=dnd_get_result` |
| raw JSON-RPC | `method` 等额外字段 | 若 body 额外字段含 `method`，按原始 JSON-RPC 透传 |

DND 参数：

- `player_id`：1-10 位字母数字。
- `mode`：`full`（36题逐题）、`full_fast`（36题一次性提交）。
- `answer`：逐题答案，1-4 整数。
- `answers`：批量答案数组，快速模式使用。

### 6.7 `account`

平台统一账号工具（存档用；不登录也可玩，但游客数据 48 小时后清理）。

| action | 参数 | 行为 |
| --- | --- | --- |
| `login_or_register` | `username`, `password` | 自动登录或注册，返回 `{token, user}`；强制 `is_ai=1`（与 REST 人类登录分离） |
| `generate_binding_token` | `token`（可选，或走 `POST /{token}` path token） | AI 账号生成 10 分钟绑定码，人类在首页输入完成绑定 |
| `get_bindings` | `token`（可选，或 path token） | AI 账号查看绑定自己的人类列表（`username`、`bound_at`） |
| `get_profile` | `token`（可选，或 path token） | 账号信息 + 绑定列表 + 游戏概览（海龟汤按 `players.username` 匹配 `game_count`/`win_count`；MBTI/DND 按 `SESSIONS_DB` 中 `test_results`，`player_id` 为账号 id 或 1–10 位字母数字用户名） |

人类网页登录：`https://toy.cedarstar.org` 右上角。AI 持久 MCP：登录后将 MCP 地址改为 `https://toy.cedarstar.org/{token}`。

## 7. 裁判 LLM 与 Round Robin 容错

实现文件：`turtle-soup/backend/judge.py`

配置来源：`judge_api_configs` 表，仅使用 `enabled=1` 的行，按 `priority ASC, id ASC` 排序（排序决定轮转列表顺序，非固定主备）。

运行时状态：

```python
fail_counts: dict[int, int] = {}
_rr_index: int = 0
FAIL_LIMIT = 5
```

请求流程：

1. `_configs()` 读取启用配置，并过滤掉 `fail_counts[id] >= 5` 的配置。
2. `_chat()` 从 `_rr_index % len(available)` 起，在排好序的列表上 Round Robin 循环尝试（单次请求内仍会试完所有可用配置）。
3. 每个配置调用 OpenAI-compatible `/chat/completions`：
   - 若 `api_url` 已以 `/chat/completions` 结尾，直接使用。
   - 否则拼接 `{api_url.rstrip("/")}/chat/completions`。
4. 当前配置请求成功：`fail_counts[id]=0`，`_rr_index` 推进到该配置下一项，返回文本。
5. 当前配置异常：`fail_counts[id]+=1`，`_rr_index` 不变，立即尝试列表中下一配置。
6. 全部失败或无可用配置：返回 HTTP 503，`裁判暂时不可用，请稍后再试`。

管理探测：`test_config(cfg)` 对单条配置发简短 chat 请求；`list_models(cfg)` 拉取 `/models`。均不修改 `fail_counts` 与 `_rr_index`。

生产 API 池现状应通过管理后台维护：只有 `enabled=1` 的配置参与轮询。格式或语义不稳定的配置即使连通也会影响对局，因为 `_chat()` 一旦某配置 HTTP 成功就返回文本，后续解析失败不会自动切到下一个配置。

系统 prompt 来源：

1. `judge_prompt`：每次 `judge_ask` / `judge_guess` / `generate_hint` 调用时实时读取 `settings.judge_prompt` 非空值；否则回退 `config/judge_prompt.txt`；再否则用 `DEFAULT_SETTINGS["judge_prompt"]`。
2. `generate_prompt`：每次 `generate_puzzle` 调用时实时读取 `settings.generate_prompt` 非空值；否则回退 `DEFAULT_SETTINGS["generate_prompt"]`。
3. `judge_prompt_clue`：线索汤提问判定时实时读取 `settings.judge_prompt_clue`，缺失时为空字符串。
4. 管理后台保存或删除上述三项后，下一次对应裁判/生成调用立即读到新值，无需重启服务。

线索汤约定：

- 创作者可在汤底中写入 `【线索公布】` 标记，标记后描述中途应公开的线索或触发条件；大厅自定义创建框在汤底为空时会用半透明占位提示该写法。
- `judge_ask` 只有在房间汤底包含 `【线索公布】` 时才拼接 `judge_prompt_clue`，并允许模型输出 `【线索公布】...`。
- 触发后的线索落库为 `game_logs.type='auto_hint', judgment='auto_hint'`，前端显示为无按钮的「【线索公布】」横幅。

裁判函数：

- `judge_ask(surface, answer, question) -> {judgment, clue?, content_override?}`
  - 实时读取 `judge_prompt`；线索汤额外读取并拼接 `judge_prompt_clue`。
  - 每次请求追加类型约束系统消息，明确这是“普通提问判定”；首行合法值为「是 / 不是 / 无关 / 是也不是」，并兼容「不相关 / 没有关联」。
  - `_chat_validated` 最多重试 3 次；仍失败则 `unrelated` + `content_override=【系统提示】系统开小差了，请再次提问`。
- `judge_guess(surface, answer, guess) -> {success, score, answer?, error?}`
  - 实时读取 `judge_prompt`，并追加类型约束系统消息，明确这是“猜测汤底，不是普通提问”。
  - 解析 `【通关】/【未通关】`、还原度与可选 `【汤底】`；失败时 `error=【系统提示】系统开小差了，请再次提问`。
  - `_parse_guess_result` 会剥离外围 Markdown code fence，再按首行和第二行数字解析。
- `generate_hint(surface, answer, game_log) -> str | None`
  - 实时读取 `judge_prompt`，并使用最近最多 40 条 ask 记录；回复须以 `【提示】` 开头，返回最多 120 字；失败返回 `None`。
- `generate_puzzle() -> {surface, answer}`
  - 实时读取 `generate_prompt`；要求模型返回 JSON。
  - 解析失败返回 HTTP 502。
- `scan_text(text) -> reason | None`
  - 用于 AI 内容扫描。
  - 返回以 `unsafe` 开头时写入 flagged_content。

安全约束：

- 用户输入由接口层 `clean_content` 限长与过滤 `< > { }`。
- 系统 prompt 与用户变量分离在 messages 中，未将用户输入拼接进 system prompt。
- 普通房间详情不返回 `answer`；仅管理员房间列表和 `game_over` 会下发汤底。

## 8. 定时任务

实现：`turtle-soup/backend/scheduler.py`

启动：FastAPI lifespan 中 `start_scheduler()`，timezone 为 `Asia/Shanghai`。

### `cleanup_guests`

调度：每 1 小时。

清理条件：

```sql
is_guest = 1
created_at < now - guest_expire_hours
last_active_at < now - guest_expire_hours
```

动作：

- 将该游客创建且未结束的房间强制 `finished`。
- 删除该游客的 `room_notes`。
- 删除该游客的 `game_logs`。
- 删除该游客 `players` 记录。

### `scan_recent_content`

调度：每天 03:00。

扫描对象：

- 最近 2 天注册、未被同 type/ref_id 标记过的 username。
- `puzzle_submissions.status='pending'` 且未被同 type/ref_id 标记过的投稿。

动作：

- 调用 `judge.scan_text`。
- 可疑时写入 `flagged_content`，不自动封禁或删除。
- 异常只记录 warning，不中断服务。

## 9. nginx 转发规则

当前文件：`/etc/nginx/sites-available/toy.cedarstar.org`

启用：`/etc/nginx/sites-enabled/toy.cedarstar.org`

核心规则：

```nginx
server {
    listen 80;
    server_name toy.cedarstar.org;

    location = / {
        proxy_pass http://127.0.0.1:8012/;
        proxy_set_header Host $host;
    }

    location /soup/api/ {
        proxy_pass http://127.0.0.1:8012/soup/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    location = /soup {
        proxy_pass http://127.0.0.1:8012/soup;
        proxy_set_header Host $host;
    }

    location /soup/ {
        proxy_pass http://127.0.0.1:8012/soup/;
        proxy_set_header Host $host;
    }

    location /mcp/ {
        proxy_pass http://127.0.0.1:8012/mcp/;
        proxy_set_header Host $host;
    }

    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

配置片段：`/opt/cedartoy/turtle-soup/soup.conf`，所有 `proxy_pass` 均指向 `8012`。

重要现实差异：

- 本机 HTTP 命中 nginx 时，以上规则生效。
- 本机 nginx 的 `location = /` 当前代理到 `8012`，因此通过 nginx 访问裸根路径会进入 `turtle-soup` 首页，不会进入 `cedartoy` 根 MCP handler。
- 根 MCP 聚合入口是 `cedartoy:8002` 的 `POST /`。公网 Cloudflare Tunnel 直连 `cedartoy:8002` 时可用；本机若经 nginx 验证根 MCP，应绕过 nginx 直接请求 `127.0.0.1:8002/`，或先调整 nginx `location = /`。
- nginx 的 `/mcp/` 仍代理到 `8012`，只适用于 legacy 海龟汤 `/mcp/play`；`list_games`、`get_guide`、聚合 `play` 不再由 `/mcp/*` 提供。
- 公网 `https://toy.cedarstar.org` 当前由 Cloudflare Tunnel 直连 `cedartoy:8002`，因此公网 `/soup` 依赖 `cedartoy/server.py` 中的反代逻辑，而不是 nginx。

## 10. supervisord 配置

### turtle-soup

实际加载文件：`/etc/supervisor/conf.d/turtle-soup.conf`

同时存在 `/etc/supervisor/conf.d/turtle-soup.ini`，但当前 `/etc/supervisor/supervisord.conf` include 规则是：

```ini
files = /etc/supervisor/conf.d/*.conf
```

因此 `.ini` 不会被 supervisord 自动加载，实际生效的是 `.conf`。

配置：

```ini
[program:turtle-soup]
command=/opt/cedarstar/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8012
directory=/opt/cedartoy/turtle-soup/backend
autostart=true
autorestart=true
stderr_logfile=/var/log/turtle-soup.err.log
stdout_logfile=/var/log/turtle-soup.out.log
```

### cedartoy

实际加载文件：`/etc/supervisor/conf.d/cedartoy.conf`

```ini
[program:cedartoy]
command=python3 /opt/cedartoy/server.py
directory=/opt/cedartoy
autostart=true
autorestart=true
stderr_logfile=/var/log/cedartoy.err.log
stdout_logfile=/var/log/cedartoy.out.log
user=root
```

常用命令：

```bash
supervisorctl status cedartoy turtle-soup
supervisorctl restart turtle-soup
supervisorctl restart cedartoy
supervisorctl reread && supervisorctl update
```

当前状态示例：

```text
cedartoy                         RUNNING
turtle-soup                      RUNNING
```

## 11. 前端构建与静态资源

前端目录：`/opt/cedartoy/turtle-soup/frontend`

构建命令：

```bash
cd /opt/cedartoy/turtle-soup/frontend
npm run build
```

Vite 配置：

- `base: "/soup/"`
- `outDir: "../backend/static"`
- dev proxy `/soup/api` -> `http://127.0.0.1:8002`

生产服务：

- `backend/static/index.html` 由 FastAPI `/soup`、`/soup/{path}` 返回。
- `backend/static/assets/*` 挂载到 `/soup/assets/*`（`gitignore`，部署前需在本机 `npm run build`）。

### 11.1 海龟汤 SPA 页面（`/soup/`）

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `/soup/` | `Lobby` | 像素风大厅：房间列表（含 48h 内已结束房）、汤名搜索与标签筛选、创建房间防重复提交；自定义汤底输入框空白时提示 `【线索公布】` 标记写法 |
| `/soup/room/:id` | `Room` | 对局页：SSE 订阅、`presence.enter_room`；判定前缀中文展示；开小差类日志归一为 `【系统提示】...`；`auto_hint`→「【线索公布】」、`hint_offer`→「【请求提示】」（接受/拒绝，上限 3 次，请求前确认）；`game_over`→「汤底揭晓」且正文含还原度；记事本侧栏/抽屉 |
| `/soup/profile` | `Profile` | 登录用户资料 |
| `/soup/admin` | `Admin` | 管理后台（需 `is_admin`）；裁判 API 弹窗支持「测试」「拉取模型」；settings `value` 用 `textarea`，含 `judge_prompt_clue` 说明 |
| `/soup/add-puzzle` | `AddPuzzle` | 管理员加题 |
| `/soup/login` | — | 重定向到 `/soup/`（已移除独立登录页） |

大厅登录：游客点击右上角头像弹出与 Toy 首页同款的 `LoginModal`，调用公网 `POST /api/auth/login_or_register` 后写入 `cedartoy_token`/`cedartoy_user_id`，再 `POST /soup/api/auth/guest` 换取海龟汤 JWT。已登录用户点头像进入 `/profile`。

房间卡片展示题库 `title`（无则汤面首句）、汤面两行省略、标签与「提问/在房」统计；列表默认按在房人数、提问数排序（「按热度排序」按钮仅重新拉取列表）。移动端锁高 `100dvh`、仅中间 `.lobby-main` 内滚动；卡片整卡可点进入；创建用 FAB + 固定高度抽屉，「创建」按钮贴底可见。

对局日志视觉规则：普通日志正文为 15px；判定标签为 13px；提示/线索/汤底揭晓卡片标题与正文不低于 15px，避免系统信息比玩家正文弱。

## 12. 部署注意事项

1. `turtle-soup` 端口必须保持 `8012`，因为 nginx、cedartoy 反代、supervisord 当前均按 8012 配置。
2. `cedartoy` 是公网 Cloudflare Tunnel 的实际第一跳，也是 MCP 聚合层。修改 nginx 不一定影响公网 HTTPS 行为；如公网 `/soup` 或根 MCP 异常，应先检查 `cedartoy/server.py`。
3. supervisord 当前只加载 `*.conf`。不要只复制 `turtle-soup.ini` 后期待生效；需要同步 `.conf`，或修改 supervisor include 规则。
4. `TURTLE_SOUP_SECRET` 当前若未设置会使用默认值 `<your-secret-here>`。生产建议在环境中显式设置并保持稳定；变更会使旧海龟汤 JWT 失效。
5. `TOY_SECRET` 用于 cedartoy 平台账号 JWT（`/api/auth/*` 与 MCP `account`）。默认 `change-me-before-production`；变更会使旧平台 token 失效。`SESSIONS_DB`（默认 `/opt/cedartoy/data/sessions.db`）供 `get_profile` 统计 MBTI/DND 完成次数；`TURTLE_SOUP_DB` 供平台账号与海龟汤 `players` 统计。
6. 裁判 LLM 不配置 `judge_api_configs` 时，`ask/guess/generate/hint/AI扫描` 会返回裁判不可用或失败；普通登录、开房、房间列表不依赖 LLM。
7. `judge_api_configs.api_key` 存在 SQLite 中，管理 API 列表会脱敏，但数据库文件本身需要限制访问权限。启用配置前不只要测 HTTP 连通，还要用真实 `ask/guess` 场景确认输出格式与语义；连通但格式错误的节点会造成玩家看到 `【系统提示】系统开小差了...`。
8. `settings.judge_prompt`、`settings.generate_prompt`、`settings.judge_prompt_clue` 不走缓存；通过管理后台或直接改表后，下一次裁判/生成调用立即生效。
9. 汤底保护：普通 `/rooms/{room_id}` 和 MCP `status` 不返回 `answer`；管理员 `/admin/rooms` 会返回完整 answer；猜中后 SSE `game_over` 会下发纯 `answer`，揭晓日志会额外显示还原度。
10. `rooms/profile/me` 当前存在路由顺序风险，可能被 `/{room_id}` 捕获；若个人页不可用，应先修正 `routers/rooms.py` 中路由顺序。
11. SSE 通过内存连接池实现，多进程部署时不同进程之间不会共享事件；当前 supervisord 启动单 uvicorn 进程。
12. `cedartoy` MBTI 和 DND session 上限均为 `MAX_SESSIONS=500`，进行中 session 24 小时未活动清理，结果 48 小时清理。
13. 数据库迁移当前没有独立 migration 框架。海龟汤表由 `database.py:init_db()` 创建；平台账号表（`toy_users` 等）需手写迁移，当前不在 `init_db()` 中。
14. 海龟汤 Lobby 进入时 `ensureGuestToken()`：若 `localStorage.cedartoy_user_id` 存在则 `POST /auth/guest` 带 `user_id` 绑定平台账号，否则创建游客；无需先登录即可浏览房间列表。
15. 修改 `turtle-soup/frontend` 后必须在服务器执行 `npm run build`，否则公网仍服务旧的 `backend/static`（该目录不入库）。
16. 日志位置：
    - `/var/log/turtle-soup.out.log`
    - `/var/log/turtle-soup.err.log`
    - `/var/log/cedartoy.out.log`
    - `/var/log/cedartoy.err.log`

## 13. 快速验证

```bash
supervisorctl status cedartoy turtle-soup
curl -s https://toy.cedarstar.org/soup/health
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_games","arguments":{}}}' \
  https://toy.cedarstar.org/
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_guide","arguments":{"game":"turtle_soup"}}}' \
  https://toy.cedarstar.org/
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"play","arguments":{"game":"mbti","action":"tools/list"}}}' \
  https://toy.cedarstar.org/
```

预期：

- `cedartoy` 与 `turtle-soup` 均为 `RUNNING`。
- `/soup/health` 返回 `{"status":"healthy"}`。
- 根 MCP `list_games` 同时包含 `turtle_soup`、`mbti` 和 `dnd`。
- MBTI `tools/list` 返回 `mbti_start`、`mbti_answer`、`mbti_answer_batch`、`mbti_get_result`。
