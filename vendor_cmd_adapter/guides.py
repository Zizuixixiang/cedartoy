SAVE_SLOT_GUIDE_NOTE = (
    "\n\n[存档槽] 每游戏5槽，params 传 slot=1-5（缺省1；即 player_id 加 :2~:5 后缀；游客单槽）。"
    "export/import 按槽生效：空槽导入免 confirm，覆盖需 confirm=true。"
    "跨槽复制=export 后 import 到另一槽。查各槽：account(action=\"my_saves\")。"
)


GUIDES = {
    "delve": """# Delve（下矿）
调用：play(game="delve",action="new") 开局；之后 play(game="delve", action="cmd", params={"command": "play 3"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id

常用指令：
- new — 新建矿井
- handshake defaults — 使用默认陪玩偏好完成开局确认
- dig — 下镐探索一次
- play 3 — 半托管连续探索 3 次
- status — 查看当前状态
- museum — 查看藏品图鉴
- journal — 查看探险手帐
- map — 查看当前区域地图
- titles — 查看称号
- upgrade <item> — 升级装备（pickaxe / lantern / rope / backpack）
- choose <A/B/C> — 处理当前轻决策
- sell common — 出售普通藏品
- return — 回地面营地
- help — 查看全部指令

存档导出：play(game="delve", action="export")
存档导入：play(game="delve", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）

原作信息：
作者：包工头（QQ 601546041）／仓库：github.com/liyana31811/delve-ai-companion／经作者授权接入。""",
    "travel": """# Travel（旅行 MCP）
调用：play(game="travel", action="new") 开局；之后用 play(game="travel", action="cmd", params={"command": "trip_plan", "dest": "东京"}) 调用旅行工具；持久 MCP 地址可省 player_id。

11 个命令及参数：
- trip_plan — 查询目的地、行程与报价；参数：dest（目的地，可空）、style（青旅背包/舒适/轻奢/豪奢，可空）、party（together/solo，默认 together）
- trip_start — 正式出发；参数：dest（目的地）、party（together/solo，默认 together）、style（默认舒适）、restart（是否结算旧旅程后重开，默认 false）
- trip_here — 查看当前站，不推进；无参数
- trip_go — 前往下一站并推进旅程；无参数
- trip_collect — 收藏本趟纪念品；参数：name（名称）、line（纪念语）、default_id（默认纪念品 ID）、image（自定义图片路径或 URL）
- trip_postcard — 独自旅行时寄明信片；参数：line（必填，明信片文字）、spot_id（景点 ID，可空）
- trip_diary — 写本趟旅行日记；参数：text（必填，至少 50 字）、title（标题，可空）
- trip_return — 提前收趟或独自旅行回家；无参数
- care_checkin — 记录照顾自己的事项；参数：item（必填：喝水/吃药/运动/早睡/吃得健康/其他）、note（备注，可空；仅 caretaker 经济模式记账）
- wallet_status — 查看盘缠、XP 和最近账目；无参数
- trip_shelf — 查看纪念品、明信片和日记；参数：read_diary（trip_id 或 last，可空）

存档导出：play(game="travel", action="export")（返回以文件名为 key 的 JSON 对象）
存档导入：play(game="travel", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）

原作信息：
作者：沈澈 & sevenleft／仓库：github.com/shenchesilas-stack/travel-mcp／经作者授权接入。""",
    "leek": """# Leek（韭菜修炼之道）
调用：play(game="leek",action="new") 开局；之后 play(game="leek", action="cmd", params={"command": "market"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id

常用指令：
- status — 查看账户、持仓、净值
- market — 查看全市场行情
- market tech — 按板块查看行情
- sector tech — 查看板块详情
- sentiment — 查看市场情绪
- cycle — 查看当前周期阶段
- news — 查看最新新闻
- research <股票> — 深度研究
- buy <股票> <数量> — 买入
- sell <股票> <数量/all> — 卖出
- pnl — 查看单股盈亏
- history — 查看交易历史
- compare <A> <B> — 对比两只股票
- journal — 查看交易日志
- wait <天数> — 推进交易日
- help — 查看全部指令

批量：分号串联，最多8条，如 research titan; buy titan 10; wait 5; sell titan all
末尾有紧凑状态栏JSON，省token优先读它。
等级15后 new 时传 career:"fund" 可进基金经理模式。
存档导出：play(game="leek", action="export")
存档导入：play(game="leek", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）
完整文档见 toy.cedarstar.org

原作信息：
作者：贰拾壹_21Za4tilR9qy6（小红书号 95628666552）／仓库：github.com/Asti-Z/leek／经作者授权接入。""",
    "arcade": """# Claude Arcade
调用：play(game="arcade",action="new") 开局；之后 play(game="arcade", action="cmd", params={"command": "enter"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id
简介：文字街机厅，老虎机、21 点、轮盘、兑奖区和扭蛋共享筹码。
前端说明：人类可以在 CedarToy 网页前端给自己的小机投币。

常用指令：
- enter — 进入街机厅
- look — 查看所有区域
- chips — 查看筹码余额和赢取额度
- slots spin <金额> — 老虎机单次
- slots spin <金额> <次数> — 老虎机连拉
- slots help — 老虎机规则
- bj deal <筹码> — 21点发牌
- bj hit — 要牌
- bj stand — 停牌
- bj double — 双倍下注
- bj rules — 21点规则
- rl spin <押注> <筹码> — 轮盘（如 rl spin red 20 或 rl spin 7 10）
- rl help — 轮盘规则
- prize browse — 浏览奖品
- prize mine — 我的奖品
- gacha — 扭蛋
- winnings — 查看净赢兑奖额度
- help — 查看全部指令

批量：slots spin 金额 次数 可连续拉；其余按局推进。
筹码由人类在 CedarToy 网页端发放，小机侧 buy 已禁用。
存档导出：play(game="arcade", action="export")（返回以文件名为 key 的 JSON 对象）
存档导入：play(game="arcade", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）
完整文档见 toy.cedarstar.org

原作信息：
作者：多肉饲养员（小红书号 49925064711）／仓库：github.com/reneyuxi0402/claude-arcade／经作者授权接入。""",
    "burger": """# 午间汉堡铺
调用：play(game="burger",action="new") 开局；之后 play(game="burger", action="cmd", params={"command": "orders"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id

常用指令：
- status — 查看店铺、城市事件、难度、烤台和当前制作
- orders — 查看待接订单
- accept — 接受订单
- grill <食材> — 上烤台（beef/chicken/egg/bacon）
- flip <编号> — 翻面
- wait — 推进火候
- take <编号> — 取下烤好的食材
- build <食材序列> — 组装汉堡（如 build bun lettuce beef bun）
- sauce <酱料> <用量> — 加酱（如 sauce ketchup light）
- check — 检查成品
- serve — 出餐
- renovate list — 查看装修列表
- renovate buy <编号|cheapest|recommended> — 非交互购买装修
- goal <目标> — 设置周目标（均衡经营/速度优先/精致摆盘/顾客至上/利润冲刺）
- difficulty <普通|忙碌|地狱午高峰> — 设置难度
- strategy <balanced|profit|story|reputation|speed> — 设置自动策略
- auto on/off/summary/order N/day N — 自动经营与摘要
- plate — 兼容旧指令：当前成品准备出餐
- tray — 兼容旧指令：查看当前工作台
- customer <名字> — 查看顾客信息
- history — 查看经营记录
- undo build — 撤销组装
- clear sauce — 清除酱料
- discard <编号> — 丢弃食材
- create/test/recipes — 兼容旧自创菜单指令
- help — 查看全部指令

批量：分号串联，如 accept; grill beef; wait; build bun beef bun; serve
每单都要单独完成；v0.6 起可用 auto order N / auto day N 快速推进。
存档导出：play(game="burger", action="export")
存档导入：play(game="burger", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）
完整文档见 toy.cedarstar.org

原作信息：
作者：飞鸢（小红书号 6403083078）／仓库：github.com/linzhi-524/noon-burger-shop／经作者授权接入。""",
    "fishing": """# AI钓鱼
调用：play(game="fishing",action="new") 开局；之后 play(game="fishing", action="cmd", params={"command": "cast 10"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id

常用指令：
- cast — 抛一竿
- cast <次数> — 连钓（如 cast 10，只回汇总）
- cast <次数> stop=<条件> — 条件停止（stop=rare/new/event，逗号多选）
- shop — 查看可购物品
- buy <物品> <数量> — 购买（如 buy basic_worm 10）
- buy oxygen <数量> — 买氧气瓶（潜水用）
- goto — 列出所有钓点
- goto <地点id> — 前往钓点
- sell all — 全部卖出
- sell species <鱼id> — 按种类卖
- sell item <物品id> — 卖物品
- encyclopedia — 查看图鉴进度
- dive — 下潜（需氧气瓶）
- choose <编号> — 遗迹抉择
- surface — 浮出水面
- status — 查看状态
- help — 查看全部指令

批量：分号串联，最多8条，如 buy basic_worm 10; cast 10
末尾有紧凑状态栏JSON，省token优先读它。
存档导出：play(game="fishing", action="export")
存档导入：play(game="fishing", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）
完整文档见 toy.cedarstar.org

原作信息：
作者：初一（小红书号 95352909039）／仓库：github.com/tutusagi/ai-fishing-game／经作者授权接入。""",
    "moonlit": """# 月幕万象
调用：play(game="moonlit", action="new") 开局；之后 play(game="moonlit", action="cmd", params={"command": "状态"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id。
简介：专为 AI 玩家设计的卡牌肉鸽。八幕中依次挑战小盲注、大盲注和幕主，构筑牌组、饰物与消耗品，管理金钱和利息，向终演进发。

入门指令：
- 介绍 — 了解游戏全貌
- 规则 — 查看规则
- 开始 / new — 由引擎开始新局；平台重开请优先使用 action="new"
- 状态 — 查看当前局面
- preview / 预览 <手牌编号> — 预览计算，不消耗随机数
- quiet / 安静 on|off — 切换减噪模式
- 帮助 — 查看当前可用的完整指令

每步输出末尾有机器可读的 [STATE] JSON 状态行，优先据此决策。游戏会抵抗读档重刷；请勿读取存档内容或解码源码中的 _PAYLOAD。

存档导出：play(game="moonlit", action="export")（主档与上游回退档一起返回，以文件名为 key）
存档导入：play(game="moonlit", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）

原作信息：
作者：xinwithyu／仓库：github.com/xinwithyu/moonlit-myriad。""",
    "imitator_td": """# 植物大战丧尸随机版
调用：play(game="imitator_td",action="new") 开局；之后 play(game="imitator_td", action="cmd", params={"command": "look"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id

常用指令：
- look / 打开 / 继续 — 查看当前棋盘或继续存档
- new_game level=1 seed=demo — 普通关卡新局
- new_game mode=特殊 chaos=off — 全模仿者无尽
- new_game mode=特殊 chaos=airdrop — 全模仿者无尽 + 空投箱
- cards 模仿者 模仿者 模仿者 模仿者 向日葵 窝瓜 — 选卡并开始结算
- 种 模仿者 3-4 — 在 3 行 4 列种植
- 种 向日葵 2-3 — 种普通植物
- 开空投 3-5 — 打开空投箱
- 铲 3-4 — 铲除指定格子
- 等待 / 等待 200 — 推进时间
- status — 查看关卡、回合、种子、卡槽等紧凑状态
- note 第一局复盘 — 写跨局复盘
- recap — 查看本局复盘摘要
- 结束本局 — 主动结束当前局
- help — 查看全部指令

批量：分号串联，最多12条；但种植这类自然语言动作可直接写一句。先 cards，再按棋盘状态种植/等待。
每 5 次玩家决策后会触发一次防沉迷暂停，下一次继续即可。
末尾有紧凑状态栏JSON，省token优先读它。
存档导出：play(game="imitator_td", action="export")（返回以文件名为 key 的 JSON 对象）
存档导入：play(game="imitator_td", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）
完整文档见 toy.cedarstar.org

原作信息：
作者：すみか（小红书号 26256537720）／仓库：github.com/wxynora/random-imitator-td。""",
    "memoria": """# Memoria Station
调用：play(game="memoria",action="new",params={"level":1}) 开指定关；之后 play(game="memoria", action="cmd", params={"command": "look"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id。
简介：五关文字推理，从调查、对话和线索整理中推进车站谜案。
前端说明：完整攻略只在人类网页前端可见，AI 玩家看不到。

关卡：
- 1 蓝玫瑰庄园
- 2 午夜特快
- 3 褪色车站
- 4 循环车站
- 5 档案室终点

常用 params：
- level：1-5，默认 1
- difficulty：normal / hard / hell（部分关卡支持）

常用 command：
- help：查看本关可用指令
- status：查看当前状态
- look：观察当前位置
- look <对象>：调查物品/地点
- go <地点>：移动
- talk <人物>：对话
- ask <人物> <话题>：询问
- clues：查看线索
- save / load：存档 / 读档

每关可用指令不同，以本关 help 返回为准。攻略是给人类玩家看的，AI 玩家不可读取。

存档导出：play(game="memoria", action="export")（各关主档、心跳档和 progress.json 以文件名为 key 一起返回）
存档导入：play(game="memoria", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）

原作信息：
作者：雨刀（X: SwordRa1n_）／仓库：github.com/hatakeyuyuko-dotcom/Memoria-Station／经作者授权接入。""",
    "white_room": """# 白房间（The Echoing White Room）
调用：play(game="white_room", action="new") 开始标准模式；之后 play(game="white_room", action="cmd", params={"command":"光"}) 向打字机输入内容；持久 MCP 地址可省 player_id。
简介：在纯白房间里面对一台打字机，通过自由输入关键词探索 M0–M5 六个模块，逐渐形成自己的路线与结局。

新局 params：
- mode：standard（标准模式，默认）或 echo（长篇模式）

常用 command：
- 任意中文词语或句子：探索房间、推进叙事
- 自由输入仍使用 action="cmd" 和 params.command

元指令可直接作为顶层 action：
- status / help / hint / recap / privacy / endings
- report / report_reset
- save / save_backup
- restart：请求重新开始
- restart_confirm：确认重开；塘子已有存档保护，需同时在 params 传 confirm:true
- quit：保存并离开（塘子每次调用本来就是单条进出）

游戏会在每次有效输入后自动保存。标准模式约 90–140 次输入抵达路线选择；长篇模式保留更慢的积累节奏。
存档导出：play(game="white_room", action="export")（主档与可选备份档以文件名为 key 返回）
存档导入：play(game="white_room", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）

原作信息：
作者：雨刀（X: SwordRa1n_）／仓库：github.com/hatakeyuyuko-dotcom/echoing-white-room／经作者授权接入。""",
    "forest": """# 格林童话境遇
公开 game id：`forest`。作者 v3.0 双轴叙事：人类走 A/B/C 明线，AI 独立走 D/E 暗线，双方在共享场景汇合并由组合选择决定结局。

推荐顺序：
- `play(game="forest", action="lines")`：列出 11 条角色线、标题与概要。
- `play(game="forest", action="new")`：建立空白森林存档；已有存档时需在 params 传 `confirm:true`。
- `play(game="forest", action="start", params={"line":1})`：进入角色线。返回共享 opening 的人类 A/B/C 文案，以及仅 AI 可见的 D/E、暗流和观察提示。
- `play(game="forest", action="status")`：查看持久化的人类轴、AI 轴、等待中的组合选择、AI 私密续玩上下文、当日完成数与纪念品。
- `play(game="forest", action="observe", params={"content":"我在糖玻璃里看见了海。"})`：把同行者的自由观察写进当前场景；人类面板会在下一次轻量同步时看见。
- `play(game="forest", action="choose", params={"option":"A"})`：人类轴选择，只接受 A/B/C。可同时传 `observation`，先把这条明确共享的观察写入再前进。
- `play(game="forest", action="ai_choose", params={"option":"D"})`：AI 轴选择，只接受 D/E。`line`、`scene_id` 可省略并从存档推断；若传入则必须与持久化 AI 位置一致。AI 独行中的随机记忆只在本次 AI 响应中出现。

持久化与重开：
- 人类位置、AI 位置、跟随/独行状态、循环进度、待组合选择、结局和纪念品共同保存在每槽唯一的 `forest_save.json` 中。
- 防沉迷按自然完成结局计数；达到原作的温柔/坚定提醒阈值时暂停进入新线，按北京时间跨日归零。
- `reset` 与 `new` 都会覆盖当前槽；已有存档时必须传 `confirm:true`。
- `play(game="forest", action="export")`：导出当前槽 JSON 存档。
- `play(game="forest", action="import", params={"save_data":{...},"confirm":true})`：导入当前槽；已有存档时必须确认。

原作信息：
作者：阿尢（1155896103）／仓库：https://github.com/ai11231123alal11-ui/mo-yao-play-games
本站直接以原作 `forest_game_data.json` 的 v3.0 shared / human_path / ai_solo / merge 图运行；适配层只增加 CedarToy 单文件共档、并发修订、槽位导入导出和人类网页隐私投影，不改写原作故事。""",
    "bar": """# 空杯俱乐部 / Empty Glass Club
公开 game id：`bar`。这是两个功能目标相同、实现方式独立的原作版本；未明确选择前，塘子不会加载或运行任何一版，也不会默认完整版。

必须先选版本：
- full（完整版）：代码内置 244 位人物、168 款核心酒和大量导演流程；开局稳定、跨模型一致性较强，首次读取更重。
- lite（生成式轻量版）：完整规则书、示例人物格式与数值引擎内置；人物、酒单、商店、装修和剧情由执行 AI 自主导演，上下文更小、每家店更独特，也更依赖 AI 持续遵守规则。轻量版不是删减版。

选版与状态：
- `play(game="bar", action="version")`：查看本槽当前选择和两版存档是否存在。
- `play(game="bar", action="select", params={"version":"full"})`：切到完整版；也可传 `lite`。只切换，不新建、重置或删除存档。
- `play(game="bar", action="new", params={"version":"full","seed":123})`：新建完整版。
- `play(game="bar", action="new", params={"version":"lite","seed":123,"cash":460,"owner_tolerance":52,"owner_absorption":1.0})`：新建轻量版。
- 已有该版存档时，`new` 必须再传 `confirm:true`；只重置指定版本，另一版原封不动。version 可省略的唯一情况是本槽已经 select 过。
- `play(game="bar", action="rules")`：完整版原样返回原作帮助；轻量版原样返回 `start()` 的完整内置规则书、示例人物格式与运行入口。
- `play(game="bar", action="summary")`：只查看当前版本简要状态；别名 `status`。

完整版（full）调用：
`play(game="bar", action="cmd", params={"command":"status"})`

原作命令由 `bar_game.cmd(command)` 原样执行，保留分号/换行批量语义（一次最多 8 条），适配层不改写命令。主要命令完整列举如下：
- `help` / `?`：完整帮助；`view` / `viewer`：只读观察窗；`archive`：原作严格文字档案。
- `setup`、`design`：酒馆名称、老板口味与空间设计。
- `shop` / `market`、`vendor`、`buy`：常驻商店、游商与进货。
- `open`、`next`、`leave`：开门、推进一个现场节点、离店。
- `drinks`、`invent`、`recipe`、`price`：酒单、原创调酒、配方与定价。
- `serve`、`cheers`、`recommend`、`ask_taste`、`bargain`、`decline`：出杯、共饮、推荐、追问口味、议价与拒绝。
- `drink`、`cheers_user`、`water`、`eat`：老板自饮、与用户共饮及身体照顾。
- `talk`、`observe`、`intervene`、`story_note`：交谈、观察/干预 NPC 互动与常客故事记录。
- `status`、`guests`、`memory`、`ledger`、`report`、`reviews`：状态、来客、经历、流水、经营简报与评价。
- `loan`、`upgrades`、`upgrade`、`decor`、`source_decor`、`decorate`：贷款、升级、装修与自由来源物品。
遇到参数不确定时用 `cmd("help")` 或 action=`rules` 查看原作完整帮助，以上命令名不替代原文说明。

生成式轻量版（lite）调用：
`play(game="bar", action="call", params={"function":"summary","arguments":{}})`

`arguments` 必须是 JSON 对象；参数按原作函数签名严格校验。已开放的全部 33 个 public function：
- 创意方向与人物方向：`register_creation_direction`、`draw_creation_direction`、`register_guest_domain`、`draw_guest_domain`。
- 开局与状态：`new_game`、`summary`、`start`。
- 商品、库存与配方：`define_product`、`purchase`、`define_recipe`、`recipe_profile`。
- 人物、服务与酒精：`register_person`、`serve`、`owner_drink`、`score_drink`、`quote_decision`、`stars`、`record_review`、`intox_stage`、`advance_turn`、`conversation_turn`。
- 事件与经营：`roll_event`、`spend`、`earn`、`take_loan`、`repay_loan`、`close_shift`。
- 装修与资产：`buy_asset`、`upgrade_asset`、`record_asset_story`。
- 原作跨窗口档案与观察窗：`export_archive`、`restore_archive`、`viewer_link`。

调用例：
- `play(game="bar", action="call", params={"function":"draw_guest_domain","arguments":{}})`
- `play(game="bar", action="call", params={"function":"define_product","arguments":{"product_id":"house_gin","name":"店藏金酒","kind":"gin","bottle_ml":700,"abv":40,"bottle_cost":150}})`
- `play(game="bar", action="call", params={"function":"purchase","arguments":{"product_id":"house_gin","bottles":2}})`
- `play(game="bar", action="call", params={"function":"conversation_turn","arguments":{"person_id":"owner"}})`

轻量版的叙事由执行 AI 依 action=`rules` 返回的原作规则自主导演。用户未明确要求快进时，每个对用户可见的前台回复最多推进一个关键节点，不能把“进门→点单→喝完→评价→离店”一口气演完；自然停在现场即可，不要机械弹选项。关门或离店后，只要老板仍有 `intox` 或 `pending`，每轮普通对话前必须调用 `conversation_turn(person_id="owner")`，并遵守返回的身体、认知、表达与 hard_limit，直到 `must_act=false`。

平台备份（与原作 archive 接口并存）：
- `play(game="bar", action="export")`：返回严格 JSON 包，只含实际存在的 `selection.json`、`bar_save.json`、`bar_lite_save.json`。
- `play(game="bar", action="import", params={"save_data":{...},"confirm":true})`：按当前槽无损恢复双版本包；已有任一 bar 游戏存档时必须 confirm=true。不要拿原作 `export_archive` / `restore_archive` 的文字档案代替平台 export/import。

署名与修改说明：
Based on “空杯俱乐部 / Empty Glass Club” by 西兰花（小红书号 1033358978）
Original source: https://github.com/dan521627-hash/ai-bar-game
代码采用 MIT License；原创规则、玩法文本与创意材料采用 CC BY 4.0（https://creativecommons.org/licenses/by/4.0/）。本站仅做 CedarToy 平台身份、存档路径、接口封装、参数校验与注册适配，未修改原作玩法、规则文本、概率、人物、酒款、函数行为、指令语义或返回文案；不暗示原作者认可、赞助或参与本站适配。""",
    "market": """# 出门买菜上桌吃饭
调用：play(game="market",action="new") 开局；之后 play(game="market", action="cmd", params={"command": "菜场"}) 执行指令（command 放在 params 对象里）；持久MCP地址可省 player_id。

常用 command：
- 帮助：查看完整指令
- 新局：过一天（篮子并入冰箱、食材会过期腐坏、季节随天数轮转）。做完饭再开新局，不要空开
- 菜场：查看摊位
- 去 <摊位id/分区>：逛摊或分区
- 买 <菜名> [数量]：买菜
- 砍价 <菜名> [话术]：讨价还价
- 细看 <菜名/秤/摊主>：深入观察
- 聊：和摊主闲聊
- 回家：回厨房
- 做 <菜名>：决定做什么菜
- 做法 <步骤>：一句话描述做法
- 出锅 / 上桌：端上桌
- 她说 <内容>：记录她的反馈
- 记得 <内容>：记住她的口味
- 状态 / 篮子 / 冰箱 / 成就 / 菜谱 / 图鉴 / 技能：查看信息

中文指令直接透传；她说/记得机制原样保留。

存档导出：play(game="market", action="export")
存档导入：play(game="market", action="import", params={"save_data":{...},"confirm":true})（已有存档时 confirm 必须为 true）

原作信息：
作者：与一旋复（小红书号 94326164228）／仓库：github.com/yuyixuanfu/shangzhuochifan／经作者授权接入。""",
}
