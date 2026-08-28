import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from database import DEFAULT_SETTINGS, fetch_all, fetch_one
from utils import ANSWER_LIMIT, SURFACE_LIMIT, TITLE_LIMIT


POOL_NAMES = ("judge", "hint", "npc")
_rr_index: dict[str, dict[int, int]] = {pool: {} for pool in POOL_NAMES}
_rr_locks: dict[str, asyncio.Lock] = {pool: asyncio.Lock() for pool in POOL_NAMES}
# Locks and health belong to the physical API credential, not a database row.
# This prevents an administrator from duplicating one key into several purpose
# rows to evade node-level serialization or cooldown.
_config_locks: dict[str, asyncio.Lock] = {}
_config_node_keys: dict[int, str] = {}
_guess_lock = asyncio.Lock()
FAIL_LIMIT = 3
COOLDOWN_SECONDS = (60, 120, 300)
RATE_LIMIT_MIN_COOLDOWN_SECONDS = 120
DEEPSEEK_V4_NODE_TIMEOUT = 30.0
DEEPSEEK_V4_MIN_MAX_TOKENS = 8192
CONFIG_DIR = Path(__file__).resolve().parent / "config"
NPC_API_MAX_CONCURRENCY = max(1, int(os.getenv("NPC_API_MAX_CONCURRENCY", "4")))
NPC_API_QUEUE_TIMEOUT_SECONDS = max(
    0.1, float(os.getenv("NPC_API_QUEUE_TIMEOUT_SECONDS", "2"))
)
NPC_CHAT_MAX_MESSAGES = 20
NPC_CHAT_MAX_CONTENT_LENGTH = 4000
NPC_CHAT_MAX_TOTAL_CONTENT_LENGTH = 12000
logger = logging.getLogger(__name__)


@dataclass
class ConfigRuntimeState:
    consecutive_failures: int = 0
    cooldown_stage: int = -1
    cooldown_until: float = 0.0
    probe_in_flight: bool = False
    last_error: str | None = None
    last_success_at: str | None = None


_runtime_states: dict[str, ConfigRuntimeState] = {}
_npc_semaphore: asyncio.Semaphore | None = None
_npc_semaphore_loop: asyncio.AbstractEventLoop | None = None
_npc_active = 0
_npc_waiting = 0
_priority_waiters = 0

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


def _pool_name(pool: str) -> str:
    return pool if pool in POOL_NAMES else "judge"


def _node_key(cfg: dict[str, Any]) -> str:
    endpoint = _endpoint(str(cfg.get("api_url") or "").strip()).lower()
    api_key = str(cfg.get("api_key") or "").strip()
    material = f"{endpoint}\0{api_key}".encode("utf-8")
    return "node:" + hashlib.sha256(material).hexdigest()


def _merge_runtime_state(target: ConfigRuntimeState, source: ConfigRuntimeState) -> None:
    target.consecutive_failures = max(
        target.consecutive_failures, source.consecutive_failures
    )
    target.cooldown_stage = max(target.cooldown_stage, source.cooldown_stage)
    target.cooldown_until = max(target.cooldown_until, source.cooldown_until)
    target.probe_in_flight = target.probe_in_flight or source.probe_in_flight
    target.last_error = source.last_error or target.last_error
    target.last_success_at = source.last_success_at or target.last_success_at


def register_config_runtime_node(cfg: dict[str, Any]) -> str:
    """Bind one database row to shared physical-node runtime state."""
    config_id = int(cfg["id"])
    key = _node_key(cfg)
    previous = _config_node_keys.get(config_id)
    _config_node_keys[config_id] = key
    legacy_key = f"config:{config_id}"
    if legacy_key in _runtime_states:
        legacy = _runtime_states.pop(legacy_key)
        _merge_runtime_state(_runtime_states.setdefault(key, ConfigRuntimeState()), legacy)
    if previous and previous != key and not any(
        mapped == previous for cid, mapped in _config_node_keys.items()
        if cid != config_id
    ):
        _config_locks.pop(previous, None)
        _runtime_states.pop(previous, None)
    return key


def _runtime_key(config_id: int) -> str:
    return _config_node_keys.get(int(config_id), f"config:{int(config_id)}")


def _config_lock(cfg: dict[str, Any]) -> asyncio.Lock:
    key = register_config_runtime_node(cfg)
    lock = _config_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _config_locks[key] = lock
    return lock


