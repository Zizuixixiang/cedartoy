# 反馈与建议 QA 工单

首页四项底栏最左侧「反馈」打开粉紫像素弹窗；管理员在 `/admin` 原找回工单区域切换「账号找回 / 意见反馈」。两类工单独立存表、独立状态，反馈不生成密码重置链接。

## 会话与权限

- 实际 `TURTLE_SOUP_DB` 账号库中的 `feedback_tickets` 保存工单，`feedback_messages` 追加 QA 消息。首次提交是第一条玩家消息，后续玩家追问和管理员答复均追加，不修改/删除旧消息。每条记录保存角色、实际写入账号（匿名为空）、正文、Unix时间；按服务端消息ID顺序展示，同秒并发也有确定顺序。
- 三种状态：玩家发起或追问 → `pending`（待处理）；管理员回复 → `processing`（处理中）；管理员结单 → `closed`（已结束）。已结束的玩家追问会自动重新打开；已结束后管理员继续答复会转处理中。只有管理员能设置状态；玩家没有结单或改状态接口。
- 网页沿用 `cedartoy_token` → `Authorization: Bearer …`。登录归属只取 `_current_account` 的实际账号ID；绑定关系、自填contact和自报ID不能赋予权限。携带失效token不能静默降为匿名提交。
- 匿名提交一次性返回 `secrets.token_urlsafe(32)`（256位随机性）查询码；服务端只存SHA-256哈希。原码可查该单、追加消息，追加时必须同时匹配单号和原码，不能转向其他工单。凭码操作不依赖登录状态；没有匿名列表。码不进入URL、日志或浏览器持久存储，持码人可读写该单，遗失无法重新获取。
- 分类 `bug/suggestion/account/other`；每条消息必填、最多4000字，自填账号参考最多120字。没有附件、截图或文件存储。
- `reply` 在管理员请求中代表**追加**的对外消息；`internal_note` 是独立内部备注，仅管理员可见，不进入消息历史。备注省略保留、空串清除；不能通过空reply清除历史答复。
- 列表每页20条，页码1–1000000；每项只有最多80字初始问题摘要、分类、状态、更新时间、消息数、末条说话方，按更新时间倒序。管理端待处理且末条为玩家时显示「玩家新留言」。默认不展开任何工单，只打开选中会话；返回/分页重载摘要，清除原选中状态。
- 对话默认只显示最近3条；更早记录在「展开历史消息 / 收起历史消息」中。服务端每次最多返回10条消息，按先后排列；`next_before_id` 游标用于读取更早记录，新消息插入不会使旧历史分页移位。历史区域可滚动，前后台均可继续加载，不截断服务端记录。
- 玩家响应不含内部备注、审核人、实际账号ID、IP哈希或查询码哈希。管理员也不能获得查询码/哈希。页面使用textContent或既有HTML转义，外部文本不作为指令执行。

## 限流与一致性

- 新建工单按IP哈希和已登录账号分别限5次/小时、20次/24小时。
- 玩家消息（含首条）按IP及实际账号限20次/小时、100次/24小时；每单限10次/小时、40次/24小时。匿名每单限流在换IP后仍有效。
- 管理员每个账号的回复、备注和改状态合计限120次/小时、1000次/24小时，MCP同样生效。`feedback_review_limits` 只保留最近24小时的写入计数事件，不存正文。
- 所有持久限流计数、消息插入和状态更新在同一SQLite写事务内完成。反馈HTTP另复用现有每IP30次请求窗口限流。JSON请求体上限64KiB，不接受分块请求；响应 `Cache-Control: no-store`。所有身份/能力码校验都在服务端完成。

## 初版升级

`init_schema` 可从初版（四状态、单条reply列）直接安全升级：在一个savepoint内重建三状态工单表，保留单号、AUTOINCREMENT高水位、归属、查询码哈希、分类、正文、参考账号、备注和时间。原正文迁为首条玩家消息，非空旧reply迁为管理员消息；`replied`映射为`processing`。旧reply列移除，不再双轨维护答复。

迁移失败整体回滚，不留下半张表；重复初始化不重复追加消息。启动迁移或首次反馈访问均可初始化；不提交调用者已有事务、不触碰密码找回表。升级后不应回退运行依赖旧reply列的初版代码；需要回滚时使用账号库的一致性快照。验证仅使用隔离样本和SQLite一致性副本。

## HTTP 与 MCP

所有反馈HTTP路由均为POST、`Content-Type: application/json`：

| 路由 | 参数 / 权限 |
| --- | --- |
| `/api/feedback/submit` | category、content、可选contact；Bearer身份或匿名 |
| `/api/feedback/list` | 可选page；必须登录，仅本人 |
| `/api/feedback/detail` | ticket_id、可选before_id；必须登录，仅本人 |
| `/api/feedback/query` | query_code、可选before_id；仅持码单条 |
| `/api/feedback/message` | ticket_id、content、可选query_code；实际owner或匹配原码的匿名单；拒绝额外字段 |
| `/api/admin/feedback/list` | 可选status（含all）、page；管理员 |
| `/api/admin/feedback/detail` | ticket_id、可选before_id；管理员 |
| `/api/admin/feedback/review` | ticket_id、confirm=true；reply/internal_note/status至少一项；管理员 |

`account` 保留 `admin_feedback_list/detail/review`，参数与对应管理HTTP路由一致。review传非空reply会追加消息并自动转processing；可同时显式传status=closed追加后结单，也可仅设置状态/内部备注。reply不允许搭配pending，避免答复后仍伪装为新玩家留言。

三个管理员动作与原 `admin_recovery_*` 共用 `_require_admin_account`，先鉴权后验证参数/读工单，绑定管理员的小机不继承权限；路径Token、Bearer、显式account token沿用原链路。管理员动作拒绝额外参数。反馈不自动注入普通MCP响应。

发现入口 `get_guide(game="account")`。普通account schema仍只有action；在既有legacy兼容schema提供status/reply/internal_note和before_id，全部使用基础string/integer类型，不引入数值enum。没有新增常驻玩家反馈MCP工具。

## 定向验证

```sh
python3 -m unittest tests_toy.test_feedback tests_toy.test_account_recovery tests_toy.test_admin_recovery_mcp tests_toy.test_mcp_schema_selection tests_toy.test_root_mcp_protocol.RootMcpProtocolTests.test_play_schemas_keep_gemini_numeric_enum_compatibility
node scripts/check_feedback_browser.js
# DOM功能检查不代替视觉验收：
FEEDBACK_DOM_ONLY=1 node scripts/check_feedback_browser.js
node scripts/check_account_recovery_ui.js
node scripts/check_reset_password_ui.js
git diff --check
```

复用已有jsdom/Playwright；`NODE_PATH`、`PLAYWRIGHT_MODULE`、`PLAYWRIGHT_BROWSERS_PATH`或`CHROMIUM_PATH`可指定工具。浏览器拦截网络，经管道调用真实处理函数和临时账号库，不监听端口、不访问生产。快速UI序列仅跳过内存请求桶，持久化写入限流仍真实执行；限流另有定向后端测试。`SCREENSHOT_DIR`指定360/430/1280px截图目录。干净worktree导入server需要已有eco和ci-yu-wu引擎源码，不应连接生产存档作为替代。

合并时以任务起点HEAD逐块合入，尤其核对并行变动的 `index.html`、`server.py`、HTTP/MCP路由，勿整文件覆盖生产树。反馈历史折叠使用独立feedback类名和节点ID，不修改其他游戏历史折叠功能。合并后重跑定向测试与三个宽度的视觉检查。本文不构成部署授权。
