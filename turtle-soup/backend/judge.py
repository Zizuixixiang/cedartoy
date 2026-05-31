import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from database import fetch_all, get_setting


fail_counts: dict[int, int] = {}
FAIL_LIMIT = 5
CONFIG_DIR = Path(__file__).resolve().parent / "config"


def _file_prompt() -> str:
    path = CONFIG_DIR / "judge_prompt.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "你是海龟汤游戏裁判。"


async def _judge_prompt() -> str:
    return await get_setting("judge_prompt", _file_prompt())


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


async def _chat(messages: list[dict[str, str]], temperature: float = 0.1) -> str:
    errors: list[str] = []
    available = await _configs()
    if not available:
        raise HTTPException(status_code=503, detail="裁判暂时不可用，请稍后再试")
    for cfg in available:
        cid = int(cfg["id"])
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(
                    _endpoint(cfg["api_url"]),
                    headers={"Authorization": f"Bearer {cfg['api_key']}"},
                    json={
                        "model": cfg["model"],
                        "messages": messages,
                        "temperature": temperature,
                    },
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
    raise HTTPException(status_code=503, detail="裁判暂时不可用，请稍后再试")


async def judge_ask(answer: str, question: str) -> str:
    text = await _chat(
        [
            {"role": "system", "content": await _judge_prompt()},
            {
                "role": "user",
                "content": (
                    "汤底如下，仅用于判断问题，不要泄露：\n"
                    f"{answer}\n\n玩家问题变量：{question}\n"
                    "只返回 yes / no / unrelated / partial 之一。"
                ),
            },
        ]
    )
    value = text.lower().strip("` \n\t。.")
    mapping = {"是": "yes", "否": "no", "不相关": "unrelated", "是也不是": "partial"}
    value = mapping.get(value, value)
    if value not in {"yes", "no", "unrelated", "partial"}:
        return "unrelated"
    return value


async def judge_guess(answer: str, guess: str) -> bool:
    text = await _chat(
        [
            {"role": "system", "content": "你是海龟汤终局裁判。只返回 true 或 false。"},
            {
                "role": "user",
                "content": f"标准汤底变量：{answer}\n玩家猜测变量：{guess}\n是否基本猜中核心真相？",
            },
        ]
    )
    return text.lower().strip("` \n\t。.") in {"true", "yes", "1", "对", "正确"}


async def generate_hint(answer: str, game_log: list[dict[str, Any]]) -> str:
    compact = [
        {"q": r.get("content"), "a": r.get("judgment")}
        for r in game_log
        if r.get("type") == "ask"
    ][-40:]
    text = await _chat(
        [
            {"role": "system", "content": "你是海龟汤主持人。给一句引导性提示，但不能直接泄露汤底。"},
            {
                "role": "user",
                "content": f"汤底变量：{answer}\n已问记录变量：{json.dumps(compact, ensure_ascii=False)}",
            },
        ],
        temperature=0.4,
    )
    return text[:120]


async def generate_puzzle() -> dict[str, str]:
    prompt = await get_setting(
        "generate_prompt",
        "你是海龟汤出题人。返回 JSON，字段 surface 和 answer。生成一道适合多人推理、无血腥露骨描写的中文海龟汤。",
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
        return {"surface": str(data["surface"])[:500], "answer": str(data["answer"])[:1000]}
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
