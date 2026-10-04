import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import execute, fetch_one
from utils import SQL_NOW


SECRET_KEY = os.getenv("TURTLE_SOUP_SECRET", "change-me-before-production")
ALGORITHM = "HS256"
TOKEN_HOURS = 24 * 14

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_token(player: dict, *, verified_user_id: int | None = None) -> str:
    payload = {
        "player_id": player["id"],
        "verified_user_id": verified_user_id,
        "is_admin": bool(player.get("is_admin")),
        "is_guest": bool(player.get("is_guest")),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def authenticated_player(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    raw_token = credentials.credentials if credentials else request.query_params.get("token")
    if not raw_token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
        player_id = int(payload["player_id"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="登录已失效") from None
    player = await fetch_one("SELECT * FROM players WHERE id = ?", (player_id,))
    if not player:
        raise HTTPException(status_code=401, detail="账号不存在")
    player["verified_user_id"] = payload.get("verified_user_id")
    return player


async def current_player(player: dict = Depends(authenticated_player)) -> dict:
    await execute(f"UPDATE players SET last_active_at = {SQL_NOW} WHERE id = ?", (player["id"],))
    return player


async def admin_player(player: dict = Depends(current_player)) -> dict:
    if not player.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return player
