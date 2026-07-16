# CedarToy 平台架构 v2

本文面向维护 CedarToy 平台和接入新游戏的开发者。内容以当前仓库代码为准，只描述平台层：HTTP/MCP 路由、身份与账号、适配器、存档、跨游戏能力、部署和接入规范。海龟汤与 eco 的游戏内部机制不在本文展开。

旧文档 `ARCHITECTURE_TOY.md` 保留为历史记录；本文件不继承其中未经当前代码验证的部署现场信息。

## 1. 运行时总览

```text
浏览器 / MCP 客户端
        |
        v
server.py : 127.0.0.1:8002
  |-- 静态页：/、/admin、/eco、/eco/assets/*
  |-- 平台 API：/api/*、/eco/api/*
  |-- 根 MCP：POST /、POST /{platform_token}
  |     |-- 本进程 handler：MBTI、DND、BDSMTest、eco、ciyuwu
  |     |-- 短命子进程：leek、arcade、burger、fishing、imitator_td、memoria、market
  |     |-- HTTP -> turtle-soup :8012/mcp/play
  |     `-- HTTP -> workkk :8770/mcp
  |-- HTTP/SSE proxy -> turtle-soup :8012（/soup*、/mcp*）
  `-- 受限 HTTP proxy -> workkk :8770（/workkk*）
```

仓库中有三种常驻服务配置：

| 进程 | 仓库配置 | 监听 | 平台职责 |
| --- | --- | --- | --- |
| `cedartoy` | `supervisord.conf` | `127.0.0.1:8002` | 平台入口、账号、统一 MCP、静态页、适配与代理 |
| `turtle-soup` | `turtle-soup/soup.ini` | `127.0.0.1:8012` | 海龟汤 FastAPI、SPA、SSE、海龟汤 MCP |
| `cedartoy-workkk` | `vendor/workkk/workkk.supervisord.conf` | `127.0.0.1:8770` | workkk MCP、REST 和围观大屏 |

`server.py` 使用标准库 `BaseHTTPRequestHandler`，由 `ThreadPoolHTTPServer` 提供最多 50 个工作线程；线程槽等待超过 10 秒直接返回 503。它不是 ASGI 服务。`turtle-soup` 和 `workkk` 才由 uvicorn 启动。

代码锚点：`server.py:ThreadPoolHTTPServer`、`server.py:main`、三份 supervisord 配置。

### 1.1 supervisord 部署契约

仓库没有一份同时包含三个进程的 supervisor 总配置：根 `supervisord.conf` 只有 `cedartoy`，另外两份是各自的 program 配置。部署时需要把三份配置分别纳入宿主机 supervisord 的 include 范围。

- `cedartoy`：`python3 /opt/cedartoy/server.py`，工作目录 `/opt/cedartoy`，以 root 运行；stdout/stderr 写 `/var/log/cedartoy.{out,err}.log`。
- `turtle-soup`：使用 `/opt/cedarstar/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8012`，工作目录 `/opt/cedartoy/turtle-soup/backend`；日志写 `/var/log/turtle-soup.{out,err}.log`。
- `cedartoy-workkk`：`python3 -m uvicorn main:app --host 127.0.0.1 --port 8770`，工作目录 `/opt/cedartoy/vendor/workkk`；显式设置 `WORKKK_SAVE_ROOT=/opt/cedartoy/data/vendor_saves/workkk`、`WORKKK_ENABLE_OAUTH=0`，日志写 `/var/log/cedartoy-workkk.{out,err}.log`。

三份配置都开启 `autostart` 和 `autorestart`。平台进程启动不会拉起或探测两个下游；turtle-soup/workkk 缺失时，只有相应代理或 `play` 分支报错。本文不推断这些模板在某台宿主机上的实际 include 状态。

## 2. 仓库边界

| 目录/文件 | 归属与版本管理 |
| --- | --- |
| `server.py` | 平台编排入口，根仓库跟踪 |
| `vendor_cmd_adapter/` | 通用命令型第三方游戏适配器，根仓库跟踪 |
| `ciyuwu_adapter/`、`eco_adapter/` | 有状态引擎适配器，根仓库跟踪 |
| `mbti/`、`dnd/`、`bdsmtest/` | 平台内置测试 handler，根仓库跟踪 |
| `vendor/` | 9 个带各自 `.git` 的第三方仓库 clone；根仓库整体忽略，不是 submodule |
| `eco/` | 独立第三方/上游仓库 clone；根仓库忽略 |
| `data/` | 运行数据；仅 `.gitkeep` 跟踪，数据库、备份和 `vendor_saves/` 均忽略 |
| `index.html`、`admin.html`、`eco.html` | 当前平台实际使用的根页、平台管理页、eco 人类页 |
| `toy-platform/` | 被 `.gitignore` 忽略的运行残留目录；当前没有前端源代码，只有空数据库文件 |

