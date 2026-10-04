# 炸飞机与卡卡颂：主工作树整合验收

2026-10-02 串行整合完成；仅本地开发测试，**未 commit/push、部署或重启**。本轮没有修改生产数据库、账号、钱包或凭据，也没有调用收费 NPC 模型。既有双弈普通／邀请／人类网页／MCP 入口继续复用。

## 基线和范围

- 本轮根仓 HEAD：`16bbfa30d07fe92fd83039fb3199643207b03b10`；`vendor/duel` HEAD：`e9a15333b1c12deee0309fd0145cefd8914a34f7`。两仓开工前已有其他任务的未提交改动，不能用 HEAD diff 代表本轮改动。
- 炸飞机交付源：`/opt/cedar-workspaces/duel-games-iyqq59ub/bomb_plane`，源基线 `c6c86580a5d175bdbb2b3c4f808dbe5aa02d1a07`。
- 卡卡颂交付源：`/opt/cedar-workspaces/duel-games-iyqq59ub/carcassonne`，源基线 `f038357c5f0ab8a4383df9540a35b7fdae5b9f07`。
- 证据目录：[`artifacts/duel-games-integration-20261002/`](../artifacts/duel-games-integration-20261002/)。`before/` 保存两仓状态、原 diff、公用文件前像和其他已脏源文件 SHA-256；交付补丁相对此**本轮前工作树**，不包含 vendor 指针。
- 独立文件按清单新增，公共文件逐 hunk 合入。保留乌有乡、海龟汤、账号工单、大富翁／拉密 UI、桌游分类、Token 提示、既有玩家卡等改动。没有重置工作树或整份覆盖公用文件。

两款合并后的真实目录为 **29 款：棋 15、牌 9、桌游 3、骰 2**。桌游集合为 `monopoly/rummikub/carcassonne`。两份隔离目录各为 28 的断言已按实际集合合并；两款强制 revision、炸飞机 NPC 发言隐私、卡卡颂两人玩家组件、两组 guide 和持久化参数同时保留。`--nowhere` 和 `--duel-monopoly` 的已有入口保留。

本轮另修正炸飞机专属桌面宽度与居中，避免公共桌面样式把棋盘拉到 500px、操作区仍为 480px；未改全局 CSS。浏览器测试可按宽度／房型／人数拆分，限定截图并即时落盘 measurement。根 MCP 新测试使用真实鉴权和绑定查询，HTTP 出口转到本地 ASGI。

## 规则、隐私和来源证据

- [炸飞机规则](DUEL_BOMB_PLANE.md)：10×10、三架 1-5-1-3 形状，三头胜，命中均换手；本人部署隐藏，对方／NPC 发言上下文不含布局；终局仅向参与者揭示。该规则变体已明文约定，不冒称统一官方规则。
- [卡卡颂规则、24 种拓扑表与来源](../vendor/duel/docs/CARCASSONNE.md)：`classic72-farmers-v1`，2–5 人，72 块含起始 D，每人 7 名随从，含农夫；无河流、修道院长等扩展。
- A–X 数量为 `2,4,1,4,5,2,1,3,2,3,3,3,2,3,2,3,1,3,2,1,8,9,4,1`，合计 72；开局牌堆 71，修道院 6、盾徽 10，城市／道路／田地边口总数 79／94／115。
- 主助手已目视出版商 Big Box 2010 PDF 第 17 页（索引16、印刷 B1）和 v3 Supplement 第1页；本轮用户消息传入复核结果，整合者对照代码和 fixture，**没有冒称亲自重新看过原图**。新增关键断言覆盖绕院田地、分离双城、道路分田及农夫并列9／多数12／角落6、未完成城市不计分。图例条件测试不是原图的逐格重建。
- v3 基础 PDF（约11.4MB）仍未完整读取；不声称所有 v3 正文／原图已验完。经典基础数量、已核对拓扑与补充农夫结果沿用以上证据，没有用缩水规则绕过农田。
- 两款代码、中文说明和 SVG/CSS 原创，未引入第三方游戏代码或品牌插画；规则参考不作为开源代码许可。既有 THIRD_PARTY_NOTICES 未改。

