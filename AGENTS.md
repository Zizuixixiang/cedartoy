# CedarToy 运维速查（AGENTS）

单体 Python 服务：`server.py`（无框架 http.server），前端 `index.html`（玩家）/ `admin.html`（管理，路由 `/admin`）。

## 服务管理
- supervisor 配置不在默认位置，必须带 `-c`：`supervisorctl -c /etc/supervisor/supervisord.conf status|restart cedartoy`（cedar-remote 的 shell_exec 里不带 `-c` 也能用，报 refused 再加）
- 进程：`cedartoy`（主）、`cedartoy-duel`、`cedartoy-garden-cat`、`cedartoy-workkk`（独立服务）
- 改 server.py / *_adapter 后需重启 cedartoy；改 scripts/ 下 cron 脚本不用
- 日志：/var/log/cedartoy.out.log、cedartoy.err.log
- ⚠️ 根目录 `server.log` 停在 2026-07-05，是死文件；里面那条 `Address already in use` 是历史尸体，重启后排障别看它，看 /var/log 那两个

## 数据库（都是 sqlite）
- `turtle-soup/backend/turtle_soup.db`：账号主库——toy_users（含 is_ai）、user_bindings（human↔ai 绑定）、binding_tokens、password_reset_tokens、players、anti_addiction_states、account_registration_events
- `data/sessions.db`：eco_sessions、ciyuwu_sessions、announcements、announcement_reads
- `vendor/Garden-Cat-Engine/garden_cat.db`：garden_saves（按 session_id，非 user_id）——只剩 1 行老档，别当主库
- `data/garden_cat_notes.db`：garden_notes（花园便签，author_type human/ai + author_name 署名，按 session_id）
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
- MCP 鉴权双通道：路径带 token 和 Authorization: Bearer 等效；改 tools/call 分发时每个工具都要 `path_token or bearer_token`，漏了 bearer 用户就会报"缺少或无效的塘子玩家身份"（2026-07-31 修过 play/list_games）
- 发全员通知/投票（2026-08-01 验证过的完整流程，别再手写 INSERT）：
  ```
  cd /opt/cedartoy && python3 -c "
  import announcements
  announcements.create_announcement(
      ann_id='<主题>_YYYYMMDD',   # 重复 id 覆盖旧内容（已读记录保留）
      ann_type='notice',          # notice 或 poll；poll 必须带 options=[...]
      title='标题',
      content='正文',
      target_game='all',          # 或具体游戏名
  )"
  ```
  - DB 路径：announcements.py 读 SESSIONS_DB 环境变量，兜底 /opt/cedartoy/data/sessions.db；若 server 配了自定义 SESSIONS_DB，命令行调用前先 export 同款，否则写错库白发
  - 不需重启：玩家下次指令时实时读库弹一次
  - 验证：`sqlite3 data/sessions.db "SELECT id,title,target_game,created_at FROM announcements ORDER BY created_at DESC LIMIT 3;"`
- 查某玩家玩过什么（最短路径，别去翻空库）：
  1. 账号+绑定 → `turtle_soup.db` 的 toy_users / user_bindings
  2. 玩过哪些游戏 → `ls -l data/vendor_saves/*/<ai_user_id>*`，看 mtime 和体积就是活跃度
  3. eco / ciyuwu 不在 vendor_saves，查 sessions.db 对应表的 user_id
  - 存档一律挂**绑定机(AI)的 user_id**，人类号名下通常是空的，别以为没玩过

## Git 纪律
- 主仓库（Zizuixixiang/cedartoy）正常提交推送；backups/ 和 *.bak 不进库
- `vendor/*` 全部是上游仓库：本地补丁（commit 注明"勿推上游"）永不 push，子模块指针变动不提交
- `eco/` 是独立仓库 cedareco，动了 eco 引擎本体才需要单独推

## vendor 更新检测（每日脚本报的"N个新commit"）
- 报了不等于真有更新：上游 force-push / 重新 init 后，会出现本地领先 N、落后 N 的对称假象
- 判定：`git diff HEAD origin/main --stat` —— 输出为空即内容一字不差，只是历史线分叉
  - 假警报处理：`git tag backup-realign-<日期>` 留底，再 `git reset --hard origin/main` 对齐，之后不再天天误报
  - travel-mcp 2026-07-31 就是这种（34 对 34，diff 0 文件，无共同祖先），已对齐
- 真更新（fork + 本地适配那类）：直接 `git merge origin/main`，本地适配是独立 commit，一般不冲突；合完看改的是不是 server.py import 的模块，是就重启 cedartoy
- 合前先 `git status --porcelain`：ci-yu-wu 的 `ending.txt` 是游戏跑出来的存档产物被 git 追踪着，别当脏改动清掉
