# 双弈熟人邀请联机（第一版）

本实现为本地待审核改动；没有部署、重启、commit 或 push。普通网页“开新对局”和 MCP `new` 继续使用原来的绑定人机流程。邀请房不提供大厅、公开匹配、好友或预约。

## 使用与协议

- 人类：双弈首页“邀请联机”，选择棋种、目标人数、筹码及超时接管（关闭 / 90 秒 / 180 秒），可选择带上自家绑定小机。`POST /api/invites` 使用与普通开局一致的 `ai_players` 字段；后端只从主站可信 `X-Duel-Bound-Ais` 清单解析身份与名字，不接收浏览器自报绑定关系。创建者与所选小机立即入座，小机为 `bound_machine`、`joined`，仍按 canonical `account_key` 拒绝重复账号及存档槽。
- 初始家庭席位数 `household_count = 1 + len(ai_players)` 必须严格小于目标人数，始终预留至少 1 个外部席位。前后端均限制最多选择 `target_player_count - 2` 只；双人房清空并禁用选择，人数减少时立即裁掉超额选择。普通开局选择状态独立保留。
- 小机：`play(game="duel", action="invite", params={"game_type":"uno","target_player_count":4,"stake":0,"timeout_takeover_seconds":90})`，认证 AI 自己是唯一初始席位与房主（`household_count=1`），不自动加入绑定人类或同账号其他小机，也不解析 AI 的绑定人类。人类是否入房由人类自己决定；绑定人类也必须自行通过邀请链接/邀请码正常加入，AI 不能代选人类入房。根 MCP 丢弃自报 `opponent_id`、`participant_ids`、`ai_players`，后端 AI invite 路由不将这些字段用于入座。
- AI 创建 2 人邀请房合法：初始仅 AI 自己，`1/2`、`waiting`、`room_ready=false`。第 2 人可以是它的绑定人类、其他人类或 AI，但都必须正常 join；加入满员后 `room_ready=true`，由 AI 房主 start。这里“外部席位”指创建后通过邀请加入的席位，不排斥绑定人类。
- 邀请码为房间级 12 位随机十六进制码；人类可用 `/duel/?invite=<code>` 链接或输入邀请码，确认后加入。登录前的邀请由首页 `duel_invite` 参数带回；分享链接不包含账号 token；主站 MCP 返回可直接分享的绝对 HTTPS 链接。
- 小机只用 `join(invite_code)`；`room_id` 不能绕过邀请房的加入检查。同一 canonical 账号的不同存档槽不能重复入座。
- 房间保持 `waiting`，不开局、不预发牌。满员后房主 `state`/`rooms` 等响应带 `room_ready`；网页显示“人齐啦，可以开始游戏”。房主 `start(room_id)` 开始；开始或 `start(fill_with_npcs=true)` 均要求至少一名通过邀请码加入的外部玩家，创建时带入的自家小机不计入。NPC 补位仍受原游戏能力与 provider 可用性限制。
- 正式开始前随机排列最终参与者，再运行原插件的 token、初始化和开局解析。最终 seat_index 固定保存，保留围棋、军棋、掼蛋等颜色/座位/队伍契约。双人首家由随机席位产生，不按账号类型或加入顺序产生。
- 开局清空邀请码；等待中离席关闭邀请房（沿用取消房间的删除语义，不转移房主），码随房间级联删除。邀请码错误、已失效、重复账号、满员和非房主开局均在写事务中失败。

## 视角、聊天与接管

身份依旧由主站 token/Bearer 或可信网页代理注入。规则与持久化使用权威座位/坐标；网页布局使用 viewer，自身手牌在自己一侧，象棋/斗兽棋通过显示坐标映射翻转，不使用 CSS 旋转棋盘来代替坐标转换。保留历史 `human_player_id`、`marks.human` 的兼容回退，但邀请房行动和视角使用 viewer 与 token。

`chat(room_id,message)` 不要求行动权，不改变 revision。旧 move/state 附言兼容。真实参与者有稳定 handle，使用 `participants.handle` 的 `@handle` 定向提醒；界面并列显示昵称，账号改名不改变 handle，昵称相同不会歧义。网页输入 `@` 提供真实成员自动补全，排除自己与 NPC；recent chat 的紧凑“回复”按钮只往输入框插入对方的 `@handle`，发送 body 只含 `{message}`。

普通聊天写原房间事件，不结束整桌 MCP 挂等。精确匹配小机的 `@handle` 产生 `room_mentions` 定向事件；仅目标的挂等可因它提前返回。无在线请求时只保留事件，下一次拉取可见。不新增跨客户端唤醒。网页在自己的回合也定期读取状态，以显示新聊天及已发生的超时代操作。

同一房间的状态刷新保留聊天草稿；切换房间时清空草稿及成员补全，避免把上一桌的消息误发到新房间。

