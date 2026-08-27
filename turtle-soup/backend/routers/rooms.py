from fastapi import APIRouter, Depends, HTTPException

from auth_utils import current_player
from database import execute, fetch_all, fetch_one, get_setting
from judge import public_answer_from_full_answer, scan_text
from models import RoomCreateBody
from utils import ANSWER_LIMIT, SURFACE_LIMIT, TITLE_LIMIT, SQL_NOW, clean_content, public_player, room_id

router = APIRouter(prefix="/rooms", tags=["rooms"])

# Legacy product entitlement originally keyed by the display name "nanshan".
# Keep the stable unified-account id so changing that username cannot remove it.
NANSHAN_TOY_USER_ID = 118


def _public_room(row: dict) -> dict:
    out = {k: row[k] for k in row.keys() if k != "answer"}
    return out


def _history_subject_has_data(subject: dict) -> bool:
    stats = subject.get("stats") or {}
    return bool(subject.get("rooms")) or any(
        int(stats.get(field) or 0) > 0
        for field in ("total_games", "win_count", "ask_count")
    )


async def _history_subject(
    *,
    subject_id: str,
    label: str,
    username: str,
    toy_user_id: int | None = None,
    fallback_player_id: int | None = None,
) -> dict:
    if toy_user_id is not None:
        players = await fetch_all(
            """
            SELECT id, ask_count, ask_count_y, ask_count_n, ask_count_u, ask_count_p,
                   win_count, game_count
            FROM players
            WHERE user_id = ?
            """,
            (toy_user_id,),
        )
    elif fallback_player_id is not None:
        row = await fetch_one(
            """
            SELECT id, ask_count, ask_count_y, ask_count_n, ask_count_u, ask_count_p,
                   win_count, game_count
            FROM players
            WHERE id = ?
            """,
            (fallback_player_id,),
        )
        players = [row] if row else []
    else:
        players = []

    stat_fields = (
        "ask_count", "ask_count_y", "ask_count_n", "ask_count_u", "ask_count_p",
        "win_count", "game_count",
    )
    stats = {field: sum(int(player.get(field) or 0) for player in players) for field in stat_fields}
    player_ids = [int(player["id"]) for player in players]
    rooms = []
    if player_ids:
        placeholders = ",".join("?" for _ in player_ids)
        rooms = await fetch_all(
            f"""
            SELECT r.id,
                   COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
                   r.surface, r.status, r.created_at, r.finished_at,
                   CASE WHEN r.created_by IN ({placeholders}) THEN 1 ELSE 0 END AS is_creator,
                   CASE WHEN r.winner_id IN ({placeholders}) THEN 1 ELSE 0 END AS is_winner,
                   winner.username AS winner_name,
                   (SELECT COUNT(*) FROM game_logs own_ask
                    WHERE own_ask.room_id = r.id
                      AND own_ask.player_id IN ({placeholders})
                      AND own_ask.type = 'ask') AS ask_count,
                   (SELECT COUNT(*) FROM game_logs own_guess
                    WHERE own_guess.room_id = r.id
                      AND own_guess.player_id IN ({placeholders})
                      AND own_guess.type = 'guess') AS guess_count,
                   (SELECT COUNT(DISTINCT participants.player_id) FROM game_logs participants
                    WHERE participants.room_id = r.id
                      AND participants.player_id IS NOT NULL) AS participant_count,
                   COALESCE(
                       (SELECT MAX(activity.created_at) FROM game_logs activity WHERE activity.room_id = r.id),
                       r.finished_at,
                       r.created_at
                   ) AS last_active_at
            FROM rooms r
            LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
            LEFT JOIN players winner ON winner.id = r.winner_id
            WHERE r.created_by IN ({placeholders})
               OR EXISTS (
                   SELECT 1 FROM game_logs mine
                   WHERE mine.room_id = r.id AND mine.player_id IN ({placeholders})
               )
            ORDER BY last_active_at DESC, r.created_at DESC
            LIMIT 30
            """,
            (*player_ids, *player_ids, *player_ids, *player_ids, *player_ids, *player_ids),
        )

    # game_count 是跨房间清理长期保留的累计数；当前仍保留的房间可能包含尚未结算的对局。
    stats["total_games"] = max(stats["game_count"], len(rooms))
    return {
        "id": subject_id,
        "label": label,
        "username": username,
        "stats": stats,
        "rooms": rooms,
    }


