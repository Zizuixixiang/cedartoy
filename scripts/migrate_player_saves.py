#!/usr/bin/env python3
"""管理员存档迁移：把指定旧 player_id 名下的存档改绑到指定 user_id。

用途：游客隔离改造前的裸 id 老档（如 eco 的 clio/clioweb/sirius），无认领码，
由管理员手动绑定到对应账号。player_id 迁为 str(user_id)，并回填 user_id 列；
冲突时整体报错不覆盖，任何情况下不删档。

用法：
    python3 scripts/migrate_player_saves.py <old_player_id> <user_id> [--dry-run]

示例（隔离改造时的既定映射）：
    python3 scripts/migrate_player_saves.py clio 2
    python3 scripts/migrate_player_saves.py clioweb 8
    python3 scripts/migrate_player_saves.py sirius 19
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_player_id", help="旧存档的 player_id（裸 id 或 guest: 前缀 id，原样匹配）")
    parser.add_argument("user_id", type=int, help="目标账号 user_id（toy_users.id）")
    parser.add_argument("--dry-run", action="store_true", help="只列出会迁移的存档和冲突，不执行")
    args = parser.parse_args()

    with server._db_connect() as conn:
        user = server._row_dict(conn.execute(
            "SELECT id, username FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (args.user_id,),
        ).fetchone())
    if not user:
        print(f"错误：user_id={args.user_id} 不存在或已删除", file=sys.stderr)
        return 1

    target = str(args.user_id)
    found, conflicts = server._collect_player_saves(args.old_player_id, target)
    print(f"目标账号：#{user['id']} {user['username']}")
    print(f"找到 {args.old_player_id} 名下存档：")
    print(json.dumps(found, ensure_ascii=False, indent=2) if found else "  （无）")
    if conflicts:
        print("冲突（账号名下已有同游戏存档，不会覆盖）：", "、".join(conflicts))
    if args.dry_run:
        print("dry-run，未执行迁移。")
        return 0
    if not found:
        print("没有可迁移的存档，退出。")
        return 1

    try:
        result = server._migrate_player_saves(args.old_player_id, args.user_id)
    except server._McpError as exc:
        print(f"迁移失败：{exc.message}", file=sys.stderr)
        return 1
    print("迁移完成：", json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
