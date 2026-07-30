import os

from scale_test_engine import JsonRpcError, PLAYER_ID_RE, ScaleTestEngine

from . import questions, scoring


DB_PATH = os.getenv("SESSIONS_DB", "/opt/cedartoy/data/sessions.db")
ACCOUNT_DB_PATH = os.getenv(
    "TURTLE_SOUP_DB", "/opt/cedartoy/turtle-soup/backend/turtle_soup.db"
)
GAME = "enneagram"

_engine = ScaleTestEngine(
    game=GAME,
    title="九型人格测试",
    db_path=lambda: DB_PATH,
    questions=questions,
    scoring=scoring,
    answer_min=1,
    answer_max=5,
    prompt=(
        "quick：每题在 A/B 两句中选择更符合你的一句，answer 传 1 表示 A、"
        "传 2 表示 B。full：按陈述符合你的频率作答，1=Almost Never，"
        "2=Rarely，3=Sometimes，4=Often，5=Almost Always。"
        "MCP 始终返回题库英文原文。"
    ),
    supports_compare=False,
    account_db_path=lambda: ACCOUNT_DB_PATH,
    result_detail_values=("full",),
)

TOOLS = _engine.tools
handle_mcp = _engine.handle_mcp
enneagram_start = _engine.start
enneagram_answer = _engine.answer
enneagram_answer_batch = _engine.answer_batch
enneagram_get_result = _engine.get_result
