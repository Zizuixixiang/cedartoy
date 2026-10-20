#!/usr/bin/env python3
"""One preserved, zero-stake Rummikub review for 南杉; dry-run by default.

Run with vendor/duel/.venv/bin/python. No HTTP, credentials, wallet writes,
create_room, result settlement, achievements or notifications. Production apply
requires explicit paths, account confirmation and a SQLite consistency backup.
The main assistant must review/deploy the terminal UI + history exclusion first.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "duel"))
from app import database, framework
from app.games.rummikub import Rummikub, TILES

USERNAME = "南杉"
USER_ID = 1
ROOM_ID = "RKDEMO01"
MARKER = "rummikub_review_demo"
NOTICE = "拉密终局复盘演示 · 规则引擎生成的样例，非真实战绩；零筹码，不计排行榜与成就。"


def read_account(path: Path, username: str, user_id: int) -> dict:
    if username != USERNAME or user_id != USER_ID:
        raise ValueError("仅允许已确认的南杉 / id 1")
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        # Deliberately never SELECT *: no account token/password is read.
        rows = conn.execute(
            "SELECT id, username, is_ai FROM toy_users WHERE id=? OR username=?",
            (user_id, username),
        ).fetchall()
    if rows != [(USER_ID, USERNAME, 0)]:
        raise ValueError("账号只读核验失败，必须唯一匹配 username 南杉 / id 1 / is_ai 0")
    return {"id": USER_ID, "username": USERNAME, "is_ai": 0}


def build_demo() -> dict:
    participants = [{
        "player_id": "1" if seat == 0 else f"npc:rummikub-review:{ROOM_ID}:{seat}",
        "display_name": USERNAME if seat == 0 else f"演示牌友{seat}",
        "role": "human" if seat == 0 else "ai",
        "participant_kind": "human" if seat == 0 else "system_npc",
        "npc_persona_id": None if seat == 0 else f"rummikub-review-{seat}",
        "seat_index": seat, "token": f"P{seat + 1}",
        "join_status": "joined", "activity_state": "active", "active": True,
    } for seat in range(4)]
    # Fixed seed: 89 fully validated turns, blocked with four nonempty racks.
    game = Rummikub(random.Random(0))
    state = game.initialize(participants)
    for revision in range(1, 501):
        actor = next(p for p in participants if p["player_id"] == state["turn_player_id"])
        move = game.choose_local_npc_action(state, actor, participants)
        state = game.apply_action(state, move, actor).state
        entities = state["pool"] + sum(state["hands"].values(), []) + sum(state["melds"], [])
        if Counter(entities) != Counter(TILES.keys()):
            raise ValueError("106 张实体牌守恒失败")
        if state["result"]:
            break
    else:
        raise ValueError("规则引擎未在限制内终局")
    if state["result"]["reason"] != "blocked" or not all(state["hands"].values()):
        raise ValueError("样例必须是四家有剩牌的真实堵局")
    result = deepcopy(state["result"])
    result.update(demo=True, result_text=NOTICE)
    state["result"] = deepcopy(result)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    winner_id = result["winner_player_id"]
    return {
        "room_id": ROOM_ID, "game_type": "rummikub", "mode": "human_first",
        "board_state": state, "turn": "human", "current_player_id": None,
        "revision": revision, "status": "finished",
        "winner": "draw" if result["draw"] else next(p["role"] for p in participants if p["player_id"] == winner_id),
        "winner_player_id": winner_id, "result": result, "stake": 0,
        "initiator_player_id": "1", "preserved": 1, "terminal_reason": MARKER,
        "created_at": now, "updated_at": now, "last_move_at": now, "terminal_at": now,
        "participants": participants,
    }


def validate_projection(room: dict) -> dict:
    view = framework.project_room_for_viewer(room, "1")
    hands = view["board_state"]["terminal_hands"]
    if set(hands) != {p["player_id"] for p in room["participants"]}:
        raise ValueError("终局投影未覆盖四席；请先部署拉密终局钩子")
    if "pool" in view["board_state"] or "hands" in view["board_state"]:
        raise ValueError("原始隐私区泄漏")
    for outsider in ("2", "human:1", "1:2", "guest:rummikub-review"):
        try:
            framework.project_room_for_viewer(room, outsider)
        except framework.DuelError as exc:
            if exc.status_code != 403:
                raise
        else:
            raise ValueError("其他身份可读取演示房")
    return view


def insert_demo(conn: sqlite3.Connection, room: dict) -> dict:
    """Caller owns the transaction. Inserts only; never migrates a real DB."""
    if conn.execute(
        "SELECT 1 FROM rooms WHERE room_id=? OR terminal_reason=?",
        (ROOM_ID, MARKER),
    ).fetchone():
        raise ValueError("拉密演示房已存在或固定房号被占用；拒绝重复/覆盖")
    npcs = [p["player_id"] for p in room["participants"] if p["participant_kind"] == "system_npc"]
    if conn.execute("SELECT 1 FROM room_participants WHERE player_id IN (?,?,?)", npcs).fetchone():
        raise ValueError("演示 NPC ID 已被使用")
    fields = ("room_id", "game_type", "mode", "turn", "current_player_id", "revision",
              "status", "winner", "winner_player_id", "stake", "initiator_player_id",
              "preserved", "terminal_reason", "created_at", "updated_at", "last_move_at", "terminal_at")
    conn.execute(
        f"INSERT INTO rooms ({','.join(fields)}, board_state, result_json) VALUES ({','.join('?' for _ in range(len(fields)+2))})",
        [room[k] for k in fields] + [json.dumps(room["board_state"], ensure_ascii=False),
                                    json.dumps(room["result"], ensure_ascii=False)],
    )
    # Invite metadata prevents the ordinary bound human/AI pair assumptions and
    # excludes any future achievement recording of this demonstration room.
    conn.execute(
        "INSERT INTO room_invites (room_id,invite_code,target_player_count,household_count) VALUES (?,NULL,4,1)",
        (ROOM_ID,),
    )
    for p in room["participants"]:
        conn.execute(
            """INSERT INTO room_participants
               (room_id,player_id,display_name,role,participant_kind,npc_persona_id,
                seat_index,token,join_status,activity_state,active,joined_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ROOM_ID, p["player_id"], p["display_name"], p["role"], p["participant_kind"],
             p["npc_persona_id"], p["seat_index"], p["token"], "joined", "active", 1, room["created_at"]),
        )
    # No move/resign event: achievement backfill must not treat this as a match.
    # This local result label also prevents a later generic result-event backfill.
    cursor = conn.execute(
        """INSERT INTO room_messages
           (room_id,sender,sender_player_id,text,revision_at_send,created_at,event_type)
           VALUES (?,'system','system',?,?,?,'result')""",
        (ROOM_ID, NOTICE, room["revision"], room["terminal_at"]),
    )
    for p in room["participants"]:
        conn.execute(
            "INSERT INTO room_event_cursors (room_id,player_id,last_event_id,updated_at) VALUES (?,?,?,?)",
            (ROOM_ID, p["player_id"], cursor.lastrowid, room["terminal_at"]),
        )
    persisted = database.decode_room(conn.execute("SELECT * FROM rooms WHERE room_id=?", (ROOM_ID,)).fetchone(), conn)
    validate_projection(persisted)
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("外键检查失败")
    return persisted


