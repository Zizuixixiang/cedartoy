# CedarToy 平台全游戏持久化最终验收报告

验收时间：2026-07-17 02:30–02:50（Asia/Shanghai）
验收性质：独立最终验收（不以此前审计结论为前提，14 游戏一视同仁）；只验收、只记录，未修改任何业务代码
动态测试账号：`guest:auditfinal`（验收前确认全平台无该 id 任何残留；验收后已全部删除并确认为 0）
方法：
- vendor 子进程系 7 游戏：动态双进程验收。每步用独立 `python3 -c` 进程直接调用 `vendor_cmd_adapter.<game>.play()`：①new 开局 ②独立进程执行一条会改状态的指令 ③再独立进程查询确认变更保留；每步核对存档文件存在且 JSON 可解析。
- 常驻系 7 游戏：代码级复核（三路独立复核）+ 只读冒烟（HTTP 探活 / 只读 SQLite 探查），未制造持久业务数据。
- 另核查 supervisord 服务状态与日志、外层及九个 vendor 嵌套仓库 git 完整性。
- 全程未重启任何服务。

---

## 第一部分：vendor 子进程系（动态双进程验收）

### 1. memoria — ✅ PASS（覆盖第 1、2、5 关）

**第 1 关（蓝玫瑰庄园）**
- ①new：返回完整开局文本；`level1/detective_save.json`（841 bytes）存在且 JSON 可解析，`current_scene=banquet_hall`。
- ②独立进程 `go 厨房`：返回厨房场景描述，状态栏 `{"loc":"厨房", "turn":1}`。
- ③再独立进程 `status`：显示「📍 当前场景：厨房」「🎯 回合数：1」；直读存档 `current_scene=kitchen`。变更跨进程保留。

**第 2 关（午夜特快）**
- ①new：返回「新游戏已开始。难度：普通」；`level2/detective_save_l2.json` 可解析，`current_scene=victim_compartment`、`difficulty=normal`。
- ②独立进程 `go 餐车`：成功移动，状态栏「📍 餐车 | 🕐 剩余 119」。
- ③再独立进程 `status`：「📍 当前位置：餐车」「📋 回合数：2」；存档 `current_scene=dining_car`。

**第 5 关（档案室终点，重点验证 read）**
- ①new：正常开局文本，`level5/detective_save_l5.json` 可解析。
- ②独立进程 `read 卷宗`：**正常返回叙事文本，无 NameError**（「你已经到达终点了……📖 已阅读：1 件」）。
- ③再独立进程 `status`：「阅读进度：1/22 件」；存档 `items_read={"卷宗": true, "__count_卷宗": 1}`。阅读进度跨进程保留。

### 2. arcade — ✅ PASS（含两项回归复核）

- ①默认 `new`（不带 command）：输出为单次进场场景文本，无重复；存档 `visits=1`，**证明 enter 只执行一次，未重复执行**。四个存档 `arcade_save.json`（163B）、`slots_save.json`（171B）、`blackjack_save.json`（221B）、`roulette_save.json`（138B）全部存在且 JSON 可解析。
- 回归复核（原高危路径）：`new` + 只读 `command="look"`（confirm=true）执行后，**四个存档全部仍存在且可解析**——原「删档后不建档」缺陷未复现。
- ②独立进程 `slots help`：存档 `current_game=slots`。
- ③再独立进程 `help`：返回「你正在 slots 前面」，机台状态跨进程保留。
- 附注：`cmd` 路径 `buy 500` 被守卫拦截（「筹码只能由人类在网页端发放」），系刻意设计，非缺陷。

### 3. burger — ✅ PASS（含不误报坏档回归）

- ①new：返回「欢迎来到『午间汉堡铺』」及首单；`save.json` 可解析，`shop_name=午间汉堡铺`、`difficulty=普通`。
- ②独立进程 `difficulty 忙碌`：返回「难度已设置为：忙碌」。
- ③再独立进程 `status`：显示「难度：忙碌」；存档 `difficulty=忙碌`。
- 回归复核：`status` 输出首行**无任何 ⚠️ 坏档告警**；存档目录仅 `save.json`，**未误生成 `save.json.corrupt`**。对正常存档不误报。

### 4. fishing — ✅ PASS（带 seed 与不带 seed 两种 new 均正常）

- ①new（seed=12345）：返回「已重开新局（种子 12345）」，**无『未知指令』**；存档 `seed=12345`。
- ①'new（不带 seed，confirm=true）：返回「已重开新局（种子 2654435769）」，随机 seed 正常写入存档。
- ②独立进程 `cast 1`：钓到「银梭鱼 · 首次收录 +20点」。
- ③再独立进程 `status`：「回合 1 ｜ 图鉴 1/81 ｜ 总抛竿 1 ｜ 普通蚯蚓×4」；存档 `turn=1`、`stats.total_casts=1`、seed 保持不变。变更跨进程保留。

