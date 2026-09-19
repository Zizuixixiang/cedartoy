"""Thin, server-side adapter for the public Detroit Blind Run service.

The upstream owner cookie, MCP connection URL and save id are credentials.  They
are encrypted at rest below ``data/vendor_saves/detroit/<player>/`` and never
returned to CedarToy clients.  One canonical CedarToy player/slot owns exactly
one upstream owner and at most one upstream save.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken


REMOTE_BASE = os.getenv(
    "DETROIT_REMOTE_BASE",
    "https://detroit-blind-run-rongrong.d7kjvtpfc4.chatgpt.site",
).rstrip("/")
REMOTE_USER_AGENT = "CedarToy4399/1.0"
SAVE_ROOT = Path(__file__).resolve().parent / "data" / "vendor_saves"
GAME_ROOT_NAME = "detroit"
STATE_NAME = "remote_state.fernet"
LOCK_NAME = ".remote_state.lock"
MCP_PROTOCOL_VERSION = "2025-03-26"
MAX_PROXY_BODY = 4 * 1024 * 1024
PUBLIC_SAVE_ID = "cedartoy-slot"
BACKUP_SAVE_ID = "0" * 32

_PLAYER_RE = re.compile(r"^[1-9][0-9]*(?::[2-5])?$")
_CONNECTION_RE = re.compile(r"^dbr_[A-Za-z0-9_-]{16,}$")
_SAVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
_MCP_TOOLS = frozenset(
    {
        "list_saves",
        "create_save",
        "read_current_scene",
        "record_choice",
        "play_step",
        "continue_scene",
        "read_progress",
        "read_record_card",
        "save_chapter_reflection",
        "start_next_chapter",
    }
)
_MCP_WRITES_WITH_REQUEST_ID = frozenset(
    {"record_choice", "continue_scene", "save_chapter_reflection", "start_next_chapter"}
)
_BROWSER_GET = frozenset({"sessions", "session", "history", "export", "backup"})
_BROWSER_POST = frozenset({"sessions", "action", "delete", "import"})


class DetroitError(Exception):
    """Safe client-facing error.  Message must never contain upstream secrets."""

    def __init__(self, message: str, *, uncertain: bool = False, status: int = 400):
        super().__init__(message)
        self.message = message
        self.uncertain = uncertain
        self.status = status


def _now() -> int:
    return int(time.time())


def _player_dir(player_id: str) -> Path:
    if not isinstance(player_id, str) or not _PLAYER_RE.fullmatch(player_id):
        raise DetroitError("底特律只支持已认证账号的规范存档身份")
    return SAVE_ROOT / GAME_ROOT_NAME / player_id


def _fernet() -> Fernet:
    secret = (
        os.getenv("DETROIT_MAPPING_SECRET", "").strip()
        or os.getenv("TOY_SECRET", "").strip()
    )
    if not secret:
        raise DetroitError("底特律安全映射密钥未配置", status=503)
    digest = hashlib.sha256(("cedartoy-detroit-v1\0" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _read_state_unlocked(player_id: str) -> dict | None:
    path = _player_dir(player_id) / STATE_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        value = json.loads(_fernet().decrypt(raw).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DetroitError("底特律存档映射损坏，已停止操作以保护远程存档", status=500) from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise DetroitError("底特律存档映射版本无法识别", status=500)
    return value


def _write_state_unlocked(player_id: str, state: dict) -> None:
    directory = _player_dir(player_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    state = dict(state)
    state["version"] = 1
    state["updated_at"] = _now()
    token = _fernet().encrypt(
        json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    fd, temp_name = tempfile.mkstemp(prefix=".detroit-state-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, directory / STATE_NAME)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


@contextmanager
def _locked(player_id: str):
    directory = _player_dir(player_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    lock_path = directory / LOCK_NAME
    with lock_path.open("a+b") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _browser_headers(*, json_request: bool = False) -> dict:
    headers = {
        "User-Agent": REMOTE_USER_AGENT,
        "Accept": "application/json" if json_request else "*/*",
        "Origin": REMOTE_BASE,
        "Referer": REMOTE_BASE + "/host",
    }
    if json_request:
        headers["Content-Type"] = "application/json"
    return headers


def _mcp_headers() -> dict:
    return {
        "User-Agent": REMOTE_USER_AGENT,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Protocol-Version": MCP_PROTOCOL_VERSION,
    }


def _validate_mcp_url(value: object) -> str:
    if not isinstance(value, str):
        raise DetroitError("老师站点返回了无效的 MCP 连接", status=502)
    parsed = urlparse(value)
    expected = urlparse(REMOTE_BASE)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected.hostname
        or parsed.path != "/detroit-mcp"
        or parsed.port not in (None, 443)
        or set(query) != {"connection"}
        or len(query["connection"]) != 1
        or not _CONNECTION_RE.fullmatch(query["connection"][0])
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise DetroitError("老师站点返回了不受信任的 MCP 连接", status=502)
    return value


def _safe_json(response: httpx.Response, context: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise DetroitError(f"老师站点的{context}响应格式异常", status=502) from exc
    if not isinstance(payload, dict):
        raise DetroitError(f"老师站点的{context}响应格式异常", status=502)
    return payload


def _safe_upstream_message(value: object, *secret_values: object) -> str:
    message = str(value or "老师站点拒绝了请求")
    message = re.sub(r"dbr_[A-Za-z0-9_-]{16,}", "<已隐藏连接>", message)
    message = re.sub(r"([?&]connection=)[^&#\s]+", r"\1<已隐藏连接>", message, flags=re.IGNORECASE)
    for secret in secret_values:
        if isinstance(secret, str) and secret:
            message = message.replace(secret, "<已隐藏凭据>")
    return message


def _redact_save_id(value: object, save_id: str, replacement: str = PUBLIC_SAVE_ID) -> object:
    if isinstance(value, str):
        return replacement if value == save_id else value
    if isinstance(value, list):
        return [_redact_save_id(item, save_id, replacement) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_save_id(item, save_id, replacement)
            for key, item in value.items()
        }
    return value


def _ensure_owner_and_connection_unlocked(player_id: str, state: dict | None) -> dict:
    if state and state.get("owner_cookie") and state.get("mcp_url"):
        _validate_mcp_url(state["mcp_url"])
        return state
    cookies = {}
    if state and isinstance(state.get("owner_cookie"), str):
        cookies["__Host-detroit_owner"] = state["owner_cookie"]
    try:
        with httpx.Client(follow_redirects=False, timeout=30) as client:
            first = client.get(REMOTE_BASE + "/api/sessions", headers=_browser_headers(json_request=True), cookies=cookies)
            if first.status_code >= 400:
                raise DetroitError("老师站点暂时无法建立存档空间", status=502)
            owner = client.cookies.get("__Host-detroit_owner") or cookies.get("__Host-detroit_owner")
            if not owner:
                raise DetroitError("老师站点未返回存档身份", status=502)
            created = client.post(
                REMOTE_BASE + "/api/mcp-connection",
                headers=_browser_headers(json_request=True),
                cookies={"__Host-detroit_owner": owner},
                json={"action": "create"},
            )
            if created.status_code != 201:
                raise DetroitError("老师站点暂时无法建立 AI 连接", status=502)
            mcp_url = _validate_mcp_url(_safe_json(created, "连接").get("mcp_url"))
    except DetroitError:
        raise
    except httpx.HTTPError as exc:
        raise DetroitError("连接老师站点失败，请稍后再试", status=502) from exc
    new_state = dict(state or {})
    new_state.update(
        {
            "version": 1,
            "owner_cookie": owner,
            "mcp_url": mcp_url,
            "created_at": (state or {}).get("created_at") or _now(),
        }
    )
    _write_state_unlocked(player_id, new_state)
    return new_state


def _mcp_call_unlocked(state: dict, tool: str, arguments: dict) -> dict:
    if tool not in _MCP_TOOLS:
        raise DetroitError("未知的底特律操作")
    url = _validate_mcp_url(state.get("mcp_url"))
    payload = {
        "jsonrpc": "2.0",
        "id": "cedartoy",
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    is_write = tool not in {"list_saves", "read_current_scene", "read_progress", "read_record_card"}
    try:
        response = httpx.post(url, headers=_mcp_headers(), json=payload, timeout=75)
    except httpx.HTTPError as exc:
        raise DetroitError(
            "连接老师站点时中断" + ("；本次写操作不会自动重试，请先读取当前场景核对" if is_write else ""),
            uncertain=is_write,
            status=502,
        ) from exc
    if response.status_code >= 500:
        raise DetroitError(
            "老师站点暂时无响应" + ("；本次写操作不会自动重试，请先读取当前场景核对" if is_write else ""),
            uncertain=is_write,
            status=502,
        )
    envelope = _safe_json(response, "MCP")
    if response.status_code >= 400 or "error" in envelope:
        error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        message = _safe_upstream_message(
            error.get("message") if isinstance(error.get("message"), str) else "老师站点拒绝了请求",
            state.get("mcp_url"),
            state.get("owner_cookie"),
            arguments.get("save_id"),
        )
        raise DetroitError(message, status=400)
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise DetroitError("老师站点的 MCP 响应缺少结果", status=502)
    text_value = None
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            text_value = item["text"]
            break
    if result.get("isError"):
        raise DetroitError(
            _safe_upstream_message(
                text_value or "老师站点拒绝了请求",
                state.get("mcp_url"),
                state.get("owner_cookie"),
                arguments.get("save_id"),
            ),
            status=400,
        )
    if text_value is None:
        return result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else result
    try:
        parsed = json.loads(text_value)
    except json.JSONDecodeError:
        return {"text": text_value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _summary_from_save(save: object) -> dict | None:
    if not isinstance(save, dict):
        return None
    return {
        key: save.get(key)
        for key in (
            "name",
            "difficulty",
            "chapter",
            "chapter_index",
            "chapter_count",
            "complete",
            "ending_progress",
        )
        if key in save
    }


def _save_id_from_result(result: dict) -> str | None:
    candidates = [result.get("save_id")]
    for key in ("save", "session"):
        value = result.get(key)
        if isinstance(value, dict):
            candidates.extend((value.get("id"), value.get("save_id")))
    for value in candidates:
        if isinstance(value, str) and _SAVE_ID_RE.fullmatch(value):
            return value
    return None


def _update_from_result_unlocked(player_id: str, state: dict, result: dict) -> None:
    save_id = _save_id_from_result(result)
    if save_id:
        state["save_id"] = save_id
    save = result.get("save") or result.get("session")
    summary = _summary_from_save(save)
    if summary:
        state["summary"] = summary
    _write_state_unlocked(player_id, state)


def _require_mapped_save(state: dict, supplied: object = None) -> str:
    save_id = state.get("save_id")
    if not isinstance(save_id, str) or not _SAVE_ID_RE.fullmatch(save_id):
        raise DetroitError("这个槽位还没有底特律存档，请先 create_save")
    if supplied not in (None, "", save_id, PUBLIC_SAVE_ID):
        raise DetroitError("不能访问这个槽位以外的远程存档", status=403)
    return save_id


def _stable_request_id(player_id: str, tool: str, args: dict) -> str:
    canonical = json.dumps(
        {"player": player_id, "tool": tool, "arguments": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ct_" + hashlib.sha256(canonical).hexdigest()[:48]


def _normalize_saves(result: dict) -> list[dict]:
    # The public MCP names the tool list_saves but currently returns the same
    # browser-shaped ``sessions`` array.  Accept ``saves`` as a forward-compatible
    # alias and normalize only at CedarToy's boundary.
    saves = result.get("saves")
    if not isinstance(saves, list):
        saves = result.get("sessions")
    return [item for item in saves if isinstance(item, dict)] if isinstance(saves, list) else []


def _reconcile_save_unlocked(player_id: str, state: dict) -> tuple[dict, list[dict], dict]:
    result = _mcp_call_unlocked(state, "list_saves", {})
    saves = _normalize_saves(result)
    mapped = state.get("save_id")
    if mapped:
        own = [item for item in saves if item.get("id") == mapped or item.get("save_id") == mapped]
        if own:
            state["summary"] = _summary_from_save(own[0]) or state.get("summary") or {}
            _write_state_unlocked(player_id, state)
            return state, own, result
        # An empty list may be temporarily stale after create_save.  The mapped
        # id is the stronger credential: never discard it merely because a
        # read-side listing has not caught up, or cleanup could become
        # impossible.  A different non-empty save is a consistency fault.
        if not saves:
            return state, [], result
        raise DetroitError("远程存档列表与本地安全映射不一致，已停止操作，请管理员人工核对", status=409)
    if len(saves) == 1:
        discovered = saves[0].get("id") or saves[0].get("save_id")
        if isinstance(discovered, str) and _SAVE_ID_RE.fullmatch(discovered):
            state["save_id"] = discovered
            state["summary"] = _summary_from_save(saves[0]) or {}
            state.pop("pending_create", None)
            _write_state_unlocked(player_id, state)
            return state, saves, result
    if len(saves) > 1:
        raise DetroitError("远程槽位出现多份存档，已停止操作，请管理员人工核对", status=409)
    _write_state_unlocked(player_id, state)
    return state, [], result


def play(player_id: str, action: str, arguments: dict) -> dict:
    """Call one upstream MCP tool for one canonical CedarToy player/slot."""
    if action not in _MCP_TOOLS:
        raise DetroitError("未知的底特律操作")
    if not isinstance(arguments, dict):
        raise DetroitError("params 必须是对象")
    args = dict(arguments)
    for wrapper_key in ("game", "action", "player_id", "slot"):
        args.pop(wrapper_key, None)
    confirm = args.pop("confirm", False) is True
    confirm_retry = args.pop("confirm_retry", False) is True
    with _locked(player_id):
        state = _ensure_owner_and_connection_unlocked(player_id, _read_state_unlocked(player_id))
        if action == "list_saves":
            state, saves, listing = _reconcile_save_unlocked(player_id, state)
            own = saves[:1]
            public = {
                "saves": own,
                "ending_progress": listing.get("ending_progress"),
                "hosted_by": "如火如風的容的老师站点",
            }
            mapped_save_id = state.get("save_id")
            return _redact_save_id(public, mapped_save_id) if isinstance(mapped_save_id, str) else public

        if action == "create_save":
            raw_name = args.get("name")
            difficulty = args.get("difficulty")
            if not isinstance(raw_name, str) or not raw_name.strip() or len(raw_name.strip()) > 60:
                raise DetroitError("create_save 的 name 必须是 1–60 字")
            if difficulty not in {"casual", "experienced", "hardcore"}:
                raise DetroitError("difficulty 只能是 casual、experienced 或 hardcore")
            state, saves, _listing = _reconcile_save_unlocked(player_id, state)
            if state.get("pending_create") and not saves:
                if not confirm_retry:
                    raise DetroitError(
                        "上次建档结果尚未确认，且远程暂未列出存档；不会自动重试。确认后可传 confirm_retry=true 再试",
                        uncertain=True,
                        status=409,
                    )
                state.pop("pending_create", None)
            if state.get("save_id"):
                if not confirm:
                    raise DetroitError("这个槽位已有存档；请先删除，或显式传 confirm=true 覆盖")
                _remote_delete_unlocked(state, state["save_id"])
                state.pop("save_id", None)
                state.pop("summary", None)
            create_args = {"name": raw_name.strip(), "difficulty": difficulty}
            state["pending_create"] = {"requested_at": _now(), **create_args}
            _write_state_unlocked(player_id, state)
            try:
                result = _mcp_call_unlocked(state, action, create_args)
            except DetroitError as exc:
                if not exc.uncertain:
                    state.pop("pending_create", None)
                    _write_state_unlocked(player_id, state)
                raise
            created_save_id = _save_id_from_result(result)
            if not created_save_id:
                _write_state_unlocked(player_id, state)
                raise DetroitError(
                    "老师站点已响应建档但未返回可识别的存档 ID；不会自动重试，请先 list_saves 核对",
                    uncertain=True,
                    status=502,
                )
            state.pop("pending_create", None)
            state["summary"] = {"name": create_args["name"], "difficulty": create_args["difficulty"]}
            _update_from_result_unlocked(player_id, state, result)
            return _redact_save_id(result, created_save_id)

        save_id = _require_mapped_save(state, args.pop("save_id", None))
        args["save_id"] = save_id
        if action == "play_step" and args.get("request_id") is not None:
            raise DetroitError("play_step 不支持 request_id；断线保护由 CedarToy 的 read_current_scene/confirm_retry 流程处理")
        if action in _MCP_WRITES_WITH_REQUEST_ID and not args.get("request_id"):
            args["request_id"] = _stable_request_id(player_id, action, args)

        if action == "play_step":
            pending = state.get("pending_play_step")
            if pending:
                same = all(pending.get(key) == args.get(key) for key in ("revision", "node_id", "label", "reason"))
                if not same or not pending.get("verified_unchanged") or not confirm_retry:
                    raise DetroitError(
                        "上次 play_step 结果不明；不会自动重试。请先 read_current_scene，确认仍在原 revision/node_id 后，再对同一选择传 confirm_retry=true",
                        uncertain=True,
                        status=409,
                    )
            state["pending_play_step"] = {
                key: args.get(key) for key in ("revision", "node_id", "label", "reason")
            }
            state["pending_play_step"]["verified_unchanged"] = False
            state["pending_play_step"]["requested_at"] = _now()
            _write_state_unlocked(player_id, state)
        try:
            result = _mcp_call_unlocked(state, action, args)
        except DetroitError as exc:
            if action == "play_step" and not exc.uncertain:
                state.pop("pending_play_step", None)
                _write_state_unlocked(player_id, state)
            raise
        if action == "play_step":
            state.pop("pending_play_step", None)
        elif action == "read_current_scene" and state.get("pending_play_step"):
            pending = state["pending_play_step"]
            current_revision = result.get("revision")
            current_node = result.get("node_id")
            if current_revision == pending.get("revision") and current_node == pending.get("node_id"):
                pending["verified_unchanged"] = True
                result = dict(result)
                result["cedartoy_retry_notice"] = (
                    "场景仍停在上次提交前；如确认重试同一 play_step，请传 confirm_retry=true。"
                )
            else:
                state.pop("pending_play_step", None)
                result = dict(result)
                result["cedartoy_recovery_notice"] = "已检测到远程进度发生变化，上次操作可能已经成功，未重复提交。"
        _update_from_result_unlocked(player_id, state, result)
        return _redact_save_id(result, save_id)


def save_summary(player_id: str) -> dict | None:
    try:
        state = _read_state_unlocked(player_id)
    except DetroitError:
        return None
    if not state or not state.get("save_id"):
        return None
    summary = dict(state.get("summary") or {})
    summary["hosted_remotely"] = True
    summary["last_active"] = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(state.get("updated_at") or 0)
    )
    return summary


def has_save(player_id: str) -> bool:
    return save_summary(player_id) is not None


def _remote_delete_unlocked(state: dict, save_id: str, save_name: str | None = None) -> bool:
    owner = state.get("owner_cookie")
    if not isinstance(owner, str):
        raise DetroitError("远程存档身份缺失", status=500)
    name = save_name or (state.get("summary") or {}).get("name")
    try:
        if not name:
            listing = httpx.get(
                REMOTE_BASE + "/api/sessions",
                headers=_browser_headers(json_request=True),
                cookies={"__Host-detroit_owner": owner},
                timeout=45,
            )
            if listing.status_code >= 400:
                raise DetroitError("无法核对待删除的远程存档", status=502)
            sessions = _safe_json(listing, "存档列表").get("sessions") or []
            match = next(
                (item for item in sessions if isinstance(item, dict) and item.get("id") == save_id),
                None,
            )
            if match is None:
                return False
            name = match.get("name")
        if not isinstance(name, str):
            raise DetroitError("无法核对待删除的远程存档名称", status=409)
        response = httpx.post(
            REMOTE_BASE + "/api/delete",
            headers=_browser_headers(json_request=True),
            cookies={"__Host-detroit_owner": owner},
            json={
                "id": save_id,
                "name": name,
                "confirmation": "刪除",
            },
            timeout=45,
        )
    except httpx.HTTPError as exc:
        raise DetroitError("远程删除结果不明，请勿立即重复操作", uncertain=True, status=502) from exc
    if response.status_code >= 500:
        raise DetroitError("远程删除结果不明，请勿立即重复操作", uncertain=True, status=409)
    if response.status_code == 404:
        return False
    if response.status_code >= 400:
        payload = _safe_json(response, "删除")
        raise DetroitError(
            _safe_upstream_message(
                payload.get("error") or "老师站点拒绝删除存档",
                state.get("mcp_url"),
                owner,
                save_id,
            ),
            status=400,
        )
    return True


def delete_save(player_id: str) -> bool:
    with _locked(player_id):
        state = _read_state_unlocked(player_id)
        if not state:
            return False
        save_id = state.get("save_id")
        if save_id:
            _remote_delete_unlocked(state, save_id)
        directory = _player_dir(player_id)
        for name in (STATE_NAME, LOCK_NAME):
            try:
                (directory / name).unlink()
            except FileNotFoundError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
        return bool(save_id)


def _browser_call_unlocked(
    state: dict,
    method: str,
    endpoint: str,
    *,
    query: dict[str, str] | None = None,
    payload: dict | None = None,
) -> httpx.Response:
    owner = state.get("owner_cookie")
    if not isinstance(owner, str):
        raise DetroitError("远程存档身份缺失", status=500)
    target = REMOTE_BASE + "/api/" + endpoint
    if query:
        target += "?" + urlencode(query)
    try:
        return httpx.request(
            method,
            target,
            headers=_browser_headers(json_request=True),
            cookies={"__Host-detroit_owner": owner},
            json=payload if method == "POST" else None,
            follow_redirects=False,
            timeout=75,
        )
    except httpx.HTTPError as exc:
        uncertain = method == "POST"
        raise DetroitError(
            "连接老师站点时中断" + ("；写入结果不明，不会自动重试" if uncertain else ""),
            uncertain=uncertain,
            status=(502 if endpoint == "action" or method != "POST" else 409),
        ) from exc


def browser_api(
    player_id: str,
    method: str,
    endpoint: str,
    *,
    query: dict[str, str] | None = None,
    payload: dict | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Proxy the original browser API while pinning it to one mapped save."""
    method = method.upper()
    if (method == "GET" and endpoint not in _BROWSER_GET) or (
        method == "POST" and endpoint not in _BROWSER_POST
    ):
        raise DetroitError("不允许的底特律网页接口", status=404)
    query = dict(query or {})
    payload = dict(payload or {})
    with _locked(player_id):
        state = _ensure_owner_and_connection_unlocked(player_id, _read_state_unlocked(player_id))
        if endpoint == "sessions" and method == "POST":
            state, saves, _listing = _reconcile_save_unlocked(player_id, state)
            if saves or state.get("save_id"):
                raise DetroitError("这个槽位已有存档；请先在存档首页确认删除后再新建", status=409)
            if state.get("pending_create"):
                raise DetroitError("上次建档结果仍待确认；不会从网页自动重试，请先刷新存档列表", uncertain=True, status=409)
            state["pending_create"] = {
                "requested_at": _now(),
                "name": payload.get("name"),
                "difficulty": payload.get("difficulty"),
            }
            _write_state_unlocked(player_id, state)
        elif endpoint == "import" and method == "POST":
            state, saves, _listing = _reconcile_save_unlocked(player_id, state)
            if saves or state.get("save_id"):
                raise DetroitError("导入会覆盖当前槽位；请先确认删除已有存档", status=409)
            if state.get("pending_create"):
                raise DetroitError("上次导入结果仍待确认；不会从网页自动重试，请先刷新存档列表", uncertain=True, status=409)
            state["pending_create"] = {"requested_at": _now(), "kind": "import"}
            _write_state_unlocked(player_id, state)
        elif endpoint == "sessions" and method == "GET":
            response = _browser_call_unlocked(state, method, endpoint)
            data = _safe_json(response, "存档列表")
            sessions = [item for item in data.get("sessions") or [] if isinstance(item, dict)]
            mapped = state.get("save_id")
            if mapped and sessions and not any(item.get("id") == mapped for item in sessions):
                raise DetroitError("远程存档列表与本地安全映射不一致，已停止显示，请管理员人工核对", status=409)
            if not mapped and len(sessions) > 1:
                raise DetroitError("远程槽位出现多份存档，已停止显示，请管理员人工核对", status=409)
            if not mapped and len(sessions) == 1:
                mapped = sessions[0].get("id")
                if isinstance(mapped, str) and _SAVE_ID_RE.fullmatch(mapped):
                    state["save_id"] = mapped
                    state["summary"] = _summary_from_save(sessions[0]) or {}
                    state.pop("pending_create", None)
                    _write_state_unlocked(player_id, state)
            data["sessions"] = [item for item in sessions if mapped and item.get("id") == mapped]
            if mapped:
                data = _redact_save_id(data, mapped)
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            return response.status_code, {"content-type": "application/json; charset=utf-8"}, body
        else:
            supplied = query.get("id") if method == "GET" else payload.get("id")
            save_id = _require_mapped_save(state, supplied)
            if method == "GET":
                query["id"] = save_id
            else:
                payload["id"] = save_id
                if endpoint == "delete" and payload.get("confirmation") != "刪除":
                    raise DetroitError("删除存档必须在原页面输入“刪除”确认")

        response = _browser_call_unlocked(
            state, method, endpoint, query=query if method == "GET" else None, payload=payload if method == "POST" else None
        )
        if method == "POST" and endpoint != "action" and response.status_code >= 500:
            raise DetroitError("老师站点写入结果不明；不会自动重试，请先刷新核对", uncertain=True, status=409)
        body = response.content
        if len(body) > MAX_PROXY_BODY:
            raise DetroitError("老师站点响应超过安全大小限制", status=502)
        if endpoint in {"sessions", "import"} and method == "POST" and response.status_code < 500:
            state.pop("pending_create", None)
            _write_state_unlocked(player_id, state)
        parsed_result = None
        if "json" in response.headers.get("content-type", "").lower():
            try:
                parsed_result = response.json()
            except ValueError:
                parsed_result = None
        if response.status_code < 400 and endpoint in {"sessions", "import", "action", "session"}:
            if isinstance(parsed_result, dict):
                _update_from_result_unlocked(player_id, state, parsed_result)
        if response.status_code < 400 and endpoint == "delete":
            state.pop("save_id", None)
            state.pop("summary", None)
            state.pop("pending_play_step", None)
            _write_state_unlocked(player_id, state)
        headers = {
            "content-type": response.headers.get("content-type", "application/json; charset=utf-8"),
            "content-disposition": response.headers.get("content-disposition", ""),
        }
        mapped_save_id = state.get("save_id")
        if isinstance(parsed_result, (dict, list)) and isinstance(mapped_save_id, str):
            replacement = BACKUP_SAVE_ID if endpoint == "backup" else PUBLIC_SAVE_ID
            body = json.dumps(
                _redact_save_id(parsed_result, mapped_save_id, replacement),
                ensure_ascii=False,
                indent=2 if endpoint in {"export", "backup"} else None,
            ).encode("utf-8")
        elif headers["content-type"].lower().startswith("text/") and isinstance(mapped_save_id, str):
            body = body.replace(mapped_save_id.encode("utf-8"), PUBLIC_SAVE_ID.encode("utf-8"))
        if endpoint == "export":
            headers["content-disposition"] = 'attachment; filename="detroit-handoff.json"'
        elif endpoint == "backup":
            headers["content-disposition"] = 'attachment; filename="detroit-full-save.json"'
        return response.status_code, headers, body


