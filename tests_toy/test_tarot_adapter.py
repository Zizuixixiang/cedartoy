import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tarot_adapter import (
    RITUAL_DISPLAY_NAME,
    RITUAL_REPOSITORY,
    TAROT_FLASH_MODEL,
    TAROT_PRO_MODEL,
    TarotCatalog,
    TarotError,
    TarotStore,
    TarotWeb,
    count_saved_tarot_sessions,
)


class FakeCatalog:
    cards = {
        "M00": ("愚者", "The Fool"),
        "M01": ("魔术师", "The Magician"),
    }

    def canonical(self, payload):
        question = payload.get("question")
        draws = payload.get("draws")
        if (
            not isinstance(question, str)
            or payload.get("spread_id") != "single"
            or not isinstance(draws, list)
            or len(draws) != 1
        ):
            raise TarotError(400, "invalid synthetic draw")
        draw = draws[0]
        if (
            draw.get("position") != 0
            or draw.get("card_id") not in self.cards
            or not isinstance(draw.get("reversed"), bool)
        ):
            raise TarotError(400, "invalid synthetic draw")
        zh, en = self.cards[draw["card_id"]]
        return {
            "question": question,
            "spread_id": "single",
            "spread": {
                "id": "single",
                "zh": "每日一牌",
                "en": "One Card",
                "count": 1,
                "desc": "synthetic",
                "slots": [{"label": "神谕", "hint": "synthetic"}],
            },
            "draws": [
                {
                    "position": 0,
                    "card_id": draw["card_id"],
                    "reversed": draw["reversed"],
                    "zh": zh,
                    "en": en,
                    "slot": "神谕",
                }
            ],
        }

    def messages(self, payload):
        canonical = self.canonical(payload)
        return canonical, [
            {"role": "system", "content": "synthetic system"},
            {"role": "user", "content": canonical["question"]},
        ]


class TarotStoreIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tarot-adapter-")
        self.now = [1_800_000_000.0]
        self.store = TarotStore(
            Path(self.temp_dir.name) / "tarot.db",
            clock=lambda: self.now[0],
            catalog=FakeCatalog(),
            public_base_url="https://toy.example",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def assertTarotStatus(self, expected, callback):
        with self.assertRaises(TarotError) as caught:
            callback()
        self.assertEqual(caught.exception.status, expected)

    def accept(self, session_id, human_id=101):
        invitation = self.store.invitation_for_human(session_id, human_id)
        return self.store.respond_invite(
            session_id,
            human_id,
            accept=True,
            csrf_token=invitation["csrf_token"],
        )

    def test_direct_human_session_is_never_visible_to_a_machine(self):
        session = self.store.create_direct_session(101)
        self.assertEqual(session["phase"], "accepted")
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(session["id"], 201, 101)
        )
        self.assertTarotStatus(
            404, lambda: self.store.bootstrap_for_human(session["id"], 102)
        )

    def test_public_save_count_only_counts_sessions_with_a_committed_draw(self):
        missing = Path(self.temp_dir.name) / "missing.db"
        self.assertEqual(count_saved_tarot_sessions(missing), 0)
        self.assertFalse(missing.exists())

        pending = self.store.create_invite(201, 101, "count_pending_1")
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 0)
        self.accept(pending["session_id"])
        pending_bootstrap = self.store.bootstrap_for_human(
            pending["session_id"], 101
        )
        self.store.stop_session(
            pending["session_id"], 101, pending_bootstrap["csrf_token"]
        )
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 0)

        stopped = self.store.create_invite(202, 102, "count_stopped_2")
        self.accept(stopped["session_id"], 102)
        stopped_csrf = self.store.bootstrap_for_human(
            stopped["session_id"], 102
        )["csrf_token"]
        self.store.commit_draw(
            stopped["session_id"],
            102,
            {
                "event_id": "count_draw_stopped",
                "question": "停止后仍计数",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M00", "reversed": False}
                ],
            },
            stopped_csrf,
        )
        self.store.stop_session(stopped["session_id"], 102, stopped_csrf)
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 1)

        returned = self.store.create_invite(203, 103, "count_returned_3")
        self.accept(returned["session_id"], 103)
        returned_csrf = self.store.bootstrap_for_human(
            returned["session_id"], 103
        )["csrf_token"]
        self.store.commit_draw(
            returned["session_id"],
            103,
            {
                "event_id": "count_draw_returned",
                "question": "返回后仍计数",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M01", "reversed": True}
                ],
            },
            returned_csrf,
        )
        self.store.reveal(
            returned["session_id"],
            103,
            {"event_id": "count_reveal_returned", "positions": [0]},
            returned_csrf,
        )
        revision = self.store.bootstrap_for_human(
            returned["session_id"], 103
        )["session"]["revision"]
        self.store.return_session(
            returned["session_id"], 103, revision, returned_csrf
        )
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 2)

    def test_two_human_machine_pairs_complete_concurrently_without_cross_reads(self):
        first = self.store.create_invite(201, 101, "concurrent_pair_1")
        second = self.store.create_invite(202, 102, "concurrent_pair_2")
        first_id, second_id = first["session_id"], second["session_id"]
        self.assertNotEqual(first_id, second_id)
        self.assertNotRegex(first_id, r"^\d+$")
        self.accept(first_id, 101)
        self.accept(second_id, 102)
        csrf_one = self.store.bootstrap_for_human(first_id, 101)["csrf_token"]
        csrf_two = self.store.bootstrap_for_human(second_id, 102)["csrf_token"]

        def complete(session_id, human_id, csrf, card_id, question, text):
            self.store.commit_draw(
                session_id,
                human_id,
                {
                    "event_id": "draw_concurrent",
                    "question": question,
                    "spread_id": "single",
                    "draws": [{
                        "position": 0,
                        "card_id": card_id,
                        "reversed": False,
                    }],
                },
                csrf,
            )
            self.store.reveal(
                session_id,
                human_id,
                {"event_id": "reveal_concurrent", "positions": [0]},
                csrf,
            )
            attempt = self.store.claim_reading(
                session_id, human_id, "read_concurrent", csrf
            )["attempt"]
            self.store.finish_reading(
                session_id,
                human_id,
                attempt["id"],
                state="succeeded",
                text=text,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = (
                executor.submit(
                    complete, first_id, 101, csrf_one, "M00",
                    "第一组私密问题", "第一组原引擎解读",
                ),
                executor.submit(
                    complete, second_id, 102, csrf_two, "M01",
                    "第二组私密问题", "第二组原引擎解读",
                ),
            )
            for job in jobs:
                job.result(timeout=5)

        first_result = self.store.ai_result(first_id, 201, 101)
        second_result = self.store.ai_result(second_id, 202, 102)
        self.assertEqual(first_result["question"], "第一组私密问题")
        self.assertEqual(first_result["reading"]["text"], "第一组原引擎解读")
        self.assertEqual(second_result["cards"][0]["card_id"], "M01")
        self.assertNotIn("第二组", str(first_result))
        self.assertNotIn("第一组", str(second_result))

        cross_reads = (
            lambda: self.store.ai_status(first_id, 202, 102),
            lambda: self.store.ai_result(first_id, 202, 102),
            lambda: self.store.ai_result(second_id, 201, 101),
            lambda: self.store.ai_result(first_id, 201, 102),
            lambda: self.store.bootstrap_for_human(first_id, 102),
            lambda: self.store.invitation_for_human(second_id, 101),
        )
        for cross_read in cross_reads:
            self.assertTarotStatus(404, cross_read)

    def test_account_purge_deletes_sessions_owned_by_either_actor(self):
        first = self.store.create_invite(301, 401, "purge_human_1")
        second = self.store.create_invite(302, 402, "purge_machine_2")
        self.assertEqual(self.store.delete_user_data(401), 1)
        self.assertEqual(self.store.delete_user_data(302), 1)
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(first["session_id"], 301, 401)
        )
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(second["session_id"], 302, 402)
        )

    def test_invite_is_idempotent_and_bound_to_exact_human_machine_pair(self):
        first = self.store.create_invite(201, 101, "request_0001")
        replay = self.store.create_invite(201, 101, "request_0001")
        self.assertEqual(first["session_id"], replay["session_id"])
        session_id = first["session_id"]

        self.assertTarotStatus(
            404, lambda: self.store.invitation_for_human(session_id, 102)
        )
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(session_id, 202, 101)
        )
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(session_id, 201, 102)
        )
        self.assertTarotStatus(
            403,
            lambda: self.store.respond_invite(
                session_id, 101, accept=True, csrf_token="wrong"
            ),
        )

        self.accept(session_id)
        self.assertEqual(
            self.store.bootstrap_for_human(session_id, 101)["session"]["phase"],
            "accepted",
        )

    def test_draw_reveal_and_result_do_not_cross_identity_boundaries(self):
        invite = self.store.create_invite(201, 101, "request_0002")
        session_id = invite["session_id"]
        accepted = self.accept(session_id)
        csrf = self.store.bootstrap_for_human(session_id, 101)["csrf_token"]
        draw = {
            "event_id": "draw_1",
            "question": "今天该看见什么？",
            "spread_id": "single",
            "draws": [{"position": 0, "card_id": "M00", "reversed": False}],
        }

        self.assertTarotStatus(
            404, lambda: self.store.commit_draw(session_id, 102, draw, csrf)
        )
        receipt = self.store.commit_draw(session_id, 101, draw, csrf)
        self.assertEqual(
            receipt, self.store.commit_draw(session_id, 101, draw, csrf)
        )
        conflict = {
            **draw,
            "draws": [{"position": 0, "card_id": "M01", "reversed": False}],
        }
        self.assertTarotStatus(
            409, lambda: self.store.commit_draw(session_id, 101, conflict, csrf)
        )
        self.assertTarotStatus(
            409,
            lambda: self.store.commit_draw(
                session_id, 101, {**draw, "event_id": "draw_2"}, csrf
            ),
        )
        self.assertTarotStatus(
            409, lambda: self.store.ai_result(session_id, 201, 101)
        )

        reveal = {"event_id": "reveal_1", "positions": [0]}
        self.assertTarotStatus(
            404, lambda: self.store.reveal(session_id, 102, reveal, csrf)
        )
        self.store.reveal(session_id, 101, reveal, csrf)
        attempt = self.store.claim_reading(
            session_id, 101, "reading_result", csrf
        )["attempt"]
        self.store.finish_reading(
            session_id,
            101,
            attempt["id"],
            state="failed",
            error_code="synthetic",
        )
        result = self.store.ai_result(session_id, 201, 101)
        self.assertTrue(result["untrusted"])
        self.assertEqual(
            result["reading"]["source"],
            f"{RITUAL_DISPLAY_NAME} · Gemini 3.5 Flash 塔罗专用池",
        )
        self.assertEqual(result["reading"]["model"], TAROT_FLASH_MODEL)
        self.assertEqual(result["question"], "今天该看见什么？")
        self.assertEqual(result["cards"][0]["card_id"], "M00")
        self.assertEqual(result["cards"][0]["zh"], "愚者")
        self.assertTarotStatus(
            404, lambda: self.store.ai_result(session_id, 202, 101)
        )
        self.assertTarotStatus(
            404, lambda: self.store.ai_result(session_id, 201, 102)
        )
        self.assertEqual(accepted["id"], session_id)

    def test_reading_action_id_never_creates_a_duplicate_attempt(self):
        invite = self.store.create_invite(201, 101, "request_0003")
        session_id = invite["session_id"]
        self.accept(session_id)
        csrf = self.store.bootstrap_for_human(session_id, 101)["csrf_token"]
        self.store.commit_draw(
            session_id,
            101,
            {
                "event_id": "draw_1",
                "question": "问题",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M00", "reversed": True}
                ],
            },
            csrf,
        )
        self.store.reveal(
            session_id, 101, {"event_id": "reveal_1", "positions": [0]}, csrf
        )
        first = self.store.claim_reading(
            session_id, 101, "reading_1", csrf, model=TAROT_PRO_MODEL
        )
        replay = self.store.claim_reading(
            session_id, 101, "reading_1", csrf, model=TAROT_FLASH_MODEL
        )
        self.assertTrue(first["claimed"])
        self.assertFalse(replay["claimed"])
        self.assertEqual(first["attempt"]["id"], replay["attempt"]["id"])
        self.assertEqual(first["attempt"]["model"], TAROT_PRO_MODEL)
        self.assertEqual(replay["attempt"]["model"], TAROT_PRO_MODEL)
        self.store.finish_reading(
            session_id,
            101,
            first["attempt"]["id"],
            state="unknown",
            error_code="timeout",
        )
        final_replay = self.store.claim_reading(
            session_id, 101, "reading_1", csrf
        )
        self.assertFalse(final_replay["claimed"])
        self.assertEqual(final_replay["attempt"]["state"], "unknown")
        self.assertEqual(final_replay["attempt"]["model"], TAROT_PRO_MODEL)

    def test_reading_rejects_any_model_outside_the_fixed_allowlist(self):
        invite = self.store.create_invite(201, 101, "request_bad_model")
        session_id = invite["session_id"]
        self.accept(session_id)
        csrf = self.store.bootstrap_for_human(session_id, 101)["csrf_token"]
        self.store.commit_draw(
            session_id,
            101,
            {
                "event_id": "draw_bad_model",
                "question": "问题",
                "spread_id": "single",
                "draws": [{"position": 0, "card_id": "M00", "reversed": False}],
            },
            csrf,
        )
        self.store.reveal(
            session_id,
            101,
            {"event_id": "reveal_bad_model", "positions": [0]},
            csrf,
        )
        self.assertTarotStatus(
            400,
            lambda: self.store.claim_reading(
                session_id, 101, "reading_bad_model", csrf, model="arbitrary-model"
            ),
        )

    def test_stop_or_process_restart_never_retries_or_overwrites_running_reading(self):
        invite = self.store.create_invite(201, 101, "request_stop_race")
        session_id = invite["session_id"]
        self.accept(session_id)
        csrf = self.store.bootstrap_for_human(session_id, 101)["csrf_token"]
        self.store.commit_draw(
            session_id,
            101,
            {
                "event_id": "draw_stop",
                "question": "问题",
                "spread_id": "single",
                "draws": [{"position": 0, "card_id": "M00", "reversed": False}],
            },
            csrf,
        )
        self.store.reveal(
            session_id, 101, {"event_id": "reveal_stop", "positions": [0]}, csrf
        )
        attempt = self.store.claim_reading(
            session_id, 101, "reading_stop", csrf
        )["attempt"]
        self.store.stop_session(session_id, 101, csrf)
        late = self.store.finish_reading(
            session_id,
            101,
            attempt["id"],
            state="succeeded",
            text="不得覆盖取消状态的迟到响应",
        )
        self.assertEqual(late["state"], "cancelled")
        self.assertEqual(late["text"], "")

        # A process restart converts orphaned running attempts to unknown so
        # clients observe rather than automatically submitting the paid call again.
        other = self.store.create_invite(202, 102, "request_restart")
        other_id = other["session_id"]
        self.accept(other_id, 102)
        other_csrf = self.store.bootstrap_for_human(other_id, 102)["csrf_token"]
        self.store.commit_draw(
            other_id,
            102,
            {
                "event_id": "draw_restart",
                "question": "另一个问题",
                "spread_id": "single",
                "draws": [{"position": 0, "card_id": "M01", "reversed": False}],
            },
            other_csrf,
        )
        self.store.reveal(
            other_id, 102,
            {"event_id": "reveal_restart", "positions": [0]}, other_csrf,
        )
        running = self.store.claim_reading(
            other_id, 102, "reading_restart", other_csrf
        )["attempt"]
        restarted = TarotStore(
            Path(self.temp_dir.name) / "tarot.db",
            clock=lambda: self.now[0],
            catalog=FakeCatalog(),
            public_base_url="https://toy.example",
        )
        observed = restarted.reading_for_human(
            other_id, 102, running["id"]
        )
        self.assertEqual(observed["state"], "unknown")
        self.assertEqual(observed["error_code"], "process_restart")

    def test_rejection_cooldown_and_rolling_invite_limit(self):
        rejected = self.store.create_invite(201, 101, "reject_01")
        invitation = self.store.invitation_for_human(rejected["session_id"], 101)
        self.store.respond_invite(
            rejected["session_id"],
            101,
            accept=False,
            csrf_token=invitation["csrf_token"],
        )
        with self.assertRaises(TarotError) as rejected_error:
            self.store.create_invite(201, 101, "reject_02")
        self.assertEqual(rejected_error.exception.status, 429)
        self.assertEqual(
            rejected_error.exception.message,
            "人类拒绝后 24 小时内不能再次邀请，请等待冷却结束",
        )
        self.now[0] += 86_401
        self.store.create_invite(201, 101, "limit_001")
        self.store.create_invite(201, 101, "limit_002")
        self.store.create_invite(201, 101, "limit_003")
        with self.assertRaises(TarotError) as limit_error:
            self.store.create_invite(201, 101, "limit_004")
        self.assertEqual(limit_error.exception.status, 429)
        self.assertEqual(
            limit_error.exception.message,
            "主动邀请 24 小时内最多 3 次，请等待额度恢复；"
            "人类想主动占问可直接从 CedarToy 首页进入塔罗",
        )

    def test_new_invite_schema_has_no_client_claimed_exemption_column(self):
        with self.store._connect() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(tarot_invites)")
            }
        self.assertNotIn("human_requested", columns)

    def test_legacy_exemption_column_is_retained_but_never_bypasses_limit(self):
        with self.store._connect() as conn:
            conn.execute(
                "ALTER TABLE tarot_invites "
                "ADD COLUMN human_requested INTEGER NOT NULL DEFAULT 0"
            )
        restarted = TarotStore(
            self.store.db_path,
            clock=lambda: self.now[0],
            catalog=FakeCatalog(),
        )
        for index in range(3):
            invite = restarted.create_invite(201, 101, f"legacy_limit_{index}")
            with restarted._connect() as conn:
                conn.execute(
                    "UPDATE tarot_invites SET human_requested=1 WHERE session_id=?",
                    (invite["session_id"],),
                )
        with self.assertRaises(TarotError) as caught:
            restarted.create_invite(201, 101, "legacy_limit_blocked")
        self.assertEqual(caught.exception.status, 429)


