#!/usr/bin/env python3
"""One-shot Mahjong review fixture. Default: retained temporary SQLite preview only.

Run with vendor/duel/.venv/bin/python. Never calls create_room/play_move, writes
move history, or initializes/migrates a production database. See --help and
scripts/create_mahjong_review_demo.md for the separately authorized apply step.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "duel"))
from app import database, framework  # noqa: E402
from app.games.mahjong import Mahjong, build_wall  # noqa: E402

DEMO_REASON = "mahjong_review_demo"
CONFIRMATION = "CREATE_MAHJONG_REVIEW_DEMO_FOR_1"
ALLOWED_TABLES = {"rooms", "room_participants", "room_event_cursors"}


def build_demo(room_id: str) -> tuple[dict, list[dict]]:
    """Allocate physical tiles once, then let the real engine finish a legal hu.

    This is an explicitly constructed review position, not a replay of a game.
    Every player has one meld; the winner has a concealed kong and self-draws.
    """
    players = [dict(
        player_id="1" if seat == 0 else f"npc:mahjong-review:{room_id}:{seat}",
        display_name="南杉" if seat == 0 else f"演示陪练{seat}",
        role="human" if seat == 0 else "ai",
        participant_kind="human" if seat == 0 else "system_npc",
        npc_persona_id=None, seat_index=seat, token=f"seat-{seat}",
        join_status="joined", activity_state="active", active=True,
    ) for seat in range(4)]
    game = Mahjong()
    state = game._empty_state([p["player_id"] for p in players])
    pool = build_wall()

    def take(codes):
        out = []
        for code in codes:
            tile = next(t for t in pool if t["code"] == code)
            pool.remove(tile)
            out.append(tile)
        return out

    meld_specs = [
        ("concealed_gang", ["J2"] * 4, "J2", 0),
        ("peng", ["F2"] * 3, "F2", 1),
        ("chi", ["B1", "B2", "B3"], "B2", 1),
        ("ming_gang", ["T9"] * 4, "T9", 1),
    ]
    for p, (kind, codes, engine_tile, offer) in zip(players, meld_specs):
        state["melds"][p["player_id"]] = [dict(
            kind=kind, tiles=take(codes), engine_tile=engine_tile, offer=offer,
            source_player_id=None if offer == 0 else players[(p["seat_index"] - 1) % 4]["player_id"],
        )]
    state["hands"]["1"] = take(["W2", "W3", "W4", "T2", "T3", "T4", "B2", "B3", "B4", "J1", "J1"])
    for p in players[1:]:
        # Deterministic physical tiles; no other real accounts or stored hands.
        state["hands"][p["player_id"]] = pool[:10]
        del pool[:10]
    state.update(wall=pool, phase="discard", turn_player_id="1",
                 drawn_tile_id=state["hands"]["1"][-1]["id"],
                 draw_context={"replacement": False, "wall_last": False})
    state["flow"]["phase"] = "discard"
    action = next(a for a in game.legal_actions_for(state, "1") if a["kind"] == "hu")
    applied = game.apply_action(state, {"action": "act", "action_id": action["action_id"]}, players[0])
    state = applied.state
    assert state["game_result"]["total_fan"] == sum(f["fan"] for f in state["game_result"]["fans"])
    zones = [*state["wall"], *(t for hand in state["hands"].values() for t in hand),
             *(t for melds in state["melds"].values() for m in melds for t in m["tiles"])]
    assert len(zones) == len({t["id"] for t in zones}) == 136
    assert Counter(t["code"] for t in zones) == Counter(t["code"] for t in build_wall())
    state["last_action_note"] = "演示房 · 人工构造终局，仅供复盘展示，不计真实战绩、筹码或成就。"
    return state, players


def verify_account(path: Path) -> None:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT id, username, is_ai, deleted_at FROM toy_users WHERE id = ?", (1,)
        ).fetchone()
    if row != (1, "南杉", 0, None):
        raise ValueError("目标账号不匹配：必须是未删除的人类账号 南杉 / 1")


def insert_demo(conn: sqlite3.Connection, room_id: str) -> dict:
    """Caller owns the transaction. Only these three INSERT targets are allowed."""
    if conn.execute(
        "SELECT 1 FROM rooms r JOIN room_participants p USING(room_id) "
        "WHERE r.terminal_reason = ? AND p.player_id = '1'", (DEMO_REASON,)
    ).fetchone():
        raise ValueError("该账号已有麻将演示房；拒绝重复生成")
    state, players = build_demo(room_id)
    timestamp = datetime.now(timezone.utc).isoformat()
    result = {**state["game_result"], "demo": True, "result_text": state["last_action_note"]}
    conn.execute(
        """INSERT INTO rooms (room_id, game_type, mode, board_state, turn,
           current_player_id, revision, status, winner, winner_player_id,
           result_json, stake, initiator_player_id, preserved, terminal_at,
           terminal_reason, created_at, updated_at, last_move_at)
           VALUES (?, 'mahjong', 'human_first', ?, 'human', NULL, 0, 'finished',
                   'human', '1', ?, 0, '1', 1, ?, ?, ?, ?, ?)""",
        (room_id, json.dumps(state, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
         timestamp, DEMO_REASON, timestamp, timestamp, timestamp),
    )
    for p in players:
        conn.execute(
            """INSERT INTO room_participants (room_id, player_id, display_name, role,
               participant_kind, npc_persona_id, seat_index, token, join_status,
               activity_state, active, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (room_id, *(p[k] for k in ("player_id", "display_name", "role", "participant_kind",
              "npc_persona_id", "seat_index", "token", "join_status", "activity_state", "active")), timestamp),
        )
        conn.execute("INSERT INTO room_event_cursors (room_id, player_id, updated_at) VALUES (?, ?, ?)",
                     (room_id, p["player_id"], timestamp))
    raw = database.decode_room(conn.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone(), conn)
    projected = framework.project_room_for_viewer(framework._decorate(raw), "1")
    assert projected["status"] == "finished" and projected["stake"] == 0
    assert len(projected["board_state"]["terminal_hands"]) == 4
    assert not projected["private_state"]["legal_actions"]
    return projected


