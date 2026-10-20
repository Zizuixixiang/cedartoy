# 拉密终局复盘与一次性演示房验收

本次只提交工作树修改，未部署、commit、push，未写生产库。正式演示房尚未创建。
固定预留房号 `RKDEMO01`，正式创建成功后入口为 `/duel/?room=RKDEMO01`；
dry-run 的临时房间不可通过生产链接访问。

## 修改范围

- Duel `app/games/rummikub.py`：只在框架选择 `terminal_public_state` 时增加
  `terminal_hands`，四席实体牌保持 id/color/number（joker 为 color=joker）。
  普通 public/private 投影、牌库、动作和计分逻辑不变。
- Duel `app/static/games/rummikub.js/.css`：仅 `isTerminal && terminal_hands`
  展示四席只读复盘，沿用牌面和文字样式、横向滚动，展示剩余点数，
  既有局分保留一处。此时用四席复盘替代重复的个人手牌/排序控件。
- Duel `guandan.js`、`liars_dice.js`：仅已有终局 details 增加 `open=true`。
  开火车、大富翁、通用 GAME OVER 弹窗未修改。
- Duel `tests/test_rummikub.py`、`test_guandan_frontend.py`、
  `test_liars_dice_frontend.py`、`test_hidden_information_audit.py`：
  隐私/终局/折叠回归，审计清单将拉密改为“仅终局公开剩牌、牌库仍隐藏”。
- 主仓库 `cedar_backend/duel_history.py`：在既有麻将演示排除条件旁仅增加
  拉密演示标记排除（两行），保留其他未提交修改；否则演示会计入真实历史总数。
- 新增本说明、`create_rummikub_review_demo.py`、
  `check_rummikub_terminal_review.cjs`、`tests_toy/test_rummikub_review_demo.py`。

## 验证入口

```bash
cd /opt/cedartoy/vendor/duel
.venv/bin/python -m unittest tests.test_rummikub tests.test_guandan_frontend tests.test_liars_dice_frontend tests.test_train_cards_frontend
.venv/bin/python -m unittest tests.test_hidden_information_audit tests.test_guandan
cd /opt/cedartoy
vendor/duel/.venv/bin/python -m unittest tests_toy.test_rummikub_review_demo
python3 -m unittest tests_toy.test_duel_history
DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python node scripts/check_duel_rummikub_ui.js
node scripts/check_rummikub_terminal_review.cjs
vendor/duel/.venv/bin/python scripts/create_rummikub_review_demo.py
```

所有演示/HTTP/引擎/持久化测试使用临时 SQLite；已有拉密测试包含新进程重读。
未运行会写其他生产游客存档的全量 persistence_check，避免超出本任务授权。
环境没有 pytest，使用项目现有 unittest 入口。
真实演示投影压缩 JSON 从 7,693 字符增至 9,055 字符（仅终局增加 1,362 字符，
不是 token 数）；进行中投影与常驻工具 schema 没有扩张。

浏览器验收脚本使用真实页面与真实投影，拦截全部网络，不访问生产。
覆盖 360、430、1280px、旧终局重进、长名/零牌/joker、原生 details 点击和键盘折叠。
主助手已运行真正 Chromium，360／430／1280px 页面、终局重进、滚动、原生折叠全部通过。
复跑命令：

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/monopoly-pending-tools/browsers \
PLAYWRIGHT_MODULE=/tmp/monopoly-pending-tools/node_modules/playwright \
node scripts/check_rummikub_terminal_review.cjs --browser
```

截图与布局测量已保存至 `artifacts/rummikub-terminal-review/`，三档宽度已人工检查。
安装路径不同可覆盖上述环境变量。不要以 DOM 测试代替视觉验收。

## 主助手验收后部署与正式创建

1. 完成视觉检查、最终 diff 审查；协调并行麻将任务，不能把其未验收代码直接一起发布。
   本次未修改 app.js/index.html 或麻将任何文件。前端 JS URL 未改变，
   上线后应验证缓存更新/强制刷新；拉密 CSS 已提升为 v7。
2. 核实运行服务的 `TURTLE_SOUP_DB`、`DUEL_DB_PATH`。本次只读账号核验结果
   为 `(1, 南杉, 0)`，正式 apply 会再次只查 id/username/is_ai。
3. 先按 `docs/BACKUP.md` 做 SQLite 一致性备份；不能直接复制运行中的 .db。
   `--backup` 必须指向解包后的独立 Duel SQLite 快照，不能指向在线库或 tar。
   脚本验证快照可打开及完整性，但备份来源、时效由执行主助手负责核实。
4. 部署时因拉密 Python 插件和主站历史查询均有修改，分别重启
   `cedartoy-duel`、`cedartoy`；始终指定
   `supervisorctl -c /etc/supervisor/supervisord.conf`。本次未执行重启。
5. 先重新 dry-run；正式 apply 仅由最终验收的主助手执行：

```bash
cd /opt/cedartoy
vendor/duel/.venv/bin/python scripts/create_rummikub_review_demo.py --apply \
  --account-db /opt/cedartoy/turtle-soup/backend/turtle_soup.db \
  --duel-db /opt/cedartoy/vendor/duel/data/duel.db \
  --backup /实际备份目录/duel.db \
  --confirm-username 南杉 --confirm-user-id 1
```

脚本不会初始化/迁移生产库。仅独立事务新增一个 finished、preserved=1、
stake=0 的房间及四席、invite 元数据、本地演示说明和已读游标。
人类 canonical ID 为字符串 `"1"`；三位 NPC 使用唯一 system_npc ID。
固定房号或演示标记存在则拒绝重复，任一投影校验失败整体回滚。
不调用 create_room、钱包、结算、通知、成就接口；不读真实账号 token/密码。
席位中的 `P1`–`P4` 是规则引擎的席位标记，不是登录凭据。

样例从完整 106 张牌初始化，经 89 次合法动作形成堵局，
剩余点数 58/39/42/56，局分 -19/+39/-3/-17；没有人工伪造牌面或分数。
房间说明明确标注“演示、非真实战绩”。不写 move/resign 事件，invite
标记也避免后续成就记录；主站历史排除该 terminal_reason。
其他身份（含 id 2、human:1、1:2）通过真实投影和 ASGI 路由均拒绝读取。
正式创建后主助手需用南杉已有登录态打开链接验收，不能换取或伪装 token。