@router.get("/history")
async def history(player: dict = Depends(current_player)):
    if player.get("is_guest"):
        raise HTTPException(status_code=401, detail="请先登录再查看历史")

    toy_user_id = int(player["user_id"]) if player.get("user_id") is not None else None
    subjects = [await _history_subject(
        subject_id="self",
        label="我",
        username=player.get("username") or "我",
        toy_user_id=toy_user_id,
        fallback_player_id=int(player["id"]),
    )]
    if toy_user_id is not None and not player.get("is_ai"):
        machines = await fetch_all(
            """
            SELECT ai.id, ai.username
            FROM user_bindings binding
            JOIN toy_users ai ON ai.id = binding.ai_user_id
            WHERE binding.human_user_id = ?
              AND ai.deleted_at IS NULL
            ORDER BY binding.created_at DESC
            """,
            (toy_user_id,),
        )
        for machine in machines:
            subjects.append(await _history_subject(
                subject_id=f"machine-{machine['id']}",
                label=machine["username"],
                username=machine["username"],
                toy_user_id=int(machine["id"]),
            ))
    return {
        "subjects": [subject for subject in subjects if _history_subject_has_data(subject)],
    }


@router.get("/")
async def list_rooms(player: dict = Depends(current_player)):
    del player
    finished_visible_hours = int(await get_setting("lobby_finished_visible_hours", "3"))
    rows = await fetch_all(
        """
        SELECT r.id, r.surface, r.status, r.created_by, r.winner_id, r.created_at, r.finished_at,
               COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
               COALESCE(pz.tags, '') AS tags,
               p.username AS creator_name,
               (SELECT COUNT(*) FROM game_logs gl WHERE gl.room_id = r.id AND gl.type = 'ask') AS ask_count,
               (SELECT COUNT(*) FROM room_presence rp
                WHERE rp.room_id = r.id
                  AND rp.last_active_at > datetime('now', 'localtime', '-1 hour')) AS active_players,
               (SELECT MAX(gl2.created_at) FROM game_logs gl2
                WHERE gl2.room_id = r.id) AS last_active_at
        FROM rooms r
        LEFT JOIN players p ON p.id = r.created_by
        LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
        WHERE r.status IN ('waiting', 'playing')
           OR (
             r.status = 'finished'
             AND r.finished_at IS NOT NULL
             AND r.finished_at >= datetime('now', 'localtime', ?)
           )
        ORDER BY CASE r.status WHEN 'finished' THEN 2 ELSE 0 END, COALESCE((SELECT MAX(gl.created_at) FROM game_logs gl WHERE gl.room_id = r.id), r.created_at) DESC
        LIMIT 100
        """,
        (f"-{finished_visible_hours} hours",),
    )
    return rows


@router.post("/create")
async def create_room(body: RoomCreateBody, player: dict = Depends(current_player)):
    unlimited_creator = (
        bool(player.get("is_admin"))
        or int(player.get("user_id") or 0) == NANSHAN_TOY_USER_ID
        or (player.get("username") or "").lower() == "nanshan"
    )
    if not unlimited_creator:
        active = await fetch_one(
            "SELECT id FROM rooms WHERE created_by = ? AND status IN ('waiting','playing')",
            (player["id"],),
        )
        if active:
            raise HTTPException(status_code=400, detail="请先关闭你当前的房间")
        max_rooms = int(await get_setting("max_rooms", "5"))
        current = await fetch_one("SELECT COUNT(*) AS c FROM rooms WHERE status IN ('waiting','playing')")
        if int(current["c"]) >= max_rooms:
            raise HTTPException(status_code=400, detail="当前房间已满")

    puzzle_id = None
    if body.mode == "random":
        if body.puzzle_id:
            puzzle = await fetch_one("SELECT * FROM puzzles WHERE id = ? AND enabled = 1", (body.puzzle_id,))
        else:
            puzzle = await fetch_one("SELECT * FROM puzzles WHERE enabled = 1 ORDER BY RANDOM() LIMIT 1")
        if not puzzle:
            raise HTTPException(status_code=404, detail="题目不存在")
        puzzle_id = puzzle["id"]
        title = puzzle["title"]
        surface, answer = puzzle["surface"], puzzle["answer"]
    else:
        title = (body.title or "").strip()[:TITLE_LIMIT]
        surface = clean_content(body.surface or "", SURFACE_LIMIT)
        answer = clean_content(body.answer or "", ANSWER_LIMIT)
        if body.mode == "custom":
            reason = await scan_text(f"{surface}\n{answer}")
            if reason:
                raise HTTPException(status_code=400, detail=reason)
            await execute(
                "INSERT INTO puzzle_submissions (surface, answer, tags, submitted_by) VALUES (?, ?, ?, ?)",
                (surface, answer, body.tags[:100], player["id"]),
            )

    rid = room_id()
    while await fetch_one("SELECT id FROM rooms WHERE id = ?", (rid,)):
        rid = room_id()
    await execute(
        "INSERT INTO rooms (id, puzzle_id, title, surface, answer, status, created_by) VALUES (?, ?, ?, ?, ?, 'playing', ?)",
        (rid, puzzle_id, title, surface, answer, player["id"]),
    )
    await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content) VALUES (?, ?, 'system', ?)",
        (rid, player["id"], "游戏开始"),
    )
    return {"room_id": rid}


