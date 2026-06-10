# Toy Platform Architecture

本文档描述 `toy.cedarstar.org` 当前已实现的线上结构，覆盖 `cedartoy`、`turtle-soup`、MCP 聚合层、部署配置与注意事项。

## 1. 总览

Toy Platform 目前由两个本地服务组成：

- `cedartoy`：Toy 聚合层，监听 `127.0.0.1:8002`。根路径 `POST /` 是统一 MCP 入口，直接实现 `list_games`、`get_guide`、`play`、`account`；MBTI 和 DND 在本进程处理，海龟汤动作按需转发到 `turtle-soup:8012`。`GET /` 返回 Toy 首页 `index.html`（含登录/绑定 UI）；`POST /{token}` 支持 AI 持久化 MCP 连接。
- `turtle-soup`：海龟汤服务，监听 `127.0.0.1:8012`，提供海龟汤 Web/API/SSE，以及只属于海龟汤自身的 `/mcp/play` 接口。

公网实际链路：

```text
Cloudflare Tunnel
  -> cedartoy 127.0.0.1:8002
      -> GET /                 cedartoy Toy 首页 index.html
      -> POST /                cedartoy MCP 聚合层：list_games/get_guide/play/account
      -> POST /{token}         cedartoy MCP（AI 持久 token，等同根 MCP）
      -> /api/auth/*           cedartoy 平台账号 REST API
      -> /mbti                 cedartoy 本地 MBTI JSON-RPC MCP
      -> /dnd                  cedartoy 本地 DND JSON-RPC MCP
      -> /soup, /soup/*        cedartoy 反代到 127.0.0.1:8012
      -> /mcp/play             legacy 海龟汤 MCP play 反代到 127.0.0.1:8012
```

本机 nginx 也配置了 `toy.cedarstar.org` 的 HTTP server，但公网 HTTPS 当前由 Cloudflare Tunnel 直连本机 `127.0.0.1:8002`，不一定经过 nginx。新的 MCP 聚合入口是 `cedartoy` 的 `POST /`，不依赖 nginx `/mcp` 规则。

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
| `cedartoy` | `8002` | `127.0.0.1` | supervisord | Toy 聚合层、根 MCP `POST /`、MBTI `/mbti`、DND `/dnd`、反代 `/soup*` 和 legacy `/mcp*` 到 `8012` |
| `turtle-soup` | `8012` | `127.0.0.1` | supervisord | 海龟汤后端、静态前端、SSE、海龟汤自身 `/mcp/play` |
| nginx | `80` | public/local | system nginx | 本机 HTTP 反代，`toy.cedarstar.org` server 块 |
| Cloudflare Tunnel | HTTPS | Cloudflare edge | systemd `cloudflared` | 公网 HTTPS 入口，当前直连本机 `cedartoy:8002` |

### 3.1 运行时边界

`cedartoy` 是公网第一跳和聚合层，代码位于根目录 `server.py`。它使用标准库 `BaseHTTPRequestHandler` + `ThreadPoolHTTPServer`，不是 FastAPI；内部通过 `ThreadPoolExecutor(max_workers=50)` 和 `BoundedSemaphore` 控制并发，排队超过 `QUEUE_TIMEOUT_SECONDS=10` 会返回服务繁忙类错误。它直接处理 Toy 首页、平台账号 REST、根 MCP、MBTI/DND MCP，并把 `/soup*` 与 legacy `/mcp*` 反代给 `turtle-soup`。

`turtle-soup` 是海龟汤专用 FastAPI 服务，代码位于 `turtle-soup/backend`。它负责海龟汤自己的 JWT、房间、日志、SSE、裁判 LLM、管理后台 API 和静态前端；FastAPI docs/OpenAPI 入口关闭，CORS 仅允许 `https://toy.cedarstar.org`。除 legacy `/mcp/play` 外，新的聚合 MCP 不在该服务内实现。

### 3.2 典型请求链路

| 场景 | 链路 | 关键状态 |
| --- | --- | --- |
| 打开 Toy 首页 | Browser -> Cloudflare Tunnel -> `cedartoy:8002` -> `index.html` | 平台 token 存在 `localStorage.cedartoy_token` |
| 打开海龟汤 SPA | Browser -> `cedartoy:8002 /soup*` -> proxy -> `turtle-soup:8012` | 海龟汤 token 存在 `localStorage.turtle_soup_token` |
| 网页登录后进海龟汤 | `POST /api/auth/login_or_register` -> `POST /soup/api/auth/guest {user_id}` | Toy 账号和 `players.user_id` 打通，但两套 JWT 独立 |
| 海龟汤对局实时日志 | Browser EventSource `/soup/api/sse/{room_id}?token=...` -> `turtle-soup` | SSE 连接进入 `room_presence`，断开时离开 |
| AI 调用根 MCP | MCP Client -> `POST /` 或 `POST /{token}` -> `cedartoy` | `/{token}` 作为 AI 持久账号 token |
| AI 玩海龟汤 | 根 MCP `play(game=turtle_soup, ...)` -> `cedartoy` -> `127.0.0.1:8012/mcp/play` | 海龟汤 MCP 玩家写入 `players.is_ai=1` |
| AI 玩 MBTI/DND | 根 MCP `play(game=mbti/dnd, ...)` -> `server.py` 本进程 handler | 状态写入 `data/sessions.db` |

### 3.3 身份与 Token 分层

| 层 | 签发位置 | 存储位置 | 过期 | 作用范围 |
| --- | --- | --- | --- | --- |
| Toy 平台账号 token | `server.py` / `TOY_SECRET` | `localStorage.cedartoy_token` 或 MCP path token | 人类 30 天；AI 无 `exp` | `/api/auth/*`、根 MCP `account`、`POST /{token}` |
| 海龟汤 token | `auth_utils.py` / `TURTLE_SOUP_SECRET` | `localStorage.turtle_soup_token` | 14 天 | `/soup/api/*`、SSE query token |
| MBTI/DND player_id | 调用方传入 | `data/sessions.db` | 进行中 24 小时；结果 48 小时 | MBTI/DND 测试流程 |

Toy 平台账号和海龟汤 `players` 不是同一张表。网页端通过 `/auth/guest {user_id}` 创建或复用 `players.user_id = toy_users.id` 的海龟汤用户；MCP 海龟汤通过 `POST /{token}` 的 path token 绑定 `toy_users.id`，无 path token 时创建游客 AI 玩家。

## 4. 数据库

### 4.1 turtle-soup 数据库

位置：`/opt/cedartoy/turtle-soup/backend/turtle_soup.db`

初始化：`turtle-soup/backend/database.py:init_db()` 在 FastAPI lifespan 启动时自动创建表、默认 settings 和 3 道种子题。

#### `players`

玩家表。

- `id`：自增主键。
- `username`：注册用户名；网页游客创建时分配 `游客1` 到 `游客999` 的循环显示名，若编号仍被占用则跳过；MCP 临时游客仍可为空并由展示层回退为 `游客{id}`；唯一。
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

- `title`：汤名（短标题，可选，统一上限 20 字）。
- `surface`：汤面（统一上限 1000 字）。
- `answer`：汤底（统一上限 3000 字），仅后端使用；普通房间 API 不返回。
- `tags`：标签。
- `enabled`：是否可随机抽取。
- `created_by`：创建人。

#### `puzzle_submissions`

用户投稿表。

- `surface` / `answer` / `tags`：投稿内容；`surface` 上限 1000 字，`answer` 上限 3000 字。
- `submitted_by`：投稿人。
- `status`：`pending`、`added`、`ignored`。

#### `rooms`

房间表。当前实现额外存储 `surface` 与 `answer`，以支持自定义题和 AI 生成题不进入题库也可开局。

