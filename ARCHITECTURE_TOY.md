# Toy Platform Architecture

本文档描述 `toy.cedarstar.org` 当前已实现的线上结构，覆盖 `cedartoy`、`turtle-soup`、MCP 聚合层、部署配置与注意事项。

## 1. 总览

Toy Platform 目前由两个本地服务组成：

- `cedartoy`：Toy 聚合层，监听 `0.0.0.0:8002`。根路径 `POST /` 是统一 MCP 入口，直接实现 `list_games`、`get_guide`、`play`；MBTI 和 DND 在本进程处理，海龟汤动作按需转发到 `turtle-soup:8012`。
- `turtle-soup`：海龟汤服务，监听 `127.0.0.1:8012`，提供海龟汤 Web/API/SSE，以及只属于海龟汤自身的 `/mcp/play` 接口。

公网实际链路：

```text
Cloudflare Tunnel
  -> cedartoy 0.0.0.0:8002
      -> POST /                cedartoy MCP 聚合层：list_games/get_guide/play
      -> /mbti                 cedartoy 本地 MBTI JSON-RPC MCP
      -> /dnd                  cedartoy 本地 DND JSON-RPC MCP
      -> /soup, /soup/*        cedartoy 反代到 127.0.0.1:8012
      -> /mcp/play             legacy 海龟汤 MCP play 反代到 127.0.0.1:8012
```

本机 nginx 也配置了 `toy.cedarstar.org` 的 HTTP server，但公网 HTTPS 当前由 Cloudflare Tunnel 直连 `8002`，不一定经过 nginx。新的 MCP 聚合入口是 `cedartoy` 的 `POST /`，不依赖 nginx `/mcp` 规则。

## 2. 项目结构