@router.get("/{room_id}")
async def get_room(room_id: str, player: dict = Depends(current_player)):
    room = await fetch_one(
        """
        SELECT r.id, r.puzzle_id,
               COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
               r.surface, r.answer, r.status, r.created_by, r.winner_id,
               r.manual_hint_count, r.last_hint_at_ask_count, r.created_at, r.finished_at,
               COALESCE(pz.tags, '') AS tags
        FROM rooms r
        LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
        WHERE r.id = ?
        """,
        (room_id,),
    )
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    logs = await fetch_all(
        """
        SELECT gl.id, gl.room_id, gl.player_id, gl.type, gl.content, gl.judgment,
               gl.hint_text, gl.resolved, gl.created_at,
               p.username, p.is_guest, p.is_ai
        FROM game_logs gl
        LEFT JOIN players p ON p.id = gl.player_id
        WHERE gl.room_id = ?
        ORDER BY gl.id ASC
        """,
        (room_id,),
    )
    notes = await fetch_all(
        """
        SELECT rn.*, p.username, p.is_guest FROM room_notes rn
        LEFT JOIN players p ON p.id = rn.player_id
        WHERE rn.room_id = ? ORDER BY rn.updated_at ASC
        """,
        (room_id,),
    )
    for note in notes:
        if not (note.get("username") or "").strip():
            note["username"] = f"游客{note['player_id']}"
    manual_hint_row = await fetch_one(
        "SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND player_id = ? AND type = 'hint_offer'",
        (room_id, player["id"]),
    )
    data = _public_room(room)
    data["manual_hint_count"] = int(manual_hint_row["c"] if manual_hint_row else 0)
    data["ask_count"] = len([log for log in logs if log["type"] == "ask"])
    reveal_row = await fetch_one(
        "SELECT 1 FROM room_answer_reveals WHERE room_id = ? AND player_id = ?",
        (room_id, player["id"]),
    )
    data["answer_revealed"] = reveal_row is not None
    if reveal_row is not None:
        data["revealed_answer"] = public_answer_from_full_answer(room["answer"])
    data["logs"] = logs
    data["notes"] = notes
    return data


@router.post("/{room_id}/close")
async def close_room(room_id: str, player: dict = Depends(current_player)):
    room = await fetch_one("SELECT * FROM rooms WHERE id = ?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    if room["created_by"] != player["id"] and not player.get("is_admin"):
        raise HTTPException(status_code=403, detail="只能关闭自己的房间")
    await execute(
        f"UPDATE rooms SET status = 'finished', finished_at = {SQL_NOW} WHERE id = ?",
        (room_id,),
    )
    return {"ok": True}


@router.get("/profile/me")
async def profile(player: dict = Depends(current_player)):
    rooms = await fetch_all(
        """
        SELECT id, surface, status, winner_id, created_at, finished_at
        FROM rooms
        WHERE created_by = ? OR id IN (SELECT DISTINCT room_id FROM game_logs WHERE player_id = ?)
        ORDER BY created_at DESC LIMIT 30
        """,
        (player["id"], player["id"]),
    )
    return {"player": public_player(player) | {k: player[k] for k in ["ask_count", "ask_count_y", "ask_count_n", "ask_count_u", "ask_count_p", "win_count", "game_count"]}, "rooms": rooms}
