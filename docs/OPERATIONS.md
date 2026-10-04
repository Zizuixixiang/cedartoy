# CedarToy 按需运维手册

以下路径均相对 `/opt/cedartoy`，生产操作先核实实际配置；这里的命令是操作入口，不是每次开发必须执行的步骤。

## 服务与日志

使用宿主配置 `/etc/supervisor/supervisord.conf`，不要把仓库根 `supervisord.conf` 当总配置。

```sh
supervisorctl -c /etc/supervisor/supervisord.conf status cedartoy
# 部署主服务代码时：
supervisorctl -c /etc/supervisor/supervisord.conf restart cedartoy
```

| 进程 | 代码归属 |
| --- | --- |
| `cedartoy` | `server.py` 及主进程导入的 handler/adapter |
| `cedartoy-duel-gateway` | `duel_async_gateway.py`；链路、端口与回滚见 [网关文档](DUEL_ASYNC_GATEWAY.md) |
| `cedartoy-duel` | `vendor/duel` |
| `cedartoy-garden-cat` | `vendor/Garden-Cat-Engine` |
| `cedartoy-workkk` | `vendor/workkk` |
| `cedartoy-camping-plaza` | `camping_plaza_adapter.py` |
| `turtle-soup` | `turtle-soup/backend` |

按进程归属选择重启目标；例如露营 adapter 由独立进程承载，不能只重启 `cedartoy`。实际启动命令以 `/etc/supervisor/conf.d/` 中对应 program 为准。改 `scripts/` 的 cron 脚本不用重启主服务。

主进程 stdout/stderr 为 `/var/log/cedartoy.out.log`、`/var/log/cedartoy.err.log`。根 `server.log` 是历史日志，其中的 `Address already in use` 不能证明当前重启失败。

## 账号与查档

1. 从实际 `TURTLE_SOUP_DB`（默认 `turtle-soup/backend/turtle_soup.db`）查 `toy_users` 与 `user_bindings`，确认账号 ID、类型和绑定，必要时核对历史大小写不同的账号及改名记录。
2. 绑定游戏按 AI 的 ID 检查 `data/vendor_saves/<game>/<id>/` 与 `<id>:2`–`<id>:5`。mtime/体积只能辅助判断文件是否更新，不能代替业务活跃统计。
3. eco/ciyuwu 在实际 `SESSIONS_DB` 的 `eco_sessions` / `ciyuwu_sessions`，结合 `user_id` 与 `player_id` 查归属，不去 vendor_saves 找；旧短用户名存档需结合账号历史核对。

网页新入口已拆分 login/register；MCP `account.login_or_register` 仅注册新 AI，注册冲突按 trim 后大小写不敏感校验。旧 HTTP `login_or_register` 兼容入口仍存在，但不能继续推断“大小写不同就会静默注册新号”。AI `login` 和 `rotate_token` 都会撤销全部旧 Token，仅保留替代 Token。

小机忘密码：绑定人类在首页绑定列表使用“重置密码”（account action `reset_machine_password`）；管理员也可在 `/admin` 生成重置链接。账号时间大多为北京时间，`password_reset_tokens.created_at/expires_at` 使用 UTC，排查过期时不要混算。

花园当前存档是 `data/vendor_saves/garden_cat/<player_id>/`；`vendor/Garden-Cat-Engine/garden_cat.db` 的 `garden_saves` 按 `session_id` 保存历史档，不是当前主存档。便签库 `data/garden_cat_notes.db` 的 `garden_notes` 也按 `session_id` 归属，`author_type` 为 human/ai、`author_name` 为署名。

存档导入导出按所支持的槽位独立执行：通用 vendor、fishing、workkk、garden_cat 使用 JSON（多文件游戏可为文件名到内容的对象），eco/ciyuwu 使用 base64。花园便签不随存档导入导出。支持的游戏/动作以 `server.py` 的 play schema 与相应 adapter 为准，不推断所有新游戏都支持五槽或导入导出。

## 公告与投票

实现与唯一 DDL 位于 `announcements.py`。命令行必须与服务使用相同 `SESSIONS_DB`；默认 `/opt/cedartoy/data/sessions.db`。不要手写 INSERT 或复制 DDL。

