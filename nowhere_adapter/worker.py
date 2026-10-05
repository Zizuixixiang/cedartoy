"""Persistent engine worker. NEVER imported by the platform HTTP process.

NOWHERE_HOME and NOWHERE_TRAVELER_NAME are assigned once at process spawn.
Only this player's disposable workspace and immutable upstream data are used.
"""
import asyncio
import base64
import copy
import inspect
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

from . import shared, storage
from .diagnostics import PUBLIC_ERROR, internal_error
from .radio_state import clear_dead_radio
from .radio_health import install as install_radio_health

RUNTIME_KEYS = ("_postcard_counter", "_hint_counter", "_mishap_last_step", "_mishap_echoed_id")


def tuples(value):
    return tuple(map(tuples, value)) if isinstance(value, list) else value


class Engine:
    def __init__(self, player):
        if sys.version_info < (3, 11):
            raise RuntimeError("Python >=3.11 required")
        vendor = Path(__file__).resolve().parents[1] / "vendor/nowhere"
        revision = subprocess.check_output(["git", "-C", str(vendor), "rev-parse", "HEAD"], text=True).strip()
        if revision != storage.VERSION:
            raise RuntimeError("上游版本不匹配，须先验收升级")
        from nowhere import server, state, web
        self.server, self.state, self.web = server, state, web
        # Worker-only compatibility patch: keep upstream country/distance and
        # online selection rules, but validate fallback streams before use.
        # No vendor file writes or upstream imports in the HTTP process.
        install_radio_health(server)
        self.player = player
        self.home = Path(os.environ["NOWHERE_HOME"])
        self.files = self.home / "private"
        self.files.mkdir(exist_ok=True)
        # All path resolution is fixed for the lifetime of this player worker.
        # No environment switching, and no write back into vendor/static.
        from nowhere import util, placememory, notebook, journeys, marks, poster
        util._get_home = placememory._get_home = notebook._get_home = lambda: self.files
        state._SAVE_DIR, state._SAVE_FILE = self.files, self.files / "journey.json"
        journeys._JOURNEYS_DIR = self.files / "journeys"
        journeys._INDEX_FILE = journeys._JOURNEYS_DIR / "index.json"
        marks._marks_path = lambda: self.files / "marks.json"
        # Detect upstream path function renames instead of silently sharing.
        if hasattr(marks, "_path"):
            marks._path = lambda: self.files / "marks.json"
        self.memory, self.journeys, self.poster = placememory, journeys, poster
        self.meta = {}
        self.resuming_alone = False
        self.defaults = {key: copy.deepcopy(getattr(server, key)) for key in RUNTIME_KEYS}
        shared.install(server.travelers_mod, player,
                       lambda: {**self.meta, "cotraveler": "0"} if self.resuming_alone else self.meta)
        self.poster_tasks = set()
        server._poster_front_async = self.queue_poster
        # Upstream omits this dynamic flag from its dataclass serializer.
        original_to = state.WorldState.to_dict
        original_from = state.WorldState.from_dict
        def to_dict(world):
            return {**original_to(world), "cotraveler_alone": bool(getattr(world, "cotraveler_alone", False))}
        def from_dict(cls, data):
            # Also covers continue/switch_journey loading an inactive old save.
            world = original_from(clear_dead_radio(data))
            world.cotraveler_alone = bool(data.get("cotraveler_alone", False))
            return world
        state.WorldState.to_dict = to_dict
        state.WorldState.from_dict = classmethod(from_dict)

    async def initialize(self):
        self.tools = await self.server.mcp.get_tools()
        manifest = json.loads(Path(__file__).with_name("schema.json").read_text())
        expected = {t["name"] for t in manifest if t.get("source") == "upstream_mcp"}
        if set(self.tools) != expected:
            raise RuntimeError("上游 MCP 注册发生变化，需重新核对完整 schema")
        # Compare names AND parameter contracts with actual FastMCP registration.
        for item in manifest:
            if item.get("source") == "upstream_mcp":
                actual = self.tools[item["name"]].parameters
                expected_props = item["inputSchema"]["properties"]
                if set(actual.get("properties", {})) != set(expected_props):
                    raise RuntimeError("上游 MCP 参数发生变化")

    def restore(self, archive, generation):
        shutil.rmtree(self.files)
        self.files.mkdir()
        if archive:
            storage.validate(archive)
            for name, value in archive["files"].items():
                target = self.files / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                # Fail on semantic corruption even in inactive journeys.
                if name == "journey.json" or (name.startswith("journeys/") and name != "journeys/index.json"):
                    self.state.WorldState.from_dict(value)
        runtime = archive.get("runtime", {}) if archive else {}
        self.meta = copy.deepcopy(runtime.get("metadata", {}))
        self.images = copy.deepcopy(archive.get("images", {})) if archive else {}
        self.generation = generation
        self.server._state = self.state.WorldState.from_dict(archive["files"]["journey.json"]) if archive else self.state.WorldState()
        self.server._rng = random.Random()
        if runtime.get("rng"):
            self.server._rng.setstate(tuples(runtime["rng"]))
        if runtime.get("global_rng"):
            random.setstate(tuples(runtime["global_rng"]))
        for key in RUNTIME_KEYS:
            setattr(self.server, key, copy.deepcopy(runtime.get(key, self.defaults[key])))
        self.counter_floor = self.server._postcard_counter
        self.server._recent_salience_kinds = set(runtime.get("recent_salience_kinds", []))
        # Registry/meeting counters live in shared transactions, never imports.
        self.server._cotraveler_encounter_counts = {}
        self.server._cotraveler_meeting_log = {}
        self.sync_postcard_counter()

    def sync_postcard_counter(self):
        # continue_journey resets the upstream counter from only that journey's
        # last 20 cards. Keep the cross-journey counter monotonic after cold load.
        self.server._postcard_counter = max(self.server._postcard_counter,
                                           getattr(self, "counter_floor", 0),
                                           max((c.get("id", 0) for c in self.memory.postcards()), default=0))
        self.counter_floor = self.server._postcard_counter

    def snapshot(self):
        self.sync_postcard_counter()
        # Upstream walk increments its odometer AFTER save(); persist the final
        # state, not that intermediate write. Archive active journey on each call.
        if self.server._state.pos is not None:
            self.journeys.save_current(self.server._state)
        self.server._state.save()
        files = {str(path.relative_to(self.files)): json.loads(path.read_text())
                 for path in self.files.rglob("*.json")}
        runtime = {key: getattr(self.server, key) for key in RUNTIME_KEYS}
        runtime.update(rng=self.server._rng.getstate(), global_rng=random.getstate(),
                       recent_salience_kinds=list(self.server._recent_salience_kinds), metadata=self.meta)
        return {"format": "cedartoy.nowhere.v1", "upstream": storage.VERSION,
                "generation": self.generation, "files": files, "images": self.images, "runtime": runtime}

    def queue_poster(self, card, lat, lon, biome=""):
        if not self.poster.available():
            return  # Upstream SVG postcard remains fully usable.
        if not getattr(self, "poster_script_ready", False):
            # maptoposter caches routes/fonts relative to its own script. Run an
            # unchanged disposable copy so even optional media never writes vendor.
            source = self.poster._SCRIPT.parent
            target = self.home / "maptoposter"
            try:
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("cache", "posters", "__pycache__"))
            except OSError:
                return
            self.poster._SCRIPT = target / "create_map_poster.py"
            self.poster_script_ready = True
        task = asyncio.create_task(self.generate_poster(copy.deepcopy(card), lat, lon, biome, self.generation))
        self.poster_tasks.add(task)
        task.add_done_callback(self.poster_tasks.discard)

    async def generate_poster(self, card, lat, lon, biome, generation):
        # Every job captures its player, generation and destination. It does not
        # touch engine _state or placememory after another request has restored it.
        try:
            with tempfile.TemporaryDirectory(prefix="poster-", dir=self.home) as directory:
                out = Path(directory) / "front.png"
                ok = await self.poster.generate(lat, lon, card["stamp"]["place"], out,
                                                distance=6000 if biome == "city" else 15000)
                if not ok:
                    ok = await asyncio.to_thread(self.poster.blank, out, card["stamp"]["place"], lat, lon,
                                                 surface=card["stamp"].get("surface", ""))
                if ok:
                    await asyncio.to_thread(self.merge_poster, card, generation, out.read_bytes())
        except Exception:
            # Optional media failure must not invalidate a posted card.
            return

    def merge_poster(self, card, generation, png):
        with storage.locked(self.player):
            archive = storage.read(self.player)
            if not archive or archive["generation"] != generation:
                return  # Deleted/imported/reset while the job was running.
            cards = archive["files"].get("postcards.json", {}).get("items", [])
            current = next((c for c in cards if c.get("id") == card["id"] and c.get("text") == card.get("text") and c.get("stamp") == card.get("stamp")), None)
            if current is None:
                return
            filename = f"postcards/card_{card['id']}.png"
            current["front_img"] = "/static/" + filename
            archive.setdefault("images", {})[filename] = base64.b64encode(png).decode()
            # Update just the image field; preserve replies arriving meanwhile.
            for value in archive["files"].values():
                if not isinstance(value, dict):
                    continue
                for c in value.get("postcards", []):
                    if c.get("id") == card["id"]:
                        c["front_img"] = current["front_img"]
            storage.write(self.player, archive)

    async def web_action(self, request):
        from starlette.requests import Request
        from starlette.routing import Match, Route
        path, method = request["path"], request["method"]
        body = json.dumps(request.get("body", {})).encode()
        scope = {"type": "http", "path": path, "method": method, "headers": [], "query_string": b""}
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        for route in self.web.app.routes:
            if not isinstance(route, Route):
                continue
            match, child = route.matches(scope)
            if match == Match.FULL:
                scope.update(child)
                response = await route.endpoint(Request(scope, receive))
                if method == "DELETE" and response.status_code == 200:
                    card_id = scope["path_params"]["card_id"]
                    self.server._state.postcards = [c for c in self.server._state.postcards if c.get("id") != card_id]
                    self.images.pop(f"postcards/card_{card_id}.png", None)
                    for file in (self.files / "journeys").glob("*.json"):
                        data = json.loads(file.read_text())
                        if "postcards" in data:
                            data["postcards"] = [c for c in data["postcards"] if c.get("id") != card_id]
                            file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return {"status": response.status_code, "body": json.loads(response.body)}
        raise ValueError("不支持的网页动作")

    async def run(self, payload):
        self.diagnostic_request = payload.get("request")
        self.diagnostic_stage = "restore"
        self.restore(payload.get("archive"), payload["generation"])
        request = dict(payload["request"])
        self.diagnostic_request = request
        self.diagnostic_stage = "prepare"
        action = request.pop("action")
        request.pop("confirm", None)
        # Old cached Kelivo/Dart/Ktor fat schemas inject these turtle_soup
        # defaults into unrelated actions. Ignore ONLY the literal bool False;
        # true, strings, 0, null and all other extras still reach strict validation.
        for key in ("is_locked", "include_finished"):
            if request.get(key) is False:
                request.pop(key)
        if action == "validate_import":
            self.diagnostic_stage = "snapshot"
            return {"result": {"text": "完整私人旅程导入成功"}, "archive": self.snapshot()}
        if action == "_web":
            self.diagnostic_stage = "action"
            result = await self.web_action(request)
            if result["status"] >= 400 or request["method"] == "GET":
                return {"result": result}
        else:
            if action in {"new", "open_door"}:
                if "traveler_name" in request:
                    name = request.pop("traveler_name")
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
                        raise ValueError("traveler_name 必须是 1–60 字符")
                    clean = self.web._sanitize_user_text(name)
                    if clean is None:
                        raise ValueError("旅者名称包含不允许的控制指令")
                    self.meta["traveler_name"] = clean.strip()
                if "cotraveler" in request:
                    mode = request.pop("cotraveler")
                    if mode not in {"0", "1", "quiet"}:
                        raise ValueError("cotraveler 可选 0、1、quiet")
                    self.meta["cotraveler"] = mode
                    if mode == "0":
                        shared.forget(self.player)
                action = "open_door"
            if action == "send_postcard":
                from jsonschema import validate
                self.diagnostic_stage = "validate"
                validate(request, {"type": "object", "properties": {"text": {"type": "string", "maxLength": 1000}}, "required": ["text"], "additionalProperties": False})
                self.diagnostic_stage = "action"
                result = await self.server._run_serialized(self.server.send_postcard_impl, **request)
            elif action == "switch_journey":
                place = request.get("place")
                if set(request) != {"place"} or not isinstance(place, str) or not place.strip():
                    raise ValueError("switch_journey 需要 place（已有地名或 slug）")
                # Delegate return narrative and journey selection to upstream.
                self.diagnostic_stage = "action"
                if self.journeys.get_journey_meta(place) is None:
                    raise ValueError("旅程不存在")
                result = await self.server.open_door_impl(to=place)
            else:
                from jsonschema import validate
                tool = self.tools[action]
                parameters = {**tool.parameters, "additionalProperties": False}
                self.diagnostic_stage = "validate"
                validate(request, parameters)
                self.diagnostic_stage = "action"
                # Upstream continue reuses open_door_impl(resume=True), whose
                # cotraveler block also resets solo and publishes a registration.
                # Suppress that block before it can publish; retain the loaded
                # flag and normal resume behavior. Actual new doors are unchanged.
                self.resuming_alone = action == "continue_journey" and bool(
                    getattr(self.server._state, "cotraveler_alone", False))
                try:
                    result = tool.fn(**request)
                    if inspect.isawaitable(result):
                        result = await result
                finally:
                    self.resuming_alone = False
                if action == "walk_alone":
                    shared.forget(self.player)
        if isinstance(result, dict) and (result.get("ok") is False or (isinstance(result.get("data"), dict) and result["data"].get("error"))):
            return {"error": result.get("text") or result.get("error", "上游动作失败")}
        self.diagnostic_stage = "snapshot"
        return {"result": result, "archive": self.snapshot()}


async def main():
    # Upstream prints must never corrupt the IPC protocol.
    output = sys.stdout
    sys.stdout = sys.stderr
    try:
        engine = Engine(sys.argv[1])
        await engine.initialize()
    except Exception as exc:
        output.write(json.dumps({"error": PUBLIC_ERROR,
                                 "internal_error": internal_error(exc, None, "initialize")}) + "\n")
        output.flush()
        return
    while line := await asyncio.to_thread(sys.stdin.readline):
        engine.diagnostic_request = None
        engine.diagnostic_stage = "decode"
        try:
            result = await asyncio.wait_for(engine.run(json.loads(line)), timeout=140)
        except Exception as exc:
            # No traceback, input echo, provider URLs or credentials in response.
            result = {"error": PUBLIC_ERROR, "internal_error": internal_error(
                exc, engine.diagnostic_request, engine.diagnostic_stage)}
        output.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()
    for task in engine.poster_tasks:
        task.cancel()
    await asyncio.gather(*engine.poster_tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
