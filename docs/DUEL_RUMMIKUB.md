# 双弈拉密

`game_type=rummikub`，大厅名“拉密”，桌游类（`tabletop`），2 / 3 / 4 人，推荐 4 人。平台的 6 人能力未改。2026-10-02 已串行整合到 `/opt/cedartoy` 及独立 `vendor/duel` 工作树；未 commit、push、部署、重启或接触生产数据。

## 规则来源与实现边界

规则核对来源：

- [官方规则入口](https://rummikub.com/rules/)
- [Classic 英文 PDF，2019-12 版链接](https://rummikub.com/wp-content/uploads/2019/12/2600-English.pdf)，第 1 页组合、开局和摸牌；第 2 页拆牌；第 3–4 页万能牌四类释放及计分。

规则代码、中文说明、CSS 牌面和 renderer 均为本次自行实现，没有引入第三方规则代码、依赖、品牌美术或规则书原文。只阅读规则不构成打包第三方代码，因此不新增虚假的 `third_party` 依赖/NOTICE，不更改主 LICENSE。

每色 1–13 两套，加两个万能牌，共 106 张，每人 14 张。同值同色牌的 ID 分别如 `red-7-1`、`red-7-2`。随机洗牌与摸牌由服务端执行。

组合为同数异色 3–4 张，或同色连续至少 3 张，13 不接 1。首次仅以原手牌组成至少 30 分的组合，不能给旧桌面添牌、拆牌、移动万能牌，开局后下一回合才能操作旧桌面。万能牌计其代表值。

开局后最终桌面须全部合法，所有旧实体牌恰好保留一次，且至少打出一张原手牌。允许拆分、合并、取第四张、跨组重排。万能牌可通过手牌替换、桌面替换、拆顺子、分配原同伴牌来释放；释放后所在新组合须有至少一张本回合原手牌，不可藏回手中。

数字实现的明确约定：

- 单房单局，先手沿用房间先手选项，不新增实体桌的抽牌比先手或多局赛程。
- 无 60 秒硬限时或非法草稿罚摸。草稿留在浏览器，最终桌面整回合原子校验。可选接管复用邀请房关闭 / 90 / 180 秒约定。
- 空堆不自动结束；仍可出牌。`pass` 是玩家明确作出的“无牌可出”声明，一整圈在局玩家连续声明才堵局结算。出牌或离场清除已有声明。**这不是服务器穷举证明无解**；NPC 也只采用有限策略，可能未发现复杂重组机会。界面与规则均说明这一边界。
- 堵局最低剩余牌值并列：`winning_player_ids` 保留并列者，平台 `draw=true`（无唯一赢家），其余玩家扣与最低值的差，并列者均分正分，允许小数。官方没有明确这类单局并列分配，本版显式采用该约定。
- 离场/认输封存该席手牌，不回牌堆、不公开牌面。至少两人在局继续，一人剩余则胜；只剩 NPC 按房间约定结束。有任何弃权的牌局不计牌面局分，避免把封存牌混入普通计分。
- 本版 `supports_stakes=false`，仅 `stake=0`。牌面 `tile_scores` 与平台钱包完全分离。

万能牌在原类型、原数字、兼容颜色范围内且仍有至少一张原非万能同伴时，可随原组合扩展/分割而留在原角色；换类型、换代表数字/颜色或完全离开原同伴视作释放。三张同数组的万能牌保留两种缺色可能，不错误固定成一种颜色；两张万能牌联合检查同组异色约束，不能通过拆组把原本不同的代表颜色偷偷变成同一色。

一张数字牌加两个万能牌可能有两种合法解释。动作的可选 `kinds` 明确区分 `group` / `run`；省略时选较高分的解释。已落桌的解释持久化，不能通过无手牌的角色变化规避万能牌释放检查。

## 接入、投影与动作

- 插件：`vendor/duel/app/games/rummikub.py`。
- UI：`vendor/duel/app/static/games/rummikub.js` / `.css`，按既有 registry 自动加载。
- 房间、邀请、绑定小机、NPC、轮转、presence、托管、聊天、事件、SQLite 持久化继续使用公共框架。
- 普通和邀请拉密房的每个动作都必须带外层 `revision`。身份与行动权校验、`BEGIN IMMEDIATE` 写事务保证过期/重复/越权请求失败；插件校验无副作用，应用时也复制状态。
- 对外入口的身份仍由主站认证代理覆盖；双弈内部 HTTP/MCP 服务沿用接收可信代理提供的 `player_id` 的契约，不能把内部端口暴露为独立认证入口。本任务未改动身份架构。
- 公共投影仅桌面实体牌、张数、轮次、开局状态、声明及终局数值。私有投影仅 viewer 自己的牌与建议；终局也不揭露别人的手牌、弃权封存牌或牌堆顺序。摸牌事件不携带摸到的 ID。
- `private_state.legal_action_spec` 是参数化提交规格，`suggested_move` 为有限搜索建议；**不限于此建议**，玩家/小机可自行提交任意合法整桌重组。

最小 MCP 调用（空桌且三张指定实体牌确实在己方手中，33 分开局）：

```json
{
  "game": "duel",
  "action": "move",
  "params": {
    "room_id": "当前房间",
    "revision": 0,
    "move": {
      "action": "meld",
      "melds": [["red-10-1", "red-11-1", "red-12-1"]],
      "kinds": ["run"]
    }
  }
}
```

`melds` 必须包含最终整桌，不是只包含新增组合；顺子按升序，万能牌在代表数字的位置。`kinds` 若提供，须与所有组合逐一对应。旧组合按 `board_state.meld_info[].kind` 保留解释。`draw` 摸一张并立即换人；空堆仅可出牌或 `pass`，不能假摸后结束全局。所有动作均通过同一 `play_move`。

NPC 候选只读取自己的手牌、公共桌面和开局状态，不读取对手手牌/池顺序。最多尝试 128 个首选手牌组合并贪心打包，再尝试接入桌面；无跨桌指数重排。返回动作再次由权威 validator 校验。策略保证合法行动，不保证找到全部可走重组。

## UI 约定与验收

复用双弈玩家栏、聊天、粉紫色变量、像素按钮（常规 42px、紧凑 36px）、system-ui 牌桌正文。新插件正文 13px、标题 14px、辅助 11px，牌为 44×60px；公共桌面最多 350px / 手机 286px 后内部滚动。红◆、蓝●、黑■、橙▲及万能★提供颜色之外的辨识。牌面由 CSS/文字绘制，无外部图片。

操作：点选牌 → 新建组/选目标 → 移入目标；组内单选可前后调整；两万能牌歧义组合可选择类型。支持手牌两种排序、整理建议、撤销、重置、仅收回原手牌、无效提交保留草稿。`context.helpers.submitMove` 提交并附 revision；更换房间/revision 后宿主清除旧草稿。无中间拖放网络写入。

Chromium 已在本地临时 fixture / SQLite 上完成 2 / 4 人 × 360 / 430 / 1280px 六组真实页面验收。每组覆盖14张开局手牌、排序、点击新建组、撤销、33分开局提交、刷新恢复、另一席私牌视角、拆分六张顺子并添加手牌提交、20组桌面、28张手牌和摸牌结束回合。中间整理没有发出 move，提交携带真实 revision；摸后的29张手牌均不能当回合再出。页面无横向溢出，长桌面内部滚动。已人工检查手机、宽手机和桌面截图。

实测牌面44×60px；主操作按钮手机44px高 / 桌面40px高，辅助文字11px；现有刷新按钮手机40px / 桌面36px，聊天输入字号14px。保持既有粉紫边框、阴影、玩家栏与聊天布局。截图和测量在 `/tmp/duel-rummikub-integration/browser-final/`。这是 Chromium 触屏模拟，未在实体手机、Safari/Firefox 或生产认证链路做浏览器测试。

## 测试命令

从主工作区根运行；数据库、缓存与截图均指向隔离目录，不安装依赖：

```bash
mkdir -p /tmp/duel-rummikub-check
TMPDIR=/tmp/duel-rummikub-check RUMMIKUB_TEST_TMP=/tmp/duel-rummikub-check \
DUEL_DB_PATH=/tmp/duel-rummikub-check/default.db \
PYTHONPYCACHEPREFIX=/tmp/duel-rummikub-check/pycache PYTHONPATH="$PWD/vendor/duel" \
vendor/duel/.venv/bin/python -m unittest vendor.duel.tests.test_rummikub

NODE_PATH="$PWD/node_modules" RUMMIKUB_TEST_TMP=/tmp/duel-rummikub-check \
DUEL_TEST_PYTHON="$PWD/vendor/duel/.venv/bin/python" \
node scripts/check_duel_rummikub_ui.js

PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright \
RUMMIKUB_TEST_TMP=/tmp/duel-rummikub-check \
DUEL_TEST_PYTHON="$PWD/vendor/duel/.venv/bin/python" \
node scripts/check_duel_rummikub_browser.js
```

浏览器拦截所有请求，仅提供本地真实静态页面，动作经过真实 framework/规则和临时数据库；不启动生产服务，不访问生产认证/对局。每个场景使用单独 SQLite，避免多轮测试触发每人进行中房间数限制；同场景的提交/刷新保留该库。四人 fixture 走邀请创建/加入/开始链路，两人走普通房。`PLAYWRIGHT_MODULE` 可改为其他既有安装路径。脚本关闭临时浏览器并删除临时库，截图保留。

专项覆盖：2/3/4 人发牌及全部实体守恒；29/30 分、两万能牌歧义；组合/顺子；官方四类万能牌释放结构、桌面牌替换、释放后新组合含手牌；拆分/合并/多组重排；摸牌不能即出、空堆继续、全圈声明、并列分数；正常/弃权/纯 NPC 余席终局；跨进程恢复；所有 viewer 私牌投影；缺失/过期/重复/并发 revision、越权及无效动作不写事件；邀请、内部 MCP、超时接管；36 个固定种子的 2/3/4 人完整策略对局，以及真实 NPC controller 的混合席位完整房间。

DOM 验收运行完整 `app.js`、registry、renderer，提交经过真实规则/SQLite：选择/排序/移动/顺序调整/建议/撤销/重置/错误恢复/修订清稿/整回合提交/空堆继续/终局展示/组合类型选择。浏览器操作与截图验收见上。

## 2026-10-02 整合验证

日志根目录：`/tmp/duel-rummikub-integration/`。共 **314 个不同双弈测试**经分组执行及针对性复测通过，另有 **59 个主站回归**通过、**3 个大富翁持久化入口测试**通过（这3项也包含在双弈集成模块中，不重复计入314）。

- 双弈首批241项：拉密36、大富翁规则23/集成21、隐私4、邀请34、接管14、多人数框架27、多人数UI6、本地MCP8、身份7、前端61。初次2项目录数量仍为26，修正后身份模块及目录断言通过；`backend.log`、`catalog-final.log`、`identity-gateway-final.log`。
- 补充本地网关6项，以及规则目录22、飞行棋规则20/前端15、消息10。规则目录另2处数量断言更新后22项通过；`identity-gateway-final.log`、`shared-regression.log`、`new-games-final.log`。
- 拉密36项含36个固定种子完整策略对局、真实NPC controller混合席位完整对局、跨进程恢复、普通/邀请2/3/4席、超时接管、私牌/事件隔离、身份与revision拒绝、原子回滚、实体守恒、30分门槛、官方万能牌示例、终局/堵局/并列/离场。
- 主站房间/邀请/异步网关/挂等59项：`root-final.log`。guide整合后压缩回4000字符以内，原能力断言保留并新增拉密断言。首次空临时账号库缺防沉迷表；用 `server._init_anti_addiction_tables` 初始化该临时库后通过，没有修改生产逻辑或读写生产账号库。
- 大富翁 `scripts/persistence_check.py --duel-monopoly`：3项通过，`persistence.log`；拉密跨进程恢复包含在其36项中。未重跑无关游戏的通用存档检查。
- DOM：`dom-final.log`。浏览器六组：`browser-final.log`，54张截图及 `browser-final/measurements.json`。中途修正测试场景的挂等弹层关闭、四人长桌面保留可摸牌堆、不同场景隔离数据库；没有为测试修改游戏规则或安全配置。
- 根仓及独立双弈 `git diff --check`、新增文件空白检查通过。

人工查看的代表截图：

- `/tmp/duel-rummikub-integration/browser-final/split-draft-4-360.png`
- `/tmp/duel-rummikub-integration/browser-final/long-2-360.png`
- `/tmp/duel-rummikub-integration/browser-final/long-2-430.png`
- `/tmp/duel-rummikub-integration/browser-final/drawn-4-430.png`
- `/tmp/duel-rummikub-integration/browser-final/split-submitted-2-1280.png`
- `/tmp/duel-rummikub-integration/browser-final/long-4-1280.png`

尚未验证：实体移动设备/其他浏览器、真实生产登录及上线链路。本轮没有重复完整大富翁浏览器矩阵；其原实现/浏览器脚本/许可保持原样，本轮补跑规则、接口、邀请、交易接管、私密投影和持久化相关回归。有限NPC策略与空堆声明制保持前述边界。

## 实际整合文件与公共增量

独立新增8文件：本说明；`scripts/check_duel_rummikub_ui.js`、`scripts/check_duel_rummikub_browser.js`；`vendor/duel/app/games/rummikub.py`；`vendor/duel/app/static/games/rummikub.js`、`.css`；`vendor/duel/tests/rummikub_ui_fixture.py`、`test_rummikub.py`。

公共文件逐处合入，共11文件：

1. `vendor/duel/app/games/__init__.py`：新增拉密 import/注册，保留大富翁注册及顺序。
2. `vendor/duel/app/framework.py`：仅增加拉密缺失revision拒绝，保留交易接管等既有改动。
3. `server.py`：guide人数/暗信息/娱乐局清单与拉密动作说明，保留大富翁段落。
4. `vendor/duel/tests/test_hidden_information_audit.py`：拉密、大富翁规则限定公开分类；核验大富翁实际投影后，断言未来牌序/内部续步/出狱卡来源不公开，终局仍隐藏，己方仅得到出狱卡数量。大富翁专门终局投影继续使用原实现。
5. `vendor/duel/README.md`：27款目录、拉密桌游分类/娱乐局及规则来源；大富翁第三方致谢行原样保留。
6. `vendor/duel/docs/MCP_GUIDE.md`：新增拉密结构化动作示例。
7. `tests_toy/test_duel_rooms.py`：guide的拉密人数/私牌/娱乐局断言，保留大富翁断言。
8. `vendor/duel/tests/test_identity.py`、`test_local_gateway.py`、`test_multiplayer_phase1.py`、`test_new_games.py`：目录数量27、拉密名称/分类/人数断言，原有行为测试保留。

未改 `AGENTS.md`、`app.js`、全局 `styles.css`、首页/管理页、账号/海龟汤、异步网关、通用持久化脚本等既有任务内容。所有 `monopoly` 源码/样式/测试/浏览器脚本、MIT LICENSE、固定来源NOTICE和第三方数据保持本轮开始时的内容。开始时记录的113个既有修改文件中，103个字节级不变；其余10个是本轮共同文件，其中9个在内存去掉本轮增量后精确恢复原哈希。`server.py` 在16:20后另有账号找回任务并行更新，本轮只改guide、保留其新变化，并重新跑过当前主站回归。没有引入拉密第三方代码或资源，没有提交上层vendor指针。

部署时涉及 `cedartoy-duel`（规则/注册/接口与静态资源）、`cedartoy`（主站guide）。本轮未改异步网关，不因拉密单独要求重启 `cedartoy-duel-gateway`；若一并部署工作树里此前网关改动，应由主助手按那部分改动另行判断。本轮没有执行任何部署或重启。
