import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tarot_adapter import (
    RITUAL_DISPLAY_NAME,
    COVE_REPOSITORY,
    MAX_INVITE_QUESTION,
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

        pending = self.store.create_invite(201, 101, "count_pending_1", "待确认问题")
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 0)
        self.accept(pending["session_id"])
        pending_bootstrap = self.store.bootstrap_for_human(
            pending["session_id"], 101
        )
        self.store.stop_session(
            pending["session_id"], 101, pending_bootstrap["csrf_token"]
        )
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 0)

        stopped = self.store.create_invite(202, 102, "count_stopped_2", "停止问题")
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

        returned = self.store.create_invite(203, 103, "count_returned_3", "返回问题")
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

    def test_history_lists_only_owned_saved_draws_and_returns_safe_detail(self):
        empty = self.store.create_direct_session(101)
        self.assertEqual(self.store.history_for_human(101)["items"], [])
        self.store.stop_session(
            empty["id"],
            101,
            self.store.bootstrap_for_human(empty["id"], 101)["csrf_token"],
        )

        first = self.store.create_invite(201, 101, "history_owned_1", "历史问题一")
        self.accept(first["session_id"], 101)
        first_csrf = self.store.bootstrap_for_human(
            first["session_id"], 101
        )["csrf_token"]
        self.store.commit_draw(
            first["session_id"],
            101,
            {
                "event_id": "history_draw_1",
                "question": "<img src=x onerror=alert(1)> 我的私密问题",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M00", "reversed": False}
                ],
            },
            first_csrf,
        )
        self.store.reveal(
            first["session_id"],
            101,
            {"event_id": "history_reveal_1", "positions": [0]},
            first_csrf,
        )
        attempt = self.store.claim_reading(
            first["session_id"], 101, "history_read_1", first_csrf
        )["attempt"]
        self.store.finish_reading(
            first["session_id"],
            101,
            attempt["id"],
            state="succeeded",
            text="<script>不能作为 HTML 执行</script>",
        )

        second_owned = self.store.create_invite(202, 101, "history_owned_2", "历史问题二")
        self.accept(second_owned["session_id"], 101)
        second_csrf = self.store.bootstrap_for_human(
            second_owned["session_id"], 101
        )["csrf_token"]
        self.store.commit_draw(
            second_owned["session_id"],
            101,
            {
                "event_id": "history_draw_owned_2",
                "question": "第二条本人记录",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M01", "reversed": True}
                ],
            },
            second_csrf,
        )

        other = self.store.create_invite(201, 102, "history_other_2", "其他问题")
        self.accept(other["session_id"], 102)
        other_csrf = self.store.bootstrap_for_human(
            other["session_id"], 102
        )["csrf_token"]
        self.store.commit_draw(
            other["session_id"],
            102,
            {
                "event_id": "history_draw_2",
                "question": "另一位人类的问题",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M01", "reversed": True}
                ],
            },
            other_csrf,
        )

        owned = self.store.history_for_human(101, limit=1)
        self.assertEqual(len(owned["items"]), 1)
        self.assertEqual(owned["next_offset"], 1)
        second_page = self.store.history_for_human(101, offset=1, limit=1)
        listed = owned["items"] + second_page["items"]
        self.assertEqual(
            {item["session_id"] for item in listed},
            {first["session_id"], second_owned["session_id"]},
        )
        self.assertTrue(
            any("<img" in item["question_summary"] for item in listed)
        )
        self.assertNotIn("human_user_id", str(listed))
        with self.store._connect() as conn:
            readings_before = conn.execute(
                "SELECT COUNT(*) FROM tarot_readings"
            ).fetchone()[0]
            revision_before = conn.execute(
                "SELECT revision FROM tarot_sessions WHERE id=?",
                (first["session_id"],),
            ).fetchone()[0]
        detail = self.store.history_detail_for_human(first["session_id"], 101)
        self.assertEqual(detail["spread"]["zh"], "每日一牌")
        self.assertEqual(detail["cards"][0]["zh"], "愚者")
        self.assertEqual(
            detail["reading"]["text"], "<script>不能作为 HTML 执行</script>"
        )
        self.assertNotIn("id", detail["reading"])
        self.assertNotIn("error_code", detail["reading"])
        self.assertNotIn("ai_user_id", detail)
        with self.store._connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tarot_readings").fetchone()[0],
                readings_before,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT revision FROM tarot_sessions WHERE id=?",
                    (first["session_id"],),
                ).fetchone()[0],
                revision_before,
            )
        self.assertTarotStatus(
            404,
            lambda: self.store.history_detail_for_human(first["session_id"], 102),
        )
        self.assertTarotStatus(
            404,
            lambda: self.store.history_detail_for_human(other["session_id"], 101),
        )

    def test_history_delete_cascades_and_late_reading_cannot_resurrect(self):
        invite = self.store.create_invite(201, 101, "history_delete_1", "删除问题")
        session_id = invite["session_id"]
        self.accept(session_id, 101)
        csrf = self.store.bootstrap_for_human(session_id, 101)["csrf_token"]
        self.store.commit_draw(
            session_id,
            101,
            {
                "event_id": "history_delete_draw",
                "question": "删除中的解读",
                "spread_id": "single",
                "draws": [
                    {"position": 0, "card_id": "M00", "reversed": False}
                ],
            },
            csrf,
        )
        self.store.reveal(
            session_id,
            101,
            {"event_id": "history_delete_reveal", "positions": [0]},
            csrf,
        )
        attempt = self.store.claim_reading(
            session_id, 101, "history_delete_read", csrf
        )["attempt"]
        self.assertTarotStatus(
            403,
            lambda: self.store.delete_history_session(
                session_id,
                101,
                csrf_session_id=session_id,
                csrf_token="wrong-csrf",
            ),
        )
        other_current = self.store.create_direct_session(102)
        other_csrf = self.store.bootstrap_for_human(
            other_current["id"], 102
        )["csrf_token"]
        self.assertTarotStatus(
            404,
            lambda: self.store.delete_history_session(
                session_id,
                102,
                csrf_session_id=other_current["id"],
                csrf_token=other_csrf,
            ),
        )
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 1)
        deleted = self.store.delete_history_session(
            session_id,
            101,
            csrf_session_id=session_id,
            csrf_token=csrf,
        )
        self.assertEqual(deleted, {"deleted": True, "session_id": session_id})
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 0)
        self.assertTarotStatus(
            404, lambda: self.store.bootstrap_for_human(session_id, 101)
        )
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(session_id, 201, 101)
        )
        self.assertTarotStatus(
            404,
            lambda: self.store.finish_reading(
                session_id,
                101,
                attempt["id"],
                state="succeeded",
                text="迟到响应不得复活",
            ),
        )
        with self.store._connect() as conn:
            for table in ("tarot_invites", "tarot_receipts", "tarot_readings"):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_two_human_machine_pairs_complete_concurrently_without_cross_reads(self):
        first = self.store.create_invite(201, 101, "concurrent_pair_1", "第一组私密问题")
        second = self.store.create_invite(202, 102, "concurrent_pair_2", "第二组私密问题")
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
        first = self.store.create_invite(301, 401, "purge_human_1", "清理问题一")
        second = self.store.create_invite(302, 402, "purge_machine_2", "清理问题二")
        self.assertEqual(self.store.delete_user_data(401), 1)
        self.assertEqual(self.store.delete_user_data(302), 1)
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(first["session_id"], 301, 401)
        )
        self.assertTarotStatus(
            404, lambda: self.store.ai_status(second["session_id"], 302, 402)
        )

    def test_invite_is_idempotent_and_bound_to_exact_human_machine_pair(self):
        first = self.store.create_invite(201, 101, "request_0001", "我该如何选择？")
        replay = self.store.create_invite(201, 101, "request_0001", "我该如何选择？")
        self.assertEqual(first["session_id"], replay["session_id"])
        self.assertEqual(first["invitation"]["state"], "pending")
        self.assertEqual(first["invitation"]["question"], "我该如何选择？")
        self.assertEqual(first["question"], "我该如何选择？")
        session_id = first["session_id"]

        self.assertTarotStatus(
            409,
            lambda: self.store.create_invite(
                201, 101, "request_0001", "换成另一个问题"
            ),
        )

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
        accepted = self.store.bootstrap_for_human(session_id, 101)["session"]
        self.assertEqual(accepted["phase"], "accepted")
        self.assertEqual(accepted["question"], "我该如何选择？")
        invitation = self.store.invitation_for_human(session_id, 101)
        accepted_replay = self.store.respond_invite(
            session_id,
            101,
            accept=True,
            csrf_token=invitation["csrf_token"],
        )
        self.assertEqual(accepted_replay["invitation_state"], "accepted")
        self.assertEqual(self.store.pending_invitations_for_human(101), [])
        self.assertTarotStatus(
            409,
            lambda: self.store.respond_invite(
                session_id,
                101,
                accept=False,
                csrf_token=invitation["csrf_token"],
            ),
        )
        self.assertEqual(
            self.store.ai_status(session_id, 201, 101)["invitation"]["state"],
            "accepted",
        )

    def test_new_invite_requires_a_bounded_nonempty_question(self):
        for index, question in enumerate((None, 42, "", " \n\t"), 1):
            with self.subTest(question=question):
                self.assertTarotStatus(
                    400,
                    lambda question=question, index=index: self.store.create_invite(
                        201, 101, f"bad_question_{index}", question
                    ),
                )
        self.assertTarotStatus(
            400,
            lambda: self.store.create_invite(
                201, 101, "bad_question_long", "问" * (MAX_INVITE_QUESTION + 1)
            ),
        )
        valid = self.store.create_invite(
            201, 101, "trimmed_question", "  保留正文，去掉首尾空白  "
        )
        self.assertEqual(valid["invitation"]["question"], "保留正文，去掉首尾空白")

    def test_pending_list_is_owned_offline_durable_and_expires_once(self):
        own = self.store.create_invite(201, 101, "pending_owned", "<b>只作纯文本</b>")
        other = self.store.create_invite(202, 102, "pending_other", "别人的问题")
        pending = self.store.pending_invitations_for_human(101)
        self.assertEqual([item["session_id"] for item in pending], [own["session_id"]])
        self.assertEqual(pending[0]["question"], "<b>只作纯文本</b>")

        self.now[0] += 86_401
        self.assertEqual(self.store.pending_invitations_for_human(101), [])
        expired = self.store.ai_status(own["session_id"], 201, 101)
        self.assertEqual(expired["invitation"]["state"], "expired")
        self.assertEqual(expired["phase"], "expired")
        revision = expired["revision"]
        self.assertEqual(self.store.pending_invitations_for_human(101), [])
        self.assertEqual(
            self.store.ai_status(own["session_id"], 201, 101)["revision"],
            revision,
        )
        self.assertEqual(
            self.store.ai_status(other["session_id"], 202, 102)["invitation"]["state"],
            "expired",
        )

    def test_pending_invite_wait_wakes_for_owner_without_cross_account_leak(self):
        initial = self.store.wait_pending_invitations_for_human(101)
        self.assertEqual(initial["invitations"], [])
        self.assertRegex(initial["cursor"], r"^[0-9a-f]{64}$")
        unchanged = self.store.wait_pending_invitations_for_human(
            101, after_cursor=initial["cursor"], wait_seconds=0
        )
        self.assertTrue(unchanged["unchanged"])

        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(
                self.store.wait_pending_invitations_for_human,
                101,
                after_cursor=initial["cursor"],
                wait_seconds=2,
            )
            self.store.create_invite(202, 102, "wait_other", "别人的等待问题")
            time.sleep(0.05)
            self.assertFalse(waiting.done(), "another account must not change this cursor")
            own = self.store.create_invite(201, 101, "wait_owner", "稍后发来的问题")
            changed = waiting.result(timeout=1)

        self.assertNotEqual(changed["cursor"], initial["cursor"])
        self.assertEqual(
            [item["session_id"] for item in changed["invitations"]],
            [own["session_id"]],
        )
        self.assertNotIn("别人的等待问题", str(changed))

    def test_legacy_questionless_invite_can_still_be_reviewed(self):
        now = self.now[0]
        session_id = "L" * 32
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO tarot_sessions(
                    id,human_user_id,ai_user_id,request_id,phase,csrf_token,
                    created_at,updated_at
                ) VALUES(?,?,?,?, 'pending',?,?,?)
                """,
                (session_id, 101, 201, "legacy_questionless", "legacy-csrf", now, now),
            )
            conn.execute(
                """
                INSERT INTO tarot_invites(
                    session_id,human_user_id,ai_user_id,state,created_at,expires_at
                ) VALUES(?,?,?,'pending',?,?)
                """,
                (session_id, 101, 201, now, now + 60),
            )
        invitation = self.store.invitation_for_human(session_id, 101)
        self.assertEqual(invitation["question"], "")
        accepted = self.store.respond_invite(
            session_id, 101, accept=True, csrf_token="legacy-csrf"
        )
        self.assertEqual(accepted["invitation_state"], "accepted")
        self.assertEqual(
            self.store.bootstrap_for_human(session_id, 101)["session"]["question"],
            "",
        )

    def test_draw_reveal_and_result_do_not_cross_identity_boundaries(self):
        invite = self.store.create_invite(201, 101, "request_0002", "小机最初想问什么？")
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
        drawn_status = self.store.ai_status(session_id, 201, 101)
        self.assertEqual(drawn_status["phase"], "drawn")
        self.assertEqual(drawn_status["invitation"]["state"], "accepted")
        self.assertEqual(
            drawn_status["invitation"]["question"], "小机最初想问什么？"
        )
        replay = self.store.create_invite(
            201, 101, "request_0002", "小机最初想问什么？"
        )
        self.assertEqual(replay["session_id"], session_id)
        self.assertEqual(replay["question"], "今天该看见什么？")
        self.assertTarotStatus(
            409,
            lambda: self.store.create_invite(
                201, 101, "request_0002", "今天该看见什么？"
            ),
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
        invite = self.store.create_invite(201, 101, "request_0003", "专业解读问题")
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
        invite = self.store.create_invite(201, 101, "request_bad_model", "模型问题")
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
        invite = self.store.create_invite(201, 101, "request_stop_race", "停止竞态问题")
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
        other = self.store.create_invite(202, 102, "request_restart", "重启问题")
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
        rejected = self.store.create_invite(201, 101, "reject_01", "会被拒绝的问题")
        invitation = self.store.invitation_for_human(rejected["session_id"], 101)
        self.store.respond_invite(
            rejected["session_id"],
            101,
            accept=False,
            csrf_token=invitation["csrf_token"],
        )
        replay = self.store.respond_invite(
            rejected["session_id"],
            101,
            accept=False,
            csrf_token=invitation["csrf_token"],
        )
        self.assertEqual(replay["invitation_state"], "rejected")
        self.assertEqual(
            self.store.ai_status(rejected["session_id"], 201, 101)["invitation"]["state"],
            "rejected",
        )
        self.assertEqual(self.store.history_for_human(101)["items"], [])
        self.assertEqual(count_saved_tarot_sessions(self.store.db_path), 0)
        self.assertEqual(self.store.pending_invitations_for_human(101), [])
        self.assertTarotStatus(
            409,
            lambda: self.store.respond_invite(
                rejected["session_id"],
                101,
                accept=True,
                csrf_token=invitation["csrf_token"],
            ),
        )
        with self.assertRaises(TarotError) as rejected_error:
            self.store.create_invite(201, 101, "reject_02", "冷却中的问题")
        self.assertEqual(rejected_error.exception.status, 429)
        self.assertEqual(
            rejected_error.exception.message,
            "人类拒绝后 24 小时内不能再次邀请，请等待冷却结束",
        )
        self.now[0] += 86_401
        self.store.create_invite(201, 101, "limit_001", "限频问题一")
        self.store.create_invite(201, 101, "limit_002", "限频问题二")
        self.store.create_invite(201, 101, "limit_003", "限频问题三")
        with self.assertRaises(TarotError) as limit_error:
            self.store.create_invite(201, 101, "limit_004", "限频问题四")
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
        self.assertIn("question", columns)
        self.assertNotIn("human_requested", columns)

    def test_existing_invite_table_gets_idempotent_question_migration(self):
        legacy_path = Path(self.temp_dir.name) / "legacy-schema.db"
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript(
                """
                CREATE TABLE tarot_sessions (
                    id TEXT PRIMARY KEY,human_user_id INTEGER NOT NULL,
                    ai_user_id INTEGER,request_id TEXT,phase TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    question TEXT NOT NULL DEFAULT '',spread_id TEXT,
                    draws_json TEXT NOT NULL DEFAULT '[]',
                    canonical_json TEXT NOT NULL DEFAULT '{}',reading_id TEXT,
                    csrf_token TEXT NOT NULL,created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,UNIQUE(ai_user_id,request_id)
                );
                CREATE TABLE tarot_invites (
                    session_id TEXT PRIMARY KEY REFERENCES tarot_sessions(id),
                    human_user_id INTEGER NOT NULL,ai_user_id INTEGER NOT NULL,
                    state TEXT NOT NULL,created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,accepted_at REAL,rejected_at REAL
                );
                """
            )
        TarotStore(legacy_path, clock=lambda: self.now[0], catalog=FakeCatalog())
        TarotStore(legacy_path, clock=lambda: self.now[0], catalog=FakeCatalog())
        with sqlite3.connect(legacy_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tarot_invites)")}
            self.assertIn("question", columns)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

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
            invite = restarted.create_invite(
                201, 101, f"legacy_limit_{index}", f"旧限频问题 {index}"
            )
            with restarted._connect() as conn:
                conn.execute(
                    "UPDATE tarot_invites SET human_requested=1 WHERE session_id=?",
                    (invite["session_id"],),
                )
        with self.assertRaises(TarotError) as caught:
            restarted.create_invite(201, 101, "legacy_limit_blocked", "旧限频拦截")
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
        self.assertIn('/tarot/static/platform/managed-core.v5.js', page)
        self.assertIn('/tarot/static/platform/managed-ui.v3.js', page)
        self.assertIn('/tarot/static/platform/managed-ui.v3.css', page)
        self.assertIn('/tarot/static/platform/managed-ui.v4.js', page)
        self.assertIn('/tarot/static/platform/managed-ui.v4.css', page)
        self.assertIn('/tarot/static/platform/managed-ui.v5.js', page)
        self.assertIn('/tarot/static/platform/managed-ui.v5.css', page)
        self.assertIn('/tarot/static/platform/managed-companion.v3.js', page)
        self.assertIn('/tarot/static/platform/managed-cards3d.v5.js', page)
        self.assertNotIn('/tarot/static/platform/managed-ui.v2.js', page)
        self.assertNotIn('/tarot/static/platform/managed-ui.v2.css', page)
        self.assertNotIn('/tarot/static/platform/managed-ui.v1.js', page)
        self.assertNotIn('/tarot/static/platform/managed-ui.v1.css', page)
        self.assertIn(f"<title>{RITUAL_DISPLAY_NAME}</title>", page)
        self.assertIn('id="companion-config"', page)
        self.assertIn('id="providerOrb"', page)
        self.assertIn("本站暂仅支持所提供的模型。如需自行配置模型，请克隆", page)
        self.assertIn(f'href="{COVE_REPOSITORY}"', page)
        self.assertIn('class="managed-model-note managed-credit"', page)
        self.assertIn("原作：林默Moon", page)
        self.assertLess(
            page.index('/tarot/static/platform/managed-ui.v3.css'),
            page.index('/tarot/static/platform/managed-ui.v4.css'),
        )
        self.assertLess(
            page.index('/tarot/static/platform/managed-ui.v4.css'),
            page.index('/tarot/static/platform/managed-ui.v5.css'),
        )
        self.assertLess(
            page.index('/tarot/static/platform/managed-ui.v3.js'),
            page.index('/tarot/static/platform/managed-ui.v4.js'),
        )
        self.assertLess(
            page.index('/tarot/static/platform/managed-ui.v4.js'),
            page.index('/tarot/static/platform/managed-ui.v5.js'),
        )
        self.assertLess(
            page.index('/tarot/static/platform/managed-ui.v5.js'),
            page.index('<script type="module" src="./js/main.js"></script>'),
        )
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
        managed_core_v5 = web.static_file(
            "platform/managed-core.v5.js"
        )[0].read_text(encoding="utf-8")
        self.assertIn("scrollTop", managed_core_v5)
        self.assertIn("managed-core.v1.js", managed_core_v5)
        self.assertNotIn("apiKey", managed_core_v5)
        self.assertNotIn("baseURL", managed_core_v5)
        self.assertNotIn("/api/chat", managed_core_v5)
        managed_ui_path, managed_ui_mime = web.static_file(
            "platform/managed-ui.v3.js"
        )
        self.assertEqual(managed_ui_mime, "text/javascript")
        managed_ui = managed_ui_path.read_text(encoding="utf-8")
        self.assertIn("本次已结束，记录已保留。", managed_ui)
        self.assertIn("managed-companion-settings-hidden", managed_ui)
        self.assertIn("/api/tarot/models/status", managed_ui)
        self.assertIn("可重试（尚未确认恢复）", managed_ui)
        self.assertIn("installSecureRandomUUID", managed_ui)
        self.assertIn("managedHistoryPanel", managed_ui)
        self.assertNotIn("formatRemaining", managed_ui)
        self.assertNotIn("setInterval", managed_ui)
        self.assertNotIn("Math.random(", managed_ui)
        managed_layout_path, managed_layout_mime = web.static_file(
            "platform/managed-ui.v4.js"
        )
        self.assertEqual(managed_layout_mime, "text/javascript")
        managed_layout = managed_layout_path.read_text(encoding="utf-8")
        self.assertIn("managedProviderLabel", managed_layout)
        self.assertIn("label.textContent = '配置'", managed_layout)
        self.assertIn(
            "trigger.replaceChildren(document.createTextNode('记录'))",
            managed_layout,
        )
        self.assertIn("top-right", managed_layout)
        self.assertNotIn("managedHistoryEntryRow", managed_layout)
        managed_mobile_path, managed_mobile_mime = web.static_file(
            "platform/managed-ui.v5.js"
        )
        self.assertEqual(managed_mobile_mime, "text/javascript")
        managed_mobile = managed_mobile_path.read_text(encoding="utf-8")
        self.assertIn("managed-companion-reading", managed_mobile)
        self.assertIn("stream.scrollTop = 0", managed_mobile)
        managed_mobile_css, managed_mobile_css_mime = web.static_file(
            "platform/managed-ui.v5.css"
        )
        self.assertTrue(managed_mobile_css.is_file())
        self.assertEqual(managed_mobile_css_mime, "text/css")
        managed_layout_css, managed_layout_css_mime = web.static_file(
            "platform/managed-ui.v4.css"
        )
        self.assertTrue(managed_layout_css.is_file())
        self.assertEqual(managed_layout_css_mime, "text/css")
        managed_companion = web.static_file(
            "platform/managed-companion.v3.js"
        )[0].read_text(encoding="utf-8")
        self.assertIn("/api/tarot/models/status", managed_companion)
        self.assertIn("body?.attempt_id", managed_companion)
        managed_cards_path, managed_cards_mime = web.static_file(
            "platform/managed-cards3d.v5.js"
        )
        self.assertTrue(managed_cards_path.is_file())
        self.assertEqual(managed_cards_mime, "text/javascript")
        managed_cards = managed_cards_path.read_text(encoding="utf-8")
        self.assertIn("selectionViewportIsCrowded", managed_cards)
        self.assertIn("deferredFrame", managed_cards)
        upstream_cards, upstream_cards_mime = web.static_file(
            "js/three/upstream-cards3d.v1.js"
        )
        self.assertEqual(upstream_cards_mime, "text/javascript")
        self.assertEqual(
            upstream_cards.resolve(),
            (web.public_root / "js" / "three" / "cards3d.js").resolve(),
        )
        self.assertTrue(web.static_file("platform/managed-ui.v1.js")[0].is_file())
        self.assertTrue(web.static_file("platform/managed-ui.v2.js")[0].is_file())
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
