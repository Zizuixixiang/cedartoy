# 后端自动分批拆分验收记录

日期：2026-10-05。工作树 `/opt/cedartoy-refactor-backend-auto`，分支
`refactor/backend-auto`，基线 `976e56a`。开始时已读取 `AGENTS.md`，工作树干净。

## 实际进度与停止原因

首轮完成阶段 A 后暂停；续轮已按顺序完成 B、C、D、E、F，及限定范围的 G。
最终 `server.py` 为 **8,197 行**。无未解释的回归、循环依赖或生产隔离失效。
下文保留 A 的历史问题；续轮没有因该旧问题再次停止。

阶段 A 第一次迁移后复测发现测试环境在两轮运行间复用了临时会话库：
前一轮留下的库没有 `test_results` 表，导致
`AccountAvatarTests.test_registration_and_set_avatar_validate_emoji` 查询资料失败。
对同一个残留库分别运行未修改的基线与迁移后代码，均复现
`sqlite3.OperationalError: no such table: test_results`。
这是测试运行间的 fixture 隔离问题，未发现此次迁移造成的业务回归。

随后仅在 A 内复核：每个测试进程使用全新临时数据库路径，基线与迁移后均通过。
遵守任务中“测试隔离失效立即停在该阶段”的规则，没有继续 B。
没有修改既有统计逻辑或既有测试来绕过失败。

## 阶段 A 文件与边界

| 文件 | 内容 |
| --- | --- |
| `cedar_backend/auth.py` | 新增；19 个密码、JWT、opaque token、当前账号、path token、Bearer 解析函数 |
| `cedar_backend/accounts.py` | 新增；55 个注册登录、登录限流、头像资料、用户名、绑定、轮换与改密函数 |
| `cedar_backend/player_identity.py` | 新增；16 个账号/游客/slot、绑定目标和上报身份处理函数 |
| `server.py` | 修改；保留上述 90 个函数的原签名与默认值，以显式关键字参数在调用时传入依赖 |
| `tests_toy/test_backend_extraction.py` | 新增；逐项验证 90 个薄转发的参数、返回对象、可 patch 依赖，以及 slot 边界 |
| `docs/BACKEND_AUTO_REFACTOR.md` | 本验收记录 |

新模块不导入 `server`；数据库路径、配置、锁和限流桶仍由 server 持有。
嵌套 helper 仍经 server 兼容入口调用。没有修改 token 失效规则、账号类型、
绑定权限、用户名和 slot 规则，也没有改动 HTTP 路由、文案、schema 或 SQL。
账号 schema 初始化和数据库连接入口仍留在 server。

## 阶段 A 验证结果（首轮）

首轮仅执行阶段 A 定向测试，没有运行全量套件。

| 检查 | 结果 |
| --- | --- |
| 初始基线：security round1/round4、machine token、binding token errors、avatar、rename 六个模块 | 65 项，64 通过、1 跳过 |
| 首次迁移后，同六个模块 | 65 项，63 通过、1 错误、1 跳过；错误原因见上文 |
| 同一残留库复现 | 未修改基线和迁移后均出现相同缺表异常 |
| 全新临时路径，重新运行基线六个模块 | 65 项，64 通过、1 跳过 |
| 全新临时路径，迁移后六个模块及新增 extraction 测试 | 67 项，66 通过、1 跳过 |
| 相同输入的迁移前后输出/异常对照 | 48 组全部相同，覆盖 slot、游客 ID、用户名、emoji、Bearer、编码、无效 JWT |
| AST 对照 | 90 个迁出函数体及 server 公共签名完全相同；server 其余语句仅增加三个 import |
| Python 语法 / 隔离环境 import | 通过 |
| `git diff --check` 与最终 diff 自审 | 通过；无无关文件修改 |

跳过项为 round4 中 turtle-soup 运行依赖不可用的测试，基线与迁移后相同。
数据库语义由原有临时 SQLite fixture 的断言覆盖；未声称对全部数据库和文件存档
做了迁移前后快照差分。首轮未进入高风险存档阶段 D；续轮 D 的结果另见后文。

### 测试环境