def _now() -> float:
    return time.monotonic()


def _wall_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _state(config_id: int) -> ConfigRuntimeState:
    return _runtime_states.setdefault(_runtime_key(config_id), ConfigRuntimeState())


def _runtime_status(state: ConfigRuntimeState, now: float | None = None) -> str:
    now = _now() if now is None else now
    if state.cooldown_until > now:
        return "cooling"
    if state.cooldown_stage >= 0 or state.probe_in_flight:
        return "half_open"
    return "healthy"


def get_config_runtime_status(config_id: int, *, enabled: bool = True) -> dict[str, Any]:
    state = _runtime_states.get(_runtime_key(config_id), ConfigRuntimeState())
    now = _now()
    remaining = max(0, math.ceil(state.cooldown_until - now))
    return {
        "runtime_status": "disabled" if not enabled else _runtime_status(state, now),
        "consecutive_failures": state.consecutive_failures,
        "cooldown_remaining_seconds": remaining,
        "last_error": state.last_error,
        "last_success_at": state.last_success_at,
    }


def _config_available(config_id: int, now: float | None = None) -> bool:
    state = _runtime_states.get(_runtime_key(config_id))
    if state is None:
        return True
    now = _now() if now is None else now
    return state.cooldown_until <= now and not state.probe_in_flight


def _claim_attempt(config_id: int) -> bool | None:
    """Return whether this attempt is half-open, or None when it must be skipped."""
    state = _state(config_id)
    now = _now()
    if state.cooldown_until > now or state.probe_in_flight:
        return None
    is_probe = state.cooldown_stage >= 0
    if is_probe:
        state.probe_in_flight = True
    return is_probe


def _retry_after_seconds(exc: Exception) -> float | None:
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code != 429:
        return None
    value = exc.response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _classify_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        category = "429" if status == 429 else f"http_{status // 100}xx"
        return category, f"HTTP {status} {exc.response.reason_phrase}".strip()
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "connect", type(exc).__name__
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return "timeout", type(exc).__name__
    if isinstance(exc, httpx.HTTPError):
        return "network", type(exc).__name__
    return "response", f"{type(exc).__name__}: {exc}"


def _record_failure(config_id: int, exc: Exception, *, was_probe: bool) -> str:
    state = _state(config_id)
    state.probe_in_flight = False
    state.consecutive_failures += 1
    category, detail = _classify_error(exc)
    state.last_error = f"{category}: {detail}"

    retry_after = _retry_after_seconds(exc)
    should_cool = was_probe or state.consecutive_failures >= FAIL_LIMIT or category == "429"
    if should_cool:
        next_stage = min(state.cooldown_stage + 1, len(COOLDOWN_SECONDS) - 1)
        if category == "429":
            next_stage = max(next_stage, 1)
        state.cooldown_stage = next_stage
        duration = float(COOLDOWN_SECONDS[next_stage])
        if category == "429":
            duration = max(duration, RATE_LIMIT_MIN_COOLDOWN_SECONDS)
        if retry_after is not None:
            duration = max(duration, retry_after)
        state.cooldown_until = _now() + duration
    return state.last_error


def _record_success(config_id: int) -> None:
    state = _state(config_id)
    state.consecutive_failures = 0
    state.cooldown_stage = -1
    state.cooldown_until = 0.0
    state.probe_in_flight = False
    state.last_error = None
    state.last_success_at = _wall_now_iso()


def mark_config_success(config_id: int) -> None:
    _record_success(config_id)


def _release_probe(config_id: int) -> None:
    state = _runtime_states.get(_runtime_key(config_id))
    if state is not None:
        state.probe_in_flight = False


async def _configs(pool: str = "judge") -> list[dict[str, Any]]:
    rows = await _matching_configs(pool)
    now = _now()
    return [row for row in rows if _config_available(int(row["id"]), now)]


async def _matching_configs(pool: str = "judge") -> list[dict[str, Any]]:
    pool = _pool_name(pool)
    rows = await fetch_all(
        "SELECT * FROM judge_api_configs WHERE enabled = 1 ORDER BY priority ASC, id ASC"
    )
    matching = [row for row in rows if _purpose_matches(row.get("purpose"), pool)]
    for row in rows:
        register_config_runtime_node(row)
    return matching


