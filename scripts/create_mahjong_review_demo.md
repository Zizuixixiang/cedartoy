# 麻将终局演示房（工单 #7）

只给已登录的人类账号 **南杉 / 1** 使用。脚本构造的是明确标注的演示局面，不是真实完成的对局；采用 136 张唯一实体牌，四家各有副露，南杉自摸，真实 Mahjong 引擎执行合法 `hu` 并返回番型。其余三个席位是本房独立 `system_npc`，不查询或借用任何其他账号。没有新 HTTP/admin API。

## 默认预览

```sh
cd /opt/cedartoy
vendor/duel/.venv/bin/python scripts/create_mahjong_review_demo.py \
  --username 南杉 --account-id 1
```

默认只在 `/tmp/mahjong-review-demo-*/` 建立并保留 `preview.db` 和 `human-projection.json`。不读取生产账号库，不连接生产 Duel 库。输出的 `/duel/?room=...` 只是路由格式，**dry-run 房号不是线上可访问链接**。完整人类端投影来自 `project_room_for_viewer`。

验证入口：

```sh
python3 -m unittest tests_toy.test_mahjong_review_demo tests_toy.test_duel_history
# 有可运行 Chromium 的环境；以下依赖位置由执行机器指定：
PLAYWRIGHT_MODULE=/path/to/playwright \
PLAYWRIGHT_BROWSERS_PATH=/path/to/browsers \
node scripts/check_mahjong_review_browser.cjs /tmp/mahjong-review-demo-XXX/human-projection.json
```

浏览器脚本仅拦截加载本地静态资源和临时投影，不访问生产站点。覆盖 360、430、1280px，产物默认写 `/tmp/mahjong-review-visuals/`；可用 `DUEL_UI_SCREENSHOTS` 修改。已使用 Chromium 在 360／430／1280px 完成真实浏览器检查：四家牌面、初次进入横向滚动下可见和牌张、弹窗原样、无页面水平溢出。截图位于 `/tmp/mahjong-review-visuals/`（临时输出）。另有真实 jsdom 测试覆盖完整页面、原有结算弹窗按钮/焦点、历史重入和其他游戏弹窗；不能替代视觉验收。

## 主助手后续执行（本开发任务未执行）

1. 验收补丁及三个宽度的视觉效果。部署 Duel 静态资源及 root `cedar_backend/duel_history.py` 的演示排除逻辑；**必须先部署 root 排除逻辑**，否则演示会被旧主站历史查询计入。按项目运维手册确定是否重启相应进程。
2. 核实实际进程的 `DUEL_DB_PATH` 和账号 `TURTLE_SOUP_DB`。只核对路径及目标账号，不读取凭据。确认这两个路径不同，备份路径尚不存在且目录已存在。
3. 在取得本次真实创建的显式指令后执行下列模板，替换全部占位路径。脚本在持有 Duel 写锁时，用独立只读连接生成 SQLite 一致性快照（0600），检查备份完整性，然后只在一个事务里新增演示房。

```sh
vendor/duel/.venv/bin/python scripts/create_mahjong_review_demo.py \
  --username 南杉 --account-id 1 \
  --apply --confirm CREATE_MAHJONG_REVIEW_DEMO_FOR_1 \
  --account-db /实际/账号主库.db \
  --duel-db /实际/duel.db \
  --backup /已存在备份目录/duel-before-mahjong-demo-唯一时间戳.db
```

4. 输出后，用本人正常登录态打开 `/duel/?room=<输出房号>`，验证复盘、演示标识、零筹码及他人访问拒绝，再把链接交给用户。不要使用自报身份或凭据冒充用户，不调用身份切换/Token 轮换工具。

## 安全范围与限制

- 生产模式只读查询账号的 `id, username, is_ai, deleted_at`，必须精确匹配南杉 / 1 / 人类 / 未删除。
- 不调用 `framework.create_room`、`play_move` 或服务启动迁移；不创建正式操作事件。只 INSERT `rooms` 1 行、`room_participants` 4 行、`room_event_cursors` 4 行，提交前要求变化总数恰为 9 并检查外键。标记 `terminal_reason=mahjong_review_demo`、`result.demo=true`、`preserved=1`、`stake=0`。
- SQL authorizer 禁止其他表写入及直接 UPDATE/DELETE；旧参与者类型推断触发器的 UPDATE 被显式忽略（脚本已写明类型）。意外副作用触发器、投影失败、外键异常或重复演示房均使事务回滚。
- 不改钱包、排行榜、账号、原有房间，不发通知/发奖。没有 move/resign 事件，因此现有成就 legacy 回填跳过；测试包含模拟服务重新初始化。root 历史查询排除该标记。目标本人可在自己的已结束房间列表看见演示，其他人的房间列表不会包含它。
- `preserved=1` 保留样例，脚本不自动删除。若重复执行会拒绝；如未来确需清理，应由主助手依据输出房号核对演示标记和参与者后另行授权处理。失败时备份文件可能仍在，不能把该文件存在当成创建成功。
- 本脚本未执行过生产模式；已在临时库验证同一原子写入函数、备份一致性、失败回滚、权限隔离、真实 ASGI HTTP 合约。生产执行仍须以上人工核对与部署步骤。