原 worktree 缺少 `eco` 和 `vendor/ci-yu-wu` 源码，直接启动 unittest 在 import
阶段失败。按任务授权，在 `/tmp/cedar-backend-auto-lab` 建立仓库隔离副本，并从
`/opt/cedartoy` 只读复制这两个源码目录；未复制其运行存档或数据库。
测试期间禁用 bytecode 写入，加入审计拦截以拒绝生产数据库连接和生产文件写操作。
已有账号测试使用各自的临时库，复核时环境默认库路径也改为每进程唯一临时目录。

本次临时复核入口和证据（仅本机临时产物，不属于仓库运行依赖）：

```sh
cd /tmp/cedar-backend-auto-lab
PYTHONPATH=/tmp/cedar-backend-auto-tools python3 /tmp/cedar-backend-auto-tools/run_a.py --baseline
PYTHONPATH=/tmp/cedar-backend-auto-tools python3 /tmp/cedar-backend-auto-tools/run_a.py
```

- `/tmp/backend-auto-A-clean-baseline.log`
- `/tmp/backend-auto-A-clean-after.log`
- `/tmp/backend-auto-A-differential.log`
- `/tmp/cedar-backend-auto-tools/manifest-A.json`：函数及注入依赖清单。

## 最终保留职责

`server.py` 从最初 12,779 行，经 A 的 11,969 行降至 **8,197 行**。
本次累计迁出 251 个函数/方法实现，保留原公共签名和薄转发。
仍在 server 的主要内容：

- 配置、常量、数据库连接、部分初始化/时间迁移、请求限流及共享状态容器。
- 各模块兼容入口，以及 `CedarToyHandler` / `ThreadPoolHTTPServer` 的兼容类定义。
- 具体 HTTP 业务处理器（账号、各游戏网页、Tarot SSE 等）、请求体读取/drain、静态文件读取和 ETag 缓存逻辑。
- 各游戏独立 proxy、cookie/绑定浏览权限辅助、网页活动记录和少量公告/文本组合辅助。

G 没有继续强拆这些 Handler 方法，也没有合并游戏代理。`ThreadPoolHTTPServer`
各生命周期实现与 main 已在 `http_server.py`；保留类壳以兼容调用方与运行时 patch。

未部署、未 commit、未 push；未修改 `/opt/cedartoy` 生产目录。
未修改专项备份 `/home/backups/cedartoy/refactor/20261005-210022_backend-auto_976e56a`。
没有处理 UI、前端拆分、vendor 更新或上游代码修改。

## 续轮测试纪律与阶段 B

续轮保留 A 的全部未提交改动。每次基线、迁移后测试或差分检查都新建独立
`/tmp/cedar-backend-isolated-*` 目录，复制当前源码及缺失的 eco/ci-yu-wu
源码（仅只读读取生产源码；排除数据库、运行存档、日志、环境文件）。
账号库、会话库与 TMPDIR 均在该次目录内；退出后整个目录删除。
Python 审计钩子拒绝隔离目录外的 SQLite 连接、文件写入和文件系统修改，
并阻断生产服务端口连接。测试日志保留在 `/tmp/cedar-backend-continue/`，
不保留运行 fixture。与上轮相同的、已解释的基线旧问题不再作为停止条件。

B 已通过：新增 `account_email.py`、`account_recovery.py`、`admin_accounts.py`，
迁出 43 个函数；修改 server 薄转发及 `test_backend_extraction.py` 覆盖范围。
`admin_recovery_mcp` 与 `admin_dashboard` 保留原边界，未修改。

- security round2、account recovery、admin recovery MCP：基线 58/58；迁移后加兼容测试 60/60。
- 管理员分页、待注销状态显示、立即释放的 3 个定向测试：3/3。
- 23 组输出/异常差分一致；邮箱、恢复工单、reset token 三套 schema 各初始化两遍的 SQLite dump 完全一致，integrity_check 通过。
- 43 个函数体和公共签名 AST 不变；累计 133 个迁出函数通过静态审计；语法/import、diff 检查通过。
- 每次 sandbox 已清理，无生产隔离拦截触发。证据：`B-before.log`、`B-after.log`、`B-admin.log`、`B-diff.log`。
- 此时 server 为 11,098 行；准许进入 C。