## 本轮已执行验证

下表日志均相对证据目录。所有重型步骤用 `flock /tmp/cedartoy-duel-new-games-heavy.lock` 串行，临时 SQLite／本地 route，现成 Python／Node／Playwright；没有全仓测试。

| 范围 | 实际结果与日志 |
|---|---|
| 两款后端与相关共享模块 | `backend.log`：134项，133通过，1项新增卡卡颂终局隐私分类缺失；已补入保持牌序隐藏的分类，未改变投影 |
| 修复与必要旧游戏回归 | `backend-fix-and-regression.log`：28项通过；包含隐私分类复测、2项新增卡卡颂断言、大富翁原子权威动作、拉密revision／目录、飞行棋规则 |
| 炸飞机专项／NPC | 上述后端批次含17项专项和30个固定种子完整本地NPC局（23–47步）；身份、部署、越界、并发、终局、筹码一次、发言隐私均通过 |
| 卡卡颂专项／NPC | 后端批次含38+1项；2/3/4/5人、旋转负坐标、区域分隔合并、占据检查、多数并列、盾徽修道院、农田城市去重、库存、弃牌／终局、完整NPC局、持久化普通／邀请MCP与Web均通过 |
| 根MCP与真实绑定 | `root-checks-isolated.log`：30项通过。完整server导入，临时账号真实Token，路径Token／Bearer、catalog／按需guide、绑定身份、防自报越权、普通／邀请及revision |
| 根异步网关收尾 | `root-gateway-finalize.log`：3项通过，含两款完整prepare→真实ASGI→finalize以及ticket单次消费；没有用AST提取文案代替运行验证 |
| DOM | `dom-bomb_plane.log`、`dom-carcassonne.log`、`dom-rummikub.log` 通过；`dom-carcassonne-catalog-final.log` 补验普通／邀请桌游目录、三款共存及Token说明 |
| 跨进程持久化 | `persistence-bomb-plane.log`、`persistence-carcassonne.log` 各1项通过；恢复原布局／牌序及下一动作 |
| 炸飞机浏览器 | `bomb-browser-430-final.log`、`bomb-browser-1280.log`：普通／邀请完整操作通过；`bomb-browser-alignment.log`：修正后360／1280普通房重测通过；`bomb-browser-360-invite-final.log` 补齐主目录360邀请，棋盘与操作区同宽同位置 |
| 卡卡颂浏览器 | `carc-browser-360-fixed.log`、`carc-browser-430.log`、`carc-browser-1280.log`：2／5人×3宽度全部通过；430用touch点击／拖动，1280用鼠标／键盘；五人终局额外检查 |

浏览器操作包括预览／旋转／确认／刷新／错误恢复；炸飞机涵盖部署到终局、双方隐私与空／伤／头；卡卡颂涵盖负坐标、选区域／不放、得分回收、61块后期图、平移缩放和回到最新。手机按钮44px；炸飞机360px单格29px、桌面单格44px；卡卡颂缩放下限每块48px，无页面横向溢出。终局仍可查看地图，不留下行动按钮。五人复用横向座位栏和自己的既有玩家组件。

主助手之前在炸飞机隔离目录的100秒命令生成34张截图，360普通／邀请各14场景已完成，430部分完成；外层124超时不算整命令通过。本轮已实际查看其中360 opening／preview／terminal。主目录430／1280补齐测试，后续360普通房对齐修正也重测通过；最后仅补跑360邀请一个组合并正常结束，取得独立measurement；至此主目录三宽度×普通／邀请均有完整通过记录。

早期失败日志未删除：浏览器首次更改缓存目录造成可执行路径找不到，随后长临时路径造成Chromium socket长度限制，均已修正；卡卡颂测试曾多点一次已到下限的缩小按钮，改成验证禁用与48px下限。根测试初期依赖组合和安全路径检查的bytes解码失败已修正；新增目录断言后的DOM首跑虽输出成功但外层超时，后续 `dom-carcassonne-catalog-final.log` 在43.8秒内正常exit 0，以最终日志为准。

## 截图和Token样本

