from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from database import init_db
from mcp_app import router as mcp_router
from middleware import IpBanMiddleware
from routers import admin, auth, game, leaderboard, notes, puzzles, report, rooms
from scheduler import start_scheduler
from sse import router as sse_router


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Turtle Soup", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(IpBanMiddleware)

api_prefix = "/soup/api"
app.include_router(auth.router, prefix=api_prefix)
app.include_router(puzzles.router, prefix=api_prefix)
app.include_router(rooms.router, prefix=api_prefix)
app.include_router(game.router, prefix=api_prefix)
app.include_router(admin.router, prefix=api_prefix)
app.include_router(leaderboard.router, prefix=api_prefix)
app.include_router(notes.router, prefix=api_prefix)
app.include_router(report.router, prefix=api_prefix)
app.include_router(sse_router, prefix=api_prefix)
app.include_router(mcp_router)


@app.get("/health")
@app.get("/soup/health")
async def health():
    return {"status": "healthy"}


@app.get("/")
async def toy_home():
    return Response(
        """
        <!doctype html>
        <html lang="zh-CN">
        <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Toy CedarStar</title></head>
        <body style="font-family:system-ui,-apple-system,'Noto Sans SC',sans-serif;margin:0;background:#f5f7f6;color:#1d2522">
          <main style="max-width:760px;margin:0 auto;padding:48px 20px">
            <h1 style="font-size:36px;margin:0 0 12px">Toy CedarStar</h1>
            <p style="color:#66736d;margin:0 0 28px">多人推理游戏大厅</p>
            <a href="/soup/" style="display:inline-block;background:#16745f;color:white;text-decoration:none;padding:12px 16px;border-radius:8px">进入海龟汤</a>
          </main>
        </body>
        </html>
        """,
        media_type="text/html",
    )


if STATIC_DIR.exists():
    app.mount("/soup/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="soup-assets")


@app.get("/soup")
@app.get("/soup/{full_path:path}")
async def spa(full_path: str = ""):
    candidate = STATIC_DIR / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return Response("Turtle Soup frontend is not built yet.", status_code=404)
