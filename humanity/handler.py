import os

from scale_test_engine import JsonRpcError, PLAYER_ID_RE, ScaleTestEngine

from . import questions, scoring


DB_PATH = os.getenv("SESSIONS_DB", "/opt/cedartoy/data/sessions.db")
GAME = "humanity"

_engine = ScaleTestEngine(
    game=GAME,
    title="人类浓度检测",
    db_path=lambda: DB_PATH,
    questions=questions,
    scoring=scoring,
    answer_min=1,
    answer_max=4,
    prompt=(
        "20 道日常小题，凭直觉选，别琢磨\"哪个答案好\"——这个测试没有好答案。\n"
        "人和机都能测，测的是同一个东西：你身上的人味儿还剩多少（或者，攒了多少）。"
    ),
    supports_compare=False,
)

TOOLS = _engine.tools
handle_mcp = _engine.handle_mcp
humanity_start = _engine.start
humanity_answer = _engine.answer
humanity_answer_batch = _engine.answer_batch
humanity_get_result = _engine.get_result