- `id`：8 位随机房间 ID。
- `puzzle_id`：题库题 ID，可为空。
- `title`：房间标题，可为空；题库房创建时复制 `puzzles.title`，自定义/MCP/AI 生成房可直接保存房间级标题，统一上限 20 字。房间列表和详情优先返回 `rooms.title`，再回退题库 `puzzles.title`。
- `surface`：房间汤面，统一上限 1000 字。
- `answer`：房间汤底，统一上限 3000 字。
- `status`：`waiting`、`playing`、`finished`；当前创建后直接为 `playing`。
- `created_by`：房主。
- `winner_id`：胜者。
- `manual_hint_count`：历史兼容字段；当前手动提示额度不再按房间共享计数，实际按 `game_logs` 中当前 `player_id` 的 `hint_offer` 数量统计，每个玩家每房间上限 3 次。
- `last_hint_at_ask_count`：上次自动提示尝试或成功时的 ask 总数；手动提示不更新该字段，因此不重置自动提示周期。
- `created_at` / `finished_at`。
- 活跃房间由 `cleanup_inactive_rooms` 按最后一条带 `player_id` 的 `game_logs.created_at` 判断；48 小时内有人发言会重置自动结束时间。

#### `game_logs`

游戏日志。

- `type`：`ask`、`guess`、`hint_offer`、`auto_hint`、`hint_accept`、`hint_reject`、`system`。
- `player_id`：玩家日志归属；手动 `hint_offer` 会记录请求者，用于每人 3 次额度统计和限制只能处理自己的提示。自动 `auto_hint` 不绑定玩家。
- `content`：提问、猜测或系统内容。
- `judgment`：`yes`、`no`、`unrelated`、`partial`、`game_over`（揭晓汤底）、`auto_hint`（特殊线索公布行）、`note_notice`（记事本新增记录的公屏系统事件）。对局页日志前缀显示为「是 / 不是 / 不相干 / 是也不是」；`note_notice` 以浅灰小字显示，内容固定为 `【系统提示】记事本有新记录。`，不展开具体记事。
- `hint_text`：提示文本，`hint_offer` / `auto_hint` 使用。
- `resolved`：提示是否已处理；新手动 `hint_offer` 创建时直接为 `1`。MCP 自动提示内容按玩家遮蔽，直到该 MCP 玩家在下一次 `ask` 中带 `auto_hint_log_id` 与 `accept_auto_hint=true`。

#### `room_answer_reveals`

记录某个玩家是否已经主动查看过本房间汤底。查看汤底是个人行为，不结束房间、不广播给其他玩家。

- 主键：`room_id, player_id`。
- 命中后该玩家不能继续 `ask/guess/hint_request`，也不能新增、修改或删除记事板。

#### `room_hint_views`

记录 MCP 玩家对自动提示的查看/拒绝决策，避免自动提示文本直接暴露给 MCP。

- 主键：`log_id, player_id`。
- `accepted=1` 时，后续 MCP `status/logs_since_last_own_action` 可向该玩家返回该条自动提示文本；否则只返回“是否查看”的确认结构。

#### `judge_audit_logs`

裁判私有审计日志，不返回给玩家或普通房间接口。

- `room_id` / `player_id` / `game_log_id`：关联房间、触发玩家和对应公开猜底日志。
- `action`：裁判动作类型；当前写入 `guess`。
- `success` / `score` / `missing_core_count`：后端解析并硬校验后的猜底结果。
- `request_json`：本次裁判 messages JSON；本地单句问句拦截时为空。
- `raw_response`：裁判 LLM 原始响应；本地拦截或未调用 LLM 时为空。
- `parsed_json`：后端解析出的私有结构化结果，包含 `core_checks` 等后台核对细节。
- `error`：裁判格式重试失败或不可用时的错误提示。

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
- 新增记事后会额外写入一条 `game_logs.type='system'`、`judgment='note_notice'` 的公屏系统事件，并通过 SSE `new_log` 广播；编辑/删除记事只广播记事板事件，不写公屏日志。

#### `judge_api_configs`

裁判 LLM 配置。

- `name`：配置名。
- `api_url`：OpenAI-compatible base URL 或 `/chat/completions` URL。
- `api_key`：API Key。管理 API 列表返回时脱敏。
- `model`：模型名。
- `purpose`：用途池，`judge` 仅用于问答/猜底/生成题/扫描，`hint` 仅用于手动/自动提示，`both` 两边都可用。
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
| `hint_trigger_count` | `30` | 距上次自动提示尝试或成功后再 ask 多少条触发自动提示 |
| `ai_cooldown_questions` | `5` | AI 冷却检查最近 N 条提问 |
| `ai_cooldown_seconds` | `3` | AI 冷却窗口秒数 |
| `generate_cooldown_seconds` | `5` | 前端 AI 出题冷却默认值 |
| `judge_prompt` | `你是海龟汤游戏裁判。` | 普通提问/猜底裁判系统提示词；对应调用时实时从表读取 |
| `judge_prompt_hint` | 回退到普通裁判提示词 | 请求提示/自动提示专用系统提示词；`generate_hint` 调用时实时从表读取 |
| `generate_prompt` | JSON 出题提示词 | AI 生成题目的系统提示词；生成调用时实时从表读取 |
| `judge_prompt_clue` | 空 | 线索汤补充提示词；仅当房间 `answer` 含 `【线索公布】` 时拼接到 `judge_prompt` 后；固定公开内容取 `【线索公布】` 与 `【线索公布结束】` 之间 |
| `guest_expire_hours` | `1` | 游客双条件清理小时数 |
| `guest_next_number` | `1` | 下一个网页游客显示编号；按 1..999 循环分配，遇到占用编号跳过 |
| `room_inactive_expire_hours` | `48` | 活跃房间无玩家发言后自动结束小时数 |
| `lobby_finished_visible_hours` | `3` | 已结束房间在大厅继续显示小时数 |
| `finished_room_retention_hours` | `24` | 已结束房间后端继续保留小时数；到期后删除房间、日志和记事 |

### 4.2 cedartoy MBTI/DND 数据库

位置：`/opt/cedartoy/data/sessions.db`

由 `/opt/cedartoy/mbti/handler.py:_init_db()` 和 `/opt/cedartoy/dnd/handler.py:_init_db()` 按需创建。两套测试共用表，通过 `game` 字段区分 `mbti` 与 `dnd`。

MBTI/DND handler 每次工具调用都会先 `_cleanup_expired(conn, now)`：

- 删除 `last_active` 超过 24 小时的进行中 `test_sessions`。
- 删除 `completed_at` 超过 48 小时的 `test_results`。
- 新开测试时统计当前进行中 session；若不存在同 player/game 的旧 session 且活跃 session 已达 `MAX_SESSIONS=500`，拒绝新建。

`player_id` 只允许 1-10 位字母数字。平台账号 `get_profile` 统计 MBTI/DND 时会同时尝试账号数字 id 和用户名：如果 `toy_users.username` 也满足 1-10 位字母数字，就把它也作为历史 `player_id` 查询。

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

### 4.2.1 MBTI/DND 状态机

| 游戏 | 模式 | 题量/提交方式 | 结束条件 | 结果写入 |
| --- | --- | --- | --- | --- |
| MBTI | `short` | 16 题逐题，`a_score` 0-5 | 答完 16 题 | `result_value` 为四字母类型 |
| MBTI | `full` | 完整题库逐题，`a_score` 0-5 | 答完全部题 | `result_detail` 保存模式和计分细节 |
| MBTI | `short_fast` / `full_fast` | `a_scores` 批量提交 | 批量覆盖到结束 | 同上 |
| DND | `full` | 36 题逐题，`answer` 1-4 | 答完 36 题 | `result_value` 为阵营 key |
| DND | `full_fast` | `answers` 批量提交 | 批量覆盖到结束 | `result_detail` 保存模式和分数 |

handler 返回 JSON-RPC 结构。工具级错误会以 MCP tool result 的 `isError` 风格返回给上层；协议级错误才使用 JSON-RPC error。

### 4.3 cedartoy 平台账号表

位置：与海龟汤共用 `/opt/cedartoy/turtle-soup/backend/turtle_soup.db`（`server.py` 通过 `TURTLE_SOUP_DB` 或默认路径连接）。由 `cedartoy/server.py` 读写，**不在** `database.py:init_db()` 中自动创建；表结构变更需手写迁移。

#### `toy_users`

Toy 平台统一账号（与海龟汤 `players` 独立）。

- `username`：2-20 字符，字母/数字/下划线/中文，唯一。
- `password_hash`：`pbkdf2_sha256`（优先 `passlib`，否则内置实现）。
- `is_ai`：AI 账号标记；MCP `account.login_or_register` 只注册 AI 账号并置 `1`，`account.login` 只校验已有账号并保持原值，REST `POST /api/auth/login_or_register` 强制 `0`。
- `is_admin`：管理员标记。
- `deleted_at`：软删除；`NULL` 表示有效。

