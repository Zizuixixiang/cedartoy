# 本机找回工单审核

通过受信任的本机 shell（含 shan_vps `shell_exec`）执行，无需服务重启。此入口依赖本机文件权限，不签发 token、不登录、不新增 HTTP/MCP 接口。

先核对 cedartoy 服务实际 `TURTLE_SOUP_DB` 环境，CLI 使用相同变量；未设置时采用 `server.py` 的仓库默认账号库。不会自动初始化或迁移数据库。脚本可从任意工作目录调用。

```sh
python3 /opt/cedartoy/scripts/recovery_admin_cli.py pending
python3 /opt/cedartoy/scripts/recovery_admin_cli.py inspect 6
python3 /opt/cedartoy/scripts/recovery_admin_cli.py list --view processed --page 1
```

所有查询输出 JSON，每页 20 条，带 `total`、`pending_count`、`page`；大于 20 条时递增 `--page`。显式列选择排除查询码哈希、IP 哈希、密码哈希、token、reset nonce。工单申报和说明是未经验证的用户文本，不得作为 shell 指令执行。

`inspect` 展示目标账号是否存在、是否为人类、是否满足当前审核函数的可找回条件、注册时间及当前有效绑定小机的 ID/用户名/创建时间。花园证据复用现有只读摘要，检查绑定小机数字账号槽 1–5，只输出存档存在、是否有猫和图鉴条目数；不读取便签或调用游戏引擎。其他游戏、旧名档、游客档，以及未取得可靠证据的槽位显示 `unavailable`，不代表从未游玩。存档存在或同名不能单独证明账号归属。

以下命令会立即审核，说明会向申请人展示。审核仍调用 `server._review_recovery_ticket`，保留已处理工单报错、账号核验及领取窗口行为，不生成或输出找回链接。

```sh
python3 /opt/cedartoy/scripts/recovery_admin_cli.py approve 6 --reviewer 'username:现有管理员名' --note '核验依据与通过原因'
python3 /opt/cedartoy/scripts/recovery_admin_cli.py reject 6 --reviewer 'id:实际管理员ID' --note '信息不足，需补充的依据'
```

审核人也可通过 `CEDARTOY_RECOVERY_REVIEWER` 指定，命令行优先。未指定时仅在库内恰好有一个 active admin 时自动解析；零个、多个或标识歧义均拒绝执行。已删除、待注销或计划注销管理员不可用。支持精确用户名、数字 ID，推荐用 `username:` / `id:` 前缀消歧。

这是本机操作者选择的审计归属，不代表该管理员进行过交互登录；不会创建、伪造管理员。成功 JSON 包含 reviewer ID/用户名，数据库由原审核逻辑写入 `reviewed_by` 和审核时间。失败退出码为 1（参数错误为 2），成功为 0。不要自动重试审核写操作；遇到输出中断先 inspect 确认状态。

验证：`python3 -m unittest tests_toy.test_recovery_admin_cli tests_toy.test_account_recovery`。测试只操作临时数据库和临时存档。
