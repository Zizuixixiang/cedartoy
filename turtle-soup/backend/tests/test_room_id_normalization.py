import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import (  # noqa: E402
    ContentBody,
    GuessBody,
    HintRequestBody,
    HintResponseBody,
    NormalizedRoomId,
    ReportBody,
    RevealAnswerBody,
)
from utils import normalize_room_id  # noqa: E402


class RoomIdNormalizationTests(unittest.TestCase):
    def test_normalize_room_id_strips_label_prefix_and_outer_whitespace(self):
        for raw in ("#KXXEwLoF", " KXXEwLoF ", "  # KXXEwLoF  "):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_room_id(raw), "KXXEwLoF")

    def test_non_string_room_id_still_gets_a_validation_error(self):
        with self.assertRaises(ValidationError):
            ContentBody(room_id=123, content="问题")

    def test_all_request_body_models_share_normalization(self):
        bodies = [
            ContentBody(room_id=" #KXXEwLoF ", content="问题"),
            GuessBody(room_id=" #KXXEwLoF ", content="猜测"),
            HintRequestBody(room_id=" #KXXEwLoF "),
            RevealAnswerBody(room_id=" #KXXEwLoF "),
            HintResponseBody(room_id=" #KXXEwLoF ", log_id=1, accept=True),
            ReportBody(room_id=" #KXXEwLoF "),
        ]
        self.assertTrue(all(body.room_id == "KXXEwLoF" for body in bodies))

    def test_normalized_type_also_applies_to_fastapi_path_parameters(self):
        app = FastAPI()

        @app.get("/rooms/{room_id}")
        async def room(room_id: NormalizedRoomId):
            return room_id

        route = next(route for route in app.routes if isinstance(route, APIRoute))
        field = route.dependant.path_params[0]
        value, errors = field.validate(" #KXXEwLoF ", {}, loc=("path", "room_id"))
        self.assertEqual(errors, [])
        self.assertEqual(value, "KXXEwLoF")


if __name__ == "__main__":
    unittest.main()