人类网页登录/注册共用 `_login_or_register(username, password, is_ai=...)`；AI 侧分为仅注册 `_login_or_register_ai` 与重新取 token 的 `_login_existing_account`：

- 用户名规则：2-20 字符，仅字母、数字、下划线、中文。
- 密码至少 6 位。
- 人类 REST 已存在用户必须验密；成功后置 `is_ai=0` 并清空 `deleted_at`。
- MCP `account.login_or_register` 遇到已有用户名会拒绝，不登录；MCP `account.login` 验密后返回 token，不改变 `is_ai` 或 `is_admin`。
- 新用户使用 `pbkdf2_sha256`；若 `passlib` 不可用，`server.py` 内置兼容的 PBKDF2-SHA256 格式。
- 登录成功会更新 `last_active_at` 并清空 `deleted_at`。

#### `binding_tokens`

AI 与人类绑定的一次性码。

- `token`：主键，10 分钟有效。
- `ai_user_id`：生成绑定码的 AI 账号。
- `used`：是否已使用。

绑定流程：

1. AI 账号通过 MCP `account.generate_binding_token` 生成一次性码。
2. 人类网页登录后在首页调用 `POST /api/auth/bind`，提交该一次性码。
3. 服务校验 token 未使用、未过期、不能绑定自己。
4. 写入 `user_bindings`，并把 `binding_tokens.used` 置 1。

#### `user_bindings`

人类与 AI 的多对多绑定关系（一个人类可绑定多个 AI）。

- `human_user_id` / `ai_user_id`：联合唯一。
- 绑定后双方可通过 `/api/auth/me` 互查对方列表。

JWT：由 `TOY_SECRET`（环境变量，默认 `change-me-before-production`）签发 HS256 token。人类 token 30 天过期；AI token 无 `exp`，用于 `POST /{token}` 持久 MCP 连接。

平台账号不会在注册时自动创建海龟汤 `players`。网页进入海龟汤时，前端 `ensureGuestToken()` 发现 `cedartoy_user_id` 后调用 `/soup/api/auth/guest` 创建或复用对应 `players`；MCP 海龟汤收到 path token 时，也会按 `toy_users.id` 创建或复用对应 `players.user_id`。

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
  - 无 body：新建 `is_guest=1` 游客，并在事务内分配 `游客1` 到 `游客999` 的循环显示名；若当前编号仍存在则顺延查找，下一个候选编号记录在 `settings.guest_next_number`。
  - body `{user_id}`：按 `toy_users` 查找/创建对应 `players`（`is_guest=0`），用于首页 `cedartoy_user_id` 与海龟汤 token 打通。
- `POST /register`：注册；若 username 已存在则转登录逻辑。body: `{username, password, source}`。
- `POST /login`：登录。`source=mcp` 会将账号标记为 `is_ai=1`。
- `GET /me`：当前用户公开信息。

JWT payload：`player_id`、`is_admin`、`is_guest`、`exp`。有效期 14 天。

### 5.3 Puzzles

前缀：`/soup/api/puzzles`

- `GET /random`：登录用户随机抽一条 enabled 题，返回 `id/title/surface/tags`。
- `GET /public`：登录用户获取 enabled 题列表，返回 `id/title/surface/tags`，供大厅下拉框选题。
- `POST /submit`：投稿，写入 `puzzle_submissions`。
- `GET /`：管理员题库列表（含 `title`），不返回汤底。
- `GET /{puzzle_id}`：管理员单题详情（含汤底）。
- `POST /`：管理员新增题（`PuzzleBody`，使用统一题目长度上限：`title` 20、`surface` 1000、`answer` 3000、`tags` 100）。
- `PUT /{puzzle_id}`：管理员修改题。
- `PATCH /{puzzle_id}/toggle`：管理员启用/禁用。
- `DELETE /{puzzle_id}`：管理员删除题。

### 5.4 Rooms

前缀：`/soup/api/rooms`

- `GET /`：房间列表，含状态、汤面、房间/题库 `title`、题库 `tags`、提问数、活跃玩家数（presence 统计）、最近活跃时间（`last_active_at`，取房间 presence 最大值）。返回 `waiting`/`playing` 房间，以及 `lobby_finished_visible_hours` 内结束的 `finished` 房间；后端按 `finished_room_retention_hours` 继续保留已结束房。
- `POST /create`：创建房间。
  - `mode=random`：从题库题创建。
  - `mode=custom`：使用 body 中 `title/surface/answer` 创建，并写入投稿；写入前调用 `judge.scan_text`，判定违规时返回 400。
  - 其他 mode：使用 body 中 `title/surface/answer` 创建，不写题库。
  - 自定义创建统一限制：`title` 20 字、`surface` 1000 字、`answer` 3000 字、`tags` 100 字。
  - 每个玩家同时只能创建一个 `waiting/playing` 房间；`is_admin` 或用户名为 `nanshan` 的账号不受单房与 `max_rooms` 限制。
  - 其他用户全局受 `settings.max_rooms` 限制。
- `GET /{room_id}`：房间详情，返回 logs 和 notes；附带房间/题库 `title` 与题库 `tags`；普通响应不含 `answer`。notes 含 `username`，历史空用户名游客缺省名为 `游客{player_id}`。
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
  - 裁判返回 503 时写入一条 `system` 日志 `SYSTEM_BUSY_NOTICE`，响应附带 `system_error=true`；前端保留输入框内容，便于玩家重试。裁判格式失败时写入 `content_override = 【系统提示】系统开小差了，请再次提问`，并记为 `unrelated`；前端会把旧日志中的“裁判开小差了”也归一显示成 `【系统提示】...`，不挂在用户名下。
  - 只有当房间 `answer` 含 `【线索公布】` 标记时，才允许解析模型回复中的 `【线索公布】` 行为 `clue`；否则忽略这类行，避免普通汤被模型主动加线索。
  - 若触发 `clue`，实际公布内容优先从房间汤底的 `【线索公布】...【线索公布结束】` 包裹段读取，兼容旧格式时最多读到下一段以 `【` 开头的内容；额外写入 `type='auto_hint'`、`judgment='auto_hint'`、触发玩家 `player_id` 并广播 `new_log`（前端绿色横幅标题「【特殊线索】」）。同一房间同一玩家触发过同一条特殊线索后不会重复广播。
  - 更新 `players.ask_count` 和对应分项；广播 SSE `new_log`。
  - 当 ask 总数 `>= last_hint_at_ask_count + hint_trigger_count` 且无未处理 `hint_offer` 时，后台异步尝试 `generate_hint(surface, answer, logs)`，写入无 `judgment='auto_hint'` 的 `auto_hint` 普通提示日志，更新 `last_hint_at_ask_count`，广播 `new_log`；前端对普通自动提示显示“是否查看”的接受/拒绝按钮，选择仅保存在本地，特殊线索不需要确认；自动提示失败不阻塞本次提问，也会更新 `last_hint_at_ask_count` 以避免每条新提问都重复撞失败。
- `POST /guess`：猜汤底。body: `{room_id, content}`。
  - 内容经过 `clean_content`，限制 1000 字，拒绝 `< > { }`；超长返回 400，提示 `内容不能超过 1000 字`。
  - 调用 `judge_guess(surface, answer, guess)`：若玩家内容明显只是单句是非问句或局部确认，后端本地直接判未通关、`score=0`，不调用裁判 LLM。
  - 完整猜底会从汤底 `【通关判定条件】` 段提取编号条件，要求裁判 LLM 返回严格 JSON：`success`、`score`、`missing_core_count`、`core_checks`、`public_answer`。`core_checks` 与原始响应只写 `judge_audit_logs`，不进入公开日志。
  - 后端按 `missing_core_count` 做硬区间校验：`>=3` 条遗漏只能 `0-39`，`2` 条遗漏只能 `40-59`，`1` 条遗漏只能 `60-79`，`0` 条遗漏只能 `80-100` 且可通关；裁判分数落在对应区间内则保留，落错区间则改为该区间边界分。
  - 裁判 JSON 格式失败时，`judge_guess` 在私有猜底锁内最多重试 3 次；仍失败才写公开 `system` 日志并返回 `system_error=true`。
  - 每次猜底都会写私有 `judge_audit_logs`，用于后台排查裁判请求、原始响应和解析结果；玩家只能看到公开 `game_logs` 中的猜测原文与「还原度：N%」或系统提示。
  - 猜中时：房间 `finished`，写 `winner_id/finished_at`，胜者 `win_count+1`，所有有 ask/guess 记录的玩家 `game_count+1`。
  - 猜中后额外写一条 `type=system, judgment=game_over` 的揭晓日志；其 `content` 为 `还原度：N%\n{汤底}`，其中汤底优先使用裁判 JSON 的 `public_answer`，否则回退房间 `answer` 去掉 `【隐藏后台设定】` 后的公开段。
  - 广播顺序：猜测日志 `new_log` -> 揭晓日志 `new_log` -> `game_over` 事件。`game_over.answer` 仍只包含纯汤底，不含还原度，供前端设置结束状态。
  - 猜错时：广播 `new_log`；响应附带 `score`。
