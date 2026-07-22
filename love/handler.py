import os

from scale_test_engine import JsonRpcError, PLAYER_ID_RE, ScaleTestEngine

from . import questions, scoring


DB_PATH = os.getenv("SESSIONS_DB", "/opt/cedartoy/data/sessions.db")
ACCOUNT_DB_PATH = os.getenv(
    "TURTLE_SOUP_DB", "/opt/cedartoy/turtle-soup/backend/turtle_soup.db"
)
GAME = "love"

_engine = ScaleTestEngine(
    game=GAME,
    title="爱之语测试",
    db_path=lambda: DB_PATH,
    questions=questions,
    scoring=scoring,
    answer_min=1,
    answer_max=2,
    prompt=(
        "以下每题两种情境，选更让你心里一动的那个。没有对错，凭直觉，别纠结。"
        "题目里的场景，按你们的相处方式代入即可——线上线下、有没有实体，都不影响作答。"
    ),
    account_db_path=lambda: ACCOUNT_DB_PATH,
)

TOOLS = _engine.tools
handle_mcp = _engine.handle_mcp
love_start = _engine.start
love_answer = _engine.answer
love_answer_batch = _engine.answer_batch
love_get_result = _engine.get_result
love_compare = _engine.compare
love_compare_data = _engine.compare_data