### 5. leek — ✅ PASS（含不误报坏档回归）

- ①new（seed=12345）：返回「新局已开（🌱散户 · 种子 0x3039 · 本金 1000 元）」；`leek_save.json` 可解析，`version=2`、`day=1`。
- ②独立进程 `wait 1`：正常推进「第 2 → 2 天」并输出行情。
- ③再独立进程 `status`：「交易日：第 2 天 ｜ 操作：1 次」；存档 `day=2`、`turn=1`。
- 回归复核：输出首行为正常持仓状态（无 ⚠️ 告警）；目录中**无误生成的 `leek_save.json.bak`**。对正常存档不误报。

### 6. market — ✅ PASS（含存档 v11 往返回归）

- ①new（seed=12345）：正常开局；存档 `save_version=11`，今晚新增的六个字段 **`_pending_chain_step`、`_pending_interaction`、`_pending_help`、`_pending_rare`、`_help_cooldown`、`_rare_found_flags` 全部在存档中**。
- ②独立进程 `记得 她爱吃土豆`：返回「记住了：她爱吃土豆」。
- ③再独立进程 `口味`：显示「土豆 — 她爱吃土豆」，记忆跨进程保留。
- 正常指令流程：独立进程 `买 春笋` 花 7.1 元；再独立进程 `状态` 显示「花了7.1元 | 剩12.9元 | 篮子：1样」，诊断行显示「存档v11」。每条指令都是一次完整的 v11 from_dict → to_dict 往返，均正常。

### 7. imitator_td — ✅ PASS

- ①new（level=1 seed=auditfinal）：返回「新游戏: lv1 seed=auditfinal」；`random_imitator_td_save.json` 与 `random_imitator_td_records.json` 均存在且 JSON 可解析。
- ②独立进程 `note auditfinalmarker`：返回「复盘已记录」，session 存档增至 4201 bytes。
- ③再独立进程 `note`：返回「- auditfinalmarker」；存档中 seed=auditfinal 保留、marker 存在。变更跨进程保留。

---

## 第二部分：常驻系（代码级复核 + 只读冒烟）

### 8. turtle_soup — ✅ PASS

- 权威状态全在 `turtle-soup/backend/turtle_soup.db`：rooms/game_logs/players/room_notes/room_answer_reveals/room_hint_views/room_presence 等表（`database.py:199-344`）。
- 写入统一走 `database.py:60-67 execute()` 逐条 commit；猜对收尾（房间 finished + winner + 全员统计）在同一连接单事务原子提交（`game.py:371-386`），均在 HTTP 返回前完成。
- 内存态仅并发锁（`game.py:17-18`）、SSE 队列（`sse.py:14`）与后台自动提示任务，均非权威状态，重启可重建；judge 网络失败走系统提示且不写半条脏数据。TTL 策略明确（`scheduler.py`：48h 不活跃结束、结束 24h 删除）。
- 冒烟：`GET http://127.0.0.1:8012/soup/api/rooms/` → HTTP 401「未登录」（服务存活、鉴权正常）；`turtle_soup.db` 只读 `PRAGMA integrity_check=ok`，rooms 37 行。
- 非阻断观察：提问日志与玩家统计为两次独立 commit，极端崩溃间隙可丢一次统计增量（非对局权威状态）。

### 9. mbti — ✅ PASS

- 进行中状态在 `sessions.db.test_sessions`（answers JSON + current_question），结果在 `test_results`；逐题 UPDATE 在 with 块退出时 commit（`handler.py:271-278`）；收尾「存结果 + 删 session」与末题 UPDATE 同事务原子（`:388-393`）。评分纯本地，无部分失败风险。异常冒泡为 isError，不静默重置。TTL：session 24h / 结果 48h。

### 10. dnd — ✅ PASS

- 与 mbti 同构同库，收尾同样原子（`handler.py:305-327`），无内存态权威状态，异常不静默。历史日志中的 `descriptions.py` SyntaxError（U+3001）已在当前代码中不存在（三个文件 `ast.parse` 全部通过，且运行中服务已正常注册 dnd）。

### 11. bdsmtest — ✅ PASS

- 原站票据 rauth/pdata、题目、答案全部持久化入表（`handler.py:218-243`）；**末题先显式 `conn.commit()` 落盘答案再调原站算分**（`:286-290`），算分失败保留 session 可重试（`:276-277`），结果保存与 session 删除同事务原子（`:361-375`）。跨重启可续答/重试收尾。TTL 24h/48h（票据随 session 过期属预期设计）。

