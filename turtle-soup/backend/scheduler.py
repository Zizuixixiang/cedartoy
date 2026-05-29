import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import execute, fetch_all, get_setting
from judge import scan_text

logger = logging.getLogger(__name__)


async def cleanup_guests() -> None:
    hours = int(await get_setting("guest_expire_hours", "48"))
    guests = await fetch_all(
        """
        SELECT id FROM players
        WHERE is_guest = 1
          AND datetime(created_at) < datetime('now', ?)
          AND datetime(last_active_at) < datetime('now', ?)
        """,
        (f"-{hours} hours", f"-{hours} hours"),
    )
    for guest in guests:
        pid = guest["id"]
        await execute(
            "UPDATE rooms SET status = 'finished', finished_at = CURRENT_TIMESTAMP WHERE created_by = ? AND status IN ('waiting','playing')",
            (pid,),
        )
        await execute("DELETE FROM room_notes WHERE player_id = ?", (pid,))
        await execute("DELETE FROM game_logs WHERE player_id = ?", (pid,))
        await execute("DELETE FROM players WHERE id = ?", (pid,))


async def scan_recent_content() -> None:
    try:
        players = await fetch_all(
            """
            SELECT id, username FROM players
            WHERE username IS NOT NULL
              AND created_at >= datetime('now', '-2 days')
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
        submissions = await fetch_all(
            """
            SELECT id, surface, answer FROM puzzle_submissions
            WHERE status = 'pending'
              AND id NOT IN (SELECT ref_id FROM flagged_content WHERE type = 'submission')
            """
        )
        for row in submissions:
            reason = await scan_text(f"{row['surface']}\n{row['answer']}")
            if reason:
                await execute(
                    "INSERT INTO flagged_content (type, ref_id, reason) VALUES ('submission', ?, ?)",
                    (row["id"], reason),
                )
    except Exception as exc:
        logger.warning("AI 内容扫描失败: %s", exc)


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(cleanup_guests, "interval", hours=1, id="cleanup_guests")
    scheduler.add_job(scan_recent_content, "cron", hour=3, minute=0, id="scan_recent_content")
    scheduler.start()
    return scheduler