def fetch_public(path: str) -> tuple[int, str, bytes]:
    """Fetch only the fixed public host assets; never sends owner credentials."""
    if path not in {"host", "host.js", "host.css", "downloads/detroit_blind_host_windows_v9.zip"}:
        raise DetroitError("未找到底特律公开资源", status=404)
    try:
        response = httpx.get(
            REMOTE_BASE + "/" + path,
            headers={"User-Agent": REMOTE_USER_AGENT, "Accept": "*/*"},
            follow_redirects=False,
            timeout=45,
        )
    except httpx.HTTPError as exc:
        raise DetroitError("老师站点公开页面暂时无法读取", status=502) from exc
    content_type = response.headers.get("content-type", "application/octet-stream")
    return response.status_code, content_type, response.content


def rewrite_public(path: str, body: bytes) -> bytes:
    """Mount the unmodified upstream UI under /detroit without leaking MCP keys."""
    if path == "host":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return body
        text = re.sub(
            r'<script>\(function\(\)\{function c\(\).*?</script>',
            "",
            text,
            flags=re.DOTALL,
        )
        text = text.replace('href="/host.css', 'href="/detroit/host.css')
        text = text.replace('src="/host.js', 'src="/detroit/host.js')
        return text.encode("utf-8")
    if path == "host.js":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return body
        text = text.replace("'/api/", "'/detroit/api/").replace('"/api/', '"/detroit/api/')
        text = text.replace("`/api/", "`/detroit/api/")
        text = text.replace("'/downloads/", "'/detroit/downloads/").replace('"/downloads/', '"/detroit/downloads/')
        text = text.replace("`/downloads/", "`/detroit/downloads/")
        replacement = '''async function showMcpSetup() {
  const existingFallback = document.querySelector('#copy-fallback');
  if (existingFallback) existingFallback.remove();
  const dialog = document.createElement('div');
  dialog.id = 'copy-fallback';
  dialog.className = 'copy-fallback';
  dialog.innerHTML = `<div class="copy-card"><h2>CedarToy 統一 MCP</h2><p>小機請繼續使用 CedarToy/4399 的統一 MCP，先呼叫 <code>get_guide(game="detroit")</code>；人類與綁定小機共用所選槽位。</p><p class="tiny">遊戲與雲端存檔由「如火如風的容」老師站點托管。</p><button id="copy-close" class="button primary">知道了</button></div>`;
  document.body.appendChild(dialog);
  dialog.querySelector('#copy-close').addEventListener('click', () => dialog.remove());
}

function showDeleteDialog'''
        text, count = re.subn(
            r"async function showMcpSetup\(\) \{.*?\n\}\n\nfunction showDeleteDialog",
            replacement,
            text,
            count=1,
            flags=re.DOTALL,
        )
        if count != 1:
            raise DetroitError("老师页面版本变化，无法安全替换连接说明", status=502)
        text = text.replace("建立 AI 連接網址", "查看 CedarToy 統一連接說明")
        text = re.sub(
            r'<div class="cloud-save-note" role="note">.*?</div>',
            '<div class="cloud-save-note" role="note"><strong>作者站點雲端存檔</strong>'
            '<p>遊戲與雲端存檔由作者站點托管；人類網頁與綁定小機共用所選槽位。頁面可以關閉，換裝置或清除本站資料前請先匯出完整存檔備份。</p></div>',
            text,
            count=1,
        )
        text = re.sub(
            r'<details class="usage-note">.*?</details>',
            '<details class="usage-note"><summary>本站玩法說明</summary><div class="tiny">'
            '<p><strong>小機：</strong>使用 CedarToy/4399 統一 MCP，先呼叫 <code>get_guide(game="detroit")</code>，再依 Guide 讀取與推進劇情。</p>'
            '<p><strong>人類：</strong>從 CedarToy 首頁選擇綁定小機與 1–5 號槽位，即可查看同一份存檔。</p>'
            '<p>遊戲與雲端存檔由「如火如風的容」老師站點托管。</p></div></details>',
            text,
            count=1,
        )
        old_note = re.compile(
            r'<p class="tiny">按上方按鈕取得這個瀏覽器專屬的完整 MCP 網址.*?</p><p class="tiny">非商用實驗.*?</p>'
        )
        new_note = (
            '<p class="tiny">小機使用 CedarToy/4399 統一 MCP；人類網頁與綁定小機共用所選的 1–5 號槽位。'
            '遊戲與雲端存檔由作者站點托管；換手機、換瀏覽器或清除本站資料前，請先匯出完整存檔備份。'
            '<strong>完整存檔不是交接卡，請勿交給盲玩的 AI 閱讀。</strong></p>'
            '<p class="tiny">作者：<a href="http://community.rhysen.love/thread/3170" target="_blank" rel="noopener noreferrer">如火如風的容</a>'
            '（小红书号 27231843685）；<a href="https://github.com/cfzdgbw42k-pixel/detroit-ai-player" target="_blank" rel="noopener noreferrer">老师仓库</a>；老师注明创作来源为 '
            '<a href="https://github.com/Baba88611/detroit-ai-player" target="_blank" rel="noopener noreferrer">Baba88611 原项目</a>。'
            '非商用实验；剧情资料依原项目的 <a href="https://github.com/Baba88611/detroit-ai-player/blob/main/docs/legal/CC-BY-NC-4.0.txt" target="_blank" rel="noopener noreferrer">CC BY-NC 4.0</a> 授权。</p>'
            '<p class="tiny"><a href="https://detroit-blind-run-rongrong.d7kjvtpfc4.chatgpt.site/host" target="_blank" rel="noopener noreferrer">作者原版 ↗</a></p>'
        )
        text = old_note.sub(new_note, text, count=1)
        return text.encode("utf-8")
    return body
