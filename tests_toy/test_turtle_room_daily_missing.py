"""Scoped data repair regression; no production data or model calls."""
import sqlite3
import unittest

from scripts.fix_turtle_room_daily_missing import (
    OLD_SURFACE, ROOM_ID, SURFACE, TITLE, apply_correction,
)


class DailyMissingRepairTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE rooms (
                id TEXT PRIMARY KEY, puzzle_id INTEGER, title TEXT,
                surface TEXT, answer TEXT, status TEXT, created_by INTEGER
            );
            CREATE TABLE puzzle_submissions (id INTEGER PRIMARY KEY, surface TEXT);
            CREATE TABLE puzzles (id INTEGER PRIMARY KEY, title TEXT, surface TEXT);
        """)
        self.db.executemany("INSERT INTO rooms VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (ROOM_ID, None, "", OLD_SURFACE, "【汤底】\n原汤底保持原样", "playing", 1),
            ("other", None, "", OLD_SURFACE, "别人的汤底", "finished", 2),
            ("random", 1, TITLE, "题库原汤面", "题库原汤底", "playing", 3),
        ])
        self.db.execute("INSERT INTO puzzle_submissions VALUES (369, ?)", (OLD_SURFACE,))
        self.db.execute("INSERT INTO puzzles VALUES (1, ?, '题库原汤面')", (TITLE,))
        self.db.commit()

    def snapshot(self):
        return {table: self.db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                for table in ("rooms", "puzzles", "puzzle_submissions")}

    def test_only_target_title_and_surface_change(self):
        expected = self.snapshot()
        expected["rooms"] = [
            (row[0], row[1], TITLE, SURFACE, *row[4:]) if row[0] == ROOM_ID else row
            for row in expected["rooms"]
        ]
        self.assertTrue(apply_correction(self.db))
        self.assertEqual(self.snapshot(), expected)
        self.assertEqual(OLD_SURFACE.removeprefix(f"标题】{TITLE}\n\n【汤面】\n\n"), SURFACE)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_second_run_is_noop(self):
        apply_correction(self.db)
        before = self.snapshot()
        self.assertFalse(apply_correction(self.db))
        self.assertEqual(self.snapshot(), before)

    def test_refuses_missing_room(self):
        self.db.execute("DELETE FROM rooms WHERE id = ?", (ROOM_ID,))
        self.db.commit()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "missing"):
            apply_correction(self.db)
        self.assertEqual(self.snapshot(), before)

    def test_refuses_changed_content_or_puzzle_room(self):
        for field, value in (("title", "另一个标题"), ("surface", "已被编辑"), ("puzzle_id", 1)):
            with self.subTest(field=field):
                self.db.execute(
                    "UPDATE rooms SET title = '', surface = ?, puzzle_id = NULL WHERE id = ?",
                    (OLD_SURFACE, ROOM_ID),
                )
                self.db.execute(f"UPDATE rooms SET {field} = ? WHERE id = ?", (value, ROOM_ID))
                self.db.commit()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Unexpected"):
                    apply_correction(self.db)
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
