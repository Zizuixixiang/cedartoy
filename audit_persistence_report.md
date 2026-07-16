# CedarToy 全游戏状态持久化审计报告

审计时间：2026-07-17（Asia/Shanghai）  
审计方式：只读代码审计 + 指定测试账号的沙盒动态测试  
动态测试账号：`guest:audit1`  
范围：`vendor_cmd_adapter` 六个游戏（`arcade`、`burger`、`fishing`、`imitator_td`、`leek`、`market`；`memoria` 按要求跳过）及七个常驻类游戏（`turtle_soup`、`mbti`、`dnd`、`bdsmtest`、`eco`、`ciyuwu`、`workkk`）。

## 审计边界与数据安全

- 动态测试前逐项确认六个 `data/vendor_saves/<game>/guest:audit1` 目录均不存在。
- 每一步都以新的 `python3 -c` 进程直接调用 `vendor_cmd_adapter.<game>.play()`，没有复用 Python 进程。
- 未重启任何服务，未读取、修改或删除其他玩家的存档内容。
- 测试结束后只删除了六个本次创建的 `guest:audit1` 目录，并逐项确认均已不存在。
- 本报告中的“重启可恢复”指存储介质仍在且未到业务 TTL/定时清理期限；不表示存档永久保留。

## 第一部分：vendor_cmd_adapter 游戏

### arcade — ❌确认问题

**结论**：默认 `new` 和普通游戏指令可以跨进程持久化；但 `new` 允许携带任意 `command`，当该命令是只读命令时，runner 会先删光所有存档却不创建新存档。该路径已动态确认。

1. **new/reset 后是否落盘**：默认会。adapter 默认把 `new` 转成 `enter`（`vendor_cmd_adapter/arcade.py:139-149`），runner 先删除四个 JSON（`:45-50`），而 vendor `enter` 更新 `visits` 并 `_save`（`vendor/claude-arcade/arcade.py:1014-1025`）。但是 `new` 的 `command` 可由调用者覆盖（adapter `:147`）；`look/help/chips` 等只读分支不保存，所以会出现“旧档已删、新档未建”。
2. **冷启动依赖恢复**：主要游戏状态从 `arcade_save.json` 加载（vendor `arcade.py:28-43`），老虎机、21 点和轮盘各自从独立 JSON 恢复；runner 在 import 后重定向全部四个 `_SAVE`（adapter `:34-43`）。子游戏 RNG seed/calls 在各自状态中持久化（如 `vendor/claude-arcade/slots.py:213-226`）。`_TextPicker._history`（vendor `arcade.py:134-153`）不持久化，只影响叙事重复抑制，不影响筹码/局面。
3. **变更后是否写盘**：筹码、进出场、当前机台、兑奖等分支均直接 `_save`；子游戏先同步筹码到子档、执行并保存、再同步回主档（vendor `arcade.py:47-86,1121-1141`）。普通变更链路正常。
4. **失败/无档兜底**：无档返回默认状态但不自动保存（vendor `arcade.py:28-39`）；损坏 JSON 的异常会向上抛出，runner 非零退出，不会静默重置。真正问题是上述 reset + 只读命令组合。

动态证据摘录：

- 标准三进程：①默认 `new` 后 `arcade_save.json` mtime=`01:44:55.988443892`；②`slots help` 后主档及 `slots_save.json` mtime=`01:44:56.136450454`；③新进程 `help` 返回“你正在 slots 前面”，证明 `current_game` 保留。
- 额外确认：`play({action:"new", command:"look", confirm:"true"})` 返回正常场景文本，但随后 `arcade_save.json`、`slots_save.json`、`blackjack_save.json`、`roulette_save.json` 全部不存在。

### burger — ❌确认问题

**结论**：正常链路保存完整且三进程验证通过；但存档损坏/读取失败会无提示地返回全新状态，adapter 随即把新状态写回原文件，属于确认的静默重置进度。

