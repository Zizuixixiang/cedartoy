#!/usr/bin/env bash
# cedartoy 每日备份：sqlite 一致性快照 + JSON 存档打包 + R2 推送 + 本地最近 3 份日期归档。
# 由 /etc/cron.d/cedartoy-backup 于每日 3:50 调用（赶在 4:00 guest 清理之前）。
#
# 归档布局（相对 /opt/cedartoy）：
#   data/...                              JSON 存档等原样打包，其中 *.db 为一致性快照
#   turtle-soup/backend/turtle_soup.db    快照（生产库，位于 data 之外）
#   toy-platform/toy_accounts.db          快照（生产库，位于 data 之外）
#   vendor/duel/data/duel.db               快照（双弈生产库，位于 data 之外）
#
# 生产数据只读：所有 sqlite 库先用 .backup 导出到临时 staging 目录再打包。
#
# 可用环境变量覆盖（自测用）：
#   BACKUP_ROOT、R2_REMOTE（置空跳过推送及清理）、RETENTION_COUNT、RETRY_DELAY_SECONDS

set -uo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

APP_ROOT="${BACKUP_APP_ROOT:-/opt/cedartoy}"
DATA_DIR="$APP_ROOT/data"
BACKUP_ROOT="${BACKUP_ROOT:-/home/backups/cedartoy}"
# 复用 cedarclio 的 rclone 凭证；token 仅授权 cedarclio-backup 桶，故用独立前缀隔离
R2_REMOTE="${R2_REMOTE-cloudflare_r2:cedarclio-backup/cedartoy-backups}"
RETENTION_COUNT="${RETENTION_COUNT:-3}"
MAX_ATTEMPTS=3
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-300}"
RCLONE_BIN="/snap/bin/rclone"

# data 之外的生产 sqlite 库（相对 APP_ROOT）
EXTRA_DBS=(
  "turtle-soup/backend/turtle_soup.db"
  "toy-platform/toy_accounts.db"
  "vendor/duel/data/duel.db"
)

DATE_STR="$(date +%Y%m%d)"
ARCHIVE="$BACKUP_ROOT/data_${DATE_STR}.tar.gz"

STAGE=""
cleanup_stage() {
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then
    rm -rf "$STAGE"
  fi
  STAGE=""
}
trap cleanup_stage EXIT

# 仅按文件名日期清理已在 R2 核对同名、同大小的成功归档；异常包和手工快照保留。
prune_local_archives() {
  python3 - "$BACKUP_ROOT" "$RETENTION_COUNT" "$RCLONE_BIN" "$R2_REMOTE" "$ARCHIVE" <<'PY'
import datetime
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

root, count, rclone, remote, current = sys.argv[1:]
if not re.fullmatch(r"[1-9][0-9]*", count):
    sys.exit("Invalid RETENTION_COUNT: expected a positive integer; no archives deleted")
objects = json.loads(subprocess.check_output(
    [rclone, "lsjson", remote, "--files-only", "--max-depth", "1"], text=True
))
remote_sizes = {obj["Path"]: obj["Size"] for obj in objects if not obj["IsDir"]}
verified = []
for path in Path(root).iterdir():
    match = re.fullmatch(r"data_([0-9]{8})\.tar\.gz", path.name)
    if not match:
        continue
    try:
        datetime.datetime.strptime(match[1], "%Y%m%d")
    except ValueError:
        continue
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        continue
    if info.st_size > 0 and remote_sizes.get(path.name) == info.st_size:
        verified.append((path, info))
    else:
        print(f"Keep unverified local archive: {path.name}")
if Path(current) not in [path for path, _ in verified]:
    sys.exit("Current archive not verified in R2; no archives deleted")
verified.sort(key=lambda item: item[0].name, reverse=True)
for path, info in verified[int(count):]:
    if path.lstat() != info:
        sys.exit(f"Archive changed during verification: {path.name}; stopping prune")
    path.unlink()
    print(f"Pruned verified local archive: {path.name} ({info.st_size} bytes)")
PY
}

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