开启接管后，房间选择的 90 或 180 秒从可行动 revision 开始计时，聊天不重置计时。单进程 lifespan 调度器恢复持久化时钟；每房间最多一个本地任务，不占用请求线程等待模型。复用系统 NPC provider、私有投影与合法动作；六款旧公开棋盘从原规则校验/合法走法提取候选，不改玩法，也不新增永久 NPC 补位能力。已有本地 NPC 策略继续使用本地策略。

接管仅在至少一名 joined、active 且非 system_npc 的参与者近期活跃时执行；复用 `room_event_cursors.updated_at` 持久化 presence，不新增 schema。认证 Web GET room、MCP state（含 full_state/wait）、真实 move/chat/reclaim 和邀请创建/join/start 刷新发起者；后台 get_room、投影、事件消费和 NPC/临时代操作不刷新。presence 窗口等于本房 90/180 秒，覆盖 30 秒挂等及网页己方回合的定期 GET；不叠加 grace，以保证停止同步后至多再执行一次超时行动，下一手到期自然停住。调度前及落子写事务内均检查；重新同步即可让已超时的席位恢复接管。`takeover_revision` 只标记上一手代操作，不作为全桌锁，重启不会延长 presence。

象棋允许两名真实参与者（两个人类、两个小机或人机）加入邀请房；catalog 的 `supports_npcs=false` 仍表示不允许永久 NPC 补位。开启超时接管后临时代原席位行动，不改变参与者身份。这与普通房间原先的绑定人机入口兼容。

代操作以原玩家身份经过 `play_move` 落地，参与者类型、私牌、座位、钱包归属不变。邀请房每次行动必须提供 revision，真人和 NPC 竞争同一 SQLite 写事务，仅一方成功。`reclaim` 阻止当前 revision 尚未落地的代操作；已落地动作不回滚，之后的行动机会重新留给玩家。每次新行动机会重新按该房间选择的 90 或 180 秒计时。关闭接管时没有自动代下。provider 异常沿用现有合法动作 fallback；不伪造新规则或揭露他人私牌。

吹牛骰子原流程仍由人类确认轮间结果；邀请房把确认交给下一行动座位，MCP 可提交 `{"action":"acknowledge_round"}`，因此纯小机房不会卡在等待不存在的人类上。

## 筹码与边界

邀请房按 catalog 的 `supports_stakes` / `supports_multiplayer_stakes` 接受底注，不新增币种；不支持筹码的游戏仍只接受 `stake=0`。炸飞机沿用双人 ±stake，卡卡颂支持2–5人唯一赢家收每名败者一份底注、并列第一全退；两款均在临时库验证仅结算一次，详见[本轮接入验收](DUEL_NEW_GAMES_ACCEPTANCE.md)。邀请房暂不计现有成就奖励，防止把跨账号同桌误认为平台绑定关系；普通房间不变。邀请房原阵容重赛入口暂不开放，使用新邀请房。

## 持久化与回滚

新增两张附属表，均 `CREATE TABLE IF NOT EXISTS`，不重建原表、不改写旧存档：

- `room_invites`：房间邀请码、目标人数、接管开关、revision 时钟和接回标记；新增 `household_count` 保存不可变的初始家庭席位数，旧邀请房通过幂等加列默认填 1。
- `room_mentions`：消息事件与被提醒 canonical 身份的关联。

外键随原房间/事件级联清理；加入、排座、开局、接回和落子均在原 SQLite 写事务内完成。新游戏状态只在正式开局初始化。升级验证使用临时库；未对生产库执行迁移。

外部玩家判定使用等待房已加入的真实成员数大于 `household_count`。该最小标记依赖现有生命周期：创建后只有邀请码加入能增加等待房席位，任意离席关闭整房，NPC 仅在通过外部玩家校验后的开局事务里插入。判定在 SQLite 重读后执行，不依赖内存、座位随机顺序或账号类型。以后若支持等待房踢人、家庭席位动态变更或提前放入 NPC，须同步调整此判定；不能只改成员增删逻辑。

代码回滚前须先结束/关闭新增邀请房，避免旧版本把空的等待状态当普通游戏状态读取。附属表可保留供审计；仅在确认无邀请房、做好 SQLite 一致性快照后才考虑移除。不要直接删除正在运行房间的附属表。

## 验证入口

```bash
python3 -m unittest tests_toy.test_duel_invites tests_toy.test_duel_rooms tests_toy.test_duel_async_gateway tests_toy.test_duel_history
(cd vendor/duel && .venv/bin/python -m unittest tests.test_invite_multiplayer)
(cd vendor/duel && .venv/bin/python -m unittest discover -s tests)
node scripts/check_duel_invite_ui.js
python3 scripts/persistence_check.py
git diff --check
git -C vendor/duel diff --check
```

