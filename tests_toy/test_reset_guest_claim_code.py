from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import reset_guest_claim_code


class ResetGuestClaimCodeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="claim-reset-")
        self.db_path = Path(self.tempdir.name) / "accounts.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE guest_claim_codes (
                    code TEXT PRIMARY KEY,
                    guest_player_id TEXT NOT NULL UNIQUE,
                    created_at TEXT,
                    claimed_by INTEGER,
                    claimed_at TEXT,
                    claimed_slot INTEGER
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO guest_claim_codes
                    (code, guest_player_id, created_at, claimed_by, claimed_at, claimed_slot)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("claimed-code", "guest:claimed", "2026-08-01 10:00:00", 81, "2026-08-02 11:00:00", 3),
                    ("unused-code", "guest:unused", "2026-08-03 12:00:00", None, None, None),
                ],
            )
            conn.commit()
        self.db_patch = patch.object(
            reset_guest_claim_code.server,
            "TURTLE_DB_PATH",
            self.db_path,
        )
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tempdir.cleanup()

    def _row(self, code):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute(
                "SELECT * FROM guest_claim_codes WHERE code = ?", (code,)
            ).fetchone())

    def test_dry_run_prints_plan_without_writing(self):
        before = self._row("claimed-code")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = reset_guest_claim_code.main(
                ["--code", "claimed-code", "--guest-player-id", "guest:claimed"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(self._row("claimed-code"), before)
        rendered = output.getvalue()
        self.assertIn("mode: dry-run", rendered)
        self.assertIn("projected after:", rendered)
        self.assertIn("affected row count: 0", rendered)
        self.assertIn("dry-run: no changes made", rendered)

    def test_confirm_clears_only_claim_result_columns(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = reset_guest_claim_code.main(
                [
                    "--code",
                    "claimed-code",
                    "--guest-player-id",
                    "guest:claimed",
                    "--confirm",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("mode: confirmed", output.getvalue())
        self.assertIn("before:", output.getvalue())
        self.assertIn("after:", output.getvalue())
        self.assertIn("affected row count: 1", output.getvalue())
        after = self._row("claimed-code")
        self.assertEqual(after["code"], "claimed-code")
        self.assertEqual(after["guest_player_id"], "guest:claimed")
        self.assertEqual(after["created_at"], "2026-08-01 10:00:00")
        self.assertIsNone(after["claimed_by"])
        self.assertIsNone(after["claimed_at"])
        self.assertIsNone(after["claimed_slot"])

    def test_code_and_player_mismatch_never_writes(self):
        before = self._row("claimed-code")
        for code, player_id in (
            ("claimed-code", "guest:wrong"),
            ("wrong-code", "guest:claimed"),
        ):
            with self.subTest(code=code, player_id=player_id):
                with self.assertRaises(reset_guest_claim_code.ClaimResetError):
                    reset_guest_claim_code.reset_guest_claim(
                        code,
                        player_id,
                        confirm=True,
                    )
                self.assertEqual(self._row("claimed-code"), before)

    def test_already_unclaimed_row_is_refused(self):
        before = self._row("unused-code")
        with self.assertRaises(reset_guest_claim_code.ClaimResetError):
            reset_guest_claim_code.reset_guest_claim(
                "unused-code",
                "guest:unused",
                confirm=True,
            )
        self.assertEqual(self._row("unused-code"), before)


if __name__ == "__main__":
    unittest.main()