def write_guard(action, table, _column, _db, _trigger):
    # Also prevents side effects from any future database trigger.
    if action == sqlite3.SQLITE_INSERT:
        return sqlite3.SQLITE_OK if table in ALLOWED_TABLES and _trigger is None else sqlite3.SQLITE_DENY
    # SQLite authorizes the legacy trigger even when its WHEN is false.
    # Suppress its UPDATE entirely; all our kinds are already explicit.
    if action == sqlite3.SQLITE_UPDATE and table == "room_participants" and _trigger == "trg_room_participants_infer_legacy_kind":
        return sqlite3.SQLITE_IGNORE
    if action in (sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def create_at(path: Path, room_id: str, backup: Path | None = None) -> dict:
    # mode=rw never silently creates or migrates the target database.
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if backup is not None:
                # Separate read-only source sees a consistent snapshot while we
                # hold the writer lock. Refuse to overwrite any existing file.
                with backup.open("xb"):
                    backup.chmod(0o600)
                with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as source:
                    with sqlite3.connect(backup) as dest:
                        source.backup(dest)
                        if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("备份完整性检查失败")
            conn.set_authorizer(write_guard)
            before = conn.total_changes
            projected = insert_demo(conn, room_id)
            if conn.total_changes - before != 9:
                raise ValueError("写入数量异常：预期仅 1 房间、4 参与者、4 游标")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("外键检查失败")
            conn.commit()
            return projected
        except BaseException:
            conn.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, choices=["南杉"])
    parser.add_argument("--account-id", required=True, type=int, choices=[1])
    parser.add_argument("--apply", action="store_true", help="生产写入；仅主助手另行验收、部署、获得执行指令后使用")
    parser.add_argument("--confirm", help=f"apply 必须逐字填写 {CONFIRMATION}")
    parser.add_argument("--duel-db", type=Path, help="apply 必须显式指定实际服务数据库")
    parser.add_argument("--account-db", type=Path, help="apply 只读核验账号的实际主库")
    parser.add_argument("--backup", type=Path, help="apply 必须指定尚不存在的 SQLite 一致性备份文件")
    args = parser.parse_args(argv)
    room_id = "MJ" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    if args.apply:
        if args.confirm != CONFIRMATION or not all((args.duel_db, args.account_db, args.backup)):
            parser.error("apply 需要 --confirm、--duel-db、--account-db、--backup")
        if len({p.resolve() for p in (args.duel_db, args.account_db, args.backup)}) != 3:
            parser.error("数据库与备份路径必须互不相同")
        verify_account(args.account_db)
        target = args.duel_db
        projected = create_at(target, room_id, args.backup)
        preview_path = None
    else:
        if any((args.confirm, args.duel_db, args.account_db, args.backup)):
            parser.error("dry-run 不接受生产路径/确认参数；只创建临时预览")
        preview_dir = Path(tempfile.mkdtemp(prefix="mahjong-review-demo-"))
        target = preview_dir / "preview.db"
        old_path = database.DB_PATH
        try:
            database.DB_PATH = target
            database.init_db()
            projected = create_at(target, room_id)
        finally:
            database.DB_PATH = old_path
        preview_path = preview_dir / "human-projection.json"
        preview_path.write_text(json.dumps(projected, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(mode="apply" if args.apply else "dry-run", database=str(target),
                         room_id=room_id, route=f"/duel/?room={room_id}",
                         preview=str(preview_path) if preview_path else None,
                         production_link_available=bool(args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