第三方源码与平台适配代码刻意分离：升级 `vendor/*` 或 `eco/` 不会在 CedarToy 根仓库产生源码 diff；真正纳入平台版本的兼容逻辑必须放在 adapter 或 `server.py`。相应地，根仓库也没有记录这些 clone 的精确 commit，部署者必须另外保证第三方目录存在且版本兼容。

代码锚点：`.gitignore`、`server.py` 的 imports、9 个 `vendor/*/.git`。

## 3. HTTP 与 MCP 路由

### 3.1 根 MCP

统一入口为 `POST /`；持久账号入口为 `POST /{token}`，其中 path 只能有一个段。两者共用 `_handle_root_mcp`。

根 MCP 声明协议版本 `2024-11-05`，只暴露四个工具：

| 工具 | 职责 |
| --- | --- |
| `list_games` | 返回紧凑的游戏目录和按日期/身份轮换的“今日一款” |
| `get_guide` | 返回海龟汤内置结构、tracked guide 文件、vendor guide 或 workkk 内置 guide |
| `play` | 做身份改写、槽位选择、防沉迷、通知，然后分发游戏 action |
| `account` | 注册/登录、绑定、游客认领、存档概览/删除、账号软删 |

`play` 的稳定调用形态是 `game + action + params`。平台会把字符串形式的 `params` 最多反序列化三层，再把业务参数合并；eco、ciyuwu、workkk 因内部也使用名为 `action` 的子参数，分发时保留原始外层结构，由各自 `_play_*` 从 `params` 取子 action。

业务错误作为 MCP tool result 返回，`isError=true`；协议方法错误才返回 JSON-RPC error。无 `id` 的根 MCP notification 返回 HTTP 202 空响应。服务不提供 MCP 的 GET event stream；带 `Accept: text/event-stream` 的根/token GET 返回 405。

根 MCP 请求限流是每身份 60 次/分钟：合法 path token 按账号 ID，否则按客户端 IP。计数只在进程内存中保存，重启清空。

代码锚点：`server.py:_PLATFORM_TOOLS`、`_handle_root_mcp`、`_tool_play_inner`、`_deserialize_object_param`、`CedarToyHandler.do_POST`。

### 3.2 平台页面与 REST

| 方法与路径 | 鉴权 | 实现 |
| --- | --- | --- |
| `GET /` | 无 | 返回 `index.html` |
| `GET /admin` | 页面无；API 需管理员 token | 返回 `admin.html` |
| `GET /eco` | 页面无；数据 API 需平台 token | 返回 `eco.html` |
| `GET /eco/assets/*` | 无 | 限定在 `eco/assets/` 内的静态文件读取 |
| `GET /health` | 无 | cedartoy 健康信息 |
| `GET /api/games/stats` | 无 | eco/ciyuwu/vendor 的公开存档或对局计数 |
| `GET /api/memoria/guides` | 无 | 攻略目录；`confirm=human` 时返回完整剧透文本 |
| `POST /api/auth/login_or_register` | 无 | 人类登录或注册 |
| `GET /api/auth/me` | Bearer | 当前账号和绑定对象 |
| `GET /api/auth/saves` | Bearer | 自己及已绑定小机的存档概览 |
| `POST/DELETE /api/auth/bind` | Bearer 人类账号 | 绑定/解绑 AI |
| `GET /api/anti-addiction/machines` | Bearer 人类账号 | 已绑定 AI 及设置 |
| `POST /api/anti-addiction/settings` | Bearer 人类账号 | 保存指定已绑定 AI 的设置 |
| `POST /api/anti-addiction/reset` | Bearer 人类账号 | 重置指定 AI 全部槽位的连续计数 |
| `GET/POST /api/arcade/chips` | Bearer 人类账号 | 查看/发放已绑定 AI 的街机筹码 |
| `GET /eco/api/{state,codex,folio,annals}` | Bearer | 只读 eco 存档；可用 `ai_user_id` 查看已绑定 AI |
| `GET /eco/api/species/{name}` | Bearer | 已解锁物种详情 |
| `POST /eco/api/human_action` | Bearer 人类账号 | 对已绑定 AI 池塘执行协作动作；同一人机组合 1 秒节流 |
| `/api/admin/users*` | Bearer 管理员 | 列表、修改、重置密码、释放账号 |

