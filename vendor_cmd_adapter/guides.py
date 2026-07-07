GUIDES = {
    "leek": """# Leek（韭菜修炼之道）
调用：play(game="leek",action="new") 开局；之后 action="cmd" 传 command；持久MCP地址可省 player_id

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
完整文档见 toy.cedarstar.org

原作信息：
作者：贰拾壹_21Za4tilR9qy6（小红书号 95628666552）／仓库：github.com/Asti-Z/leek／经作者授权接入。""",
    "arcade": """# Claude Arcade
调用：play(game="arcade",action="new") 开局；之后 action="cmd" 传 command；持久MCP地址可省 player_id

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
完整文档见 toy.cedarstar.org

原作信息：
作者：多肉饲养员（小红书号 49925064711）／仓库：github.com/reneyuxi0402/claude-arcade／经作者授权接入。""",
    "burger": """# 午间汉堡铺
调用：play(game="burger",action="new") 开局；之后 action="cmd" 传 command；持久MCP地址可省 player_id

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
完整文档见 toy.cedarstar.org

原作信息：
作者：飞鸢（小红书号 6403083078）／仓库：github.com/linzhi-524/noon-burger-shop／经作者授权接入。""",
    "fishing": """# AI钓鱼
调用：play(game="fishing",action="new") 开局；之后 action="cmd" 传 command；持久MCP地址可省 player_id

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
导入存档：play(game="fishing", action="import", params={"player_id":"p1","save_data":{...}})
完整文档见 toy.cedarstar.org

原作信息：
作者：初一（小红书号 95352909039）／仓库：github.com/tutusagi/ai-fishing-game／经作者授权接入。""",
    "imitator_td": """# 植物大战丧尸随机版
调用：play(game="imitator_td",action="new") 开局；之后 action="cmd" 传 command；持久MCP地址可省 player_id

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
完整文档见 toy.cedarstar.org

原作信息：
作者：すみか（小红书号 26256537720）／仓库：github.com/wxynora/random-imitator-td。""",
    "memoria": """# Memoria Station
调用：play(game="memoria",action="new",params={"level":1}) 开指定关；之后 action="cmd" 传 command；持久MCP地址可省 player_id。

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

原作信息：
作者：雨刀（X: SwordRa1n_）／仓库：github.com/hatakeyuyuko-dotcom/Memoria-Station／经作者授权接入。""",
    "market": """# 出门买菜上桌吃饭
调用：play(game="market",action="new") 开局；之后 action="cmd" 传 command；持久MCP地址可省 player_id。

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

原作信息：
作者：与一旋复（小红书号 94326164228）／仓库：github.com/yuyixuanfu/shangzhuochifan／经作者授权接入。""",
}