def _purpose_matches(config_purpose: Any, pool: str) -> bool:
    purpose = str(config_purpose or "judge").strip().lower()
    pool = _pool_name(pool)
    if purpose == "all":
        return True
    if purpose == "both":
        return pool in {"judge", "hint"}
    return purpose == pool


def reset_fail_counts(config_id: int | None = None, purpose: str | None = None) -> None:
    del purpose  # Runtime health belongs to the API node, not to its scheduling pool.
    if config_id is None:
        _runtime_states.clear()
        _config_node_keys.clear()
    else:
        _runtime_states.pop(_runtime_key(config_id), None)


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


def _is_official_deepseek_v4(cfg: dict[str, Any]) -> bool:
    try:
        host = (urlsplit(str(cfg.get("api_url") or "")).hostname or "").lower()
    except ValueError:
        return False
    model = str(cfg.get("model") or "").lower()
    return host == "api.deepseek.com" and model.startswith("deepseek-v4-")


def _is_openrouter_gpt_oss(cfg: dict[str, Any]) -> bool:
    try:
        host = (urlsplit(str(cfg.get("api_url") or "")).hostname or "").lower()
    except ValueError:
        return False
    model = str(cfg.get("model") or "").lower()
    return host == "openrouter.ai" and model.startswith("openai/gpt-oss-")


def _request_timeout(cfg: dict[str, Any], timeout: float) -> float | httpx.Timeout:
    if not _is_official_deepseek_v4(cfg):
        return timeout
    return httpx.Timeout(timeout, read=DEEPSEEK_V4_NODE_TIMEOUT)


def _request_max_tokens(cfg: dict[str, Any], max_tokens: int | None) -> int | None:
    if max_tokens is None or not _is_official_deepseek_v4(cfg):
        return max_tokens
    return max(max_tokens, DEEPSEEK_V4_MIN_MAX_TOKENS)


async def _post_chat_completion(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    timeout: float,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_request_timeout(cfg, timeout)) as client:
        return await client.post(
            _endpoint(cfg["api_url"]),
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json=payload,
        )


async def _layer_start(pool: str, priority: int, size: int) -> int:
    async with _rr_locks[pool]:
        indices = _rr_index[pool]
        start = indices.get(priority, 0) % size
        indices[priority] = (start + 1) % size
        return start


def _npc_limit_semaphore() -> asyncio.Semaphore:
    global _npc_semaphore, _npc_semaphore_loop
    loop = asyncio.get_running_loop()
    if _npc_semaphore is None or _npc_semaphore_loop is not loop:
        _npc_semaphore = asyncio.Semaphore(NPC_API_MAX_CONCURRENCY)
        _npc_semaphore_loop = loop
    return _npc_semaphore


def get_pool_runtime_status() -> dict[str, Any]:
    return {
        "npc": {
            "active": _npc_active,
            "waiting": _npc_waiting,
            "max_concurrency": NPC_API_MAX_CONCURRENCY,
            "queue_timeout_seconds": NPC_API_QUEUE_TIMEOUT_SECONDS,
        },
        "priority_waiters": _priority_waiters,
    }


async def _chat(
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    *,
    timeout: float = 20,
    max_tokens: int | None = None,
    pool: str = "judge",
) -> str:
    global _npc_active, _npc_waiting, _priority_waiters
    pool = _pool_name(pool)
    if pool == "npc":
        semaphore = _npc_limit_semaphore()
        _npc_waiting += 1
        try:
            await asyncio.wait_for(
                semaphore.acquire(), timeout=NPC_API_QUEUE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=503, detail="NPC 通道繁忙，请稍后再试") from exc
        finally:
            _npc_waiting -= 1
        _npc_active += 1
        try:
            return await _chat_from_pool(
                messages,
                temperature,
                timeout=timeout,
                max_tokens=max_tokens,
                pool=pool,
            )
        finally:
            _npc_active -= 1
            semaphore.release()

    _priority_waiters += 1
    try:
        return await _chat_from_pool(
            messages,
            temperature,
            timeout=timeout,
            max_tokens=max_tokens,
            pool=pool,
        )
    finally:
        _priority_waiters -= 1