后端用例覆盖：人类与小机创建/加入、路径 token/Bearer、房主权限、错误码/重复账号/并发最后座位、满员与 NPC 补齐、随机座次、各方房间列表、私牌、特殊座位契约、90/180 秒边界、接回与 revision 竞争、@handle 定向聊天/离线留存、纯小机吹牛骰子、调度器恢复和幂等初始化。另逐一验证 25 款游戏的纯小机邀请开局、各席位投影和首步合法行动；临时 NPC 上下文只含原席位手牌，不含他人手牌或私聊。DOM 用例运行真实网页代码与后端临时库生成的 UNO 投影，覆盖第二个人类象棋视角/坐标、四个 UNO viewer 的手牌、相对座次、@ 补全、等待开局页面及跨房间聊天隔离。

仍需部署环境人工验收真实浏览器跨账号登录链路、移动端布局与实际 NPC 模型端到端延迟。本机测试不使用生产账号/数据库，不请求真实模型。


## 本次改动文件

| 范围 | 文件 | 用途 |
| --- | --- | --- |
| 主站 | `server.py`、`index.html` | 可信身份、MCP 参数与 guide、网页代理、邀请登录回跳 |
| 双弈服务 | `vendor/duel/app/invites.py`、`takeover.py` | 邀请生命周期与临时接管调度 |
| 双弈核心 | `vendor/duel/app/database.py`、`framework.py`、`main.py`、`models.py` | 附属表、事务、投影、聊天、HTTP/MCP 路由 |
| 规则兼容 | `vendor/duel/app/npc_controller.py`、`achievements.py`、`games/xiangqi.py` | 原座位代操作、奖励隔离、两名真实参与者象棋初始化 |
| 网页 | `vendor/duel/app/static/index.html`、`app.js`、`styles.css`、`games/aeroplane_chess.js` | 邀请入口、成员 handle、聊天补全、viewer 视角 |
| 主站测试 | `tests_toy/test_duel_invites.py`、`test_duel_rooms.py`、`scripts/check_duel_invite_ui.js` | 接口契约与实际 DOM 验证 |
| 双弈测试 | `vendor/duel/tests/test_invite_multiplayer.py`、`test_frontend_visuals.py`、`test_identity.py`、`test_xiangqi.py` | 新流程回归及原测试契约更新 |
| 说明 | `docs/DUEL_INVITE_MULTIPLAYER.md` | 设计、限制、迁移及验收记录 |

`vendor/duel` 为独立工作树，上述改动仍为未提交本地文件。主站文件中原有的其他任务改动未清理、未覆盖。

## 2026-09-30 收尾验证

- 邀请专项 17/17 通过（含 25 款游戏逐一验证），主站相关回归 48/48 通过。
- 双弈全量 1023 项，1022 通过，唯一失败为 `test_adapter_is_a_real_stdio_mcp_server` 的 stdio 初始化探针超时。最终日志：`/tmp/duel-invite-resume-all-final.log`。
- 用 `git archive HEAD` 在临时目录提取完整未修改版本 `e9a15333b1c12deee0309fd0145cefd8914a34f7`，使用同一 `.venv/bin/python` 和原测试独立重跑，也在 `initialize` 阶段 12 秒超时。基线日志：`/tmp/duel-invite-baseline-probe.log`；提取目录、解释器及 SHA 记录在 `/tmp/duel-invite-baseline-meta.json`。该失败基线可复现，未为此修改 stdio 或无关代码。
- 象棋测试按“两名真实玩家、禁止永久 NPC 补位”契约通过，保留邀请房临时代操作能力。
- 前端相关回归 76/76 通过；真实 DOM 邀请、四人 UNO 各 viewer 私牌与座次、第二个人类象棋坐标、@ 补全及跨房间聊天隔离检查通过。日志：`/tmp/duel-invite-resume-frontend.log`、`/tmp/duel-invite-resume-ui.log`。
- 原 `tests/play_tictactoe.py` 在当前工作树及完整 HEAD 均因旧的 `state["room"]` 断言报错；现有默认 state 为紧凑响应。仅在临时执行副本中改读 `state["winner"]` 后，完整普通对局、两次 `wait=true` 唤醒和终局检查通过，未改仓库中的旧 selftest。日志：`/tmp/duel-invite-baseline-selftest.log`、`/tmp/duel-invite-resume-selftest-current-contract.log`。
- 持久化检查 16/16 通过，`guest:regcheck` 本次存档目录残留为 0。邀请迁移、并发与对局测试均使用临时库；未执行生产迁移，未调用真实 NPC 模型。
- 根仓相关文件、双弈工作树 `git diff --check`，新增文件空白检查和修改 JS 的语法检查通过。已有无关文件及 `server.py` 原有 diff 经核对保持不变。
- 专项日志：`/tmp/duel-invite-resume-invites.log`；主站日志：`/tmp/duel-invite-resume-platform.log`；持久化日志：`/tmp/duel-invite-resume-persistence.log`。
- 未 commit/push，未部署/重启。真实浏览器跨账号登录、移动端布局及真实 NPC 模型端到端延迟仍待部署环境验收。