```text
/opt/cedartoy/
├── server.py                 # cedartoy HTTP 服务，端口 8002
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
│   ├── judge.py              # 裁判 LLM 调用、轮询容错
│   ├── sse.py                # SSE 连接池与广播
│   ├── mcp_app.py            # 海龟汤自身 MCP play 接口
│   ├── guides/
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
│   │   ├── pages/
│   │   ├── components/
│   │   ├── styles/
│   │   └── api.js
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
- `created_at` / `finished_at`。

#### `game_logs`

游戏日志。

- `type`：`ask`、`guess`、`hint_offer`、`hint_accept`、`hint_reject`、`system`。
- `content`：提问、猜测或系统内容。
- `judgment`：`yes`、`no`、`unrelated`、`partial`。
- `hint_text`：提示文本，`hint_offer` 使用。
- `resolved`：提示是否已被第一个响应处理。

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
| `hint_trigger_count` | `30` | 每多少个 ask 触发提示 |
| `ai_cooldown_questions` | `5` | AI 冷却检查最近 N 条提问 |
| `ai_cooldown_seconds` | `3` | AI 冷却窗口秒数 |
| `generate_cooldown_seconds` | `5` | 前端 AI 出题冷却默认值 |
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

- `POST /guest`：创建游客，返回 `{token, player}`。
- `POST /register`：注册；若 username 已存在则转登录逻辑。body: `{username, password, source}`。
- `POST /login`：登录。`source=mcp` 会将账号标记为 `is_ai=1`。
- `GET /me`：当前用户公开信息。

JWT payload：`player_id`、`is_admin`、`is_guest`、`exp`。有效期 14 天。

### 5.3 Puzzles

前缀：`/soup/api/puzzles`

- `GET /random`：登录用户随机抽一条 enabled 题，只返回 `id/surface/tags`。
- `POST /submit`：投稿，写入 `puzzle_submissions`。
- `GET /`：管理员题库列表，不返回汤底。
- `POST /`：管理员新增题。
- `PATCH /{puzzle_id}/toggle`：管理员启用/禁用。
- `DELETE /{puzzle_id}`：管理员删除题。

### 5.4 Rooms

前缀：`/soup/api/rooms`

- `GET /`：房间列表，含状态、汤面、提问数、活跃玩家数。
- `POST /create`：创建房间。
  - `mode=random`：从题库题创建。
  - `mode=custom`：使用 body 中 `surface/answer` 创建，并写入投稿。
  - 其他 mode：使用 body 中 `surface/answer` 创建，不写题库。
  - 每个玩家同时只能创建一个 `waiting/playing` 房间。
  - 全局受 `settings.max_rooms` 限制。
- `GET /{room_id}`：房间详情，返回 logs 和 notes；普通响应不含 `answer`。
- `POST /{room_id}/close`：房主或管理员关闭房间。
- `GET /profile/me`：当前用户统计和历史房间。

注意：`/profile/me` 在代码中定义在 `/{room_id}` 之后，FastAPI 路径匹配可能使 `/rooms/profile/me` 被 `/{room_id}` 捕获；如发现个人页异常，应调整路由顺序或改路径。

### 5.5 Game

前缀：`/soup/api/game`

- `POST /ask`：提问。body: `{room_id, content}`。
  - 内容经过 `clean_content`，限制 200 字，拒绝 `< > { }`。
  - `is_ai=1` 时启用提问冷却：若最近 `ai_cooldown_questions` 条 ask 都在 `ai_cooldown_seconds` 内，返回 429。
  - 调用 `judge_ask(answer, question)`，写入 `game_logs(type=ask)`。
  - 更新 `players.ask_count` 和对应分项。
  - 广播 SSE `new_log`。
  - 当房间 ask 数是 `hint_trigger_count` 的倍数，且同触发点未发过提示，调用 `generate_hint`，写入 `hint_offer`，广播 `hint_offer`。
- `POST /guess`：猜汤底。body: `{room_id, content}`。
  - 调用 `judge_guess(answer, guess)`。
  - 写入 `game_logs(type=guess)`。
  - 猜中时：房间 `finished`，写 `winner_id/finished_at`，胜者 `win_count+1`，所有有 ask/guess 记录的玩家 `game_count+1`，广播 `new_log` 和 `game_over`，`game_over` 中下发汤底。
  - 猜错时：广播 `new_log`。
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
- `GET /settings`：配置列表，返回 `[{key,value}]`。
- `PUT /settings/{key}`：保存配置值。管理页 settings Tab 已实现为 key/value 表单。

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

### 5.11 cedartoy

`cedartoy` 自身 HTTP 路由：

- `GET /`、`GET /health`：返回 `{"ok": true, "service": "cedartoy", "endpoints": ["https://toy.cedarstar.org/mbti", "https://toy.cedarstar.org/dnd", "https://toy.cedarstar.org/"]}`。
- `POST /`：根 MCP 聚合入口，实现 `initialize`、`tools/list`、`tools/call`。
- `POST /mbti`：MBTI JSON-RPC MCP server。
- `POST /dnd`：DND JSON-RPC MCP server。
- `GET /mbti`：MBTI HTTP GET 入口。通过 `action` 参数区分操作（`mbti_start`、`mbti_answer`、`mbti_answer_batch`、`mbti_get_result`），参数通过 query string 传入。响应自动附带 `next_urls`（逐题模式，含 `step` 和 `_r` 缓存busting 参数）或 `next_url`（快速模式）。
- `GET /dnd`：DND HTTP GET 入口。结构同 MBTI，action 为 `dnd_start`、`dnd_answer`、`dnd_answer_batch`、`dnd_get_result`。
- `GET/POST/PUT/DELETE/OPTIONS /soup*`：反代到 `127.0.0.1:8012`。
- `GET/POST/PUT/DELETE/OPTIONS /mcp*`：legacy 反代到 `127.0.0.1:8012`。`turtle-soup` 当前只保留海龟汤自己的 `/mcp/play`；聚合工具不再走该路径。

## 6. MCP 层

### 6.1 入口

MCP 聚合层由 `/opt/cedartoy/server.py` 的根路径 `POST /` 提供：

- `tools/list` 暴露 `list_games`、`get_guide`、`play` 三个工具。
- `tools/call name=list_games`：在 `server.py` 返回硬编码游戏列表。
- `tools/call name=get_guide`：`turtle_soup` 返回硬编码 action 字典；`mbti`、`dnd` 读取 `/opt/cedartoy/turtle-soup/backend/guides/*.md`。
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

## 7. 裁判 LLM 与轮询容错

实现文件：`turtle-soup/backend/judge.py`

配置来源：`judge_api_configs` 表，仅使用 `enabled=1` 的行，按 `priority ASC, id ASC` 排序。

运行时状态：

```python
fail_counts: dict[int, int] = {}
FAIL_LIMIT = 5
```

请求流程：

1. `_configs()` 读取启用配置，并过滤掉 `fail_counts[id] >= 5` 的配置。
2. `_chat()` 按优先级遍历可用配置。
3. 每个配置调用 OpenAI-compatible `/chat/completions`：
   - 若 `api_url` 已以 `/chat/completions` 结尾，直接使用。
   - 否则拼接 `{api_url.rstrip("/")}/chat/completions`。
4. 当前配置请求成功：`fail_counts[id]=0`，返回文本。
5. 当前配置异常：`fail_counts[id]+=1`，立即尝试下一个配置。
6. 全部失败或无可用配置：返回 HTTP 503，`裁判暂时不可用，请稍后再试`。

裁判函数：

- `judge_ask(answer, question) -> yes/no/unrelated/partial`
  - 使用 `config/judge_prompt.txt`。
  - 若模型返回非合法值，当前实现回退为 `unrelated`。
- `judge_guess(answer, guess) -> bool`
  - 模型只应返回 true/false。
  - `true/yes/1/对/正确` 判定为真。
- `generate_hint(answer, game_log) -> str`
  - 使用最近最多 40 条 ask 记录。
  - 返回最多 120 字。
- `generate_puzzle() -> {surface, answer}`
  - 要求模型返回 JSON。
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
- `backend/static/assets/*` 挂载到 `/soup/assets/*`。

## 12. 部署注意事项

1. `turtle-soup` 端口必须保持 `8012`，因为 nginx、cedartoy 反代、supervisord 当前均按 8012 配置。
2. `cedartoy` 是公网 Cloudflare Tunnel 的实际第一跳，也是 MCP 聚合层。修改 nginx 不一定影响公网 HTTPS 行为；如公网 `/soup` 或根 MCP 异常，应先检查 `cedartoy/server.py`。
3. supervisord 当前只加载 `*.conf`。不要只复制 `turtle-soup.ini` 后期待生效；需要同步 `.conf`，或修改 supervisor include 规则。
4. `TURTLE_SOUP_SECRET` 当前若未设置会使用默认值 `<your-secret-here>`。生产建议在环境中显式设置并保持稳定；变更会使旧 JWT 失效。
5. 裁判 LLM 不配置 `judge_api_configs` 时，`ask/guess/generate/hint/AI扫描` 会返回裁判不可用或失败；普通登录、开房、房间列表不依赖 LLM。
6. `judge_api_configs.api_key` 存在 SQLite 中，管理 API 列表会脱敏，但数据库文件本身需要限制访问权限。
7. 汤底保护：普通 `/rooms/{room_id}` 和 MCP `status` 不返回 `answer`；管理员 `/admin/rooms` 会返回完整 answer；猜中后 SSE `game_over` 会下发 answer。
8. `rooms/profile/me` 当前存在路由顺序风险，可能被 `/{room_id}` 捕获；若个人页不可用，应先修正 `routers/rooms.py` 中路由顺序。
9. SSE 通过内存连接池实现，多进程部署时不同进程之间不会共享事件；当前 supervisord 启动单 uvicorn 进程。
10. `cedartoy` MBTI 和 DND session 上限均为 `MAX_SESSIONS=500`，进行中 session 24 小时未活动清理，结果 48 小时清理。
11. 数据库迁移当前没有独立 migration 框架，表结构由启动时 `CREATE TABLE IF NOT EXISTS` 创建。已有表新增字段不会自动补齐，结构变更需手写迁移。
12. 日志位置：
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