async def _chat_from_pool(
    messages: list[dict[str, str]],
    temperature: float,
    *,
    timeout: float,
    max_tokens: int | None,
    pool: str,
) -> str:
    errors: list[str] = []
    available = await _configs(pool)
    if not available:
        matching = await _matching_configs(pool)
        if matching:
            logger.warning("%s configs are cooling or already probing", pool)
        else:
            logger.warning("%s has no enabled API configs", pool)
        detail = (
            "NPC 通道繁忙，请稍后再试"
            if pool == "npc"
            else "裁判暂时不可用，请稍后再试"
        )
        raise HTTPException(status_code=503, detail=detail)
    layers: dict[int, list[dict[str, Any]]] = {}
    for cfg in sorted(available, key=lambda item: (int(item.get("priority") or 0), int(item["id"]))):
        layers.setdefault(int(cfg.get("priority") or 0), []).append(cfg)

    for priority, candidates in layers.items():
        start = await _layer_start(pool, priority, len(candidates))
        for i in range(len(candidates)):
            cfg = candidates[(start + i) % len(candidates)]
            cid = int(cfg["id"])
            config_lock = _config_lock(cfg)
            # NPC work never queues behind a busy shared node and never starts
            # while a judge/hint request is trying to acquire capacity. A
            # request already in flight is allowed to finish normally.
            if pool == "npc" and (_priority_waiters > 0 or config_lock.locked()):
                continue
            async with config_lock:
                is_probe = _claim_attempt(cid)
                if is_probe is None:
                    continue
                try:
                    payload: dict[str, Any] = {
                        "model": cfg["model"],
                        "messages": messages,
                        "temperature": temperature,
                    }
                    if _is_openrouter_gpt_oss(cfg):
                        payload["reasoning"] = {"effort": "low", "exclude": True}
                    request_max_tokens = _request_max_tokens(cfg, max_tokens)
                    if request_max_tokens is not None:
                        payload["max_tokens"] = request_max_tokens
                    request = _post_chat_completion(cfg, payload, timeout)
                    if _is_official_deepseek_v4(cfg):
                        resp = await asyncio.wait_for(
                            request,
                            timeout=DEEPSEEK_V4_NODE_TIMEOUT,
                        )
                    else:
                        resp = await request
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise RuntimeError("response truncated due to max_tokens")
                    text = choice["message"]["content"]
                    if not isinstance(text, str) or not text.strip():
                        raise RuntimeError("empty response content")
                except asyncio.CancelledError:
                    _release_probe(cid)
                    raise
                except Exception as exc:
                    error = _record_failure(cid, exc, was_probe=is_probe)
                    errors.append(f"{cfg.get('name')}: {error}")
                    continue
                _record_success(cid)
                return text.strip()
    logger.warning("%s chat failed across configs: %s", pool, "; ".join(errors))
    detail = (
        "NPC 通道繁忙，请稍后再试"
        if pool == "npc"
        else "裁判暂时不可用，请稍后再试"
    )
    raise HTTPException(status_code=503, detail=detail)


async def npc_chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 512,
    timeout: float = 20,
) -> str:
    """Internal compact NPC completion entry point; it is not an HTTP route."""
    if (
        not isinstance(messages, list)
        or not 1 <= len(messages) <= NPC_CHAT_MAX_MESSAGES
    ):
        raise ValueError(f"messages 必须包含 1–{NPC_CHAT_MAX_MESSAGES} 条消息")
    compact: list[dict[str, str]] = []
    total_length = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("每条 NPC message 只能包含 role 和 content")
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise ValueError("NPC message role 无效")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("NPC message content 不能为空")
        content = content.strip()
        if len(content) > NPC_CHAT_MAX_CONTENT_LENGTH:
            raise ValueError("单条 NPC message 过长")
        total_length += len(content)
        compact.append({"role": role, "content": content})
    if total_length > NPC_CHAT_MAX_TOTAL_CONTENT_LENGTH:
        raise ValueError("NPC messages 总长度过长")
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 1 <= max_tokens <= 4096
    ):
        raise ValueError("max_tokens 必须是 1–4096 的整数")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 1 <= timeout <= 60
    ):
        raise ValueError("timeout 必须在 1–60 秒之间")
    return await _chat(
        compact,
        temperature=0.3,
        timeout=float(timeout),
        max_tokens=max_tokens,
        pool="npc",
    )


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
    retry_messages = messages
    for _ in range(max_retry):
        text = await _chat(retry_messages, **chat_kwargs)
        if validator(text):
            return text
        logger.warning("%s response failed validation: %r", log_label, text[:300])
        repair_prompt = "上次回复为空或格式错误，请重新生成，并严格遵守本次任务的输出格式。"
        retry_messages = retry_messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": repair_prompt},
        ]
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
    clue = (triggered_clue or "").strip()
    if not clue or clue == _CLUE_PREFIX:
        return None

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
    normalized_clue = re.sub(r"\s+", "", clue)
    for segment in segments:
        if re.sub(r"\s+", "", segment) == normalized_clue:
            return segment
    return None


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
        ask_instruction += (
            "若触发线索，第二行必须输出【线索公布】并逐字复制汤底对应线索的完整具体内容；"
            "仅输出空的【线索公布】视为未触发。实际公布内容会从汤底的"
            "【线索公布】与【线索公布结束】之间读取。"
        )
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
        clue = _extract_clue_from_answer(answer, clue)
    return {
        "judgment": _ASK_MAPPING[first_line],
        "clue": clue,
    }


