# 弈间 / AutoChess 4399 独立适配：部署与验收记录

2026-10-27 较早版本已部署到 CedarToy/4399 正式原入口 `/autochess/`；**网页和根 MCP 已完成生产连通性检查，但未完成真人/真机整局试玩验收，不发公告或通知作者。** 原有其他未提交改动均保留。阶段一、阶段二及首页小增量的隔离验证记录见下文；历史正式部署细节见「正式入口部署记录」。**本轮恢复局外经济与名称修正仅在工作树实施，未部署、未重启；生产仍为旧 slim 模式。当前发布前注意事项及证据见文末「局外经济恢复」。**

## 来源与版本

- 作者：**江川雪**；小红书 **63657956652**；已获二次改编许可。
- 原作与来源链接：https://github.com/Cecilia-coding/AutoChess-MCP 。MIT，保留 `vendor/autochess/LICENSE`，独立网页另提供同文许可证链接。
- `vendor/autochess` 是独立 Git 仓库；origin 保留上述作者地址。基线 `7a21f63310e56fa1d9cdcf47921232ae2916aae2`。本轮 `git ls-remote origin HEAD` 成功，远端 HEAD 与该基线一致；未 fetch、合并或推送。
- [本地完整补丁](patches/autochess-cedartoy.patch)包含两阶段及本轮局外经济恢复的 vendor 改动，只供 4399 使用。当前 vendor 已应用；干净基线先 `git apply --check` 后再应用，不要重复应用，也不要提交上层 vendor 指针。
- 已重新阅读原作 `docs/CEDARTOY_ADAPTER.md`、房间/平台/Web 实现及 4399 双弈认证、邀请、异步网关文档；原版 adapter 的确只创建 duel，本地新增薄入口保留原方法。

## 已实现

### 私人网页与首页

首页增加「弈间」卡片，统一站内入口 `/autochess/`。页面直接复用原作 `RoomApp`、`Arena3D`、装备、商店、战斗及回放组件，没有 iframe 或外站游玩跳转。首页「作者原版 ↗」指向 https://play.ceciliaceci.me/，与「进入棋室 →」分开；GitHub 来源署名、Guide 和适配网页页脚的仓库链接及 MIT 声明保留。

独立 Vite 配置 `vite.cedar.config.ts` 构建纯静态客户端。原版 Next/vinext Web 服务不运行；原版 Lobby、账号、社交、公共匹配组件不挂载。原作席位 token 仅保留组件内部空字符串兼容占位，不发送、不缓存真实席位凭证。原作图片组件换成静态 img；棋盘/头像框资源在本构建中转到 `/autochess/static/`，不改变上游资源定义。

网页读取首页已有的 `cedartoy_token`，仅以同源 Bearer 请求交给 **4399 主站**。未登录者可另开首页登录后返回重连，好友邀请保留在当前标签的 URL fragment，不进日志。每次请求重新鉴权和读取绑定；支持：

- 私人单人：创建后由房主在原棋盘点「补路人机，直接开局」。
- 人类与自家小机：从实时绑定列表勾选小机，为同槽 canonical 身份预留独立席位。小机从根 MCP `rooms` 找到房间；不承诺后台自动唤醒。
- 好友：房主在原房间菜单复制邀请链接；受邀者用自己的 4399 人类账号加入。第二位玩家入座不会自动开始，只有房主能 start。
- 本人的房间列表、重连与只读观看；当前绑定小机的房间只读观看。解绑后立即失去新的读取权限。没有任意 roomId 公开围观，也没有公开房间目录。
- 槽 1–5，各自隔离；账号切换/注销事件清掉当前页面会话。返回棋室保留席位，认输采用明确操作。

Web 不开放游客操作；根 MCP 延续 `guest:` 隔离。双人 duel、standard 默认八人及 2–16 偶数人数、ai/jianghu 两套棋谱保留。frenzy 在此基线 `lobby=false`，仍不开放，没有改作者规则。

### 身份、路由与服务密钥

主站仅开放以下白名单；未知路径返回 404：