- `POST /hint/request`：手动请求提示。body: `{room_id}`。
  - 按当前 `player_id` 统计该房间 `hint_offer` 数量；每个玩家每房间最多 3 次，用尽返回 400。
  - 若当前玩家仍有历史未处理 `hint_offer`，先返回 400，不调用 LLM；新生成的手动提示会直接置为 `resolved=1`，因此不会再产生接受/拒绝待处理框。
  - 同一个房间内手动提示和自动提示共享 `_hint_locks[room_id]`，锁内重新检查未处理提示和额度，并串行调用 `generate_hint`，避免并发请求同时打到裁判 LLM。
  - `generate_hint` 使用 `purpose IN ('hint','both')` 的提示 LLM 池，提示格式失败最多重试 5 次；生成失败返回 503；成功则写入带 `player_id`、`resolved=1` 的 `hint_offer` 并广播 `hint_offer`（前端「【请求提示】」横幅直接显示提示内容，请求前仍二次确认）。手动提示不更新 `last_hint_at_ask_count`，不影响自动提示触发周期。
- `POST /hint/respond`：兼容旧手动提示记录。body: `{room_id, log_id, accept}`。
  - 新手动请求提示已经直接显示，不需要调用该接口；网页自动提示的接受/拒绝是本地选择，不调用后端。
  - 对历史未处理 `hint_offer`，仍只能处理自己的提示；其他玩家的提示返回 403，已 resolved 返回 409。
- `POST /generate`：调用 LLM 生成 `{title, surface, answer}`，只返回预览，不写库。请求 body 可带 `style`，支持 `cozy/absurd/mystery/fantasy/history/scifi/horror`；后端将风格说明替换进 `generate_prompt` 的 `{style_description}` 占位符，未知或缺省风格回退 `horror`。

### 5.6 Leaderboard

前缀：`/soup/api/leaderboard`

- `GET /{metric}`：排行榜。
  - `metric=games` -> `game_count`
  - `metric=wins` -> `win_count`
  - `metric=asks` -> `ask_count`
  - `metric=yes` -> `ask_count_y`
  - `metric=no` -> `ask_count_n`
  - 其他值回退到 `game_count`
  - 只返回 `is_guest=0` 且当前 metric 分数大于 0 的玩家；游客不进排行榜。

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
- `PATCH /players/{player_id}/admin?enabled=0|1`：设置管理员，并同步绑定的 `toy_users.is_admin`。
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

### 5.12 cedartoy 平台账号 Admin

前缀：`/api/admin`（由 `cedartoy/server.py` 直接处理，不经 turtle-soup）。均需 Bearer `cedartoy_token` 且 `toy_users.is_admin=1`。

- `GET /users`：列出 Toy 平台账号，含 `is_ai/is_admin/deleted_at`、关联海龟汤玩家数、绑定数量、未使用绑定码数量。
- `PUT /users/{user_id}`：更新用户名、`is_ai`、`is_admin` 与软删除状态；会同步同 `user_id` 的海龟汤 `players.username/is_ai/is_admin`。不能取消当前登录管理员的管理员权限或软删当前账号。
- `POST /users/{user_id}/reset-password`：重置 Toy 平台账号密码，并清空 `deleted_at`。
- `PATCH /users/{user_id}`：释放账号，删除 Toy 平台账号、绑定关系、绑定码，并将对应海龟汤 `players.user_id` 置空；不能释放当前登录管理员。

`GET /admin` 返回根目录 `admin.html`。Toy 首页管理员入口有两种：小游戏列表中管理员专属「总管理」卡片（`adminOnly`，链接 `/admin`），以及移动端登录区的「总管理」短入口；非管理员不渲染这些入口。移动端登录区保留用户名在动作区左侧、登出图标在最右侧的顺序。

### 5.13 cedartoy HTTP 路由

`cedartoy` 自身 HTTP 路由：

- `GET /`：返回 Toy 首页 `index.html`（游戏入口、排行榜、登录/绑定 UI）。
- `GET /admin`：返回 Toy 平台账号管理页 `admin.html`。
- `GET /health`：返回 `{"ok": true, "service": "cedartoy", "endpoints": [...]}`。
- `POST /`：根 MCP 聚合入口，实现 `initialize`、`tools/list`、`tools/call`。
- `POST /{token}`：与 `POST /` 相同 MCP handler；URL path 中的 token 作为 AI 持久登录凭证（`generate_binding_token` 流程外的另一种方式：登录后直接改 MCP 地址为 `https://toy.cedarstar.org/{token}`）。
- `POST /mbti`：MBTI JSON-RPC MCP server。
- `POST /dnd`：DND JSON-RPC MCP server。
- `GET /mbti`：MBTI HTTP GET 入口。通过 `action` 参数区分操作（`mbti_start`、`mbti_answer`、`mbti_get_result`），参数通过 query string 传入。响应自动附带 `next_urls`（含 `step` 和 `_r` 缓存busting 参数）。
- `GET /dnd`：DND HTTP GET 入口。结构同 MBTI，action 为 `dnd_start`、`dnd_answer`、`dnd_get_result`。
- `GET/POST/PUT/PATCH/DELETE/OPTIONS /soup*`：反代到 `127.0.0.1:8012`。SSE（`/soup/api/sse/`）使用 HTTP/1.1 流式转发、`Cache-Control: no-cache`、`X-Accel-Buffering: no`，避免 Cloudflare Tunnel 路径下缓冲导致事件延迟。
- `GET/POST/PUT/PATCH/DELETE/OPTIONS /mcp*`：legacy 反代到 `127.0.0.1:8012`。`turtle-soup` 当前只保留海龟汤自己的 `/mcp/play`；聚合工具不再走该路径。

`cedartoy` 反代细节：