def _parse_guess_result(text: str) -> dict[str, Any] | None:
    cleaned = _strip_guess_preamble(_strip_code_fence(text))
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


def _strip_guess_preamble(text: str) -> str:
    markers = [idx for marker in ("【通关】", "【未通关】") if (idx := text.find(marker)) >= 0]
    if not markers:
        return text.strip()
    return text[min(markers) :].strip()


def _strip_json_fence(text: str) -> str:
    stripped = _strip_code_fence(text).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def _extract_core_conditions(answer: str) -> list[str]:
    marker = "【通关判定条件】"
    idx = answer.find(marker)
    if idx < 0:
        return []
    section = answer[idx + len(marker) :]
    next_marker = re.search(r"\n【[^】]+】", section)
    if next_marker:
        section = section[: next_marker.start()]
    conditions: list[str] = []
    current: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\d+[\.\、]", line):
            if current:
                conditions.append(" ".join(current).strip())
            current = [re.sub(r"^\d+[\.\、]\s*", "", line)]
        elif current:
            current.append(line)
    if current:
        conditions.append(" ".join(current).strip())
    return [item for item in conditions if item]


def _parse_guess_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_json_fence(text))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        score = int(data.get("score"))
        missing_core_count = int(data.get("missing_core_count"))
    except Exception:
        return None
    success = bool(data.get("success"))
    if not 0 <= score <= 100 or missing_core_count < 0:
        return None
    core_checks = data.get("core_checks")
    if not isinstance(core_checks, list):
        return None
    public_answer = data.get("public_answer")
    if public_answer is not None and not isinstance(public_answer, str):
        return None
    if missing_core_count >= 3:
        success = False
        if not 0 <= score <= 39:
            score = 39
    elif missing_core_count == 2:
        success = False
        if not 40 <= score <= 59:
            score = 59
    elif missing_core_count == 1:
        score = max(60, min(score, 79))
        success = score >= 75
    else:
        success = True
        if not 80 <= score <= 100:
            score = 80
    return {
        "success": success,
        "score": score,
        "missing_core_count": missing_core_count,
        "core_checks": core_checks,
        "answer": public_answer.strip() if isinstance(public_answer, str) and public_answer.strip() else None,
        "raw_json": data,
    }


def _is_local_question_guess(guess: str) -> bool:
    text = guess.strip()
    if not text:
        return True
    if len(text) > 120 or "\n" in text:
        return False
    if text.endswith(("?", "？", "吗", "呢")):
        return True
    question_words = ("是不是", "是否", "有没有", "会不会", "难道")
    return any(word in text for word in question_words)


