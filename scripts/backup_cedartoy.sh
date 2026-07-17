#!/usr/bin/env bash
# cedartoy 每日备份：sqlite 一致性快照 + JSON 存档打包 + R2 推送 + 本地 7 天滚动。
# 由 /etc/cron.d/cedartoy-backup 于每日 3:50 调用（赶在 4:00 guest 清理之前）。
#
# 归档布局（相对 /opt/cedartoy）：
#   data/...                              JSON 存档等原样打包，其中 *.db 为一致性快照
#   turtle-soup/backend/turtle_soup.db    快照（生产库，位于 data 之外）
#   toy-platform/toy_accounts.db          快照（生产库，位于 data 之外）
#
# 生产数据只读：所有 sqlite 库先用 .backup 导出到临时 staging 目录再打包。
#
# 可用环境变量覆盖（自测用）：BACKUP_ROOT、R2_REMOTE（置空跳过推送）、RETENTION_DAYS

set -uo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

APP_ROOT="/opt/cedartoy"
DATA_DIR="$APP_ROOT/data"
BACKUP_ROOT="${BACKUP_ROOT:-/home/backups/cedartoy}"
# 复用 cedarclio 的 rclone 凭证；token 仅授权 cedarclio-backup 桶，故用独立前缀隔离
R2_REMOTE="${R2_REMOTE-cloudflare_r2:cedarclio-backup/cedartoy-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
RCLONE_BIN="/snap/bin/rclone"

# data 之外的生产 sqlite 库（相对 APP_ROOT）
EXTRA_DBS=(
  "turtle-soup/backend/turtle_soup.db"
  "toy-platform/toy_accounts.db"
)

DATE_STR="$(date +%Y%m%d)"
ARCHIVE="$BACKUP_ROOT/data_${DATE_STR}.tar.gz"

STAGE="$(mktemp -d /tmp/cedartoy_backup.XXXXXX)" || { log "Failed: mktemp"; exit 1; }
trap 'rm -rf "$STAGE"' EXIT

log "Starting backup (stage=$STAGE, archive=$ARCHIVE)"

# --- 1. 复制 data（排除 sqlite 主库及 -wal/-shm，稍后以快照替代；.bak-* 历史文件原样保留） ---
log "Step 1: copying $DATA_DIR (excluding live sqlite files)"
if ! tar -C "$APP_ROOT" \
      --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
      -cf - data | tar -C "$STAGE" -xf -; then
  log "Failed: staging copy of data/"
  exit 1
fi

# --- 2. sqlite 一致性快照（.backup 对 WAL 库安全，sessions.db 已启用 WAL） ---
snapshot_db() {
  local src="$1" rel="$2" dest
  dest="$STAGE/$rel"
  mkdir -p "$(dirname "$dest")" || return 1
  if ! sqlite3 "$src" ".timeout 10000" ".backup '$dest'"; then
    log "Failed: sqlite backup of $src"
    return 1
  fi
  local check
  check="$(sqlite3 "$dest" "PRAGMA quick_check;")" || check="pragma-failed"
  if [[ "$check" != "ok" ]]; then
    log "Failed: quick_check on snapshot of $src -> $check"
    return 1
  fi
  log "  snapshot ok: $rel"
  return 0
}

log "Step 2: snapshotting sqlite databases"
SNAP_FAIL=0
while IFS= read -r -d '' db; do
  snapshot_db "$db" "${db#"$APP_ROOT"/}" || SNAP_FAIL=1
done < <(find "$DATA_DIR" -type f -name '*.db' -print0)

for rel in "${EXTRA_DBS[@]}"; do
  if [[ -f "$APP_ROOT/$rel" ]]; then
    snapshot_db "$APP_ROOT/$rel" "$rel" || SNAP_FAIL=1
  else
    log "  warning: expected db missing: $APP_ROOT/$rel"
    SNAP_FAIL=1
  fi
done

if [[ "$SNAP_FAIL" -ne 0 ]]; then
  log "Failed: one or more sqlite snapshots failed"
  exit 1
fi

# --- 3. 打包（先写 .tmp 再原子改名，避免半截归档被当成有效备份） ---
log "Step 3: creating archive"
mkdir -p "$BACKUP_ROOT" || { log "Failed: cannot create $BACKUP_ROOT"; exit 1; }
STAGE_TOPS=()
for top in data turtle-soup toy-platform; do
  [[ -d "$STAGE/$top" ]] && STAGE_TOPS+=("$top")
done
if ! tar -zcf "$ARCHIVE.tmp" -C "$STAGE" "${STAGE_TOPS[@]}"; then
  log "Failed: tar failed"
  rm -f "$ARCHIVE.tmp"
  exit 1
fi
mv -f "$ARCHIVE.tmp" "$ARCHIVE" || { log "Failed: rename archive"; exit 1; }
log "Step 3: ok ($(du -h "$ARCHIVE" | cut -f1))"

# --- 4. 推送 R2（失败不影响本地备份与滚动清理，但以非零退出码上报） ---
R2_FAIL=0
if [[ -n "$R2_REMOTE" ]]; then
  log "Step 4: pushing archive to $R2_REMOTE"
  if "$RCLONE_BIN" copy "$ARCHIVE" "$R2_REMOTE"; then
    log "Step 4: ok"
  else
    log "Failed: rclone copy failed (local archive kept)"
    R2_FAIL=1
  fi
else
  log "Step 4: skipped (R2_REMOTE empty)"
fi

# --- 5. 本地 7 天滚动清理 ---
log "Step 5: pruning data_*.tar.gz older than $RETENTION_DAYS days in $BACKUP_ROOT"
if ! find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'data_*.tar.gz' -mtime "+$RETENTION_DAYS" -delete; then
  log "Failed: prune failed"
  exit 1
fi

if [[ "$R2_FAIL" -ne 0 ]]; then
  log "Backup finished with R2 push failure"
  exit 2
fi
log "Backup complete"
exit 0