| 路径 | 边界 |
| --- | --- |
| GET `/autochess/`、`/autochess/static/*` | 只读构建产物；解析后的真实路径必须在 dist 内；限制文件类型，无目录索引 |
| POST `/autochess/api/bootstrap` | 4399 人类 Bearer，自己的房间/当前绑定小机列表 |
| POST `/autochess/api/room` | 白名单游戏动作；服务端注入身份/名字/slot；观看小机只读 config/state |
| POST `/autochess/api/economy` | 4399 人类 Bearer，自己或当前绑定小机的同槽局外操作；拒绝任意加币、原站账号动作 |
| POST `/autochess/api/stats` | 独立作者授权，只有本游戏聚合；`{"op":"access"}` 仅在主站认证后返回 `allowed`，不查询卫星 |
| POST `/_internal/autochess-gateway/{prepare,finalize,abandon}` | loopback + 双弈现有 gateway 共享密钥；公网 Nginx 必须拒绝；prepare 64 KiB，finalize 最多 2 MiB |

卫星是新增 `scripts/cedartoy-server.ts`，不导入原站 HTTP/账号服务器。它只接受 loopback socket、64 位小写 hex 的独立密钥，拒绝 Origin、Authorization 和 Cookie；白名单 `/health`、`/platform/autochess`、`/platform/web`、`/platform/stats`、`/platform/economy` 均受保护。`/room`、`/account`、`/mcp`、公共匹配等路由不存在，不是仅隐藏按钮。

主站到卫星只允许 `http://127.0.0.1:<port>`，禁用环境代理/重定向。只发送 canonical 身份、显示名、身份类型、白名单业务字段及服务间密钥，**不转发 4399 token、密码、Cookie 或原始请求头**。请求体中的 `player_id`、小机名字等不能换座；家庭席位必须来自服务端当前绑定表。浏览器 API 使用 Bearer，无 Cookie/query token 后门，拒绝跨源 Origin，限 64 KiB，并使用独立于 MCP 的网页请求频率桶。

`AUTOCHESS_ENABLED` 默认关闭。受支持的卫星启动方式 `scripts/run_autochess.py` 要求绝对数据目录、Node ≥22.13、独占 flock。新 `CHESS_CEDAR_MODE=platform` 启用本地可信身份桥接及原作局外经济；没有该开关时保留旧 `CHESS_SLIM=1` 行为。单独 `CHESS_SLIM=0` 仍不能打开原站路由。启动器保留内部 slim 环境兼容，但新网页配置由实际 economy 实例决定；不启动原站账号、公共社交或 Cursor。构建无原站服务地址。

### 根 MCP 异步等待

公开 schema **没有新增字段**，仍为 `list_games/get_guide/play` 薄外壳。所有专有参数和署名放按需 Guide；list 只增加短发现项。

`rooms/create/join/start/read/act/cancel_wait` 经统一认证和 slot 推导，兼容路径 token 与 Bearer、标准 params 和 legacy JSON params。根异步网关按游戏识别 AutoChess，采用独立 prepare/finalize/abandon 票据：

- 主站线程仅执行短请求，不承载长轮询；异步 HTTP 直连本机卫星。
- `read(after=<房间revision>, waitMs=0..25000)`；约 12 秒空白心跳，最终返回单个 JSON。同步备用入口拒绝正 waitMs。
- AutoChess 单独的 80 个等待容量、连接池；卫星最多 96 个等待，预留控制容量。不会借用双弈的等待池。
- 同身份/槽/房间新等待替换旧等待；`cancel_wait`、act/start 可中断旧等待。客户端断开取消后端任务，回收槽位和一次性票据；票据 TTL 90 秒、上限 128。
- 不自动循环 replay 行动、不承诺请求结束后唤醒客户端。客户端根据 nextAction 继续调用。

### 作者只读统计

`AUTOCHESS_AUTHOR_USER_IDS` 是**明确授权的 4399 数字账号 ID**列表，默认空。不根据「江川雪」昵称或小红书号猜账号，不给全站 admin，也不因 is_admin 自动授予查看权。

首页选中「弈间」时，仅授权作者显示「查看游玩统计」；点击复用首页《烛》/《共同手作间》的 modal、列表行和按钮样式。原适配网页的统计区保留。后端每次读取重新检查同一授权。支持最近 10m、1h、6h、12h、24h，返回 `active_humans/active_machines/created/started/finished`、区间与生成时间。没有玩家 ID、昵称、房号、邀请或逐房内容，也不查询其他游戏。