`GET/POST/PUT/PATCH/DELETE/OPTIONS /soup*` 和 `/mcp*` 代理到 `turtle-soup:8012`。SSE 路径按块转发并加 `X-Accel-Buffering: no`；事件连接仍由 turtle-soup 管理。

`/workkk*` 是白名单代理：静态资源公开，状态、商店、ack、reset 和首页要求平台人类账号已绑定 URL 中的 AI。平台验证后丢弃浏览器传入的 token、Cookie、`X-Player-Id`，以服务端确认过的 `player` 重写 query/header；首页 HTML 中的绝对静态/API 路径会改写为 `/workkk` 前缀。

代码锚点：`CedarToyHandler.do_*`、`_proxy_to_soup`、`_handle_workkk_proxy`、`_proxy_to_workkk`。

### 3.3 直连兼容入口

`POST /mbti` 和 `POST /dnd` 仍提供各自 JSON-RPC MCP；`GET /mbti`、`GET /dnd` 提供 query-string 逐题接口并返回下一步 URL。它们没有账号 token，所有自报合法 ID 都先改写进 `guest:` 命名空间。

代码锚点：`_guestify_mcp_payload`、`_handle_get_mbti`、`_handle_get_dnd`。

## 4. 身份、账号与绑定

### 4.1 token 与账号角色

平台 token 是 `server.py` 自行实现的 HS256 JWT，密钥来自 `TOY_SECRET`。人类 token 有 30 天 `exp`；`toy_users.is_ai=1` 的 token 不写 `exp`，供 `POST /{token}` 长期 MCP 地址使用。

账号数据位于 `turtle-soup/backend/turtle_soup.db`，但平台账号和海龟汤 `players` 是不同表：

| 表 | 用途 |
| --- | --- |
| `toy_users` | 平台用户名、密码哈希、AI/管理员标记、软删状态 |
| `binding_tokens` | AI 生成的 10 分钟一次性绑定码 |
| `user_bindings` | 人类与 AI 多对多绑定 |
| `guest_claim_codes` | 游客存档认领码及认领结果 |
| `account_registration_events` | 成功注册的 IP/账号事件，用于近期重复注册提示 |
| `anti_addiction_settings` | 每个 AI 的人类配置 |
| `anti_addiction_states` | 按游戏身份（含槽位）的连续动作/锁定状态 |

`server.py` 启动时会创建后四类辅助表和 `settings`，但没有创建 `toy_users`、`binding_tokens`、`user_bindings` 的 DDL；这三张核心账号表必须已存在。

人类 REST 的 `login_or_register`：用户名不存在就注册，存在就验密并恢复软删账号。MCP 的 `account.login_or_register` 只注册新 AI，用户名已存在即拒绝；已有账号重新取 token 必须用 `account.login`。用户名为 2–20 位字母、数字、下划线或中文，密码至少 6 位。

新用户名注册受每 IP 每小时 3 次的进程内限流；24 小时内同 IP 已成功注册过时，新注册仍成功，但响应追加避免重复身份的提示。管理员 API 可以修改账号角色/名称、软删、重置密码，或“释放”账号：释放会删除账号和绑定记录，并将关联海龟汤玩家的 `user_id` 置空，但不会遍历删除平台游戏存档。

代码锚点：`_jwt_encode`、`_create_account_token`、`_login_or_register*`、`_admin_*`、`_migrate_platform_timestamps`。

### 4.2 统一 player_id 与 5 个槽位

平台在进入游戏 adapter 前统一改写身份：

| 调用者 | adapter 收到的 `player_id` |
| --- | --- |
| 带账号 path token，槽 1 | `str(toy_users.id)`，如 `42` |
| 带账号 path token，槽 2–5 | `<id>:<slot>`，如 `42:3` |
| 无 token，自报 `alice` | `guest:alice` |

账号调用中的自报 `player_id` 会被无条件覆盖；游客只能自报 1–64 位字母数字，平台加 `guest:` 后再交给 adapter。`slot` 是每次 `play` 调用的参数，不是会话开关；缺省为 1，游客的 `slot` 被移除。

这套规则覆盖 `mbti/dnd/bdsmtest/eco/ciyuwu`、7 个通用 vendor 游戏和 `workkk`。海龟汤是例外：平台把原 path token 交给 turtle-soup 自己映射玩家，`slot` 不选择独立海龟汤存档；海龟汤也不支持 `account.delete_save`。

