# 乌有乡（nowhere）平台接入

本次只开发和测试，未 commit/push、未部署、未重启、未发公告。真实验收修复与证据见下方“当前证据与交接状态”。

## 上游与署名

- 原项目/保留的 origin：`https://github.com/yuyixuanfu/nowhere.git`
- 验收目标版本：`f0803c1053e5f97a6f44bab26e15043d71825ec7`
- 本地：`vendor/nowhere` 独立仓库，未修改上游文件，不提交 vendor 指针，不推作者仓库。
- 作者、原项目、许可、平台适配声明与第三方数据说明登记在 [`links.py:AUTHORS`](../links.py)。作者青少年小鼠狂饮乙醇（小红书号 94326164228）；CC BY-NC 4.0。GeoNames、WorldClim、Met Museum、iNaturalist 等数据各遵原提供者许可。
- game 为 `nowhere`，与既有 `travel` 的代码、目录、卡片及存档完全分开。

## 接入范围

根 MCP 的目录、guide、play 分发、身份/1–5 槽位、游客认领、my_saves、删除、统计和防沉迷均注册。根 MCP 继续共用已有 `path_token or bearer_token` 鉴权。`play(game="nowhere", action="schema")` 返回动作契约。

源码实际有 28 个 `@mcp.tool()` 注册，全部接入：open_door、continue_journey、walk、listen、look_around、ask、mark、marks、where_am_i、souvenir、give_souvenir、bury、deliver、postcards、walk_to、journeys_list、atlas、wait、look、say、quotes、talk、journal、notebook、walk_alone、guess、reveal、drift。清单位于 `nowhere_adapter/schema.json`。工作进程启动时还会读取真正的 `mcp.get_tools()`，对比名称与参数名；此运行时校验已在 Python 3.11.13 的真实工作进程中通过。

上游 README 还列出 send_postcard、switch_journey，但没有对应 MCP 注册。本适配调用已有 send_postcard_impl，并通过已有开门/旅程选择实现 switch_journey；另提供 schema/new/export/import。游戏叙事与行动仍交给上游执行。

网页 `/nowhere/?player=<绑定AI的账号[:slot]>` 复用上游 `static/index.html`、`world110m.js`，保留 `relief.jpg` 的静态路由。当前上游页面主要用矢量地貌，不强行替换为位图。地图拖动/缩放、足迹、标记、动物目击、电台、明信片墙及回信仍来自上游界面和 web.py。主旁观页仅保留鉴权/私有 API 与图片路由桥接、安全转义和默认隐藏的错误提示；不注入署名/许可/返回入口说明，不改变原版按钮、移动端布局或键盘操作。真实 listen 足迹的电台链接保留原版 target="_blank"、rel、文案及 safeHttpUrl 的 HTTP(S) 过滤；桌面仍开新页。当前状态已有的“📻 电台名”由桥接原位转为链接，仅在 state.radio.stream_url 是显式 HTTP(S) 地址时启用，继承原字号/颜色，不新增区块；移动端同样进入同源音频页。空值或普通足迹不能生成电台链接，也不向存档补造 stream_url。所有足迹电台链接的 href 均指向同源播放页（桌面新标签、复制链接也不直跳源站）；coarse pointer 且移动端 UA 时在当前页打开 `/nowhere/radio?player=<原槽位>&url=<原始流地址>`。该文档复用现有 Secure/HttpOnly cookie、账户与绑定槽位鉴权；只包含原生 `<audio controls autoplay playsinline>` 和固定返回原槽位的“返回旅程”链接，自动播放受限时可点原生控件。URL 经服务端 HTTP(S) 协议白名单、主机/端口/凭据/控制字符检查和 HTML 转义；不重定向至传入 URL。audio 的 src 指向同源 `/nowhere/radio/stream`；该路由再次检查登录、绑定槽位及当前槽位的持久化电台 URL：footprints.json 包含跨旅程足迹；当前 journey.json 与 journeys/index.json 所引用的历史 WorldState 另存 radio_station / last_env.radio（上游 /state 从后者返回电台）。只读取这几个明确的 stream_url 字段，不递归扫描其他字段、未索引文件或其他槽位。页面与流端点使用同一 URL 规范化授权：统一主机/默认端口、UTF-8 与等价百分号编码，忽略 fragment；保留 query 顺序、重复参数、大小写路径和编码分隔符的差异，不能把不同目标合并。电台状态会先于 listen 足迹持久化，不能因尚无收听记录就拒绝该槽位实际保存的电台，也不能据此伪造收听足迹。渲染桥接拒绝空/相对电台地址，避免上游 new URL("") 把当前网页误当音频流。每次解析与最多 3 次重定向均拒绝非公网 IP，并固定实际拨号 IP、保留 TLS 主机校验。代理只逐块透传（32KiB），无转码、落盘或整流缓存；仅允许音频 MIME 和少量 ICY 头，最多 8 路，满额 503，连接 8 秒/读写 30 秒超时，客户端断开即关闭上游。所有设备播放均经过该代理，会消耗服务器进出流量；需真机复验，未声称浏览器测试能证明手机播放兼容。Android intent 与隐藏 Audio 播放逻辑已删除，桌面仍保持原版 _blank；主页面 CSS/UI 不变。署名与许可登记仍在首页、guide 和 links.py。登录恢复 access 页面独立保留，不启动上游裸 HTTP 服务。首页选择器使用只读 `/api/nowhere/saves`，仅检查登录人类绑定小机的 1–5 槽乌有乡 save_summary；不调用其他游戏汇总、迁移或 worker。

