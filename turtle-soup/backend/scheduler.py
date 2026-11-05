import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import execute, fetch_all, get_setting
from judge import scan_text
from presence import cleanup_stale_presence

logger = logging.getLogger(__name__)


async def _delete_room(room_id: str) -> None:
    await execute(
        "UPDATE reports SET log_id = NULL WHERE log_id IN (SELECT id FROM game_logs WHERE room_id = ?)",
        (room_id,),
    )
    await execute("UPDATE reports SET room_id = NULL WHERE room_id = ?", (room_id,))
    await execute(
        "UPDATE judge_audit_logs SET game_log_id = NULL WHERE game_log_id IN (SELECT id FROM game_logs WHERE room_id = ?)",
        (room_id,),
    )
    await execute("UPDATE judge_audit_logs SET room_id = NULL WHERE room_id = ?", (room_id,))
    await execute(
        "DELETE FROM room_hint_views WHERE log_id IN (SELECT id FROM game_logs WHERE room_id = ?)",
        (room_id,),
    )
    await execute("DELETE FROM room_answer_reveals WHERE room_id = ?", (room_id,))
    await execute("DELETE FROM room_presence WHERE room_id = ?", (room_id,))
    await execute("DELETE FROM room_notes WHERE room_id = ?", (room_id,))
    await execute("DELETE FROM game_logs WHERE room_id = ?", (room_id,))
    await execute("DELETE FROM rooms WHERE id = ?", (room_id,))


async def _unlink_player_refs(player_id: int) -> None:
    await execute("UPDATE reports SET reporter_id = NULL WHERE reporter_id = ?", (player_id,))
    await execute("UPDATE reports SET target_player_id = NULL WHERE target_player_id = ?", (player_id,))
    await execute("UPDATE judge_audit_logs SET player_id = NULL WHERE player_id = ?", (player_id,))
    await execute("UPDATE ban_ips SET banned_by = NULL WHERE banned_by = ?", (player_id,))
    await execute("UPDATE rooms SET created_by = NULL WHERE created_by = ?", (player_id,))
    await execute("UPDATE rooms SET winner_id = NULL WHERE winner_id = ?", (player_id,))
    await execute("UPDATE puzzles SET created_by = NULL WHERE created_by = ?", (player_id,))
    await execute("UPDATE puzzle_submissions SET submitted_by = NULL WHERE submitted_by = ?", (player_id,))
    await execute("UPDATE room_notes SET player_id = NULL WHERE player_id = ?", (player_id,))
    await execute("UPDATE game_logs SET player_id = NULL WHERE player_id = ?", (player_id,))
    await execute("DELETE FROM room_answer_reveals WHERE player_id = ?", (player_id,))
    await execute("DELETE FROM room_hint_views WHERE player_id = ?", (player_id,))
    await execute("DELETE FROM room_presence WHERE player_id = ?", (player_id,))


async def cleanup_inactive_rooms() -> None:
    hours = int(await get_setting("room_inactive_expire_hours", "48"))
    await execute(
        """
        UPDATE rooms
        SET status = 'finished', finished_at = datetime('now', 'localtime')
        WHERE status IN ('waiting', 'playing')
          AND datetime(COALESCE(
                (
                  SELECT MAX(gl.created_at)
                  FROM game_logs gl
                  WHERE gl.room_id = rooms.id
                    AND gl.player_id IS NOT NULL
                ),
                rooms.created_at
              )) < datetime('now', 'localtime', ?)
        """,
        (f"-{hours} hours",),
    )


async def cleanup_finished_rooms() -> None:
    hours = int(await get_setting("finished_room_retention_hours", "24"))
    rooms = await fetch_all(
        """
        SELECT id FROM rooms
        WHERE status = 'finished'
          AND datetime(COALESCE(finished_at, created_at)) < datetime('now', 'localtime', ?)
        """,
        (f"-{hours} hours",),
    )
    for room in rooms:
        try:
            await _delete_room(room["id"])
        except Exception as exc:
            logger.warning("清理已结束房间失败，跳过 room_id=%s: %s", room["id"], exc, exc_info=True)


async def cleanup_guests() -> None:
    hours = int(await get_setting("guest_expire_hours", "1"))
    room_hours = int(await get_setting("room_inactive_expire_hours", "48"))
    guests = await fetch_all(
        """
        SELECT id FROM players
        WHERE is_guest = 1
          AND datetime(created_at) < datetime('now', 'localtime', ?)
          AND datetime(last_active_at) < datetime('now', 'localtime', ?)
          AND NOT EXISTS (
            SELECT 1
            FROM game_logs gl
            WHERE gl.player_id = players.id
              AND datetime(gl.created_at) >= datetime('now', 'localtime', ?)
          )
          AND NOT EXISTS (
            SELECT 1
            FROM rooms r
            WHERE r.created_by = players.id
              AND datetime(COALESCE(
                    (
                      SELECT MAX(gl.created_at)
                      FROM game_logs gl
                      WHERE gl.room_id = r.id
                        AND gl.player_id IS NOT NULL
                    ),
                    r.created_at
                  )) >= datetime('now', 'localtime', ?)
          )
        """,
        (
            f"-{hours} hours",
            f"-{hours} hours",
            f"-{room_hours} hours",
            f"-{room_hours} hours",
        ),
    )
    for guest in guests:
        pid = guest["id"]
        try:
            guest_rooms = await fetch_all(
                """
                SELECT id FROM rooms
                WHERE created_by = ?
                  AND datetime(COALESCE(
                        (
                          SELECT MAX(gl.created_at)
                          FROM game_logs gl
                          WHERE gl.room_id = rooms.id
                            AND gl.player_id IS NOT NULL
                        ),
                        rooms.created_at
                      )) < datetime('now', 'localtime', ?)
                """,
                (pid, f"-{room_hours} hours"),
            )
            for room in guest_rooms:
                try:
                    await _delete_room(room["id"])
                except Exception as exc:
                    logger.warning(
                        "清理游客旧房间失败，继续处理 guest_id=%s room_id=%s: %s",
                        pid,
                        room["id"],
                        exc,
                        exc_info=True,
                    )
            await _unlink_player_refs(pid)
            await execute("DELETE FROM players WHERE id = ?", (pid,))
        except Exception as exc:
            logger.warning("清理游客失败，跳过 guest_id=%s: %s", pid, exc, exc_info=True)


async def scan_recent_content() -> None:
    try:
        players = await fetch_all(
            """
            SELECT id, username FROM players
            WHERE username IS NOT NULL
              AND created_at >= datetime('now', 'localtime', '-2 days')
              AND id NOT IN (SELECT ref_id FROM flagged_content WHERE type = 'username')
            """
        )
        for row in players:
            reason = await scan_text(row["username"])
            if reason:
                await execute(
                    "INSERT INTO flagged_content (type, ref_id, reason) VALUES ('username', ?, ?)",
                    (row["id"], reason),
                )
        # 投稿不自动扫描，由管理员直接审核
    except Exception as exc:
        logger.warning("AI 内容扫描失败: %s", exc)


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(cleanup_inactive_rooms, "interval", hours=1, id="cleanup_inactive_rooms")
    scheduler.add_job(cleanup_finished_rooms, "interval", minutes=10, id="cleanup_finished_rooms")
    scheduler.add_job(cleanup_guests, "interval", hours=1, id="cleanup_guests")
    scheduler.add_job(cleanup_stale_presence, "interval", minutes=15, id="cleanup_stale_presence")
    scheduler.add_job(scan_recent_content, "cron", hour=3, minute=0, id="scan_recent_content")
    scheduler.start()
    return scheduler
