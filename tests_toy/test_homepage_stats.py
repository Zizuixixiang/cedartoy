"""GET / prefill and conditional caching, using only temporary databases."""
import hashlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import puzzle_box
import server


class HomepageStatsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "sessions.db"
        self.source = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        self.real_tarot_homepage = server.TAROT_WEB.homepage_index
        self.patch_db = patch.object(server, "SESSIONS_DB_PATH", self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        # The homepage must never run the whole public stats aggregation.
        self.stats = patch.object(server, "_public_game_stats", side_effect=AssertionError("slow stats called"))
        self.stats.start()
        self.addCleanup(self.stats.stop)

    def add_saves(self, count, first=1):
        puzzle_box.init_db(self.db)
        with sqlite3.connect(self.db) as conn:
            # Two puzzle rows per identity must still count as a single save.
            conn.executemany(
                "INSERT INTO puzzle_box_progress(ai_user_id, puzzle_id, status) VALUES (?, ?, 'opened')",
                [(ai, pid) for ai in range(first, first + count) for pid in ("N01", "N02")],
            )

    def get_home(self, etag=None):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/"
        handler.headers = {"If-None-Match": etag} if etag else {}
        handler.wfile = io.BytesIO()
        handler.response_status = None
        handler.response_headers = {}
        handler.send_response = lambda status: setattr(handler, "response_status", status)
        handler.send_header = lambda key, value: handler.response_headers.update({key: value})
        handler.end_headers = lambda: None
        handler.do_GET()
        return handler.response_status, handler.response_headers, handler.wfile.getvalue()

    def expected_source(self, metric):
        start = self.source.index('        id: "puzzle_box",')
        end = self.source.index("\n      },", start)
        card = self.source[start:end].replace('metric: "--"', f'metric: "{metric}"')
        return self.source[:start] + card + self.source[end:]

    def assert_home(self, metric):
        status, headers, body = self.get_home()
        self.assertEqual(status, 200)
        expected_source = self.expected_source(metric)
        self.assertEqual(body, self.real_tarot_homepage(expected_source))
        self.assertIn(b'id: "tarot"', body)  # Existing injection still runs.
        self.assertEqual(headers["Content-Length"], str(len(body)))
        self.assertEqual(headers["ETag"], f'"{hashlib.sha256(body).hexdigest()[:16]}"')
        return headers, body

    def test_real_distinct_count_prefills_only_puzzle_box_before_tarot(self):
        self.add_saves(8)
        with patch.object(server.TAROT_WEB, "homepage_index", wraps=self.real_tarot_homepage) as inject:
            self.assert_home("8")
        inject.assert_called_once_with(self.expected_source("8"))
        # Full-source equality above also protects every other placeholder and
        # the unchanged loadGameStats implementation, not just this one card.

    def test_existing_empty_table_prefills_zero(self):
        self.add_saves(0)
        self.assert_home("0")

    def test_missing_database_and_missing_table_keep_placeholder_without_writes(self):
        self.assert_home("--")
        self.assertFalse(self.db.exists())
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE unrelated(value TEXT)")
        before = self.db.read_bytes()
        self.assert_home("--")
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(server._count_puzzle_box_saves(), 0)  # API's old default.

    def test_read_failure_keeps_homepage_available(self):
        self.db.write_bytes(b"not a sqlite database")
        with self.assertLogs(server.logger, level="WARNING"):
            self.assert_home("--")
        self.assertEqual(self.db.read_bytes(), b"not a sqlite database")
        with patch.object(server, "_count_puzzle_box_saves", side_effect=OSError("fixture")):
            with self.assertLogs(server.logger, level="WARNING"):
                self.assert_home("--")

    def test_locked_database_uses_short_homepage_busy_timeout(self):
        self.add_saves(8)
        connect = server._read_only_connect
        busy_timeouts = []

        def capture(path):
            conn = connect(path)
            conn.set_trace_callback(lambda sql: busy_timeouts.append(sql) if "busy_timeout" in sql else None)
            return conn

        with sqlite3.connect(self.db) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            with patch.object(server, "_read_only_connect", side_effect=capture):
                with self.assertLogs(server.logger, level="WARNING"):
                    self.assert_home("--")
        self.assertEqual(busy_timeouts, ["PRAGMA busy_timeout=200"])

    def test_conditional_get_recounts_and_hashes_final_html(self):
        self.add_saves(8)
        headers, _ = self.assert_home("8")
        old_etag = headers["ETag"]
        status, cached_headers, body = self.get_home(old_etag)
        self.assertEqual((status, body), (304, b""))
        self.assertEqual(cached_headers["ETag"], old_etag)
        self.assertEqual(cached_headers["Cache-Control"], "no-cache")
        self.add_saves(1, first=9)
        status, changed_headers, body = self.get_home(old_etag)
        self.assertEqual(status, 200)
        self.assertNotEqual(changed_headers["ETag"], old_etag)
        self.assertEqual(body, self.real_tarot_homepage(self.expected_source("9")))
        # A failed count must not serve a stale cached successful count either.
        with patch.object(server, "_count_puzzle_box_saves", side_effect=sqlite3.OperationalError("fixture")):
            with self.assertLogs(server.logger, level="WARNING"):
                status, _, body = self.get_home(changed_headers["ETag"])
        self.assertEqual(status, 200)
        self.assertEqual(body, self.real_tarot_homepage(self.source))

    def test_tarot_failure_preserves_prefill_and_dynamic_etag(self):
        self.add_saves(8)
        with patch.object(server.TAROT_WEB, "homepage_index", side_effect=server.TarotError(500, "fixture")):
            with self.assertLogs(server.logger, level="ERROR"):
                status, headers, body = self.get_home()
            self.assertEqual(status, 200)
            self.assertEqual(body, self.expected_source("8").encode("utf-8"))
            self.add_saves(1, first=9)
            with self.assertLogs(server.logger, level="ERROR"):
                status, changed_headers, body = self.get_home(headers["ETag"])
            self.assertEqual(status, 200)
            self.assertNotEqual(changed_headers["ETag"], headers["ETag"])
            self.assertEqual(body, self.expected_source("9").encode("utf-8"))

    def test_missing_or_ambiguous_card_never_changes_another_game(self):
        for source in (
            self.source.replace('id: "puzzle_box",', 'id: "renamed",'),
            self.source + self.source,
            self.expected_source("already-filled"),
        ):
            with patch.object(server, "_count_puzzle_box_saves") as count:
                self.assertEqual(server._prefill_puzzle_box_homepage_metric(source), source)
                count.assert_not_called()


if __name__ == "__main__":
    unittest.main()