async def judge_guess(surface: str, answer: str, guess: str) -> dict[str, Any]:
    if _is_local_question_guess(guess):
        return {
            "success": False,
            "score": 0,
            "missing_core_count": None,
            "answer": None,
            "raw_json": {
                "success": False,
                "score": 0,
                "missing_core_count": None,
                "core_checks": [],
                "reason": "玩家提交内容是单句问句或局部确认，不是完整汤底猜测。",
            },
            "raw_response": None,
            "request_messages": None,
        }
    core_conditions = _extract_core_conditions(answer)
    condition_text = "\n".join(f"{idx}. {item}" for idx, item in enumerate(core_conditions, start=1))
    messages = [
        {
            "role": "system",
            "content": (
                await _get_judge_prompt_guess()
                + "\n\n你现在必须返回严格 JSON，不要返回 Markdown，不要返回【通关】/【未通关】文本。"
                "所有后台核对只允许放在 JSON 字段中，前端不会看到这些字段。"
            ),
        },
        {
            "role": "system",
            "content": (
                "本次请求类型是猜测汤底，不是普通提问。"
                "如果玩家内容只是单个事实确认、是非问题、局部追问、或没有尝试还原完整故事，"
                "必须判定 success=false。"
                "逐条判断 core_conditions 中每一条是否被玩家猜测明确盘出；missing_core_count >= 2 时必须判定 success=false。"
                "score 必须遵守：missing_core_count >= 3 时只能 0-39；missing_core_count == 2 时只能 40-59；"
                "missing_core_count == 1 时只能 60-79，只有 score >= 75 才允许 success=true；"
                "missing_core_count == 0 且 score >= 80 才允许 success=true。"
                "success=true 时 public_answer 必须输出面向玩家的完整汤底；success=false 时 public_answer 必须为 null。"
                "只输出以下 JSON 对象："
                '{"success":false,"score":0,"missing_core_count":0,'
                '"core_checks":[{"id":1,"passed":false,"reason":"..."}],'
                '"public_answer":null}'
            ),
        },
        {
            "role": "user",
            "content": (
                "请评估下面的玩家汤底猜测。\n"
                f"汤面：{surface}\n"
                f"完整汤底：{answer}\n"
                f"core_conditions：\n{condition_text or '本题未显式提供通关判定条件，请按完整汤底主干严格判定。'}\n"
                f"玩家猜测：{guess}"
            ),
        },
    ]
    raw_text = None
    async with _guess_lock:
        for _ in range(3):
            raw_text = await _chat(messages)
            parsed = _parse_guess_json(raw_text)
            if parsed is not None:
                return parsed | {
                    "raw_response": raw_text,
                    "request_messages": messages,
                }
            logger.warning("judge_guess response failed validation: %r", raw_text[:300])
    return {
        "success": False,
        "score": 0,
        "missing_core_count": None,
        "answer": None,
        "error": SYSTEM_BUSY_NOTICE,
        "raw_response": raw_text,
        "request_messages": messages,
    }


def public_answer_from_full_answer(answer: str) -> str:
    marker = "【隐藏后台设定】"
    if marker in answer:
        return answer.split(marker, 1)[0].strip()
    return answer.strip()


def _recent_user_utterances(game_log: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    utterances: list[dict[str, Any]] = []
    for log in game_log:
        if not log.get("player_id"):
            continue
        if log.get("type") not in {"ask", "guess"}:
            continue
        content = str(log.get("content") or "").strip()
        if not content:
            continue
        item: dict[str, Any] = {
            "type": log.get("type"),
            "content": content,
        }
        if log.get("username"):
            item["username"] = log.get("username")
        if log.get("type") == "ask" and log.get("judgment"):
            item["judgment"] = log.get("judgment")
        utterances.append(item)
    return utterances[-limit:]


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
        "可以根据用户偏离的方向适当引导，但不能直接提示答案。"
        "必须以【提示】开头，总字数不超过 30 字，必须是无标点的一句话。"
        "即使无法生成更具体的提示，也不要空回复，必须按格式给出一个低剧透的观察方向。"
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
                f"已问记录：{json.dumps(compact, ensure_ascii=False)}\n"
                f"最近20条用户发言：{json.dumps(_recent_user_utterances(game_log), ensure_ascii=False)}"
            ),
        },
    ]
    text = await _chat_validated(
        messages,
        _valid_hint_response,
        max_retry=5,
        log_label="generate_hint",
        timeout=12,
        max_tokens=1024,
        pool="hint",
    )
    if text is None:
        return None
    return text.strip()[len("【提示】") :].strip()


def _valid_hint_response(value: str) -> bool:
    text = value.strip()
    if not text.startswith("【提示】"):
        return False
    hint = text[len("【提示】") :].strip()
    if len(hint) < 7:
        return False
    if len(hint) > 120:
        return False
    if "【线索公布】" in hint:
        return False
    dangling_suffixes = (
        "的", "了", "是", "在", "和", "与", "把", "被", "从", "向", "对", "将", "让",
        "但", "而", "要", "会", "能", "到", "给", "用", "为", "去", "来", "着", "得",
        "地", "之", "其", "所", "以", "及", "或", "且", "因", "由", "于", "中", "里",
    )
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
