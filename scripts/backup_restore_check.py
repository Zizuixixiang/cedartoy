#!/usr/bin/env python3
"""cedartoy 备份恢复演练：解包最新备份并验证可恢复性。

流程：
  1. 找到 /home/backups/cedartoy 下最新的 data_YYYYMMDD.tar.gz
  2. 解包到临时目录（拒绝绝对路径 / .. 越界成员）
  3. 对包内所有 .db 跑 PRAGMA integrity_check
  4. 抽查关键路径：
     - sessions.db 内 eco_sessions（eco 存档）非空
     - data/vendor_saves 的游戏子目录与线上一致（线上只读对照）
     - progress.json / save.json 抽样可被 json 解析
  5. 新布局附加库（turtle-soup、toy-platform 快照）缺失时记 WARN
     （2026-07-17 之前的旧格式归档没有这两个快照）

输出 PASS / PASS_WITH_WARNINGS / FAIL；退出码 0 = 通过（含警告），1 = 失败。
由 /etc/cron.d/cedartoy-backup-check 每周一 4:30 调用。
"""

import argparse
import json
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path("/home/backups/cedartoy")
LIVE_VENDOR_SAVES = Path("/opt/cedartoy/data/vendor_saves")
# 新布局中应存在的 data 之外生产库快照（旧归档缺失时仅告警）
OPTIONAL_DBS = [
    "turtle-soup/backend/turtle_soup.db",
    "toy-platform/toy_accounts.db",
]
JSON_SAMPLE_LIMIT = 20

failures = []
warnings = []


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fail(msg):
    failures.append(msg)
    log(f"FAIL-ITEM: {msg}")


def warn(msg):
    warnings.append(msg)
    log(f"WARN: {msg}")


def find_latest_archive(backup_dir):
    archives = sorted(backup_dir.glob("data_*.tar.gz"))
    return archives[-1] if archives else None


def safe_extract(archive, dest):
    """解包并拒绝绝对路径 / 越界成员（Python 3.10 无 tarfile filter 参数）。"""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = (dest / member.name).resolve()
            if not member_path.is_relative_to(dest.resolve()):
                raise RuntimeError(f"归档成员越界: {member.name}")
        tar.extractall(dest)


def check_db_integrity(db_path, rel):
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            rows = con.execute("PRAGMA integrity_check;").fetchall()
        finally:
            con.close()
        if rows == [("ok",)]:
            log(f"  integrity ok: {rel}")
            return True
        fail(f"{rel} integrity_check 异常: {rows[:5]}")
    except sqlite3.Error as e:
        fail(f"{rel} 无法打开或校验: {e}")
    return False


def check_eco_sessions(sessions_db):
    try:
        con = sqlite3.connect(f"file:{sessions_db}?mode=ro&immutable=1", uri=True)
        try:
            count = con.execute("SELECT count(*) FROM eco_sessions").fetchone()[0]
        finally:
            con.close()
        if count > 0:
            log(f"  eco 存档 ok: eco_sessions {count} 行")
        else:
            fail("sessions.db 的 eco_sessions 表为空")
    except sqlite3.Error as e:
        fail(f"sessions.db 读取 eco_sessions 失败: {e}")


def check_vendor_saves(extracted_vendor):
    if not extracted_vendor.is_dir():
        fail("归档内缺少 data/vendor_saves 目录")
        return
    archived_games = {p.name for p in extracted_vendor.iterdir() if p.is_dir()}
    log(f"  vendor_saves 游戏目录: {sorted(archived_games)}")
    if LIVE_VENDOR_SAVES.is_dir():
        live_games = {p.name for p in LIVE_VENDOR_SAVES.iterdir() if p.is_dir()}
        missing = live_games - archived_games
        if missing:
            # 备份时间点之后新增的游戏不算失败，但线上已有而归档缺失需要人看
            fail(f"vendor_saves 缺少线上已有的游戏目录: {sorted(missing)}")
    else:
        warn("线上 vendor_saves 不可读，跳过目录对照")


def check_json_parsable(extracted_vendor):
    if not extracted_vendor.is_dir():
        return
    samples = []
    for name in ("progress.json", "save.json"):
        samples.extend(sorted(extracted_vendor.rglob(name))[: JSON_SAMPLE_LIMIT // 2])
    if not samples:
        fail("vendor_saves 内未找到任何 progress.json / save.json")
        return
    bad = 0
    for p in samples[:JSON_SAMPLE_LIMIT]:
        try:
            with open(p, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, OSError) as e:
            bad += 1
            fail(f"JSON 解析失败: {p.relative_to(extracted_vendor)}: {e}")
    log(f"  progress/save 抽样 {len(samples[:JSON_SAMPLE_LIMIT])} 个，解析失败 {bad} 个")


def main():
    parser = argparse.ArgumentParser(description="cedartoy 备份恢复演练")
    parser.add_argument("--archive", type=Path, default=None,
                        help="指定归档路径（默认取备份目录中最新的 data_*.tar.gz）")
    parser.add_argument("--backup-dir", type=Path, default=BACKUP_DIR)
    args = parser.parse_args()

    archive = args.archive or find_latest_archive(args.backup_dir)
    if archive is None or not archive.is_file():
        log(f"RESULT: FAIL — 找不到备份归档（目录 {args.backup_dir}）")
        return 1
    log(f"演练目标: {archive} ({archive.stat().st_size} bytes)")

    with tempfile.TemporaryDirectory(prefix="cedartoy_restore_") as tmp:
        dest = Path(tmp)
        try:
            safe_extract(archive, dest)
        except (tarfile.TarError, RuntimeError, OSError) as e:
            log(f"RESULT: FAIL — 解包失败: {e}")
            return 1
        log("解包完成")

        # 1) 所有 .db 完整性
        dbs = sorted(dest.rglob("*.db"))
        if not dbs:
            fail("归档内没有任何 .db 文件")
        for db in dbs:
            check_db_integrity(db, db.relative_to(dest))

        # 2) 关键库存在性
        sessions_db = dest / "data" / "sessions.db"
        if sessions_db.is_file():
            check_eco_sessions(sessions_db)
        else:
            fail("归档内缺少 data/sessions.db")
        for rel in OPTIONAL_DBS:
            if not (dest / rel).is_file():
                warn(f"归档缺少 {rel}（2026-07-17 前的旧格式归档属正常）")

        # 3) vendor_saves 结构 + progress 类文件可解析
        extracted_vendor = dest / "data" / "vendor_saves"
        check_vendor_saves(extracted_vendor)
        check_json_parsable(extracted_vendor)

    if failures:
        log(f"RESULT: FAIL — {len(failures)} 项失败, {len(warnings)} 项警告")
        return 1
    if warnings:
        log(f"RESULT: PASS_WITH_WARNINGS — {len(warnings)} 项警告")
        return 0
    log("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
