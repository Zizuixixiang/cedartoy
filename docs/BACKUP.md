# cedartoy 备份体系

> 2026-07-17 升级：sqlite 一致性快照 + 每周恢复演练 + R2 异地推送。

## 方案总览

| 组件 | 位置 | 触发 |
|---|---|---|
| 每日备份脚本 | `scripts/backup_cedartoy.sh` | `/etc/cron.d/cedartoy-backup`，每日 3:50（赶在 4:00 guest 清理前） |
| 恢复演练脚本 | `scripts/backup_restore_check.py` | `/etc/cron.d/cedartoy-backup-check`，每周一 4:30 |
| 本地归档 | `/home/backups/cedartoy/data_YYYYMMDD.tar.gz` | 保留 7 天滚动删除 |
| 异地归档 | R2 `cedarclio-backup` 桶下 `cedartoy-backups/` 前缀 | 每日备份后 rclone 推送 |
| 日志 | `/var/log/cedartoy-backup.log`、`/var/log/cedartoy-backup-check.log` | cron 追加 |

## 每日备份（backup_cedartoy.sh）

旧方案直接 `tar` 打包 `/opt/cedartoy/data`，对启用 WAL 的 sqlite 库（sessions.db）有拿到不一致快照的风险，且遗漏了 data 之外的两个生产库。新流程（生产数据全程只读）：

1. 把 `data/` 复制到临时 staging 目录，排除 `*.db` / `*.db-wal` / `*.db-shm`（历史 `*.db.bak-*` 文件原样保留）；
2. 对全部生产 sqlite 库用 `sqlite3 .backup` 导出一致性快照到 staging 对应路径，并逐个跑 `PRAGMA quick_check` 验证：
   - `data/sessions.db`（WAL，主库）
   - `data/soup.db`、`data/toy.db`（当前为 0 字节占位，一并快照）
   - `turtle-soup/backend/turtle_soup.db`（**在 data 之外**，旧方案漏备）
   - `toy-platform/toy_accounts.db`（**在 data 之外**，旧方案漏备）
3. 打包为 `data_YYYYMMDD.tar.gz`（先写 `.tmp` 再原子改名）。归档内路径相对 `/opt/cedartoy`：`data/...`、`turtle-soup/backend/turtle_soup.db`、`toy-platform/toy_accounts.db`；
4. `rclone copy` 推送 R2（失败不影响本地备份与滚动清理，退出码 2 上报）；
5. 本地 7 天滚动删除（同旧方案）。

环境变量可覆盖（自测用）：`BACKUP_ROOT`、`R2_REMOTE`（置空跳过推送）、`RETENTION_DAYS`。

## 每周恢复演练（backup_restore_check.py）

每周一 4:30 自动执行，也可手动 `python3 scripts/backup_restore_check.py [--archive 路径]`：

1. 取 `/home/backups/cedartoy` 下最新 `data_*.tar.gz`，解包到临时目录（拒绝越界成员）；
2. 包内所有 `.db` 跑 `PRAGMA integrity_check`；
3. 关键路径抽查：sessions.db 的 `eco_sessions`（eco 存档）非空；`data/vendor_saves` 游戏子目录与线上对照无缺失；抽样 20 个 `progress.json` / `save.json` 可解析；
4. 新布局附加库（turtle-soup / toy-platform 快照）缺失记 WARN（2026-07-17 之前的旧格式归档属正常）。

结果 `PASS` / `PASS_WITH_WARNINGS`（退出码 0）或 `FAIL`（退出码 1）。

## 2026-07-17 演练结果

**对今日凌晨真实备份 `data_20260717.tar.gz`（旧格式，3.7 MB）：`PASS_WITH_WARNINGS`，退出码 0。**
- 3 个 .db integrity_check 全部 ok；eco_sessions 338 行；vendor_saves 8 个游戏目录齐全（arcade/burger/fishing/imitator_td/leek/market/memoria/workkk）；抽样 20 个 progress/save JSON 解析 0 失败。
- 2 项 WARN：旧格式归档缺 turtle_soup.db / toy_accounts.db 快照（预期，明日起新格式包含）。

**对新脚本自测产物（新格式，6.2 MB）：`PASS`，无警告** —— 5 个 .db（含 turtle_soup.db、toy_accounts.db）integrity_check 全部 ok，其余检查同上。自测归档与 R2 selftest 前缀已清理。

## R2 排查结论（2026-07-17）

- 本机 cedarclio 的异地备份机制：root crontab 每日 21:30 运行 `/opt/cedarclio/backup.sh`（pg_dump + chroma 打包），用 **snap 版 rclone**（`/snap/bin/rclone`，配置在 `/root/snap/rclone/599/.config/rclone/rclone.conf`）推送到 `cloudflare_r2_clio:cedarclio-backup`（alias → `cloudflare_r2:cedarclio-backup`，Cloudflare R2 S3 端点）。日志确认每日推送成功。
- **凭证可复用但为桶级 token**：无 ListBuckets 权限，且对其它桶（试探 `cedartoy-backups`）AccessDenied，无法新建独立桶。因此 cedartoy 备份推送到同一桶下的独立前缀 **`cloudflare_r2:cedarclio-backup/cedartoy-backups/`**，与 cedarclio 的归档（位于 `cedarclio-backup/cedarclio-backup/` 前缀）互不干扰；cedarclio 现有备份逻辑未做任何改动。
- 已把今日 `data_20260717.tar.gz` 手动推送到该前缀作为种子；此后由每日备份自动推送。
- 注意事项：snap 版 rclone 受沙箱限制**读不到 `/tmp`**，推送源文件须放在 `/home` 等常规路径（本方案归档在 `/home/backups/cedartoy`，不受影响）；R2 端未设生命周期删除，前缀会持续累积，如需远端滚动可后续在脚本里加 `rclone delete --min-age`。
- 未创建任何新凭证、未安装新工具。

## 恢复步骤（手册）

```bash
tar -xzf /home/backups/cedartoy/data_YYYYMMDD.tar.gz -C /tmp/restore
# 核对后按需拷回：
#   /tmp/restore/data/...                            → /opt/cedartoy/data/
#   /tmp/restore/turtle-soup/backend/turtle_soup.db  → /opt/cedartoy/turtle-soup/backend/
#   /tmp/restore/toy-platform/toy_accounts.db        → /opt/cedartoy/toy-platform/
# 拷回前停服务：supervisorctl -c /etc/supervisor/supervisord.conf stop cedartoy
# 远端取档：rclone copy cloudflare_r2:cedarclio-backup/cedartoy-backups/data_YYYYMMDD.tar.gz /tmp/
```