口径：按当前窗口内自行创建/加入、开局或有效游戏行动的最近时间去重；同账号不同槽合并为一人/机，游客/NPC 不计。网页预留小机席位不证明小机已玩，**不计小机活跃**；轮询、观看、统计查询和聊天不计。开房/开始/完成各按独立房间首次时间统计，重试不重复计数；完成不含取消/闲置作废。时间证据与房间原子持久化，重启恢复。旧阶段未记录时间的房间不追溯编造统计。当前只提供相对现在的滑动窗口，未提供历史任意区间或每日报表。

首页弹窗明确标注时段活跃不是在线人数；空记录展示服务端返回的 0，读取失败不伪装成 0，提供重试。关闭、切换游戏或账号会清除权限和结果；重新打开前重新查权限，并用请求取消和身份/请求校验防止迟到结果回写。权限查询失败时入口保持隐藏，重新选择游戏可重试。

**未实际授权作者账号**：仍需管理员给出江川雪准确的 4399 账号 ID 后配置。授权代码及隔离测试已完成，默认任何人均不能看。

### 持久化

延续阶段一：房间按独立 `CHESS_ROOM_DATA` 保存，canonical 身份散列区分账号/槽/游客；创建签名、开局键、行动 receipts 持久化。相同请求 ID 重试不重复执行；改参数复用 ID 报错；新房不覆盖旧房。临时文件 fsync + 原子 rename，坏 JSON 保留并阻止启动；独占启动器防止两个进程同时写同目录。

保留原作一小时闲置结束规则。不提供导入/覆盖/游客认领、账号删除联动。房间列表最多最近 40 条；持有合法旧 roomId 仍可按自身身份读取。当前统计扫描房间内存，规模增长后的归档/索引是后续容量工作。

## 前两阶段验证与证据（历史记录）

本轮环境已能联网安装和监听；阶段一的 DNS/EPERM 限制不再存在。Node **22.14.0** 安装在 `/tmp/autochess-stage2-runtime`，没有替换系统 Node 20；vendor `npm ci` 按原 `package-lock.json` 安装 293 个包，锁文件未修改。

- Python **88 个不同定向用例分批通过**：AutoChess bridge 7、真实 Web/MCP/启动器联调 3、异步容量/非法输入 2、双弈异步网关 21、根 MCP 19、schema 4、wait control 11、活跃统计 21。无全平台长测试。
- TypeScript **26 个不同用例分批通过**：原作 15，加私人房/路由/真实 HTTP/网页薄接口/统计 11。覆盖两棋谱、各已开放模式、原作战斗/商店、NPC、并发重试、冷加载、坏档、写失败、HTTP 拒绝和取消。
- 三独立进程持久化 create → mutate → read 通过；`npm run typecheck` 和独立 Vite 构建通过。3D renderer 是按需加载的大 chunk，构建提示 >500 KiB，未因此重写原作渲染器。
- 真实临时 SQLite 账号库、真实 JWT/opaque AI token、实时绑定查询、真实 4399 HTTP 与 loopback Node 服务，以及 ASGI 异步边缘串联通过。内部票据、路径/Bearer、等待不阻塞其他调用、替换/取消/客户端断开清理均覆盖。测试只 mock 无关公告/迁移/通用活跃收尾；业务认证和引擎不 mock。
- Chromium 真实 WebGL（软件渲染）验证 360/430/1440px：入口、创建、NPC 开始、原装备选择、战斗回放、商店、刷新重连、离线恢复、自己只读围观。棋子站位测试用已认证 Web API 设置，**未把 API 操作当成触屏拖放验证**。请求审查没有原站或外站 API；图片也使用站内静态前缀。
- 首页卡片与入口另做隔离浏览器检查，首页账号/统计响应使用测试数据，不读取生产资料。新棋室延用 4399 淡粉底、紫灰主按钮、深色边框，原棋盘保持作者主题。检查了手机边距、无横向溢出、菜单浮层及进入/退出切换。
- 根公共 schema 增量 0；标准工具序列化 1918 字符、legacy 15211 字符。list 只在启用时增加 22 字符；Guide 按需加载。这里是字符数，不是 token 数。
- [本轮日志和截图](../artifacts/autochess-stage2/)；[阶段一证据](../artifacts/autochess-adapter-20261027-2gx4fizv/)。修复中发现过静态贴图前缀、全屏导航遮挡、HTTP 拒绝后未消费请求体的问题，均已修复并定向重跑。

