"""Account-scoped puzzle box; no import-time DB writes or LLM adjudication."""

import random
import re
import sqlite3
import unicodedata
import game_activity
from contextlib import closing, contextmanager

from puzzle_box_data import PROMPT, PUZZLES

GUIDE = '''解谜盲盒 · Runsheng_（小红书 _Sssonnet0220）原创并授权收录。
22 道独立题（16 普通 + 6 挑战），无积分、排行或顺序要求。

这类题不一定要自己一口气解完，也很适合拉上人类一起拆。解到一半时，可以把当前发现、猜测或卡住的地方说出来，一起猜接下来会是什么；如果已经解出了谜底，也可以先问问人类想不想自己猜一下，再决定什么时候揭晓。怎么玩都可以，重点是一起玩得开心。

统一调用 play(game="puzzle_box", action=..., params={...})：
draw 随机拆一道未拆题；open {puzzle_id:"N01"} 指定打开/回看（N01–N16、H01–H06，也可编号 1–22）。
list / progress 仅列标题、状态和汇总；submit {puzzle_id:"...", answer:"最终谜底"} 由代码判定。
N03 的 answer 可用两条断句组成的字符串数组，也可分行提交；其他题只提交最终谜底，开放回应请放在工具调用外。
check_step {puzzle_id:"...", checkpoint_id:"...", answer:...} 仅校验当前题提供的步骤，不算解完。
draw/open 会附作者原文提示；只有正确 submit 才标记已解。错误不公开答案。
已拆题永不回到随机池；全部拆过后可 open 回看待解题。
需要已登录的小机账号；进度按 AI 账号永久记录，不分存档槽，不支持重置/导入覆盖。'''


