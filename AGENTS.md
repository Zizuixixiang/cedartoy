# CedarToy Agent 规则

主入口 `server.py`（标准库 `http.server`）；首页 `index.html`，管理页 `admin.html`（`/admin`）。另有独立游戏服务与双弈网关，不能把主进程当成全部服务。项目概览见 [README](README.md)，按任务读取下列文档。

## 数据与安全边界

- 账号主库默认 `turtle-soup/backend/turtle_soup.db`（`TURTLE_SOUP_DB`）：`toy_users`、`user_bindings`、凭据、防沉迷等；海龟汤 `players` 与平台账号不是同一张表。会话库默认 `data/sessions.db`（`SESSIONS_DB`）：eco/ciyuwu、测评、公告等。操作前核实服务实际环境变量，不凭 `.db` 文件名猜库，历史空库不能当主库。
- 文件存档：`data/vendor_saves/<game>/<player_id>/`；账号槽 1 为数字 ID，槽 2–5 为 `id:2`–`id:5`，游客为 `guest:xxx`。eco/ciyuwu 在会话库。查绑定游戏先找 AI 的 ID，不用人类号空目录断言丢档；人类测评、塔罗等有各自归属，不能推广为“所有存档都属于 AI”。花园旧 `garden_cat.db` 不是当前主存档；便签在 `data/garden_cat_notes.db`，不随存档导入导出。
- 身份由服务端鉴权/绑定推导，覆盖自报 `player_id`；根 MCP 各工具必须同时支持路径 token 和 Bearer（`path_token or bearer_token`）。不同身份、槽位、游客不得串档。账号 `login_or_register` 在 MCP 中仅注册；AI `login`/`rotate_token` 会使全部旧 Token 失效，不能为排障随意换取凭据。
- 覆盖存档必须确认：通用 vendor 用 `vendor_cmd_adapter/base.py:require_save_confirm`，eco_new 与 garden_cat 保留各自确认；坏档先保留再告警，不静默覆盖。SQLite 读改写同事务，文件存档持锁/原子写。持久化检查见 [接入清单](docs/GAME_ONBOARDING_CHECKLIST.md)。
- 生产排查优先只读连接；测试用临时库/目录，需走真实存档链路时只用一次性 `guest:<用途>`，仅清理本次测试产物。迁移先在数据库一致性副本验证幂等与完整性。时间不可混用：账号多为北京时间，`password_reset_tokens` 使用 SQLite UTC。
- 公告用 `announcements` API，禁止手写 INSERT；确认 `SESSIONS_DB`，既有投票置标用 `set_force_mcp_push`，不要重建定义。人类与 AI 投票不按绑定合并，有效首票锁定，文字意见仅本机运营可汇总。操作见 [运维手册](docs/OPERATIONS.md#公告与投票)。

## 测试与部署入口

- 在仓库根按改动运行 `python3 -m unittest tests_toy.<相关模块>`；前端检查见 `package.json` 与 `scripts/check_*.js`，按相关文档选择。存档/接入改动另跑 `python3 scripts/persistence_check.py`（跨进程，使用并清理 `guest:regcheck`）；检查最终 diff 与 `git diff --check`。
- supervisor 必须指定配置：`supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy`；主服务代码或其已导入 adapter 修改后，部署用同命令的 `restart cedartoy`。独立服务改动重启对应进程；cron 脚本修改无需重启主服务。进程归属见 [运维手册](docs/OPERATIONS.md#服务与日志)，双弈链路/回滚见 [网关文档](docs/DUEL_ASYNC_GATEWAY.md)。
- 主服务日志：`/var/log/cedartoy.out.log`、`/var/log/cedartoy.err.log`；根 `server.log` 是历史文件，不能用于当前排障。
- 备份/恢复必须使用 SQLite 一致性快照；入口 `scripts/backup_cedartoy.sh`，说明见 [备份手册](docs/BACKUP.md)。仓库 `deploy/cron.d/cedartoy-backup` 是旧直接 tar 模板，不可当作现行部署入口。
- 管理员看板只返回聚合，不提供逐房明细或私密内容；口径、范围与只读 smoke 见 [看板文档](docs/ADMIN_DASHBOARD.md)。

## 新游戏与 Git 边界

- “前端别忘了”默认只指首页/4399 卡片、图标、简介与入口。先检查上游有无 Web；没有时禁止擅自新造网页、控制台、围观页或第二入口，“完整玩法”链接作者原仓库/页面。只有上游自带网页或管理员明确要求才接入平台网页，优先复用原 UI。
- MCP 只做身份/独立存档、schema、guide、紧凑状态与错误规范的薄适配；未经明确需要并说明，不改作者玩法、命令语义或上游源码。验收分别检查首页/作者链接、MCP list/guide/play/schema/存档，以及确实存在的人类网页。
- 主仓库 `Zizuixixiang/cedartoy`；`backups/`、`*.bak` 不入库。`vendor/*` 是上游独立仓库（部分留有 gitlink）：本地补丁 commit 注明“勿推上游”，永不 push，上层仓库不提交 vendor 指针变动。`eco/` 是独立 `cedareco` 仓库，仅改引擎本体才需单独推。
- vendor `origin` 保留作者原始 GitHub URL；镜像只作额外 fetch remote。更新先查脏改动与实际内容 diff，不能仅凭领先/落后提交数判断；不清掉 `ci-yu-wu/ending.txt` 等运行存档。检测入口 `scripts/check_vendor_updates.sh`，对齐/合并步骤见 [运维手册](docs/OPERATIONS.md#vendor-更新)。

## UI / 前端改动纪律

- 任何可见 UI/CSS/交互改动，Codex 必须先读 .agents/skills/frontend-design/SKILL.md、.agents/skills/ui-ux-pro-max/SKILL.md、.agents/skills/cedar-ui-review/SKILL.md。
- CedarToy/Duel 现有界面就是设计系统：先量现有字号、间距、边框、阴影、按钮高度和移动端行为，再新增样式；优先复用已有组件，不另造一套视觉语言。
- autocomplete / popover / tooltip 等瞬时 UI 默认应为悬浮层，不得因出现而挤动棋盘、聊天或页面布局；辅助字号不得大于正文。
- UI 测试通过不等于视觉通过；至少检查约 360px 手机宽度、较宽手机和桌面，并对照同页现有组件做层级/字号/间距审查。
- 禁止通过连续追加 CSS hotfix 解决视觉问题；完成前必须合并被替代规则，避免层层覆盖。

## UI / 前端任务

- 任何可见 UI/CSS/交互改动，开发 agent 开工前必须读取 .agents/skills/frontend-design/SKILL.md、.agents/skills/ui-ux-pro-max/SKILL.md、.agents/skills/cedar-ui-review/SKILL.md。
- CedarToy/Duel 现有页面就是设计系统：优先复用已有组件、字号、边框、阴影和间距，不另造一套视觉语言。
- 临时 UI 采用浮层还是内联由当前交互语义和现有产品模式决定；不得把一次性组件方案固化成全局规则，也不得因实现副作用造成非预期布局位移。必须验证移动端布局、字体层级、触控尺寸和状态切换。测试通过不能替代视觉检查。
- 有用户截图/参考图时，必须先对照字号、相对尺寸、层级、是否悬浮、是否移动布局再实现。

## MCP Schema 与 Token 纪律

- 根 MCP 的公开工具 schema 是所有调用方都会承担的上下文成本。新增或修改工具字段前必须先评估 token 影响；不得为了单个游戏“更容易调用”把该游戏的大量业务参数长期平铺到全局 `play` schema。游戏专属参数、动作说明和示例优先放 `get_guide(game)`；公共 `play` 保持最小稳定外壳。
- 客户端兼容必须局部化：Kelivo/Dart/Ktor 等确有 schema 缺陷的客户端可使用专用兼容 schema，但不得让其兼容字段污染普通客户端，也不得用旧兼容定义覆盖当前正确字段类型。兼容层变更需检查同名参数跨游戏复用造成的类型冲突。
- 所有 MCP tool schema 变更都必须保留 Gemini 兼容。Gemini function declarations 不接受数值 enum 等已知不兼容结构时，对外 schema 应使用兼容的基础类型并由后端保持严格校验；不得为了 schema 展示把已验证的 Gemini 兼容处理回退掉。至少保留/运行对应的 schema 回归测试。
- 不只 MCP：任何会进入模型上下文、长期重复注入或随请求携带的 guide、工具描述、状态、历史、提示文本，修改时都要考虑 token 成本。新增内容应说明为什么必须常驻；能按需加载、放 guide、压缩或索引化的，不要无条件塞进全局上下文。

## Agent 任务纪律

- `agent_execute` 超时必须显式填写，禁止依赖默认 600 秒：普通非小型开发任务至少 1800 秒；大补丁、合并、重构、跨模块任务至少 3600 秒。只有明确可在短时间完成的小任务才可使用更短超时。
- 修改前检查工作树，保留既有未提交改动，不清理、不覆盖无关文件。
- 新 `agent_execute` 是独立任务，不能当作给运行中任务追加上下文。同一任务的补充先汇总，等结束后用原 session follow-up；不能复用时一次性提供完整最终要求和当前改动背景，避免多个任务并改同一批文件。
- session 只复用明确相关、需要继承上下文的同一任务链；不同模块/目标或无依赖任务新开，不确定时新开。UI/交互需求先汇总再发，不碎片化追加。
- 2026-09-18 授权：已确认范围内修改验证后由主助手部署并 push，无需重复询问；当次“先不部署/先汇报/不 commit/push”等要求优先。开发 agent 只修改和测试，Git 推拉与部署由主助手执行。
- 本文件只存长期规则与可靠文档指针；操作细节放专题文档，不追加当前任务、临时 TODO 或聊天摘要。