复现（均为隔离测试，Node/浏览器路径按实际安装设置）：

```sh
cd /opt/cedartoy/vendor/autochess
npm ci --no-audit --no-fund
npm test
node --import tsx test/cedartoy-persistence.ts
npm run typecheck
node_modules/.bin/vite build --config vite.cedar.config.ts
cd /opt/cedartoy
AUTOCHESS_TEST_NODE=/path/to/node22 python3 -m unittest tests_toy.test_autochess_adapter tests_toy.test_autochess_web tests_toy.test_autochess_async_edge tests_toy.test_duel_async_gateway tests_toy.test_root_mcp_protocol tests_toy.test_mcp_schema_selection tests_toy.test_duel_wait_control tests_toy.test_game_activity
AUTOCHESS_TEST_NODE=/path/to/node22 PLAYWRIGHT_MODULE=/path/to/playwright PLAYWRIGHT_BROWSERS_PATH=/path/to/browsers python3 scripts/check_autochess_browser.py
```

`tests_toy/test_autochess_web.py` 在 Node/锁定依赖缺失时明确 skip，不能把 skip 算作验证。当时未改通用 `scripts/persistence_check.py`；本轮在保留其既有脏改动的基础上补充 `--autochess` 定向参数，复用现有独立进程测试。没有运行默认全平台检查。

## 部署前方案（当时的检查清单；正式执行记录见文末）

1. 保留 4399 与 vendor 工作树快照、上一个构建；仅携带本任务文件，不能提交其他脏改动或 vendor 指针。先完成下节人工验收与明确部署授权。
2. 在确定的运行环境准备锁定 Node ≥22.13 与 `npm ci`，构建 `dist/cedartoy`。**不要运行上游 `npm start`、room server 或 cloud**。本轮未构建原版整站，4399 部署只需要独立静态构建。
3. 为卫星设私有绝对数据目录（专用低权限用户、目录 0700），纳入备份；确保停写/文件系统快照取得跨文件一致的房间备份，单文件原子写不能代替整目录一致快照。不要原地覆盖现有数据。实际账号/会话库路径仍以生产环境变量核实。
4. 私密运行环境：卫星 `CHESS_PLATFORM_SECRET` 随机 32 字节小写 hex、`CHESS_ROOM_DATA`、`CHESS_ROOM_PORT=8791`、`CHESS_SLIM=1`、Node PATH。主站相同 secret、`AUTOCHESS_ORIGIN=http://127.0.0.1:8791`。秘密不得写入仓库/浏览器/日志；网关继续与主站共享现有 `DUEL_GATEWAY_SHARED_SECRET`，不把它当作卫星 secret。
5. 审阅 [supervisor 示例](../deploy/autochess.supervisor.conf.example)、[Nginx 示例](../deploy/nginx-autochess.conf.example)。核实实际主站端口（项目可能有 8002/8004 链路，不能照抄）。卫星只 loopback；公网拒绝 `/_internal/autochess-gateway/`；`/autochess/*` 走主站白名单，不直连卫星。保留根 MCP POST 到异步网关的路由、禁缓冲与超时配置。
6. 将作者账号 ID 放入独立 allowlist，未确认前留空；无需任何全站 admin 授权。先在隔离预发布复现权限/密钥/断线/双弈回归，再按授权开启 `AUTOCHESS_ENABLED=1` 并发布首页与构建。
7. 真正上线时还需更新主站和既有异步网关进程以加载新代码，启动专用卫星；本任务没有执行这些操作。生产 Nginx 验证必须确认无外部端口、无可绕过的旧代理规则。

## 回滚（没有执行）

先关闭主站 `AUTOCHESS_ENABLED` 并回退/隐藏首页入口，阻止新操作；按实际发布流程回退主站与网关的本任务补丁以及静态构建，然后停专用卫星。保留数据目录和备份，不删除、不复用其他游戏目录，不启动原版服务接管这批房间。保留公网 internal 拒绝规则。确认旧双弈根 MCP/网页正常；只做 UI/网关回滚不应回滚账号库。房间格式回退需先在目录副本演练，不能用旧服务直接写新档。再次启用时继续使用原数据及身份 namespace，除非发生泄露不要无故轮换服务密钥/玩家 token。

## 尚待人工验收与明确限制