电台健康兼容只在平台层实现，不修改 vendor 清单或用户原档。worker 过滤 `dead=true` 和已知失效 URL（含 Radio Jordan 的 `https://www.jrtv.gov.jo/radio/stream`）；旧档恢复只清理匹配的 `radio_station/radio_pos/last_env.radio`，足迹响应移除坏流链接但保留文字。选中或复用 fallback 的游戏动作按需验证，复用代理的 SSRF/IP/重定向检查，独立短进程将 DNS、TLS、响应头总等待限制为 4 秒；仅 `200 + audio/*` 可通过。每进程内存健康缓存最多 256 项，成功 1 小时、失败 5 分钟，进程退出即失效，不写 data；普通 state/history、恢复和授权只查缓存，不访问外网。代理每次播放仍重新检查响应，非音频/非 200 失败也更新其进程的缓存；worker 与 HTTP 进程的动态缓存不共享，已确认坏 URL 的静态禁用则两侧一致。同国规则沿用上游，验证失败可返回无台，不跨国补台。

## 隔离与存档

主服务继续运行其原 Python；通过 stdin/stdout 私有协议启动 Python ≥3.11 常驻工作进程，无新监听端口、无内部身份 HTTP 头可伪造。

每个进程终生只绑定一个 canonical player_id/slot；环境中的 NOWHERE_HOME、NOWHERE_TRAVELER_NAME 在创建子进程时固定，不在运行中切换。重依赖只在工作进程初始化时加载。默认至多两个驻留进程，可用 `NOWHERE_MAX_WORKERS` 调整；容量满时淘汰空闲进程，忙时最多排队 15 秒。连续访问同一玩家复用进程；超过池容量的不同玩家轮换可能产生冷加载，应部署前测量内存并设置容量。单动作工作上限 140 秒，父进程 IPC 上限 150 秒；超时杀整组子进程并保留上一次私人快照。

所有上游私人 JSON 文件在进程临时工作目录内执行；平台权威档为：

```
data/vendor_saves/nowhere/<player_id>/save.json
data/vendor_saves/.nowhere-locks/<player_id>
data/nowhere_shared.db
```

save.json 封装当前旅程、journeys 索引与各历史旅程、图鉴、手账、标记、明信片、私人留言等全部 JSON、私人 PNG 图片及随机状态。文件名白名单阻止目录穿越。文件锁在玩家目录外，因此认领/删除不能绕过旧 inode 的锁。完整动作在锁内恢复→调用→快照→fsync→原子替换；进程中途退出只丢当次未提交动作，不发布半套多文件档。

除上游 _state 外，还保存独立 RNG、Python 全局 RNG、提示/意外计数器、近期显著事件类型和明信片计数器。补上上游 serializer 遗漏的 cotraveler_alone；走路完成后再保存，覆盖上游在里程累计前保存的中间态。明信片编号在恢复旧旅程后仍保持跨旅程递增。

已有档的 open_door、新建完整档 new、import 都需要 confirm=true。open_door 仍保留旧旅程；new 清空整个私人存档。损坏档原文件保留，同时按内容摘要保存证据副本并拒绝自动新建；需要人工修复或明确删除后重建。import 仅接受相同上游版本的完整平台导出，并在工作进程加载校验后提交。

