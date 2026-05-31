from database import execute


async def enter_room(room_id: str, player_id: int) -> None:
    await execute(
        """
        INSERT INTO room_presence (room_id, player_id, joined_at, last_active_at)
        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(room_id, player_id) DO UPDATE SET last_active_at = CURRENT_TIMESTAMP
        """,
        (room_id, player_id),
    )


async def leave_room(room_id: str, player_id: int) -> None:
    await execute(
        "DELETE FROM room_presence WHERE room_id = ? AND player_id = ?",
        (room_id, player_id),
    )


async def touch_room(room_id: str, player_id: int) -> None:
    await execute(
        """
        UPDATE room_presence
        SET last_active_at = CURRENT_TIMESTAMP
        WHERE room_id = ? AND player_id = ?
        """,
        (room_id, player_id),
    )


async def cleanup_stale_presence(hours: int = 1) -> None:
    await execute(
        f"DELETE FROM room_presence WHERE last_active_at < datetime('now', '-{int(hours)} hour')",
    )