- 作者真实 4399 账号尚未提供，因此授权未启用。
- 手机真机 WebGL 性能、触屏拖棋/装备/商店的完整长局；本轮是 Chromium 软件渲染和浏览器宽度模拟，不代表 iOS/Android 实机验收。
- Gemini/Kelivo schema 回归通过，但未使用真实客户端完成动态 params、长等待/取消及通知续聊验收。
- 初期仅提供部署方案；较早版本的实际生产路由检查见后面的部署记录。本轮局外经济尚未发布，未对生产做经济操作或 R2 恢复。
- 不提供公共房间/随机匹配、任意公开观战、自动后台唤醒小机；这些是本项目范围边界。原作未开放 frenzy 沿用原状态。
- 长期归档、房间数据保留/账号删除联动、游客认领、导入导出未实现，不宣称已有。

## 首页小增量验收（2026-10-27）

本次仅改首页、现有 `autochess_adapter/web.py` 的轻量 access 分支，并扩展既有测试文件；未改 vendor、3D 棋局、MCP、统计存储模型或生产配置，未添加页面/测试入口。

- `tests_toy.test_autochess_web`：4 项真实临时 loopback 测试通过。新增检查 access 不转发卫星、匿名/小机拒绝、授权作者可见、普通账号/网站管理员不获隐式权限、撤销授权后统计读取拒绝、五区间空记录全 0、响应 no-store。
- 复用 `scripts/check_autochess_browser.py`：首页统计使用隔离临时账号与真实统计 API；仅故障/迟到响应人为注入。覆盖五时段、错误重试、权限撤销、快速换时段、关闭/遮罩/Escape、切换账号/游戏、旧 access/read 回写阻断、移动抽屉入口，以及两个按钮的实际目标。作者原版导航在浏览器测试中被拦截，不访问作者域名、不发送凭据。
- 上述完整浏览器检查通过（含既有棋室流程，0 个页面脚本错误）；现有 `scripts/check_shared_craft_author_ui.js` 的《共同手作间》/《烛》授权弹窗回归，以及 `scripts/check_home_stats_ui.js` 均通过。最终逐文件对照本次开始时快照自审，`git diff --check` 通过。
- 实际查看 360px / 430px / 1440px 截图：`artifacts/autochess-stage2/home-stats-{width}.png`。沿用 4px 边框、8px 阴影、11px 按钮/列表字号和 44px 列表行；弹窗未横向溢出，五区间在手机正常换行。
- 沿用前述部署前与回滚方案。尚未用作者真实账号或手机真机验收：numeric ID 仍未知，默认 allowlist 为空；没有部署、重启、commit/push 或联系作者。

## 正式入口部署记录（2026-10-27）

已在现有 VPS 以受控方式部署，仍需南杉真人试玩验收。

- **运行时**：从官方 nodejs.org latest-v22.x 下载并 SHA-256 校验的 Node v22.23.4，安装在 `/opt/cedartoy-runtime/node22`，并重新通过 `npm run typecheck` 与 `vite build --config vite.cedar.config.ts`。依赖沿用 vendor 当前 `package-lock.json` 对应的 node_modules。
- **服务**：独立 Supervisor `cedartoy-autochess` 以系统用户 `cedartoy-autochess` 启动，只监听 `127.0.0.1:8791`，固定 `CHESS_SLIM=1`；房间存于权限 `0700` 的 `/opt/cedartoy/data/autochess_rooms`。主站 `cedartoy` 和现有 `cedartoy-duel-gateway` 已更新/重启，双弈与其他进程保持运行。
- **密钥**：独立随机 32 字节服务间密钥仅在 root 权限的 `/etc/supervisor/conf.d/cedartoy.conf` 和 `/etc/supervisor/conf.d/cedartoy-autochess.conf` 运行环境中，不进入 Git、响应、日志。配置及已有 Nginx 站点在目录外保留私有回滚副本：`/etc/supervisor/cedartoy.conf.pre-autochess-20261027` 与 `/etc/nginx/toy.cedarstar.org.pre-autochess-20261027`。
- **路由**：在既有 `toy.cedarstar.org` Nginx 上显式代理 `/autochess/` 到 4399 HTTP 入口，拒绝公网 `/_internal/autochess-gateway/`；不代理至作者或卫星内部。`nginx -t` 成功，已 reload。
- **公网核验**：HTTPS `/`、`/autochess/`、JS/CSS、许可证均 HTTP 200；`/autochess/mcp`、`/autochess/account`、`/platform/autochess`、`/_internal/autochess-gateway/prepare` 均 404；未认证的游戏 API 均 403。直连卫星未携密钥 403，受信密钥 health 200、旧原站路由 404。
- **根 MCP**：已通过实际 4399 connector 的 `list_games` 发现 autochess，`get_guide` 返回按需协议，`play(action=rooms)` 返回正常的空房列表。这是只读生产验证，没有创建玩家棋局。
- **存档备份**：新房间目录放在现有 `data/` 下，匹配 CedarToy 每日 SQLite 一致性快照 + R2 归档脚本对 `data/` 的备份范围。上线后仍需验证新目录实际有存档时的归档、恢复与长期保留策略。
- **权限**：作者 4399 numeric ID 未提供，`AUTOCHESS_AUTHOR_USER_IDS` 未配置，首页统计界面暂不显示给普通用户或站点管理员。待作者绑定账号后另行授予仅本游戏的只读访问。