海报仍为可选异步任务：每个任务捕获固定玩家、卡片及存档代次，独立临时输出；完成后拿同一玩家锁，仅合并图片字段，保留后来到达的回复。删除卡片、删除档或导入/重开改变代次后，旧任务不回写。删除和认领会清理旧工作进程的临时缓存。海报任务在工作进程被淘汰时可能取消；原版 SVG 卡片仍可用。

## 同游与网页权限

共享 SQLite 只保存原版旅者注册、位置、脚印、显示名、脚印相遇次数与成对相遇冷却，所有读改写由 BEGIN IMMEDIATE 事务保护。对原版 travelers.py 的读写接口作适配，仍使用其距离、概率、冷却和文案规则。内部键为稳定平台 ID/槽位；昵称只用于展示，开门昵称与走路环境变量不再生成两个旅者。

`cotraveler="1"` 允许脚印与相遇；`"quiet"` 仅脚印；`"0"` 关闭同游。walk_alone 持久保存独行标记，并撤下该玩家已有共享痕迹；同一旅程冷恢复/热续玩保留独行，真正新开旅程仍按上游行为重置。私人的图鉴/手账/游记/标记/留言/明信片不进入共享库。无实时聊天室；上游 @留言无已接通的发送工具，此接入明确不开放 @留言。

网页每次数据读写和明信片图片请求都使用平台账户校验当前绑定与指定槽位。普通域名的首页入口先切换到 HTTPS，再用平台已有 localStorage 登录凭据通过同源 Bearer 请求建立 Path=/nowhere/、HttpOnly、Secure、SameSite=Strict cookie；正常导航 URL 不含 token。HTTP/HTTPS 的 localStorage 不互通，只有 HTTP 登录时会引导在 HTTPS 首页重新登录，不转运凭据。旧 HTTPS query-token 入口仍兼容并立即 303 去掉 token；普通 HTTP 文档不交换凭据。localhost/loopback 保留浏览器本地开发例外，不能据此认定普通 HTTP 域名验收通过。

缺失或失效会话的文档返回纸色提示页及首页登录/选择入口，不返回 MCP 换 token 指导或 JSON 白屏；同源平台登录仍有效时可恢复 cookie，先验证 cookie 能回送再重新打开页面，避免循环。API 未登录仍 401、未绑定/越权槽位仍 403、未列入白名单的接口或文件仍 404。提前拒绝写请求时显式关闭连接，避免未读请求体污染下一条 HTTP/1.1 请求。请求体中的 player/player_id/slot 被拒绝，跨站写请求被拒绝，删除明信片需要 confirm=true。原有公开静态文件及恢复页 CSS/JS 不包含私人数据。

音频页短验收（真实临时鉴权/绑定/cookie，合成引擎状态，不启动重引擎）：

```sh
PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright python3 tests_toy/nowhere_radio_acceptance.py
```

非 loopback 会话专项验收复用现有临时库/真实引擎夹具，使用 `host-resolver-rules` 和一次性自签证书测试实际 HTTP/TLS（仅测试浏览器忽略该证书，不安装系统证书）：

```sh
NOWHERE_SCREENSHOTS=/tmp/nowhere-session-evidence PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright python3 tests_toy/nowhere_session_acceptance.py
```

此入口需要已有 `openssl`，不会安装依赖；浏览器没有全局 Authorization 注入，明信片附加确定性的私人 PNG 测试夹具以检查图片权限，不代表远程海报生成已验收。HTTP 首次失败复现证据见 `/tmp/nowhere-session-fix-baseline/reproduction/`。

2026-10-02 会话专项通过：360/428/1280px 的非 loopback HTTP→HTTPS 入口、刷新、cookie 丢失恢复、缺失/过期/错误账号提示、私人图片与回信、越权 403、当前身份被拒绝时不回退旧 cookie；JS errors 为空。最终证据在 `/tmp/nowhere-session-acceptance-final-2/`，日志 `/tmp/nowhere-session-fix-baseline/browser-final-2.log`。16 项针对性单测及独立进程持久化检查通过。未部署，线上代理链和真实手机验收由主助手部署后处理；下方 39 项记录是此前独行恢复阶段的证据。

