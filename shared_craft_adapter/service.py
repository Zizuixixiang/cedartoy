"""Private Python 3.11 satellite; original handlers, one namespace per room.

Run with one uvicorn worker on loopback. The platform authenticates tokens; this
service accepts only the configured gateway secret and rechecks live membership.
No upstream setup, key-based access or standalone MCP endpoint is mounted.
"""
import ast
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import threading

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .identity import resolve, author_access
from .storage import RoomLock, protect_store, read_json
from .sharing import Shares
from . import ui

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/shared-craft-room/src/shared_craft_room"
sys.path.insert(0, str(UPSTREAM.parent))


def original_code():
    # Keep the vendor repo untouched. Only omit standalone bootstrap: route
    # registration, default global stores and participant credential setup.
    source = (UPSTREAM / "server.py").read_text()
    tree = ast.parse(source)
    constants = {"STATIC_DIR", "MCP_INSTRUCTIONS", "POINT", "BEAD", "STROKE", "TOOLS", "logger"}
    tree.body = [node for node in tree.body
                 if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef))
                 or (isinstance(node, ast.Assign) and
                     all(isinstance(t, ast.Name) and t.id in constants for t in node.targets))]
    return compile(tree, str(UPSTREAM / "server.py"), "exec")


def verify_upstream():
    manifest = json.loads(Path(__file__).with_name("upstream.json").read_text())
    root = UPSTREAM.parents[1]
    for name, digest in manifest["files"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError("上游文件已变化，需重新核验: " + name)


class Participants:
    def __init__(self, room):
        self.room = room

    def list_participants(self):
        return resolve(self.room.accounts, self.room.id)["participants"]

    def participant(self, actor):
        return next(p for p in self.list_participants() if p["id"] == actor)


class Guarded:
    """Recheck queued actions too; unbinding cancels the remaining job."""
    def __init__(self, store, room):
        self.store, self.room = store, room

    def __getattr__(self, name):
        method = getattr(self.store, name)
        if not callable(method):
            return method

        def call(actor, *args, **kwargs):
            with self.room.lock:
                self.room.check(actor)
                return method(actor, *args, **kwargs)
        return call


def with_save_metadata(store, annotate, operation):
    """Attach consent metadata in the same atomic write as the original action."""
    with store.lock:
        save = store._save
        def annotated_save(data):
            annotate(data)
            save(data)
        store._save = annotated_save
        try:
            return operation()
        finally:
            store._save = save


def mcp_content(result):
    """Keep binary snapshots/reference patterns out of ordinary model text."""
    images = []
    image = result.pop("_image_base64", None)
    raw = result.pop("_image_bytes", None)
    if raw:
        image = base64.b64encode(raw).decode()
    if image:
        images.append({"type": "image", "data": image, "mimeType": "image/png"})
    reference = result.pop("reference_image", None)
    if reference:
        result["has_reference"] = True
        header, separator, encoded = reference.partition(",")
        mime = header.removeprefix("data:").removesuffix(";base64")
        if separator and header.endswith(";base64") and mime in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            images.append({"type": "image", "data": encoded, "mimeType": mime})
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}, *images]


