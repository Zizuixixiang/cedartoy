import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

auth_utils_stub = types.ModuleType("auth_utils")
auth_utils_stub.current_player = lambda: None
sys.modules["auth_utils"] = auth_utils_stub
database_stub = types.ModuleType("database")
database_stub.execute = AsyncMock(return_value=1)
database_stub.fetch_all = AsyncMock(return_value=[])
database_stub.fetch_one = AsyncMock(return_value=None)
database_stub.get_setting = AsyncMock(return_value="3")
sys.modules["database"] = database_stub
judge_stub = types.ModuleType("judge")
judge_stub.public_answer_from_full_answer = lambda answer: answer
judge_stub.scan_text = AsyncMock(return_value=None)
sys.modules["judge"] = judge_stub

from routers import rooms as rooms_router  # noqa: E402
sys.modules.pop("judge", None)


class HistorySubjectTests(unittest.IsolatedAsyncioTestCase):
    def test_subject_visibility_requires_stats_or_retained_room(self):
        empty = {
            "stats": {"total_games": 0, "win_count": 0, "ask_count": 0},
            "rooms": [],
        }
        self.assertFalse(rooms_router._history_subject_has_data(empty))
        self.assertTrue(rooms_router._history_subject_has_data({
            **empty,
            "stats": {**empty["stats"], "ask_count": 1},
        }))
        self.assertTrue(rooms_router._history_subject_has_data({
            **empty,
            "rooms": [{"id": "ABCD1234"}],
        }))

    async def test_subject_uses_long_term_stats_and_recent_room_details(self):
        players = [{
            "id": 7,
            "ask_count": 12,
            "ask_count_y": 4,
            "ask_count_n": 5,
            "ask_count_u": 2,
            "ask_count_p": 1,
            "win_count": 2,
            "game_count": 9,
        }]
        recent_rooms = [{"id": "ABCD1234", "is_creator": 1, "is_winner": 0}]
        with patch.object(rooms_router, "fetch_all", new=AsyncMock(side_effect=[players, recent_rooms])) as fetch_all:
            result = await rooms_router._history_subject(
                subject_id="self",
                label="我",
                username="tester",
                toy_user_id=42,
            )

        self.assertEqual(result["stats"]["total_games"], 9)
        self.assertEqual(result["stats"]["win_count"], 2)
        self.assertEqual(result["stats"]["ask_count"], 12)
        self.assertEqual(result["rooms"], recent_rooms)
        self.assertEqual(fetch_all.await_args_list[1].args[1], (7, 7, 7, 7, 7, 7))

    async def test_current_unsettled_rooms_raise_visible_total_floor(self):
        players = [{
            "id": 7,
            "ask_count": 1,
            "ask_count_y": 0,
            "ask_count_n": 1,
            "ask_count_u": 0,
            "ask_count_p": 0,
            "win_count": 0,
            "game_count": 0,
        }]
        with patch.object(
            rooms_router,
            "fetch_all",
            new=AsyncMock(side_effect=[players, [{"id": "A"}, {"id": "B"}]]),
        ):
            result = await rooms_router._history_subject(
                subject_id="self",
                label="我",
                username="tester",
                toy_user_id=42,
            )
        self.assertEqual(result["stats"]["total_games"], 2)


class HistoryEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_must_login(self):
        with self.assertRaises(HTTPException) as raised:
            await rooms_router.history({"id": 1, "is_guest": 1})
        self.assertEqual(raised.exception.status_code, 401)

    async def test_human_history_includes_bound_machines(self):
        self_subject = {
            "id": "self",
            "stats": {"total_games": 1, "win_count": 0, "ask_count": 3},
            "rooms": [],
        }
        machine_subject = {
            "id": "machine-99",
            "stats": {"total_games": 2, "win_count": 1, "ask_count": 8},
            "rooms": [],
        }
        with (
            patch.object(
                rooms_router,
                "_history_subject",
                new=AsyncMock(side_effect=[self_subject, machine_subject]),
            ),
            patch.object(
                rooms_router,
                "fetch_all",
                new=AsyncMock(return_value=[{"id": 99, "username": "小机甲"}]),
            ),
        ):
            result = await rooms_router.history({
                "id": 7,
                "user_id": 42,
                "username": "人类甲",
                "is_guest": 0,
                "is_ai": 0,
            })
        self.assertEqual(result["subjects"], [self_subject, machine_subject])

    async def test_human_history_hides_empty_self_and_empty_machines(self):
        empty_self = {
            "id": "self",
            "stats": {"total_games": 0, "win_count": 0, "ask_count": 0},
            "rooms": [],
        }
        active_machine = {
            "id": "machine-99",
            "stats": {"total_games": 0, "win_count": 0, "ask_count": 0},
            "rooms": [{"id": "ABCD1234"}],
        }
        empty_machine = {
            "id": "machine-100",
            "stats": {"total_games": 0, "win_count": 0, "ask_count": 0},
            "rooms": [],
        }
        with (
            patch.object(
                rooms_router,
                "_history_subject",
                new=AsyncMock(side_effect=[empty_self, active_machine, empty_machine]),
            ),
            patch.object(
                rooms_router,
                "fetch_all",
                new=AsyncMock(return_value=[
                    {"id": 99, "username": "小机甲"},
                    {"id": 100, "username": "小机乙"},
                ]),
            ),
        ):
            result = await rooms_router.history({
                "id": 7,
                "user_id": 42,
                "username": "人类甲",
                "is_guest": 0,
                "is_ai": 0,
            })
        self.assertEqual(result["subjects"], [active_machine])


if __name__ == "__main__":
    unittest.main()
