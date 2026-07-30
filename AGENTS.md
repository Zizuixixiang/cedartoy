# CedarToy 运维速查（AGENTS）

单体 Python 服务：`server.py`（无框架 http.server），前端 `index.html`（玩家）/ `admin.html`（管理，路由 `/admin`）。

## 服务管理
- supervisor 配置不在默认位置，必须带 `-c`：`supervisorctl -c /etc/supervisor/supervisord.conf status|restart cedartoy`
- 进程：`cedartoy`（主）、`cedartoy-duel`、`cedartoy-garden-cat`、`cedartoy-workkk`（独立服务）
- 改 server.py / *_adapter 后需重启 cedartoy；改 scripts/ 下 cron 脚本不用
- 日志：/var/log/cedartoy.out.log、cedartoy.err.log

## 数据库（都是 sqlite）
- `turtle-soup/backend/turtle_soup.db`：账号主库——toy_users（含 is_ai）、user_bindings（human↔ai 绑定）、binding_tokens、password_reset_tokens、players、anti_addiction_states、account_registration_events
- `data/sessions.db`：eco_sessions、ciyuwu_sessions、announcements、announcement_reads
- `vendor/Garden-Cat-Engine/garden_cat.db`：garden_saves（按 session_id，非 user_id）
- 根目录和 data/ 下若干 0 字节 .db 是历史遗留，别用
- ⚠️ 时区坑：toy_users 等大多是北京时间字符串；password_reset_tokens 用 sqlite `datetime('now')` 是 UTC

## 存档
- 文件型：`data/vendor_saves/<game>/<player_id>/`，槽位后缀 `id:2`~`id:5`（槽1为纯id），游客 `guest:xxx`
- eco / ciyuwu 存 sessions.db 对应表，不在 vendor_saves
- 覆盖二次确认：vendor 系走 base.py `require_save_confirm`；eco_new 有 confirm 参数；garden_cat 自带
- export/import：全部 vendor 游戏 + fishing 走 JSON；eco/ciyuwu 走 base64；均按槽独立

## 备份
- cron 每天 3:50 `scripts/backup_cedartoy.sh` → `/home/backups/cedartoy/data_YYYYMMDD.tar.gz`，保留7天，推 R2；失败自动重试3次，全败日志有 `BACKUP FAILED AFTER 3 ATTEMPTS`
- 4:00 清理 guest 存档；周一 4:30 恢复演练
- 日志 /var/log/cedartoy-backup.log
- 捞档：解包对应日期 tar，注意覆盖发生在当天 3:50 之前还是之后

## 常见工单
- 用户称"存档/绑定全没了"：先查 toy_users 是否有同名不同大小写的新账号——login_or_register 大小写敏感且查无此人会静默注册新号
- 小机忘密码：人类登录前端，绑定列表里小机条目有「重置密码」按钮（后端 account action `reset_machine_password`）；管理员也可在 /admin 生成重置链接
- 发全员公告：INSERT 进 sessions.db 的 announcements 表（参考 announcements.py 头部注释），玩家下次指令时弹一次

## Git 纪律
- 主仓库（Zizuixixiang/cedartoy）正常提交推送；backups/ 和 *.bak 不进库
- `vendor/*` 全部是上游仓库：本地补丁（commit 注明"勿推上游"）永不 push，子模块指针变动不提交
- `eco/` 是独立仓库 cedareco，动了 eco 引擎本体才需要单独推