class Room:
    def __init__(self, root, room_id, accounts):
        self.id, self.accounts = room_id, accounts
        folder = root / "rooms" / room_id
        self.lock = RoomLock(folder / ".lock")
        self.ns = {"__name__": "shared_craft_room.platform_room",
                   "__package__": "shared_craft_room", "__file__": str(UPSTREAM / "server.py")}
        exec(original_code(), self.ns)
        with self.lock:
            for name, cls in (("canvas", "SharedCanvas"), ("beads", "BeadBoards"), ("gallery", "CraftGallery")):
                path = folder / (name + ".json")
                if path.exists():
                    read_json(path, name)
                store = self.ns[cls](path)
                protect_store(store, name, self.lock)
                setattr(self, name, store)
                self.ns[name] = store
            history_path = folder / "participants.json"
            if history_path.exists():
                read_json(history_path, "participants")
            self.history = self.ns["IdentityStore"](history_path)
            protect_store(self.history, "participants", self.lock)
            # Capture stable actor IDs with the original gallery snapshot, not
            # display names (which may coincide or be renamed later).
            original_add = self.gallery.add

            def add(kind, *args, **kwargs):
                if kind == "sketch":
                    actors = [s["actor"] for s in self.canvas.state()["strokes"]]
                else:
                    source = args[4] if len(args) > 4 else kwargs.get("source_id")
                    board = self.beads.get(args[1], source)
                    actors = [b["actor"] for b in board["cells"].values()]
                    actors += [value for key, value in board.get("craft", {}).items()
                               if key.endswith("_by") and isinstance(value, str)]
                    actors += board.get("craft_contributors", [])
                def annotate(data):
                    data["items"][-1]["actor_ids"] = list(dict.fromkeys(actors))
                return with_save_metadata(self.gallery, annotate, lambda: original_add(kind, *args, **kwargs))

            self.gallery.add = add
            def with_contributor(method):
                def call(actor, board_id, *args, **kwargs):
                    def annotate(data):
                        board = next(b for b in data["boards"] if b["id"] == board_id)
                        board["craft_contributors"] = list(dict.fromkeys(
                            [*board.get("craft_contributors", []), actor]))
                    return with_save_metadata(self.beads, annotate,
                                              lambda: method(actor, board_id, *args, **kwargs))
                return call
            self.beads.craft = with_contributor(self.beads.craft)
            self.beads.save_decoration = with_contributor(self.beads.save_decoration)
            self.ns["identities"] = Participants(self)
            def participant_names(actors):
                names = {p["id"]: p["display_name"] for p in self.history._load()["participants"]}
                return [names.get(actor, "共同作者") for actor in dict.fromkeys(actors)]
            self.ns["participant_names"] = participant_names
            self.ns["jobs"] = self.ns["CraftJobs"](Guarded(self.canvas, self), Guarded(self.beads, self))
            self.ns["actor_for"] = lambda request: request.scope["craft_identity"]["actor"]

    def check(self, actor):
        identity = resolve(self.accounts, actor)
        if identity["room"] != self.id:
            raise PermissionError("绑定已变更，无法访问原手作空间")
        # Historical signatures stay attached to old strokes after unbinding;
        # only current membership grants access or appears as active presence.
        data = self.history._load()
        names = {p["id"]: p for p in data["participants"]}
        updated = {**names, **{p["id"]: p for p in identity["participants"]}}
        if updated != names:
            self.history._save({"participants": list(updated.values())})
        return identity