def init_db(db_path):
    with closing(sqlite3.connect(db_path, timeout=15)) as conn, conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS puzzle_box_progress (
            ai_user_id INTEGER NOT NULL,
            puzzle_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('opened', 'solved')),
            opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            solved_at TEXT,
            PRIMARY KEY (ai_user_id, puzzle_id)
        )''')


@contextmanager
def _connection(db_path, write=False):
    init_db(db_path)
    conn = sqlite3.connect(db_path, timeout=15)
    try:
        # Serialize pool selection + claim, including simultaneous MCP requests.
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def puzzle_id(value):
    value = str(value).strip().upper()
    if value.isascii() and value.isdigit() and 1 <= int(value) <= 22:
        n = int(value)
        value = f"N{n:02}" if n <= 16 else f"H{n - 16:02}"
    if value not in PUZZLES:
        raise ValueError("未知题号；使用 N01–N16、H01–H06 或 1–22")
    return value


def _brief(pid, status):
    p = PUZZLES[pid]
    return {"id": pid, "title": p["title"], "challenge": p["challenge"], "status": status}


def catalog():
    return {"items": [_brief(pid, None) for pid in PUZZLES], "summary": None}


def _status(conn, ai_id, pid):
    row = conn.execute("SELECT status FROM puzzle_box_progress WHERE ai_user_id=? AND puzzle_id=?",
                       (ai_id, pid)).fetchone()
    return row[0] if row else "unseen"


def progress(db_path, ai_id):
    with _connection(db_path) as conn:
        states = dict(conn.execute("SELECT puzzle_id,status FROM puzzle_box_progress WHERE ai_user_id=?", (ai_id,)))
    items = [_brief(pid, states.get(pid, "unseen")) for pid in PUZZLES]
    return {"items": items, "summary": {s: sum(p["status"] == s for p in items) for s in ("solved", "opened", "unseen")}}


def _unwrap(value):
    if not isinstance(value, str) or len(value) > 512:
        return ""
    value = unicodedata.normalize("NFKC", value).strip()
    value = re.sub(r"^(?:(?:最终)?(?:答案|谜底)(?:是|为)?\s*[:：]?\s*|(?:the\s+)?answer\s*(?:is\s*|:\s*))", "", value, flags=re.I)
    return value.strip().strip('"“”‘’「」『』`')


def normalize(value):
    value = _unwrap(value)
    if re.search(r"[\u3400-\u9fff]", value):
        return "".join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith("P"))
    value = value.casefold().translate(str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"}))
    # Apostrophes may be straight/curly or omitted in a decoded letter stream.
    value = value.replace("'", "")
    value = re.sub(r'[.,!?;:，。！？；："“”]', " ", value)
    return " ".join(value.split())


def _two_readings(value):
    if isinstance(value, list):
        if not all(isinstance(v, str) for v in value):
            return False
        value = "\n".join(value)
    value = _unwrap(value)
    # Both exact character sequences must occur, with different clause boundaries.
    # Restrict separators to punctuation/space; never accept inserted Hanzi.
    breaks = set()
    for match in re.finditer(r"(?<!\w)只要你想([^\w]*)我([^\w]*)就会回来(?!\w)", value):
        left, right = match.groups()
        if re.search(r"[,，。.!！?？;；:：、—…\n]", left) and not right.strip():
            breaks.add(4)
        if re.search(r"[,，。.!！?？;；:：、—…\n]", right) and not left.strip():
            breaks.add(5)
    return breaks == {4, 5}


def validate(pid, value):
    if pid == "N03":
        return _two_readings(value)
    return bool(normalize(value)) and normalize(value) == normalize(PUZZLES[pid]["answer"])


def _check_step(pid, checkpoint, value):
    expected = PUZZLES[pid]["checkpoints"][checkpoint]
    if checkpoint == "joined_base64":
        return isinstance(value, str) and value.strip() == expected
    if checkpoint in {"sudoku", "flattened_indices", "deletion_positions"}:
        def numbers(v):
            if isinstance(v, list):
                v = str(v)
            if not isinstance(v, str) or not re.fullmatch(r"[\d\s,，/;；\[\]]+", v):
                return None
            return re.findall(r"\d+", v)
        return numbers(value) == numbers(expected)
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return normalize(value) == normalize(expected)


def play(db_path, ai_id, action, params):
    if action in {"list", "progress"}:
        return progress(db_path, ai_id)
    if action not in {"draw", "open", "submit", "check_step"}:
        raise ValueError("支持 draw/open/list/progress/submit/check_step")
    with _connection(db_path, write=True) as conn:
        if action == "draw":
            seen = {row[0] for row in conn.execute("SELECT puzzle_id FROM puzzle_box_progress WHERE ai_user_id=?", (ai_id,))}
            pool = [pid for pid in PUZZLES if pid not in seen]
            if not pool:
                return {"ok": False, "message": "都已拆过，可回看待解题"}
            pid = random.choice(pool)
        else:
            pid = puzzle_id(params.get("puzzle_id", ""))
        status = _status(conn, ai_id, pid)
        if action in {"draw", "open"}:
            game_activity.observe_change(status, "opened" if status == "unseen" else status)
            if status == "unseen":
                conn.execute("INSERT INTO puzzle_box_progress(ai_user_id,puzzle_id,status) VALUES(?,?,'opened')", (ai_id, pid))
                status = "opened"
            result = _brief(pid, status)
            result.update(prompt=PROMPT, body=PUZZLES[pid]["body"])
            if PUZZLES[pid]["checkpoints"]:
                result["checkpoints"] = list(PUZZLES[pid]["checkpoints"])
            return result
        if status == "unseen":
            return {"ok": False, "message": "请先 draw 或 open 这道题"}
        if action == "check_step":
            checkpoint = params.get("checkpoint_id")
            if not isinstance(checkpoint, str) or checkpoint not in PUZZLES[pid]["checkpoints"]:
                raise ValueError("该题没有此 checkpoint；可 open 查看可用步骤编号")
            return {"correct": _check_step(pid, checkpoint, params.get("answer"))}
        correct = validate(pid, params.get("answer"))
        if correct and status != "solved":
            conn.execute("UPDATE puzzle_box_progress SET status='solved',solved_at=CURRENT_TIMESTAMP WHERE ai_user_id=? AND puzzle_id=?", (ai_id, pid))
            status = "solved"
        return {"id": pid, "correct": correct, "status": status}


def reveal(db_path, ai_id, pid, confirmed=False):
    """Human-only HTTP boundary authenticates the binding before calling here."""
    pid = puzzle_id(pid)
    with _connection(db_path) as conn:
        status = _status(conn, ai_id, pid)
    if status != "solved" and confirmed is not True:
        return {"confirmation_required": True, "message": "以下内容包含完整剧透，会看到标准谜底。"}
    p = PUZZLES[pid]
    return {**_brief(pid, status), "steps": p["steps"], "answer": p["answer"]}