backup_once() {
  local attempt="$1"
  local source_tar_status dest_tar_status
  local snap_fail=0
  local r2_fail=0
  local db rel top
  local -a copy_status
  local -a stage_tops=()

  cleanup_stage
  STAGE="$(mktemp -d /tmp/cedartoy_backup.XXXXXX)" || {
    log "Failed: mktemp"
    return 1
  }

  log "Starting backup attempt $attempt/$MAX_ATTEMPTS (stage=$STAGE, archive=$ARCHIVE)"

  # --- 1. 复制 data（排除 sqlite 主库及 -wal/-shm，稍后以快照替代；.bak-* 历史文件原样保留） ---
  log "Step 1: copying $DATA_DIR (excluding live sqlite files)"
  tar -C "$APP_ROOT" \
      --warning=no-file-changed \
      --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
      --exclude='data/vendor_saves/*/*/.view/*.html' \
      --exclude='data/autochess_rooms/*.pending' --exclude='data/autochess_rooms/.cedartoy.lock' \
      -cf - data | tar -C "$STAGE" -xf -
  copy_status=("${PIPESTATUS[@]}")
  source_tar_status="${copy_status[0]}"
  dest_tar_status="${copy_status[1]}"

  # GNU tar create returns 1 when a live file changes while being read. The
  # staged copy is still usable; status 2 is a real error and must be retried.
  if [[ "$dest_tar_status" -ne 0 || "$source_tar_status" -gt 1 ]]; then
    log "Failed: staging copy of data/ (source tar=$source_tar_status, destination tar=$dest_tar_status)"
    return 1
  fi
  if [[ "$source_tar_status" -eq 1 ]]; then
    log "Step 1: completed with live-file changes (tar status 1 tolerated)"
  fi

  # --- 2. sqlite 一致性快照（.backup 对 WAL 库安全，sessions.db 已启用 WAL） ---
  log "Step 2: snapshotting sqlite databases"
  while IFS= read -r -d '' db; do
    snapshot_db "$db" "${db#"$APP_ROOT"/}" || snap_fail=1
  done < <(find "$DATA_DIR" -type f -name '*.db' -print0)

  for rel in "${EXTRA_DBS[@]}"; do
    if [[ -f "$APP_ROOT/$rel" ]]; then
      snapshot_db "$APP_ROOT/$rel" "$rel" || snap_fail=1
    else
      log "  warning: expected db missing: $APP_ROOT/$rel"
      snap_fail=1
    fi
  done

  if [[ "$snap_fail" -ne 0 ]]; then
    log "Failed: one or more sqlite snapshots failed"
    return 1
  fi

  # AutoChess: JSON rooms are copied BEFORE the wallet snapshot. A copied paid
  # result therefore cannot be newer than its wallet receipt. Keep both together.
  if ! python3 - "$APP_ROOT" "$STAGE" <<'PY_CHECK'
import json, re, sqlite3, sys
from pathlib import Path
source, stage = map(Path, sys.argv[1:])
rooms = stage / 'data/autochess_rooms'
for room in rooms.glob('*.json'):
    if re.fullmatch(r'[a-f0-9]{12}\.json', room.name):
        if json.loads(room.read_text()).get('version') != 1:
            raise ValueError('Unsupported AutoChess room snapshot')
wallet = Path('data/autochess_rooms/economy/accounts.db')
if (source / wallet).is_file():
    with sqlite3.connect((stage / wallet).as_uri() + '?mode=ro', uri=True) as db:
        for (body,) in db.execute('SELECT body FROM state'):
            if json.loads(body).get('version') != 1:
                raise ValueError('Unsupported AutoChess wallet snapshot')
        db.execute('SELECT COUNT(*) FROM receipts').fetchone()
PY_CHECK
  then
    log "Failed: AutoChess rooms/wallet snapshot validation"
    return 1
  fi

  # --- 3. 打包（先写 .tmp 再原子改名，避免半截归档被当成有效备份） ---
  log "Step 3: creating archive"
  mkdir -p "$BACKUP_ROOT" || {
    log "Failed: cannot create $BACKUP_ROOT"
    return 1
  }
  for top in data turtle-soup toy-platform vendor; do
    [[ -d "$STAGE/$top" ]] && stage_tops+=("$top")
  done
  if ! tar -zcf "$ARCHIVE.tmp" -C "$STAGE" "${stage_tops[@]}"; then
    log "Failed: tar failed"
    rm -f "$ARCHIVE.tmp"
    return 1
  fi
  if ! mv -f "$ARCHIVE.tmp" "$ARCHIVE"; then
    log "Failed: rename archive"
    return 1
  fi
  log "Step 3: ok ($(du -h "$ARCHIVE" | cut -f1))"

  # --- 4. 推送 R2（失败保留全部本地归档，禁止滚动清理） ---
  if [[ -n "$R2_REMOTE" ]]; then
    log "Step 4: pushing archive to $R2_REMOTE"
    if "$RCLONE_BIN" copy "$ARCHIVE" "$R2_REMOTE"; then
      log "Step 4: ok"
    else
      log "Failed: rclone copy failed (local archive kept)"
      r2_fail=1
    fi
  else
    log "Step 4: skipped (R2_REMOTE empty)"
  fi

  if [[ "$r2_fail" -ne 0 ]]; then
    log "Backup attempt finished with R2 push failure; local pruning skipped"
    return 2
  fi

  # --- 5. R2 验证后按日期保留最近 N 份；不动远端、临时包或手工快照 ---
  if [[ -n "$R2_REMOTE" ]]; then
    log "Step 5: keeping latest $RETENTION_COUNT verified date archives in $BACKUP_ROOT"
    if ! prune_local_archives; then
      log "Failed: archive verification or prune failed"
      return 1
    fi
  else
    log "Step 5: skipped (R2_REMOTE empty; all local archives kept)"
  fi

  cleanup_stage
  log "Backup complete"
  return 0
}

attempt=1
while (( attempt <= MAX_ATTEMPTS )); do
  if backup_once "$attempt"; then
    exit 0
  else
    status=$?
  fi

  cleanup_stage
  log "Backup attempt $attempt/$MAX_ATTEMPTS failed (status=$status)"
  if (( attempt == MAX_ATTEMPTS )); then
    log "================ BACKUP FAILED AFTER 3 ATTEMPTS ================"
    exit "$status"
  fi

  log "Retrying in $RETRY_DELAY_SECONDS seconds"
  sleep "$RETRY_DELAY_SECONDS"
  ((attempt++))
done