1. **new/reset 后是否落盘**：会。runner 删除 `save.json` 后创建 `fresh_state`，`ensure_started()` 生成订单并调用 `game.save`（`vendor_cmd_adapter/burger.py:47-59,288-296`）。
2. **冷启动依赖恢复**：`game.load()` 恢复 JSON 并补齐 `INITIAL_STATE` 字段（`vendor/noon-burger-shop/game.py:151-161`）；订单、工作台、装修、日志等均在 state。adapter 每进程按“存档路径 + command”初始化 Python `random`（adapter `:294-295`），随后加载 state；没有依赖未恢复的进程级游戏对象。
3. **变更后是否写盘**：`ensure_started()` 先保存；每个分号子命令 dispatch 后无条件 `game.save(state)`（adapter `:294-309`），因此连 `status` 也会刷新 mtime。vendor 内部关键结算也有保存（如 `generate_day` 的 `vendor/noon-burger-shop/game.py:330-362`）。
4. **失败/无档兜底**：`load()` 对 `OSError`/`JSONDecodeError` 直接 `fresh_state()`（vendor `game.py:151-157`），无备份、无错误提示；adapter 的 `ensure_started()` 随即保存（adapter `:47-59`），覆盖坏档。

动态证据摘录：①`new` 后 `save.json` mtime=`01:45:04.220808861`；②`difficulty 忙碌` 后 mtime=`01:45:04.400816841`；③独立进程 `status` 显示“难度：忙碌”，mtime=`01:45:04.600825708`。

### fishing — ⚠️疑似

**结论**：正常变更的持久化、冷启动 RNG 恢复及损坏存档告警都正常；但 adapter 的 `new` 没有调用引擎 `new_game()`，而是把 `new_game [seed]` 当普通文本传给 `cmd()`。当前恰好因先删文件、再由无档兜底创建默认档而能重置，但指定 seed 被忽略且返回“未知指令”。

1. **new/reset 后是否落盘**：会，但路径异常。runner 先删 `fishing_save.json`，再调用 `fishing.cmd(command)`（`vendor_cmd_adapter/fishing.py:30-47`）；adapter 传入的是 `new_game [seed]`（`:79-85`）。引擎 `cmd()` 不识别该命令，但 `_load()` 先创建默认内存状态，末尾 `_save()`（`vendor/ai-fishing-game/engine.py:2076-2091`），所以仍产生文件。
2. **冷启动依赖恢复**：runner 显式把打包引擎的 `fishing.S` 置空并重定向 `_SAVE`（adapter `:18-28`）。引擎从 JSON 恢复 `seed/rngState/rngCalls` 和全部状态（vendor engine `:1228-1295`），随机进度在 state 内。
3. **变更后是否写盘**：`cmd()` 在所有单条/批量指令结束后统一 `_save()`（vendor engine `:2076-2091`）；`new_game()` 本身也保存（`:2093-2097`）。正常指令有保证。
4. **失败/无档兜底**：无档创建默认状态；损坏档会重命名为 `.corrupt`、创建新状态并把明确警告附到输出（`:1243-1258,1296-1303`），不会静默。写失败也会告警。

动态证据摘录：①带 `seed=12345` 的 `new` 返回“未知指令「new_game」”，但文件存在，mtime=`01:45:13.101202554`，状态实际是默认 seed；②`cast 1` 后 mtime=`01:45:13.369214436`；③独立进程 `status` 显示“回合 1、总抛竿 1、鱼饵 4”，mtime=`01:45:13.549222416`。

### imitator_td — ✅正常

1. **new/reset 后是否落盘**：会。runner 通过环境变量把 session/records 指到玩家目录，reset 时删除旧文件（`vendor_cmd_adapter/imitator_td.py:18-31`）；vendor `cmd()` 结束时总是保存 session 和 records（`vendor/random-imitator-td/random_imitator_td/engine.py:62-81`）。
2. **冷启动依赖恢复**：session 包含 config、完整 state、RNG snapshot（含每个 stream 的 `random.Random` state）、wave schedule、事件日志、计数器、复盘和可见实体集合（vendor engine `:569-607`；`game/randomizer.py:76-94`）。没有发现关键运行时依赖遗漏。
3. **变更后是否写盘**：顶层 `cmd()` 对最多 12 条命令处理完后统一 `_save_session/_save_records`（vendor engine `:62-81,395-417`）；部分查看路径还会提前保存更新后的观察状态（`:278-288`）。
4. **失败/无档兜底**：无档会创建 fresh session 并立即保存（`:347-357`）；主 session JSON 损坏会抛异常而非静默重置。records 损坏会退回空 records（`:401-409`），只影响无尽纪录，不会重置当前局。

