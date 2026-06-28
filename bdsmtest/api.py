"""bdsmtest.org 官方接口封装。

原站测试流程：
    POST /ajax/init            -> 取得 rauth 与 prelims（人口学预设项）
    POST /ajax/nextquestions   -> 按页拉题，需把已答题回填 testdata.qdata
    POST /ajax/score           -> 提交全部答案
    POST /ajax/getresult       -> 取得各原型百分比

整套流程仅靠 rauth 串联，无需保持同一 HTTP 会话，因此 init / fetch / score
可以分别在独立请求中完成（正好对应逐题作答的交互节奏）。
"""

import time

import requests

API_BASE = "https://bdsmtest.org"
# 原站前端版本号，作为模块级常量；原站偶尔升级，可在此集中更新。
APPVER = "20260424005439"

_UAUTH = {
    "uid": 0,
    "salt": "",
    "authsig": "814a69afc15258000678f00526b0c107ac271b5ea997beb4f7c1e81c861c972b",
}
_USER = {"name": "", "email": "", "key": "", "gender": "", "country": "", "state": ""}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": API_BASE + "/",
}
_TIMEOUT = 20
_RATE_LIMIT_SLEEP = 0.1  # 每次调用后限速，避免触发原站风控
_MAX_PAGES = 80  # nextquestions 分页上限保护
# score 之后服务端算分有延迟，getresult 偶发返回空体；带退避重试几次。
_RESULT_RETRIES = 5
_RESULT_RETRY_SLEEP = 0.6

_UNAVAILABLE = "原站暂时不可用，请稍后再试"


class BdsmApiError(Exception):
    """原站接口异常的友好封装。"""


def _flatten(obj, prefix=""):
    """把嵌套 dict/list 摊平成原站要求的 PHP 风格表单键值对。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}[{k}]" if prefix else str(k)
            out += _flatten(v, key)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += _flatten(v, f"{prefix}[{i}]")
    else:
        if isinstance(obj, bool):
            obj = "true" if obj else "false"
        out.append((prefix, "" if obj is None else str(obj)))
    return out


def _new_session():
    session = requests.Session()
    session.headers.update(_HEADERS)
    return session


def _call(session, endpoint, data):
    data = dict(data)
    # 原站支持中文：lang=zh 时题目 wording 与原型 name 均直接返回中文，
    # 无需本地维护翻译表（也避免题库改版后本地翻译错位）。
    data["lang"] = "zh"
    data["frontend"] = APPVER
    try:
        resp = session.post(API_BASE + endpoint, data=_flatten(data), timeout=_TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise BdsmApiError(_UNAVAILABLE) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise BdsmApiError(_UNAVAILABLE)
    time.sleep(_RATE_LIMIT_SLEEP)
    return payload.get("response") or {}


def init_session():
    """开一局：返回 {'rauth': {...}, 'pdata': {...}}。

    pdata 是人口学预设项（用各项默认值），仅用于后续打分时回填 testdata，
    不影响原型百分比的核心算分。
    """
    session = _new_session()
    ini = _call(session, "/ajax/init", {"uauth": _UAUTH, "user": _USER})
    rauth = ini.get("rauth")
    if not rauth or not rauth.get("rid"):
        raise BdsmApiError(_UNAVAILABLE)
    pdata = {str(p["id"]): p.get("defaultkey") for p in ini.get("prelims", [])}
    # 与原站前端一致的两个固定预设（性别/取向相关项），保证算分口径稳定。
    pdata["20"] = "1"
    pdata["16"] = "2"
    return {"rauth": rauth, "pdata": pdata}


def fetch_questions(rauth, pdata):
    """循环 nextquestions 拉取全部题目，返回 [{'id': int, 'wording': str}, ...]。

    拉题时以中立值（4）回填已答题以驱动翻页；这些占位答案不会进入最终算分，
    真正的算分发生在 submit_and_score 时用用户答案重建 qdata。
    """
    session = _new_session()
    testdata = {
        "gender": "?", "country": "??", "state": "??",
        "pdata": pdata, "qdata": {}, "qemails": {}, "fdata": {},
        "timespent": 1, "questionPageCounter": 0,
        "percentage": 0, "complete": False,
    }
    questions = []
    seen = set()
    for _ in range(_MAX_PAGES):
        nq = _call(
            session,
            "/ajax/nextquestions",
            {"uauth": _UAUTH, "rauth": rauth, "user": _USER, "testdata": testdata},
        )
        page = nq.get("questions", []) or []
        for q in page:
            qid = q.get("id")
            if qid is None or qid in seen:
                continue
            seen.add(qid)
            questions.append({"id": qid, "wording": q.get("wording", "")})
            testdata["qdata"][str(qid)] = 4
        testdata["questionPageCounter"] += 1
        testdata["timespent"] += 2
        testdata["percentage"] = nq.get("progress", 0)
        if not page and nq.get("progress", 0) >= 100:
            break
    if not questions:
        raise BdsmApiError(_UNAVAILABLE)
    return questions


def submit_and_score(rauth, pdata, answers):
    """用户答案 answers={题号id(str/int): 1-7} 提交并算分。

    返回 {'scores': [...按分降序...], 'rid': str}；scores 每项含
    id/name/description/score。
    """
    session = _new_session()
    qdata = {str(k): int(v) for k, v in answers.items()}
    testdata = {
        "gender": "?", "country": "??", "state": "??",
        "pdata": pdata, "qdata": qdata, "qemails": {}, "fdata": {},
        "timespent": 300, "questionPageCounter": 12,
        "percentage": 100, "complete": True,
    }
    _call(
        session,
        "/ajax/score",
        {"uauth": _UAUTH, "rauth": rauth, "testdata": testdata, "user": _USER},
    )
    # 紧跟 score 调 getresult 时，服务端可能尚未算完而返回空体/空结果；带退避重试。
    for attempt in range(_RESULT_RETRIES):
        try:
            res = _call(session, "/ajax/getresult", {"uauth": _UAUTH, "rauth": rauth})
        except BdsmApiError:
            res = {}
        scores = sorted(res.get("scores", []) or [], key=lambda x: -x.get("score", 0))
        if scores:
            return {"scores": scores, "rid": rauth.get("rid")}
        if attempt < _RESULT_RETRIES - 1:
            time.sleep(_RESULT_RETRY_SLEEP * (attempt + 1))
    raise BdsmApiError(_UNAVAILABLE)
