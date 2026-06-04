import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import HTTPException

from database import DEFAULT_SETTINGS, fetch_all, fetch_one
from utils import ANSWER_LIMIT, SURFACE_LIMIT, TITLE_LIMIT


fail_counts: dict[int, int] = {}
_rr_index: int = 0
_rr_lock = asyncio.Lock()
FAIL_LIMIT = 5
CONFIG_DIR = Path(__file__).resolve().parent / "config"
logger = logging.getLogger(__name__)

STYLE_DESCRIPTIONS = {
    "cozy": '主打"情感的错位与反转"。汤面必须看起来像是某种冷漠、奇怪甚至带有恶意的行为，但汤底揭晓时，其实是极致的保护、笨拙的爱意或温柔的成全。出题发力点参考：误解的善意、跨越时间的约定、隐秘的保护、无法开口的道别、用笨拙方式表达的爱。',
    "absurd": '主打"打破常规预期的思维盲区"。用极其严肃、紧张的语气描述一件小事，最后用一个让人哭笑不得的生活常识或物理逻辑来解构它。出题发力点参考：一本正经的胡说八道、跨频道的沟通、沙雕的巧合、常识被滥用导致的连锁反应、完全对不上的信息差。',
    "mystery": '主打"纯粹的物理与逻辑诡计"。汤面以紧绷的节奏和层叠疑点营造不可能完成的谜题氛围，汤底用时间差、空间结构、物理现象或精妙的不在场证明来自洽解答，通关感来自逻辑的精准扣合。出题发力点参考：密室手法、时间线错觉、职业特征利用、连续事件中的关键缺口、语义的严格歧义。',
    "fantasy": '主打"设定先行且自洽"。允许世界设定里有魔法、异能或奇幻种族，但必须在汤面里给出隐晦暗示，且汤底的解谜必须严格遵循这个设定的物理法则，绝不能机械降神。出题发力点参考：副作用的代价、规则的漏洞、非人物种的日常、能力的边界与禁忌、非人类视角对人类行为的误读。',
    "history": '主打"信息差与时代局限性"。利用现代人的思维定势去审视古代或异国文化中的正常行为。转折必须依托于真实的民俗、冷知识或特定的历史事件。出题发力点参考：被遗忘的旧习俗、时代背景下的无奈、特殊的文化禁忌、名词的古今含义偏移、隐秘职业的操作规范。',
    "scifi": '主打"近未来与技术的细思极恐"。利用赛博朋克、AI意识、记忆篡改等元素出题。汤面是诡异的日常，汤底揭晓其实是代码故障、缸中之脑或意识上传。出题发力点参考：仿生人的图灵测试、记忆备份的漏洞、虚拟现实的边界、人机融合后的认知错位、技术迭代中被遗忘的旧版本人格。',
    "horror": '主打"病态、绝望与心理惊悚"。故事的底色应该带有一丝病态、黑色幽默或极其残酷的冷逻辑，探索人性中最黑暗扭曲的一面。出题发力点参考：病态的爱、极限环境求生、身份认知错乱、致命的误会、伪装的善意。',
}


def _file_judge_prompt() -> str:
    path = CONFIG_DIR / "judge_prompt.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return DEFAULT_SETTINGS["judge_prompt"]


async def _get_judge_prompt() -> str:
    row = await fetch_one("SELECT value FROM settings WHERE key = 'judge_prompt'")
    if row and str(row.get("value") or "").strip():
        return str(row["value"])
    return _file_judge_prompt()


async def _get_judge_prompt_hint() -> str:
    row = await fetch_one("SELECT value FROM settings WHERE key = 'judge_prompt_hint'")
    if row and str(row.get("value") or "").strip():
        return str(row["value"])
    return _file_judge_prompt()


async def _get_judge_prompt_guess() -> str:
    row = await fetch_one("SELECT value FROM settings WHERE key = 'judge_prompt_guess'")
    if row and str(row.get("value") or "").strip():
        return str(row["value"])
    return _file_judge_prompt()


async def _get_generate_prompt() -> str:
    row = await fetch_one("SELECT value FROM settings WHERE key = 'generate_prompt'")
    if row and str(row.get("value") or "").strip():
        return str(row["value"])
    return DEFAULT_SETTINGS["generate_prompt"]


async def _get_judge_prompt_clue() -> str:
    row = await fetch_one("SELECT value FROM settings WHERE key = 'judge_prompt_clue'")
    return str(row["value"]) if row else ""


async def _configs() -> list[dict[str, Any]]:
    rows = await fetch_all(
        "SELECT * FROM judge_api_configs WHERE enabled = 1 ORDER BY priority ASC, id ASC"
    )
    return [r for r in rows if fail_counts.get(int(r["id"]), 0) < FAIL_LIMIT]