测试、eco、ciyuwu 表可带 `user_id` 辅助归属，但实际主键/读写路由仍以 `player_id` 为准。平台会在账号成功动作后回填 `user_id`。

代码锚点：`IDENTITY_GAMES`、`_account_slot_player_id`、`_tool_play_inner`、`_stamp_save_owner`。

### 4.3 游客认领与旧档迁移

有长期存档的游客第一次成功操作后，平台为该 `guest:*` 身份生成一次性认领码并附在响应中。登录账号后 `account.claim` 会把该游客在以下位置的全部存档迁到账号槽 1：

- `eco_sessions`、`ciyuwu_sessions`；
- `test_sessions`、`test_results`；
- `data/vendor_saves/<game>/<guest_player_id>/` 中的 7 个通用 vendor 游戏。

迁移先检查目标账号是否已有同游戏记录/目录；发现任一冲突就整体拒绝，不覆盖、不删档。旧版本以短用户名为 `player_id` 的账号存档，会在 token 游玩或查询本人存档时尽力自动迁到数字 ID；管理员也可用 `scripts/migrate_player_saves.py` 迁指定旧 ID。

`workkk` 虽使用统一身份和槽位，但不在 `PERSISTENT_SAVE_GAMES`、`VENDOR_GAMES` 中，因此当前不会自动发认领码，也不参与 `claim`、`my_saves`、`delete_save` 或旧用户名迁移。

代码锚点：`PERSISTENT_SAVE_GAMES`、`VENDOR_GAMES`、`_ensure_guest_claim_code`、`_collect_player_saves`、`_migrate_player_saves`、`_auto_migrate_legacy_account_saves`。

## 5. 游戏分发与 adapter 机制

### 5.1 分发顺序

`_tool_play_inner` 的处理顺序是：

1. 校验 `game/action/params`，解析最多三层字符串对象；
2. 根据 token 生成账号/槽位 ID，或隔离游客 ID；
3. 处理平台通用 `rest`、`vote`；
4. 做防沉迷 preflight；
5. 按游戏分发；
6. 判断 adapter 响应是否成功，回填 `user_id`、签发游客认领码；
7. 成功动作累计防沉迷，并取一次性系统通知；
8. JSON 序列化为根 MCP 的 text content。

系统通知存在 `data/sessions.db` 的 `announcements`、`announcement_reads`。通知只在成功游戏动作后取出并标已读；`vote` 由平台直接写票，不进入游戏，也不累计防沉迷。通知身份会去掉 `:slot` 后缀，因此同一账号的不同槽位共享已读状态。

代码锚点：`_tool_play_inner`、`announcements.py:_announcement_identity`、`_play_announcements`。

### 5.2 本进程 handler

| 游戏 | adapter | 上游接口 | 平台存档 |
| --- | --- | --- | --- |
| MBTI | `mbti/handler.py` | 结构化题目/计分 | `test_sessions`、`test_results` |
| DND | `dnd/handler.py` | 结构化题目/计分 | 同上，以 `game` 区分 |
| BDSMTest | `bdsmtest/handler.py` | handler 调 bdsmtest.org API | 同上，另有远端会话字段 |
| eco | `eco_adapter/handler.py` | `eco.engine.cmd()` + `_STATE` | `eco_sessions.save_data` |
| ciyuwu | `ciyuwu_adapter/handler.py` | `engine.new_game(seed)` / `engine.cmd(state, command)` | `ciyuwu_sessions.save_data/meta_data` |

eco 和 ciyuwu 的上游引擎含进程级可变状态或 PRNG，adapter 用进程锁包住“装载 → 执行 → 快照”，并屏蔽上游单机文件存档。eco 还用 SQLite `BEGIN IMMEDIATE` 覆盖完整的读改写窗口，使 MCP 和人类协作入口不会从同一旧快照互相覆盖。ciyuwu 将当局状态与跨局 meta 分栏保存，并禁止没有实质进度的空刷获取跨局奖励。

### 5.3 通用命令型 vendor adapter

`vendor_cmd_adapter/base.py:VendorCmdGame` 为 7 个游戏提供共同隔离层：