动态证据摘录：①`new level=1 seed=audit-seed` 后 session/records 均存在，mtime=`01:45:24.349701233`；②`note audit-marker` 后 session 从 117 bytes 增至 4197 bytes，mtime=`01:45:24.513708504`；③独立进程 `note` 返回 `- audit-marker`，mtime=`01:45:24.685716130`。

### leek — ❌确认问题

**结论**：正常命令逐条原子写盘且三进程验证通过；但损坏或旧版本存档会被备份后无提示重建，玩家看到的是全新进度，属于静默重置。

1. **new/reset 后是否落盘**：会。runner 删除主档/tmp/bak 后调用 `leek.cmd("new_game ...")`（`vendor_cmd_adapter/leek.py:16-27,51-68`）；vendor `new_game()` 创建状态并 `_save`（`vendor/leek/leek.py:2438-2453`）。
2. **冷启动依赖恢复**：状态内持久化 `rng_state/rng_calls`，每次用 `_rng(state)` 重建 RNG（vendor `leek.py:724-740`）；资金、行情、持仓、周期、挂单、日志等均在 JSON，没有关键进程全局依赖。
3. **变更后是否写盘**：`cmd()` 每个子命令后 `_save(state)` 并重新 `_load()`，末尾再次保存（vendor `leek.py:1303-1322,1363-1377`）。`_save` 使用 tmp + `os.replace` 原子替换（`:724-733`）。
4. **失败/无档兜底**：无档自动创建；JSON 损坏或版本过旧会复制为 `.bak` 后创建默认状态并保存（`:702-722`），输出没有告警。备份降低了不可逆损失，但当前进度仍会静默显示为新局。

动态证据摘录：①`new seed=12345` 后 mtime=`01:45:33.206093863`；②`wait 1` 后 mtime=`01:45:33.342099893`；③独立进程 `status` 显示“第 2 天、操作 1 次”，mtime=`01:45:33.482106100`。

### market — ❌确认问题

**结论**：新局、普通变更和 RNG/当日缓存的保存均正常；但若干“等待下一条指令选择”的运行时对象没有进入 `to_dict/from_dict`。在生产的一命令一子进程模型下，这些选择上下文在返回提示后立即消失，使下一进程无法完成互动。

1. **new/reset 后是否落盘**：会。runner 删除 `market_save.json` 并直接调用 `market_engine.new_game(seed)`（`vendor_cmd_adapter/market.py:19-36`）；`new_day()` 完成后 `self.save()`（`vendor/shangzhuochifan/market_engine.py:552-569,790-822`）。
2. **冷启动依赖恢复**：核心状态恢复较完整：save 中包含 `rt_rng_state`、季节/天气/时段、厨房、盘子、灾害修饰、旅程文本和摊位缓存（vendor `market_engine.py:266-353`），`from_dict` 恢复 RNG 和这些字段（`:369-548`）。但 `_pending_chain_step`、`_pending_interaction`、`_pending_help`、`_pending_rare`、`_help_cooldown` 及动态 `_rare_found_*` 只存在于对象属性（例如 `:1248-1260,4582,5860-5898,6022-6097`），不在上述序列化/恢复清单中。这是确认的跨进程上下文缺失。
3. **变更后是否写盘**：顶层 `MarketGame.cmd()` 会在每条非只读命令后统一 `self.save()`，批量命令只要含一条非只读也会保存（`:5546-5569`）；多个关键分支还主动保存。普通变更有保证。
4. **失败/无档兜底**：`load()` 无档时用空 dict 迁移；JSON 解析错误没有吞掉，会让 runner 报错，不会静默覆盖（`:356-367`）。主要风险不是失败兜底，而是保存模式漏掉 pending 运行时状态。

动态证据摘录：①`new seed=12345` 后 mtime=`01:45:42.886523026`；②`记得 她爱吃土豆` 后 mtime=`01:45:43.042529942`；③独立进程 `口味` 显示“土豆 — 她爱吃土豆”，查询未改 mtime。额外执行 `买袋子` 后 mtime 更新，下一进程 `状态` 显示“花了1元”，验证统一保存入口本身正常。

## 第二部分：常驻类游戏代码审计

### turtle_soup — ✅正常