### 12. eco — ✅ PASS

- `BEGIN IMMEDIATE` 事务完整覆盖 load→mutate→save：`eco_adapter/handler.py:472`（命令）、`:512`（human_action，仅 `result.ok` 才写回）、`:249`（new，且 `_engine_new` 在事务外持锁更短）。
- `try/finally` 清 `_STATE`（`:550-551`、`:565-566`），engine 抛异常也不会串档；所有写路径 with 块异常自动 rollback。与 ciyuwu 新改动无相互死锁（加锁顺序全局一致）。

### 13. ciyuwu — ✅ PASS（附 1 条非阻断 CONCERN）

- **BEGIN IMMEDIATE 改动核实**：`ciyuwu_adapter/handler.py:235-238`（ciyuwu_new）与 `:365-370`（_run_player_command）均为「`_init_db` → `commit()` 清隐式事务 → `BEGIN IMMEDIATE`」，read→engine mutate→write 全程在同一写事务内，形态正确。
- **无死锁**：IMMEDIATE 立即取 RESERVED 写锁，避免了 DEFERRED 升级死锁；两个并发写事务只会一个等待至 busy_timeout（5s）后得 SQLITE_BUSY，非死锁。`_ENGINE_LOCK` 获取点仅 `:414`、`:426` 两处，且都在 `BEGIN IMMEDIATE` 之后——「DB 写锁 → 引擎锁」顺序全局一致（与 eco 相同、各自独立引擎锁），无 lock-ordering 环。
- **事务闭合**：所有路径 `with _connect()`，正常退出 commit、异常/早退（-32001 无档、-32603 解析失败、engine 异常）自动 rollback；引擎锁 with 包裹无泄漏。
- **CONCERN（长事务尾部风险，非阻断）**：engine 计算在写事务内执行，`sessions.db` 为回滚日志模式（journal_mode=delete、busy_timeout=5000ms），持锁期间会阻塞同库 mbti/dnd/bdsmtest/eco/公告 的写入。引擎无 sleep、批量指令上限 20 步，正常单指令毫秒级、远低于 5s；但**分号串联指令无条数上限**（`vendor/ci-yu-wu/engine.py:329`）且 command 参数无长度上限，极端超长串联理论上可让邻居游戏偶发 busy。正常玩法不触发。建议（后续可选）：加串联条数上限 / engine 计算移出事务 / sessions.db 改 WAL。
- 次要提示：`ciyuwu_save import`（`:343-356`）为单条 upsert，未加 BEGIN IMMEDIATE，风险低但与新模式不完全对齐。

### 14. workkk — ✅ PASS（附 4 条非阻断建议）

- **原子写完整**：唯一写实现 `_write_state_file`（`vendor/workkk/main.py:544-558`）：`.tmp` 写入 → flush → `os.fsync` → `os.replace`，失败清理 tmp 并抛 `PersistenceError`。全文件排查确认**无任何绕过原子写的散点写盘**（`open(...,"w")` 仅此一处）；所有 `_save_state` 都在 `_STATE_LOCK` 下串行。
- **失败传播链路完整**：MCP 工具路径保存失败 → JSON-RPC error `-32000`（`main.py:1138-1149`）；加载期失败冒泡 → HTTP 500（`:52-54`）；REST（/shop/buy、/ack-*、/reset）失败同样 500。**未发现任何一条「保存失败仍报成功」的路径**。
- **坏档告警链路完整**：损坏 JSON/非 object → `os.replace` 备份为 `game_state.json.corrupt`（备份失败则抛 PersistenceError 不建新档）→ 按 player_id 记录一次性告警 → `_attach_persistence_warning` 以同一 `_ACTIVE_PLAYER_ID` 消费，**不串玩家、不重复、延迟不丢失**（`main.py:568-594`、`:561-566`）。
- **server.py 代理不吞错**：MCP 代理把上游 error 原样透传且 `succeeded=False`（`server.py:2716-2721`、`:2963-2966`）；人类大屏代理忠实转发上游状态码（`:3936`、`:3947`），500 不会变 200。
- 冒烟：`GET http://127.0.0.1:8770/status?player=205` → 200 正常 JSON。
- 非阻断建议：①tmp 名固定 `{path}.tmp`，安全性依赖当前单 worker 部署（supervisord 配置确认无 `--workers`），建议加进程后缀；②未 fsync 父目录（断电最坏丢最后一次保存，不损坏）；③`PermissionError` 等其他读错误会响亮 500 但不走友好告警分支（行为安全、仅不对称）；④`.corrupt` 固定名二次损坏会覆盖前一份备份。