def create_in_database(path: Path) -> dict:
    # mode=rw refuses accidental empty production database creation.
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        room = build_demo()
        conn.execute("BEGIN IMMEDIATE")
        try:
            persisted = insert_demo(conn, room)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return persisted


def dry_run(export: Path | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="rummikub-review-") as directory:
        previous = database.DB_PATH
        database.DB_PATH = Path(directory) / "duel.db"
        try:
            database.init_db()
            room = create_in_database(database.DB_PATH)
            # Reload is the same path used when opening a historical room.
            room = framework.get_room(ROOM_ID, "human", "1")
            view = validate_projection(room)
            if export:
                export.write_text(json.dumps({"ok": True, "room": view, "timeline": []}, ensure_ascii=False), encoding="utf-8")
            return summary(room, applied=False)
        finally:
            database.DB_PATH = previous


def summary(room: dict, *, applied: bool) -> dict:
    return {"applied": applied, "room_id": ROOM_ID,
            "url": f"/duel/?room={ROOM_ID}" if applied else None,
            "note": NOTICE, "revision": room["revision"], "stake": room["stake"],
            "status": room["status"], "preserved": room["preserved"],
            "remaining_values": room["result"]["remaining_values"],
            "tile_scores": room["result"]["tile_scores"],
            "authorized_player_id": "1", "other_viewers_denied": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--account-db", type=Path)
    parser.add_argument("--duel-db", type=Path)
    parser.add_argument("--confirm-username")
    parser.add_argument("--confirm-user-id", type=int)
    parser.add_argument("--backup", type=Path, help="主助手预先生成的 Duel SQLite 一致性快照")
    parser.add_argument("--export-view", type=Path, help="仅 dry-run：保存脱敏后的浏览器测试夹具")
    args = parser.parse_args()
    if not args.apply:
        if args.duel_db or args.account_db or args.backup or args.confirm_username or args.confirm_user_id is not None:
            parser.error("dry-run 不接受真实数据库/账号参数")
        print(json.dumps(dry_run(args.export_view), ensure_ascii=False, indent=2))
        return
    if args.export_view or not all((args.account_db, args.duel_db, args.backup)):
        parser.error("--apply 必须指定 --account-db --duel-db --backup，且不能导出测试夹具")
    read_account(args.account_db, args.confirm_username, args.confirm_user_id)
    if args.backup.resolve() in (args.duel_db.resolve(), args.account_db.resolve()):
        parser.error("备份必须是独立 SQLite 一致性快照")
    with closing(sqlite3.connect(f"{args.backup.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("备份完整性检查失败")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='room_participants'").fetchone():
            raise ValueError("不是 Duel 备份")
    # The script is an operator entrypoint, never imported by public handlers.
    print(json.dumps(summary(create_in_database(args.duel_db), applied=True), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