- **状态位置**：SQLite，默认 `turtle-soup/backend/turtle_soup.db`，可由 `TURTLE_SOUP_DB` 覆盖（`turtle-soup/backend/database.py:8-10`）。房间、题面/汤底、状态、胜者、日志、提示、个人揭底、记事板、玩家统计和 presence 都有表（database `:193-349`）。
- **落盘时机**：通用 `execute()` 每次 DML 都 commit（`:60-67`）；猜题成功的房间结束/胜者/统计在显式事务中 commit（`routers/game.py:342-386`）。提问、猜测、提示及揭底均先写 DB，再广播。
- **重启恢复**：业务状态均从 SQLite 查询，服务 lifespan 只初始化 schema 和调度器（`backend/main.py:21-26`），重启后可恢复未过清理期限的房间和日志。
- **仅内存状态**：`routers/game.py:17-34` 的 per-room asyncio locks、`sse.py:13-20` 的连接队列和 leaderboard cache 是并发/推送/展示缓存，不是权威游戏状态；重启会断 SSE、清锁和缓存，但客户端重连后可从 DB 恢复。定时策略会把 48 小时无活动房间结束，并在结束 24 小时后删除（`scheduler.py:50-85`），属于明确保留策略。

### mbti — ✅正常

- **状态位置**：`/opt/cedartoy/data/sessions.db`（`mbti/handler.py:22-26`），进行中状态在 `test_sessions`，完成结果在 `test_results`（`:547-573`）。
- **落盘时机**：start 使用 UPSERT；每题/每批更新 `current_question + answers + last_active`（`:179-217,234-282,305-357`）；完成时保存评分详情并删除进行中 session（`:388-425`）。`sqlite3.Connection` context 成功退出时 commit。
- **重启恢复**：mode、题号和全部答案都在 DB，题库/评分函数是静态代码，重启后可继续；结果也可查询。
- **仅内存关键状态**：未发现。24 小时未活动 session、48 小时结果会在下一次调用时清理（`:576-584`），是明确 TTL。

### dnd — ✅正常

- **状态位置**：同一 `sessions.db`（`dnd/handler.py:18-22`），复用 `test_sessions/test_results`，以 `(player_id, game)` 隔离。
- **落盘时机**：start UPSERT；每题/批次 UPDATE（`:155-186,193-283`）；完成时 INSERT/UPDATE 结果并 DELETE session（`:305-327`）。
- **重启恢复**：mode、current_question、answers 全在 DB；问题与计分定义来自静态模块，可在重启后重建展示和最终结果。
- **仅内存关键状态**：未发现。与 MBTI 一样有 24h/48h TTL（`:453-455`）。

### bdsmtest — ✅正常

- **状态位置**：`sessions.db`（`bdsmtest/handler.py:20-24`）。除题号/答案外，还把原站 `rauth`、完整 questions、`pdata` 保存到 `test_sessions`（`:218-243,517-533`）；结果 scores/rid 存 `test_results`（`:361-393`）。
- **落盘时机**：start UPSERT；逐题/批量 UPDATE。最后一题在请求原站算分前显式 commit，算分失败时保留完整 session 以便重试（`:275-290,324-331,361-375`）。
- **重启恢复**：本地恢复所需字段完整；服务重启后可继续提交或重试收尾。最终算分仍依赖外部原站及其凭证有效性，这属于外部可用性而非本地状态遗漏。
- **仅内存关键状态**：未发现。24h/48h TTL 明确（`:595-603`）。

### eco — ✅正常

- **状态位置**：`sessions.db` 的 `eco_sessions.save_data` JSON（`eco_adapter/handler.py:23-28,686-703`）。engine 原本的文件保存被禁用，由 adapter 接管（`:13-20`）。state 内包含 seed 与 `rng_state`（`eco/engine.py:2192-2199,2627-2634`）。
- **落盘时机**：新局生成后立即 UPSERT；每条命令先 `BEGIN IMMEDIATE`，在同一事务内 load → engine cmd → UPDATE（adapter `:461-498`）；人类网页动作也使用同样的早期写锁并在成功时 UPDATE（`:501-536`）。
- **重启恢复**：每次命令从 DB JSON 恢复，`_migrate` 后赋给 engine `_STATE`，执行后重新序列化，并在 finally 清掉 `_STATE`（`:539-567`）。重启可恢复 30 天 TTL 内的完整状态。
- **仅内存关键状态**：`_STATE` 是临时工作对象，`_ENGINE_LOCK` 只负责进程内串行；权威状态在 DB。没有发现未序列化的关键进度。