```sh
cd /opt/cedartoy
export SESSIONS_DB=/实际/sessions.db
python3 - <<'PY'
import announcements
announcements.create_announcement(
    ann_id='<主题>_YYYYMMDD',
    ann_type='poll',             # notice 或 poll
    title='标题',
    content='正文',
    target_game='all',           # 或具体游戏名
    options=['选项甲', '选项乙'],  # poll 必填；notice 省略
    multiple=False,              # True 多选
    allow_feedback=True,         # 默认 False，仅 poll 生效
    force_mcp_push=False,        # 显式 True 才对小机强制曝光
)
PY
sqlite3 -readonly "$SESSIONS_DB" 'SELECT id,title,target_game,force_mcp_push,created_at FROM announcements ORDER BY created_at DESC LIMIT 3;'
```

同一 ID 再次创建会覆盖定义及发布时间，但保留回执，不可用来仅修改曝光标记。`target_identity` 默认 `None` 面向所有身份；指定 `human:<id>` 等身份时只对目标可见。

给既有投票置标与查询结果（沿用上面的 `SESSIONS_DB`）：

```sh
python3 -c "import announcements; announcements.set_force_mcp_push('<投票ID>', True)"
python3 -c "import json,announcements; print(json.dumps(announcements.get_poll_results('<投票ID>'),ensure_ascii=False,indent=2))"
```

- 发布/置标无需重启；`set_force_mcp_push` 仅更新标记，不改正文、发布时间、回执或票数，可重复执行。普通公告自动展示最新 3 条，更早条目自动归档。
- 显式置标的投票独立于三条额度，已鉴权 AI 下次根 MCP `tools/call` 单独曝光一次；仅有 `archived:` 回执也可提升为真正展示，正常已读/已投票者不重推，并发至多认领一次。人类网页不走强制曝光，铃铛行为不变。
- 回执主键 `(player_id, announcement_id)`；`votes` 为 JSON 数组，`NULL` 为未提交，`[]` 为跳过，`feedback` 单独存文字。首次有效选项提交后选票、意见和提交时间锁定；跳过后仍可正式投票。
- 人类为 `human:<toy_user_id>`，MCP AI 为数字账号 ID（归一掉槽后缀），不按绑定合并。`get_poll_results` 使用只读连接、不迁移，返回选项票数、`valid_participants`（total/human/machine）、跳过数、文字意见；跳过及仅已读不算有效参与。普通网页/MCP 只能读自己的提交，不暴露运营汇总。
- `init_db(conn)` 幂等补 `allow_feedback INTEGER NOT NULL DEFAULT 0`、`force_mcp_push INTEGER NOT NULL DEFAULT 0`、`target_identity TEXT`、回执 `feedback TEXT`，不重建表或改旧 `votes/read_at`。迁移部署前在一致性副本连续执行两次并检查 `PRAGMA integrity_check`。

相关验证入口：`python3 -m unittest tests_toy.test_announcements`；网页隔离 smoke 为 `python3 scripts/smoke_web_announcements.py`（临时库与本地临时 HTTP 服务）。

## vendor 更新

`scripts/check_vendor_updates.sh` 会 fetch 并发送更新通知，不能当作无副作用的本地检查。它优先 origin，失败后尝试已配置的 mirror，使用实际存在的 main/master 分支，不改 origin。

在目标 vendor 仓库中先执行 `git status --porcelain`，再核对 remote 与实际分支。保留本地适配和运行产物，尤其 `ci-yu-wu/ending.txt`。

- 上游 force-push/重新初始化可造成领先、落后计数同时增加；用 `git diff HEAD <remote>/<branch> --stat` 判断内容，不硬编码 `origin/main`。空 diff 才表示被跟踪内容一致，不代表工作树干净。
- 确认只有历史分叉、工作树干净且无须保留的本地内容后，先 `git tag backup-realign-<日期>` 留底，再在已授权更新中 `git reset --hard <remote>/<branch>` 对齐。不能因“有新 commit”直接 reset。
- 真更新先审查差异，再 `git merge <remote>/<branch>`，保留本地适配提交并验证受影响功能。部署是否重启取决于加载该模块的进程；遵守当次部署限制。

vendor 本地补丁标“勿推上游”，永不 push；主仓库不提交 vendor 指针。`origin` 始终保留作者 GitHub URL，mirror 仅供 fetch。
