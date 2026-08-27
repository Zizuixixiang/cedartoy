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
auth_utils_stub.hash_password = lambda password: f"hashed:{password}"
sys.modules["auth_utils"] = auth_utils_stub
database_stub = types.ModuleType("database")
database_stub.DEFAULT_SETTINGS = {
    "judge_prompt": "judge",
    "generate_prompt": "generate",
}
database_stub.execute = AsyncMock(return_value=1)
database_stub.fetch_all = AsyncMock(return_value=[])
database_stub.fetch_one = AsyncMock(return_value=None)
database_stub.get_db = AsyncMock(return_value=None)
database_stub.get_setting = AsyncMock(return_value=None)
sys.modules["database"] = database_stub

import mcp_app  # noqa: E402


def list_body(**kwargs):
    return mcp_app.PlayBody(game="turtle_soup", action="list_puzzles", **kwargs)


class ListPuzzlesTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, *, count=0, items=None, **kwargs):
        fetch_one = AsyncMock(return_value={"c": count})
        fetch_all = AsyncMock(return_value=items or [])
        with (
            patch.object(mcp_app, "fetch_one", new=fetch_one),
            patch.object(mcp_app, "fetch_all", new=fetch_all),
        ):
            result = await mcp_app.play(list_body(**kwargs))
        return result, fetch_one, fetch_all

    async def test_default_pagination_and_public_item_fields(self):
        items = [{"id": 1, "title": "题一", "tags": "本格"}]
        result, _fetch_one, fetch_all = await self.call(count=45, items=items)

        self.assertEqual(result, {
            "items": items,
            "page": 1,
            "page_size": 20,
            "total": 45,
            "total_pages": 3,
            "has_next": True,
            "has_prev": False,
        })
        items_sql = fetch_all.await_args.args[0]
        self.assertEqual(fetch_all.await_args.args[1], (20, 0))
        self.assertNotIn("answer", items_sql)
        self.assertEqual(items_sql.count("surface"), 1)
        self.assertIn("SUBSTR(surface, 1, 10)", items_sql)
        self.assertNotIn("surface", result["items"][0])
        self.assertNotIn("answer", result["items"][0])

    async def test_can_jump_directly_to_second_or_arbitrary_page(self):
        second, _fetch_one, second_fetch = await self.call(
            count=45, page=2, page_size=10,
        )
        arbitrary, _fetch_one, arbitrary_fetch = await self.call(
            count=45, page=5, page_size=10,
        )

        self.assertEqual(second["page"], 2)
        self.assertTrue(second["has_next"])
        self.assertTrue(second["has_prev"])
        self.assertEqual(second_fetch.await_args.args[1], (10, 10))
        self.assertEqual(arbitrary["page"], 5)
        self.assertFalse(arbitrary["has_next"])
        self.assertTrue(arbitrary["has_prev"])
        self.assertEqual(arbitrary_fetch.await_args.args[1], (10, 40))

    async def test_tag_uses_string_contains_filter_and_remains_paginated(self):
        result, fetch_one, fetch_all = await self.call(
            count=12, page=2, page_size=5, tag="红汤",
        )

        count_sql, count_params = fetch_one.await_args.args
        items_sql, items_params = fetch_all.await_args.args
        self.assertIn("COALESCE(tags, '') LIKE ?", count_sql)
        self.assertIn("COALESCE(tags, '') LIKE ?", items_sql)
        self.assertEqual(count_params, ("%红汤%",))
        self.assertEqual(items_params, ("%红汤%", 5, 5))
        self.assertEqual(result["total_pages"], 3)
        self.assertTrue(result["has_next"])
        self.assertTrue(result["has_prev"])

    async def test_q_filters_only_real_title_not_surface(self):
        _result, fetch_one, fetch_all = await self.call(count=1, q="失踪")

        count_sql, count_params = fetch_one.await_args.args
        items_sql, items_params = fetch_all.await_args.args
        items_where = items_sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
        self.assertIn("title LIKE ?", count_sql)
        self.assertNotIn("surface", count_sql)
        self.assertIn("title LIKE ?", items_where)
        self.assertNotIn("surface", items_where)
        self.assertEqual(count_params, ("%失踪%",))
        self.assertEqual(items_params, ("%失踪%", 20, 0))

    async def test_out_of_range_page_returns_empty_items_with_metadata(self):
        result, _fetch_one, fetch_all = await self.call(
            count=21, page=99, page_size=20,
        )

        self.assertEqual(result["items"], [])
        self.assertEqual(result["page"], 99)
        self.assertEqual(result["total"], 21)
        self.assertEqual(result["total_pages"], 2)
        self.assertFalse(result["has_next"])
        self.assertTrue(result["has_prev"])
        self.assertEqual(fetch_all.await_args.args[1], (20, 1960))

    async def test_page_size_rejects_invalid_boundaries(self):
        for page_size in (0, 51):
            with self.subTest(page_size=page_size):
                with (
                    patch.object(mcp_app, "fetch_one", new=AsyncMock()) as fetch_one,
                    patch.object(mcp_app, "fetch_all", new=AsyncMock()) as fetch_all,
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await mcp_app.play(list_body(page_size=page_size))
                self.assertEqual(raised.exception.status_code, 400)
                fetch_one.assert_not_awaited()
                fetch_all.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