注意：以上线上 HTTP/MCP 与之前的隔离浏览器测试，**不能替代真人手机拖棋、装备、完整对局以及实际 Gemini/Kelivo 交互验收**。试玩阶段仍使用站内原入口，不另设测试页，不发布公告。

## 局外经济恢复（2026-10-27，已上线，仍待用户真机验收）

### 实际完成

- 首页卡片、作者统计 modal、站内页头/标题、根发现索引与 Guide 展示名改为 **弈间**；`autochess`、URL、canonical 身份散列和房间文件路径不变。作者原站外链、江川雪 / 63657956652 / GitHub / MIT 保留。
- 上游 slim 确实关闭了 catalog、profile、赠礼、wheel 和结算 recorder。本轮用独立 `CHESS_CEDAR_MODE=platform` 恢复这些可私域运行的功能；原 `chess-room-server.ts` 不启动、不增加原站登录或公共账号查询路由，不能通过 `CHESS_SLIM=0` 绕过。旧模式未配置新开关时仍能运行。
- 服务端每次从 4399 认证及当前绑定表构造可信身份：人类/小机和 1–5 槽各自独立钱包。房间和钱包共用原 canonical 散列；钱包懒创建，初始零余额与原作默认棋盘，不迁移作者线上账户、不接其他游戏余额。原作 `AccountStore` 只增加存储适配口与可信平台身份桥接；内部临时 session 只用于复用其方法，提交前清除，不存真实密码/平台 token，不发给浏览器。
- 购买、装备、卸下头像框、铜钱接济小机、AI 给唯一绑定人类的普通外观赠礼/礼函领取已接入。原作 `CosmeticCard` / `Avatar` / `GiftReveal` / `WheelAccount` 和 CSS/素材在同一 `/autochess/` 页面复用；未增加管理后台或测试网页。网页可选择当前绑定小机的同槽钱包，解绑后服务端拒绝代理操作。
- 对对碰保留原作试玩规则、首次 30 券、游戏币购券、概率/返券、积分兑换、外观自用和绑定家人赠送；服务端执行原作经济约束，没有现金支付入口。人类可赠给唯一归属自己的小机，AI 只赠给当前唯一绑定人类；多主人歧义拒绝。公共礼物广播和任意账号搜索不接入。
- `RoomStore` 的真实结算接入原 `settleMatch`，胜负、短局、每日额度、NPC 排除等规则未重写。已装备棋盘/头像框在实际 room view 中从本地钱包读取，原 3D 组件直接采用。旧 slim 已结束且 `payment=guest` 的房间不追补发币；尚未初始化钱包的旧局真实玩家保持 pending，等可信身份到达后入账，避免跳过账号却误标 paid。小机主人不在场的负局沿用原作允许欠款的规则，负机芯能保存/恢复，购物仍需足额，接济可还欠款。原有房间 JSON 不改路径、不重置。
- 根 MCP 新增按需 `action=meta`，字段在 Guide；公开工具 schema 不增加字段。Guide 从 1539 增至 2377 字符，增加的是局外实际操作字段/重试规则/作者原站，非每次常驻注入；这是字符数而非 token 数。异步棋局等待继续走既有网关。

### 账本和备份边界

