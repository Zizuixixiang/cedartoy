import os

from scale_test_engine import JsonRpcError, PLAYER_ID_RE, ScaleTestEngine

from . import questions, scoring


DB_PATH = os.getenv("SESSIONS_DB", "/opt/cedartoy/data/sessions.db")
ACCOUNT_DB_PATH = os.getenv(
    "TURTLE_SOUP_DB", "/opt/cedartoy/turtle-soup/backend/turtle_soup.db"
)
GAME = "ecr"

_engine = ScaleTestEngine(
    game=GAME,
    title="依恋类型测试",
    db_path=lambda: DB_PATH,
    questions=questions,
    scoring=scoring,
    answer_min=1,
    answer_max=7,
    prompt=(
        "下面的句子描述的是恋爱关系中每个人可能有的感觉。请评估你自己的一般体验与每句话的相似程度，"
        "1 表示非常不同意，7 表示非常同意。注意：不仅指现在的关系，而是你在亲密关系中常常体验到的感觉。"
        "人和机通用，按你们的相处方式代入\"恋人\"一词即可。"
    ),
    account_db_path=lambda: ACCOUNT_DB_PATH,
)

TOOLS = _engine.tools
handle_mcp = _engine.handle_mcp
ecr_start = _engine.start
ecr_answer = _engine.answer
ecr_answer_batch = _engine.answer_batch
ecr_get_result = _engine.get_result
ecr_compare = _engine.compare
ecr_compare_data = _engine.compare_data