### ciyuwu — ⚠️疑似

- **状态位置**：`sessions.db` 的 `ciyuwu_sessions.save_data`（当局完整 snapshot，含 `_rng_state`）和 `meta_data`（跨局进度）（`ciyuwu_adapter/handler.py:1-14,48-55,567-582`）。上游单机文件保存和 `DarkWorld` meta 文件保存均被屏蔽（`:40-46`）。
- **落盘时机**：新局立即 UPSERT，同时保留旧 meta（`:230-264`）；每条命令读取两层状态、恢复 meta、执行并 UPDATE 两层 JSON（`:360-403`）。上游 snapshot 保存 `_det_rng._state`，restore 时重建（`vendor/ci-yu-wu/engine.py:135-178`），adapter 的 engine lock 覆盖 restore/cmd/snapshot（adapter `:68-70,406-422`）。
- **重启恢复**：单请求串行场景可恢复当局、PRNG 和跨局 meta，30 天 TTL 后主动清理。
- **仅内存/并发风险**：没有发现依赖未恢复的关键全局；但 `_ENGINE_LOCK` 只包 engine 执行，DB 的 SELECT 在锁前、UPDATE 在锁后，而且没有像 eco 一样 `BEGIN IMMEDIATE`。两个并发请求可能同时读到同一旧快照，依次计算后由后一次 UPDATE 覆盖前一次进度。该风险由代码结构确认，但本次按要求未对常驻服务做并发动态测试，故标为疑似。

### workkk — ❌确认问题

- **状态位置**：每玩家 JSON：`/opt/cedartoy/data/vendor_saves/workkk/<player_id>/game_state.json`，另有 `_STATE_CACHE` 和临时全局 `_s`（`vendor/workkk/main.py:37-45,527-578`）。server 代理用 `X-Player-Id` 传身份（`server.py:2933-2960`）。
- **落盘时机**：入职、登记、挑战阶段、每个正常 work action、购买、前端 ack 和 reset 都调用 `_save_state()`（workkk `main.py:581-593,632-638,689-737,840-870,1011-1021,1268-1310`）。请求通过 `_STATE_LOCK` 包住整段玩家 context（`:564-578,1231-1240`）。
- **重启恢复**：正常 JSON 会在首次访问时加载、与默认字段合并并缓存（`:544-562`），所以正常重启可恢复。
- **仅内存/确认问题**：OAuth `_clients/_codes/_tokens` 只在内存（`:32-35`），但部署默认禁用 OAuth，不是玩家游戏进度。更严重的是 `_save_state()` 捕获所有写入异常后只打印日志、仍向玩家返回成功（`:519-525`）；`_load_state()` 捕获所有解析/读取异常后只打印日志并继续返回默认状态（`:544-561`），后续任一保存会把默认状态写回原文件。文件写入也不是 tmp + rename 原子替换（`:519-523,539-542`）。这符合“当次成功、状态未保存”及“加载失败静默重置”两种已知风险模式。

## 风险汇总

| 游戏 | 结论 | 风险级别 | 核心结论 |
|---|---|---:|---|
| arcade | ❌确认问题 | 高 | `new` 可用只读 command；先删档后不建档，已动态确认 |
| burger | ❌确认问题 | 高 | 损坏/读取失败静默 fresh，并立即覆盖原档 |
| fishing | ⚠️疑似 | 中 | 状态保存正常，但 adapter `new` 走错入口，seed 被忽略且返回未知指令 |
| imitator_td | ✅正常 | 低 | 完整 session/RNG 快照，每次 cmd 统一保存 |
| leek | ❌确认问题 | 中 | 损坏/旧版档备份后静默重建；可恢复备份但当下无告警 |
| market | ❌确认问题 | 高 | pending 选择/事件上下文未序列化，一命令一进程下后续选择丢失 |
| turtle_soup | ✅正常 | 低 | 权威业务状态全在 SQLite；内存仅锁、SSE 和缓存 |
| mbti | ✅正常 | 低 | 进行中答案和结果均在 SQLite，受明确 TTL 管理 |
| dnd | ✅正常 | 低 | 进行中答案和结果均在 SQLite，受明确 TTL 管理 |
| bdsmtest | ✅正常 | 低 | 题目、答案、原站凭证和结果均入 DB；外部算分失败可重试 |
| eco | ✅正常 | 低 | DB 事务覆盖完整 load→mutate→save，RNG 在 state |
| ciyuwu | ⚠️疑似 | 中 | 单请求持久化完整；并发请求可能旧快照覆盖新进度 |
| workkk | ❌确认问题 | 高 | 读写异常被吞、默认状态继续运行；非原子写，可能静默丢档 |

