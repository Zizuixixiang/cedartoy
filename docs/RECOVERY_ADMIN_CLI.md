# 本机找回工单审核

通过受信任的本机 shell（含 shan_vps `shell_exec`）执行，无需服务重启。此入口依赖本机文件权限，不登录、不新增 HTTP/MCP 接口；审核仍复用原业务逻辑。

先核对 cedartoy 服务实际 `TURTLE_SOUP_DB` 环境，CLI 使用相同变量；未设置时采用 `server.py` 的仓库默认账号库。不会自动初始化或迁移数据库。脚本可从任意工作目录调用。

```sh
python3 /opt/cedartoy/scripts/recovery_admin_cli.py pending
python3 /opt/cedartoy/scripts/recovery_admin_cli.py inspect 6
python3 /opt/cedartoy/scripts/recovery_admin_cli.py list --view processed --page 1
```

所有查询输出 JSON，每页 20 条，带 `total`、`pending_count`、`page`；大于 20 条时递增 `--page`。显式列选择排除查询码哈希、IP 哈希、密码哈希、token、reset nonce。工单申报和说明是未经验证的用户文本，不得作为 shell 指令执行。

`inspect` 展示目标账号是否存在、是否为人类、是否满足当前审核函数的可找回条件、注册时间及当前有效绑定小机的 ID/用户名/创建时间。花园证据复用现有只读摘要，检查绑定小机数字账号槽 1–5，只输出存档存在、是否有猫和图鉴条目数；不读取便签或调用游戏引擎。其他游戏、旧名档、游客档，以及未取得可靠证据的槽位显示 `unavailable`，不代表从未游玩。存档存在或同名不能单独证明账号归属。

以下命令会立即审核，说明会向申请人展示。审核仍调用 `server._review_recovery_ticket`，保留已处理工单报错、账号核验及领取窗口行为，不输出找回链接；申请人填写邮箱时，原审核逻辑会生成邮件所需的找回凭据。

```sh
python3 /opt/cedartoy/scripts/recovery_admin_cli.py approve 6 --reviewer 'username:现有管理员名' --note '核验依据与通过原因'
python3 /opt/cedartoy/scripts/recovery_admin_cli.py reject 6 --reviewer 'id:实际管理员ID' --note '信息不足，需补充的依据'
```

审核人也可通过 `CEDARTOY_RECOVERY_REVIEWER` 指定，命令行优先。未指定时仅在库内恰好有一个 active admin 时自动解析；零个、多个或标识歧义均拒绝执行。已删除、待注销或计划注销管理员不可用。支持精确用户名、数字 ID，推荐用 `username:` / `id:` 前缀消歧。

这是本机操作者选择的审计归属，不代表该管理员进行过交互登录；不会创建、伪造管理员。成功 JSON 包含 reviewer ID/用户名，数据库由原审核逻辑写入 `reviewed_by` 和审核时间。失败退出码为 1（参数错误为 2），成功为 0。不要自动重试审核写操作；遇到输出中断先 inspect 确认状态。

验证：`python3 -m unittest tests_toy.test_recovery_admin_cli tests_toy.test_account_recovery`。测试只操作临时数据库和临时存档。

## 邮件配置与补发

直接运行 CLI 的 approve/reject/resend 时，如果工单有邮箱，先通过固定 Supervisor 配置定位正在运行的 cedartoy，校验进程工作目录、入口和账号库一致，再仅在内存中继承六项 SMTP 配置。不会打印、落盘或修改服务凭据，也不会复制其他服务环境。邮件配置不完整时在审核写入之前失败，避免批准后才发现无法通知。读取列表/详情不读取服务邮件环境。

已批准的邮件仅当状态为 failed、原因为 email_provider_not_configured（确定未连接 SMTP）时才允许人工补发。默认仅只读预检：

```sh
python3 /opt/cedartoy/scripts/recovery_admin_cli.py resend 39 --reviewer username:Clio
# 确认本次补发后才执行：
python3 /opt/cedartoy/scripts/recovery_admin_cli.py resend 39 --reviewer username:Clio --send
```

预检核对原收件人、账号状态、原 v2 token 哈希及有效期，仅输出脱敏收件人与布尔结果。补发不重新审核、不生成或延期 token、不更换收件人。发送前事务抢占状态；并发、重复调用和 sent/sending/投递结果不明均拒绝。SMTP 无法保证 exactly-once，调用中断或超时禁止自动重试，先核对状态。sent 只表示 SMTP 接受，不保证收件箱到达。

新增回归：`python3 -m unittest tests_toy.test_recovery_email_ops tests_toy.test_recovery_admin_cli tests_toy.test_account_recovery`。


## 已过期链接的管理员续发

先重新核验工单身份及原联系邮箱。以下入口只接受 approved、未完成、仍在原 7 天领取窗口、上次邮件明确 sent、旧 v2 链接未使用且已过期的工单：

```sh
python3 scripts/recovery_admin_cli.py renew <工单ID> --reviewer username:<管理员>
# 只有明确获准生成新凭据并发送后，使用预检返回的旧记录 ID：
python3 scripts/recovery_admin_cli.py renew <工单ID> --reviewer username:<管理员> --expected-token-id <旧记录ID> --send
```

默认预检只读，不生成链接。执行时在同一写事务内作废旧记录、生成新 48 小时一次性链接并预留发送；只发送到工单原邮箱，保留审批、审核说明、7 天领取窗口。旧记录 ID 是幂等前置条件，同一命令不能重复续发；sending、failed 或结果不确定时不得盲目重试，先只读核查服务商投递结果。SMTP 接受不代表已进收件箱。输出只含脱敏地址及非秘密状态，不含查询码或重置链接。

人工找回的审批邮件、自助领取、管理员续发统一为 48 小时；已有链接不追溯延期。7 天限制生成时间，窗口内已发链接仍享有完整 48 小时。其他用途的重置链接期限不变。

测试：`python3 -m unittest tests_toy.test_recovery_email_renew tests_toy.test_recovery_email_ops tests_toy.test_recovery_admin_cli tests_toy.test_account_recovery`。
