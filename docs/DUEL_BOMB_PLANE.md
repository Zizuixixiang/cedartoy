# 双弈炸飞机：实现与整合验收

`game_type=bomb_plane`，棋类 `board`，固定 2 人，简介“寻机头”。隔离开发基线为
`c6c86580a5d175bdbb2b3c4f808dbe5aa02d1a07`；2026-10-02 已与卡卡颂串行整合到主工作树。
本轮未 commit/push、部署、重启或访问生产数据；详细证据见 [两款整合验收](DUEL_NEW_GAMES_ACCEPTANCE.md)。

## 规则与接口

10×10，列A–J、行1–10；每方三架。飞机从头向后为1、5、1、3格，总计10格，朝向N/E/S/W
分别为上右下左。飞机可互相重叠，但三个机头不能重合；机头可落在另一架机体上，单架不可越界。
双方**独立秘密布阵**，无需等待对方；本人确认后
锁定，双方确认才由原先手攻击。普通房先手沿用人类/小机先手选项；邀请房先手沿用随机座位。

每次攻击一个此前没猜过的格，空/伤/机头均换手，重叠格优先判定机头，否则命中任一机体为伤；率先击中三个不同机头获胜。没有唯一官方变体，
本版明确：命中机头不展开轮廓；击落机体仍反馈伤，空格仍为空；终局（包括认输）才向本局
参与者揭示全图。筹码沿用双人标准零和机制：胜者加一份底注，败者减一份，零底注为娱乐局。

全部写动作必须携带外层 `revision`。格式错误、越权、陈旧版本、重复攻击、越界、机头重合、
开始后改图都在事务提交前拒绝，状态、事件、revision、钱包不改变。

| move.action | 字段 | 行为 |
|---|---|---|
| `place` | `head`, `direction` | 增添一架，立即保存到本人私密布局 |
| `undo` | 无 | 撤销末架 |
| `clear` | 无 | 清空本人未确认布局 |
| `shuffle` | 无 | 重新随机三架，不确认 |
| `set_layout` | `planes` | 原子替换0–3架布局 |
| `ready` | 无 | 确认并锁定三架 |
| `auto_setup` | 无 | 随机合法部署并确认 |
| `attack` | `cell` | 攻击坐标，例如E5 |

最小根 MCP 例子：

```json
{"game":"duel","action":"move","params":{"room_id":"当前房间","revision":0,"move":{"action":"place","head":"C1","direction":"N"}}}
```

合法完整示例布局为C1/N、H1/N、C6/N。可依次place，也可一次set_layout，然后以最新revision
提交ready。随机一步完成用auto_setup。攻击示例 `move={"action":"attack","cell":"E5"}`。
更完整示例见 `vendor/duel/docs/MCP_GUIDE.md`。

普通目录和邀请目录自动使用现有catalog，固定2人，不增加第二入口。普通网页/MCP new仍遵守
既有绑定人机限制；邀请房仍要求两名真实参与者，不为这款游戏新增单人对NPC大厅或放宽身份。
开启现有邀请房90/180秒接管后，本地NPC可代原座位部署/攻击；系统NPC控制器的完整模拟房也已验证。

## 隐私、持久化与NPC

原始planes按身份存入同一房间SQLite；网页只收到公共状态与本人的 `private_state.planes`。
布局仅存机头与方向，不重复100格答案矩阵。公共shots按攻击者记录 `{cell,result}`，result为
miss/hit/head。双方所有射击最多200条，NPC/网页/MCP合法攻击规格不包含敌方答案。

部署事件只对本人可见，公开标签不含坐标。NPC决策可以收到自身布局与公开反馈；NPC发言
上下文清空私有字段且只取公开事件，即使它先前做过手动布局也不会把自己的飞机位置带给发言模型。
终局投影的 `revealed_planes` 只经现有房间参与者鉴权返回，外人不能获得。

攻击策略只接收公开shots，最多检查168个单机候选，排除机体经过空格、候选机头已经攻击等矛盾的候选，允许机体经过已命中的其他机头，对命中机体支持的
候选机头评分后有界随机打破平局。没有三机组合穷举、模型决策调用或读取对手答案。随机部署对168个合法单机候选打乱后单次扫描，
取三个机头各不相同的候选，允许机体重叠。策略不保证最优；固定可见信息与随机种子，替换兼容隐藏
布局不会改变NPC动作。

共享存档、revision写事务、普通/邀请身份、聊天、筹码结算及NPC决策持久化均复用框架。
新增 `scripts/persistence_check.py --duel-bomb-plane` 仅运行本游戏临时SQLite跨进程恢复测试。
不运行通用文件存档检查，不接触其guest存档或其他游戏数据。

## 网页实现

仅新增registry renderer与局部CSS，复用双弈玩家卡、聊天、规则/结果弹窗及提交函数。
代码CSS绘制格网和飞机结构；没有外部图像、字体或品牌美术。标题14px、正文13px/1.5 system-ui、
辅助11px，按钮复用pixel-btn。旋转/放置主操作44px高，页签及辅助按钮40px高；桌面棋盘与操作区统一480px并居中。

复用既有 `usesEmbeddedActionFeedback` 选项关闭通用“现在轮到你落子”重复提示，网络错误仍显示。
布阵仅显示“已放置 x/3 架（可重叠）”；一个“旋转 ↻”按钮按上→右→下→左循环，
与“放置这架飞机”并排；满三架后改为“完成布阵”。随机/撤销/清空紧凑排成一组，
飞机编号、机头坐标与方向用小字列出；移除重复教程与随机说明。出界或机头重合时才显示错误。
“攻击对方 / 我的布阵”标签保留，未完成布阵时攻击禁用。机体相叠不会遮掉机头箭头；
攻击仍需选格再确认，只保留空/伤/头短图例。每次放置后真实保存，刷新恢复；未放置的预览仍为本地草稿。