按优先级建议先处理 `arcade` 的 destructive reset、`market` 的 pending 上下文、`burger/workkk` 的静默重置/写失败；随后处理 `ciyuwu` 的事务边界、`fishing` 的 new 入口和 `leek` 的显式告警。按要求，本次只记录问题，没有修改任何业务代码。

## 修复记录

修复时间：2026-07-17（Asia/Shanghai）  
动态验证账号：`guest:audit2`

### 1. arcade — 已修复

- **改动**：仅修改 `vendor_cmd_adapter/arcade.py` 的 reset runner。删档后固定先执行 `enter` 创建主档，并把 slots、blackjack、roulette 的默认状态分别落盘；自定义 command 随后执行。默认 `enter` 不会重复执行。
- **验证证据**：执行 `play({"action":"new", "command":"look", "confirm":"true", "player_id":"guest:audit2"})` 返回正常场景文本；随后 `arcade_save.json`（163 bytes）、`slots_save.json`（171 bytes）、`blackjack_save.json`（221 bytes）、`roulette_save.json`（138 bytes）全部存在，且均成功解析为 JSON object。
- **静态校验**：`python3 -m py_compile vendor_cmd_adapter/arcade.py` 通过。

### 2. market — 已修复

- **改动**：修改 `vendor/shangzhuochifan/market_engine.py`，存档版本升至 v11；把 `_pending_chain_step`、`_pending_interaction`、`_pending_help`、`_pending_rare`、`_help_cooldown` 和动态 `_rare_found_*` 标记纳入 `to_dict/from_dict`。旧 v10 或缺字段存档按 `None`/空 dict 恢复；重复加载前会清除旧的动态 rare 标记，避免对象残留。
- **验证证据**：独立进程 A 冷加载 `guest:audit2` 后注入上述全部 pending/动态字段并保存；独立进程 B 冷加载后逐字段断言完整，随后执行 `介入 1`，正确返回 `audit interaction resolved`。另将六个新字段从存档中删除并标记为 v10，恢复结果均为安全默认值。
- **静态校验**：`python3 -m py_compile vendor/shangzhuochifan/market_engine.py` 通过。

### 3. workkk — 已修复

- **改动**：修改 `vendor/workkk/main.py`。所有写盘统一改为同目录 `.tmp` 写入、flush/fsync 后 `os.replace`；失败清理 tmp 并抛出 `PersistenceError`，MCP 返回明确 error，REST 返回 HTTP 500 JSON error，不再报告默认成功。加载到损坏 JSON/非 object 时用 `os.replace` 备份为 `game_state.json.corrupt`，为当前玩家记录一次性告警，并在当次工具或 REST 结果附 `存档告警`；其他读取错误不再被宽泛吞掉。
- **验证证据（坏 JSON）**：写入 `{"broken":` 后冷加载，原内容完整保存在 `game_state.json.corrupt`；当次 MCP 文本包含“检测到损坏存档……本次使用全新状态”，重建后的 `game_state.json` 可正常解析，且无残留 tmp。
- **验证证据（只读/原子失败）**：把目标指向只读 `/sys/cedartoy-audit2/game_state.json`，MCP 返回 error：`存档写入失败，本次操作未确认保存：[Errno 30] Read-only file system`。另模拟 `os.replace` 抛 `PermissionError`，确认异常未被吞、旧文件内容保持不变、tmp 已清理。
- **静态校验**：`python3 -m py_compile vendor/workkk/main.py` 通过。

### 4. burger — 已修复