---

## 第三部分：supervisord 服务与日志

- `supervisorctl status`：**cedartoy RUNNING**（pid 818999，2026-07-17 02:08:08 启动）；**cedartoy-workkk RUNNING**（pid 817741，02:17:16 启动）。另 turtle-soup 服务 RUNNING（7 天+）。
- `/var/log/cedartoy.err.log` 最后写入时间为 **2026-07-16 22:41:59**，早于两服务今晚重启时刻——**重启后零新增 Traceback**。
- 历史异常归类（均在重启前）：52× `Address already in use`（按要求忽略的 7 月 5 日遗留）；27× BrokenPipe + 9× JSONDecodeError（客户端发送非法请求体后提前断连的探测流量，非业务异常）；10× ConnectionReset；17× `ThreadPoolHTTPServer ... executor` AttributeError 与 4× dnd `descriptions.py` SyntaxError（历史版本代码所致，当前代码三文件 ast.parse 全部通过，运行中服务正常）；3× TimeoutError。
- workkk 日志：err.log 仅正常 uvicorn 启停信息；out.log（4MB 访问日志）**0 个 Traceback**，尾部均为 200 OK。
- 冒烟汇总：根 MCP 端点 `POST http://127.0.0.1:8002/` tools/list → 200，`list_games` 返回完整 14 游戏清单；`sessions.db` 与 `turtle_soup.db` 只读 `PRAGMA integrity_check` 均为 ok。

## 第四部分：git 完整性

- 外层仓库 `git status --porcelain` 仅两条允许的未跟踪项：`?? .claude/`、`?? turtle-soup/*.db`。无未提交修改。
- 九个 vendor 嵌套仓库（ai-fishing-game、ci-yu-wu、claude-arcade、leek、Memoria-Station、noon-burger-shop、random-imitator-td、shangzhuochifan、workkk）工作区**全部 clean**——今晚涉及 vendor 的修复（market_engine.py、workkk/main.py）均已在各自嵌套仓库内提交。

## 第五部分：测试数据清理

- 删除前清点：`guest:auditfinal` 存档目录共 7 个（memoria、arcade、burger、fishing、leek、market、imitator_td），与本次测试完全一致。
- 已全部删除；`find /opt/cedartoy/data -name '*auditfinal*'` 结果为 **0**。
- `sessions.db`（test_sessions/test_results/eco_sessions/ciyuwu_sessions）与 `turtle_soup.db` 全表按 player/user 列检索 `auditfinal` 均为 **0 行**（本次常驻系只做只读冒烟，本就未写入）。
- 全程未重启服务，未触碰其他玩家数据（workkk 冒烟仅只读查询已存在玩家的 /status）。

---

## 总表与整体结论

| # | 游戏 | 验收方式 | 结论 | 备注 |
|---|---|---|---|---|
| 1 | memoria | 动态双进程（L1/L2/L5） | ✅ PASS | L5 read 无 NameError，三关状态均跨进程保留 |
| 2 | arcade | 动态双进程 + 回归 | ✅ PASS | 默认 new 单次 enter（visits=1）；new+只读 command 不再删档不建档 |
| 3 | burger | 动态双进程 + 回归 | ✅ PASS | 正常存档无坏档误报，无误生成 .corrupt |
| 4 | fishing | 动态双进程 + 回归 | ✅ PASS | 带/不带 seed 两种 new 均正常，无「未知指令」 |
| 5 | leek | 动态双进程 + 回归 | ✅ PASS | 正常存档无误报，无误生成 .bak |
| 6 | market | 动态双进程 + 回归 | ✅ PASS | v11 六个 pending 字段入档，正常指令流与 v11 往返正常 |
| 7 | imitator_td | 动态双进程 | ✅ PASS | session/records 双档完整，seed 与复盘跨进程保留 |
| 8 | turtle_soup | 代码复核 + 冒烟 | ✅ PASS | 权威状态全 SQLite，收尾单事务原子 |
| 9 | mbti | 代码复核 | ✅ PASS | 结果存档+session 删除同事务原子 |
| 10 | dnd | 代码复核 | ✅ PASS | 同 mbti；历史 SyntaxError 已不存在于当前代码 |
| 11 | bdsmtest | 代码复核 | ✅ PASS | 先 commit 答案再外部算分，失败保留 session 可重试 |
| 12 | eco | 代码复核 | ✅ PASS | BEGIN IMMEDIATE 覆盖完整，finally 清 _STATE |
| 13 | ciyuwu | 代码复核 | ✅ PASS | BEGIN IMMEDIATE 正确、无死锁；留 1 条长事务尾部 CONCERN（非阻断） |
| 14 | workkk | 代码复核 + 冒烟 | ✅ PASS | 原子写/错误传播/坏档告警三链路完整；留 4 条耐久性建议（非阻断） |

