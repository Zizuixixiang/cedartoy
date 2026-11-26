"""Offline HTTP admin + real SQLite router integration, no network completions."""
import asyncio
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from jose import jwt

BACKEND = Path(__file__).resolve().parents[1]
# Other resilience tests supply module-level database/auth stubs. Exercise
# the real modules here without replacing the modules those tests imported.
with patch.object(sys, "path", [str(BACKEND), *sys.path]), patch.dict(sys.modules):
    for name in ("api_costs", "auth_utils", "database", "judge", "models", "pool_order",
                 "routers", "routers.admin", "utils"):
        sys.modules.pop(name, None)
    import api_costs
    import auth_utils
    import database
    import judge
    from routers import admin


class PoolPriceOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for module, name in ((database, "account"), (api_costs, "ledger")):
            p = patch.object(module, "DB_PATH", Path(temp.name) / (name + ".db"))
            p.start()
            self.addCleanup(p.stop)
        await database.init_db()
        api_costs.initialize()
        judge.reset_fail_counts()
        self.app = app = FastAPI()
        app.include_router(admin.router)
        app.dependency_overrides[admin.admin_player] = lambda: {"id": 1}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture")
        self.addAsyncCleanup(self.client.aclose)
        self.configs = []

    async def config(self, name, purpose, rank=0, enabled=1, **options):
        cfg = dict(name=name, model="model", purpose=purpose, priority=rank, enabled=enabled,
                   api_key="fixture", api_url=f"https://node{len(self.configs)}.invalid/v1")
        cfg.update(options)
        cols = ",".join(cfg)
        cfg["id"] = await database.execute(
            f"INSERT INTO judge_api_configs({cols}) VALUES(?,?,?,?,?,?,?)", cfg.values())
        self.configs.append(cfg)
        return cfg

    def price(self, cfg, price, **kwargs):
        rule = dict(mode="request", per_request=price) if price is not None else dict(
            mode="tokens", input="1", output="2", cache="0.10")
        api_costs.save_price(dict(config_id=cfg["id"], model=cfg["model"], **rule, **kwargs), self.configs)

    def samples(self, cfg, pool, counts, n=10, age=0):
        for _ in range(n):
            event = api_costs.make_event(cfg, pool, time.time() - age)
            event.update(status="success", http_status=200, counts=(*counts, 0))
            api_costs.write_event(event)

    async def preview(self, pool, direction="asc"):
        result = await self.client.get(f"/admin/api-pools/{pool}/price-order", params={"direction": direction})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def apply(self, preview, status=200):
        result = await self.client.put(f"/admin/api-pools/{preview['pool']}/price-order", json={
            "direction": preview["direction"], "revision": preview["revision"], "cutoff": preview["usage"]["cutoff"]})
        self.assertEqual(result.status_code, status, result.text)

    async def attempts(self, pool, expected):
        calls = []

        async def completion(cfg, *args, **kwargs):
            calls.append(cfg["name"])
            if len(calls) < len(expected):
                raise httpx.ReadTimeout("fixture timeout")
            return httpx.Response(200, request=httpx.Request("POST", cfg["api_url"]),
                                  json={"choices": [{"message": {"content": "ok"}}]})

        with patch.object(judge, "_post_chat_completion", completion):
            await judge._chat([{"role": "user", "content": "fixture"}], pool=pool)
        self.assertEqual(calls, expected)

    async def test_independent_shared_order_reaches_failover_and_npc_tiers(self):
        a = await self.config("按次", "all", 0)
        b = await self.config("按量", "all", 0)
        c = await self.config("塔罗一", "tarot", 0)
        d = await self.config("塔罗二", "tarot", 0)
        legacy = await self.config("兼容", "npc")
        self.price(a, "0.003")
        self.price(b, None)
        self.price(c, "0.02")
        self.price(d, "0.01")
        self.samples(a, "judge", (1000, 100, 500))
        self.samples(a, "npc_speech", (1500, 500, 0))
        soup, npc, tarot = [await self.preview(p, direction) for p, direction in
                            (("soup", "asc"), ("npc", "desc"), ("tarot", "asc"))]
        soup_cost = next(r["cost"] for r in soup["rows"] if r["id"] == b["id"])
        npc_cost = next(r["cost"] for r in npc["rows"] if r["id"] == b["id"])
        self.assertEqual(Decimal(soup_cost), Decimal("0.00075"))
        self.assertEqual(Decimal(npc_cost), Decimal("0.0025"))
        self.assertFalse(await database.fetch_all("SELECT * FROM settings WHERE key LIKE 'api_pool_order.%'"))
        for view in (npc, tarot, soup):
            self.assertTrue(view["changed"])
            await self.apply(view)
        self.assertEqual({r["priority"] for r in await database.fetch_all("SELECT priority FROM judge_api_configs")}, {0})
        await self.attempts("judge", ["按量", "按次"])
        await self.attempts("hint", ["按量", "按次"])
        await self.attempts("npc_speech", ["按次", "按量"])
        await self.attempts("tarot", ["塔罗二", "塔罗一"])
        await self.config("专用", "npc_decision", 99)
        await self.attempts("npc_decision", ["专用"])
        await self.attempts("npc_speech", ["按次"])
        await database.execute("UPDATE judge_api_configs SET enabled=0 WHERE purpose IN ('all','npc_decision')")
        await self.attempts("npc_decision", [legacy["name"]])

    async def test_pinned_unknown_disabled_ties_and_immutable_quotes(self):
        rows = [await self.config(n, "both", i) for i, n in enumerate(("贵", "未知", "便宜甲", "便宜乙"))]
        disabled = await self.config("停用", "judge", -5, enabled=0)
        for cfg, price in ((rows[0], "1"), (rows[2], "0"), (rows[3], "0"), (disabled, "0")):
            self.price(cfg, price)
        with closing(api_costs.connect()) as ledger:
            before = list(ledger.iterdump())
            preview = await self.preview("soup")
            self.assertEqual([r["name"] for r in preview["rows"]], ["便宜甲", "未知", "便宜乙", "贵", "停用"])
            self.assertIsNone(preview["rows"][1]["cost"])
            self.assertEqual(preview["rows"][-1]["before"], preview["rows"][-1]["after"])
            await self.apply(preview)
            self.assertEqual(list(ledger.iterdump()), before)
        await database.execute("UPDATE judge_api_configs SET enabled=0 WHERE name='未知'")
        await self.apply(await self.preview("soup"))
        judge._rr_index["judge"].clear()
        await self.attempts("judge", ["便宜甲"])
        await self.attempts("judge", ["便宜乙"])

    async def test_incomplete_old_wrong_pool_conflict_and_exact_model(self):
        cfg = await self.config("甲 · NPC", "npc")
        other = await self.config("甲 · 塔罗", "tarot", api_url=cfg["api_url"])
        self.price(cfg, None)
        row = (await self.preview("npc"))["rows"][0]
        self.assertIn("冲突", row["reason"])
        self.price(other, None)
        self.samples(cfg, "npc_decision", (100, 20, 10), n=9)
        self.samples(cfg, "npc_decision", (100, 20, 10), n=1, age=31*86400)
        self.samples(cfg, "npc_decision", (100, 20, None), n=1)
        self.samples(other, "tarot", (100, 20, 10))
        self.samples(cfg, "test", (999, 99, 99))
        preview = await self.preview("npc")
        self.assertEqual(preview["usage"]["count"], 9)
        self.assertIsNone(preview["rows"][0]["cost"])
        self.assertIn("不足", preview["rows"][0]["reason"])
        await database.execute("UPDATE judge_api_configs SET model='other' WHERE id=?", (cfg["id"],))
        self.assertIn("暂无", (await self.preview("npc"))["rows"][0]["reason"])

    async def test_sample_cap_cutoff_and_unusable_rules(self):
        cfg = await self.config("计量", "tarot")
        fixed = await self.config("计次", "tarot")
        self.price(cfg, None)
        self.price(fixed, "0.01")
        self.samples(cfg, "tarot", (100, 10, 20), n=1, age=60)
        self.samples(cfg, "tarot", (900, 20, 100), n=api_costs.SAMPLE_LIMIT)
        view = await self.preview("tarot")
        self.assertEqual(view["usage"]["count"], api_costs.SAMPLE_LIMIT)
        self.assertEqual(list(map(Decimal, view["usage"]["means"])), [900, 20, 100])
        self.samples(cfg, "tarot", (1500, 30, 200), n=1)
        await self.apply(view)  # a later call belongs to the next preview
        with closing(api_costs.connect()) as db, db:
            db.execute("INSERT INTO prices(config_id,model,effective,source,rule) VALUES(?,?,?,?,?)",
                       (cfg["id"], cfg["model"], time.time(), "user", '{"mode":"other"}'))
        row = next(r for r in (await self.preview("tarot"))["rows"] if r["id"] == cfg["id"])
        self.assertIsNone(row["cost"])
        self.assertIn("不支持", row["reason"])

    async def test_temporarily_unavailable_known_node_keeps_its_slot(self):
        a, b, c = [await self.config(name, "both", rank) for rank, name in enumerate("甲乙丙")]
        for cfg, price in ((a, "3"), (b, "1"), (c, "2")):
            self.price(cfg, price)
        view = await self.preview("soup")
        for _ in range(judge.FAIL_LIMIT):
            judge._record_failure(b["id"], TimeoutError(), was_probe=False)
        await self.apply(view, 409)
        held = await self.preview("soup")
        self.assertEqual([r["name"] for r in held["rows"]], ["丙", "乙", "甲"])
        self.assertEqual(held["rows"][1]["hold_reason"], "节点冷却中")
        self.assertEqual(held["rows"][1]["cost"], "1")
        await self.apply(held)

    async def test_missing_ledger_preview_does_not_create_it(self):
        await self.config("新渠道", "both")
        missing = api_costs.DB_PATH.with_name("absent.db")
        with patch.object(api_costs, "DB_PATH", missing):
            result = await self.preview("soup")
        self.assertFalse(missing.exists())
        self.assertFalse(result["changed"])
        self.assertIsNone(result["rows"][0]["cost"])

    async def test_stale_or_invalid_apply_never_partly_saves(self):
        a, b = await self.config("一", "hint"), await self.config("二", "hint")
        self.price(a, "1")
        self.price(b, "2")
        view = await self.preview("soup")
        self.price(a, "1")  # New version, even if numerical amount is unchanged.
        await self.apply(view, 409)
        view = await self.preview("soup")
        await database.execute("UPDATE judge_api_configs SET enabled=0 WHERE id=?", (b["id"],))
        await self.apply(view, 409)
        self.assertFalse(await database.fetch_one("SELECT * FROM settings WHERE key='api_pool_order.soup'"))
        await database.execute("UPDATE judge_api_configs SET enabled=1 WHERE id=?", (b["id"],))
        async def put(view):
            return await self.client.put("/admin/api-pools/soup/price-order", json={
                "direction": view["direction"], "cutoff": view["usage"]["cutoff"], "revision": view["revision"]})
        views = [await self.preview("soup", direction) for direction in ("asc", "desc")]
        results = await asyncio.gather(*(put(view) for view in views))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual((await self.client.get("/admin/api-pools/bogus/price-order")).status_code, 422)
        self.assertEqual((await self.client.get("/admin/api-pools/soup/price-order?direction=x")).status_code, 422)
        with patch.object(api_costs, "pool_estimates", side_effect=sqlite3.OperationalError("fixture")):
            self.assertEqual((await self.client.get("/admin/api-pools/soup/price-order")).status_code, 503)

    async def put_quote(self, pool, row, status=200, **fields):
        body = dict(model=row["model"], group_revision=row["group_revision"],
                    config_ids=[q["id"] for q in row["quotes"]], price_versions=row["versions"], mode="request", per_request="0")
        body.update(fields)
        response = await self.client.put(f"/admin/api-pools/{pool}/configs/{row['id']}/price", json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response

    async def test_quote_editor_group_model_key_scope_and_history(self):
        a = await self.config("商家 · 裁判", "judge", api_key="private-key-A")
        url = a["api_url"]
        npc = await self.config("商家 · NPC", "npc", api_url=url, api_key="private-key-A")
        tarot = await self.config("商家 · 塔罗", "tarot", api_url=url, api_key="private-key-A")
        b = await self.config("商家 · 裁判", "judge", api_url=url, api_key="private-key-A", model="second")
        other = await self.config("商家 · 裁判", "judge", api_url=url, api_key="private-key-B")
        self.price(other, "9")
        self.samples(a, "judge", (1000, 200, 300))
        with closing(api_costs.connect()) as db:
            events = list(db.execute("SELECT * FROM events"))
            prior = list(db.execute("SELECT * FROM prices"))
        preview = await self.preview("soup")
        row = next(r for r in preview["rows"] if r["id"] == a["id"])
        self.assertNotIn("private-key", str(preview))
        self.assertNotIn("api_key", str(preview))
        self.assertIsNone(row["quotes"][0]["rule"])
        self.assertEqual([q["id"] for q in row["quotes"]], [a["id"], npc["id"], tarot["id"]])
        await self.put_quote("soup", row, 409, config_ids=[a["id"], other["id"]])
        await self.put_quote("soup", row)
        with closing(api_costs.connect()) as db:
            self.assertFalse(db.execute("SELECT * FROM prices WHERE config_id=?", (b["id"],)).fetchall())
        for pool, cfg in (("soup", a), ("npc", npc), ("tarot", tarot)):
            result = next(r for r in (await self.preview(pool))["rows"] if r["id"] == cfg["id"])
            self.assertEqual((result["cost"], result["source"]), ("0", "user"))
        await self.put_quote("soup", row, 409, per_request="1")
        fresh = (await self.preview("tarot"))["rows"][0]
        await self.put_quote("tarot", fresh, mode="tokens", input="1", output="2", cache="0.01", reference_only=True)
        rows = {r["id"]: r for r in (await self.preview("soup"))["rows"]}
        self.assertIsNone(rows[b["id"]]["cost"])
        self.assertEqual(rows[other["id"]]["cost"], "9")
        self.assertEqual(Decimal(rows[a["id"]]["cost"]), Decimal("0.001103"))
        self.assertTrue(rows[a["id"]]["reference_only"])
        npc_row = (await self.preview("npc"))["rows"][0]
        self.assertIn("不足", npc_row["reason"])
        report = api_costs.report("today", api_costs.read_configs(database.DB_PATH))
        price = next(p for p in report["prices"] if p["config_id"] == a["id"] and p["model"] == a["model"])
        self.assertEqual(price["price_versions"], npc_row["versions"])
        self.assertEqual(price["price"]["cache"], "0.01")
        api_costs.save_price(dict(config_id=a["id"], config_ids=price["config_ids"],
                                 price_versions=price["price_versions"], model=a["model"],
                                 mode="request", per_request="0.03"), self.configs)
        await self.put_quote("npc", npc_row, 409)
        self.assertEqual((await self.preview("npc"))["rows"][0]["cost"], "0.03")
        self.assertFalse(await database.fetch_all("SELECT * FROM settings WHERE key LIKE 'api_pool_order.%'"))
        with closing(api_costs.connect()) as db:
            self.assertEqual(list(db.execute("SELECT * FROM events")), events)
            self.assertEqual(list(db.execute("SELECT * FROM prices ORDER BY id LIMIT ?", (len(prior),))), prior)

    async def test_quote_scope_tracks_historical_model_and_new_alias(self):
        a = await self.config("商家", "judge", model="current-A")
        b = await self.config("商家 · NPC", "npc", model="current-B", api_url=a["api_url"])
        row = (await self.preview("npc"))["rows"][0]
        self.assertEqual([q["id"] for q in row["quotes"]], [b["id"]])
        # Main admin routes by merchant representative, even if its current
        # model differs from the catalog entry selected for editing.
        report = api_costs.report("today", self.configs)
        price = next(p for p in report["prices"] if p["model"] == b["model"])
        self.assertEqual((price["config_id"], price["config_ids"]), (a["id"], [b["id"]]))
        api_costs.save_price(dict(config_id=price["config_id"], config_ids=price["config_ids"],
                                 price_versions=price["price_versions"], model=b["model"],
                                 mode="request", per_request="0.10"), self.configs)
        fresh = (await self.preview("npc"))["rows"][0]
        self.samples(dict(a, model=b["model"]), "judge", (10, 1, 0), n=1)
        await self.put_quote("npc", fresh, 409)
        fresh = (await self.preview("npc"))["rows"][0]
        self.assertEqual([(q["id"], q["historical"]) for q in fresh["quotes"]],
                         [(a["id"], True), (b["id"], False)])
        self.assertIn("冲突", fresh["reason"])
        await self.put_quote("npc", fresh)
        self.assertIsNone((await self.preview("soup"))["rows"][0]["cost"])
        fresh = (await self.preview("npc"))["rows"][0]
        await self.config("商家 · 塔罗", "tarot", model=b["model"], api_url=a["api_url"])
        await self.put_quote("npc", fresh, 409)

    async def test_quote_editor_auth_identity_and_atomic_errors(self):
        cfg = await self.config("商家", "judge")
        row = (await self.preview("soup"))["rows"][0]
        for changes, status in ((dict(model="other"), 409), (dict(price_versions={}), 409),
                                (dict(price_versions={str(cfg["id"]): False}), 409),
                                (dict(per_request=""), 400), (dict(reference_only=True), 400),
                                (dict(player_id=9), 400)):
            await self.put_quote("soup", row, status, **changes)
        await self.put_quote("tarot", row, 409)
        await database.execute("UPDATE judge_api_configs SET api_key='rotated' WHERE id=?", (cfg["id"],))
        await self.put_quote("soup", row, 409)
        row = (await self.preview("soup"))["rows"][0]
        with patch.object(api_costs, "save_price", side_effect=sqlite3.OperationalError("private-key")):
            response = await self.put_quote("soup", row, 503)
            self.assertNotIn("private-key", response.text)
        self.app.dependency_overrides.clear()
        secret = patch.object(auth_utils, "SECRET_KEY", "offline-price-test")
        secret.start()
        self.addCleanup(secret.stop)
        await self.put_quote("soup", row, 401)
        uid = await database.execute("INSERT INTO players(username) VALUES ('reader')")
        self.client.headers["Authorization"] = "Bearer " + jwt.encode(
            {"player_id": uid, "is_admin": True}, "offline-price-test", algorithm="HS256")
        self.assertEqual((await self.client.get("/admin/api-pools/soup/price-order")).status_code, 403)
        await self.put_quote("soup", row, 403)
        with closing(api_costs.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM prices").fetchone()[0], 0)
        await database.execute("UPDATE players SET is_admin=1 WHERE id=?", (uid,))
        await self.put_quote("soup", row)

    async def test_first_edit_of_unseeded_default(self):
        cfg = await self.config("巧克力 · NPC", "npc", model="gemini-3.1-flash-lite")
        await database.execute("UPDATE judge_api_configs SET id=31 WHERE id=?", (cfg["id"],))
        row = (await self.preview("npc"))["rows"][0]
        self.assertEqual((row["version"], row["source"], row["quotes"][0]["rule"]["cache"]), (0, "default", "0.01"))
        await self.put_quote("npc", row)
        await self.put_quote("npc", row, 409)
        with closing(api_costs.connect()) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT source FROM prices ORDER BY id")], ["default", "user"])

    async def test_inflight_failure_stays_on_the_node_actually_called(self):
        soup = await self.config("裁判", "judge")
        await self.config("别名", "npc", api_url=soup["api_url"])
        started, release = asyncio.Event(), asyncio.Event()

        async def pending(*args, **kwargs):
            started.set()
            await release.wait()
            raise httpx.ReadTimeout("offline fixture")

        with patch.object(judge, "_post_chat_completion", pending):
            call = asyncio.create_task(judge._chat([{"role": "user", "content": "fixture"}], pool="judge"))
            try:
                await asyncio.wait_for(started.wait(), 2)
                update = await self.client.put(f"/admin/api-configs/{soup['id']}",
                                               json=dict(soup, api_key="replacement"))
                self.assertEqual(update.status_code, 200)
                await self.client.get("/admin/api-configs")
            finally:
                release.set()
                with self.assertRaises(HTTPException):
                    await call
        rows = (await self.client.get("/admin/api-configs")).json()
        self.assertEqual({r["name"]: r["consecutive_failures"] for r in rows}, {"裁判": 0, "别名": 1})

    async def test_shared_node_snapshot_edit_delete_draft_and_recovery(self):
        root = await self.config("汤", "both")
        url = root["api_url"]
        npc = await self.config("NPC", "npc", api_url=url + "/chat/completions/")
        tarot = await self.config("牌", "tarot", api_url=url)
        disabled = await self.config("停用别名", "hint", api_url=url, enabled=0)
        await self.config("另一密钥", "all", api_url=url, api_key="distinct")
        await self.config("另一模型", "all", api_url=url, model="different")
        await self.config("另一路径", "all", api_url=url.upper().replace("HTTPS", "https"))
        clock = [1000]
        with patch.object(judge, "_now", side_effect=lambda: clock[0]):
            for _ in range(judge.FAIL_LIMIT):
                judge._record_failure(tarot["id"], TimeoutError(), was_probe=False)
            async def assert_nodes(status):
                result = await self.client.get("/admin/api-configs")
                self.assertEqual(result.status_code, 200)
                data = result.json()
                common = [r for r in data if r["name"] in ("汤", "NPC", "牌", "停用别名")]
                for field in ("node_status", "cooldown_remaining_seconds", "consecutive_failures",
                              "last_success_at", "last_error", "probe_in_flight"):
                    self.assertEqual(len({r[field] for r in common}), 1, field)
                self.assertEqual(common[0]["node_status"], status)
                self.assertTrue(all(r["node_status"] == "healthy" for r in data if r not in common))
                return common
            shared = await assert_nodes("cooling")
            self.assertEqual(len(shared), 4)
            self.assertEqual(shared[-1]["enabled"], 0)
            judge.reset_fail_counts(npc["id"])
            await assert_nodes("cooling")
            result = await self.client.put(f"/admin/api-configs/{npc['id']}", json=dict(npc, priority=42))
            self.assertEqual(result.status_code, 200)
            with patch.object(admin, "test_config", AsyncMock(return_value={"success": True})):
                await self.client.post("/admin/api-configs/test", json={"config_id": root["id"], "model": "draft"})
            await assert_nodes("cooling")
            await self.client.delete(f"/admin/api-configs/{disabled['id']}")
            await assert_nodes("cooling")
            clock[0] += judge.COOLDOWN_SECONDS[0]
            await assert_nodes("half_open")
            self.assertTrue(judge._claim_attempt(root["id"]))
            self.assertTrue((await assert_nodes("half_open"))[0]["probe_in_flight"])
            with patch.object(admin, "test_config", AsyncMock(return_value={"success": True})):
                await self.client.post(f"/admin/api-configs/{root['id']}/test")
            self.assertIsNotNone((await assert_nodes("healthy"))[0]["last_success_at"])