## 续轮阶段 C

C 已通过：新增 `operit.py`，迁出 16 个函数（含 schema、凭据失效协调、
web ticket 与 Duel call）；修改 server 薄转发和兼容测试覆盖范围。

- Operit integration 基线 12/12；迁移后加兼容测试 14/14。
- 12 组 client_id 输出/异常一致；固定随机值与时间下，3 次 session 签发（含同 caller 切换 AI）的返回 Token 和 SQLite dump 完全一致。
- 累计 149 个函数体、公共签名 AST 一致；语法/import、diff 检查通过。
- 所有 sandbox 已清理，无隔离拦截触发。证据：`C-before.log`、`C-after.log`、`C-diff.log`。
- 此时 server 为 10,889 行；准许进入 D。

## 续轮阶段 D

D 已通过：新增 `save_management.py`、`account_lifecycle.py`，迁出 40 个函数；
修改 server 薄转发及兼容测试。游客迁移的 `nowhere_storage.lock_platform_claim`
装饰器保留在 server 原入口，覆盖调用与完整回滚过程。
`**payload` helper 的注入依赖使用 positional-only 参数，避免占用原本任意合法的 payload key。

基线准备发现旧 fixture 缺少 `account_avatar_selection`（7 个错误）与环境缺 Flask
（6 个错误）。仅为完成本阶段覆盖，在 `test_account_saves_workkk.py` 两处临时库
初始化中补调用现有 `avatar_appearances.init_schema`；未修改业务逻辑或断言。
Flask 及其依赖和包元数据只读复制到每次 sandbox，未安装项目依赖。

- account saves/workkk、account deletion round3、filtered save picker、garden save management：补齐 fixture 后基线 53/53，迁移后加兼容测试 55/55。
- 相同内容、独立临时库/目录的 6 个差分场景：成功迁移、迁移后删除、目标冲突、常驻服务失败、后续目录失败并回滚、非法槽位。返回值/异常、SQLite dump、文件字节、常驻服务迁移/回滚调用顺序完全相同；包含坏 JSON 保留和其他账号槽不受影响。
- 使用原 `scripts/persistence_check.py` 的 `main`/`run_case`，仅选择相关文件存档的 bar-full/bar-lite 两项，三个独立进程各执行 new/mutate/query：2/2 通过，`guest:regcheck` 残留 0。没有运行全游戏持久化矩阵。
- 累计 189 个函数体、公共签名、入口装饰器 AST 一致；语法/import、diff 检查通过。所有 sandbox 清理完成，无生产访问。
- 证据：`D-before-fixed.log`、`D-after-ready.log`、`D-diff.log`、`D-persistence.log`（之前的环境准备失败也保留日志）。
- 此时 server 为 10,147 行；准许进入 E。

## 续轮阶段 E

E 已通过：新增 `game_dispatch.py`，迁出 15 个游戏薄分发及其解析/错误辅助函数；
修改 server 薄转发和兼容测试。未改任何 adapter、vendor、schema 或 guide。

- root MCP protocol、Tarot MCP boundary、workkk/garden 导入导出确认、camping plan/restart：基线 28/28；迁移后加兼容测试 30/30。
- 首轮双方均缺 fishing 源码；只读复制后在全新 sandbox 复测通过。root protocol 包含 Gemini/普通/Kelivo schema 回归与真实临时 fishing 存档链路。
- 59 个差分场景覆盖五种 JSON-RPC 游戏、scale、16 个 vendor 分支和三个常驻服务：输出/异常、下游调用参数及输入修改情况完全一致。
- 累计 204 个函数体/签名/装饰器 AST 一致，语法/import、diff 检查通过，sandbox 均已清理。
- 证据：`E-before-ready.log`、`E-after-ready.log`、`E-diff.log`。
- 此时 server 为 9,694 行；准许进入 F。

## 续轮阶段 F

F 已通过：新增 `duel_bridge.py` 与 `mcp_dispatch.py`，迁出 30 个函数；
包含 `_handle_root_mcp`、`_tool_play`、`_tool_play_inner`、`_tool_account`、
`_finalize_play_response`。保留原语句顺序；ticket 容器、锁和 deferred-call 类型仍在 server。
新增 `test_mcp_dispatch_order.py`，并扩展薄转发兼容测试。

