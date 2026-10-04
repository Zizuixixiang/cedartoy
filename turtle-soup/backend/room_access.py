"""Locked rooms use authenticated platform accounts and live platform bindings."""
from fastapi import HTTPException

from database import fetch_one


LOCKED_ROOM_MESSAGE = "这是锁房，仅限创建者本人和同一绑定关系的小机进入"


async def verified_account(player: dict | None) -> dict | None:
    if not player or player.get("is_guest"):
        return None
    user_id = player.get("user_id")
    # Older soup JWTs and legacy player logins cannot prove a platform identity.
    if not user_id or player.get("verified_user_id") != user_id:
        return None
    return await fetch_one(
        "SELECT id, is_ai, is_admin FROM toy_users WHERE id = ? AND deleted_at IS NULL "
        "AND deletion_requested_at_epoch IS NULL",
        (user_id,),
    )


async def require_room_access(room_id: str, player: dict | None, *, room: dict | None = None) -> dict:
    if room is None:
        room = await fetch_one("SELECT * FROM rooms WHERE id = ?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    if not room.get("is_locked"):
        return room
    account = await verified_account(player)
    if account:
        owner_id = room.get("lock_owner_user_id")
        allowed = await fetch_one(
            """
            WITH household AS (
                SELECT id AS user_id, id AS human_id FROM toy_users
                WHERE is_ai = 0 AND deleted_at IS NULL AND deletion_requested_at_epoch IS NULL
                UNION ALL
                SELECT b.ai_user_id, b.human_user_id FROM user_bindings b
                JOIN toy_users h ON h.id = b.human_user_id AND h.is_ai = 0
                JOIN toy_users a ON a.id = b.ai_user_id AND a.is_ai = 1
                WHERE h.deleted_at IS NULL AND h.deletion_requested_at_epoch IS NULL
                  AND a.deleted_at IS NULL AND a.deletion_requested_at_epoch IS NULL
            )
            SELECT 1 FROM toy_users owner
            WHERE owner.id = ? AND owner.deleted_at IS NULL
              AND owner.deletion_requested_at_epoch IS NULL
              AND (owner.id = ? OR EXISTS (
                  SELECT 1 FROM household o JOIN household p ON o.human_id = p.human_id
                  WHERE o.user_id = owner.id AND p.user_id = ?
              ))
            """,
            (owner_id, account["id"], account["id"]),
        )
        if allowed:
            return room
    raise HTTPException(status_code=403, detail=LOCKED_ROOM_MESSAGE)


async def room_read_access_mode(room_id: str, player: dict, *, room: dict) -> str:
    """GET-only fallback. Writes and SSE must keep require_room_access."""
    try:
        await require_room_access(room_id, player, room=room)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        account = await verified_account(player)
        if account and account.get("is_admin"):
            return "admin_readonly"
        raise
    return "member"
