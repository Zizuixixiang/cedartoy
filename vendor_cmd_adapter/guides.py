GUIDES = {
    "leek": """# Leek（韭菜修炼之道）
A股模拟交易，1000元本金起步。

开局：play(game="leek", action="new", params={"player_id":"p1","seed":42})

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
文字街机厅：老虎机、21点、轮盘、兑奖区、扭蛋。

开局：play(game="arcade", action="new", params={"player_id":"p1"})

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
命令行汉堡店经营：接单、烤制、组装、出餐。

开局：play(game="burger", action="new", params={"player_id":"p1","shop_name":"午间汉堡铺","chef_name":"AI主厨","sign_style":"温馨"})

常用指令：
- orders — 查看待接订单
- accept — 接受订单
- grill <食材> — 上烤台（beef/chicken/egg/bacon）
- wait — 推进火候
- take <编号> — 取下烤好的食材
- build <食材序列> — 组装汉堡（如 build bun lettuce beef bun）
- sauce <酱料> <用量> — 加酱（如 sauce ketchup light）
- check — 检查成品
- serve — 出餐单份
- plate — 装盘
- tray — 整理托盘
- status — 查看当前状态
- customer <名字> — 查看顾客信息
- history — 查看历史订单
- undo build — 撤销组装
- clear sauce — 清除酱料
- discard <编号> — 丢弃食材
- create <菜名> — 创建隐藏菜单（第6天后）
- test <菜名> — 测试隐藏菜品
- recipes — 查看已解锁食谱
- help — 查看全部指令

批量：分号串联，如 accept; grill beef; wait; build bun beef bun; serve
每单都要单独完成，批量订单逐份 build→plate→tray→serve。
完整文档见 toy.cedarstar.org

原作信息：
作者：飞鸢（小红书号 6403083078）／仓库：github.com/linzhi-524/noon-burger-shop／经作者授权接入。""",
    "fishing": """# AI钓鱼
钓鱼模拟：抛竿、卖鱼、升级装备、集图鉴。

开局：play(game="fishing", action="new", params={"player_id":"p1","seed":2024})

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
}
