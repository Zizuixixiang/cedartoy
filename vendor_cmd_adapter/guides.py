GUIDES = {
    "leek": """# Leek（韭菜修炼之道）

调用：`play(game="leek", action="new", params={"player_id":"p1","seed":42})` 开局；之后用 `play(game="leek", action="cmd", params={"player_id":"p1","command":"market"})`。

AI 精简玩法：
- 先 `status; market; sentiment; cycle` 看账户、行情、情绪和周期。
- 常用交易：`research nebula` 深研，`buy nebula 5` 买入，`sell nebula all` 清仓，`wait 5` 推进交易日。
- 支持批量指令，用分号串联，最多 8 条：`research titan; buy titan 10; wait 5; sell titan all; history`。
- 每次返回末尾有紧凑状态栏 JSON，例如现金、净值、盈亏、day、turn；省 token 时优先读最后一行。
- 进阶：`market tech` 按板块看行情，`sector tech` 看板块，`news` 看新闻，`pnl` 看单股盈亏，`history/compare/journal` 复盘。
- 等级 15 后可 `new` 时传 `career:"fund"`，或指令 `new_game fund 12345` 进入基金经理模式。

原作信息：
作者：贰拾壹_21Za4tilR9qy6（小红书号 501518888）／仓库：github.com/Asti-Z/leek／经作者授权接入。""",
    "arcade": """# Claude Arcade

调用：`play(game="arcade", action="new", params={"player_id":"p1"})` 进场；之后用 `play(game="arcade", action="cmd", params={"player_id":"p1","command":"look"})`。如果你使用自己的持久 MCP 地址，`player_id` 可省略，平台会默认用你的小机账号 id。

AI 精简玩法：
- 开局 `enter`，筹码由人类在 CedarToy 网页端发放（单次最多 500），再用 `look` 看老虎机、21 点、轮盘和兑奖区；小机侧 `buy` 已禁用。
- 常用：`chips` 看筹码和 winnings；`slots spin 10` 或 `slots spin 10 5` 连拉；`bj deal 50` 后 `bj hit/stand/double`；`rl spin red 20` 或 `rl spin 7 10`。
- 每个子游戏有 help：`slots help`、`bj rules`、`rl help`。
- `winnings` 是净赢兑奖额度，和下注用 chips 分离；用 `prize browse`、`prize mine`、`gacha`。
- 省 token 路线：`chips` 看资金摘要，批量老虎机可用 `slots spin 金额 次数`，轮盘/21 点按局推进。

原作信息：
作者：多肉饲养员（小红书号 49925064711）／仓库：github.com/reneyuxi0402/claude-arcade／经作者授权接入。""",
    "burger": """# 午间汉堡铺

调用：`play(game="burger", action="new", params={"player_id":"p1","shop_name":"午间汉堡铺","chef_name":"AI主厨","sign_style":"温馨"})` 开店；之后用 `play(game="burger", action="cmd", params={"player_id":"p1","command":"orders"})`。

AI 精简玩法：
- 每单流程：`orders` 看订单，`accept` 接单，`grill beef/chicken/egg/bacon` 上烤台，`wait` 推进火候，目标熟度到时 `take 1`。
- 组装：`build bun lettuce beef bun`，加酱 `sauce ketchup light`，出餐前 `check`，单份 `serve`。
- 批量订单每份都要单独完成：`build ...; plate; build ...; plate; tray; serve`。
- 支持分号批量执行，适合把"组装、加酱、检查、出餐"合并；烤制仍要按火候读提示。
- 补救：`undo build`、`clear sauce`、`discard 1`；信息：`status`、`customer 伊芙`、`history`。
- 第 6 天后隐藏菜单：先组装样品，再 `create 月光汉堡`、`test 月光汉堡`、`recipes`。

原作信息：
作者：飞鸢（小红书号 6403083078）／仓库：github.com/linzhi-524/noon-burger-shop／经作者授权接入。""",
    "fishing": """# AI钓鱼

调用：`play(game="fishing", action="new", params={"player_id":"p1","seed":2024})` 开局；之后用 `play(game="fishing", action="cmd", params={"player_id":"p1","command":"cast"})`。

AI 精简玩法：
- 买饵：`shop` 看可买鱼饵，`buy basic_worm 10` 购买；`buy oxygen 5` 买氧气瓶（潜水用）。
- 抛竿核心：`cast` 抛一竿，`cast 10` 连钓 10 竿只回汇总，`cast 20 stop=rare` 钓到稀有就停，`stop` 支持 new/rare/event 逗号多选。
- 分号串联批量指令（最多 8 条）：`buy basic_worm 10; cast 10`、`goto reed_river; cast 8 stop=new`，省 token。
- 每次返回末尾有紧凑状态栏 JSON，例如 `📊 {"pts":270,"loc":"芦苇河","sea":"春","turn":6,"enc":"5/55",...}`，省 token 时优先读它。
- 卖鱼：`sell all` 全卖，`sell species <鱼id>` 按种类卖，`sell item <物品id>` 卖物品；`encyclopedia` 看图鉴进度。
- 换地点：`goto` 列出所有钓点（含价格/本季未发现鱼数），`goto <地点id>` 前往（未解锁则花点数解锁）。
- 潜水后期玩法：`buy oxygen` 买氧气瓶，`dive` 下潜，`choose <编号>` 遗迹抉择，`surface` 上浮。
- 导入存档：`play(game="fishing", action="import", params={"player_id":"p1","save_data":{...}})` 导入 JSON 存档。

原作信息：
作者：初一（小红书号 95352909039）／仓库：github.com/tutusagi/ai-fishing-game／经作者授权接入。""",
}
