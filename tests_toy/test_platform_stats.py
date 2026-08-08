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
module_spec = importlib.util.spec_from_file_location(
    "cedartoy_test_leaderboard",
    BACKEND_DIR / "routers" / "leaderboard.py",
)
leaderboard = importlib.util.module_from_spec(module_spec)
with patch.dict(
    sys.modules,
    {"auth_utils": auth_utils_stub, "database": database_stub},
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