## Token粗估

`artifacts/duel-games-integration-20261002/samples/bomb-plane-token-samples.json` 是112次真实临时房间HTTP决策样本，含早/中/晚期、
最后样本之前已有109次射击。样本为一次 `state(full_state=true)` 的紧凑完整状态加本次move回复，
排除首次bootstrap、指南、聊天、模型思考；日常增量state通常更短。

隔离采样环境无tiktoken，使用紧凑JSON的UTF-8字节数÷4至÷3作粗略长度估算，样本区间371–1645；
界面显示向外取整的“约300–2000 token/轮”，tooltip明确非分词器实测、非实际计费。
不能与使用cl100k_base测量的其他游戏样本直接比较精确数值。重现脚本为
`scripts/sample_duel_bomb_plane_tokens.py`，无模型调用。

## 验证记录

本轮只用临时 SQLite、本地 fixture 和现成依赖，重型验证统一使用
`flock /tmp/cedartoy-duel-new-games-heavy.lock`，未触发 NPC 模型。证据根目录：
`artifacts/duel-games-integration-20261002/`。保留初次失败日志，并在整合验收记录标注后续修复结果。

- 17 项炸飞机专项、30 个种子完整 NPC 局在主工作树通过。覆盖旋转/碰撞、部署/锁定、空/伤/头、三头胜、
  原子失败/并发、越权/revision、普通/邀请、筹码仅结算一次、跨进程恢复、接管及 NPC 发言上下文隔离。
- 共享分类、目录、身份、隐私、MCP 和 NPC 相关检查已合入两款；真实目录是 29 项，不是将隔离副本的 28 项覆盖到主目录。
- 普通/邀请 DOM、独立持久化入口通过；大富翁/拉密/飞行棋的小范围回归通过。
- Chromium 430/1280px × 普通/邀请完整流程通过。本轮前主助手的 360px 普通/邀请场景截图已实际查看；
  其原 100 秒命令超时不记作整条通过。本轮修正桌面宽度对齐后，补测主目录 360/1280px 普通房，
  另补主目录360邀请，保留预览及终局少量截图，并断言棋盘与操作区位置、宽度一致。
- 根 MCP 已使用完整 `server` 实际导入，临时账号真实 Token、数据库绑定身份，验证路径 Token/Bearer、
  catalog/按需 guide、普通/邀请动作、过期版本/越权和异步 prepare/finalize；本轮不再用 AST 文案提取代替运行测试。

实体手机、其他浏览器、真实生产账号/钱包、收费 NPC 发言模型、上线链路未验证。UI 技能附属搜索脚本未安装；
已读三个 SKILL.md，复用现有界面并查看实际渲染截图，无额外依赖。

## 重现命令

从主目录运行；浏览器矩阵可按宽度/房型拆分，所有脚本只连接本地 fixture：

```bash
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=vendor/duel /opt/cedartoy/vendor/duel/.venv/bin/python -m unittest -v tests.test_bomb_plane
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 NODE_PATH=/opt/cedartoy/node_modules DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python node scripts/check_duel_bomb_plane_ui.js
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 NODE_PATH=/opt/cedartoy/node_modules DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright DUEL_BROWSER_WIDTHS=430 DUEL_BROWSER_ROOMS=ordinary,invite timeout 180s node scripts/check_duel_bomb_plane_browser.js
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python python3 scripts/persistence_check.py --duel-bomb-plane
```

## 来源、许可与串行整合

玩法核对：[Nirvazure/PlaneBlock README](https://github.com/Nirvazure/PlaneBlock/blob/main/README.md)，
2026-10-02查阅，只核对10×10、三架、1-5-1-3形状及寻机头目标。全部规则代码、NPC、中文文案、
CSS格网和交互均自行实现；未复制参考仓库代码、登录/云服务/UI或资源，没有新增第三方依赖。
因此不伪造第三方NOTICE或额外许可条目，现有THIRD_PARTY_NOTICES/LICENSE保持不变。

独立新增文件：

- `vendor/duel/app/games/bomb_plane.py`
- `vendor/duel/app/static/games/bomb_plane.js`、`bomb_plane.css`
- `vendor/duel/tests/test_bomb_plane.py`、`bomb_plane_ui_fixture.py`
- `scripts/check_duel_bomb_plane_ui.js`、`check_duel_bomb_plane_browser.js`、`sample_duel_bomb_plane_tokens.py`
- 本说明 `docs/DUEL_BOMB_PLANE.md`

公共增量：

- games注册增加一项；framework仅为本游戏要求revision；npc_controller仅为本游戏收紧发言投影。
- app.js仅新增Token提示及说明；没有改全局CSS、玩家卡、聊天栏、首页、邀请协议或数据库schema。
- server.py仅guide增加2人类型、暗信息说明和动作短例；保留既有大富翁/拉密段落。
- persistence_check新增本游戏限定入口；vendor README/MCP_GUIDE补充目录、规则与接口。
- catalog/identity/local-gateway/multiplayer/new-games/hidden-information/NPC-speech测试更新预期；
  主站guide测试增加炸飞机标记。原隔离目录总数28；本轮与卡卡颂合并后按实际集合核验为29。

原隔离补丁仍保留在交付源 `artifacts/bomb_plane/handoff/`。本轮最小增量补丁及清单位于
主目录 `artifacts/duel-games-integration-20261002/`，以本轮前已脏的主工作树为基准，按 root/vendor 分开；
没有包含 vendor 指针更新。主目录已经整合，不能再次套用补丁。后续上线由主助手单独执行。