此前原版 UI 恢复与同页电台导航验收（现已由同源原生音频页替代）：额外说明块、手机重排和键盘增强已撤回；仅足迹电台流链接同页打开。`test_nowhere` 27 项及署名测试 1 项通过（无跳过），其中真实两玩家脚印/相遇、3km/24h/15% 规则、独行冷恢复和私人边界通过。非 loopback 浏览器 360/428/1280px 原版地图、足迹、明信片、鉴权恢复与电台同页 `200 audio/mpeg` 通过，popup=0、JS errors=[]；危险协议不生成电台链接。完整退出码与日志位于 `/tmp/nowhere-original-ui-baseline/`，最终截图/报告位于 `/tmp/nowhere-original-ui-radio-verified/`。电台使用临时足迹中的有效静音 MP3 测试端点，未声称真实内嵌手机硬件或外部电台播放已验收；原版窄屏排版和回退字体竖排表现按要求保留。未部署。

导入导出不含共享库；认领和删除撤下旧身份共享痕迹。定时游客清理与账号注销文件清理也走同一存档锁。备份继续使用平台现有的一致性快照流程，覆盖整个 data（包括新共享 SQLite）；不可只复制 SQLite 主文件。

## 宿主环境准备与真实验收

主助手已执行准备脚本，独立 `.venv-nowhere` Python **3.11.13** 与固定依赖就绪，宿主系统 Python 仍为 3.10.12，余盘约 **2.8 GB**。本轮复用该环境，没有重装或升级。临时端口监听与 Chromium 已实际运行；此前沙箱限制不再阻塞本轮验收。