def _endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _models_endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return f"{base}/models"


async def _chat(
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    *,
    timeout: float = 45,
    max_tokens: int | None = None,
) -> str:
    global _rr_index
    errors: list[str] = []
    available = await _configs()
    if not available:
        raise HTTPException(status_code=503, detail="裁判暂时不可用，请稍后再试")
    n = len(available)
    async with _rr_lock:
        start = _rr_index % n
        _rr_index = (_rr_index + 1) % n
    for i in range(n):
        cfg = available[(start + i) % n]
        cid = int(cfg["id"])
        try:
            payload: dict[str, Any] = {
                "model": cfg["model"],
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    _endpoint(cfg["api_url"]),
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
            fail_counts[cid] = 0
            return str(text).strip()
        except Exception as exc:
            fail_counts[cid] = fail_counts.get(cid, 0) + 1
            errors.append(f"{cfg.get('name')}: {exc}")
            continue
    logger.warning("judge chat failed across configs: %s", "; ".join(errors))
    raise HTTPException(status_code=503, detail="裁判暂时不可用，请稍后再试")


async def list_models(cfg: dict[str, Any]) -> dict[str, Any]:
    api_key = (cfg.get("api_key") or "").strip()
    api_url = (cfg.get("api_url") or "").strip()
    if not api_url or not api_key:
        return {"success": False, "models": [], "message": "配置缺少 API Key 或接口地址"}

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.get(
                _models_endpoint(api_url),
                headers={"Authorization": f"Bearer {api_key}"},
            )
            try:
                raw = resp.json()
            except Exception:
                raw = {"_raw_text": (resp.text or "")[:4000]}
            if resp.status_code >= 400:
                return {
                    "success": False,
                    "models": [],
                    "raw": raw,
                    "http_status": resp.status_code,
                    "message": f"拉取失败: HTTP {resp.status_code}",
                }
            data = raw.get("data") if isinstance(raw, dict) else raw
            if not isinstance(data, list):
                return {
                    "success": False,
                    "models": [],
                    "raw": raw,
                    "http_status": resp.status_code,
                    "message": "模型列表格式错误",
                }
            models: list[str] = []
            for item in data:
                model_id = item.get("id") if isinstance(item, dict) else item
                if isinstance(model_id, str) and model_id.strip():
                    models.append(model_id.strip())
            models = sorted(set(models))
            return {
                "success": True,
                "models": models,
                "raw": raw,
                "http_status": resp.status_code,
                "message": f"拉取成功，共 {len(models)} 个模型",
            }
    except Exception as exc:
        return {"success": False, "models": [], "message": f"拉取请求失败: {exc}"}


async def _chat_validated(
    messages: list[dict[str, str]],
    validator: Callable[[str], bool],
    max_retry: int = 3,
    log_label: str = "chat",
    **chat_kwargs: Any,
) -> str | None:
    for _ in range(max_retry):
        text = await _chat(messages, **chat_kwargs)
        if validator(text):
            return text
        logger.warning("%s response failed validation: %r", log_label, text[:300])
    return None


def _extract_reply(raw: Any) -> str:
    if not isinstance(raw, dict):
        return str(raw)[:2000]
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    for key in ("output_text", "text", "content"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return json.dumps(raw, ensure_ascii=False)[:2000]


async def test_config(cfg: dict[str, Any]) -> dict[str, Any]:
    api_key = (cfg.get("api_key") or "").strip()
    api_url = (cfg.get("api_url") or "").strip()
    model = (cfg.get("model") or "").strip()
    name = cfg.get("name") or ""

    if not api_url or not api_key:
        return {"success": False, "data": None, "message": "配置缺少 API Key 或接口地址"}
    if not model:
        return {"success": False, "data": None, "message": "请先填写模型名再测试"}

    messages = [
        {"role": "system", "content": "你是连通性测试助手。"},
        {"role": "user", "content": "请只回复：测试成功"},
    ]
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                _endpoint(api_url),
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 32,
                },
            )
            try:
                raw = resp.json()
            except Exception:
                raw = {"_raw_text": (resp.text or "")[:4000]}
            llm_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code >= 400:
                detail = raw if isinstance(raw, dict) else {"_raw_text": str(raw)}
                return {
                    "success": False,
                    "data": {
                        "http_status": resp.status_code,
                        "raw": detail,
                        "config_name": name,
                        "model": model,
                        "llm_ms": llm_ms,
                    },
                    "message": f"测试失败: HTTP {resp.status_code}",
                }
            reply_text = _extract_reply(raw) or "(无文本回复)"
            return {
                "success": True,
                "data": {
                    "reply": reply_text,
                    "raw": raw,
                    "http_status": resp.status_code,
                    "config_name": name,
                    "model": model,
                    "llm_ms": llm_ms,
                },
                "message": "测试成功",
            }
    except Exception as exc:
        return {"success": False, "data": None, "message": f"测试请求失败: {exc}"}