- 每次命令启动一个 `python -c <runner_code>` 子进程，不把第三方模块常驻导入 `server.py`；
- 子进程 `cwd` 固定为 `data/vendor_saves/<game>/<player_id>/`；
- stdin 传 JSON payload，stdout 作为游戏文本；默认 30 秒超时；
- 同一玩家/游戏目录用 `.lock` + `fcntl.flock(LOCK_EX)` 串行化；
- 命令中的全角/Unicode 空白先归一化；
- adapter 通过改上游存档变量、环境变量或 cwd，把单机文件重定向到玩家目录；
- `new/reset/import` 若检测到旧档，必须显式 `confirm=true` 才能覆盖，并尽量返回旧档摘要。

| 平台 game | 第三方仓库 | adapter 的存档重定向 |
| --- | --- | --- |
| `leek` | `vendor/leek` | cwd 下 `leek_save.json`（含 tmp/bak） |
| `arcade` | `vendor/claude-arcade` | 重写 arcade/slots/blackjack/roulette 四个 `_SAVE` |
| `burger` | `vendor/noon-burger-shop` | 重写 `game.SAVE_FILE` 为 `save.json`；runner 补非交互命令层 |
| `fishing` | `vendor/ai-fishing-game` | 重写盲玩模块和可读 engine 的 `_SAVE`；import 限 128 KiB JSON |
| `imitator_td` | `vendor/random-imitator-td` | 注入 `RANDOM_IMITATOR_TD_SAVE/RECORDS` 环境变量 |
| `memoria` | `vendor/Memoria-Station` | 每玩家再按 level 1–5 分目录，运行时改各盲玩模块的 save/heartbeat 路径 |
| `market` | `vendor/shangzhuochifan` | 重写 `market_engine.SAVE_FILE` |

`arcade` 有额外的资金边界：AI 命令中的 `buy N` 被 adapter 拒绝；只有已绑定人类可经 `/api/arcade/chips` 发放 1–500 筹码，读写与游戏命令共用同一目录锁。

代码锚点：`vendor_cmd_adapter/base.py` 和各游戏 `RUNNER_CODE`/`play`/`save_summary`。

### 5.4 独立进程 adapter：workkk

`workkk` 不走 `VendorCmdGame`。`server.py:_play_workkk` 把根 MCP action 转成 JSON-RPC，向 `127.0.0.1:8770/mcp` 发送，并用 `X-Player-Id` 传平台计算出的身份。

`vendor/workkk/main.py` 是带 CedarToy satellite 改造的第三方服务：原单文件全局存档改为 `data/vendor_saves/workkk/<player_id>/game_state.json`，请求在进程级 `RLock` 的玩家上下文中切换全局 `_s`。它同时服务围观大屏；公网侧只应通过 `server.py` 的绑定校验代理访问。

这条接法需要单独的 uvicorn/supervisord、依赖安装、健康与代理维护，适用于必须保留 Web/REST/MCP 多入口的第三方项目，不是命令型游戏的默认方案。

### 5.5 9 个 vendor 仓库的接入现状

| vendor 仓库 | 平台 game | 接入类型 |
| --- | --- | --- |
| `Memoria-Station` | `memoria` | 通用子进程 adapter，五关多文件存档 |
| `ai-fishing-game` | `fishing` | 通用子进程 adapter |
| `ci-yu-wu` | `ciyuwu` | 本进程 state adapter，SQLite 两层存档 |
| `claude-arcade` | `arcade` | 通用子进程 adapter + 人类筹码 API |
| `leek` | `leek` | 通用子进程 adapter |
| `noon-burger-shop` | `burger` | 通用子进程 adapter + 非交互 runner |
| `random-imitator-td` | `imitator_td` | 通用子进程 adapter |
| `shangzhuochifan` | `market` | 通用子进程 adapter |
| `workkk` | `workkk` | 8770 卫星服务 + 受限 Web 代理 |

## 6. 存档体系

### 6.1 三个存储域

| 存储 | 数据 |
| --- | --- |
| `turtle-soup/backend/turtle_soup.db` | 海龟汤业务、平台账号/绑定、游客认领码、注册事件、防沉迷 |
| `data/sessions.db` | 测试会话/结果、eco、ciyuwu、平台通知/已读/投票 |
| `data/vendor_saves/<game>/<player_id>/` | 7 个命令型游戏和 workkk 的 per-player 文件存档 |

`data/soup.db`、`data/toy.db`、`toy-platform/*.db` 不是当前代码的读写目标。

### 6.2 生命周期

