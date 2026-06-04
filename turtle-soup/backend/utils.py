import re
import secrets
import string

from fastapi import HTTPException


SAFE_TEXT_RE = re.compile(r"[<>{}]")
ROOM_ALPHABET = string.ascii_letters + string.digits

# SQLite CURRENT_TIMESTAMP / datetime('now') are UTC; store/compare China wall time (server TZ).
SQL_NOW = "datetime('now', 'localtime')"
ROOM_FINISHED_STATUS_HINT = (
    "该房间游戏已结束，无法继续操作。详情可查看 status："
    'play(game="turtle_soup", action="status", room_id=...)。'
)


def strip_puzzle_text(value: str | None, *, required: bool = False, label: str = "内容") -> str:
    text = (value or "").strip()
    if required and not text:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if text and SAFE_TEXT_RE.search(text):
        raise HTTPException(status_code=400, detail="内容包含不允许的字符")
    return text


def clean_content(value: str, limit: int = 200) -> str:
    text = (value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容不能为空")
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=f"内容不能超过 {limit} 字")
    if SAFE_TEXT_RE.search(text):
        raise HTTPException(status_code=400, detail="内容包含不允许的字符")
    return text


def room_id() -> str:
    return "".join(secrets.choice(ROOM_ALPHABET) for _ in range(8))


def public_player(row: dict | None) -> dict | None:
    if not row:
        return None
    name = row.get("username") or f"游客{row['id']}"
    return {
        "id": row["id"],
        "username": name,
        "is_guest": bool(row.get("is_guest")),
        "is_ai": bool(row.get("is_ai")),
        "is_admin": bool(row.get("is_admin")),
    }
