import os

from scale_test_engine import JsonRpcError, PLAYER_ID_RE, ScaleTestEngine

from . import questions, scoring


DB_PATH = os.getenv("SESSIONS_DB", "/opt/cedartoy/data/sessions.db")
GAME = "sins_virtues"

_engine = ScaleTestEngine(
    game=GAME,
    title="七宗罪 VS 七美德",
    db_path=lambda: DB_PATH,
    questions=questions,
    scoring=scoring,
    answer_min=1,
    answer_max=5,
    prompt=(
        f"{questions.DISCLAIMER}\n"
        "共 35 题，请按 1–5 级同意度凭第一反应作答。这里借用十四个老词做趣味比喻，"
        "测的是欲望与调节可能怎样同时出现，不给人贴好坏标签。"
    ),
    supports_compare=False,
)

TOOLS = _engine.tools
for _tool in TOOLS:
    _tool["description"] = f"{_tool['description']} {questions.DISCLAIMER}"
handle_mcp = _engine.handle_mcp
sins_virtues_start = _engine.start
sins_virtues_answer = _engine.answer
sins_virtues_answer_batch = _engine.answer_batch
sins_virtues_get_result = _engine.get_result