def create_app(data_dir, accounts_db, grants_db, secret):
    if len(secret) < 32:
        raise ValueError("SHARED_CRAFT_SECRET must contain at least 32 characters")
    verify_upstream()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    # The platform backup job snapshots data/**/*.db with SQLite's backup API.
    shares = Shares(data_dir / "shares.db", grants_db)
    rooms, cache_lock = {}, threading.Lock()

    def get_room(identity):
        with cache_lock:
            if identity["room"] not in rooms:
                rooms[identity["room"]] = Room(data_dir, identity["room"], accounts_db)
            return rooms[identity["room"]]

    async def endpoint(request):
        if not hmac.compare_digest(request.headers.get("x-craft-secret", ""), secret):
            return JSONResponse({"error": "Not found"}, status_code=404)
        try:
            user_id = int(request.headers["x-craft-user"])
            path = request.path_params.get("path", "")
            if path == "author" or path.startswith("author/"):
                if request.method != "GET":
                    return Response(status_code=405)
                if request.query_params.get("access") == "1":
                    return JSONResponse({"allowed": author_access(grants_db, user_id)})
                if path == "author":
                    return JSONResponse(shares.read(user_id, request.query_params.get("page", 1)))
                return Response(shares.read(user_id, image_id=path.split("/")[1]), media_type="image/png")
            identity = resolve(accounts_db, user_id)
            request.scope["craft_identity"] = identity
            if request.method == "POST":
                raw = await request.body()
                if len(raw) > 17_000_000:
                    raise ValueError("请求过大")
                request._json = json.loads(raw)
                if not isinstance(request._json, dict):
                    raise ValueError("参数必须为对象")
            # Static assets use exactly the original files with narrowly scoped
            # transport/multiplayer changes; no alternative room UI.
            if request.method == "GET" and path.startswith("assets/"):
                return ui.asset(path.split("/", 1)[1])
            if request.method == "GET" and path in {"sketch/", "beads/", "crafts/"}:
                return ui.page(path.rstrip("/"))
            room = get_room(identity)
            with room.lock:
                room.check(identity["actor"])
                if path == "play" and request.method == "POST":
                    body = request._json
                    action, params = body.get("action"), body.get("params") or {}
                    if not isinstance(params, dict):
                        raise ValueError("params 必须为对象")
                    if action == "list_gallery":
                        result = {"items": room.gallery.list_items()}
                    elif action == "save_gallery":
                        request._json = params
                        return await room.ns["api_gallery"](request)
                    elif action == "share_work":
                        result = shares.submit(room, identity, **params)
                    else:
                        if action == "new_canvas":
                            params = dict(params)
                            confirmed = params.pop("confirm", False)
                            if room.canvas.state()["strokes"] and confirmed is not True:
                                raise ValueError("画本已有笔迹；换新页需要 confirm=true")
                        # Original action and params are passed intact, including
                        # params.action used by work_bead_finish.
                        result = room.ns["call_tool"](identity["actor"], action, params)
                    return JSONResponse({"content": mcp_content(result)})
                if path == "api/shares" and request.method == "POST":
                    return JSONResponse({"ok": True, **shares.submit(room, identity, **request._json)})
                match = re.fullmatch(r"api/shares/([a-f0-9]{32})(\.png)?", path)
                if match:
                    if request.method == "GET" and match[2]:
                        return Response(shares.pending_image(identity, match[1]), media_type="image/png")
                    if request.method == "POST" and not match[2]:
                        return JSONResponse({"ok": True, **shares.decide(identity, match[1], request._json.get("confirm"))})
                for pattern, methods, handler in (
                    (r"api/canvas", {"GET", "POST"}, "api_canvas"),
                    (r"api/canvas.png", {"GET"}, "api_canvas_png"),
                    (r"api/bead-boards", {"GET", "POST"}, "api_boards"),
                    (r"api/bead-boards/(?P<board_id>[a-f0-9]+)\.png", {"GET"}, "api_board_png"),
                    (r"api/bead-boards/(?P<board_id>[a-f0-9]+)", {"GET", "POST", "DELETE"}, "api_board"),
                    (r"api/gallery", {"GET", "POST"}, "api_gallery"),
                    (r"api/gallery/(?P<item_id>[a-f0-9]+)(?:\.png|/display\.png)", {"GET"}, "gallery_image"),
                    (r"api/gallery/(?P<item_id>[a-f0-9]+)", {"POST", "DELETE"}, "api_gallery_item"),
                ):
                    match = re.fullmatch(pattern, path)
                    if match and request.method in methods:
                        request.path_params.update(match.groupdict())
                        if handler == "api_canvas" and request.method == "POST":
                            if (request._json.get("action") == "clear" and room.canvas.state()["strokes"]
                                    and request._json.get("confirm") is not True):
                                raise ValueError("画本已有笔迹；换新页需要明确确认")
                        response = await room.ns[handler](request)
                        if handler in {"api_canvas", "api_board", "api_gallery"} and request.method == "GET":
                            data = json.loads(response.body)
                            names = {p["id"]: p["display_name"] for p in identity["participants"]}
                            data["presence"] = [{**p, "name": names[p["actor"]]} for p in data.get("presence", [])
                                                if p["actor"] in names]
                            if handler == "api_gallery":
                                data["pending_shares"] = shares.pending(identity)
                            response = JSONResponse(data, status_code=response.status_code)
                        # FileResponse streams after the lock is released. Read
                        # the immutable snapshot now, before a concurrent delete.
                        if hasattr(response, "path"):
                            response = Response(Path(response.path).read_bytes(), media_type="image/png")
                        return response
                return JSONResponse({"error": "Not found"}, status_code=404)
        except PermissionError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)
        except (KeyError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Shared craft request failed")
            return JSONResponse({"ok": False, "error": "手作空间暂时不可用；数据未重置"}, status_code=503)

    app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "DELETE"])])
    app.state.rooms, app.state.shares = rooms, shares
    return app


def application():
    return create_app(os.environ["CRAFT_DATA_DIR"], os.environ["TURTLE_SOUP_DB"],
                      os.environ["SESSIONS_DB"], os.environ["SHARED_CRAFT_SECRET"])


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        raise SystemExit("Run the satellite with isolated Python >=3.11.")
    import uvicorn
    uvicorn.run(application(), host="127.0.0.1", port=int(os.environ.get("SHARED_CRAFT_PORT", "8793")),
                access_log=False)