- 过滤 hop-by-hop headers：`connection`、`keep-alive`、`proxy-authenticate`、`proxy-authorization`、`te`、`trailers`、`transfer-encoding`、`upgrade`。
- 转发时保留原始 path/query/body，并补充 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto` 等常规代理头。
- 普通代理请求超时返回 `502 {"error":"proxy error","detail":...}`。
- SSE 路径会以流式方式转发响应，避免一次性读完整 body 导致事件阻塞。

`server.py` 请求处理顺序：

1. `POST` 若是 `/soup*` 或 legacy `/mcp*`，先反代到 `turtle-soup`。
2. `POST /api/auth/login_or_register`、`POST /api/auth/bind` 由平台账号逻辑处理。
3. `POST /`、`POST /{token}`、`POST /mbti`、`POST /dnd` 按 MCP/JSON-RPC 处理。
4. `GET /` 返回 Toy 首页；`GET /api/auth/me` 返回平台账号；`GET /mbti`、`GET /dnd` 是浏览器 GET 版测试入口；`GET /soup*` 反代。

## 6. MCP 层

### 6.1 入口

MCP 聚合层由 `/opt/cedartoy/server.py` 的根路径 `POST /` 提供：

- `tools/list` 暴露 `list_games`、`get_guide`、`play`、`account` 四个工具。
- `tools/call name=list_games`：在 `server.py` 返回硬编码游戏列表。
- `tools/call name=get_guide`：`turtle_soup` 返回硬编码 action 字典和提示 notes；`account`、`mbti`、`dnd` 读取 `/opt/cedartoy/turtle-soup/backend/guides/*.md`。
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

根 MCP 协议行为：

- `initialize` 返回 `protocolVersion="2024-11-05"`，`serverInfo.name="cedartoy"`，capabilities 只声明 `tools`。
- `tools/list` 只暴露 `list_games`、`get_guide`、`play`、`account` 四个根工具。
- `tools/call` 成功时返回 MCP content text；业务错误不会抛 JSON-RPC error，而是返回 `isError: true` 且文本以 `【cedartoy】` 开头。
- 未知 JSON-RPC method 返回 JSON-RPC error `-32601`。
- `POST /{token}` 与 `POST /` 共用 handler；path token 会传给 `account` 工具，也会在 `play(game="turtle_soup", ...)` 时转发到海龟汤 `/mcp/play`，用于持久 AI 身份。

### 6.2 `list_games`

返回：

```json
{
  "测试": [
    {"name": "mbti", "display": "MBTI", "desc": "16型人格测试，4种模式可选（短/完整/快速）"},
    {"name": "dnd", "display": "DND阵营测试", "desc": "测试你的D&D道德阵营，守序善良还是混乱邪恶？"}
  ],
  "小游戏": [
    {"name": "turtle_soup", "display": "海龟汤", "desc": "横向思维推理游戏，题库抽取大多微恐"}
  ],
  "提示": "用 get_guide(game) 查看具体玩法，再用 play(game, action, ...) 执行操作"
}
```

### 6.3 `get_guide`

- `game=turtle_soup`：返回硬编码 action 字典和 notes（含 `list_puzzles` 选题、`create_random(puzzle_id)` 指定题/随机题说明、自动提示与 100 题查看汤底提示均通过下一次 `ask` 顺便带参处理、`note_add/note_edit/note_delete` 记事本说明，以及“海龟汤房间是对局公屏，不是群聊”的 MCP 使用提醒）。
- `game=account`：读取 `/opt/cedartoy/turtle-soup/backend/guides/account.md` 并返回 `{game, guide}`。
- `game=mbti`：读取 `/opt/cedartoy/turtle-soup/backend/guides/mbti.md` 并返回 `{game, guide}`。
- `game=dnd`：读取 `/opt/cedartoy/turtle-soup/backend/guides/dnd.md` 并返回 `{game, guide}`。

### 6.4 `play(game="turtle_soup", ...)`

请求 body 基础字段：

```json
{
  "game": "turtle_soup",
  "action": "...",
  "path_token": "...",
  "room_id": "...",
  "content": "...",
  "title": "...",
  "surface": "...",
  "answer": "...",
  "tags": "...",
  "style": "...",
  "note_id": 1,
  "log_limit": 20,
  "auto_hint_log_id": 123,
  "accept_auto_hint": true,
  "confirm_reveal": true
}
```

action 列表：

| action | 参数 | 行为 |
| --- | --- | --- |
| `list_rooms` | 无 | 返回 `waiting/playing` 房间，以及结束 3 小时内的 `finished` 房间；字段 `id/title/surface/status/created_at/last_active_at` |
| `list_puzzles` | 无 | 返回 enabled 题库目录，字段 `id/title/tags`；不返回汤面和汤底，避免一次性把完整题库喂给 MCP AI |
| `get_puzzle` | `puzzle_id` | 返回单题公开信息，字段 `id/title/surface/tags`，不返回汤底 |
| `status` | `room_id`, `log_limit?` | 返回房间公开状态与日志；`log_limit` 取最新 N 条后按时间正序返回；日志含 `username/is_guest/is_ai` 和记事本 `note_notice` 系统事件；不返回房间表的 `answer` 和记事本具体内容。自动提示日志在 MCP 端默认遮蔽 `hint_text/content`，只返回 `next_ask_confirm_parameters` / `next_ask_reject_parameters`；该玩家接受后才返回提示文本。若房间已结束，公开日志中的 `game_over` 揭晓行会包含最终汤底 |
| `register` | `username`, `password` | 仅注册 Toy AI 账号，返回 path token；让人类把 MCP 地址改为 `https://toy.cedarstar.org/{token}` 后获得持久身份 |
| `create_random` | `puzzle_id?`, `path_token?` | 以 MCP 玩家身份创建题库房间；传 `puzzle_id` 时指定题，不传则随机；题库抽取大多微恐 |
| `create_custom` | `title?`, `surface`, `answer`, `tags?`, `path_token?` | 创建自定义题房间，复用人类端 `create_room(mode="custom")`，走 `scan_text` 审核并写入投稿表；`title` 保存为房间标题；长度限制为 `title` 20、`surface` 1000、`answer` 3000、`tags` 100 |
| `generate` | `style?` | 调用 `/game/generate` 返回 `title/surface/answer` 预览，不写库、不开房；生成结果按 `title` 20、`surface` 1000、`answer` 3000 截断；`style` 支持 `cozy/absurd/mystery/fantasy/history/scifi/horror`，缺省走后端默认风格；满意后再用 `create_custom` |
| `close_room` | `room_id`, `path_token?` | 复用 `/rooms/{room_id}/close`，只允许房主或管理员关闭 |
| `join` | `room_id` | 查询并返回房间公开信息，含 `title/surface/status/created_at` |
| `ask` | `room_id`, `content?`, `path_token?`, `auto_hint_log_id?`, `accept_auto_hint?`, `confirm_reveal?` | 调用海龟汤 `ask` 逻辑，`content` 最多 200 字，受 AI 冷却限制；响应保留本次 ask 结果字段，并追加 `room` 与 `logs_since_last_own_action`：从该 MCP 玩家上一次公开动作之后到本次 ask 完成之间的全部公开日志，不包含上次自己的那条。若自动提示确认已出现，下一次 `ask` 可同时传 `auto_hint_log_id` 和 `accept_auto_hint=true/false` 查看或拒绝该提示；若 100 题查看汤底提示已出现，下一次 `ask` 顺便传 `confirm_reveal=true` 会接受提示、直接返回汤底并锁定当前玩家，本次不会再判题且不要求 `content`；若房间已结束或当前玩家已查看汤底，返回错误 |
| `guess` | `room_id`, `content`, `path_token?` | 调用海龟汤 `guess` 逻辑，`content` 最多 1000 字，应提交完整汤底还原而不是是/否问题；超长返回明确错误；若房间已结束，返回带 `status` 查看指引的错误 |
| `hint_request` | `room_id`, `path_token?` | 主动请求一次提示并直接返回/显示提示内容，每个玩家每房间最多 3 次；同房间提示生成串行调用提示池 LLM，最多 5 次格式重试；手动提示不重置自动提示周期；若房间已结束，返回带 `status` 查看指引的错误 |
| `note_list` | `room_id` | 返回该房间所有记事 |
| `note_add` | `room_id`, `content`, `path_token?` | 新增自己的记事，最多 50 字；同时写入一条不含记事内容的系统公屏日志 `【系统提示】记事本有新记录。`，随 `status/logs_since_last_own_action` 返回 |
| `note_edit` | `note_id`, `content`, `path_token?` | 修改自己的记事，最多 50 字 |
| `note_delete` | `note_id`, `path_token?` | 删除自己的记事 |

身份规则：

- 传 `path_token`：解析 Toy 平台账号，按 `toy_users.id` 创建或复用 `players.user_id`，并将 `username/is_ai/is_admin/source` 同步到海龟汤玩家记录。
- 不传 `path_token`：创建 `is_guest=1, is_ai=1, source='mcp'` 的游客 AI 玩家；这类身份不持久。

海龟汤 MCP 返回的是 `turtle-soup/backend/mcp_app.py` 的普通 JSON，不是 MCP content envelope；根 `server.py` 会再把该 JSON stringify 成 MCP text content，并在海龟汤后端返回 4xx 时透传 JSON 中的 `detail/error` 为 MCP 工具错误文本，避免调用方只看到 HTTP 400。`join` 不会返回汤底；`status` 不直接返回房间表 `answer`，但会返回公开对局日志的玩家名、`note_notice` 系统事件和结束后的 `game_over` 揭晓行。MCP 自动提示不直接暴露提示文本，日志会给出 `next_ask_confirm_parameters` / `next_ask_reject_parameters`，要求 AI 在下一次 `ask` 中带 `auto_hint_log_id` 和 `accept_auto_hint` 处理；100 题查看汤底提示也只给出 `next_ask_confirm_parameters: {confirm_reveal: true}`，要求下一次 `ask` 顺便带参接受。`ask` 也会在本次提问结果外追加 `logs_since_last_own_action`，避免 AI 与人类同房时看不见自己两次动作之间别人产生的问答或系统事件；`guess` 猜中后的 `game_over` 会让网页侧收到汤底，也会写入公开日志；`note_list` 独立于 `status`，用于读取记事本具体内容。

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

MBTI 返回语义：

- `mbti_start` 若已有同 `player_id` 进行中 session，会用 `ON CONFLICT` 重置为新模式、新进度和空答案。
- 普通模式每次 `mbti_answer` 更新一题并返回下一题；完成时删除进行中 session，写入 `test_results`。
- 快速模式 `mbti_answer_batch` 接受最多 16 个分数，适合一次性提交短测；不足完成题量时会返回下一批题。

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

DND 返回语义和 MBTI 类似：逐题模式返回下一题或最终结果；快速模式批量接收答案，完成后写入 `test_results` 并删除进行中 session。

### 6.7 `account`

平台统一账号工具（存档用；不登录也可玩，但游客数据 1 小时后清理）。

| action | 参数 | 行为 |
| --- | --- | --- |
| `login_or_register` | `username`, `password` | 仅注册 AI 账号，返回 `{token, user, message}`；若用户名已存在会拒绝，避免误覆盖已有账号 |
| `login` | `username`, `password` | 已有账号重新获取 token；AI 和人类账号都可用，不改变 `is_ai/is_admin` |
| `generate_binding_token` | `token`（可选，或走 `POST /{token}` path token） | AI 账号生成 10 分钟绑定码，人类在首页输入完成绑定 |
| `get_bindings` | `token`（可选，或 path token） | AI 账号查看绑定自己的人类列表（`username`、`bound_at`） |
| `get_profile` | `token`（可选，或 path token） | 账号信息 + 绑定列表 + 游戏概览（海龟汤按 `players.username` 匹配 `game_count`/`win_count`；MBTI/DND 按 `SESSIONS_DB` 中 `test_results`，`player_id` 为账号 id 或 1–10 位字母数字用户名） |

人类网页登录：`https://toy.cedarstar.org` 右上角。AI 持久 MCP：注册或登录拿到 token 后，让人类把 MCP 地址改为 `https://toy.cedarstar.org/{token}`。

账号工具的边界：

- `account.login_or_register` 永远按 AI 账号注册，返回的 token 不带 `exp`；已有账号必须用 `account.login` 重新取 token。
- REST `/api/auth/login_or_register` 永远按人类账号处理，返回的 token 带 30 天 `exp`。
- 绑定关系只影响资料页和跨账号查看，不会自动让 AI 继承人类海龟汤房间权限，也不会改变 MBTI/DND 的 `player_id` 规则。
- 海龟汤管理后台设置 `players.is_admin` 时会同步绑定的 `toy_users.is_admin`，否则 `/auth/guest {user_id}` 会在下次登录时用 Toy 账号权限覆盖玩家权限。

## 7. 裁判 LLM 与 Round Robin 容错

实现文件：`turtle-soup/backend/judge.py`

配置来源：`judge_api_configs` 表，仅使用 `enabled=1` 的行，按 `priority ASC, id ASC` 排序（排序决定轮转列表顺序，非固定主备）。`purpose` 将同一张配置表拆成两个运行池：`judge` 池服务普通提问、猜底、生成题和内容扫描；`hint` 池服务手动/自动提示；`both` 同时进入两个池。

运行时状态：

```python
fail_counts: dict[str, dict[int, int]] = {"judge": {}, "hint": {}}
_rr_index: dict[str, int] = {"judge": 0, "hint": 0}
_rr_locks: dict[str, asyncio.Lock] = {"judge": asyncio.Lock(), "hint": asyncio.Lock()}
_config_locks: dict[str, asyncio.Lock] = {}
FAIL_LIMIT = 5
```

请求流程：

1. `_configs(pool)` 读取启用配置，按 `purpose in {pool, "both"}` 过滤，并过滤掉该池内 `fail_counts[pool][id] >= 5` 的配置。
2. `_chat(..., pool="judge")` 进入时用对应池的 `_rr_locks[pool]` 预占 `_rr_index[pool] % len(available)` 作为起点，并立即推进该池下标；`generate_hint` 显式传 `pool="hint"`。
3. 每个配置调用前按物理目标 `api_url + api_key + model` 获取 `_config_locks`，避免同一个 key/model 被问答池与提示池或并发房间同时撞上。
4. 每个配置调用 OpenAI-compatible `/chat/completions`：
   - 若 `api_url` 已以 `/chat/completions` 结尾，直接使用。
   - 否则拼接 `{api_url.rstrip("/")}/chat/completions`。
5. 当前配置请求成功：`fail_counts[pool][id]=0`，返回文本。
6. 当前配置异常：`fail_counts[pool][id]+=1`，立即尝试列表中下一配置；若全部失败，会记录包含各配置异常摘要的 warning 日志。
7. 全部失败或无可用配置：返回 HTTP 503，`裁判暂时不可用，请稍后再试`。

管理探测：`test_config(cfg)` 对单条配置发简短 chat 请求；`list_models(cfg)` 拉取 `/models`。均不修改运行池的 `fail_counts` 与 `_rr_index`。

生产 API 池现状应通过管理后台维护：只有 `enabled=1` 的配置参与轮询，`purpose` 可选「问答 / 提示 / 两边」。格式或语义不稳定的配置即使连通也会影响对应池；`judge_ask` 和 `generate_hint` 会通过 `_chat_validated` 按各自重试次数重新调用 `_chat()`，`judge_guess` 在独立猜底锁内要求 JSON 并最多重试 3 次，格式失败会记录 warning 日志。

系统 prompt 来源：

1. `judge_prompt`：每次 `judge_ask` 调用时实时读取 `settings.judge_prompt` 非空值；否则回退 `config/judge_prompt.txt`；再否则用 `DEFAULT_SETTINGS["judge_prompt"]`。
2. `generate_prompt`：每次 `generate_puzzle` 调用时实时读取 `settings.generate_prompt` 非空值；否则回退 `DEFAULT_SETTINGS["generate_prompt"]`。
3. `judge_prompt_hint`：每次 `generate_hint` 调用时实时读取 `settings.judge_prompt_hint` 非空值；否则回退 `config/judge_prompt.txt`；再否则用 `DEFAULT_SETTINGS["judge_prompt"]`。
4. `judge_prompt_guess`：每次 `judge_guess` 调用时实时读取 `settings.judge_prompt_guess` 非空值；否则回退 `config/judge_prompt.txt`；再否则用 `DEFAULT_SETTINGS["judge_prompt"]`。
5. `judge_prompt_clue`：线索汤提问判定时实时读取 `settings.judge_prompt_clue`，缺失时为空字符串。
6. 管理后台保存或删除上述设置后，下一次对应裁判/生成调用立即读到新值，无需重启服务。

线索汤约定：

- 创作者可在汤底中用 `【线索公布】` 与 `【线索公布结束】` 包住中途应公开的线索；大厅自定义创建框和临时加题页会提示该写法。
- `judge_ask` 只有在房间汤底包含 `【线索公布】` 时才拼接 `judge_prompt_clue`，并允许模型输出 `【线索公布】` 作为触发信号；触发后系统优先公布汤底两个标记之间的固定文本。
- 触发后的特殊线索落库为 `game_logs.type='auto_hint', judgment='auto_hint'`，并记录触发玩家 `player_id`；同一房间同一玩家触发过同一条特殊线索后不会重复广播。前端显示为无按钮绿色「【特殊线索】」横幅；ask 数量阈值触发的普通提示同样使用 `type='auto_hint'`，但不写 `judgment='auto_hint'`，前端显示为无按钮粉色「【提示】」横幅。

裁判函数：

- `judge_ask(surface, answer, question) -> {judgment, clue?, content_override?}`
  - 实时读取 `judge_prompt`；线索汤额外读取并拼接 `judge_prompt_clue`。
  - 每次请求追加类型约束系统消息，明确这是“普通提问判定”；首行合法值为「是 / 不是 / 无关 / 是也不是」，并兼容「不相关 / 没有关联」。
  - `_chat_validated` 最多重试 3 次；仍失败则 `unrelated` + `content_override=【系统提示】系统开小差了，请再次提问`。
- `judge_guess(surface, answer, guess) -> {success, score, answer?, error?}`
  - 实时读取 `judge_prompt_guess`，并追加类型约束系统消息，明确这是“猜测汤底，不是普通提问”。
  - 单句问句或局部确认会被本地拦截为未通关、`score=0`，避免把普通提问误走猜底通关流程。
  - 完整猜底要求裁判返回 JSON；`_parse_guess_json` 会剥离外围 code fence、解析 `success/score/missing_core_count/core_checks/public_answer`，并按遗漏核心条件数强制套用 `0-39 / 40-59 / 60-79 / 80-100` 四档分数区间。
  - `judge_guess` 使用独立 `_guess_lock` 串行化猜底裁判调用，避免同类猜底重试互相打架；格式失败最多重试 3 次，仍失败则 `error=【系统提示】系统开小差了，请再次提问`。
  - 公开揭晓汤底优先用 `public_answer`；缺失时 `public_answer_from_full_answer()` 会剥离 `【隐藏后台设定】` 后的内容，避免后台设定进入玩家公屏。
- `generate_hint(surface, answer, game_log) -> str | None`
  - 实时读取 `judge_prompt_hint`，但不读取或拼接 `judge_prompt_clue`；随后追加“用户申请提示”系统消息，明确不要执行线索汤专用特殊规则、不要输出 `【线索公布】`、不要泄露完整汤底。
  - 使用最近最多 40 条 ask 记录和最近 20 条玩家 ask/guess 发言；追加系统消息要求回复以 `【提示】` 开头、总字数不超过 30 字且无标点，且不要空回复；后端校验提示长度为 7-120 字且不能以常见虚词/介词结尾，避免保存半句话；最多 5 次格式重试，检测到空回或格式错误时会追加修复提示词再重新生成；单次 LLM 调用使用较短 timeout 与 `max_tokens=256`；失败返回 `None`。
  - 手动提示与自动提示在 `routers/game.py` 中额外受房间级 `_hint_locks[room_id]` 串行保护；锁内会重新检查手动提示额度和未处理提示，避免同房间并发请求同时打到裁判 LLM。
- `generate_puzzle(style="horror") -> {title, surface, answer}`
  - 实时读取 `generate_prompt`；将 `STYLE_DESCRIPTIONS[style]` 通过 `.replace("{style_description}", style_desc)` 注入 prompt，并额外要求返回含 `title/surface/answer` 的 JSON。生成结果按 `title` 20、`surface` 1000、`answer` 3000 截断。这里刻意不用 `.format()`，避免 prompt 中 JSON 大括号触发格式化错误。
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

当前只有海龟汤 FastAPI 进程启动 APScheduler；`cedartoy` 的 MBTI/DND 清理不是 scheduler，而是在 handler 调用时顺手清理。

### `cleanup_inactive_rooms`

调度：每 1 小时。

清理条件：`waiting/playing` 房间最后一条带 `player_id` 的 `game_logs.created_at` 早于 `room_inactive_expire_hours`（默认 48 小时）；若房间没有玩家日志，则回退用 `rooms.created_at`。

动作：

- 将房间标记为 `finished` 并写入 `finished_at`。

### `cleanup_guests`

调度：每 1 小时。

清理条件：

```sql
is_guest = 1
created_at < now - guest_expire_hours
last_active_at < now - guest_expire_hours
AND 不存在 room_inactive_expire_hours 内仍有玩家日志的 active 自建房间
AND 不存在 room_inactive_expire_hours 内该游客自己的玩家日志
```

动作：

- 删除该游客创建且最后一条带玩家日志早于 `room_inactive_expire_hours` 的房间。
- 将该游客在保留房间中的 `room_notes.player_id` 与 `game_logs.player_id` 置空，再删除游客账号。

### `cleanup_finished_rooms`

调度：每 10 分钟。

清理条件：`finished` 房间的 `finished_at` 早于 `finished_room_retention_hours`（默认 24 小时）；若 `finished_at` 为空，则回退用 `rooms.created_at`。

动作：

- 清理举报引用。
- 删除该房间的记事、日志和房间记录。

注意：清理游客不再删除其保留房间内的历史发言，只将 `player_id` 置空；注册/平台账号用户不受该任务影响。

### `scan_recent_content`

调度：每天 03:00。

扫描对象：

- 最近 2 天注册、未被同 type/ref_id 标记过的 username。
- `puzzle_submissions.status='pending'` 且未被同 type/ref_id 标记过的投稿。

动作：

- 调用 `judge.scan_text`。
- 可疑时写入 `flagged_content`，不自动封禁或删除。
- 异常只记录 warning，不中断服务。

扫描依赖裁判 API 池；若所有 LLM 配置不可用，本任务只会跳过或记录异常，不影响玩家登录、房间列表和静态前端。

## 8.1 数据生命周期

| 数据 | 创建 | 清理/结束 | 备注 |
| --- | --- | --- | --- |
| 海龟汤游客 `players` | 网页 `/auth/guest` 无 `user_id` 时分配 `游客1..游客999` 循环名；MCP 无用户名时创建临时 AI 游客 | `cleanup_guests` 双条件 1 小时默认，且无 48 小时内玩家日志/自建房间 | 清理时保留房间日志文本，仅将游客 `player_id` 置空；网页游客编号由 `settings.guest_next_number` 继续轮转，遇到占用号跳过 |
| 海龟汤注册/平台玩家 | `/auth/register`、`/auth/login`、`/auth/guest {user_id}` | 无自动删除 | 管理后台可删玩家 |
| 海龟汤房间 | `/rooms/create` 或 MCP `create_random` | 猜中、房主关闭、管理员 finish、48 小时无玩家发言自动结束、已结束房大厅显示 3 小时、结束后 24 小时删除 | 普通 API 不暴露 `answer` |
| 海龟汤 presence | SSE connect / `touch_room` | SSE disconnect 或 1 小时 stale 清理 | 房间列表在线人数来源 |
| MBTI/DND 进行中 session | `*_start` | 完成或 24 小时无活动 | 每次 handler 调用顺手清理 |
| MBTI/DND 结果 | 测试完成 | 48 小时后 handler 调用顺手清理 | `get_profile` 只统计未清理结果 |
| 绑定 token | MCP AI 生成 | 使用后 `used=1` 或 10 分钟过期 | 过期 token 不一定立即删除 |

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
supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy turtle-soup
supervisorctl -c /etc/supervisor/supervisord.conf restart turtle-soup
supervisorctl -c /etc/supervisor/supervisord.conf restart cedartoy
supervisorctl -c /etc/supervisor/supervisord.conf reread
supervisorctl -c /etc/supervisor/supervisord.conf update
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
| `/soup/` | `Lobby` | 像素风大厅：房间列表显示等待/进行中房间，以及结束 3 小时内的已结束房；已结束房后端保留 24 小时；汤名搜索与标签筛选、创建房间防重复提交；题库下拉框可选题，也可随机抽题；AI 生成页签有「风格（可选）」下拉，仅显示用户可理解的风格名，生成中禁用生成/创建按钮但不再使用 5 秒倒计时，生成预览分开显示 `title` 与 `surface`；排行榜排除游客，移动端排行榜分类为横向滑块；底部导航含「大厅」入口，可从排行榜/我的回到房间列表；自定义汤底输入框空白时提示 `【线索公布】...【线索公布结束】` 包裹写法；移动端顶栏保留头像/管理员入口，并以图标按钮显示登出 |
| `/soup/room/:id` | `Room` | 对局页：SSE 订阅、`presence.enter_room`；判定前缀中文展示；开小差类日志归一为 `【系统提示】...`；记事本新增时会收到 `judgment='note_notice'` 的系统公屏提示 `【系统提示】记事本有新记录。`，前端以浅灰小字显示；猜测汤底显示为独立玩家猜测卡片与裁判判定卡片；`type='auto_hint'` 且 `judgment='auto_hint'`→绿色「【特殊线索】」，无该 judgment 的 `auto_hint`→粉色「【提示】」且由玩家本地接受/拒绝是否查看；`hint_offer`→粉色「【请求提示】」并直接显示提示内容（每个玩家每房间 3 次，请求前确认）；`game_over`→「汤底揭晓」且还原度拆成独立标记；记事本侧栏/抽屉；若 ask/guess 返回 `system_error` 则保留输入框内容；移动端顶栏保留房间状态并以图标按钮显示登出 |
| `/soup/profile` | `Profile` | 登录用户资料 |
| `/soup/admin` | `Admin` | 管理后台（需 `is_admin`）；裁判 API 弹窗支持「测试」「拉取模型」；settings `value` 用 `textarea`，含 `judge_prompt_clue` 说明 |
| `/soup/add-puzzle` | `AddPuzzle` | 管理员加题 |
| `/soup/login` | — | 重定向到 `/soup/`（已移除独立登录页） |

大厅登录：游客点击右上角头像弹出与 Toy 首页同款的 `LoginModal`，调用公网 `POST /api/auth/login_or_register` 后写入 `cedartoy_token`/`cedartoy_user_id`，再 `POST /soup/api/auth/guest` 换取海龟汤 JWT。已登录用户点头像打开「我的」抽屉，可查看/解绑 AI 账号、进入管理后台或登出；移动端底部「我的」同样打开该抽屉。

房间卡片展示房间级 `title`（无则题库 `title`，再无则汤面首句）、汤面两行省略、标签与「提问/在房」统计；列表默认按最近活跃时间排序，0 提问房间排倒数第二、已结束房间排最后（「按活跃排序」按钮重新拉取列表，移动端另有紧凑刷新按钮）；点击已结束房间会弹出剧透确认弹窗，确认后才进入。移动端锁高 `100dvh`、仅中间 `.lobby-main` 内滚动；卡片整卡可点进入；创建用 FAB + 固定高度抽屉，「创建」按钮贴底可见。

Room 移动端规则：顶部栏只保留房间状态（隐藏「游戏大厅」标题，避免与房间号/状态重叠），右侧动作区不改变原顺序，登出显示为无文字图标按钮；房主/管理员的关闭房间入口在移动端放到汤面标题条内，位于折叠按钮左侧，避免挤占顶部房间号；汤面区高度压缩并可一键折叠，折叠按钮紧凑最右对齐，右侧滚动条常显且加宽加深；底部提问/猜测汤底输入区、请求提示按钮与发送按钮压缩高度。侦探日志标题条在移动端只保留「侦探日志」文字并压低高度；日志时间列与判定列固定窄列，隐藏移动端判定前缀 `>`，避免「是也不是」与用户名/正文重叠。

对局日志视觉规则：桌面端普通日志正文为 15px、判定标签为 13px、提示/线索/猜测/裁判判定/汤底揭晓卡片标题与正文不低于 15px；猜测卡片右上角玩家名略大于时间文本。移动端日志正文约 12px，猜测与裁判判定卡片使用更小 padding 和 12px 正文、13px 玩家名，时间/判定列更紧凑，以保证提问正文宽度。

## 12. 部署注意事项

1. `turtle-soup` 端口必须保持 `8012`，因为 nginx、cedartoy 反代、supervisord 当前均按 8012 配置。
2. `cedartoy` 是公网 Cloudflare Tunnel 的实际第一跳，也是 MCP 聚合层。修改 nginx 不一定影响公网 HTTPS 行为；如公网 `/soup` 或根 MCP 异常，应先检查 `cedartoy/server.py`。
3. supervisord 当前只加载 `*.conf`。不要只复制 `turtle-soup.ini` 后期待生效；需要同步 `.conf`，或修改 supervisor include 规则。
4. `TURTLE_SOUP_SECRET` 当前若未设置会使用默认值 `<your-secret-here>`。生产建议在环境中显式设置并保持稳定；变更会使旧海龟汤 JWT 失效。
5. `TOY_SECRET` 用于 cedartoy 平台账号 JWT（`/api/auth/*` 与 MCP `account`）。默认 `change-me-before-production`；变更会使旧平台 token 失效。`SESSIONS_DB`（默认 `/opt/cedartoy/data/sessions.db`）供 `get_profile` 统计 MBTI/DND 完成次数；`TURTLE_SOUP_DB` 供平台账号与海龟汤 `players` 统计。
6. 裁判 LLM 不配置可用 `judge_api_configs` 时，`ask/guess/generate/hint/AI扫描` 会返回裁判不可用或失败；普通登录、开房、房间列表不依赖 LLM。注意 `purpose='hint'` 只服务提示池，`purpose='judge'` 只服务问答/猜底/生成/扫描，`purpose='both'` 两边都用。
7. `judge_api_configs.api_key` 存在 SQLite 中，管理 API 列表会脱敏，但数据库文件本身需要限制访问权限。启用配置前不只要测 HTTP 连通，还要用真实 `ask/guess/hint_request` 场景确认输出格式与语义；`guess` 现在要求严格 JSON，提示生成要求低剧透且不空回。连通但格式错误的节点会在后端内部重试，连续失败后玩家才会看到 `【系统提示】系统开小差了...` 或提示请求失败。
8. `settings.judge_prompt`、`settings.generate_prompt`、`settings.judge_prompt_clue` 不走缓存；通过管理后台或直接改表后，下一次裁判/生成调用立即生效。提示生成另有房间级异步锁，同一房间内手动/自动提示会排队调用提示池 LLM；手动提示不重置自动提示周期。
9. 汤底保护：普通 `/rooms/{room_id}`、MCP `join` 和进行中房间的 MCP `status` 不返回房间表 `answer`；管理员 `/admin/rooms` 会返回完整 answer；猜中后 SSE `game_over` 会下发纯 `answer`，同时写入含还原度和汤底的公开揭晓日志，因此结束后 MCP `status` 可通过日志看到最终汤底。100 题查看汤底是个人行为：网页端先显示页面内提示条，点“接受”后才进入最终确认弹窗；MCP 端在下一次 `ask` 顺便带 `confirm_reveal=true` 接受提示。确认后写入 `room_answer_reveals`，不结束房间、不广播答案，但该玩家不能继续答题、请求提示或操作记事板。若题目包含 `【隐藏后台设定】`，公开揭晓默认只使用该标记之前的汤底段，除非裁判 JSON 提供 `public_answer`。
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
supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy turtle-soup
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

### 13.1 本机验证

公网经 Cloudflare Tunnel，排查时建议先做本机分层验证：

```bash
curl -s http://127.0.0.1:8002/health
curl -s http://127.0.0.1:8012/soup/health
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8002/
```

如果本机 `8002` 正常但公网异常，优先查 Cloudflare Tunnel 和 `cedartoy` 反代；如果 `8012` 正常但 `/soup` 公网异常，优先查 `server.py` 的 `/soup*` proxy；如果 `8012` 本机也异常，查 `turtle-soup` 后端日志。

### 13.2 常见故障定位

| 现象 | 优先检查 | 说明 |
| --- | --- | --- |
| `/soup` 白屏或资源 404 | 是否执行 `npm run build`；`backend/static/assets` 是否存在 | 前端静态资源不入库，构建产物由 FastAPI 服务 |
| SSE 无新日志 | 浏览器 EventSource、`/soup/api/sse/{room_id}`、Cloudflare/反代 buffering | SSE 是进程内连接池，多进程不会互通 |
| 猜汤底出现系统提示 | `judge_api_configs` 启用项真实 guess JSON 输出格式；必要时查 `judge_audit_logs` | HTTP 连通不等于格式/语义正确；后端会先内部重试 3 次 |
| 管理后台改 prompt 不生效 | 确认改的是 `settings` 表当前 key | 代码每次抽表；不需要重启 |
| 个人页 404 或房间不存在 | `/rooms/profile/me` 路由顺序 | 当前文档已标注该路由顺序风险 |
| 根 MCP 不通但 `/soup` 正常 | 请求是否打到 `cedartoy:8002` 而不是 nginx `location = /` | 公网走 Cloudflare；本机 nginx 裸根会进 `8012` |
| MBTI/DND 结果丢失 | 是否超过 48 小时或 player_id 不一致 | `get_profile` 查询账号 id 和短用户名两类 player_id |