- **改动**：仅修改 `vendor_cmd_adapter/burger.py` runner；调用 vendor `load()` 前先解析 `save.json`。解析/读取失败或顶层不是 JSON object 时，先将原档原子改名为 `save.json.corrupt`，再用 fresh state 重建，并把明确告警置于当次输出首行；未修改 vendor 引擎。
- **验证证据**：手工写入坏档 `{"broken":` 后调用一次 `status`，输出首行为 `⚠️ 检测到损坏存档，已备份为 save.json.corrupt，本次已重建新档。`；备份内容与坏档完全一致，新 `save.json` 可解析，店名为“午间汉堡铺”且生成 5 个订单。第二次冷加载正常且不重复告警。
- **静态校验**：`python3 -m py_compile vendor_cmd_adapter/burger.py` 通过。

### 收尾

- 对四个涉及文件再次统一运行 `python3 -m py_compile`，全部通过。
- 已删除 `data/vendor_saves/{arcade,market,workkk,burger}/guest:audit2`；最终全局查找 `data/vendor_saves/**/guest:audit2` 结果为 0。
- 未重启任何服务，未修改或删除其他玩家存档。

### 5. fishing — 已修复

- **改动**：仅修改 `vendor_cmd_adapter/fishing.py`。reset runner 删档后直接调用 `fishing.new_game(seed)`；未传 seed 时调用无参数的 `fishing.new_game()`，不再把 `new_game [seed]` 作为文本交给 `cmd()`。
- **验证证据**：独立进程调用 `play({"action":"new", "seed":12345, "player_id":"guest:audit3"})`，返回 `已重开新局（种子 12345）`，无“未知指令”；`fishing_save.json` 中 `seed=12345`。第二个进程执行 `cast 1`，第三个进程执行 `status` 并从存档确认 `turn=1`、`stats.total_casts=1`、`basic_worm=4`，证明 cast 状态跨进程保留。
- **静态校验**：`python3 -m py_compile vendor_cmd_adapter/fishing.py` 通过。

### 6. ciyuwu — 已修复

- **改动**：仅修改 `ciyuwu_adapter/handler.py`。`ciyuwu_new` 与 `_run_player_command` 均在 schema 初始化提交后、读取玩家记录前执行 `BEGIN IMMEDIATE`，使完整 read → engine mutate → write 在同一写事务内串行化；现有 `_ENGINE_LOCK` 保持不变。
- **单请求回归**：以 seed 12345 为 `guest:audit3` 开局，独立进程执行“新角”，再由另一独立进程调用 info/status 并直接读取 SQLite，均确认 `phase=creation`（状态栏为“创建角色”），证明单请求与冷加载恢复正常。
- **并发验证**：临时两线程脚本用 barrier 同时向同一玩家发送“调 痛 喉”和“调 怕 壳”。两条命令均返回成功，最终同一行 `save_data.word_chambers` 同时为 `{"痛":"喉","怕":"壳"}`，无丢失更新；脚本执行后已删除。
- **静态校验**：`python3 -m py_compile ciyuwu_adapter/handler.py` 通过。

### 7. leek — 已修复

- **改动**：仅修改 `vendor_cmd_adapter/leek.py` runner，未修改 vendor。非 reset 调用前预解析主档，校验顶层为 JSON object 且 `version >= leek._SAVE_VERSION`；解析、读取、版本或类型异常时先把原档原子改名为 `leek_save.json.bak`，再交给引擎按无档路径重建，并把包含备份名和具体原因的告警放在输出首行。
- **验证证据**：手工写入坏档 `{"broken":` 后调用一次 `status`，输出首行为 `⚠️ 存档告警：已备份为 leek_save.json.bak（原因：Expecting value: line 2 column 1 (char 11)），本次已重建新档。`；`.bak` 与原坏档逐字节一致（均为 11 bytes），新主档可解析且 `version=2`、`day=1`。
- **静态校验**：`python3 -m py_compile vendor_cmd_adapter/leek.py` 通过。

### 本轮收尾（guest:audit3）

- 对本轮三个涉及文件统一再次运行 `python3 -m py_compile`，全部通过；`git diff --check` 通过。
- 已删除 `data/vendor_saves/fishing/guest:audit3` 与 `data/vendor_saves/leek/guest:audit3`，全局递归查找 `data/**/guest:audit3` 结果为 0。
- 已删除 `sessions.db` 中 `guest:audit3` 的 ciyuwu 测试行，并确认 `ciyuwu_sessions`、`eco_sessions`、`test_sessions`、`test_results` 中该 player_id 计数均为 0。
- 全程未重启任何服务，未修改或删除其他玩家存档。