准备入口：[`scripts/prepare_nowhere_env.py`](../scripts/prepare_nowhere_env.py)。无参数只检查；`--prepare` 才下载安装。只写 `.nowhere-runtime/` 和 `.venv-nowhere/`，不替换系统 Python、不改共享环境/CLI 配置，不启动服务。解释器来自 [python-build-standalone 20250918](https://github.com/astral-sh/python-build-standalone/releases/tag/20250918)，固定 **CPython 3.11.13**、Linux x86_64/glibc install_only_stripped，下载约 29 MiB，SHA-256：

```
511ceceeff184ad742a35583755f5070c0274cc0f0bb5c9218abd939240a2146
```

直接依赖固定在 `nowhere_adapter/requirements.txt`，pip 固定 25.2。首次成功后 `.nowhere-runtime/manifest.json` 记录解释器版本/摘要、上游 SHA、requirements 摘要、完整 pip freeze 和所下载依赖 wheel 哈希，`resolved-requirements.txt` 保留完整版本清单。传递依赖首次由官方 PyPI 解析，随后以该完整记录校验；不是预先冻结全部传递依赖的离线供应包。重跑已完成环境只核对版本与 pip check；版本漂移/无归属标记的已有环境立即停止，不覆盖升级。失败留下已标记的专用目录，可修复外部条件后重跑；只清本脚本此次临时下载，不清他人缓存。

首次至少 2048 MiB 空余，全程监测并保留 768 MiB 余量；下载单连接 20 秒、总计 180 秒、最多 40 MiB，校验后才解包（最多 256 MiB）。pip 仅 wheel、禁缓存、官方 PyPI、连接超时 20 秒/重试 1 次、安装总计最多 15 分钟。失败/超时/空间不足退出非零，不继续验收、不打印凭据。不下载可选地形包；默认不装 osmnx/geopandas 海报依赖。

额外宿主前提明确如下：Linux x86_64/glibc，现有 Python ≥3.10、git、Node、可用 Playwright 模块及 Chromium 和它的系统动态库；可访问 GitHub Release 与官方 PyPI。脚本不运行 apt、不安装浏览器或系统库。已找到的模块/浏览器路径见以下命令；若宿主缺失，需主助手另行准备，脚本会明确失败。

环境已就绪。需要复验时直接运行原验收入口（无安装、部署或重启动作）：

```bash
cd /opt/cedartoy
PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright \
  PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
  python3 scripts/accept_nowhere.py
```

验收入口：[`scripts/accept_nowhere.py`](../scripts/accept_nowhere.py)，使用平台原 Python 驱动、独立 Python 驱动工作进程。不读取真实玩家档、不运行生产服务、不改生产配置。导入 server **之前**将账号库、sessions、tarot、nowhere 私人档及共享库指向新临时目录；测试账号和 token 仅存在该次临时库，不打印。浏览器临时服务只监听 `127.0.0.1` 随机端口，结束关闭。新增证据默认写 `/tmp/nowhere-acceptance-日期-随机串/`，可用 `--output <不存在的新目录>` 指定；测试数据结束删除、证据保留。

正式入口复用并执行：

- `tests_toy.test_nowhere`、署名/准备安全测试、根 MCP 协议与认领码回归；强制 `NOWHERE_REAL_TEST=1`。**任何 skip/expected failure 都失败**。
- `tests_toy/nowhere_live_acceptance.py`：真实引擎初始化时核对全部 **28 项实际 FastMCP 注册与参数名**；根 MCP 真实路径 token/Bearer 和服务端身份覆盖；两账号×两槽位真实开门、私人标记/原话、走路、关闭进程后续玩、同槽并发真实动作、同名稳定 ID、共享/私有边界、独行/关闭同游、覆盖拒绝、明信片。
- 实际 HTTP 使用平台 `_current_account` 和绑定查询，不 mock 鉴权；验证未登录、他人绑定、非法槽位、伪造身份、未确认删除等拒绝；未绑定人类拒绝、实际留言/回信跨冷恢复保留、删除不复活。无关公告/活动/历史迁移由测试夹具关闭。
- `scripts/check_nowhere_browser.js` 的 **live-real-engine** 模式，复用原版界面与真实临时 HTTP 响应，不使用合成 route fulfillment；360/428/1280px 的地图、明信片、足迹各一张，共 9 张截图，操作缩放/回信/Escape，检查溢出和 JS 错误。脚本仍保留单独的 synthetic-fixture 模式用于 UI 调试，但不能计入正式验收。
- `scripts/persistence_check.py --nowhere`，复用三个独立进程的真实开门→say→quotes 链路，所有档均在临时目录。不要用无参数的全游戏持久化入口替代本入口。

`report.json` 与分阶段日志记录结果/耗时；缺环境、缺浏览器、缺截图、超时、任何测试错误均非零退出。全自动项通过时状态为 `AUTOMATED_PASS_VISUAL_REVIEW_PENDING`，仍须主助手实际看截图，不能把自动断言当视觉通过。`--preflight` 只检查前提，结果为 `PREFLIGHT_ONLY`，不是验收通过。

生产将来仍可默认使用 `.venv-nowhere/bin/python`，或显式配置 `NOWHERE_PYTHON`。`NOWHERE_SAVE_ROOT` 与 `NOWHERE_SHARED_DB` 只用于隔离测试或配套配置；生产默认必须与平台 VENDOR_SAVE_ROOT 一致。此次没有更改服务环境，也没有部署。默认两个工作进程的内存/冷启动容量仍待宿主测量。

## 当前证据与交接状态

**本轮真实验收修复**

- 根因复现：walk_alone 后 `journey.json` 和 `journeys/北京.json` 都为 `cotraveler_alone=true`，共享注册已撤下；冷续玩却同时变回 false 并重新公开注册。原因是上游 continue_journey 调用 open_door_impl(resume=True)，随后共用的同游开门分支无条件清独行并注册，非多文件快照不同步。
- `nowhere_adapter/worker.py` 仅在“续玩已有独行旅程”调用期间令该工作进程的同游适配返回禁用，阻止重置与任何短暂公开；finally 恢复正常判定，不改变持久化 cotraveler 配置、不切换全局环境变量。上游文件、真正开新门的语义不变。
- 新增真实回归核对冷恢复、热续玩、当局/旅程快照一致、活动/归档共享注册均不出现，以及真正新开门重新启用同游。原 live 验收的独行/共享退出断言完整保留。
- 修复浏览器脚本选择：HTTP 测试已回信的卡片位于原版“寄回家的”页；进入该页再验证回信。足迹截图等面板滑入完成，避免用动画中间帧充当截图证据。
- 历史验收曾针对宿主回退字体增加竖排按钮兼容样式；本次按原版 UI 恢复要求撤回该样式与对应字形断言，保留原版 vertical-rl，不修改上游字体或页面。

**已通过（最终实际执行）**

- 完整入口 `scripts/accept_nowhere.py`：**39 项通过，0 失败、0 错误、0 跳过**，真实引擎/网页/浏览器阶段 132.62 秒。真实 28 项 MCP 注册校验、根 MCP 路径 token/Bearer、两账号两槽位、独行冷续玩、并发、私有/共享边界、覆盖拒绝、明信片/留言/回信权限及冷恢复、删除不复活均通过。临时共享库/账号库完整性检查通过。
- `persistence_check.py --nowhere`：**PASS**，三个独立进程开门→say→quotes，18.79 秒，临时档清理完成。
- 最新证据：`/tmp/nowhere-acceptance-20261002-192945-17e874/` 的 `report.json`、`real-tests-and-live-browser.log`、`cross-process-persistence.log` 和 `screenshots/report.json`。
- 原版实际页面 **360/428/1280px** 地图、明信片、足迹共 **9 张截图**已逐张用图像查看工具检视；入口无字形重叠，足迹面板完整展开，回信输入/按钮可见，字号与原版层级一致。三宽度均无横向溢出、JS errors 为空。审查证据 `visual-review.json`；自动报告保留其原始 `AUTOMATED_PASS_VISUAL_REVIEW_PENDING` 状态，不将自动结果冒充人工阅图，Codex 阅图结果单列记录。
- 署名一致性测试通过：三款仍为 **青少年小鼠狂饮乙醇（小红书号 94326164228）**，游戏名、GitHub 作者与原项目/许可链接保留。上游 HEAD=`f0803c1053e5f97a6f44bab26e15043d71825ec7`，上游工作树干净。
- Python 语法、浏览器 JS 语法及 `git diff --check` 通过。未重装/升级独立环境，未改系统 Python、CLI 配置或生产服务。

**验收边界**

- 本轮不需要宿主再补跑完整入口；主助手可按既有命令复验并处理部署，本任务没有执行部署。
- 全 28 动作的注册已校验，但仅上述代表动作链路实际执行；其余动作逐项语义 smoke、外部天气/电台服务降级、并发负载/内存容量、可选海报进程仍未实测。未声称真实触屏硬件、屏幕阅读器或 200% 缩放已验收。
- 主助手首次失败证据 `/tmp/nowhere-acceptance-20261002-191623-5e1199/` 与本轮第一次浏览器选页失败证据 `/tmp/nowhere-acceptance-20261002-192152-016848/` 保留；它们是修复前记录，不能代替上述最终结果。

**功能未实现/明确不提供**

- 上游 @留言没有发送工具，此适配未实现 @留言发送，不以网页私人留言冒充同游消息；没有实时聊天室。
- 可选真实路网 PNG 海报依赖不包含在本准备环境，后台隔离/合并代码已写，实际海报生成尚未验收；原版 SVG 明信片/回信属于必测链路。

## 本任务增量与基线

- 本轮仅修改 `nowhere_adapter/worker.py`、`tests_toy/test_nowhere.py`、`scripts/check_nowhere_browser.js`、`nowhere_adapter/assets/platform.css` 和本说明。真实 live 测试断言、环境脚本、固定依赖、server.py、index.html、AUTHORS 及上游均未在本轮更改。修复前基线 `/tmp/nowhere-solo-fix-baseline/`，本轮补丁 `solo-fix.patch`；既有任务总补丁同步更新。

- 此前署名修改：`links.py`、`server.py`、`index.html`、`vendor_cmd_adapter/guides.py`、`turtle-soup/backend/guides/ciyuwu.md`、`nowhere_adapter/web.py`、`nowhere_adapter/guide.md`、本说明。
- 此前交接新增：`scripts/prepare_nowhere_env.py`、`scripts/accept_nowhere.py`、`tests_toy/nowhere_live_acceptance.py`、`tests_toy/test_nowhere_handoff.py`；修改 `requirements.txt`、`.gitignore`、既有浏览器脚本和持久化脚本（nowhere 超时及临时路径报错）。
- 本窗口此前实现：`nowhere_adapter/`、`vendor_cmd_adapter/nowhere.py`、`tests_toy/test_nowhere.py`、原版网页适配与首页入口、主服务注册；已有 `account_deletion.py`、`scripts/clean_guest_saves.py` 等仅本任务增量，未改 AGENTS。
- 初始基线 `/tmp/nowhere-baseline/`；此前交接基线 `/tmp/nowhere-handoff-baseline/`，均保存 status 与相关文件副本。任务总补丁 `/tmp/nowhere-baseline/task-only.patch`，此前交接增量 `/tmp/nowhere-handoff-baseline/handoff-only.patch`；它们基于相应脏工作树基线，不是对干净 HEAD 的全仓库 diff。无 vendor 指针、AGENTS 或其他任务改动，**当前工作树已含这些内容，不要重复应用**。

本轮未 commit/push、未部署/重启、未发公告、未操作真实玩家数据。