- Duel async gateway、wait control、root MCP protocol、防沉迷、活动记录、公告、游客 tombstone：基线 84/84；迁移后加兼容测试 86/86。
- 新增顺序测试基线与迁移后各 1/1；覆盖账号成功、游客成功、游戏报错、防沉迷拦截、已认领游客拒绝。初写断言误以为游客顶层 ID 优先，差分确认基线优先嵌套 params ID 后更正了测试期望，未改实现。
- 五条完整 root→play→finalization 链路：响应、下游 canonical 参数及副作用顺序完全一致。
- 网关现有测试覆盖 move 不重放、挂等心跳、票据消费/弃用、取消竞态、断连释放、path/Bearer 身份。
- 累计 234 个函数体/签名/装饰器 AST 一致，语法/import、diff 检查通过，无隔离失效，所有 sandbox 已清理。HTTP 日志中的生产形状 URL 来自 MockTransport，不是真实连接。
- 证据：`F-before.log`、`F-after.log`、`F-order-before.log`、`F-order-after.log`、`F-diff.log`。
- 此时 server 为 8,709 行；G 限于六个 HTTP 方法入口、三个纯响应方法、线程池和启动协调，保持其余 Handler/proxy 方法原地。

## 续轮阶段 G

G 已按限定范围完成：新增 `http_handler.py`、`http_server.py`、
`web/__init__.py`、`web/responses.py`，迁出 17 个实现：六个 HTTP 方法路由入口、
三个响应方法、两个 JSON-RPC 响应函数、五个线程池方法和 main。
新增 `test_http_shell_extraction.py`。各游戏 HTTP 处理器和 proxy 均未移动或改写。

- HTTP 外壳、satellite proxy、恢复路由、Operit 路由定向测试：基线与迁移后均 15/15。
- 外壳扩展到 5 项后，在基线与最终实现各 5/5：JSON/HTML/空响应字节、headers/ETag/cookie、503、队列超时、异常释放和基类初始化/关闭。
- 自审发现搬移代码中的 `sys.modules[__name__]` 必须保留 server 上下文，已显式注入原模块名；新增测试验证 nowhere 的 POST/GET/DELETE 均收到原 server 模块。未留下该迁移风险。
- 1,308 组方法/路径/query 差分：路由下游用 mock 隔离，真实执行路由和 body 读取；返回/异常、调用顺序、body 读取位置及响应状态完全一致。该检查不声称执行了所有下游业务或真实生产 HTTP。
- 17 个实现的公共签名、入口装饰器与函数体静态对照通过；仅两处零参数 super 调用改成由原方法传入绑定的基类方法，保持继承与调用顺序，专门测试通过。
- 独立导入所有新增 backend 模块不加载 server，没有循环依赖；Python 语法、最终 diff 检查通过。
- 证据：`G-before.log`、`G-after.log`、`G-shell-before.log`、`G-shell-after.log`、`G-diff.log`。
- 最终 server 为 **8,197 行**。已完成本轮授权范围，不再继续扩大拆分。

## 验收边界

所有阶段在进入下一阶段前完成记录与定向验证。未跑全量测试，未进行生产 smoke。
本轮所有 `cedar-backend-isolated-*` 运行目录与数据库已随进程退出清理；
`/tmp/cedar-backend-continue/` 仅保留控制脚本、阶段源码参考、manifest 和测试日志。
阶段失败记录仅包括已解释的基线环境/fixture 缺口及新增测试的期望校正，
未发现修正后实现与基线的不一致；未因风险中断某阶段。

本轮新增模块：B 三个，C 一个，D 两个，E 一个，F 两个，G 三个模块及 web 包入口。
共同修改 `server.py`、扩展 `test_backend_extraction.py`；D 仅补齐旧存档测试的两处
临时库 schema，F/G 各新增一个有行为断言的测试文件。A 的三个模块未被重写。

**未部署、未 commit、未 push、未修改生产目录 `/opt/cedartoy` 或专项备份。**
前端、UI、vendor 内容、MCP schema/guide 和独立服务均未修改。停止等待主助手验收。