class TarotUpstreamAndUiTests(unittest.TestCase):
    def test_raw_upstream_html_cannot_bypass_the_authenticated_session_shell(self):
        web = TarotWeb()
        with self.assertRaises(TarotError) as caught:
            web.static_file("index.html")
        self.assertEqual(caught.exception.status, 404)
        asset, mime = web.static_file("css/style.css")
        self.assertTrue(asset.is_file())
        self.assertEqual(mime, "text/css")

    def test_real_upstream_catalog_is_used_for_facts_and_prompt(self):
        catalog = TarotCatalog()
        canonical, messages = catalog.messages(
            {
                "question": "今天该看见什么？",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M00", "reversed": False}
                ],
            }
        )
        self.assertEqual(canonical["spread"]["zh"], "每日一牌")
        self.assertEqual(canonical["draws"][0]["zh"], "愚者")
        self.assertTrue(messages)

    def test_managed_page_keeps_upstream_ui_without_platform_shell(self):
        web = TarotWeb()
        upstream = web.index_path.read_text(encoding="utf-8")
        page = web.ritual_index("A" * 32).decode("utf-8")
        self.assertIn('<base href="/tarot/static/">', page)
        self.assertIn('<link rel="stylesheet" href="./fonts/fonts.css">', page)
        self.assertIn('<link rel="stylesheet" href="./css/style.css">', page)
        self.assertIn('<script type="module" src="./js/main.js"></script>', page)
        self.assertIn('/tarot/static/platform/managed-core.v1.js', page)
        self.assertIn('/tarot/static/platform/managed-ui.v2.js', page)
        self.assertIn('/tarot/static/platform/managed-ui.v2.css', page)
        self.assertNotIn('/tarot/static/platform/managed-ui.v1.js', page)
        self.assertNotIn('/tarot/static/platform/managed-ui.v1.css', page)
        self.assertIn(f"<title>{RITUAL_DISPLAY_NAME}</title>", page)
        self.assertIn('id="companion-config"', page)
        self.assertIn('id="providerOrb"', page)
        self.assertIn("本站暂仅支持所提供的模型。如需自行配置模型，请克隆", page)
        self.assertIn(f'href="{RITUAL_REPOSITORY}"', page)
        self.assertNotIn("导入本机 DSH", page)
        self.assertNotIn("手动填写模型 ID", page)
        self.assertNotIn("Base URL", page)
        self.assertNotIn("API Key", page)
        self.assertIn('class="mode-opt photo-opt"', page)
        self.assertEqual(page.count("<style"), upstream.count("<style"))
        self.assertNotIn("CedarToy", page)
        main_js = web.static_file("js/main.js")[0].read_text(encoding="utf-8")
        self.assertIn("mkBtn('返回聊天', 'ghost small'", main_js)
        self.assertIn("mkBtn('停止本次', 'ghost small'", main_js)
        self.assertIn(
            "label.textContent = `${p.label}${m ? ' · ' + m : ''}`;",
            main_js,
        )
        managed_core = web.static_file("platform/managed-core.v1.js")[0].read_text(
            encoding="utf-8"
        )
        self.assertNotIn("/api/dsh", managed_core)
        self.assertNotIn("/api/models", managed_core)
        self.assertNotIn("/api/chat", managed_core)
        self.assertNotIn("apiKey", managed_core)
        self.assertNotIn("baseURL", managed_core)
        managed_ui_path, managed_ui_mime = web.static_file(
            "platform/managed-ui.v2.js"
        )
        self.assertEqual(managed_ui_mime, "text/javascript")
        managed_ui = managed_ui_path.read_text(encoding="utf-8")
        self.assertIn("本次已结束，记录已保留。", managed_ui)
        self.assertIn("managed-companion-settings-hidden", managed_ui)
        self.assertTrue(web.static_file("platform/managed-ui.v1.js")[0].is_file())
        for platform_marker in (
            "cedar-platform-bar",
            "CEDAR TOY",
            "pixel-logo",
            "pixel-btn",
            "cedar-source",
            "cedartoy-platform-style",
            "cedartoy-managed-style",
            "来源与作者",
            "作者：林默Moon",
            "小红书号：427689021",
            "GitHub 原项目",
            "CedarToy · Tarot 专业解读",
            f"{RITUAL_DISPLAY_NAME} ISC License",
        ):
            with self.subTest(platform_marker=platform_marker):
                self.assertNotIn(platform_marker, page)

    def test_homepage_tarot_card_uses_live_save_count_placeholder(self):
        web = TarotWeb()
        homepage = (Path(__file__).resolve().parents[1] / "index.html").read_text(
            encoding="utf-8"
        )
        rendered = web.homepage_index(homepage).decode("utf-8")
        tarot_card = rendered.split('id: "tarot"', 1)[1].split('id: "fishing"', 1)[0]
        self.assertIn('metricLabel: "存档数"', tarot_card)
        self.assertIn('metric: "--"', tarot_card)
        self.assertNotIn('metricLabel: "牌阵数"', tarot_card)
        self.assertNotIn('metric: "00005"', tarot_card)


if __name__ == "__main__":
    unittest.main()