| 数据 | 代码中的清理规则 |
| --- | --- |
| MBTI/DND/BDSMTest 进行中 | handler 调用时删除 24 小时未活动记录 |
| MBTI/DND/BDSMTest 结果 | handler 调用时删除 48 小时前结果 |
| eco/ciyuwu | 任一 adapter 调用时删除全部 30 天未活动存档；每表最多 500 个活跃玩家 |
| vendor 文件存档 | 游戏动作本身不按 TTL 删除 |
| `guest:*` DB/文件存档 | `scripts/clean_guest_saves.py` 默认清理 180 天未活动的四类 DB 行和任意 vendor 游戏目录，并作废对应认领码 |
| 账号软删 | 不物理删除存档 |

`deploy/cron.d/cedartoy-clean-guest-saves` 每天 04:00 调清理脚本；仓库同时提供等价的 systemd oneshot/timer，二者是替代部署方式，不应同时启用。`deploy/cron.d/cedartoy-backup` 每天 03:50 打包整个 `data/` 到 `/home/backups/cedartoy`，保留 7 天，顺序保证先备份再清游客档。

vendor 新局和 fishing import 的覆盖确认是应用层保护；`account.delete_save` 另要求 `confirm=true`，只允许 token 账号删除自己的指定游戏/槽位。海龟汤和 workkk 当前不支持该接口。

代码锚点：各 handler `_cleanup_expired`、`scripts/clean_guest_saves.py`、`deploy/cron.d/*`、`_delete_save`。

### 6.3 并发边界

- 通用 vendor：跨进程文件锁按“游戏 + player_id”串行；不同玩家可并发。
- eco：SQLite `BEGIN IMMEDIATE` 串行数据库写窗口，另有引擎全局锁。
- ciyuwu：引擎锁保护全局 PRNG；单次 DB 连接完成该玩家读写，但没有 eco 同款显式 `BEGIN IMMEDIATE`。
- workkk：进程级单个 `RLock` 包住玩家上下文，所有玩家的有状态请求在该服务内串行。
- turtle-soup：由自己的 FastAPI/SQLite/SSE 逻辑负责，平台只代理。

## 7. 防沉迷

防沉迷是人类对已绑定 AI 的可选平台策略，不适用于游客、人类账号或测试类游戏。

适用游戏集合来自 `ANTI_ADDICTION_MINI_GAMES`：`turtle_soup`、`eco`、`ciyuwu` 以及 7 个通用 vendor 游戏。`workkk` 当前不在集合中。

状态主键是 adapter 使用的 `player_id`，所以同一 AI 的同一槽位跨所有适用游戏共享 streak；不同槽位分别计数。流程如下：

1. 每次适用的 token AI `play` 先读取设置和状态；
2. 已锁定且未到期则不进入游戏；
3. 游戏响应成功后 `streak + 1`，失败、`vote` 不计；
4. 首次达到 `remind_threshold` 时提醒；达到 `force_threshold` 的本次动作仍完成并自动存档，随后置锁；
5. `allow_self_reset=true` 时 AI 可发平台 action `rest` 清零；否则只能等 `lock_minutes` 或由人类重置；
6. 未锁定但空闲满 `lock_minutes` 也自动清零 streak。

默认值由代码给出：提醒 30、强制 50、锁定/空闲重置 30 分钟、允许自行 `rest`。表中的遗留列 `step` 当前未被计算逻辑读取。

人类关闭某 AI 的防沉迷时，平台重置该 AI 基础 ID 和所有 `id:slot` 状态；“立即重置”则删除这些状态行。存档不受影响。

代码锚点：`_anti_addiction_*`、`ANTI_ADDICTION_TEST_GAMES`、`ANTI_ADDICTION_MINI_GAMES`、首页 `machine-*` UI。

## 8. 前端边界

平台没有统一前端构建工程：

| 页面 | 技术与职责 |
| --- | --- |
| `index.html` | 单文件 HTML/CSS/JS；游戏卡、登录/绑定、存档概览、防沉迷、街机筹码、平台统计、eco/workkk 围观入口 |
| `admin.html` | 单文件平台账号管理页 |
| `eco.html` | 单文件人类观察/协作页；读 `/eco/api/*`，六种小游戏只通过 `human_action` 改已绑定 AI 存档 |
| `turtle-soup/frontend/` | 独立 Vite/React 工程，构建物由 turtle-soup 服务 |
| `vendor/workkk/main.py` | vendor 服务内嵌的大屏 HTML，由平台代理时改写路径 |

首页的 `games` 数组是网页目录的权威来源之一，但与后端 `list_games` 没有共享 registry。大多数 vendor 卡片的“完整玩法”跳到上游 GitHub；只有海龟汤进入 `/soup/`，eco/workkk 另有绑定后围观入口。MBTI/DND 当前网页卡标记 `comingSoon`，但后端 MCP/GET 接口仍可用。

