from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from database import fetch_one


class IpBanMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
        if ip:
            banned = await fetch_one("SELECT id FROM ban_ips WHERE ip = ?", (ip,))
            if banned:
                return JSONResponse({"detail": "IP 已被封禁"}, status_code=403)
        return await call_next(request)
