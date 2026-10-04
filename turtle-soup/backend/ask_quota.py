"""Personal Beijing-day quota for ordinary asks only."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from database import fetch_one, get_db

DAILY_ASK_LIMIT = 300
ASK_QUOTA_MESSAGE = "今日个人次数已达上限（300 次），0 点重置"
BEIJING_TZ = timezone(timedelta(hours=8))


async def get_ask_quota(player: dict) -> dict:
    now = datetime.now(BEIJING_TZ)
    reset_at = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        "SELECT ask_count FROM player_daily_asks WHERE player_id = ? AND beijing_date = ?",
        (player["id"], now.date().isoformat()),
    )
    return {
        "used": int(row["ask_count"]) if row else 0,
        "limit": None if player.get("is_admin") else DAILY_ASK_LIMIT,
        "reset_at": reset_at.isoformat(),
    }


async def reserve_daily_ask(player: dict) -> None:
    if player.get("is_admin"):
        return
    db = await get_db()
    try:
        # Serialize across rooms and workers; date is sampled after the lock.
        await db.execute("BEGIN IMMEDIATE")
        day = datetime.now(BEIJING_TZ).date().isoformat()
        cur = await db.execute(
            """
            INSERT INTO player_daily_asks (player_id, beijing_date, ask_count)
            VALUES (?, ?, 1)
            ON CONFLICT (player_id, beijing_date) DO UPDATE
            SET ask_count = ask_count + 1
            WHERE ask_count < ?
            """,
            (player["id"], day, DAILY_ASK_LIMIT),
        )
        allowed = cur.rowcount == 1
        # Release the DB lock before calling upstream; failed calls still consume quota.
        await db.commit()
    finally:
        await db.close()
    if not allowed:
        raise HTTPException(status_code=429, detail=ASK_QUOTA_MESSAGE)