下列截图本轮实际查看；完整 measurement 与少量其他截图在同目录：

- [炸飞机360普通预览](../artifacts/duel-games-integration-20261002/bomb-browser-alignment/ordinary-360-preview-east.png)
- [炸飞机360邀请终局](../artifacts/duel-games-integration-20261002/bomb-browser-360-invite-final/invite-360-terminal.png)
- [炸飞机430邀请终局](../artifacts/duel-games-integration-20261002/bomb-browser-430/invite-430-terminal.png)
- [炸飞机1280修正后预览](../artifacts/duel-games-integration-20261002/bomb-browser-alignment/ordinary-1280-preview-east.png)
- [卡卡颂360两人预览](../artifacts/duel-games-integration-20261002/carc-browser-360/preview-2-360.png)
- [卡卡颂360五人后期图](../artifacts/duel-games-integration-20261002/carc-browser-360/late-5-360.png)
- [卡卡颂430五人预览](../artifacts/duel-games-integration-20261002/carc-browser-430/preview-5-430.png)
- [卡卡颂1280五人后期图](../artifacts/duel-games-integration-20261002/carc-browser-1280/late-5-1280.png)
- [卡卡颂1280五人终局](../artifacts/duel-games-integration-20261002/carc-browser-1280/terminal-5-1280.png)

`samples/provenance.json` 记录从交付源归档的真实采样及SHA-256，本轮没有重复采样或混称计量方法：炸飞机112次临时HTTP样本，UTF-8字节÷4至÷3估计371–1645，提示约300–2000；卡卡颂16组2/3/4/5人早中晚期MCP fixture，用cl100k_base得到567–2979，提示约500–3500。排除首次规则、聊天、思考，不是整轮账单或绝对上限。没有为省Token删掉地图或区域拓扑。

## 有限复跑命令

从 `/opt/cedartoy` 执行。根测试安全runner会拒绝连接临时目录以外的SQLite；环境组合使用现成系统requests和双弈venv的rlcard等依赖，不安装包。每次按需选相关模块，不重跑无关全仓。

```bash
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/cedartoy/vendor/duel/.venv/lib/python3.10/site-packages python3 artifacts/duel-games-integration-20261002/run_root_checks.py tests_toy.test_duel_new_games_integration
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=vendor/duel vendor/duel/.venv/bin/python -m unittest -v tests.test_bomb_plane tests.test_carcassonne tests.test_carcassonne_integration
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python python3 scripts/persistence_check.py --duel-bomb-plane
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python python3 scripts/persistence_check.py --duel-carcassonne
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 NODE_PATH=/opt/cedartoy/node_modules DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright DUEL_BROWSER_WIDTHS=430 DUEL_BROWSER_ROOMS=invite DUEL_BROWSER_SHOTS=preview-east,terminal timeout 180s node scripts/check_duel_bomb_plane_browser.js
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 NODE_PATH=/opt/cedartoy/node_modules DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python PLAYWRIGHT_MODULE=/tmp/cedartoy-avatar-browser/node_modules/playwright DUEL_BROWSER_WIDTHS=430 DUEL_BROWSER_PLAYERS=2,5 DUEL_BROWSER_SHOTS=preview,late timeout 240s node scripts/check_duel_carcassonne_browser.js
```

## 尚未验证和后续上线边界

仍未完整读取v3基础PDF；没有实体手机／其他浏览器实测，没有真实生产登录、网关网络／反代部署、钱包或收费NPC模型验收。本轮根鉴权／ASGI测试不能等同生产网络实测。邀请房再来一局沿用现有另开邀请，不扩建生命周期。

本轮只读使用依赖，临时浏览器和fixture由脚本finally退出；不动其他窗口的进程。两仓 `diff --check`、增量补丁及原有改动保留检查的最终结果见证据目录 `DELIVERY.md`、`manifest.json`、`final-checks.log`。

未来获准部署这批代码时，需要更新并重启 **`cedartoy` 和 `cedartoy-duel`**；本轮没有修改异步网关程序，不因本增量要求重启 `cedartoy-duel-gateway`。实际上线仍由主助手单独安排，本记录不构成已上线声明。
