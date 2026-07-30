import importlib.util
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


if __name__ == "__main__":
    unittest.main()