SYSTEM_BUSY_NOTICE = "【系统提示】系统开小差了，请再次提问"
_ASK_CHOICES = {"是", "不是", "无关", "不相关", "没有关联", "是也不是"}
_ASK_MAPPING = {
    "是": "yes",
    "不是": "no",
    "无关": "unrelated",
    "不相关": "unrelated",
    "没有关联": "unrelated",
    "是也不是": "partial",
}
_CLUE_PREFIX = "【线索公布】"
_CLUE_SUFFIX = "【线索公布结束】"


def _ask_first_line_valid(text: str) -> bool:
    lines = [line for line in _strip_code_fence(text).splitlines() if line.strip()]
    return bool(lines) and lines[0].strip() in _ASK_CHOICES


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _extract_clue_from_ask(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(_CLUE_PREFIX):
            clue_lines = [stripped[len(_CLUE_PREFIX) :].strip()]
            for next_line in lines[index + 1 :]:
                next_stripped = next_line.strip()
                if not next_stripped:
                    continue
                if next_stripped.startswith("【"):
                    break
                clue_lines.append(next_stripped)
            content = "\n".join(line for line in clue_lines if line).strip()
            return content or None
    return None


def _extract_clue_from_answer(answer: str, triggered_clue: str | None = None) -> str | None:
    segments: list[str] = []
    search_start = 0
    while True:
        marker_index = answer.find(_CLUE_PREFIX, search_start)
        if marker_index < 0:
            break
        clue_start = marker_index + len(_CLUE_PREFIX)
        suffix_index = answer.find(_CLUE_SUFFIX, clue_start)
        if suffix_index >= 0:
            content = answer[clue_start:suffix_index].strip()
            search_start = suffix_index + len(_CLUE_SUFFIX)
        else:
            content = _extract_legacy_clue_segment(answer[clue_start:])
            search_start = clue_start + len(content)
        if content:
            segments.append(content)
    if not segments:
        return None
    clue = (triggered_clue or "").strip()
    if clue:
        for segment in segments:
            if clue in segment or segment in clue:
                return segment
    return segments[0]


def _extract_legacy_clue_segment(clue_text: str) -> str:
    clue_lines: list[str] = []
    for line in clue_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if clue_lines:
                clue_lines.append("")
            continue
        if stripped.startswith("【"):
            break
        clue_lines.append(stripped)
    return "\n".join(clue_lines).strip()


async def judge_ask(surface: str, answer: str, question: str) -> dict[str, str | None]:
    has_clue = "【线索公布】" in answer
    system = await _get_judge_prompt()
    if has_clue:
        clue_prompt = await _get_judge_prompt_clue()
        if clue_prompt:
            system = system + "\n\n" + clue_prompt
    ask_instruction = (
        "本次请求类型是普通提问判定。第一行必须且只能是以下之一："
        "是、不是、无关、是也不是。不要输出 yes/no/unrelated/partial，"
        "不要输出通关格式。"
    )
    if has_clue:
        ask_instruction += "若触发线索，可在第二行输出【线索公布】。实际公布内容会从汤底的【线索公布】与【线索公布结束】之间读取。"
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": ask_instruction},
        {
            "role": "user",
            "content": (
                "请判定下面的玩家问题。\n"
                f"汤面：{surface}\n"
                f"汤底：{answer}\n"
                f"玩家问题：{question}"
            ),
        },
    ]
    text = await _chat_validated(messages, _ask_first_line_valid)
    if text is None:
        return {
            "judgment": "unrelated",
            "clue": None,
            "content_override": SYSTEM_BUSY_NOTICE,
        }
    first_line = next(line.strip() for line in _strip_code_fence(text).splitlines() if line.strip())
    clue = _extract_clue_from_ask(text)
    if not has_clue:
        clue = None
    elif clue is not None:
        clue = _extract_clue_from_answer(answer, clue) or clue
    return {
        "judgment": _ASK_MAPPING[first_line],
        "clue": clue,
    }


def _parse_guess_result(text: str) -> dict[str, Any] | None:
    cleaned = _strip_code_fence(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0] not in {"【通关】", "【未通关】"}:
        return None
    score_match = re.search(r"\d+", lines[1])
    if not score_match:
        return None
    answer_text = None
    marker = "【汤底】"
    marker_idx = cleaned.find(marker)
    if marker_idx >= 0:
        answer_text = cleaned[marker_idx + len(marker) :].strip() or None
    return {
        "success": lines[0] == "【通关】",
        "score": int(score_match.group(0)),
        "answer": answer_text,
    }


