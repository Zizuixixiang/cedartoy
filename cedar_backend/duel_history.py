"""Duel history queries and formatting; dependencies are supplied by the caller.

No database paths or server callbacks are captured at import time.
"""

import json
import sqlite3


def _duel_history_outcome(room, result, player_id, player_role):
    """Return one subject-relative outcome from Duel's real terminal payload."""
    status = room.get("status")
    if status in {"pending", "waiting", "playing"}:
        return status
    if room.get("terminal_reason") == "stale_archive":
        return "archived"

    outcomes = result.get("outcomes_by_player")
    if isinstance(outcomes, dict):
        own = outcomes.get(player_id)
        if isinstance(own, dict):
            outcome = own.get("outcome")
            if outcome in {"win", "loss"}:
                return outcome
            if outcome == "push":
                return "draw"

    tied_ids = result.get("tied_player_ids")
    if isinstance(tied_ids, list) and tied_ids:
        return "draw" if player_id in map(str, tied_ids) else "loss"

    winner_ids = set()
    for key in ("winner_player_ids", "winning_player_ids"):
        values = result.get(key)
        if isinstance(values, list):
            winner_ids.update(str(value) for value in values)
    if winner_ids:
        return "win" if player_id in winner_ids else "loss"

    winner_player_id = room.get("winner_player_id") or result.get("winner_player_id")
    if winner_player_id not in {None, ""}:
        return "win" if player_id == str(winner_player_id) else "loss"

    resigned_player_id = result.get("resigned_player_id")
    if resigned_player_id not in {None, ""} and player_id == str(resigned_player_id):
        return "loss"

    legacy_winner = room.get("winner")
    if legacy_winner in {"human", "ai"}:
        return "win" if player_role == legacy_winner else "loss"
    if legacy_winner == "draw" or result.get("draw") is True:
        return "draw"
    return "archived" if status == "archived" else "finished"


def _duel_history_result_detail(result, player_id):
    outcomes = result.get("outcomes_by_player")
    if isinstance(outcomes, dict):
        own = outcomes.get(player_id)
        if isinstance(own, dict) and isinstance(own.get("result_text"), str):
            return " ".join(own["result_text"].split())[:160]
    if isinstance(result.get("result_text"), str):
        return " ".join(result["result_text"].split())[:160]
    return ""


def _duel_history_for_user(
    user, *,
    limit,
    duel_db_path,
    logger,
    history_outcome,
    history_result_detail,
):
    """Read compact Duel room history for exactly one account identity."""
    empty = {"available": False, "total": 0, "limit": limit, "matches": []}
    if not duel_db_path.is_file():
        return empty
    player_id = str(int(user["id"]))
    player_role = "ai" if user.get("is_ai") else "human"
    participant_kind = "bound_machine" if user.get("is_ai") else "human"
    try:
        conn = sqlite3.connect(
            f"{duel_db_path.resolve().as_uri()}?mode=ro", uri=True
        )
        conn.row_factory = sqlite3.Row
        try:
            args = (player_id, player_role, participant_kind)
            total = conn.execute(
                """
                SELECT COUNT(*)
                FROM rooms AS room
                JOIN room_participants AS subject
                  ON subject.room_id = room.room_id
                 AND subject.player_id = ?
                 AND subject.role = ?
                 AND subject.participant_kind = ?
                 AND subject.join_status IN ('joined', 'left')
                """,
                args,
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT room.room_id, room.game_type, room.status, room.winner,
                       room.winner_player_id, room.result_json,
                       room.terminal_reason, room.created_at, room.updated_at,
                       room.terminal_at, subject.role AS subject_role
                FROM rooms AS room
                JOIN room_participants AS subject
                  ON subject.room_id = room.room_id
                 AND subject.player_id = ?
                 AND subject.role = ?
                 AND subject.participant_kind = ?
                 AND subject.join_status IN ('joined', 'left')
                ORDER BY COALESCE(
                    room.terminal_at, room.updated_at, room.created_at
                ) DESC, room.room_id DESC
                LIMIT ?
                """,
                (*args, limit),
            ).fetchall()
            room_ids = [row["room_id"] for row in rows]
            participants_by_room = {room_id: [] for room_id in room_ids}
            if room_ids:
                placeholders = ",".join("?" for _ in room_ids)
                participant_rows = conn.execute(
                    f"""
                    SELECT room_id, player_id, display_name, role,
                           participant_kind, seat_index
                    FROM room_participants
                    WHERE room_id IN ({placeholders})
                    ORDER BY room_id, seat_index
                    """,
                    room_ids,
                ).fetchall()
                for participant in participant_rows:
                    participants_by_room[participant["room_id"]].append(
                        {
                            "display_name": (
                                participant["display_name"]
                                or participant["player_id"]
                            ),
                            "role": participant["role"],
                            "kind": participant["participant_kind"],
                            "seat": participant["seat_index"],
                            "is_self": participant["player_id"] == player_id,
                        }
                    )
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        logger.warning("duel history unavailable for player %s: %s", player_id, exc)
        return empty

    matches = []
    for raw_row in rows:
        row = dict(raw_row)
        try:
            parsed_result = json.loads(row.get("result_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            parsed_result = {}
        result = parsed_result if isinstance(parsed_result, dict) else {}
        participants = participants_by_room.get(row["room_id"], [])
        matches.append({
            "room_id": row["room_id"],
            "game_type": row["game_type"],
            "status": row["status"],
            "outcome": history_outcome(
                row, result, player_id, row["subject_role"]
            ),
            "result_detail": history_result_detail(result, player_id),
            "time": (
                row.get("terminal_at")
                or row.get("updated_at")
                or row.get("created_at")
            ),
            "participants": participants,
            "opponents": [
                participant["display_name"]
                for participant in participants
                if not participant["is_self"]
            ],
        })
    return {
        "available": True,
        "total": int(total),
        "limit": limit,
        "matches": matches,
    }