`CHESS_ROOM_DATA/economy/accounts.db` 保存原作 AccountFile（state）、额外事务重试凭据（receipts）。钱包、拥有物、领取记录、转盘、绑定快照、原作结算/赠礼账本保留原模型；不是新增一套经济规则。每次加载新状态，在 `BEGIN IMMEDIATE` 内执行原方法并将结果与请求凭据一并提交，失败整体回滚；WAL + FULL，同步跨进程互斥。相同 requestId 改参数/收礼人报错，重放前仍校验当前绑定。坏版本、无效 JSON/钱包数据拒绝读取并保留原档，不当成首次钱包重建。

`data/autochess_rooms` 下的钱包由既有每日 SQLite 快照与 R2 归档覆盖，新增房间/钱包打包前校验；先复制房间，后快照钱包，避免 paid 房间领先于结算凭据。详见 [备份手册](BACKUP.md#弈间局外钱包工作树实现2026-10-27)。真实备份脚本已在临时目录运行并解包核验，R2_REMOTE 为空；本轮没有触碰生产存档、备份或 R2。

### 定向验证

- Python **38 项通过**：`test_autochess_web`、`test_autochess_adapter`、`test_mcp_schema_selection`、`test_root_mcp_protocol`、`test_autochess_async_edge`。真实临时 4399 账号 DB、Bearer/AI token、loopback 卫星与 HTTP；覆盖人/机/槽、伪造身份、实时解绑、未知/越权经济动作、并发重试、旧路由拒绝、作者授权隔离、实际备份归档，以及根 MCP/schema/等待。
- vendor `test/*.test.ts` **29 项通过**，包括原作棋谱/战斗测试、新钱包/赠礼/转盘与原有私人房边界。检查重复购买/兑换/赠礼/领取不重复扣发、余额不足、收礼方校验、解绑拒绝旧请求、注入凭据写失败的整体回滚、坏钱包保留、guest 无钱包但仍可玩。自审补充未初始化旧局玩家及原作负余额边界后，重跑其中 11 项通过。
- **实际单人 NPC 完整局**：原作引擎 duel，逐轮选择装备/站位/ready，12 轮结束，玩家负，按原作发 **24 铜钱**；冷加载不重复结算。胜局及 AI 发币使用原 `settleMatch` 的确定性测试输入验证，不能冒充真人/真机完整胜局验收。
- `scripts/persistence_check.py --autochess` 通过：create → mutate → read → verify 四个独立进程，检查房间/NPC、钱包、购买、赠礼/领取及重试记录。保留该脚本其他任务的既有修改，未运行全平台持久化测试。
- `tsc --noEmit`、独立 Vite 构建通过。使用已有锁定依赖与 `/opt/cedartoy-runtime/node22` v22.23.4；未改锁文件。构建输出 **`artifacts/autochess-economy-build`**，没有覆盖线上 `vendor/autochess/dist/cedartoy`。仍有原 3D renderer 大于 500 KiB 的 chunk 提示。
- 既有隔离浏览器检查验证 360 / 430 / 1440px 商城和转盘、真实 API 购置皮肤、进入实际 3D 棋室、原装备/商店/回放、刷新/断线/围观和首页作者统计；实际切换槽、小机、另一位人类账号验证钱包清除与隔离。浏览器购买测试的初始资金为临时 fixture；上面的 NPC 整局发币测试未使用该 fixture。实际截图中 3D 棋盘显示购买的玫瑰古堡，未出现原站/外站 API 请求或页面脚本错误。截图已人工查看，不仅检查横向溢出。
- 本轮小量证据保留在 `artifacts/autochess-stage2/`（`economy-*`、当前 `board-*`、`browser-result.json` 及 `economy-validation.json`）。无用的本轮 `/tmp` Playwright 包/浏览器缓存在检查后删除；已有运行时、vendor 锁定依赖和本轮待审构建保留。

### 尚未完成 / 人工验收

- **好友人类之间的转盘赠礼未接入**：当前身份桥只提供实时绑定家人；尚无不泄露全站账号的好友收礼授权流程，不用公开用户名搜索代替。普通 AI→主人赠礼、绑定人机转盘互赠已接好。
- 原作公共匹配、大厅、公共社交/广播继续不开放；原作未开放的 frenzy 不改规则。作者账号 ID 仍未知，统计许可默认空。原作反馈奖励/运营发币/账号管理等不在本轮恢复的局外范围内，也未提供绕过经济的客户端动作。
- 手机真机触控/WebGL 性能、真人与真实小机长局/胜局、Gemini/Kelivo 实际经济交互，以及正式 R2 钱包恢复仍需发布前/后按授权验收。浏览器为 Chromium 软件渲染。
- 钱包仍复用原作整份 AccountFile 模型；大量账户/历史增长后的存储拆分和结算凭据归档未做。不能提前清除重试/结算账本以节省空间。

### 发布迁移与回滚（仅方案，未执行）

1. 独立审查本轮主仓库 diff 与 vendor 补丁，不能提交 vendor 指针或夹带其他任务改动。保留旧代码/静态构建/运行配置；先在数据一致性副本验证。
2. 未来获准发布时，先阻止新操作并停写《弈间》专用服务，备份完整房间目录及可能已有的 wallet SQLite（使用 `.backup`，不裸拷运行中的 DB）；不得清空生产目录。原 slim 到本版无需房间重写，新增 economy 子目录由服务低权限用户写入，目录 0700/DB 0600。
3. 审核使用同一个 CHESS_ROOM_DATA、原 secret、Node ≥22.13 和 `CHESS_CEDAR_MODE=platform`；其他路由、4399 认证和作者 allowlist 不扩大。发布主站/网关的局外桥与新静态构建、专用卫星后，在授权范围内核验私房、真实结算、装备、禁用路由及首份每日 R2 包。**不能直接运行原站服务器或用 CHESS_SLIM=0 暴露原站。**
4. 回滚优先关闭平台经济模式并恢复旧静态构建/代码；完整保留 `economy/accounts.db` 和 receipts，不删除钱包，也不将新账本替换为空库。旧 slim 模式中新完成的局仍不发局外币；重新启用时这些历史 guest 局不追补。需要回滚数据时从同一归档恢复整套房间+钱包并先在副本核验，不能只回滚钱包导致重复奖励。

经济代码已在生产启用并提交推送；未联系作者、未发布公告，未手动操作 R2。真实手机/小机实机长期验收仍待完成。

### 经济版本上线核验（2026-10-27）

- Git 提交 `76b9f71` 只含《弈间》经济相关改动，历史清理和其他任务工作树修改未混入提交。 vendor 仍保持作者独立源仓，修改以 `docs/patches/autochess-cedartoy.patch` 保留。
- 升级前已停独立游戏进程并对全部现有房间制作权限 0600 的回滚包：`/opt/cedartoy/backups/autochess-before-economy-20261027.tar.gz`；原静态构建保留在 `vendor/autochess/dist/cedartoy-before-economy-20261027`，Supervisor 配置私密备份 `/etc/supervisor/cedartoy-autochess.conf.pre-economy-20261027`。
- Supervisor 仅为独立服务加 `CHESS_CEDAR_MODE=platform`，不开放原站登录/账号/公共匹配。默认 URL、房间路径和 `CHESS_PLATFORM_SECRET` 未修改；主站、异步网关及游戏卫星按顺序重载。原站公开 `catalog` 当日返回 `wheelEnabled=true`，本版沿用作者目前开放的对对碰试玩功能及其原始规则。
- 上线前独立重跑 15 项 Python 定向用例及 6 项 TypeScript 钱包/原作集成用例，另外对「观看绑定小机」的只读钱包权限修复重跑 6 项 HTTP 测试，均通过；重新运行 Node v22 `typecheck`/Vite 构建通过。
- 线上 HTTPS 首页、`/autochess/`、新版 JS/CSS 与 MIT 许可 HTTP 200；匿名经济/对局/作者统计 API HTTP 403；原站账号路由、MCP 路由及内网握手公网 HTTP 404；钱包 SQLite `quick_check=ok`，独立游戏进程及主站/双弈网关运行正常。实际 4399 MCP `list_games` 返回 `autochess·弈间·江川雪`，`get_guide` 确认局外 `meta` 协议已对客户端开放。
- **人类对绑定小机仅可查看钱包、已拥有装扮和转盘状态；购买/装备/赠礼/领礼必须由小机自己的 MCP 身份操作。** 无其他账户冒名操作。
- 当日线上原房间已保留，没有重置玩家存档。正式钱包 DB 新建于 `data/autochess_rooms/economy/accounts.db`；每日 R2 备份代码涵盖该库，待下一次生产归档和独立恢复检查。试玩阶段不发布公告。
