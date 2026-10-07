"""Check precise, atomic and idempotent corrections using a disposable database."""
import sqlite3
import unittest

from scripts.fix_turtle_puzzles_52_112 import apply_corrections, NEW_RULE_LABEL, OLD_RULE_LABEL


class TurtlePuzzleCorrectionsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE puzzles (id INTEGER PRIMARY KEY, title TEXT, surface TEXT, answer TEXT)")
        self.db.executemany("INSERT INTO puzzles VALUES (?, ?, ?, ?)", [
            (52, "蚊子", "我吃饱饭就死了，浑身都是血。", "52 原汤底"),
            (112, "白雪公主规则怪谈", f"原题面（注：{OLD_RULE_LABEL}，即反话）", "112 原汤底"),
            (113, "双缝干涉实验", "双缝原汤面", "双缝原汤底及玩法规则"),
        ])
        self.db.commit()

    def rows(self):
        return self.db.execute("SELECT * FROM puzzles ORDER BY id").fetchall()

    def test_only_requested_fields_change_and_second_run_is_noop(self):
        before = self.rows()
        self.assertEqual(apply_corrections(self.db), [52, 112])
        expected = [
            (52, "吃饱之后", *before[0][2:]),
            (112, before[1][1], before[1][2].replace(OLD_RULE_LABEL, NEW_RULE_LABEL), before[1][3]),
            before[2],
        ]
        self.assertEqual(self.rows(), expected)
        self.assertEqual(apply_corrections(self.db), [])
        self.assertEqual(self.rows(), expected)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unexpected_rule_label_rolls_back_title_change(self):
        self.db.execute("UPDATE puzzles SET surface = '不同题面' WHERE id = 112")
        self.db.commit()
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "unexpected rule label"):
            apply_corrections(self.db)
        self.assertEqual(self.rows(), before)

    def test_wrong_database_is_rejected(self):
        self.db.execute("DELETE FROM puzzles WHERE id = 52")
        self.db.commit()
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "Expected puzzle IDs"):
            apply_corrections(self.db)
        self.assertEqual(self.rows(), before)