**整体结论：14/14 全部 PASS，验收通过。** 今晚 7 项修复均验证有效且未引入回归；两个 supervisord 服务运行正常、重启后零新增异常；外层与九个嵌套仓库工作区干净；测试数据已清零。遗留两组非阻断改进项（ciyuwu 长事务上限/WAL、workkk 目录 fsync 与 tmp 命名等）建议列入后续待办，不影响本次验收结论。

---

## 改进实施记录（2026-07-17）

本节记录上述两组非阻断耐久性建议的落实情况。只做报告点名的改进，未做其他重构；未重启服务、未提交 git、未触碰玩家数据（生产 `sessions.db` 未在本次操作中转换，WAL 将在服务下次以新代码建连时惰性生效）。

### ciyuwu（`ciyuwu_adapter/handler.py`）

1. **WAL + 显式 busy_timeout**：`_connect()` 由裸 `sqlite3.connect(DB_PATH)` 改为 `connect(DB_PATH, timeout=10)` 并执行 `PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=10000`。写事务期间同库读不再被阻塞，busy 窗口只剩写-写竞争；journal_mode 持久于 db 文件、重复执行幂等。
2. **长事务尾部防护（对应第 13 节 CONCERN）**：`ciyuwu_cmd` 入口新增两道上限——`MAX_COMMAND_CHARS = 500`（command 长度）与 `MAX_CHAIN_COMMANDS = 20`（分号串联条数，与引擎批量指令 20 步上限对齐；引擎仅按 ASCII 分号切分，适配层同规则计数）。超限返回 `-32602` 参数错误，vendor 引擎未改，`_ENGINE_LOCK` 与事务形态（`_init_db → commit → BEGIN IMMEDIATE`）保持原样。

沙盒验证（临时 DB，未触生产库）：`PRAGMA journal_mode` 返回 `wal`、`busy_timeout` 返回 `10000` 且二次连接幂等；`ciyuwu_new` + 「新角;确认」串联正常流程不受影响；21 条串联与 600 字符 command 均被 `-32602` 拦截，恰 20 条串联放行；挂起 `BEGIN IMMEDIATE` 写事务时另一连接读查询不阻塞（WAL 语义生效）。`py_compile` 通过。

### workkk（`vendor/workkk/main.py`）

按第 14 节四条建议逐条落实：

1. **tmp 文件名唯一化（建议①）**：`_write_state_file` 的临时文件由固定 `{path}.tmp` 改为 `{path}.{pid}.tmp`，多 worker 部署下并发写同一存档不再互覆临时文件；失败清理逻辑不变。
2. **父目录 fsync（建议②）**：新增 `_fsync_dir()`，`os.replace` 后对父目录 fsync，使重命名在断电后也持久；对不支持目录 fsync 的文件系统 best-effort 降级（不影响已落盘数据）。
3. **其他读错误对称化（建议③）**：`_load_state` 新增 `except OSError` 分支（置于 `FileNotFoundError` 之后），`PermissionError` 等读错误包装为 `PersistenceError` → 结构化 500，不建新档、不缓存坏状态；建新档与坏档告警两分支行为不变。
4. **`.corrupt` 备份不覆盖（建议④）**：坏档备份名唯一化——`.corrupt` 已存在时改用 `.corrupt.<时间戳>`（同秒再冲突追加序号），二次损坏不再覆盖前一份备份。

沙盒验证（临时 `WORKKK_SAVE_ROOT`，未触生产存档）：正常写入成功且无 tmp 残留；不可序列化对象写入 → `PersistenceError` 且原档完好、tmp 已清理；连续两次坏档 → 生成 `game_state.json.corrupt` 与 `.corrupt.20260717-095402` 两份备份、内容各自对应且告警照常生成；读到目录（`IsADirectoryError`）→ `PersistenceError` 且不建新档；新玩家 `FileNotFoundError` 建新档分支未被新 OSError 分支劫走。`py_compile` 通过。

### 部署提示

两处改动均需服务重启后生效（本次未重启）。ciyuwu 首个新代码连接会把 `sessions.db` 转为 WAL（产生 `-wal`/`-shm` 伴生文件，属预期）；该库同时服务 mbti/dnd/bdsmtest/eco，WAL 为库级属性，即报告第 13 节建议中「sessions.db 改 WAL」的预期效果。