async def judge_guess(surface: str, answer: str, guess: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": await _get_judge_prompt_guess()},
        {
            "role": "system",
            "content": (
                "本次请求类型是猜测汤底，不是普通提问。禁止只回答 是/不是/无关/是也不是。"
                "必须严格返回以下二选一格式：\n"
                "【未通关】\n还原度：xx%\n"
                "或\n"
                "【通关】\n还原度：xx%\n【汤底】完整汤底故事文本"
            ),
        },
        {
            "role": "user",
            "content": (
                "请评估下面的玩家汤底猜测。\n"
                f"汤面：{surface}\n"
                f"汤底：{answer}\n"
                f"玩家猜测：{guess}"
            ),
        },
    ]
    text = await _chat_validated(messages, lambda value: _parse_guess_result(value) is not None)
    if text is None:
        return {"success": False, "score": 0, "answer": None, "error": SYSTEM_BUSY_NOTICE}
    return _parse_guess_result(text) or {
        "success": False,
        "score": 0,
        "answer": None,
        "error": SYSTEM_BUSY_NOTICE,
    }


async def generate_hint(surface: str, answer: str, game_log: list[dict[str, Any]]) -> str | None:
    compact = [
        {"q": r.get("content"), "a": r.get("judgment")}
        for r in game_log
        if r.get("type") == "ask"
    ][-40:]
    previous_hints = [
        log["hint_text"] for log in game_log
        if log.get("hint_text") and log.get("judgment") != "auto_hint"
    ]
    hint_system_content = (
        "本次请求类型是用户申请提示。不要执行线索汤专用特殊规则，"
        "不要输出【线索公布】，不要泄露完整汤底。"
        "只给一个基于汤面、汤底和已问记录的温和提示。"
        "必须以【提示】开头，总字数不超过 30 字，必须是无标点的一句话。"
    )
    if previous_hints:
        numbered_list = "\n".join(
            f"{index}. {hint}" for index, hint in enumerate(previous_hints, start=1)
        )
        hint_system_content += (
            "\n\n已经提供过以下提示，请给出完全不同方向的新线索，不要重复任何已有提示：\n"
            f"{numbered_list}"
        )
    messages = [
        {"role": "system", "content": await _get_judge_prompt_hint()},
        {
            "role": "system",
            "content": hint_system_content,
        },
        {
            "role": "user",
            "content": (
                "用户申请提示。\n"
                f"汤面：{surface}\n"
                f"汤底：{answer}\n"
                f"已问记录：{json.dumps(compact, ensure_ascii=False)}"
            ),
        },
    ]
    text = await _chat_validated(
        messages,
        _valid_hint_response,
        max_retry=3,
        log_label="generate_hint",
        timeout=12,
        max_tokens=180,
    )
    if text is None:
        return None
    return text.strip()[len("【提示】") :].strip()[:120]


def _valid_hint_response(value: str) -> bool:
    text = value.strip()
    if not text.startswith("【提示】"):
        return False
    hint = text[len("【提示】") :].strip()
    if len(hint) < 6:
        return False
    if "【线索公布】" in hint:
        return False
    dangling_suffixes = ("的", "了", "是", "在", "和", "与", "把", "被", "从", "向", "对", "将", "让", "但", "而")
    return not hint.endswith(dangling_suffixes)


async def generate_puzzle(style: str = "horror") -> dict[str, str]:
    prompt = await _get_generate_prompt()
    style_desc = STYLE_DESCRIPTIONS.get(style, STYLE_DESCRIPTIONS["horror"])
    prompt = prompt.replace("{style_description}", style_desc)
    prompt = (
        f"{prompt}\n\n"
        "本次返回的 JSON 必须包含 title、surface、answer 三个字段；"
        f"title 是单独题目标题，不是汤面，不超过 {TITLE_LIMIT} 字；surface 不超过 {SURFACE_LIMIT} 字；answer 不超过 {ANSWER_LIMIT} 字。"
    )
    text = await _chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请按系统提示生成题目，只返回 JSON。"},
        ],
        temperature=0.8,
    )
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        data = json.loads(text[start:end])
        return {
            "title": str(data.get("title") or "")[:TITLE_LIMIT],
            "surface": str(data["surface"])[:SURFACE_LIMIT],
            "answer": str(data["answer"])[:ANSWER_LIMIT],
        }
    except Exception:
        raise HTTPException(status_code=502, detail="AI 生成结果格式错误") from None


async def scan_text(text: str) -> str | None:
    result = await _chat(
        [
            {"role": "system", "content": "判断文本是否含侮辱、骚扰、违法或明显违规内容。只返回 safe 或 unsafe:理由。"},
            {"role": "user", "content": f"待检测文本变量：{text[:1000]}"},
        ]
    )
    if result.lower().startswith("unsafe"):
        return result.split(":", 1)[-1].strip() or "疑似违规"
    return None