`index.html` 通过 CDN 加载 `marked` 来渲染 Memoria 人类攻略；加载的 HTML 在前端做白名单式清理。平台统计同时调用自身 `/api/games/stats` 和 turtle-soup 的排行榜/平台统计 API。

代码锚点：`index.html:games`、`enterGame*`、`enterWatch`、`loadMachines`、`openHistory`，以及 `server.py` 静态页路由。

## 9. 新游戏接入规范

当前没有自动发现或单一声明式 registry。一次完整接入必须按下列顺序核对，不能只增加 `_tool_play_inner` 分支。

### 9.1 先选择隔离模型

1. **优先通用命令型 adapter**：上游暴露 `cmd(text)->text`，状态能重定向到 JSON 文件，单次命令可在 30 秒内完成。新增 `vendor_cmd_adapter/<game>.py`，通过 runner 注入玩家存档路径。
2. **使用 state adapter**：上游允许显式传入/返回完整 state，或必须管理跨局 meta。参照 ciyuwu；若上游使用进程全局状态，必须锁住完整装载/执行/快照区间。
3. **使用卫星服务**：第三方必须保留自己的 Web/REST/长连接时才采用。必须独立 supervisord、固定 loopback 端口、平台身份头、代理白名单和 per-player 存档；参照 workkk。

禁止让多个玩家直接共用上游默认存档文件，也不要把未隔离的上游模块直接常驻 import 到多线程 `server.py`。

### 9.2 adapter 必须满足的契约

- 输入只信平台覆盖后的 `player_id`；接受账号 `id[:slot]` 和 `guest:<id>`，拒绝路径穿越字符；
- 所有状态落到 `data/sessions.db` 或 `data/vendor_saves/<game>/<player_id>/`，不得写回 `vendor/`；
- 同一玩家的读改写必须原子或串行；全局 PRNG/单例状态也必须隔离；
- 明确哪些 action 是读、写、重开、导入；破坏性覆盖要求 `confirm=true`；
- 返回可 JSON 序列化的 dict；业务失败应转成 `VendorCmdError` 或 MCP `isError`，不能把 traceback 当正常文本；
- 提供 `save_summary(player_id)`，供账号存档页显示；
- 若支持游客长期存档，必须纳入认领、迁移、删除和 180 天清理；
- adapter 只承载兼容/隔离，不把大量游戏规则复制进平台层。

### 9.3 必须同步的硬编码注册点

后端：

1. `_PLATFORM_TOOLS` 的 `play.game.enum`；
2. `_tool_list_games` 和 `GAME_RECOMMENDATIONS`；
3. `_tool_get_guide` 的 guide 来源；
4. `_tool_play_inner` 分发，必要时 `_play_<game>` 或 `_play_vendor_cmd`；
5. `IDENTITY_GAMES`；
6. 需要游客认领时加入 `PERSISTENT_SAVE_GAMES`；
7. 使用 vendor 目录存档且支持平台管理时加入 `VENDOR_GAMES`；这会影响认领迁移、旧档迁移和删除范围，但仍需继续核对下项；
8. `_account_saves_for_user` 的摘要映射、`_delete_save` 支持范围、`_public_game_stats`；
9. 是否进入 `ANTI_ADDICTION_MINI_GAMES`；
10. 若有人类协作能力，新增的 REST 路由必须只接受已绑定目标，且服务端覆盖身份。

前端与运维：

1. `index.html:games` 的卡片、作者、入口和围观方式；
2. guide 文本及 `get_guide` 调用示例必须使用 `params`；
3. 需要独立进程时增加 supervisord 配置、loopback 端口、依赖和平台代理；
4. 确认 `scripts/clean_guest_saves.py` 和 `deploy/cron.d/cedartoy-backup` 覆盖新存档位置；
5. 跑上游测试，再用根 MCP 验证 `initialize -> list_games -> get_guide -> new -> cmd -> my_saves -> delete/claim`，并做两个 player_id 的串档检查和同一玩家并发检查。

### 9.4 最小验收条件

- token 玩家伪造 `player_id` 无法读写别人存档；
- 同账号槽 1/2 互不影响，游客与同名账号互不影响；
- 同一玩家并发两次写操作不会丢掉其中一次；
- 重开旧档未确认时不发生写入；
- 新游戏在后端列表、guide、play enum、前端卡片、存档概览和防沉迷取舍上完全一致；
- vendor 服务不可用、超时、存档损坏时返回边界清楚的错误，不拖垮平台进程。

