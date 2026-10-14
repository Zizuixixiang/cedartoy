import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1] / "turtle-soup" / "backend"
auth_utils_stub = types.ModuleType("auth_utils")
auth_utils_stub.current_player = lambda: None
database_stub = types.ModuleType("database")
database_stub.DB_PATH = BACKEND_DIR / "turtle_soup.db"
database_stub.fetch_all = lambda *_args, **_kwargs: []
utils_stub = types.ModuleType("utils")
utils_stub.SQL_NOW = "datetime('now')"
module_spec = importlib.util.spec_from_file_location(
    "cedartoy_test_leaderboard",
    BACKEND_DIR / "routers" / "leaderboard.py",
)
leaderboard = importlib.util.module_from_spec(module_spec)
with patch.dict(
    sys.modules,
    {"auth_utils": auth_utils_stub, "database": database_stub, "utils": utils_stub},
):
    module_spec.loader.exec_module(leaderboard)


class PlatformStatsTests(unittest.TestCase):
    def test_enneagram_results_are_grouped_by_primary_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "sessions.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE test_results (
                        player_id TEXT NOT NULL,
                        game TEXT NOT NULL,
                        result_value TEXT,
                        result_detail TEXT,
                        completed_at REAL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO test_results
                        (player_id, game, result_value, result_detail, completed_at)
                    VALUES (?, ?, ?, '{}', 0)
                    """,
                    (
                        ("ennea-1", "enneagram", "1"),
                        ("ennea-2", "enneagram", "5"),
                        ("ennea-3", "enneagram", "5"),
                        ("mbti-1", "mbti", "INTJ"),
                    ),
                )

            with patch.object(leaderboard, "SESSIONS_DB_PATH", db_path):
                distributions = leaderboard._test_result_distributions()

        self.assertEqual(
            distributions["enneagram"],
            [
                {"result": "5", "count": 2},
                {"result": "1", "count": 1},
            ],
        )
        self.assertEqual(
            distributions["mbti"],
            [{"result": "INTJ", "count": 1}],
        )

    def _research_distributions(self, records):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "sessions.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE test_results (
                        player_id TEXT, game TEXT, result_value TEXT,
                        result_detail TEXT, completed_at REAL
                    )
                """)
                conn.executemany("""
                    INSERT INTO test_results (player_id, game, result_value, result_detail, completed_at)
                    VALUES (?, ?, ?, ?, ?)
                """, records)
            with patch.object(leaderboard, "SESSIONS_DB_PATH", db_path):
                return leaderboard._scored_research_distributions()

    def test_scored_research_has_all_18_and_never_includes_deeptalk(self):
        from deeptalk.catalog import ALIASES

        scales = leaderboard._scored_research_specs()
        self.assertEqual(len(scales), 18)
        self.assertIn("aidep", scales)
        self.assertIn("ipip_adhd", scales)
        self.assertNotIn("deeptalk", scales)
        self.assertFalse(any(game.startswith("dt_") for game in scales))
        self.assertEqual(len(ALIASES), 10)
        # Even research-shaped records must not bring unscored question banks in.
        scores = {key: (lo + hi) / 2 for fields in scales.values()
                  for key, _, lo, hi in fields}
        records = [("guest:deeptalk", game, "research",
                    json.dumps({"scores": scores}), 1)
                   for game in ["deeptalk", *ALIASES, *ALIASES.values()]]
        distributions = self._research_distributions(records)
        self.assertEqual(set(distributions), set(scales))
        self.assertTrue(all(d == {"status": "empty", "dimensions": []}
                            for d in distributions.values()))

    def test_all_scored_research_publish_one_sample_without_private_data(self):
        specs = leaderboard._scored_research_specs()
        records = []
        for game, fields in specs.items():
            scores = {key: lo + (hi - lo) * 0.53 for key, _, lo, hi in fields}
            records.append(("guest:PRIVATE_ID_NEVER_PUBLIC", game, "research", json.dumps({
                "scores": scores, "answers": ["PRIVATE_ANSWER_NEVER_PUBLIC"],
                "owner_name": "PRIVATE_NAME_NEVER_PUBLIC",
                "machine_id": "PRIVATE_MACHINE_NEVER_PUBLIC",
            }), 1))
        stats = self._research_distributions(records)
        for game, fields in specs.items():
            with self.subTest(game=game):
                # Exact public shape prevents IDs, raw scores or extra details leaking.
                self.assertEqual(stats[game], {
                    "status": "ready", "sample_size": 1,
                    "dimensions": [{
                        "label": label,
                        "buckets": [{
                            "result": f"{lo + i * (hi - lo) / 5:.1f}–{lo + (i + 1) * (hi - lo) / 5:.1f}",
                            "count": int(i == 2),
                        } for i in range(5)],
                    } for _, label, lo, hi in fields],
                })
        payload = json.dumps(stats, ensure_ascii=False)
        self.assertNotIn("PRIVATE_", payload)

    def test_scored_research_zero_samples_are_empty(self):
        stats = self._research_distributions([])
        self.assertEqual(len(stats), 18)
        for game, data in stats.items():
            with self.subTest(game=game):
                self.assertEqual(data, {"status": "empty", "dimensions": []})

    def test_scored_research_rejects_invalid_json_and_scores(self):
        specs = leaderboard._scored_research_specs()
        for game in ("bsr", "aidep"):
            fields = specs[game]
            scores = {key: (lo + hi) / 2 for key, _, lo, hi in fields}
            key, _, lo, hi = fields[0]
            invalid = [None, "{bad json", "null", "[]", "{}",
                       '{"scores": null}', '{"scores": []}', '{"scores": {}}']
            invalid.extend(json.dumps({"scores": {**scores, key: value}})
                           for value in (True, "3", None, [], {}, lo - 1, hi + 1,
                                         float("nan"), float("inf"), -float("inf")))
            for raw in invalid:
                with self.subTest(game=game, raw=raw):
                    records = [("guest:invalid", game, "research", raw, 1),
                               ("guest:wrong-kind", game, "something_else",
                                json.dumps({"scores": scores}), 2)]
                    self.assertEqual(self._research_distributions(records)[game],
                                     {"status": "empty", "dimensions": []})

    def test_scored_research_dedupes_slots_using_latest_valid_result(self):
        shapes = leaderboard._scored_research_specs()
        for game in ("bsr", "aidep"):
            with self.subTest(game=game):
                def detail(fraction):
                    return json.dumps({"scores": {
                        key: lo + (hi - lo) * fraction
                        for key, _, lo, hi in shapes[game]
                    }})

                # All five account slots count once; guests remain distinct.
                records = [(player_id, game, "research", detail(0), i)
                           for i, player_id in enumerate(("100", "100:2", "100:3", "100:4", "100:5"))]
                records += [
                    ("100:2", game, "research", detail(1), 10),
                    ("100", game, "research", "{bad json", 12),
                    ("100:5", game, "research", detail(2), 11),
                    ("guest:one", game, "research", detail(0), 1),
                    ("guest:two", game, "research", detail(0.25), 1),
                    ("guest:one", game, "research", detail(0.5), 0),
                ]
                data = self._research_distributions(records)[game]
                self.assertEqual(data["status"], "ready")
                self.assertEqual(data["sample_size"], 3)
                for dimension in data["dimensions"]:
                    self.assertEqual([b["count"] for b in dimension["buckets"]],
                                     [1, 1, 0, 0, 1])

    def _sins_virtues_distributions(self, rows):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "sessions.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE test_results (
                        player_id TEXT NOT NULL,
                        game TEXT NOT NULL,
                        result_value TEXT,
                        result_detail TEXT,
                        completed_at REAL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO test_results
                        (player_id, game, result_value, result_detail, completed_at)
                    VALUES (?, 'sins_virtues', ?, ?, 0)
                    """,
                    rows,
                )

            with patch.object(leaderboard, "SESSIONS_DB_PATH", db_path):
                distributions = leaderboard._test_result_distributions()

        return distributions

    def test_sins_and_virtues_use_independent_top_dimension_distributions(self):
        distributions = self._sins_virtues_distributions(
            (
                (
                    "sins-1",
                    "pride_humility",
                    json.dumps(
                        {
                            "top_sins": ["wrath", "pride"],
                            "top_virtues": ["kindness", "humility"],
                        }
                    ),
                ),
                (
                    "sins-2",
                    "lust_chastity",
                    json.dumps(
                        {
                            "top_sins": ["wrath", "lust"],
                            "top_virtues": ["patience", "chastity"],
                        }
                    ),
                ),
            )
        )

        sins = distributions["sins_virtues_sins"]
        virtues = distributions["sins_virtues_virtues"]
        self.assertEqual(sum(item["count"] for item in sins), 2)
        self.assertEqual(sum(item["count"] for item in virtues), 2)
        self.assertEqual({item["result"]: item["count"] for item in sins}["wrath"], 2)
        self.assertEqual(
            {item["result"]: item["count"] for item in virtues},
            {
                "chastity": 0,
                "temperance": 0,
                "generosity": 0,
                "diligence": 0,
                "patience": 1,
                "kindness": 1,
                "humility": 0,
            },
        )
        self.assertNotIn("sins_virtues", distributions)

    def test_sins_virtues_old_scores_use_stable_order_for_ties(self):
        distributions = self._sins_virtues_distributions(
            (
                (
                    "scores-only",
                    "pride_humility",
                    json.dumps(
                        {
                            "scores": {
                                "lust": 90,
                                "gluttony": 90,
                                "chastity": 75,
                                "temperance": 75,
                            }
                        }
                    ),
                ),
            )
        )

        sins = {item["result"]: item["count"] for item in distributions["sins_virtues_sins"]}
        virtues = {
            item["result"]: item["count"]
            for item in distributions["sins_virtues_virtues"]
        }
        self.assertEqual(sins["lust"], 1)
        self.assertEqual(sins["pride"], 0)
        self.assertEqual(virtues["chastity"], 1)
        self.assertEqual(virtues["humility"], 0)

    def test_sins_virtues_old_pair_is_final_compatibility_fallback(self):
        distributions = self._sins_virtues_distributions(
            (
                ("sins-1", "wrath_patience", "{}"),
                ("sins-2", "wrath_patience", "not-json"),
                ("sins-3", "pride_humility", "{}"),
            )
        )

        sins = distributions["sins_virtues_sins"]
        virtues = distributions["sins_virtues_virtues"]
        self.assertEqual(len(sins), 7)
        self.assertEqual(len(virtues), 7)
        self.assertEqual(
            {item["result"]: item["count"] for item in sins},
            {
                "lust": 0,
                "gluttony": 0,
                "greed": 0,
                "sloth": 0,
                "wrath": 2,
                "envy": 0,
                "pride": 1,
            },
        )
        self.assertEqual(
            {item["result"]: item["count"] for item in virtues}["patience"], 2
        )
        self.assertEqual(
            {item["result"]: item["count"] for item in virtues}["humility"], 1
        )


if __name__ == "__main__":
    unittest.main()