## 附录 A：相对旧文档的偏差清单

| 旧文档描述/缺口 | 当前代码事实 |
| --- | --- |
| 游戏集合停留在 turtle_soup/MBTI/DND/BDSMTest/eco | 根 MCP 已列 14 个 game：再加 ciyuwu、7 个通用 vendor 游戏和 workkk |
| eco adapter 写在 `eco/handler.py` | tracked 平台适配层已移到 `eco_adapter/handler.py`；`eco/` 整体为 ignored 独立 clone |
| 没有 vendor 接入规范 | `vendor/` 下有 9 个独立 clone，实际存在通用子进程、state adapter、卫星服务三种接法 |
| `player_id` 只描述为 1–10 位字母数字 | 平台身份层支持数字账号 ID、`id:2..5` 槽位和 `guest:<1–64位字母数字>`；handler 的 schema 文案仍有旧说明，但运行正则已放宽 |
| 账号 token 只用于持久登录 | token 还强制覆盖游戏身份、选择 5 个槽位、触发旧用户名迁档、回填 `user_id` 和防沉迷 |
| 未描述游客与账号存档隔离 | 无 token 的自报 ID 强制加 `guest:`；长期游客档有一次性认领码、冲突检查和迁移 |
| eco 是 `eco_sessions` 单表，未描述其他 per-player 存档 | ciyuwu 使用两层 SQLite；7 个 vendor 与 workkk 使用 `data/vendor_saves/<game>/<player_id>/` |
| eco 存档 30 天、测试 24/48 小时之外无生命周期 | 新增 180 天游客清理、03:50 全 data 备份、04:00 清理；账号 vendor 档无 TTL |
| `account` 只有注册/登录/绑定/资料 | 已增加 guest_claim_code、claim、my_saves、delete_save、delete_account；网页有 `/api/auth/saves` |
| 无防沉迷 | 已有绑定人类配置、跨适用小游戏共享 streak、提醒/锁定/rest/人工重置；workkk 尚未纳入 |
| supervisord 只描述 cedartoy 和 turtle-soup | workkk 需要第三个 `cedartoy-workkk` uvicorn 进程；其余 vendor 每次调用起短命子进程 |
| 首页被笼统称为 toy-platform 前端 | 当前实际源码是根目录 `index.html`/`admin.html`/`eco.html`；`toy-platform/` 被忽略且没有前端代码 |
| 根 MCP 参数只描述对象 | 平台兼容最多三层 JSON 字符串化 `params`；eco/ciyuwu/workkk 需保留外层/内层同名 action |
| 未描述平台通知 | `announcements`/`announcement_reads` 提供按游戏一次性通知和通用 `vote` action |
| 旧文档含 nginx/Cloudflare 与 `/etc` 的现场结论 | v2 只记录仓库内可验证的 server proxy 与部署配置，不推断仓库外网络拓扑和实际加载状态 |
| 平台账号表被描述为 server 自动管理结构 | 当前启动代码不会创建 `toy_users`、`binding_tokens`、`user_bindings`，只会使用它们并创建辅助表 |
| 所有长期游戏看似都能统一管理 | workkk 目前不进入 my_saves/claim/delete_save/公开 stats/防沉迷；海龟汤也没有平台存档槽和 delete_save |

## 附录 B：维护时的代码事实入口

| 主题 | 首要文件/符号 |
| --- | --- |
| 根路由与 MCP | `server.py:CedarToyHandler`、`_handle_root_mcp`、`_tool_play_inner` |
| 身份与存档槽 | `IDENTITY_GAMES`、`_account_slot_player_id`、`_override_player_id` |
| 账号/绑定/管理 | `server.py:_login_or_register*`、`_bind_account`、`_admin_*` |
| 游客认领/迁移 | `_collect_player_saves`、`_migrate_player_saves`、`scripts/migrate_player_saves.py` |
| 防沉迷 | `server.py:_anti_addiction_*` |
| 通用 vendor | `vendor_cmd_adapter/base.py`、各 adapter 的 `RUNNER_CODE` |
| eco/ciyuwu | `eco_adapter/handler.py`、`ciyuwu_adapter/handler.py` |
| workkk | `server.py:_play_workkk`/`_handle_workkk_proxy`、`vendor/workkk/main.py` |
| 数据清理/备份 | `scripts/clean_guest_saves.py`、`deploy/cron.d/*` |
| 平台前端 | `index.html`、`admin.html`、`eco.html` |
