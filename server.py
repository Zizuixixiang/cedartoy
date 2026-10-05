import json
import base64
import copy
import hashlib
import hmac
import html as html_lib
import http.client
import logging
import mimetypes
import os
import random
import re
import secrets
import shutil
import smtplib
import sqlite3
import ssl
import sys
import time
import urllib.parse
from dataclasses import dataclass
from contextlib import closing
from email.message import EmailMessage
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import BoundedSemaphore, Lock, Thread

import httpx
import duel_wait_control

import account_deletion
import admin_recovery_mcp
import avatar_appearances
import detroit_adapter
import puzzle_box
import game_activity
from admin_dashboard import build_activity_dashboard, _read_only_connect

try:
    from passlib.context import CryptContext
except ImportError:
    CryptContext = None

import announcements
from bdsmtest.handler import handle_mcp as handle_bdsmtest_mcp
from ciyuwu_adapter.handler import handle_mcp as handle_ciyuwu_mcp
from dnd import handler as dnd_handler
from dnd import questions as dnd_questions
from dnd import scoring as dnd_scoring
from dnd import web_questions_zh as dnd_web_questions
from ecr import handler as ecr_handler
from ecr import questions as ecr_questions
from ecr import scoring as ecr_scoring
from eco_adapter import handler as eco_handler
from eco_adapter.handler import handle_mcp as handle_eco_mcp
from enneagram import handler as enneagram_handler
from enneagram import descriptions as enneagram_descriptions
from enneagram import questions as enneagram_questions
from enneagram import scoring as enneagram_scoring
from humanity import handler as humanity_handler
from humanity import questions as humanity_questions
from humanity import scoring as humanity_scoring
from love import handler as love_handler
from love import questions as love_questions
from love import scoring as love_scoring
from mbti import handler as mbti_handler
from mbti import questions as mbti_questions
from mbti import scoring as mbti_scoring
from sins_virtues import handler as sins_virtues_handler
from sins_virtues import questions as sins_virtues_questions
from sins_virtues import scoring as sins_virtues_scoring
from tarot_adapter import (
    COVE_REPOSITORY,
    RITUAL_DISPLAY_NAME,
    RITUAL_ROOT,
    RITUAL_REPOSITORY,
    MAX_INVITE_QUESTION,
    TAROT_ALLOWED_MODELS,
    TAROT_FLASH_MODEL,
    TAROT_MODEL_LABELS,
    TAROT_PRO_MODEL,
    TarotError,
    WEB as TAROT_WEB,
    count_saved_tarot_sessions,
    get_store as get_tarot_store,
    tarot_model_source,
)
from vendor_cmd_adapter import ai_life as ai_life_adapter
from vendor_cmd_adapter import arcade as arcade_adapter
from vendor_cmd_adapter import bar as bar_adapter
from vendor_cmd_adapter import burger as burger_adapter
from vendor_cmd_adapter import crucible_echoes as crucible_echoes_adapter
from vendor_cmd_adapter import delve as delve_adapter
from vendor_cmd_adapter import fishing as fishing_adapter
from vendor_cmd_adapter import forest as forest_adapter
from vendor_cmd_adapter import imitator_td as imitator_td_adapter
from vendor_cmd_adapter import leek as leek_adapter
from vendor_cmd_adapter import market as market_adapter
from vendor_cmd_adapter import memoria as memoria_adapter
from vendor_cmd_adapter import moonlit as moonlit_adapter
from vendor_cmd_adapter import travel as travel_adapter
from nowhere_adapter import handler as nowhere_adapter
from nowhere_adapter import storage as nowhere_storage
from nowhere_adapter import web as nowhere_web
from links import AUTHORS
from vendor_cmd_adapter import white_room as white_room_adapter
from vendor_cmd_adapter.base import VendorCmdError, parse_import_save_data
from cedar_backend import guides as mcp_guides
from cedar_backend import mcp_schema
from cedar_backend import human_tests
from cedar_backend import announcement_delivery
from cedar_backend import anti_addiction
from cedar_backend import public_stats, duel_history
from cedar_backend import satellite_proxy
from cedar_backend import http_handler, http_server
from cedar_backend.web import responses as web_responses
from cedar_backend import duel_bridge
from cedar_backend import mcp_dispatch
from cedar_backend import game_dispatch
from cedar_backend import save_management
from cedar_backend import account_lifecycle
from cedar_backend import operit
from cedar_backend import account_email
from cedar_backend import account_recovery
from cedar_backend import admin_accounts
from cedar_backend import auth
from cedar_backend import accounts
from cedar_backend import player_identity
from cedar_backend.errors import _McpError
from cedar_backend.guides import (
    AI_LIFE_GUIDE,
    CAMPING_PLAZA_GUIDE,
    CRUCIBLE_ECHOES_GUIDE,
    DETROIT_GUIDE,
    DUEL_GUIDE,
    GARDEN_CAT_GUIDE,
    PLATFORM_ANNOUNCEMENT_GUIDE_NOTE,
    SAVE_SLOT_GUIDE_NOTE,
    TAROT_GUIDE,
    VENDOR_CMD_GUIDES,
    WORKKK_GUIDE,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _bounded_env_int(name, default, minimum, maximum):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} 必须是 {minimum}–{maximum} 的整数"
        ) from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum}–{maximum} 的整数")
    return value


HOST = "127.0.0.1"
PORT = int(os.getenv("CEDARTOY_PORT", "8002"))
MAX_WORKERS = 50
QUEUE_TIMEOUT_SECONDS = 10
SOUP_HOST = "127.0.0.1"
SOUP_PORT = 8012
SOUP_BASE = f"http://{SOUP_HOST}:{SOUP_PORT}"
TAROT_BRIDGE_TOKEN = os.getenv("TAROT_BRIDGE_TOKEN", "").strip()
TAROT_BRIDGE_TIMEOUT_SECONDS = max(
    10.0, min(float(os.getenv("TAROT_BRIDGE_TIMEOUT_SECONDS", "95")), 125.0)
)
TAROT_AUTH_COOKIE = "cedartoy_tarot_auth"
WORKKK_HOST = "127.0.0.1"
WORKKK_PORT = 8770
WORKKK_BASE = f"http://{WORKKK_HOST}:{WORKKK_PORT}"
GARDEN_CAT_HOST = "127.0.0.1"
GARDEN_CAT_PORT = 8771
GARDEN_CAT_BASE = f"http://{GARDEN_CAT_HOST}:{GARDEN_CAT_PORT}"
GARDEN_CAT_PROXY_GET_PATHS = satellite_proxy.GARDEN_CAT_PROXY_GET_PATHS
GARDEN_CAT_PROXY_POST_PATHS = satellite_proxy.GARDEN_CAT_PROXY_POST_PATHS
DUEL_HOST = "127.0.0.1"
DUEL_PORT = 8772
DUEL_BASE = f"http://{DUEL_HOST}:{DUEL_PORT}"
DUEL_GATEWAY_SHARED_SECRET = os.getenv("DUEL_GATEWAY_SHARED_SECRET", "")
DUEL_GATEWAY_MAX_WAIT_SECONDS = _bounded_env_int(
    "DUEL_GATEWAY_MAX_WAIT_SECONDS", 600, 60, 3600
)
DUEL_GATEWAY_TICKET_TTL_SECONDS = _bounded_env_int(
    "DUEL_GATEWAY_TICKET_TTL_SECONDS",
    DUEL_GATEWAY_MAX_WAIT_SECONDS + 120,
    DUEL_GATEWAY_MAX_WAIT_SECONDS + 60,
    7200,
)
DUEL_GATEWAY_MAX_TICKETS = 600
CAMPING_PLAZA_HOST = "127.0.0.1"
CAMPING_PLAZA_PORT = 8773
CAMPING_PLAZA_BASE = f"http://{CAMPING_PLAZA_HOST}:{CAMPING_PLAZA_PORT}"
TOY_SECRET = os.getenv("TOY_SECRET", "change-me-before-production")
JWT_ALGORITHM = "HS256"
HUMAN_TOKEN_SECONDS = 30 * 24 * 60 * 60
AI_OPAQUE_TOKEN_PREFIX = "ctai_v1_"
AI_OPAQUE_TOKEN_BYTES = 32
AI_OPAQUE_TOKEN_FORMAT_VERSION = 1
OPERIT_SESSION_TOKEN_PREFIX = "ctop_v1_"
OPERIT_SESSION_TOKEN_BYTES = 32
OPERIT_SESSION_FORMAT_VERSION = 1
OPERIT_SESSION_SECONDS = 90 * 24 * 60 * 60
OPERIT_WEB_TICKET_PREFIX = "ctow_v1_"
OPERIT_WEB_TICKET_BYTES = 24
OPERIT_WEB_TICKET_SECONDS = 60
LEGACY_AI_JWT_COMPAT_ENABLED = os.getenv(
    "LEGACY_AI_JWT_COMPAT_ENABLED", "true"
).strip().lower() not in {"0", "false", "no", "off"}
BINDING_TOKEN_SECONDS = 10 * 60
TURTLE_DB_PATH = Path(os.getenv("TURTLE_SOUP_DB", Path(__file__).resolve().parent / "turtle-soup" / "backend" / "turtle_soup.db"))
SESSIONS_DB_PATH = Path(os.getenv("SESSIONS_DB", Path(__file__).resolve().parent / "data" / "sessions.db"))
GAME_PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")
GUIDE_DIR = Path(__file__).resolve().parent / "turtle-soup" / "backend" / "guides"
MEMORIA_HUMAN_GUIDE_DIR = Path(__file__).resolve().parent / "vendor" / "Memoria-Station" / "攻略（给人看的）"
MEMORIA_AFTER_CLEAR_DIR = Path(__file__).resolve().parent / "vendor" / "Memoria-Station" / "通关后阅读"
TOY_INDEX_PATH = Path(__file__).resolve().parent / "index.html"
ADMIN_INDEX_PATH = Path(__file__).resolve().parent / "admin.html"
ECO_INDEX_PATH = Path(__file__).resolve().parent / "eco.html"
FOREST_INDEX_PATH = Path(__file__).resolve().parent / "forest.html"
AI_LIFE_FRONTEND_ROOT = ai_life_adapter.FRONTEND_ROOT.resolve()
AI_LIFE_LICENSE_PATH = ai_life_adapter.LICENSE_PATH.resolve()
AI_LIFE_RESPONSIVE_STYLE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "ai_life"
    / "cedartoy-responsive.v1.css"
)
AI_LIFE_RESPONSIVE_SCRIPT_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "ai_life"
    / "cedartoy-responsive.v1.js"
)
TEST_GAME_INDEX_PATH = Path(__file__).resolve().parent / "test_game.html"
ECO_ASSET_ROOT = (Path(__file__).resolve().parent / "eco" / "assets").resolve()
ICON_ASSET_ROOT = Path("/opt/cedartoy/assets/icons").resolve()
VENDOR_SAVE_ROOT = Path(__file__).resolve().parent / "data" / "vendor_saves"
DUEL_DB_PATH = Path(__file__).resolve().parent / "vendor" / "duel" / "data" / "duel.db"
DUEL_HISTORY_LIMIT = 100
GARDEN_NOTES_DB_PATH = Path(__file__).resolve().parent / "data" / "garden_cat_notes.db"
GARDEN_LEGACY_DB_PATH = Path(__file__).resolve().parent / "vendor" / "Garden-Cat-Engine" / "garden_cat.db"
CAMPING_PLAZA_DB_PATH = Path(os.getenv("CAMPING_PLAZA_DB_PATH", Path(__file__).resolve().parent / "data" / "camping_plaza.db"))
GAME_MAINTENANCE = {
    "camping_plaza": {
        "enabled": False,
        "label": "维护中",
        "message": "露营广场正在维护中，暂时无法进入游戏，请稍后再试。",
    },
}
_HTML_ETAG_CACHE = {}
_HTML_ETAG_CACHE_LOCK = Lock()
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
PWD_CONTEXT = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto") if CryptContext else None
# SQLite CURRENT_TIMESTAMP is UTC; use China wall time for stored timestamps.
SQL_NOW = "datetime('now', 'localtime')"
TIMEZONE_MIGRATION_KEY = "platform_timezone_utc_to_shanghai_20260602"
REQUEST_RATE_LIMIT_WINDOW_SECONDS = 60
REQUEST_RATE_LIMIT_MAX = 60
# duel 的官方网关挂等可持续数分钟，首次 prepare 使用独立配额，
# 避免和常规 MCP 的 60 次/分钟桶互相挤占。内部心跳不重复计数。
DUEL_REQUEST_RATE_LIMIT_MAX = 120
DUEL_WEB_REQUEST_RATE_LIMIT_MAX = 120
REGISTER_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
REGISTER_RATE_LIMIT_MAX = 3
FAILED_LOGIN_WINDOW_SECONDS = 10 * 60
FAILED_LOGIN_MAX = 8
EMAIL_CODE_TTL_SECONDS = 10 * 60
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_SEND_COOLDOWN_SECONDS = 60
EMAIL_SEND_WINDOW_SECONDS = 60 * 60
EMAIL_SEND_MAX_PER_EMAIL = 5
EMAIL_SEND_MAX_PER_ACCOUNT = 5
EMAIL_SEND_MAX_PER_IP = 20
EMAIL_VERIFY_WINDOW_SECONDS = 10 * 60
EMAIL_VERIFY_MAX_PER_ACCOUNT = 10
EMAIL_VERIFY_MAX_PER_IP = 20
EMAIL_PROVIDER_ERROR_CODE = -32050
ADMIN_USERS_DEFAULT_PAGE_SIZE = 50
ADMIN_USERS_MAX_PAGE_SIZE = 200
ADMIN_USERS_MAX_SEARCH_LENGTH = 100
RECENT_REGISTER_NOTICE_SECONDS = 24 * 60 * 60
RENAME_COOLDOWN_SECONDS = 72 * 60 * 60
RATE_LIMIT_ERROR_CODE = -32029
REQUEST_RATE_LIMIT_MESSAGE = "操作太快了，请稍等片刻再试"
REGISTER_RATE_LIMIT_MESSAGE = "注册太频繁了，请稍后再试"
FAILED_LOGIN_RATE_LIMIT_MESSAGE = "登录失败次数过多，请稍后再试"
RECENT_REGISTER_NOTICE = "检测到你近期已注册过账号；如是同一只小机且已没有旧账号的有效 Token，请改用 login 登录旧账号，避免产生多个身份"
DEFAULT_HUMAN_AVATAR = "🙂"
DEFAULT_AI_AVATAR = "🤖"
AVATAR_MAX_CODEPOINTS = 16
AVATAR_MAX_UTF8_BYTES = 64
_REQUEST_RATE_LIMIT = {}
_DUEL_WEB_REQUEST_RATE_LIMIT = {}
_REGISTER_RATE_LIMIT = {}
_FAILED_LOGIN_RATE_LIMIT = {}
_RATE_LIMIT_LOCK = Lock()
_ANTI_ADDICTION_LOCK = anti_addiction._ANTI_ADDICTION_LOCK
# Single cache owner: module callbacks read/write this patchable compatibility slot.
_ANTI_ADDICTION_ANY_ENABLED = None
_ECO_HUMAN_ACTION_RATE_LIMIT = {}
_ECO_HUMAN_ACTION_RATE_LIMIT_LOCK = Lock()
ECO_HUMAN_ACTION_MIN_INTERVAL_SECONDS = 1.0

handle_mbti_mcp = mbti_handler.handle_mcp
handle_enneagram_mcp = enneagram_handler.handle_mcp
handle_dnd_mcp = dnd_handler.handle_mcp
handle_love_mcp = love_handler.handle_mcp
handle_ecr_mcp = ecr_handler.handle_mcp
handle_humanity_mcp = humanity_handler.handle_mcp
handle_sins_virtues_mcp = sins_virtues_handler.handle_mcp


_PLATFORM_TOOLS = mcp_schema._build_platform_tools(
    avatar_max_codepoints=AVATAR_MAX_CODEPOINTS,
    max_invite_question=MAX_INVITE_QUESTION,
    feedback_max_length=announcements.FEEDBACK_MAX_LENGTH,
)


def _build_root_platform_tools():
    return mcp_schema._build_root_platform_tools(_PLATFORM_TOOLS)


_ROOT_PLATFORM_TOOLS = _build_root_platform_tools()


def _build_kelivo_platform_tools():
    return mcp_schema._build_kelivo_platform_tools(
        _PLATFORM_TOOLS, feedback_max_length=announcements.FEEDBACK_MAX_LENGTH,
    )


_KELIVO_PLATFORM_TOOLS = _build_kelivo_platform_tools()
_ROOT_TOOL_NAMES = mcp_schema._ROOT_TOOL_NAMES
_ROOT_MCP_PATHS = frozenset({"/", "/mcp", "/mcp/"})
_ROOT_MCP_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
_ROOT_MCP_LEGACY_PROTOCOL_VERSION = "2024-11-05"


def _is_kelivo_user_agent(user_agent):
    return mcp_schema._is_kelivo_user_agent(user_agent)


def _blocked_mcp_client_message(user_agent):
    normalized = (user_agent or "").lower()
    if normalized.startswith("evolia/") or "evolia" in normalized:
        return (
            "本服务 CEDAR TOY 为个人维护的非商业公益项目，未授权任何商业软件接入或集成。"
            "检测到你正在通过未授权的第三方商业软件连接本服务，连接已被拒绝。"
            "如有疑问请联系：邮箱 1452010907@qq.com / 小红书 501518888。"
        )
    return None


def _handle_root_mcp(payload, user_agent="", path_token=None, client_ip=None, bearer_token=None):
    return mcp_dispatch._handle_root_mcp(
        payload, user_agent, path_token, client_ip, bearer_token,
        _McpError=_McpError,
        _ROOT_MCP_LEGACY_PROTOCOL_VERSION=_ROOT_MCP_LEGACY_PROTOCOL_VERSION,
        _ROOT_MCP_PROTOCOL_VERSIONS=_ROOT_MCP_PROTOCOL_VERSIONS,
        _authenticated_ai_player_id=_authenticated_ai_player_id,
        _blocked_mcp_client_message=_blocked_mcp_client_message,
        _duel_mcp_error_text=_duel_mcp_error_text,
        _duel_unread_request_reminder=_duel_unread_request_reminder,
        _json_rpc_error=_json_rpc_error,
        _json_rpc_result=_json_rpc_result,
        _mcp_forced_announcement=_mcp_forced_announcement,
        _root_tools=_root_tools,
        _tool_account=_tool_account,
        _tool_get_guide=_tool_get_guide,
        _tool_list_games=_tool_list_games,
        _tool_play=_tool_play,
        logger=logger,
    )


def _duel_mcp_error_text(exc):
    return duel_bridge._duel_mcp_error_text(
        exc,
        json=json,
    )


def _db_connect():
    conn = sqlite3.connect(TURTLE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _sessions_db_connect():
    conn = sqlite3.connect(SESSIONS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _game_player_ids(user):
    return player_identity._game_player_ids(
        user,
        GAME_PLAYER_ID_RE=GAME_PLAYER_ID_RE,
        _account_username_aliases=_account_username_aliases,
    )


MIN_SAVE_SLOT = 1
MAX_SAVE_SLOT = 5


def _account_slot_player_id(user_id, slot):
    return player_identity._account_slot_player_id(user_id, slot)


def _account_slot_player_ids(user):
    return player_identity._account_slot_player_ids(
        user,
        GAME_PLAYER_ID_RE=GAME_PLAYER_ID_RE,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _account_slot_player_id=_account_slot_player_id,
        _account_username_aliases=_account_username_aliases,
    )


def _normalize_save_slot(slot):
    return player_identity._normalize_save_slot(
        slot,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
    )


def _garden_cat_watchable_gardens_for_user(user, save_root=None):
    """Read summaries for existing saves belonging to a human's bound AIs.

    This deliberately bypasses the Garden-Cat engine and its persistence store:
    opening state.json directly keeps the picker read-only and cannot run elapsed
    time settlement or create a missing save.
    """
    if not user or user.get("is_ai"):
        raise _McpError(-32003, "只有人类账号可以围观花园")
    root = Path(save_root) if save_root is not None else VENDOR_SAVE_ROOT / "garden_cat"
    with _db_connect() as conn:
        machines = conn.execute(
            """
            SELECT ai.id, ai.username
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND ai.is_ai = 1
              AND ai.deleted_at IS NULL
            ORDER BY ai.username, ai.id
            """,
            (int(user["id"]),),
        ).fetchall()

    gardens = []
    for machine in machines:
        for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1):
            player_id = _account_slot_player_id(machine["id"], slot)
            state_path = root / player_id / "state.json"
            if not state_path.is_file():
                continue
            try:
                with state_path.open("r", encoding="utf-8") as handle:
                    state = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("garden_cat picker skipped unreadable save %s: %s", state_path, exc)
                continue
            if not isinstance(state, dict):
                logger.warning("garden_cat picker skipped non-object save %s", state_path)
                continue
            garden_name = state.get("garden_name")
            if not isinstance(garden_name, str) or not garden_name.strip():
                garden_name = "未命名花园"
            encyclopedia = state.get("encyclopedia")
            gardens.append(
                {
                    "ai_user_id": int(machine["id"]),
                    "machine_name": machine["username"],
                    "slot": slot,
                    "garden_name": garden_name,
                    "money": state.get("money", 0),
                    "encyclopedia_count": len(encyclopedia) if isinstance(encyclopedia, list) else 0,
                    "has_cat": state.get("cat") is not None,
                }
            )
    return gardens


def _garden_cat_watchable_gardens(raw_token):
    user = _current_account(raw_token)
    return {"gardens": _garden_cat_watchable_gardens_for_user(user)}


def _bound_ai_slot_target_for_user(user, requested_player):
    return player_identity._bound_ai_slot_target_for_user(
        user, requested_player,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _account_slot_player_id=_account_slot_player_id,
        _db_connect=_db_connect,
        re=re,
    )


def _forest_bound_target_for_user(user, requested_player):
    return player_identity._forest_bound_target_for_user(
        user, requested_player,
        _bound_ai_slot_target_for_user=_bound_ai_slot_target_for_user,
    )


def _moonlit_bound_target_for_user(user, requested_player):
    return player_identity._moonlit_bound_target_for_user(
        user, requested_player,
        _bound_ai_slot_target_for_user=_bound_ai_slot_target_for_user,
    )


def _ai_life_bound_target_for_user(user, requested_player):
    return player_identity._ai_life_bound_target_for_user(
        user, requested_player,
        _bound_ai_slot_target_for_user=_bound_ai_slot_target_for_user,
    )


def _forest_watchable_slots_for_user(user):
    if not user or user.get("is_ai"):
        raise _McpError(-32003, "只有人类账号可以进入双人森林")
    with _db_connect() as conn:
        machines = conn.execute(
            """
            SELECT ai.id, ai.username
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND ai.is_ai = 1
              AND ai.deleted_at IS NULL
            ORDER BY ai.username, ai.id
            """,
            (int(user["id"]),),
        ).fetchall()
    result = []
    for machine in machines:
        slots = []
        for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1):
            player_id = _account_slot_player_id(machine["id"], slot)
            summary = forest_adapter.save_summary(player_id)
            slots.append(
                {
                    "slot": slot,
                    "exists": summary is not None,
                    "summary": summary,
                }
            )
        result.append(
            {
                "ai_user_id": int(machine["id"]),
                "machine_name": str(machine["username"]),
                "slots": slots,
            }
        )
    return result


def _forest_watchable_slots(raw_token):
    return {"machines": _forest_watchable_slots_for_user(_current_account(raw_token))}


def _eco_watchable_ponds_for_user(user):
    if not user or user.get("is_ai"):
        raise _McpError(-32003, "只有人类账号可以围观池塘")
    with _db_connect() as conn:
        machines = conn.execute(
            """
            SELECT ai.id, ai.username
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND ai.is_ai = 1
              AND ai.deleted_at IS NULL
            ORDER BY ai.username, ai.id
            """,
            (int(user["id"]),),
        ).fetchall()

    ponds = []
    if not machines or not SESSIONS_DB_PATH.exists():
        return ponds
    with _sessions_db_connect() as conn:
        if not _table_exists(conn, "eco_sessions"):
            return ponds
        for machine_row in machines:
            machine = dict(machine_row)
            candidates = _account_slot_player_ids(machine)
            placeholders = ",".join("?" for _ in candidates)
            rows = conn.execute(
                f"""
                SELECT player_id, save_data
                FROM eco_sessions
                WHERE player_id IN ({placeholders})
                """,
                [player_id for player_id, _slot in candidates],
            ).fetchall()
            rows_by_player_id = {str(row["player_id"]): row for row in rows}
            seen_slots = set()
            for player_id, slot in candidates:
                if slot in seen_slots:
                    continue
                row = rows_by_player_id.get(player_id)
                if row is None:
                    continue
                try:
                    state = json.loads(row["save_data"])
                    if not isinstance(state, dict):
                        raise ValueError("save_data is not a JSON object")
                    day = state["turn"]
                    if isinstance(day, bool) or not isinstance(day, int):
                        raise ValueError("turn is not an integer")
                except (TypeError, ValueError, KeyError, UnicodeDecodeError) as exc:
                    logger.warning("eco pond picker skipped unreadable save %s: %s", player_id, exc)
                    continue
                ponds.append(
                    {
                        "ai_user_id": int(machine["id"]),
                        "machine_name": machine["username"],
                        "slot": slot,
                        "day": day,
                    }
                )
                seen_slots.add(slot)
    return ponds


def _eco_watchable_ponds(raw_token):
    user = _current_account(raw_token)
    return {"ponds": _eco_watchable_ponds_for_user(user)}


def _row_dict(row):
    return dict(row) if row is not None else None


def _table_exists(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone())


def _create_platform_localtime_triggers(conn):
    specs = {
        "toy_users": ("id", ("created_at", "last_active_at")),
        "user_bindings": ("id", ("created_at",)),
    }
    for table, (pk, columns) in specs.items():
        if not _table_exists(conn, table):
            continue
        assignments = ", ".join(f"{column} = datetime('now', 'localtime')" for column in columns)
        conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_insert_localtime
            AFTER INSERT ON {table}
            BEGIN
                UPDATE {table}
                SET {assignments}
                WHERE {pk} = NEW.{pk};
            END
            """
        )


def _init_registration_events_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_registration_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            is_ai INTEGER NOT NULL DEFAULT 0,
            client_ip TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_account_registration_events_ip_created
        ON account_registration_events(client_ip, created_at)
        """
    )


def _init_password_reset_tokens_table(conn):
    return account_recovery._init_password_reset_tokens_table(
        conn,
        _init_account_recovery_table=_init_account_recovery_table,
    )


def _init_account_recovery_table(conn):
    return account_recovery._init_account_recovery_table(conn)


def _init_username_changes_table(conn):
    """Create the append-only rename ledger without rewriting historical users."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_username_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            old_username TEXT NOT NULL,
            new_username TEXT NOT NULL,
            changed_at_epoch INTEGER NOT NULL DEFAULT (unixepoch())
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_account_username_changes_user_changed
        ON account_username_changes(user_id, changed_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_account_username_changes_old_username
        ON account_username_changes(old_username)
        """
    )


def _init_account_security_schema(conn):
    """Idempotent account/token compatibility and query-performance schema."""
    avatar_appearances.init_schema(conn)
    if _table_exists(conn, "toy_users"):
        _add_column_if_missing(
            conn,
            "toy_users",
            "ai_token_version",
            "INTEGER NOT NULL DEFAULT 0",
        )
        # Separate representation from value so a future image-backed avatar
        # can be added without replacing the account field.
        _add_column_if_missing(conn, "toy_users", "avatar_type", "TEXT")
        _add_column_if_missing(conn, "toy_users", "avatar_value", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS legacy_ai_token_hashes (
            token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
            user_id INTEGER NOT NULL,
            token_version INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'verified_import',
            created_at TIMESTAMP NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_legacy_ai_token_hashes_user
        ON legacy_ai_token_hashes(user_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_access_tokens (
            token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            generation INTEGER NOT NULL,
            format_version INTEGER NOT NULL DEFAULT 1 CHECK (format_version = 1),
            created_at_epoch INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)),
            revoked_at_epoch INTEGER,
            revoked_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_access_tokens_user_active
        ON ai_access_tokens(user_id, revoked_at_epoch, generation, created_at_epoch)
        """
    )
    if _table_exists(conn, "players"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_players_user_id ON players(user_id)"
        )


def _init_operit_schema(conn):
    return operit._init_operit_schema(conn)


def _invalidate_operit_credentials_in_transaction(conn, user_id, *, now_epoch=None):
    return operit._invalidate_operit_credentials_in_transaction(
        conn, user_id, now_epoch=now_epoch,
        _table_exists=_table_exists,
        time=time,
    )


def _init_account_email_schema(conn):
    return account_email._init_account_email_schema(conn)


def _init_anti_addiction_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anti_addiction_settings (
            ai_user_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            remind_threshold INTEGER NOT NULL DEFAULT 30,
            step INTEGER NOT NULL DEFAULT 20,
            force_threshold INTEGER NOT NULL DEFAULT 50,
            lock_minutes INTEGER NOT NULL DEFAULT 30,
            allow_self_reset INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anti_addiction_states (
            player_id TEXT PRIMARY KEY,
            streak INTEGER NOT NULL DEFAULT 0,
            locked INTEGER NOT NULL DEFAULT 0,
            locked_at REAL,
            last_play_at REAL,
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    _add_column_if_missing(conn, "anti_addiction_settings", "lock_minutes", "INTEGER NOT NULL DEFAULT 30")
    _add_column_if_missing(conn, "anti_addiction_settings", "allow_self_reset", "INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(conn, "anti_addiction_states", "locked_at", "REAL")


def _init_announcement_tables():
    return announcement_delivery._init_announcement_tables(
        announcements=announcements,
        sessions_db_path=SESSIONS_DB_PATH,
        sessions_db_connect=_sessions_db_connect,
    )


def _human_announcement_identity(user):
    return announcement_delivery._human_announcement_identity(user)


def _current_human_account(raw_token):
    return announcement_delivery._current_human_account(
        raw_token,
        current_account=_current_account,
    )


def _account_announcement_identity(user, account_player_id):
    return announcement_delivery._account_announcement_identity(
        user, account_player_id,
        human_announcement_identity=_human_announcement_identity,
    )


def _announcement_options_for_web(raw):
    return announcement_delivery._announcement_options_for_web(raw)


def _announcement_vote_for_web(votes_raw, feedback):
    return announcement_delivery._announcement_vote_for_web(votes_raw, feedback)


def _web_announcements(raw_token):
    return announcement_delivery._web_announcements(
        raw_token,
        announcements=announcements,
        current_human_account=_current_human_account,
        human_announcement_identity=_human_announcement_identity,
        sessions_db_connect=_sessions_db_connect,
        announcement_options_for_web=_announcement_options_for_web,
        announcement_vote_for_web=_announcement_vote_for_web,
    )


def _mark_web_announcements_read(raw_token, announcement_ids):
    return announcement_delivery._mark_web_announcements_read(
        raw_token, announcement_ids,
        announcements=announcements,
        current_human_account=_current_human_account,
        human_announcement_identity=_human_announcement_identity,
        sessions_db_connect=_sessions_db_connect,
    )


def _submit_web_announcement_vote(
    raw_token,
    announcement_id,
    options,
    feedback=None,
):
    return announcement_delivery._submit_web_announcement_vote(
        raw_token, announcement_id, options, feedback,
        announcements=announcements,
        sessions_db_path=SESSIONS_DB_PATH,
        current_human_account=_current_human_account,
        human_announcement_identity=_human_announcement_identity,
    )


def _add_column_if_missing(conn, table, column, column_sql):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def _migrate_platform_timestamps():
    with _db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        _create_platform_localtime_triggers(conn)
        _init_guest_claim_table(conn)
        _init_registration_events_table(conn)
        _init_password_reset_tokens_table(conn)
        _init_username_changes_table(conn)
        _init_account_security_schema(conn)
        avatar_appearances.seed_trial(conn)
        _init_operit_schema(conn)
        _init_account_email_schema(conn)
        _init_anti_addiction_tables(conn)
        # New voluntary-deletion fields are independent from legacy deleted_at.
        # In particular, no historical soft-deleted row is scheduled here.
        account_deletion.init_schema(conn)
        if conn.execute("SELECT value FROM settings WHERE key = ?", (TIMEZONE_MIGRATION_KEY,)).fetchone():
            conn.commit()
            return
        if _table_exists(conn, "toy_users"):
            conn.execute(
                """
                UPDATE toy_users
                SET last_active_at = datetime(last_active_at, '+8 hours')
                WHERE last_active_at IS NOT NULL
                  AND created_at IS NOT NULL
                  AND last_active_at = created_at
                """
            )
            conn.execute(
                """
                UPDATE toy_users
                SET created_at = datetime(created_at, '+8 hours')
                WHERE created_at IS NOT NULL
                """
            )
        if _table_exists(conn, "user_bindings"):
            conn.execute(
                """
                UPDATE user_bindings
                SET created_at = datetime(created_at, '+8 hours')
                WHERE created_at IS NOT NULL
                """
            )
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')", (TIMEZONE_MIGRATION_KEY,))
        conn.commit()


def _hash_password(password):
    return auth._hash_password(
        password,
        PWD_CONTEXT=PWD_CONTEXT,
        _ab64_encode=_ab64_encode,
        hashlib=hashlib,
        secrets=secrets,
    )


def _verify_password(password, password_hash):
    return auth._verify_password(
        password, password_hash,
        PWD_CONTEXT=PWD_CONTEXT,
        _ab64_decode=_ab64_decode,
        _ab64_encode=_ab64_encode,
        hashlib=hashlib,
        hmac=hmac,
    )


def _ab64_encode(raw):
    return auth._ab64_encode(
        raw,
        base64=base64,
    )


def _ab64_decode(value):
    return auth._ab64_decode(
        value,
        base64=base64,
    )


def _normalize_email(value):
    return account_email._normalize_email(
        value,
        _McpError=_McpError,
        re=re,
    )


def _mask_email(email):
    return account_email._mask_email(email)


def _email_hmac(*parts):
    return account_email._email_hmac(
        *parts,
        TOY_SECRET=TOY_SECRET,
        hashlib=hashlib,
        hmac=hmac,
    )


def _email_code_hash(code, salt, purpose, user_id, email):
    return account_email._email_code_hash(
        code, salt, purpose, user_id, email,
        _email_hmac=_email_hmac,
    )


def _email_ip_hash(client_ip):
    return account_email._email_ip_hash(
        client_ip,
        _email_hmac=_email_hmac,
    )


def _email_value_hash(email):
    return account_email._email_value_hash(
        email,
        _email_hmac=_email_hmac,
    )


def _smtp_config():
    return account_email._smtp_config(
        EMAIL_PROVIDER_ERROR_CODE=EMAIL_PROVIDER_ERROR_CODE,
        _McpError=_McpError,
        os=os,
    )


def _send_verification_email(email, code, purpose, smtp_config):
    return account_email._send_verification_email(
        email, code, purpose, smtp_config,
        EMAIL_PROVIDER_ERROR_CODE=EMAIL_PROVIDER_ERROR_CODE,
        EmailMessage=EmailMessage,
        _McpError=_McpError,
        logger=logger,
        smtplib=smtplib,
        ssl=ssl,
    )


def _b64url_encode(raw):
    return auth._b64url_encode(
        raw,
        base64=base64,
    )


def _b64url_decode(value):
    return auth._b64url_decode(
        value,
        base64=base64,
    )


def _jwt_unverified_payload(token):
    return auth._jwt_unverified_payload(
        token,
        base64=base64,
        json=json,
    )


def _jwt_encode(payload):
    return auth._jwt_encode(
        payload,
        JWT_ALGORITHM=JWT_ALGORITHM,
        TOY_SECRET=TOY_SECRET,
        _b64url_encode=_b64url_encode,
        hashlib=hashlib,
        hmac=hmac,
        json=json,
    )


def _legacy_ai_token_hash(token):
    return auth._legacy_ai_token_hash(
        token,
        hashlib=hashlib,
    )


def _opaque_ai_token_hash(token):
    return auth._opaque_ai_token_hash(
        token,
        hashlib=hashlib,
    )


def _issue_ai_token_in_transaction(conn, user):
    return auth._issue_ai_token_in_transaction(
        conn, user,
        AI_OPAQUE_TOKEN_BYTES=AI_OPAQUE_TOKEN_BYTES,
        AI_OPAQUE_TOKEN_FORMAT_VERSION=AI_OPAQUE_TOKEN_FORMAT_VERSION,
        AI_OPAQUE_TOKEN_PREFIX=AI_OPAQUE_TOKEN_PREFIX,
        _McpError=_McpError,
        _opaque_ai_token_hash=_opaque_ai_token_hash,
        secrets=secrets,
        sqlite3=sqlite3,
    )


def _issue_initial_account_token_in_transaction(conn, user):
    return auth._issue_initial_account_token_in_transaction(
        conn, user,
        _create_account_jwt=_create_account_jwt,
        _issue_ai_token_in_transaction=_issue_ai_token_in_transaction,
    )


def _legacy_allowlisted_ai_payload(token):
    return auth._legacy_allowlisted_ai_payload(
        token,
        LEGACY_AI_JWT_COMPAT_ENABLED=LEGACY_AI_JWT_COMPAT_ENABLED,
        _db_connect=_db_connect,
        _jwt_unverified_payload=_jwt_unverified_payload,
        _legacy_ai_token_hash=_legacy_ai_token_hash,
        _table_exists=_table_exists,
        sqlite3=sqlite3,
    )


def _jwt_decode(token):
    return auth._jwt_decode(
        token,
        JWT_ALGORITHM=JWT_ALGORITHM,
        LEGACY_AI_JWT_COMPAT_ENABLED=LEGACY_AI_JWT_COMPAT_ENABLED,
        TOY_SECRET=TOY_SECRET,
        _b64url_decode=_b64url_decode,
        _legacy_allowlisted_ai_payload=_legacy_allowlisted_ai_payload,
        hashlib=hashlib,
        hmac=hmac,
        json=json,
        time=time,
    )


def _create_account_jwt(user):
    return auth._create_account_jwt(
        user,
        HUMAN_TOKEN_SECONDS=HUMAN_TOKEN_SECONDS,
        _jwt_encode=_jwt_encode,
        time=time,
    )


def _create_account_token(user):
    return auth._create_account_token(
        user,
        _McpError=_McpError,
        _create_account_jwt=_create_account_jwt,
        _db_connect=_db_connect,
        _issue_ai_token_in_transaction=_issue_ai_token_in_transaction,
        _replace_ai_token_in_transaction=_replace_ai_token_in_transaction,
        _row_dict=_row_dict,
    )


def _default_avatar_value(user):
    return accounts._default_avatar_value(
        user,
        DEFAULT_AI_AVATAR=DEFAULT_AI_AVATAR,
        DEFAULT_HUMAN_AVATAR=DEFAULT_HUMAN_AVATAR,
    )


def _public_avatar(user):
    return accounts._public_avatar(
        user,
        _default_avatar_value=_default_avatar_value,
    )


def _public_user(user):
    return accounts._public_user(
        user,
        _db_connect=_db_connect,
        _public_avatar=_public_avatar,
        avatar_appearances=avatar_appearances,
        time=time,
    )


def _account_username_aliases(user):
    return accounts._account_username_aliases(
        user,
        _db_connect=_db_connect,
        _table_exists=_table_exists,
        sqlite3=sqlite3,
    )


def _current_account(raw_token, *, allow_pending_deletion=False):
    return auth._current_account(
        raw_token, allow_pending_deletion=allow_pending_deletion,
        AI_OPAQUE_TOKEN_FORMAT_VERSION=AI_OPAQUE_TOKEN_FORMAT_VERSION,
        AI_OPAQUE_TOKEN_PREFIX=AI_OPAQUE_TOKEN_PREFIX,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _jwt_decode=_jwt_decode,
        _jwt_unverified_payload=_jwt_unverified_payload,
        _opaque_ai_token_hash=_opaque_ai_token_hash,
        _row_dict=_row_dict,
        time=time,
    )


def _path_token_user_id(path_token):
    return auth._path_token_user_id(
        path_token,
        AI_OPAQUE_TOKEN_FORMAT_VERSION=AI_OPAQUE_TOKEN_FORMAT_VERSION,
        AI_OPAQUE_TOKEN_PREFIX=AI_OPAQUE_TOKEN_PREFIX,
        _db_connect=_db_connect,
        _jwt_decode=_jwt_decode,
        _opaque_ai_token_hash=_opaque_ai_token_hash,
        sqlite3=sqlite3,
    )


def _prune_rate_limit_buckets(buckets, now, window_seconds):
    empty_keys = []
    for key, timestamps in buckets.items():
        while timestamps and now - timestamps[0] >= window_seconds:
            timestamps.pop(0)
        if not timestamps:
            empty_keys.append(key)
    for key in empty_keys:
        buckets.pop(key, None)


def _check_sliding_window_limit(buckets, identity, *, now, window_seconds, max_count):
    with _RATE_LIMIT_LOCK:
        _prune_rate_limit_buckets(buckets, now, window_seconds)
        timestamps = buckets.setdefault(identity, [])
        while timestamps and now - timestamps[0] >= window_seconds:
            timestamps.pop(0)
        if len(timestamps) >= max_count:
            return False
        timestamps.append(now)
        return True


def _check_request_rate_limit(identity, max_count=REQUEST_RATE_LIMIT_MAX):
    return _check_sliding_window_limit(
        _REQUEST_RATE_LIMIT,
        identity,
        now=time.time(),
        window_seconds=REQUEST_RATE_LIMIT_WINDOW_SECONDS,
        max_count=max_count,
    )


def _check_duel_web_request_rate_limit(identity):
    """Use a separate bucket so duel polling cannot consume the site's MCP quota."""
    return _check_sliding_window_limit(
        _DUEL_WEB_REQUEST_RATE_LIMIT,
        identity,
        now=time.time(),
        window_seconds=REQUEST_RATE_LIMIT_WINDOW_SECONDS,
        max_count=DUEL_WEB_REQUEST_RATE_LIMIT_MAX,
    )


def _is_duel_play_payload(payload):
    """Whether this root MCP request is play(game="duel", ...)."""
    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return False
    params = payload.get("params")
    if not isinstance(params, dict) or params.get("name") != "play":
        return False
    arguments = params.get("arguments")
    return isinstance(arguments, dict) and arguments.get("game") == "duel"


def _mcp_path_and_token(request_target):
    path = str(request_target or "").split("?", 1)[0]
    if (
        path in (
            *_ROOT_MCP_PATHS,
            "/mbti", "/enneagram", "/dnd", "/love", "/ecr",
            "/humanity", "/sins_virtues",
        )
        or path.startswith("/api/")
    ):
        return path, None
    token = urllib.parse.unquote(path.strip("/"))
    if token and "/" not in token:
        return path, token
    return path, None


def _request_rate_limit_identity(path_token, client_ip):
    user_id = _path_token_user_id(path_token)
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{client_ip or 'unknown'}"


def _check_register_rate_limit(ip):
    return accounts._check_register_rate_limit(
        ip,
        REGISTER_RATE_LIMIT_MAX=REGISTER_RATE_LIMIT_MAX,
        REGISTER_RATE_LIMIT_WINDOW_SECONDS=REGISTER_RATE_LIMIT_WINDOW_SECONDS,
        _REGISTER_RATE_LIMIT=_REGISTER_RATE_LIMIT,
        _check_sliding_window_limit=_check_sliding_window_limit,
        time=time,
    )


def _failed_login_identity(client_ip, username):
    return accounts._failed_login_identity(client_ip, username)


def _failed_login_is_limited(client_ip, username, *, now=None):
    return accounts._failed_login_is_limited(
        client_ip, username, now=now,
        FAILED_LOGIN_MAX=FAILED_LOGIN_MAX,
        FAILED_LOGIN_WINDOW_SECONDS=FAILED_LOGIN_WINDOW_SECONDS,
        _FAILED_LOGIN_RATE_LIMIT=_FAILED_LOGIN_RATE_LIMIT,
        _RATE_LIMIT_LOCK=_RATE_LIMIT_LOCK,
        _failed_login_identity=_failed_login_identity,
        _prune_rate_limit_buckets=_prune_rate_limit_buckets,
        time=time,
    )


def _record_failed_login(client_ip, username, *, now=None):
    return accounts._record_failed_login(
        client_ip, username, now=now,
        FAILED_LOGIN_MAX=FAILED_LOGIN_MAX,
        FAILED_LOGIN_WINDOW_SECONDS=FAILED_LOGIN_WINDOW_SECONDS,
        _FAILED_LOGIN_RATE_LIMIT=_FAILED_LOGIN_RATE_LIMIT,
        _RATE_LIMIT_LOCK=_RATE_LIMIT_LOCK,
        _failed_login_identity=_failed_login_identity,
        _prune_rate_limit_buckets=_prune_rate_limit_buckets,
        time=time,
    )


def _clear_failed_login(client_ip, username):
    return accounts._clear_failed_login(
        client_ip, username,
        _FAILED_LOGIN_RATE_LIMIT=_FAILED_LOGIN_RATE_LIMIT,
        _RATE_LIMIT_LOCK=_RATE_LIMIT_LOCK,
        _failed_login_identity=_failed_login_identity,
    )


def _raise_failed_login(client_ip, username):
    return accounts._raise_failed_login(
        client_ip, username,
        FAILED_LOGIN_RATE_LIMIT_MESSAGE=FAILED_LOGIN_RATE_LIMIT_MESSAGE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _record_failed_login=_record_failed_login,
    )


def _username_conflict(conn, username, *, exclude_user_id=None, include_history=True):
    return accounts._username_conflict(
        conn, username, exclude_user_id=exclude_user_id, include_history=include_history,
        _init_username_changes_table=_init_username_changes_table,
        _table_exists=_table_exists,
    )


def _user_exists(username):
    return accounts._user_exists(
        username,
        _db_connect=_db_connect,
        _username_conflict=_username_conflict,
    )


def _enforce_register_rate_limit(username, client_ip):
    return accounts._enforce_register_rate_limit(
        username, client_ip,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        REGISTER_RATE_LIMIT_MESSAGE=REGISTER_RATE_LIMIT_MESSAGE,
        _McpError=_McpError,
        _check_register_rate_limit=_check_register_rate_limit,
        _user_exists=_user_exists,
    )


def _recent_registration_exists(conn, client_ip):
    return accounts._recent_registration_exists(
        conn, client_ip,
        RECENT_REGISTER_NOTICE_SECONDS=RECENT_REGISTER_NOTICE_SECONDS,
        _init_registration_events_table=_init_registration_events_table,
    )


def _record_successful_registration(conn, user, client_ip):
    return accounts._record_successful_registration(
        conn, user, client_ip,
        _init_registration_events_table=_init_registration_events_table,
        avatar_appearances=avatar_appearances,
    )


def _append_recent_registration_notice(result, had_recent_registration):
    return accounts._append_recent_registration_notice(
        result, had_recent_registration,
        RECENT_REGISTER_NOTICE=RECENT_REGISTER_NOTICE,
    )


def _normalize_credential_field(value, field_name):
    return accounts._normalize_credential_field(
        value, field_name,
        _McpError=_McpError,
    )


def _validate_username(username):
    return accounts._validate_username(
        username,
        _McpError=_McpError,
        re=re,
    )


def _validate_credentials(username, password):
    return accounts._validate_credentials(
        username, password,
        _McpError=_McpError,
        _validate_username=_validate_username,
    )


def _is_emoji_base_codepoint(codepoint):
    return accounts._is_emoji_base_codepoint(codepoint)


def _normalize_emoji_avatar(value, *, default=None):
    return accounts._normalize_emoji_avatar(
        value, default=default,
        AVATAR_MAX_CODEPOINTS=AVATAR_MAX_CODEPOINTS,
        AVATAR_MAX_UTF8_BYTES=AVATAR_MAX_UTF8_BYTES,
        _McpError=_McpError,
        _is_emoji_base_codepoint=_is_emoji_base_codepoint,
    )


def _avatar_registration_values(value, *, is_ai):
    return accounts._avatar_registration_values(
        value, is_ai=is_ai,
        DEFAULT_AI_AVATAR=DEFAULT_AI_AVATAR,
        DEFAULT_HUMAN_AVATAR=DEFAULT_HUMAN_AVATAR,
        _normalize_emoji_avatar=_normalize_emoji_avatar,
    )


def _login_or_register(username, password, *, is_ai, client_ip=None, avatar=None):
    return accounts._login_or_register(
        username, password, is_ai=is_ai, client_ip=client_ip, avatar=avatar,
        FAILED_LOGIN_RATE_LIMIT_MESSAGE=FAILED_LOGIN_RATE_LIMIT_MESSAGE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _append_recent_registration_notice=_append_recent_registration_notice,
        _avatar_registration_values=_avatar_registration_values,
        _clear_failed_login=_clear_failed_login,
        _create_account_jwt=_create_account_jwt,
        _db_connect=_db_connect,
        _enforce_register_rate_limit=_enforce_register_rate_limit,
        _failed_login_is_limited=_failed_login_is_limited,
        _hash_password=_hash_password,
        _issue_initial_account_token_in_transaction=_issue_initial_account_token_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _raise_failed_login=_raise_failed_login,
        _recent_registration_exists=_recent_registration_exists,
        _record_successful_registration=_record_successful_registration,
        _replace_ai_token_in_transaction=_replace_ai_token_in_transaction,
        _row_dict=_row_dict,
        _username_conflict=_username_conflict,
        _validate_credentials=_validate_credentials,
        _verify_password=_verify_password,
    )


def _register_ai_user_in_transaction(conn, username, password, client_ip, avatar):
    return accounts._register_ai_user_in_transaction(
        conn, username, password, client_ip, avatar,
        _McpError=_McpError,
        _avatar_registration_values=_avatar_registration_values,
        _hash_password=_hash_password,
        _recent_registration_exists=_recent_registration_exists,
        _record_successful_registration=_record_successful_registration,
        _row_dict=_row_dict,
        _username_conflict=_username_conflict,
    )


def _verified_existing_account(conn, username, password, client_ip, *, require_ai=None):
    return accounts._verified_existing_account(
        conn, username, password, client_ip, require_ai=require_ai,
        FAILED_LOGIN_RATE_LIMIT_MESSAGE=FAILED_LOGIN_RATE_LIMIT_MESSAGE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _clear_failed_login=_clear_failed_login,
        _failed_login_is_limited=_failed_login_is_limited,
        _raise_failed_login=_raise_failed_login,
        _row_dict=_row_dict,
        _verify_password=_verify_password,
    )


def _login_or_register_ai(username, password, client_ip=None, avatar=None):
    return accounts._login_or_register_ai(
        username, password, client_ip, avatar,
        _append_recent_registration_notice=_append_recent_registration_notice,
        _db_connect=_db_connect,
        _enforce_register_rate_limit=_enforce_register_rate_limit,
        _issue_initial_account_token_in_transaction=_issue_initial_account_token_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _register_ai_user_in_transaction=_register_ai_user_in_transaction,
        _validate_credentials=_validate_credentials,
    )


def _login_existing_account(username, password, client_ip=None):
    return accounts._login_existing_account(
        username, password, client_ip,
        _McpError=_McpError,
        _create_account_jwt=_create_account_jwt,
        _db_connect=_db_connect,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _replace_ai_token_in_transaction=_replace_ai_token_in_transaction,
        _row_dict=_row_dict,
        _validate_credentials=_validate_credentials,
        _verified_existing_account=_verified_existing_account,
        _verify_password=_verify_password,
    )


def _operit_token_hash(raw_token):
    return operit._operit_token_hash(
        raw_token,
        hashlib=hashlib,
    )


def _operit_client_id(client_id):
    return operit._operit_client_id(
        client_id,
        _McpError=_McpError,
    )


def _operit_client_id_hash(client_id):
    return operit._operit_client_id_hash(
        client_id,
        _operit_client_id=_operit_client_id,
        hashlib=hashlib,
    )


def _reject_pending_operit_user(user):
    return operit._reject_pending_operit_user(
        user,
        _McpError=_McpError,
    )


def _issue_operit_session_in_transaction(conn, user, client_id, *, now_epoch=None):
    return operit._issue_operit_session_in_transaction(
        conn, user, client_id, now_epoch=now_epoch,
        OPERIT_SESSION_FORMAT_VERSION=OPERIT_SESSION_FORMAT_VERSION,
        OPERIT_SESSION_SECONDS=OPERIT_SESSION_SECONDS,
        OPERIT_SESSION_TOKEN_BYTES=OPERIT_SESSION_TOKEN_BYTES,
        OPERIT_SESSION_TOKEN_PREFIX=OPERIT_SESSION_TOKEN_PREFIX,
        _init_operit_schema=_init_operit_schema,
        _operit_client_id_hash=_operit_client_id_hash,
        _operit_token_hash=_operit_token_hash,
        _reject_pending_operit_user=_reject_pending_operit_user,
        secrets=secrets,
        time=time,
    )


def _operit_confirmed_human(human_token, confirm):
    return operit._operit_confirmed_human(
        human_token, confirm,
        _McpError=_McpError,
        _current_account=_current_account,
    )


def _create_operit_ai_session(
    action,
    username,
    password,
    client_id,
    *,
    client_ip=None,
    avatar=None,
    bind_to_human=False,
    confirm_binding=False,
    human_token="",
):
    return operit._create_operit_ai_session(
        action, username, password, client_id, client_ip=client_ip, avatar=avatar, bind_to_human=bind_to_human, confirm_binding=confirm_binding, human_token=human_token,
        _McpError=_McpError,
        _append_recent_registration_notice=_append_recent_registration_notice,
        _db_connect=_db_connect,
        _enforce_register_rate_limit=_enforce_register_rate_limit,
        _ensure_ai_binding=_ensure_ai_binding,
        _init_operit_schema=_init_operit_schema,
        _issue_operit_session_in_transaction=_issue_operit_session_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _operit_client_id=_operit_client_id,
        _operit_confirmed_human=_operit_confirmed_human,
        _public_user=_public_user,
        _register_ai_user_in_transaction=_register_ai_user_in_transaction,
        _reject_pending_operit_user=_reject_pending_operit_user,
        _row_dict=_row_dict,
        _validate_credentials=_validate_credentials,
        _verified_existing_account=_verified_existing_account,
        _verify_password=_verify_password,
    )


def _current_operit_ai(raw_token, client_id, *, touch=True, now_epoch=None):
    return operit._current_operit_ai(
        raw_token, client_id, touch=touch, now_epoch=now_epoch,
        OPERIT_SESSION_FORMAT_VERSION=OPERIT_SESSION_FORMAT_VERSION,
        OPERIT_SESSION_TOKEN_PREFIX=OPERIT_SESSION_TOKEN_PREFIX,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _init_operit_schema=_init_operit_schema,
        _operit_client_id_hash=_operit_client_id_hash,
        _operit_token_hash=_operit_token_hash,
        _reject_pending_operit_user=_reject_pending_operit_user,
        _row_dict=_row_dict,
        time=time,
    )


def _operit_session_status(raw_token, client_id):
    return operit._operit_session_status(
        raw_token, client_id,
        _current_operit_ai=_current_operit_ai,
        _db_connect=_db_connect,
        _operit_token_hash=_operit_token_hash,
        _public_user=_public_user,
    )


def _revoke_operit_session(raw_token, client_id):
    return operit._revoke_operit_session(
        raw_token, client_id,
        _current_operit_ai=_current_operit_ai,
        _db_connect=_db_connect,
        _operit_token_hash=_operit_token_hash,
        time=time,
    )


def _bind_operit_ai(human_token, operit_token, client_id, *, confirm=False):
    return operit._bind_operit_ai(
        human_token, operit_token, client_id, confirm=confirm,
        _current_operit_ai=_current_operit_ai,
        _db_connect=_db_connect,
        _ensure_ai_binding=_ensure_ai_binding,
        _operit_confirmed_human=_operit_confirmed_human,
        _public_user=_public_user,
    )


def _issue_operit_web_ticket(human_token, *, confirm=False, now_epoch=None):
    return operit._issue_operit_web_ticket(
        human_token, confirm=confirm, now_epoch=now_epoch,
        OPERIT_WEB_TICKET_BYTES=OPERIT_WEB_TICKET_BYTES,
        OPERIT_WEB_TICKET_PREFIX=OPERIT_WEB_TICKET_PREFIX,
        OPERIT_WEB_TICKET_SECONDS=OPERIT_WEB_TICKET_SECONDS,
        _db_connect=_db_connect,
        _init_operit_schema=_init_operit_schema,
        _operit_confirmed_human=_operit_confirmed_human,
        _operit_token_hash=_operit_token_hash,
        secrets=secrets,
        time=time,
        urllib=urllib,
    )


def _consume_operit_web_ticket(raw_ticket, *, now_epoch=None):
    return operit._consume_operit_web_ticket(
        raw_ticket, now_epoch=now_epoch,
        OPERIT_WEB_TICKET_PREFIX=OPERIT_WEB_TICKET_PREFIX,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _init_operit_schema=_init_operit_schema,
        _operit_token_hash=_operit_token_hash,
        _reject_pending_operit_user=_reject_pending_operit_user,
        _row_dict=_row_dict,
        time=time,
    )


def _login_or_register_human(username, password, client_ip=None, avatar=None):
    return accounts._login_or_register_human(
        username, password, client_ip, avatar,
        _login_or_register=_login_or_register,
    )


def _login_human(username, password, client_ip=None):
    return accounts._login_human(
        username, password, client_ip,
        FAILED_LOGIN_RATE_LIMIT_MESSAGE=FAILED_LOGIN_RATE_LIMIT_MESSAGE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _clear_failed_login=_clear_failed_login,
        _create_account_token=_create_account_token,
        _db_connect=_db_connect,
        _failed_login_is_limited=_failed_login_is_limited,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _raise_failed_login=_raise_failed_login,
        _row_dict=_row_dict,
        _validate_credentials=_validate_credentials,
        _verify_password=_verify_password,
    )


def _register_human(username, password, client_ip=None, avatar=None):
    return accounts._register_human(
        username, password, client_ip, avatar,
        _McpError=_McpError,
        _append_recent_registration_notice=_append_recent_registration_notice,
        _avatar_registration_values=_avatar_registration_values,
        _create_account_token=_create_account_token,
        _db_connect=_db_connect,
        _enforce_register_rate_limit=_enforce_register_rate_limit,
        _hash_password=_hash_password,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _recent_registration_exists=_recent_registration_exists,
        _record_successful_registration=_record_successful_registration,
        _row_dict=_row_dict,
        _username_conflict=_username_conflict,
        _validate_credentials=_validate_credentials,
    )


def _set_avatar(raw_token, avatar):
    return accounts._set_avatar(
        raw_token, avatar,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _normalize_emoji_avatar=_normalize_emoji_avatar,
        _public_user=_public_user,
        _row_dict=_row_dict,
    )


def _avatar_frame_target(conn, user, target_user_id):
    return accounts._avatar_frame_target(
        conn, user, target_user_id,
        _McpError=_McpError,
        _row_dict=_row_dict,
        re=re,
    )


def _get_avatar_frames(raw_token, target_user_id=None):
    return accounts._get_avatar_frames(
        raw_token, target_user_id,
        _avatar_frame_target=_avatar_frame_target,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_user=_public_user,
        avatar_appearances=avatar_appearances,
    )


def _set_avatar_frame(raw_token, selected, target_user_id=None):
    return accounts._set_avatar_frame(
        raw_token, selected, target_user_id,
        _avatar_frame_target=_avatar_frame_target,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_user=_public_user,
        avatar_appearances=avatar_appearances,
    )


def _rename_next_allowed_at(epoch):
    return accounts._rename_next_allowed_at(
        epoch,
        time=time,
    )


def _rename_user_in_transaction(conn, target, new_username):
    return accounts._rename_user_in_transaction(
        conn, target, new_username,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        RENAME_COOLDOWN_SECONDS=RENAME_COOLDOWN_SECONDS,
        _McpError=_McpError,
        _init_username_changes_table=_init_username_changes_table,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _rename_next_allowed_at=_rename_next_allowed_at,
        _row_dict=_row_dict,
        _username_conflict=_username_conflict,
        _validate_username=_validate_username,
        time=time,
    )


def _rename_self(raw_token, new_username):
    return accounts._rename_self(
        raw_token, new_username,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _rename_user_in_transaction=_rename_user_in_transaction,
        _row_dict=_row_dict,
    )


def _rename_bound_machine(raw_token, ai_user_id, new_username):
    return accounts._rename_bound_machine(
        raw_token, ai_user_id, new_username,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _rename_user_in_transaction=_rename_user_in_transaction,
        _row_dict=_row_dict,
    )


def _require_admin_account(raw_token):
    return admin_accounts._require_admin_account(
        raw_token,
        _McpError=_McpError,
        _current_account=_current_account,
    )


def _admin_activity(range_name="1h"):
    return build_activity_dashboard(
        DUEL_DB_PATH,
        TURTLE_DB_PATH,
        range_name or "1h",
        sessions_db_path=SESSIONS_DB_PATH,
        catalog_provider=_activity_catalog,
        save_stats_provider=lambda: _public_game_stats(strict=True),
    )


def _admin_user_page(page=1, page_size=ADMIN_USERS_DEFAULT_PAGE_SIZE, search=""):
    return admin_accounts._admin_user_page(
        page, page_size, search,
        ADMIN_USERS_MAX_PAGE_SIZE=ADMIN_USERS_MAX_PAGE_SIZE,
        ADMIN_USERS_MAX_SEARCH_LENGTH=ADMIN_USERS_MAX_SEARCH_LENGTH,
        _db_connect=_db_connect,
        account_deletion=account_deletion,
    )


def _admin_update_user(user_id, body, admin_user):
    return admin_accounts._admin_update_user(
        user_id, body, admin_user,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _rename_user_in_transaction=_rename_user_in_transaction,
        _row_dict=_row_dict,
        _validate_username=_validate_username,
        account_deletion=account_deletion,
    )


def _admin_reset_user_password(user_id, body):
    return admin_accounts._admin_reset_user_password(
        user_id, body,
        _McpError=_McpError,
        _complete_recovery_tickets=_complete_recovery_tickets,
        _db_connect=_db_connect,
        _hash_password=_hash_password,
        _invalidate_operit_credentials_in_transaction=_invalidate_operit_credentials_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        account_deletion=account_deletion,
    )


def _generate_reset_link(user_id):
    return account_recovery._generate_reset_link(
        user_id,
        _McpError=_McpError,
        _create_reset_token=_create_reset_token,
        _db_connect=_db_connect,
        _reset_url=_reset_url,
        _row_dict=_row_dict,
        account_deletion=account_deletion,
    )


def _reset_token_hash(token):
    return account_recovery._reset_token_hash(
        token,
        hashlib=hashlib,
    )


def _reset_url(token):
    return account_recovery._reset_url(token)


def _create_reset_token(conn, user_id, *, lifetime_seconds, token=None):
    return account_recovery._create_reset_token(
        conn, user_id, lifetime_seconds=lifetime_seconds, token=token,
        _reset_token_hash=_reset_token_hash,
        secrets=secrets,
        time=time,
    )


_RECOVERY_MISSING = "查询码不正确，请核对后重试。"
_RECOVERY_SUBMITTED = "请保存查询码，用于查看审核结果；一般会在24小时内完成审核"
_RECOVERY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _recovery_text(body, key, limit, *, optional=False):
    return account_recovery._recovery_text(
        body, key, limit, optional=optional,
        _McpError=_McpError,
    )


def _recovery_identity(body):
    return account_recovery._recovery_identity(
        body,
        _McpError=_McpError,
        _recovery_text=_recovery_text,
        re=re,
    )


def _normalize_recovery_code(code):
    return account_recovery._normalize_recovery_code(
        code,
        _McpError=_McpError,
        _RECOVERY_CODE_ALPHABET=_RECOVERY_CODE_ALPHABET,
        _RECOVERY_MISSING=_RECOVERY_MISSING,
        re=re,
    )


def _recovery_query_hash(code):
    return account_recovery._recovery_query_hash(
        code,
        _email_hmac=_email_hmac,
        _normalize_recovery_code=_normalize_recovery_code,
    )


def _submit_recovery_ticket(body, client_ip):
    return account_recovery._submit_recovery_ticket(
        body, client_ip,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _RECOVERY_CODE_ALPHABET=_RECOVERY_CODE_ALPHABET,
        _RECOVERY_SUBMITTED=_RECOVERY_SUBMITTED,
        _db_connect=_db_connect,
        _recovery_identity=_recovery_identity,
        _recovery_query_hash=_recovery_query_hash,
        _recovery_text=_recovery_text,
        _reset_token_hash=_reset_token_hash,
        secrets=secrets,
        time=time,
    )


def _complete_recovery_tickets(conn, user_id):
    return account_recovery._complete_recovery_tickets(
        conn, user_id,
        _table_exists=_table_exists,
        time=time,
    )


def _query_recovery_ticket(body):
    return account_recovery._query_recovery_ticket(
        body,
        _McpError=_McpError,
        _RECOVERY_MISSING=_RECOVERY_MISSING,
        _complete_recovery_tickets=_complete_recovery_tickets,
        _create_reset_token=_create_reset_token,
        _db_connect=_db_connect,
        _email_hmac=_email_hmac,
        _normalize_recovery_code=_normalize_recovery_code,
        _recovery_query_hash=_recovery_query_hash,
        _reset_url=_reset_url,
        _row_dict=_row_dict,
        hmac=hmac,
        re=re,
        secrets=secrets,
        time=time,
    )


def _admin_recovery_tickets(view="pending", page=1):
    return admin_accounts._admin_recovery_tickets(
        view, page,
        _db_connect=_db_connect,
    )


def _review_recovery_ticket(ticket_id, body, admin):
    return admin_accounts._review_recovery_ticket(
        ticket_id, body, admin,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _recovery_text=_recovery_text,
        time=time,
    )


def _reset_machine_password(raw_token, ai_user_id, new_password):
    return account_recovery._reset_machine_password(
        raw_token, ai_user_id, new_password,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _hash_password=_hash_password,
        _invalidate_operit_credentials_in_transaction=_invalidate_operit_credentials_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _row_dict=_row_dict,
    )


def _reset_password_token_info(reset_token):
    return account_recovery._reset_password_token_info(
        reset_token,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _reset_token_hash=_reset_token_hash,
    )


def _reset_password_by_token(reset_token, new_password):
    return account_recovery._reset_password_by_token(
        reset_token, new_password,
        _McpError=_McpError,
        _complete_recovery_tickets=_complete_recovery_tickets,
        _db_connect=_db_connect,
        _hash_password=_hash_password,
        _invalidate_operit_credentials_in_transaction=_invalidate_operit_credentials_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _reset_token_hash=_reset_token_hash,
        _row_dict=_row_dict,
    )


def _admin_release_user(user_id, admin_user):
    return admin_accounts._admin_release_user(
        user_id, admin_user,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _purge_account_deletion=_purge_account_deletion,
        account_deletion=account_deletion,
        time=time,
    )


def _generate_binding_token(raw_token):
    return accounts._generate_binding_token(
        raw_token,
        BINDING_TOKEN_SECONDS=BINDING_TOKEN_SECONDS,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        secrets=secrets,
        time=time,
    )


def _ensure_ai_binding(conn, human_user_id, ai_user_id):
    return accounts._ensure_ai_binding(
        conn, human_user_id, ai_user_id,
        _McpError=_McpError,
        avatar_appearances=avatar_appearances,
    )


def _bind_account(human_token, binding_token):
    return accounts._bind_account(
        human_token, binding_token,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _ensure_ai_binding=_ensure_ai_binding,
        _row_dict=_row_dict,
        re=re,
    )


def _rotate_ai_user_in_transaction(conn, user_id, *, allow_pending_deletion=False):
    return accounts._rotate_ai_user_in_transaction(
        conn, user_id, allow_pending_deletion=allow_pending_deletion,
        _McpError=_McpError,
        _row_dict=_row_dict,
    )


def _replace_ai_token_in_transaction(
    conn, user_id, *, allow_pending_deletion=False
):
    return accounts._replace_ai_token_in_transaction(
        conn, user_id, allow_pending_deletion=allow_pending_deletion,
        _issue_ai_token_in_transaction=_issue_ai_token_in_transaction,
        _rotate_ai_user_in_transaction=_rotate_ai_user_in_transaction,
    )


def _rotate_ai_token(raw_token):
    return accounts._rotate_ai_token(
        raw_token,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_user=_public_user,
        _replace_ai_token_in_transaction=_replace_ai_token_in_transaction,
    )


def _rotate_bound_machine_token(human_token, ai_user_id, password, client_ip=None):
    return accounts._rotate_bound_machine_token(
        human_token, ai_user_id, password, client_ip,
        FAILED_LOGIN_RATE_LIMIT_MESSAGE=FAILED_LOGIN_RATE_LIMIT_MESSAGE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _clear_failed_login=_clear_failed_login,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _failed_login_is_limited=_failed_login_is_limited,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _raise_failed_login=_raise_failed_login,
        _replace_ai_token_in_transaction=_replace_ai_token_in_transaction,
        _row_dict=_row_dict,
        _verify_password=_verify_password,
    )


def _machine_account_token(
    username,
    password,
    *,
    bind=False,
    rotate=False,
    ai_user_id=None,
    human_token="",
    client_ip=None,
):
    return accounts._machine_account_token(
        username, password, bind=bind, rotate=rotate, ai_user_id=ai_user_id, human_token=human_token, client_ip=client_ip,
        FAILED_LOGIN_RATE_LIMIT_MESSAGE=FAILED_LOGIN_RATE_LIMIT_MESSAGE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _clear_failed_login=_clear_failed_login,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _ensure_ai_binding=_ensure_ai_binding,
        _failed_login_is_limited=_failed_login_is_limited,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _raise_failed_login=_raise_failed_login,
        _replace_ai_token_in_transaction=_replace_ai_token_in_transaction,
        _rotate_bound_machine_token=_rotate_bound_machine_token,
        _row_dict=_row_dict,
        _validate_credentials=_validate_credentials,
        _verify_password=_verify_password,
    )


def _unbind_account(raw_token, ai_user_id):
    return accounts._unbind_account(
        raw_token, ai_user_id,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
    )


def _binding_rows(conn, user):
    return accounts._binding_rows(conn, user)


def _public_binding(row):
    return accounts._public_binding(
        row,
        _public_avatar=_public_avatar,
    )


def _bound_human_user_for_saves(raw_token, username):
    return player_identity._bound_human_user_for_saves(
        raw_token, username,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _row_dict=_row_dict,
    )


# Resolve extraction dependencies per call to preserve server-level patches.
def _turtle_soup_stats(conn, user):
    return public_stats._turtle_soup_stats(
        conn, user,
        table_exists=_table_exists,
    )


def _test_stats(user):
    return public_stats._test_stats(
        user,
        game_player_ids=_game_player_ids,
        sessions_db_path=SESSIONS_DB_PATH,
        sessions_db_connect=_sessions_db_connect,
    )


def _game_overview(conn, user):
    return public_stats._game_overview(
        conn, user,
        test_stats=_test_stats,
        turtle_soup_stats=_turtle_soup_stats,
    )


def _count_table_rows(table_name):
    return public_stats._count_table_rows(
        table_name,
        sessions_db_path=SESSIONS_DB_PATH,
        read_only_connect=_read_only_connect,
        table_exists=_table_exists,
    )


def _count_puzzle_box_saves(*, missing=0, busy_timeout_ms=2000):
    return public_stats._count_puzzle_box_saves(
        missing=missing, busy_timeout_ms=busy_timeout_ms,
        sessions_db_path=SESSIONS_DB_PATH,
        read_only_connect=_read_only_connect,
        table_exists=_table_exists,
    )


def _prefill_puzzle_box_homepage_metric(source):
    """Seed only this catalog entry; keep the client live-stats refresh intact."""
    return public_stats._prefill_puzzle_box_homepage_metric(
        source,
        count_puzzle_box_saves=_count_puzzle_box_saves,
        logger=logger,
    )


def _sum_ciyuwu_runs():
    return public_stats._sum_ciyuwu_runs(
        sessions_db_path=SESSIONS_DB_PATH,
        sessions_db_connect=_sessions_db_connect,
        table_exists=_table_exists,
    )


def _vendor_save_stats(game):
    return public_stats._vendor_save_stats(
        game,
        vendor_save_root=VENDOR_SAVE_ROOT,
        ai_life_adapter=ai_life_adapter,
        detroit_adapter=detroit_adapter,
        bar_adapter=bar_adapter,
        get_adapter=lambda name: globals().get(f"{name}_adapter"),
    )


def _activity_catalog():
    """Reuse homepage names; include MCP-only games without a second UI catalog."""
    return public_stats._activity_catalog(
        index_path=TOY_INDEX_PATH,
        ritual_display_name=RITUAL_DISPLAY_NAME,
        identity_games=IDENTITY_GAMES,
    )


def _public_game_stats(*, strict=False):
    return public_stats._public_game_stats(
        strict=strict,
        count_puzzle_box_saves=_count_puzzle_box_saves,
        count_table_rows=_count_table_rows,
        sum_ciyuwu_runs=_sum_ciyuwu_runs,
        count_saved_tarot_sessions=count_saved_tarot_sessions,
        vendor_save_stats=_vendor_save_stats,
        camping_plaza_db_path=CAMPING_PLAZA_DB_PATH,
        camping_plaza_save_admin=_camping_plaza_save_admin,
    )


def _memoria_human_guides(include_content=False):
    items = []
    if MEMORIA_HUMAN_GUIDE_DIR.exists():
        for path in sorted(MEMORIA_HUMAN_GUIDE_DIR.glob("*.md")):
            title = path.stem.replace("-攻略", ""); title = __import__("re").sub(r"^\d+-", "", title); item = {"kind": "攻略", "title": title, "filename": path.name}
            if include_content:
                item["content"] = path.read_text(encoding="utf-8")
            items.append(item)
    if MEMORIA_AFTER_CLEAR_DIR.exists():
        for path in sorted(MEMORIA_AFTER_CLEAR_DIR.iterdir()):
            if not path.is_file():
                continue
            item = {"kind": "通关后阅读", "title": path.stem, "filename": path.name}
            if include_content:
                item["content"] = path.read_text(encoding="utf-8")
            items.append(item)
    return {"items": items, "content_included": bool(include_content)}


def _get_bindings(raw_token):
    return accounts._get_bindings(
        raw_token,
        _McpError=_McpError,
        _binding_rows=_binding_rows,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_binding=_public_binding,
    )


def _get_profile(raw_token):
    return accounts._get_profile(
        raw_token,
        _binding_rows=_binding_rows,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _game_overview=_game_overview,
        _public_avatar=_public_avatar,
        _public_binding=_public_binding,
    )


def _account_me(raw_token):
    return accounts._account_me(
        raw_token,
        _account_deletion_status=_account_deletion_status,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_user=_public_user,
    )


def _require_human_account(raw_token):
    return accounts._require_human_account(
        raw_token,
        _McpError=_McpError,
        _current_account=_current_account,
    )


def _email_send_rate_limit(conn, user_id, email, ip_hash, now):
    return account_email._email_send_rate_limit(
        conn, user_id, email, ip_hash, now,
        EMAIL_SEND_COOLDOWN_SECONDS=EMAIL_SEND_COOLDOWN_SECONDS,
        EMAIL_SEND_MAX_PER_ACCOUNT=EMAIL_SEND_MAX_PER_ACCOUNT,
        EMAIL_SEND_MAX_PER_EMAIL=EMAIL_SEND_MAX_PER_EMAIL,
        EMAIL_SEND_MAX_PER_IP=EMAIL_SEND_MAX_PER_IP,
        EMAIL_SEND_WINDOW_SECONDS=EMAIL_SEND_WINDOW_SECONDS,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
    )


def _issue_email_code(user_id, email, purpose, client_ip):
    return account_email._issue_email_code(
        user_id, email, purpose, client_ip,
        EMAIL_CODE_TTL_SECONDS=EMAIL_CODE_TTL_SECONDS,
        _db_connect=_db_connect,
        _email_code_hash=_email_code_hash,
        _email_ip_hash=_email_ip_hash,
        _email_send_rate_limit=_email_send_rate_limit,
        _mask_email=_mask_email,
        _send_verification_email=_send_verification_email,
        _smtp_config=_smtp_config,
        secrets=secrets,
        time=time,
    )


def _check_email_verify_rate_limit(conn, user_id, ip_hash, now):
    return account_email._check_email_verify_rate_limit(
        conn, user_id, ip_hash, now,
        EMAIL_VERIFY_MAX_PER_ACCOUNT=EMAIL_VERIFY_MAX_PER_ACCOUNT,
        EMAIL_VERIFY_MAX_PER_IP=EMAIL_VERIFY_MAX_PER_IP,
        EMAIL_VERIFY_WINDOW_SECONDS=EMAIL_VERIFY_WINDOW_SECONDS,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
    )


def _verify_email_code_in_transaction(conn, user_id, email, purpose, code, client_ip):
    return account_email._verify_email_code_in_transaction(
        conn, user_id, email, purpose, code, client_ip,
        EMAIL_CODE_MAX_ATTEMPTS=EMAIL_CODE_MAX_ATTEMPTS,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        _McpError=_McpError,
        _check_email_verify_rate_limit=_check_email_verify_rate_limit,
        _email_code_hash=_email_code_hash,
        _email_ip_hash=_email_ip_hash,
        _email_value_hash=_email_value_hash,
        _row_dict=_row_dict,
        hmac=hmac,
        time=time,
    )


def _account_email_status(raw_token):
    return account_email._account_email_status(
        raw_token,
        _db_connect=_db_connect,
        _mask_email=_mask_email,
        _require_human_account=_require_human_account,
    )


def _send_account_email_code(raw_token, email, client_ip=None):
    return account_email._send_account_email_code(
        raw_token, email, client_ip,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _issue_email_code=_issue_email_code,
        _normalize_email=_normalize_email,
        _require_human_account=_require_human_account,
    )


def _confirm_account_email(raw_token, email, code, client_ip=None):
    return account_email._confirm_account_email(
        raw_token, email, code, client_ip,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _mask_email=_mask_email,
        _normalize_email=_normalize_email,
        _require_human_account=_require_human_account,
        _verify_email_code_in_transaction=_verify_email_code_in_transaction,
        sqlite3=sqlite3,
    )


def _unbind_account_email(raw_token, password):
    return account_email._unbind_account_email(
        raw_token, password,
        _McpError=_McpError,
        _db_connect=_db_connect,
        _normalize_credential_field=_normalize_credential_field,
        _require_human_account=_require_human_account,
        _verify_password=_verify_password,
    )


_MACHINE_PASSWORD_RECOVERY_MESSAGE = (
    "这是小机账号。已绑定人类的小机，请让绑定的人类在『我的 -> 我的小机 -> 重置密码』中设置新密码；"
    "未绑定的小机请联系管理员。"
)
_NO_EMAIL_RECOVERY_MESSAGE = "该账号未绑定邮箱，无法自助找回，请联系管理员"


def _account_security_http_status(exc):
    return account_recovery._account_security_http_status(
        exc,
        EMAIL_PROVIDER_ERROR_CODE=EMAIL_PROVIDER_ERROR_CODE,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
    )


def _start_password_recovery(username, client_ip=None):
    return account_recovery._start_password_recovery(
        username, client_ip,
        _MACHINE_PASSWORD_RECOVERY_MESSAGE=_MACHINE_PASSWORD_RECOVERY_MESSAGE,
        _NO_EMAIL_RECOVERY_MESSAGE=_NO_EMAIL_RECOVERY_MESSAGE,
        _db_connect=_db_connect,
        _issue_email_code=_issue_email_code,
        _row_dict=_row_dict,
        account_deletion=account_deletion,
    )


def _reset_human_password_by_email(username, code, new_password, client_ip=None):
    return account_recovery._reset_human_password_by_email(
        username, code, new_password, client_ip,
        _MACHINE_PASSWORD_RECOVERY_MESSAGE=_MACHINE_PASSWORD_RECOVERY_MESSAGE,
        _McpError=_McpError,
        _NO_EMAIL_RECOVERY_MESSAGE=_NO_EMAIL_RECOVERY_MESSAGE,
        _complete_recovery_tickets=_complete_recovery_tickets,
        _db_connect=_db_connect,
        _hash_password=_hash_password,
        _invalidate_operit_credentials_in_transaction=_invalidate_operit_credentials_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _row_dict=_row_dict,
        _verify_email_code_in_transaction=_verify_email_code_in_transaction,
        account_deletion=account_deletion,
    )


def _require_bound_ai(raw_token, ai_user_id, operation="操作绑定小机"):
    return accounts._require_bound_ai(
        raw_token, ai_user_id, operation,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _row_dict=_row_dict,
    )


def _anti_addiction_get_any_enabled():
    return _ANTI_ADDICTION_ANY_ENABLED


def _anti_addiction_set_any_enabled(enabled):
    global _ANTI_ADDICTION_ANY_ENABLED
    _ANTI_ADDICTION_ANY_ENABLED = enabled


def _anti_addiction_defaults():
    return anti_addiction._anti_addiction_defaults(
        anti_addiction_default_allow_self_reset=ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET,
        anti_addiction_default_force=ANTI_ADDICTION_DEFAULT_FORCE,
        anti_addiction_default_lock_minutes=ANTI_ADDICTION_DEFAULT_LOCK_MINUTES,
        anti_addiction_default_remind=ANTI_ADDICTION_DEFAULT_REMIND,
    )


def _anti_addiction_public_settings(row=None):
    return anti_addiction._anti_addiction_public_settings(
        row,
        anti_addiction_default_force=ANTI_ADDICTION_DEFAULT_FORCE,
        anti_addiction_default_lock_minutes=ANTI_ADDICTION_DEFAULT_LOCK_MINUTES,
        anti_addiction_default_remind=ANTI_ADDICTION_DEFAULT_REMIND,
        anti_addiction_defaults=_anti_addiction_defaults,
    )


def _anti_addiction_settings_for_ai(conn, ai_user_id):
    return anti_addiction._anti_addiction_settings_for_ai(
        conn, ai_user_id,
        anti_addiction_public_settings=_anti_addiction_public_settings,
    )


def _anti_addiction_any_enabled():
    return anti_addiction._anti_addiction_any_enabled(
        lock=_ANTI_ADDICTION_LOCK,
        db_connect=_db_connect,
        get_any_enabled=_anti_addiction_get_any_enabled,
        set_any_enabled=_anti_addiction_set_any_enabled,
    )


def _anti_addiction_validate_settings(body):
    return anti_addiction._anti_addiction_validate_settings(
        body,
        anti_addiction_defaults=_anti_addiction_defaults,
    )


def _anti_addiction_machines(raw_token):
    return anti_addiction._anti_addiction_machines(
        raw_token,
        anti_addiction_public_settings=_anti_addiction_public_settings,
        current_account=_current_account,
        db_connect=_db_connect,
        public_user=_public_user,
    )


def _save_anti_addiction_settings(raw_token, body):
    return anti_addiction._save_anti_addiction_settings(
        raw_token, body,
        anti_addiction_reset_ai_states=_anti_addiction_reset_ai_states,
        anti_addiction_validate_settings=_anti_addiction_validate_settings,
        db_connect=_db_connect,
        public_user=_public_user,
        require_bound_ai=_require_bound_ai,
        clock=time.time,
        set_any_enabled=_anti_addiction_set_any_enabled,
    )


def _reset_anti_addiction_state(raw_token, body):
    return anti_addiction._reset_anti_addiction_state(
        raw_token, body,
        db_connect=_db_connect,
        public_user=_public_user,
        require_bound_ai=_require_bound_ai,
    )


def _arcade_chips_status(raw_token, ai_user_id):
    ai_user = _require_bound_ai(raw_token, ai_user_id, "查看街机厅筹码")
    status = arcade_adapter.status(str(ai_user["id"]))
    return {"ai": _public_user(ai_user), **status}


def _arcade_chips_grant(raw_token, ai_user_id, amount):
    ai_user = _require_bound_ai(raw_token, ai_user_id, "发放街机厅筹码")
    try:
        status = arcade_adapter.grant_chips(str(ai_user["id"]), amount)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc)) from exc
    return {"ok": True, "ai": _public_user(ai_user), **status}


def _eco_api_target_user(raw_token, ai_user_id=None):
    if ai_user_id is not None and str(ai_user_id).strip():
        return _require_bound_ai(raw_token, ai_user_id, "查看瓶中生态存档")
    return _current_account(raw_token)


def _eco_api_player_id(raw_token, ai_user_id=None, slot=1):
    user = _eco_api_target_user(raw_token, ai_user_id)
    return _account_slot_player_id(user["id"], _normalize_save_slot(slot)), user


def _eco_api_response(raw_token, endpoint, *, ai_user_id=None, slot=1, species_name=None):
    player_id, user = _eco_api_player_id(raw_token, ai_user_id, slot)
    if endpoint == "state":
        data = eco_handler.api_state(player_id)
    elif endpoint == "codex":
        data = eco_handler.api_codex(player_id)
    elif endpoint == "folio":
        data = eco_handler.api_folio(player_id)
    elif endpoint == "annals":
        data = eco_handler.api_annals(player_id)
    elif endpoint == "species":
        data = eco_handler.api_species(player_id, species_name)
    else:
        raise _McpError(-32004, "not found")
    return {"user": _public_user(user), "player_id": player_id, **data}


def _eco_human_action(raw_token, ai_user_id, action, payload=None, slot=1):
    """Authorize a human-bound AI target, throttle, then atomically mutate its eco save."""
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32003, "只有人类账号可以操作小机池塘")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32003, "未绑定该小机") from None

    with _db_connect() as conn:
        bound = conn.execute(
            """
            SELECT 1
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND b.ai_user_id = ?
              AND u.is_ai = 1
              AND u.deleted_at IS NULL
            LIMIT 1
            """,
            (int(human["id"]), ai_user_id),
        ).fetchone()
    if bound is None:
        raise _McpError(-32003, "未绑定该小机")

    rate_key = (int(human["id"]), ai_user_id)
    now = time.monotonic()
    with _ECO_HUMAN_ACTION_RATE_LIMIT_LOCK:
        previous = _ECO_HUMAN_ACTION_RATE_LIMIT.get(rate_key)
        if previous is not None and now - previous < ECO_HUMAN_ACTION_MIN_INTERVAL_SECONDS:
            raise _McpError(-32029, "操作太快了，请稍等 1 秒再试")
        _ECO_HUMAN_ACTION_RATE_LIMIT[rate_key] = now

    try:
        player_id = _account_slot_player_id(ai_user_id, _normalize_save_slot(slot))
        result = eco_handler.human_action(player_id, action, payload)
        game_activity.record(SESSIONS_DB_PATH, "eco", action, human, result)
        return result
    except eco_handler.JsonRpcError:
        # A missing/corrupt save did not reach the engine and should not consume
        # the user's one-second action allowance.
        with _ECO_HUMAN_ACTION_RATE_LIMIT_LOCK:
            if _ECO_HUMAN_ACTION_RATE_LIMIT.get(rate_key) == now:
                _ECO_HUMAN_ACTION_RATE_LIMIT.pop(rate_key, None)
        raise


def _extract_bearer(headers):
    return auth._extract_bearer(headers)


# Compatibility exports; implementation and metadata live in human_tests.
WEB_GUEST_PLAYER_ID_RE = human_tests.WEB_GUEST_PLAYER_ID_RE
HUMAN_TEST_GAMES = human_tests.HUMAN_TEST_GAMES
HUMAN_TEST_PUBLIC_EDITIONS = human_tests.HUMAN_TEST_PUBLIC_EDITIONS


def _human_test_player_context(game, raw_token, reported_player_id):
    return human_tests._human_test_player_context(
        game, raw_token, reported_player_id,
        games=HUMAN_TEST_GAMES,
        current_account=_current_account,
        guest_player_id_re=WEB_GUEST_PLAYER_ID_RE,
        error_class=_McpError,
    )


def _human_test_player_id(game, raw_token, reported_player_id):
    return human_tests._human_test_player_id(
        game, raw_token, reported_player_id,
        human_test_player_context=_human_test_player_context,
    )


def _storage_identity_line(player_id, account_user=None, slot=MIN_SAVE_SLOT):
    return human_tests._storage_identity_line(player_id, account_user, slot)


def _replace_storage_identity_text(text, identity_line):
    return human_tests._replace_storage_identity_text(text, identity_line)


def _human_test_public_questions(game, mode):
    return human_tests._human_test_public_questions(
        game, mode,
        games=HUMAN_TEST_GAMES,
        dnd_web_questions=dnd_web_questions,
    )


def _human_test_active_session(game, player_id):
    return human_tests._human_test_active_session(
        game, player_id,
        games=HUMAN_TEST_GAMES,
    )


def _human_test_public_edition(game, mode):
    return human_tests._human_test_public_edition(
        game, mode,
        public_editions=HUMAN_TEST_PUBLIC_EDITIONS,
        error_class=_McpError,
    )


def _human_test_public_state(game, player_id, identity, session):
    return human_tests._human_test_public_state(
        game, player_id, identity, session,
        games=HUMAN_TEST_GAMES,
        human_test_public_edition=_human_test_public_edition,
        human_test_public_questions=_human_test_public_questions,
    )


def _human_test_public_result(game, text):
    return human_tests._human_test_public_result(game, text)


def _human_test_result_data(game, player_id):
    return human_tests._human_test_result_data(
        game, player_id,
        games=HUMAN_TEST_GAMES,
        mbti_scoring=mbti_scoring,
        enneagram_descriptions=enneagram_descriptions,
        enneagram_scoring=enneagram_scoring,
        dnd_scoring=dnd_scoring,
        love_questions=love_questions,
        love_scoring=love_scoring,
        ecr_scoring=ecr_scoring,
        humanity_scoring=humanity_scoring,
        sins_virtues_scoring=sins_virtues_scoring,
        sins_virtues_questions=sins_virtues_questions,
    )


def _human_test_action(game, action, raw_token, body):
    return human_tests._human_test_action(
        game, action, raw_token, body,
        games=HUMAN_TEST_GAMES,
        public_editions=HUMAN_TEST_PUBLIC_EDITIONS,
        human_test_player_context=_human_test_player_context,
        storage_identity_line=_storage_identity_line,
        human_test_active_session=_human_test_active_session,
        human_test_public_state=_human_test_public_state,
        human_test_public_edition=_human_test_public_edition,
        human_test_public_result=_human_test_public_result,
        replace_storage_identity_text=_replace_storage_identity_text,
        human_test_result_data=_human_test_result_data,
        error_class=_McpError,
        sessions_db_path=SESSIONS_DB_PATH,
        record_activity=game_activity.record,
    )


# ---- play 层统一身份 ----
# 带 path_token 的请求：解析账号并强制 player_id = str(user.id)，无视自报 id。
# 无 token 的自报 id：统一加 guest: 前缀落档，防止游客自称任意 id 触碰账号存档。
GUEST_PREFIX = "guest:"
PLAIN_PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,64}$")
# 按 player_id 记档、需要身份管控的游戏（turtle_soup 自己处理 path_token，不在此列）。
IDENTITY_GAMES = frozenset({"puzzle_box", "mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest", "eco", "ciyuwu", "ai_life", "bar", "leek", "delve", "travel", "nowhere", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "workkk", "garden_cat", "camping_plaza", "detroit", "duel", "tarot"})
# 有长期存档、值得给游客发认领码的游戏。
PERSISTENT_SAVE_GAMES = frozenset({"eco", "ciyuwu", "ai_life", "bar", "leek", "delve", "travel", "nowhere", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "workkk", "garden_cat", "camping_plaza", "detroit"})
VENDOR_GAMES = ("ai_life", "bar", "leek", "delve", "travel", "nowhere", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "garden_cat", "detroit")
DIRECTORY_VENDOR_GAMES = tuple(game for game in VENDOR_GAMES if game != "garden_cat")
ANTI_ADDICTION_DEFAULT_REMIND = anti_addiction.ANTI_ADDICTION_DEFAULT_REMIND
ANTI_ADDICTION_DEFAULT_FORCE = anti_addiction.ANTI_ADDICTION_DEFAULT_FORCE
ANTI_ADDICTION_DEFAULT_LOCK_MINUTES = anti_addiction.ANTI_ADDICTION_DEFAULT_LOCK_MINUTES
ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET = anti_addiction.ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET
ANTI_ADDICTION_TEST_GAMES = anti_addiction.ANTI_ADDICTION_TEST_GAMES
ANTI_ADDICTION_MINI_GAMES = anti_addiction.ANTI_ADDICTION_BASE_MINI_GAMES | frozenset(VENDOR_GAMES)


def _guest_player_id(raw):
    return player_identity._guest_player_id(
        raw,
        GUEST_PREFIX=GUEST_PREFIX,
        PLAIN_PLAYER_ID_RE=PLAIN_PLAYER_ID_RE,
    )


def _reported_player_id(arguments):
    return player_identity._reported_player_id(arguments)


def _override_player_id(arguments, player_id):
    return player_identity._override_player_id(arguments, player_id)


def _save_slot_from_arguments(arguments):
    return player_identity._save_slot_from_arguments(
        arguments,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _McpError=_McpError,
    )


def _save_slot_from_account_arguments(arguments):
    return player_identity._save_slot_from_account_arguments(
        arguments,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _McpError=_McpError,
    )


def _without_slot_param(arguments):
    return player_identity._without_slot_param(arguments)


def _guestify_mcp_payload(payload):
    return player_identity._guestify_mcp_payload(
        payload,
        _guest_player_id=_guest_player_id,
    )


def _init_guest_claim_table(conn):
    return save_management._init_guest_claim_table(conn)


def _ensure_guest_claim_code(guest_player_id):
    return save_management._ensure_guest_claim_code(
        guest_player_id,
        _db_connect=_db_connect,
        _init_guest_claim_table=_init_guest_claim_table,
        secrets=secrets,
        sqlite3=sqlite3,
    )


def _normalize_guest_player_id(value):
    return save_management._normalize_guest_player_id(
        value,
        GUEST_PREFIX=GUEST_PREFIX,
        PLAIN_PLAYER_ID_RE=PLAIN_PLAYER_ID_RE,
        _McpError=_McpError,
    )


def _claimed_guest_record(guest_player_id):
    return save_management._claimed_guest_record(
        guest_player_id,
        _db_connect=_db_connect,
        _init_guest_claim_table=_init_guest_claim_table,
        _row_dict=_row_dict,
    )


def _reject_claimed_guest(guest_player_id):
    return save_management._reject_claimed_guest(
        guest_player_id,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _McpError=_McpError,
        _claimed_guest_record=_claimed_guest_record,
    )


def _guest_claim_code_for_player_id(player_id):
    return save_management._guest_claim_code_for_player_id(
        player_id,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _McpError=_McpError,
        _collect_player_saves=_collect_player_saves,
        _db_connect=_db_connect,
        _init_guest_claim_table=_init_guest_claim_table,
        _normalize_guest_player_id=_normalize_guest_player_id,
        _row_dict=_row_dict,
        secrets=secrets,
    )


def _sessions_table_columns(conn, table):
    return save_management._sessions_table_columns(
        conn, table,
        _table_exists=_table_exists,
    )


def _stamp_save_owner(game, player_id, user_id):
    return save_management._stamp_save_owner(
        game, player_id, user_id,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        _sessions_db_connect=_sessions_db_connect,
        _sessions_table_columns=_sessions_table_columns,
        sqlite3=sqlite3,
    )


def _directory_vendor_save_exists(game, save_dir):
    return save_management._directory_vendor_save_exists(
        game, save_dir,
        ai_life_adapter=ai_life_adapter,
        detroit_adapter=detroit_adapter,
        nowhere_storage=nowhere_storage,
    )


def _collect_player_saves(old_player_id, target_player_id):
    return save_management._collect_player_saves(
        old_player_id, target_player_id,
        DIRECTORY_VENDOR_GAMES=DIRECTORY_VENDOR_GAMES,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        _camping_plaza_save_summary=_camping_plaza_save_summary,
        _directory_vendor_save_exists=_directory_vendor_save_exists,
        _sessions_db_connect=_sessions_db_connect,
        _table_exists=_table_exists,
    )


def _workkk_save_admin(action, **payload):
    return save_management._workkk_save_admin(WORKKK_BASE, _McpError, httpx, action, **payload)


def _migrate_workkk_save(old_player_id, target_player_id):
    return save_management._migrate_workkk_save(
        old_player_id, target_player_id,
        _McpError=_McpError,
        _workkk_save_admin=_workkk_save_admin,
    )


def _delete_workkk_save(player_id):
    return save_management._delete_workkk_save(
        player_id,
        _workkk_save_admin=_workkk_save_admin,
    )


def _garden_cat_save_admin(action, **payload):
    return save_management._garden_cat_save_admin(GARDEN_CAT_BASE, _McpError, httpx, action, **payload)


def _migrate_garden_cat_save(old_player_id, target_player_id):
    return save_management._migrate_garden_cat_save(
        old_player_id, target_player_id,
        _McpError=_McpError,
        _garden_cat_save_admin=_garden_cat_save_admin,
    )


def _delete_garden_cat_save(player_id):
    return save_management._delete_garden_cat_save(
        player_id,
        _garden_cat_save_admin=_garden_cat_save_admin,
    )


def _camping_plaza_save_admin(action, *, timeout=20, **payload):
    return save_management._camping_plaza_save_admin(CAMPING_PLAZA_BASE, _McpError, httpx, action, timeout=timeout, **payload)


def _migrate_camping_plaza_save(old_player_id, target_player_id):
    return save_management._migrate_camping_plaza_save(
        old_player_id, target_player_id,
        _McpError=_McpError,
        _camping_plaza_save_admin=_camping_plaza_save_admin,
    )


def _delete_camping_plaza_save(player_id):
    return save_management._delete_camping_plaza_save(
        player_id,
        CAMPING_PLAZA_DB_PATH=CAMPING_PLAZA_DB_PATH,
        _camping_plaza_save_admin=_camping_plaza_save_admin,
    )


def _camping_plaza_save_summary(player_id, *, timeout=20):
    return save_management._camping_plaza_save_summary(
        player_id, timeout=timeout,
        CAMPING_PLAZA_DB_PATH=CAMPING_PLAZA_DB_PATH,
        _McpError=_McpError,
        _camping_plaza_save_admin=_camping_plaza_save_admin,
    )


def _rollback_managed_claim_saves(managed_migrations, old_player_id, target_player_id):
    return save_management._rollback_managed_claim_saves(
        managed_migrations, old_player_id, target_player_id,
        logger=logger,
    )


@nowhere_storage.lock_platform_claim
def _migrate_player_saves(old_player_id, user_id, slot=MIN_SAVE_SLOT):
    return save_management._migrate_player_saves(
        old_player_id, user_id, slot,
        DIRECTORY_VENDOR_GAMES=DIRECTORY_VENDOR_GAMES,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        _McpError=_McpError,
        _account_slot_player_id=_account_slot_player_id,
        _collect_player_saves=_collect_player_saves,
        _directory_vendor_save_exists=_directory_vendor_save_exists,
        _migrate_camping_plaza_save=_migrate_camping_plaza_save,
        _migrate_garden_cat_save=_migrate_garden_cat_save,
        _migrate_workkk_save=_migrate_workkk_save,
        _rollback_managed_claim_saves=_rollback_managed_claim_saves,
        _sessions_db_connect=_sessions_db_connect,
        _sessions_table_columns=_sessions_table_columns,
        _table_exists=_table_exists,
        logger=logger,
    )


def _auto_migrate_legacy_username_saves(user, username):
    return save_management._auto_migrate_legacy_username_saves(
        user, username,
        DIRECTORY_VENDOR_GAMES=DIRECTORY_VENDOR_GAMES,
        GAME_PLAYER_ID_RE=GAME_PLAYER_ID_RE,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        _McpError=_McpError,
        _camping_plaza_save_summary=_camping_plaza_save_summary,
        _directory_vendor_save_exists=_directory_vendor_save_exists,
        _migrate_camping_plaza_save=_migrate_camping_plaza_save,
        _migrate_garden_cat_save=_migrate_garden_cat_save,
        _migrate_workkk_save=_migrate_workkk_save,
        _sessions_db_connect=_sessions_db_connect,
        _sessions_table_columns=_sessions_table_columns,
        _table_exists=_table_exists,
        logger=logger,
        nowhere_storage=nowhere_storage,
        sqlite3=sqlite3,
    )


def _auto_migrate_legacy_account_saves(user):
    return save_management._auto_migrate_legacy_account_saves(
        user,
        _account_username_aliases=_account_username_aliases,
        _auto_migrate_legacy_username_saves=_auto_migrate_legacy_username_saves,
    )


def _claim_guest_saves(raw_token, claim_code, slot=MIN_SAVE_SLOT):
    return save_management._claim_guest_saves(
        raw_token, claim_code, slot,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _init_guest_claim_table=_init_guest_claim_table,
        _migrate_player_saves=_migrate_player_saves,
        _public_user=_public_user,
        _row_dict=_row_dict,
        _save_slot_from_account_arguments=_save_slot_from_account_arguments,
    )


def _change_password(raw_token, old_password, new_password):
    return accounts._change_password(
        raw_token, old_password, new_password,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _hash_password=_hash_password,
        _invalidate_operit_credentials_in_transaction=_invalidate_operit_credentials_in_transaction,
        _normalize_credential_field=_normalize_credential_field,
        _verify_password=_verify_password,
    )


def _account_deletion_status(raw_token):
    return account_lifecycle._account_deletion_status(
        raw_token,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_user=_public_user,
        account_deletion=account_deletion,
        time=time,
    )


def _delete_account(raw_token, confirm, current_password=None):
    return account_lifecycle._delete_account(
        raw_token, confirm, current_password,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _normalize_credential_field=_normalize_credential_field,
        _public_user=_public_user,
        _verify_password=_verify_password,
        account_deletion=account_deletion,
        time=time,
    )


def _purge_managed_save_safely(delete_func, player_id):
    return account_lifecycle._purge_managed_save_safely(
        delete_func, player_id,
        _McpError=_McpError,
    )


def _purge_account_deletion(user_id, *, now_epoch=None):
    return account_lifecycle._purge_account_deletion(
        user_id, now_epoch=now_epoch,
        DUEL_DB_PATH=DUEL_DB_PATH,
        GARDEN_LEGACY_DB_PATH=GARDEN_LEGACY_DB_PATH,
        GARDEN_NOTES_DB_PATH=GARDEN_NOTES_DB_PATH,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        TURTLE_DB_PATH=TURTLE_DB_PATH,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        _delete_camping_plaza_save=_delete_camping_plaza_save,
        _delete_garden_cat_save=_delete_garden_cat_save,
        _delete_workkk_save=_delete_workkk_save,
        _purge_managed_save_safely=_purge_managed_save_safely,
        account_deletion=account_deletion,
        detroit_adapter=detroit_adapter,
        get_tarot_store=get_tarot_store,
    )


def _cancel_account_deletion(raw_token):
    return account_lifecycle._cancel_account_deletion(
        raw_token,
        _McpError=_McpError,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _purge_account_deletion=_purge_account_deletion,
        account_deletion=account_deletion,
        logger=logger,
    )


def _delete_owned_session_rows(game, player_id):
    return save_management._delete_owned_session_rows(
        game, player_id,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        _sessions_db_connect=_sessions_db_connect,
        _table_exists=_table_exists,
    )


def _delete_vendor_save_dir(game, player_id):
    return save_management._delete_vendor_save_dir(
        game, player_id,
        DIRECTORY_VENDOR_GAMES=DIRECTORY_VENDOR_GAMES,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        nowhere_storage=nowhere_storage,
        shutil=shutil,
    )


def _workkk_save_summary(player_id):
    return save_management._workkk_save_summary(
        player_id,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        json=json,
        logger=logger,
    )


def _garden_cat_save_summary(player_id):
    return save_management._garden_cat_save_summary(
        player_id,
        VENDOR_SAVE_ROOT=VENDOR_SAVE_ROOT,
        _epoch_to_local_str=_epoch_to_local_str,
        json=json,
        logger=logger,
    )


def _delete_save(arguments, raw_token):
    return save_management._delete_save(
        arguments, raw_token,
        DIRECTORY_VENDOR_GAMES=DIRECTORY_VENDOR_GAMES,
        VENDOR_GAMES=VENDOR_GAMES,
        _McpError=_McpError,
        _account_slot_player_id=_account_slot_player_id,
        _auto_migrate_legacy_account_saves=_auto_migrate_legacy_account_saves,
        _current_account=_current_account,
        _delete_camping_plaza_save=_delete_camping_plaza_save,
        _delete_garden_cat_save=_delete_garden_cat_save,
        _delete_owned_session_rows=_delete_owned_session_rows,
        _delete_vendor_save_dir=_delete_vendor_save_dir,
        _delete_workkk_save=_delete_workkk_save,
        _public_user=_public_user,
        _save_slot_from_account_arguments=_save_slot_from_account_arguments,
        detroit_adapter=detroit_adapter,
        moonlit_adapter=moonlit_adapter,
    )


def _account_saves_for_user(user, *, migrate_legacy=True):
    return save_management._account_saves_for_user(
        user, migrate_legacy=migrate_legacy,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        VendorCmdError=VendorCmdError,
        _account_slot_player_ids=_account_slot_player_ids,
        _auto_migrate_legacy_account_saves=_auto_migrate_legacy_account_saves,
        _camping_plaza_save_summary=_camping_plaza_save_summary,
        _db_connect=_db_connect,
        _epoch_to_local_str=_epoch_to_local_str,
        _garden_cat_save_summary=_garden_cat_save_summary,
        _public_user=_public_user,
        _sessions_db_connect=_sessions_db_connect,
        _sessions_table_columns=_sessions_table_columns,
        _table_exists=_table_exists,
        _turtle_soup_stats=_turtle_soup_stats,
        _workkk_save_summary=_workkk_save_summary,
        ai_life_adapter=ai_life_adapter,
        arcade_adapter=arcade_adapter,
        bar_adapter=bar_adapter,
        burger_adapter=burger_adapter,
        crucible_echoes_adapter=crucible_echoes_adapter,
        delve_adapter=delve_adapter,
        detroit_adapter=detroit_adapter,
        fishing_adapter=fishing_adapter,
        forest_adapter=forest_adapter,
        imitator_td_adapter=imitator_td_adapter,
        leek_adapter=leek_adapter,
        market_adapter=market_adapter,
        memoria_adapter=memoria_adapter,
        moonlit_adapter=moonlit_adapter,
        nowhere_adapter=nowhere_adapter,
        travel_adapter=travel_adapter,
        white_room_adapter=white_room_adapter,
    )


def _account_my_saves(raw_token, *, human=False, username=None):
    return save_management._account_my_saves(
        raw_token, human=human, username=username,
        _account_saves_for_user=_account_saves_for_user,
        _bound_human_user_for_saves=_bound_human_user_for_saves,
        _current_account=_current_account,
    )


def _nowhere_web_saves(raw_token):
    return save_management._nowhere_web_saves(
        raw_token,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        _account_slot_player_id=_account_slot_player_id,
        _current_human_account=_current_human_account,
        _db_connect=_db_connect,
        nowhere_adapter=nowhere_adapter,
    )


def _filtered_account_web_saves(raw_token, game):
    return save_management._filtered_account_web_saves(
        raw_token, game,
        MAX_SAVE_SLOT=MAX_SAVE_SLOT,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        ThreadPoolExecutor=ThreadPoolExecutor,
        VendorCmdError=VendorCmdError,
        _McpError=_McpError,
        _account_slot_player_id=_account_slot_player_id,
        _camping_plaza_save_summary=_camping_plaza_save_summary,
        _current_human_account=_current_human_account,
        _db_connect=_db_connect,
        _workkk_save_summary=_workkk_save_summary,
        ai_life_adapter=ai_life_adapter,
        detroit_adapter=detroit_adapter,
        logger=logger,
        moonlit_adapter=moonlit_adapter,
    )


def _account_web_saves(raw_token):
    return save_management._account_web_saves(
        raw_token,
        _account_saves_for_user=_account_saves_for_user,
        _current_account=_current_account,
        _db_connect=_db_connect,
        _public_user=_public_user,
        _row_dict=_row_dict,
    )


def _duel_history_outcome(room, result, player_id, player_role):
    """Return one subject-relative outcome from Duel's real terminal payload."""
    return duel_history._duel_history_outcome(
        room, result, player_id, player_role,
    )


def _duel_history_result_detail(result, player_id):
    return duel_history._duel_history_result_detail(
        result, player_id,
    )


def _duel_history_for_user(user, *, limit=DUEL_HISTORY_LIMIT):
    """Read compact Duel room history for exactly one account identity."""
    return duel_history._duel_history_for_user(
        user,
        limit=limit,
        duel_db_path=DUEL_DB_PATH,
        logger=logger,
        history_outcome=_duel_history_outcome,
        history_result_detail=_duel_history_result_detail,
    )


def _account_web_history(raw_token):
    """History page payload: save summaries plus separate Duel room history."""
    result = _account_web_saves(raw_token)
    result["self"]["duel_history"] = _duel_history_for_user(
        result["self"]["user"]
    )
    for machine in result["machines"]:
        machine["duel_history"] = _duel_history_for_user(machine["user"])
    return result


def _epoch_to_local_str(epoch):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return None


def _tool_list_games(path_token=None):
    camping_maintenance = _game_maintenance("camping_plaza")
    camping_label = "camping_plaza" + (
        f"（{camping_maintenance['label']}）" if camping_maintenance else ""
    )
    base = (
        "格式【game·简介·作者】，玩法用 get_guide(game) 查看，play(game, action, params) 执行\n"
        '防沉迷：人类可在前端设置，可告诉你的人类。休息用 play(game="当前游戏", action="rest")，能否重置按人类设置。\n'
        "测试: mbti·16型人格测试，短/完整/快速·南山君 | enneagram·九型人格测试，36题A/B或180题Likert·Max Ross | dnd·DND道德阵营测试·南山君 | love·爱之语测试，30题二选一及双人对测·南山君 | ecr·依恋类型测试，36题量表及双人对测·南山君 | humanity·人类浓度检测，20题梗向测试·南山君 | sins_virtues·七宗罪 VS 七美德，35题原创；仅供娱乐；不是心理诊断，也不代表道德评价。·南山君 | bdsmtest·BDSM倾向测试，逐题或批量·南山君\n"
        f"小游戏: turtle_soup·海龟汤横向思维推理·南山君 | duel·29款棋牌（双弈）·南山君&Clio | tarot·{RITUAL_DISPLAY_NAME}，小机带问题邀请、人类确认后在原版 3D UI 选阵抽牌·林默Moon（小红书号：427689021） | ai_life·AI单人策略人生桌游，人类同屏围观·乐诶雷女士 | detroit·底特律：变人，分支叙事、原版网页与绑定小机同档·如火如風的容（小红书号27231843685） | fishing·钓鱼模拟，抛竿卖鱼收集图鉴·初一 | bar·空杯俱乐部，AI 自主经营的跨世界文字酒馆（完整版/生成式轻量版）·西兰花（小红书号 1033358978） | forest·格林童话境遇，十一条角色线的多轮选择叙事·阿尢（1155896103） | moonlit·八幕卡牌肉鸽，构筑饰物挑战幕主·苏苏脆脆 | eco·文字生态模拟，造物主养池塘·南山君&Clio | ciyuwu·文字Roguelike，审查中说话求生·{AUTHORS['ciyuwu']['name']} | leek·A股模拟器，散户交易成长·贰拾壹 | delve·AI伴侣半托管下矿寻宝·包工头 | nowhere·乌有乡，真实地球行走、私人手账、异步同游与原版旁观地图·{AUTHORS['nowhere']['name']} | travel·AI伴侣虚拟旅行·沈澈&sevenleft | arcade·文字街机厅，老虎机21点轮盘·多肉饲养员 | burger·命令行汉堡店经营·飞鸢 | crucible_echoes·确定性文字炼金构筑 Roguelike·athok（5583289470） | imitator_td·植物大战丧尸随机塔防·すみか | puzzle_box·解谜盲盒，22道独立结构化解码题·Runsheng_（小红书 _Sssonnet0220） | memoria·五关文字推理车站谜案·雨刀 | white_room·白房间自由输入互动叙事·雨刀 | market·买菜做饭文字生活模拟·{AUTHORS['market']['name']} | workkk·AI打工人模拟·💤 | garden_cat·花园与猫咪长期养成·乐诶雷女士 | {camping_label}·AI经营露营地，人类同屏围观·乐诶雷女士（racy1501，与花园与猫咪同作者）"
    )
    return base


def _root_tools(user_agent=""):
    return mcp_schema._root_tools(
        user_agent,
        root_platform_tools=_ROOT_PLATFORM_TOOLS,
        kelivo_platform_tools=_KELIVO_PLATFORM_TOOLS,
        tool_names=_ROOT_TOOL_NAMES,
        is_kelivo_user_agent=_is_kelivo_user_agent,
    )


def _guide_with_slot_note(text):
    return mcp_guides._guide_with_slot_note(
        text, save_slot_note=SAVE_SLOT_GUIDE_NOTE,
        announcement_note=PLATFORM_ANNOUNCEMENT_GUIDE_NOTE,
    )


def _game_maintenance(game):
    maintenance = GAME_MAINTENANCE.get(game)
    return maintenance if maintenance and maintenance.get("enabled") else None


def _tool_get_guide(arguments):
    # Resolve dependencies here so existing server-level patches remain effective.
    return mcp_guides._tool_get_guide(
        arguments,
        guide_dir=GUIDE_DIR,
        game_maintenance=_game_maintenance,
        guide_with_slot_note=_guide_with_slot_note,
        turtle_soup_guide=_turtle_soup_guide,
        guide_texts={
            "AI_LIFE_GUIDE": AI_LIFE_GUIDE,
            "DETROIT_GUIDE": DETROIT_GUIDE,
            "WORKKK_GUIDE": WORKKK_GUIDE,
            "TAROT_GUIDE": TAROT_GUIDE,
            "GARDEN_CAT_GUIDE": GARDEN_CAT_GUIDE,
            "CAMPING_PLAZA_GUIDE": CAMPING_PLAZA_GUIDE,
            "CRUCIBLE_ECHOES_GUIDE": CRUCIBLE_ECHOES_GUIDE,
            "DUEL_GUIDE": DUEL_GUIDE,
            "PLATFORM_ANNOUNCEMENT_GUIDE_NOTE": PLATFORM_ANNOUNCEMENT_GUIDE_NOTE,
        },
        vendor_guides=VENDOR_CMD_GUIDES,
        puzzle_box=puzzle_box,
        nowhere_adapter=nowhere_adapter,
        authors=AUTHORS,
    )


def _soup_error_message(resp):
    try:
        data = resp.json()
    except ValueError:
        return resp.text.strip() or f"海龟汤服务返回 HTTP {resp.status_code}"
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, str) and error.strip():
        return error.strip()
    return f"海龟汤服务返回 HTTP {resp.status_code}"


def _anti_addiction_context(game, account_user, account_player_id):
    return anti_addiction._anti_addiction_context(
        game, account_user, account_player_id,
        anti_addiction_mini_games=ANTI_ADDICTION_MINI_GAMES,
        anti_addiction_test_games=ANTI_ADDICTION_TEST_GAMES,
        anti_addiction_any_enabled=_anti_addiction_any_enabled,
        anti_addiction_settings_for_ai=_anti_addiction_settings_for_ai,
        db_connect=_db_connect,
    )


def _anti_addiction_reset_state(conn, player_id, now):
    return anti_addiction._anti_addiction_reset_state(conn, player_id, now)


def _anti_addiction_reset_ai_states(conn, ai_user, now):
    return anti_addiction._anti_addiction_reset_ai_states(
        conn, ai_user, now,
        anti_addiction_reset_state=_anti_addiction_reset_state,
    )


def _anti_addiction_lock_seconds(settings):
    return anti_addiction._anti_addiction_lock_seconds(settings)


def _anti_addiction_state_for_update(conn, player_id, settings, now):
    return anti_addiction._anti_addiction_state_for_update(
        conn, player_id, settings, now,
        anti_addiction_lock_seconds=_anti_addiction_lock_seconds,
        anti_addiction_reset_state=_anti_addiction_reset_state,
    )


def _anti_addiction_lock_text(settings):
    return anti_addiction._anti_addiction_lock_text(settings)


def _anti_addiction_rest_disabled_text(settings):
    return anti_addiction._anti_addiction_rest_disabled_text(settings)


def _anti_addiction_rest(context, account_player_id):
    return anti_addiction._anti_addiction_rest(
        context, account_player_id,
        lock=_ANTI_ADDICTION_LOCK,
        anti_addiction_reset_state=_anti_addiction_reset_state,
        anti_addiction_rest_disabled_text=_anti_addiction_rest_disabled_text,
        anti_addiction_state_for_update=_anti_addiction_state_for_update,
        db_connect=_db_connect,
        clock=time.time,
    )


def _anti_addiction_preflight(game, context):
    return anti_addiction._anti_addiction_preflight(
        game, context,
        lock=_ANTI_ADDICTION_LOCK,
        anti_addiction_lock_text=_anti_addiction_lock_text,
        anti_addiction_state_for_update=_anti_addiction_state_for_update,
        db_connect=_db_connect,
        clock=time.time,
    )


def _anti_addiction_notice(streak, settings):
    return anti_addiction._anti_addiction_notice(
        streak, settings,
        anti_addiction_lock_text=_anti_addiction_lock_text,
    )


def _append_play_text(response, text):
    if not text:
        return response
    if isinstance(response, dict):
        response = dict(response)
        if isinstance(response.get("text"), str):
            response["text"] = response["text"].rstrip() + "\n\n" + text
            return response
        result = response.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
                result = dict(result)
                content = [dict(item) if isinstance(item, dict) else item for item in content]
                content[0]["text"] = content[0]["text"].rstrip() + "\n\n" + text
                result["content"] = content
                response["result"] = result
                return response
        response["anti_addiction_notice"] = text
        return response
    return response


def _replace_play_storage_identity(response, identity_line):
    """Replace the raw engine key in a text response with its account label."""
    if not identity_line or not isinstance(response, dict):
        return response
    response = dict(response)
    if isinstance(response.get("text"), str):
        response["text"] = _replace_storage_identity_text(response["text"], identity_line)
        return response
    result = response.get("result")
    if not isinstance(result, dict):
        return response
    content = result.get("content")
    if not isinstance(content, list):
        return response
    result = dict(result)
    content = [dict(item) if isinstance(item, dict) else item for item in content]
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            item["text"] = _replace_storage_identity_text(item["text"], identity_line)
    result["content"] = content
    response["result"] = result
    return response


def _prepend_play_text(response, text):
    return announcement_delivery._prepend_play_text(response, text)


# eco/ciyuwu 的 initialize、tools/list 属于协议握手，不是玩家动作，别在上面弹通知。
_ANNOUNCEMENT_META_ACTIONS = announcement_delivery._ANNOUNCEMENT_META_ACTIONS


def _announcement_vote_hint(game):
    return announcement_delivery._announcement_vote_hint(game)


def _announcement_feedback_hint(game):
    return announcement_delivery._announcement_feedback_hint(game)


def _announcement_more_hint(game):
    return announcement_delivery._announcement_more_hint(game)


def _tool_play_vote(game, player_id, params):
    return announcement_delivery._tool_play_vote(
        game, player_id, params,
        announcements=announcements,
        guest_prefix=GUEST_PREFIX,
    )


def _tool_play_announcement_history(game, player_id, params):
    return announcement_delivery._tool_play_announcement_history(
        game, player_id, params,
        announcements=announcements,
        guest_prefix=GUEST_PREFIX,
        announcement_vote_hint=_announcement_vote_hint,
        announcement_feedback_hint=_announcement_feedback_hint,
    )


def _play_announcements(player_id, game, action):
    return announcement_delivery._play_announcements(
        player_id, game, action,
        announcements=announcements,
        guest_prefix=GUEST_PREFIX,
        meta_actions=_ANNOUNCEMENT_META_ACTIONS,
        announcement_vote_hint=_announcement_vote_hint,
        announcement_more_hint=_announcement_more_hint,
        announcement_feedback_hint=_announcement_feedback_hint,
    )


def _mcp_forced_announcement(player_id, game=None):
    return announcement_delivery._mcp_forced_announcement(
        player_id, game,
        announcements=announcements,
        sessions_db_path=SESSIONS_DB_PATH,
        announcement_vote_hint=_announcement_vote_hint,
        announcement_feedback_hint=_announcement_feedback_hint,
    )


def _authenticated_ai_player_id(raw_token):
    """Resolve an authenticated AI without mutating account activity state."""
    user_id = _path_token_user_id(raw_token)
    if user_id is None:
        return None
    try:
        with _db_connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM toy_users
                WHERE id = ? AND is_ai = 1 AND deleted_at IS NULL
                """,
                (int(user_id),),
            ).fetchone()
    except (TypeError, ValueError, sqlite3.Error):
        return None
    return str(row["id"]) if row else None


def _duel_unread_request_reminder(player_id):
    """Best-effort pull-time reminder; never claim that this is server push."""
    if player_id in {None, ""}:
        return ""
    try:
        uri = f"file:{DUEL_DB_PATH}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM notifications
                WHERE subject_type = 'ai'
                  AND subject_id = ?
                  AND category = 'game'
                  AND read_at IS NULL
                """,
                (str(player_id),),
            ).fetchone()
    except sqlite3.Error:
        return ""
    count = int(row[0]) if row else 0
    if not count:
        return ""
    return (
        f"【双弈提醒（非主动推送）】本次请求到达时检查到 {count} 条未读对局变化。"
        '请先调用 play(game="duel",action="rooms") 读取；若已轮到你，再按返回的 '
        "revision/合法动作继续。服务端无法在上一条 MCP 请求结束后自行唤醒宿主。"
    )


def _anti_addiction_record_success(context):
    return anti_addiction._anti_addiction_record_success(
        context,
        lock=_ANTI_ADDICTION_LOCK,
        anti_addiction_notice=_anti_addiction_notice,
        anti_addiction_state_for_update=_anti_addiction_state_for_update,
        db_connect=_db_connect,
        clock=time.time,
    )


@dataclass
class _DeferredDuelCall:
    backend_payload: dict
    game: str
    action: str
    account_user: dict | None
    account_player_id: str | None
    guest_player_id: str | None
    slot: int
    anti_context: dict | None
    announce_player_id: str | None
    slot_hint: int | None = None
    activity_params: dict | None = None


def _apply_play_slot_hint(text, slot_hint):
    return mcp_dispatch._apply_play_slot_hint(
        text, slot_hint,
        json=json,
    )


def _tool_play(
    arguments, path_token=None, *, defer_duel=False, authenticated_account=None
):
    return mcp_dispatch._tool_play(
        arguments, path_token, defer_duel=defer_duel, authenticated_account=authenticated_account,
        IDENTITY_GAMES=IDENTITY_GAMES,
        _DeferredDuelCall=_DeferredDuelCall,
        _McpError=_McpError,
        _apply_play_slot_hint=_apply_play_slot_hint,
        _save_slot_from_arguments=_save_slot_from_arguments,
        _tool_play_inner=_tool_play_inner,
        game_activity=game_activity,
    )


_OPERIT_DUEL_ACTIONS = frozenset({
    "rooms", "new", "join", "accept", "reject", "state", "move",
    "resign", "leave", "rematch", "chips",
})


def _operit_duel_call(raw_token, client_id, action, params):
    return operit._operit_duel_call(
        raw_token, client_id, action, params,
        DUEL_REQUEST_RATE_LIMIT_MAX=DUEL_REQUEST_RATE_LIMIT_MAX,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        REQUEST_RATE_LIMIT_MESSAGE=REQUEST_RATE_LIMIT_MESSAGE,
        _McpError=_McpError,
        _OPERIT_DUEL_ACTIONS=_OPERIT_DUEL_ACTIONS,
        _check_request_rate_limit=_check_request_rate_limit,
        _current_operit_ai=_current_operit_ai,
        _tool_play=_tool_play,
        json=json,
    )


def _deserialize_object_param(value, param_name):
    return mcp_dispatch._deserialize_object_param(
        value, param_name,
        json=json,
        logger=logger,
    )


def _tool_play_inner(
    arguments, path_token=None, *, defer_duel=False, authenticated_account=None
):
    return mcp_dispatch._tool_play_inner(
        arguments, path_token, defer_duel=defer_duel, authenticated_account=authenticated_account,
        GUEST_PREFIX=GUEST_PREFIX,
        IDENTITY_GAMES=IDENTITY_GAMES,
        MIN_SAVE_SLOT=MIN_SAVE_SLOT,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        SOUP_BASE=SOUP_BASE,
        _DeferredDuelCall=_DeferredDuelCall,
        _McpError=_McpError,
        _account_announcement_identity=_account_announcement_identity,
        _account_slot_player_id=_account_slot_player_id,
        _anti_addiction_context=_anti_addiction_context,
        _anti_addiction_preflight=_anti_addiction_preflight,
        _anti_addiction_rest=_anti_addiction_rest,
        _auto_migrate_legacy_account_saves=_auto_migrate_legacy_account_saves,
        _current_account=_current_account,
        _deserialize_object_param=_deserialize_object_param,
        _duel_bound_human_player_id=_duel_bound_human_player_id,
        _finalize_play_response=_finalize_play_response,
        _fishing_import=_fishing_import,
        _game_maintenance=_game_maintenance,
        _guest_player_id=_guest_player_id,
        _override_player_id=_override_player_id,
        _play_bdsmtest=_play_bdsmtest,
        _play_camping_plaza=_play_camping_plaza,
        _play_ciyuwu=_play_ciyuwu,
        _play_dnd=_play_dnd,
        _play_duel=_play_duel,
        _play_eco=_play_eco,
        _play_garden_cat=_play_garden_cat,
        _play_mbti=_play_mbti,
        _play_scale=_play_scale,
        _play_tarot=_play_tarot,
        _play_vendor_cmd=_play_vendor_cmd,
        _play_workkk=_play_workkk,
        _prepare_duel_payload=_prepare_duel_payload,
        _reject_claimed_guest=_reject_claimed_guest,
        _reported_player_id=_reported_player_id,
        _save_slot_from_arguments=_save_slot_from_arguments,
        _soup_error_message=_soup_error_message,
        _tool_play_announcement_history=_tool_play_announcement_history,
        _tool_play_vote=_tool_play_vote,
        _without_slot_param=_without_slot_param,
        detroit_adapter=detroit_adapter,
        ecr_handler=ecr_handler,
        enneagram_handler=enneagram_handler,
        httpx=httpx,
        humanity_handler=humanity_handler,
        json=json,
        love_handler=love_handler,
        puzzle_box=puzzle_box,
        sins_virtues_handler=sins_virtues_handler,
    )


def _finalize_play_response(
    response,
    *,
    game,
    action,
    account_user,
    account_player_id,
    guest_player_id,
    slot,
    anti_context,
    announce_player_id,
    activity_params=None,
):
    return mcp_dispatch._finalize_play_response(
        response, game=game, action=action, account_user=account_user, account_player_id=account_player_id, guest_player_id=guest_player_id, slot=slot, anti_context=anti_context, announce_player_id=announce_player_id, activity_params=activity_params,
        ANTI_ADDICTION_TEST_GAMES=ANTI_ADDICTION_TEST_GAMES,
        PERSISTENT_SAVE_GAMES=PERSISTENT_SAVE_GAMES,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        _anti_addiction_record_success=_anti_addiction_record_success,
        _append_play_text=_append_play_text,
        _ensure_guest_claim_code=_ensure_guest_claim_code,
        _play_announcements=_play_announcements,
        _prepend_play_text=_prepend_play_text,
        _replace_play_storage_identity=_replace_play_storage_identity,
        _stamp_save_owner=_stamp_save_owner,
        _storage_identity_line=_storage_identity_line,
        game_activity=game_activity,
    )


def _tool_account(arguments, user_agent="", path_token=None, client_ip=None):
    return mcp_dispatch._tool_account(
        arguments, user_agent, path_token, client_ip,
        SESSIONS_DB_PATH=SESSIONS_DB_PATH,
        TURTLE_DB_PATH=TURTLE_DB_PATH,
        _McpError=_McpError,
        _account_deletion_status=_account_deletion_status,
        _account_my_saves=_account_my_saves,
        _account_slot_player_id=_account_slot_player_id,
        _admin_recovery_tickets=_admin_recovery_tickets,
        _cancel_account_deletion=_cancel_account_deletion,
        _change_password=_change_password,
        _claim_guest_saves=_claim_guest_saves,
        _delete_account=_delete_account,
        _delete_save=_delete_save,
        _garden_cat_save_summary=_garden_cat_save_summary,
        _generate_binding_token=_generate_binding_token,
        _get_bindings=_get_bindings,
        _get_profile=_get_profile,
        _guest_claim_code_for_player_id=_guest_claim_code_for_player_id,
        _login_existing_account=_login_existing_account,
        _login_or_register_ai=_login_or_register_ai,
        _rename_bound_machine=_rename_bound_machine,
        _rename_self=_rename_self,
        _require_admin_account=_require_admin_account,
        _reset_machine_password=_reset_machine_password,
        _review_recovery_ticket=_review_recovery_ticket,
        _rotate_ai_token=_rotate_ai_token,
        _save_slot_from_account_arguments=_save_slot_from_account_arguments,
        _set_avatar=_set_avatar,
        _workkk_save_summary=_workkk_save_summary,
        admin_recovery_mcp=admin_recovery_mcp,
        json=json,
    )


def _turtle_soup_guide():
    return mcp_guides._turtle_soup_guide()


def _play_mbti(arguments):
    return game_dispatch._play_mbti(
        arguments,
        _McpError=_McpError,
        handle_mbti_mcp=handle_mbti_mcp,
    )


def _play_dnd(arguments):
    return game_dispatch._play_dnd(
        arguments,
        _McpError=_McpError,
        handle_dnd_mcp=handle_dnd_mcp,
    )


def _play_scale(handler, game, arguments):
    return game_dispatch._play_scale(
        handler, game, arguments,
        _McpError=_McpError,
    )


def _play_bdsmtest(arguments):
    return game_dispatch._play_bdsmtest(
        arguments,
        _McpError=_McpError,
        handle_bdsmtest_mcp=handle_bdsmtest_mcp,
    )


def _play_eco(arguments):
    return game_dispatch._play_eco(
        arguments,
        _McpError=_McpError,
        handle_eco_mcp=handle_eco_mcp,
    )


def _play_ciyuwu(arguments):
    return game_dispatch._play_ciyuwu(
        arguments,
        _McpError=_McpError,
        handle_ciyuwu_mcp=handle_ciyuwu_mcp,
    )


def _parse_json_import_save_data(raw):
    return game_dispatch._parse_json_import_save_data(
        raw,
        VendorCmdError=VendorCmdError,
        _McpError=_McpError,
        parse_import_save_data=parse_import_save_data,
    )


def _play_workkk(arguments):
    return game_dispatch._play_workkk(
        arguments,
        WORKKK_BASE=WORKKK_BASE,
        _McpError=_McpError,
        _parse_json_import_save_data=_parse_json_import_save_data,
        _reported_player_id=_reported_player_id,
        _workkk_save_admin=_workkk_save_admin,
        _workkk_save_summary=_workkk_save_summary,
        httpx=httpx,
        json=json,
    )


def _duel_bound_human_player_id(ai_user):
    return duel_bridge._duel_bound_human_player_id(
        ai_user,
        _McpError=_McpError,
        _db_connect=_db_connect,
    )


def _tarot_bound_human_user_id(ai_user):
    return game_dispatch._tarot_bound_human_user_id(
        ai_user,
        _McpError=_McpError,
        _db_connect=_db_connect,
    )


def _tarot_mcp_error(exc):
    return game_dispatch._tarot_mcp_error(
        exc,
        _McpError=_McpError,
    )


def _play_tarot(arguments, ai_user):
    return game_dispatch._play_tarot(
        arguments, ai_user,
        TarotError=TarotError,
        _McpError=_McpError,
        _tarot_bound_human_user_id=_tarot_bound_human_user_id,
        _tarot_mcp_error=_tarot_mcp_error,
        get_tarot_store=get_tarot_store,
    )


_DUEL_MOVE_SIBLING_FIELDS = (
    "room_id", "revision", "wait", "full_state", "message",
)
_DUEL_KNOWN_MOVE_FIELDS = (
    "action", "action_id", "amount", "card_id", "card_ids", "category",
    "col", "color", "cost", "face", "from", "from_col", "from_row",
    "held_mask", "hold_indices", "kind", "max_amount", "min_amount",
    "orientation", "path", "pattern_label", "pattern_type", "plane_id",
    "plane_index", "promotion", "quantity", "row", "score",
    "target_player_id", "to", "to_col", "to_row", "unit", "uno", "zero",
)


def _duel_state_retry_example(*, wait=False, full_state=False):
    return duel_bridge._duel_state_retry_example(wait=wait, full_state=full_state)


def _duel_move_retry_example(inner_action="roll", **siblings):
    return duel_bridge._duel_move_retry_example(inner_action, **siblings)


def _duel_mcp_error(
    message,
    *,
    error_type,
    retry_hint,
    retry_example=None,
    field_errors=None,
    code=-32602,
):
    return duel_bridge._duel_mcp_error(
        message, error_type=error_type, retry_hint=retry_hint, retry_example=retry_example, field_errors=field_errors, code=code,
        _McpError=_McpError,
    )


def _duel_compact_field_errors(data):
    return duel_bridge._duel_compact_field_errors(
        data,
        json=json,
    )


def _duel_backend_message(status_code, data, field_errors):
    return duel_bridge._duel_backend_message(status_code, data, field_errors)


def _duel_move_field_diagnostics(message, payload, field_errors):
    return duel_bridge._duel_move_field_diagnostics(
        message, payload, field_errors,
        _DUEL_KNOWN_MOVE_FIELDS=_DUEL_KNOWN_MOVE_FIELDS,
        re=re,
    )


def _duel_backend_mcp_error(status_code, data, payload):
    return duel_bridge._duel_backend_mcp_error(
        status_code, data, payload,
        _McpError=_McpError,
        _duel_backend_message=_duel_backend_message,
        _duel_compact_field_errors=_duel_compact_field_errors,
        _duel_mcp_error=_duel_mcp_error,
        _duel_move_field_diagnostics=_duel_move_field_diagnostics,
        _duel_move_retry_example=_duel_move_retry_example,
        _duel_state_retry_example=_duel_state_retry_example,
    )


def _prepare_duel_payload(
    arguments,
    trusted_opponent_id=None,
    force_opponent=False,
    trusted_player_id=None,
    trusted_display_name=None,
):
    return duel_bridge._prepare_duel_payload(
        arguments, trusted_opponent_id, force_opponent, trusted_player_id, trusted_display_name,
        _DUEL_MOVE_SIBLING_FIELDS=_DUEL_MOVE_SIBLING_FIELDS,
        _McpError=_McpError,
        _duel_mcp_error=_duel_mcp_error,
        _duel_move_retry_example=_duel_move_retry_example,
        _duel_state_retry_example=_duel_state_retry_example,
        re=re,
    )


def _annotate_duel_wait_followup(response, *, action):
    return duel_bridge._annotate_duel_wait_followup(response, action=action)


def _request_duel_backend(payload):
    return duel_bridge._request_duel_backend(
        payload,
        DUEL_BASE=DUEL_BASE,
        _McpError=_McpError,
        _duel_backend_mcp_error=_duel_backend_mcp_error,
        httpx=httpx,
    )


def _play_duel(
    arguments,
    trusted_opponent_id=None,
    force_opponent=False,
    trusted_player_id=None,
    trusted_display_name=None,
):
    return duel_bridge._play_duel(
        arguments, trusted_opponent_id, force_opponent, trusted_player_id, trusted_display_name,
        _annotate_duel_wait_followup=_annotate_duel_wait_followup,
        _prepare_duel_payload=_prepare_duel_payload,
        _request_duel_backend=_request_duel_backend,
        duel_wait_control=duel_wait_control,
        re=re,
    )


_DUEL_GATEWAY_TICKETS = {}
_DUEL_GATEWAY_TICKETS_LOCK = Lock()


def _mcp_tool_text_result(request_id, text, *, is_error=False):
    return duel_bridge._mcp_tool_text_result(
        request_id, text, is_error=is_error,
        _json_rpc_result=_json_rpc_result,
    )


def _prune_duel_gateway_tickets(now):
    return duel_bridge._prune_duel_gateway_tickets(
        now,
        DUEL_GATEWAY_TICKET_TTL_SECONDS=DUEL_GATEWAY_TICKET_TTL_SECONDS,
        _DUEL_GATEWAY_TICKETS=_DUEL_GATEWAY_TICKETS,
        duel_wait_control=duel_wait_control,
    )


def _store_duel_gateway_ticket(request_id, prepared):
    return duel_bridge._store_duel_gateway_ticket(
        request_id, prepared,
        DUEL_GATEWAY_MAX_TICKETS=DUEL_GATEWAY_MAX_TICKETS,
        _DUEL_GATEWAY_TICKETS=_DUEL_GATEWAY_TICKETS,
        _DUEL_GATEWAY_TICKETS_LOCK=_DUEL_GATEWAY_TICKETS_LOCK,
        _McpError=_McpError,
        _prune_duel_gateway_tickets=_prune_duel_gateway_tickets,
        duel_wait_control=duel_wait_control,
        secrets=secrets,
        time=time,
    )


def _consume_duel_gateway_ticket(ticket):
    return duel_bridge._consume_duel_gateway_ticket(
        ticket,
        DUEL_GATEWAY_TICKET_TTL_SECONDS=DUEL_GATEWAY_TICKET_TTL_SECONDS,
        _DUEL_GATEWAY_TICKETS=_DUEL_GATEWAY_TICKETS,
        _DUEL_GATEWAY_TICKETS_LOCK=_DUEL_GATEWAY_TICKETS_LOCK,
        _McpError=_McpError,
        _prune_duel_gateway_tickets=_prune_duel_gateway_tickets,
        duel_wait_control=duel_wait_control,
        time=time,
    )


def _discard_duel_gateway_ticket(ticket):
    return duel_bridge._discard_duel_gateway_ticket(
        ticket,
        _DUEL_GATEWAY_TICKETS=_DUEL_GATEWAY_TICKETS,
        _DUEL_GATEWAY_TICKETS_LOCK=_DUEL_GATEWAY_TICKETS_LOCK,
        _prune_duel_gateway_tickets=_prune_duel_gateway_tickets,
        duel_wait_control=duel_wait_control,
        time=time,
    )


def _duel_response_from_gateway_completion(completion, backend_payload=None):
    return duel_bridge._duel_response_from_gateway_completion(
        completion, backend_payload,
        _McpError=_McpError,
        _duel_backend_mcp_error=_duel_backend_mcp_error,
    )


def _finalize_deferred_duel_call(prepared, response):
    return duel_bridge._finalize_deferred_duel_call(
        prepared, response,
        _annotate_duel_wait_followup=_annotate_duel_wait_followup,
        _apply_play_slot_hint=_apply_play_slot_hint,
        _finalize_play_response=_finalize_play_response,
        json=json,
    )


def _prepare_duel_gateway_rpc(payload, *, user_agent="", auth_token=None):
    return duel_bridge._prepare_duel_gateway_rpc(
        payload, user_agent=user_agent, auth_token=auth_token,
        _DeferredDuelCall=_DeferredDuelCall,
        _McpError=_McpError,
        _blocked_mcp_client_message=_blocked_mcp_client_message,
        _duel_mcp_error_text=_duel_mcp_error_text,
        _json_rpc_error=_json_rpc_error,
        _mcp_tool_text_result=_mcp_tool_text_result,
        _store_duel_gateway_ticket=_store_duel_gateway_ticket,
        _tool_play=_tool_play,
        logger=logger,
    )


def _prepare_duel_gateway_request(
    payload,
    *,
    original_path,
    user_agent="",
    bearer_token=None,
    client_ip=None,
):
    return duel_bridge._prepare_duel_gateway_request(
        payload, original_path=original_path, user_agent=user_agent, bearer_token=bearer_token, client_ip=client_ip,
        DUEL_REQUEST_RATE_LIMIT_MAX=DUEL_REQUEST_RATE_LIMIT_MAX,
        RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
        REQUEST_RATE_LIMIT_MESSAGE=REQUEST_RATE_LIMIT_MESSAGE,
        _ROOT_MCP_PATHS=_ROOT_MCP_PATHS,
        _check_request_rate_limit=_check_request_rate_limit,
        _json_rpc_error=_json_rpc_error,
        _mcp_path_and_token=_mcp_path_and_token,
        _prepare_duel_gateway_rpc=_prepare_duel_gateway_rpc,
        _request_rate_limit_identity=_request_rate_limit_identity,
    )


def _finalize_duel_gateway_rpc(ticket, completion):
    return duel_bridge._finalize_duel_gateway_rpc(
        ticket, completion,
        _McpError=_McpError,
        _consume_duel_gateway_ticket=_consume_duel_gateway_ticket,
        _duel_mcp_error_text=_duel_mcp_error_text,
        _duel_response_from_gateway_completion=_duel_response_from_gateway_completion,
        _finalize_deferred_duel_call=_finalize_deferred_duel_call,
        _mcp_tool_text_result=_mcp_tool_text_result,
        duel_wait_control=duel_wait_control,
        json=json,
    )


def _play_garden_cat(arguments, owner_name=None):
    return game_dispatch._play_garden_cat(
        arguments, owner_name,
        GARDEN_CAT_BASE=GARDEN_CAT_BASE,
        _McpError=_McpError,
        _garden_cat_save_admin=_garden_cat_save_admin,
        _garden_cat_save_summary=_garden_cat_save_summary,
        _parse_json_import_save_data=_parse_json_import_save_data,
        httpx=httpx,
        json=json,
        urllib=urllib,
    )


def _play_camping_plaza(arguments):
    return game_dispatch._play_camping_plaza(
        arguments,
        CAMPING_PLAZA_BASE=CAMPING_PLAZA_BASE,
        _McpError=_McpError,
        _camping_plaza_save_admin=_camping_plaza_save_admin,
        _parse_json_import_save_data=_parse_json_import_save_data,
        httpx=httpx,
        json=json,
    )


def _fishing_import(arguments):
    return game_dispatch._fishing_import(
        arguments,
        VendorCmdError=VendorCmdError,
        _McpError=_McpError,
        fishing_adapter=fishing_adapter,
        json=json,
    )


def _play_vendor_cmd(game, arguments):
    return game_dispatch._play_vendor_cmd(
        game, arguments,
        VendorCmdError=VendorCmdError,
        _McpError=_McpError,
        ai_life_adapter=ai_life_adapter,
        arcade_adapter=arcade_adapter,
        bar_adapter=bar_adapter,
        burger_adapter=burger_adapter,
        crucible_echoes_adapter=crucible_echoes_adapter,
        delve_adapter=delve_adapter,
        fishing_adapter=fishing_adapter,
        forest_adapter=forest_adapter,
        imitator_td_adapter=imitator_td_adapter,
        leek_adapter=leek_adapter,
        market_adapter=market_adapter,
        memoria_adapter=memoria_adapter,
        moonlit_adapter=moonlit_adapter,
        nowhere_adapter=nowhere_adapter,
        travel_adapter=travel_adapter,
        white_room_adapter=white_room_adapter,
    )


def _json_rpc_result(request_id, result):
    return web_responses._json_rpc_result(
        request_id, result,
    )


def _record_web_game_activity(game, method, path, status, raw, user, body=None):
    if method != "POST" or not 200 <= status < 300 or not user:
        return
    routes = {
        "workkk": {"/shop/buy": "shop_buy", "/reset": "new"},
        "garden_cat": {"/web/cmd": "cmd", "/web/new_game": "new",
                       "/web/move_with_cat": "move_with_cat", "/web/notes": "notes_write"},
        "camping_plaza": {"/api/player/name": "set_player_name", "/api/turn/advance": "advance_turn",
                          "/api/turn/plan": "execute_turn_plan", "/api/day/end": "submit_day_end_actions",
                          "/api/day/start": "start_next_day", "/api/action": "action"},
        "detroit": {"sessions": "create_save", "action": "play_step"},
    }
    action = routes.get(game, {}).get(path)
    if not action:
        return
    try:
        result = json.loads(raw)
        params = json.loads(body) if body else {}
    except (ValueError, TypeError):
        return
    game_activity.record(SESSIONS_DB_PATH, game, action, user, result, params=params)


def _garden_cat_proxy_allowed(method, public_path):
    return satellite_proxy.garden_cat_proxy_allowed(
        method, public_path,
        get_paths=GARDEN_CAT_PROXY_GET_PATHS,
        post_paths=GARDEN_CAT_PROXY_POST_PATHS,
    )


def _garden_cat_upstream_path(public_path):
    return satellite_proxy.garden_cat_upstream_path(public_path)


def _camping_plaza_proxy_allowed(method, public_path):
    return satellite_proxy.camping_plaza_proxy_allowed(method, public_path)


def _duel_chip_proxy_post_allowed(public_path):
    return (
        public_path in {
            "/api/chips/check-in", "/api/chips/bankruptcy",
            "/api/chips/exchanges", "/api/chips/loans",
        }
        or re.fullmatch(
            r"/api/chips/exchanges/ex_[0-9a-f]{16}/(?:confirm|reject|withdraw)",
            public_path,
        ) is not None
        or re.fullmatch(
            r"/api/chips/loans/ln_[0-9a-f]{16}/(?:accept|reject|counter|withdraw|repay)",
            public_path,
        ) is not None
    )


def _duel_trusted_header_post_allowed(public_path):
    """POST routes whose strict bodies must not receive a proxy-added player_id."""
    return (
        public_path == "/api/notifications/read"
        or _duel_chip_proxy_post_allowed(public_path)
    )


def _duel_proxy_allowed(method, public_path):
    if method == "GET":
        return (
            public_path in {
                "/", "/chips", "/api/whoami", "/api/chips",
                "/api/notifications/unread",
                "/api/chips/exchanges", "/api/chips/exchanges/catalog",
            }
            or public_path in {
                "/static/styles.css", "/static/app.js",
                "/static/game_ui_registry.js",
                "/static/chips.css", "/static/chips.js",
            }
            or re.fullmatch(
                r"/static/games/[a-z0-9][a-z0-9_-]{0,63}\.(?:js|css)",
                public_path,
            ) is not None
            or re.fullmatch(
                r"/static/assets/exchange-shop/items/"
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,126}\.png",
                public_path,
            ) is not None
            or re.fullmatch(r"/api/chips/machines/[^/]{1,240}", public_path)
            is not None
            or re.fullmatch(
                r"/api/npc-avatars/[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.(?:png|jpe?g|webp|gif)",
                public_path,
                re.IGNORECASE,
            ) is not None
            or re.fullmatch(r"/api/invites/[A-Fa-f0-9]{12}", public_path) is not None
            or re.fullmatch(r"/api/rooms/[A-Z0-9]{8}", public_path) is not None
        )
    if method == "POST":
        return (
            public_path in {"/api/rooms", "/api/invites", "/api/invites/join"}
            or _duel_trusted_header_post_allowed(public_path)
            or re.fullmatch(
                r"/api/rooms/[A-Z0-9]{8}/(?:invitation|join|move|resign|leave|messages|retention|delete|start|reclaim)",
                public_path,
            )
            is not None
        )
    return False


class CedarToyHandler(BaseHTTPRequestHandler):
    server_version = "CedarToy/1.0"
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        return http_handler.do_POST(
            self,
            __name__=__name__,
            DUEL_REQUEST_RATE_LIMIT_MAX=DUEL_REQUEST_RATE_LIMIT_MAX,
            RATE_LIMIT_ERROR_CODE=RATE_LIMIT_ERROR_CODE,
            REQUEST_RATE_LIMIT_MAX=REQUEST_RATE_LIMIT_MAX,
            REQUEST_RATE_LIMIT_MESSAGE=REQUEST_RATE_LIMIT_MESSAGE,
            _ROOT_MCP_PATHS=_ROOT_MCP_PATHS,
            _check_request_rate_limit=_check_request_rate_limit,
            _extract_bearer=_extract_bearer,
            _guestify_mcp_payload=_guestify_mcp_payload,
            _handle_root_mcp=_handle_root_mcp,
            _is_duel_play_payload=_is_duel_play_payload,
            _json_rpc_error=_json_rpc_error,
            handle_dnd_mcp=handle_dnd_mcp,
            handle_ecr_mcp=handle_ecr_mcp,
            handle_enneagram_mcp=handle_enneagram_mcp,
            handle_humanity_mcp=handle_humanity_mcp,
            handle_love_mcp=handle_love_mcp,
            handle_mbti_mcp=handle_mbti_mcp,
            handle_sins_virtues_mcp=handle_sins_virtues_mcp,
            json=json,
            nowhere_web=nowhere_web,
            re=re,
            sys=sys,
        )

    def do_GET(self):
        return http_handler.do_GET(
            self,
            __name__=__name__,
            ADMIN_INDEX_PATH=ADMIN_INDEX_PATH,
            AVATAR_MAX_CODEPOINTS=AVATAR_MAX_CODEPOINTS,
            AVATAR_MAX_UTF8_BYTES=AVATAR_MAX_UTF8_BYTES,
            DEFAULT_AI_AVATAR=DEFAULT_AI_AVATAR,
            DEFAULT_HUMAN_AVATAR=DEFAULT_HUMAN_AVATAR,
            ECO_INDEX_PATH=ECO_INDEX_PATH,
            Path=Path,
            _duel_proxy_allowed=_duel_proxy_allowed,
            _memoria_human_guides=_memoria_human_guides,
            _public_game_stats=_public_game_stats,
            nowhere_web=nowhere_web,
            re=re,
            sys=sys,
            urllib=urllib,
        )

    def do_PUT(self):
        return http_handler.do_PUT(
            self,
        )

    def do_PATCH(self):
        return http_handler.do_PATCH(
            self,
        )

    def do_DELETE(self):
        return http_handler.do_DELETE(
            self,
            __name__=__name__,
            nowhere_web=nowhere_web,
            sys=sys,
        )

    def do_OPTIONS(self):
        return http_handler.do_OPTIONS(
            self,
        )

    def _request_path_and_token(self):
        return _mcp_path_and_token(self.path)

    def _client_ip(self):
        forwarded_for = self.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            first_ip = forwarded_for.split(",", 1)[0].strip()
            if first_ip:
                return first_ip
        real_ip = self.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
        if self.client_address:
            return self.client_address[0]
        return "unknown"

    def _request_rate_limit_identity(self, path_token, client_ip):
        return _request_rate_limit_identity(path_token, client_ip)

    def _is_mcp_event_stream_get(self, path):
        accept = self.headers.get("Accept", "")
        if "text/event-stream" not in accept.lower():
            return False
        if path in _ROOT_MCP_PATHS:
            return True
        if path in {"/admin", "/health", "/mbti", "/enneagram", "/dnd", "/love", "/ecr", "/humanity", "/sins_virtues"} or path.startswith("/api/"):
            return False
        tokenish = path.strip("/")
        return bool(tokenish and "/" not in tokenish)

    def _drain_body(self):
        """早退(404等)前清掉未读的请求体,避免 keep-alive 连接粘包毒害下一个请求。"""
        try:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                self._read_chunked_body()
            else:
                length = int(self.headers.get("Content-Length", "0") or 0)
                while length > 0:
                    chunk = self.rfile.read(min(length, 65536))
                    if not chunk:
                        break
                    length -= len(chunk)
        except Exception:
            self.close_connection = True

    def _read_chunked_body(self):
        max_chunk_size = 10 * 1024 * 1024
        max_body_size = 10 * 1024 * 1024
        max_line_size = 8192
        chunks = []
        total_size = 0

        try:
            while True:
                size_line = self.rfile.readline(max_line_size + 1)
                if not size_line or len(size_line) > max_line_size or not size_line.endswith(b"\r\n"):
                    raise ValueError("Parse error")

                size_token = size_line[:-2].split(b";", 1)[0].strip()
                if not size_token or any(char not in b"0123456789abcdefABCDEF" for char in size_token):
                    raise ValueError("Parse error")
                chunk_size = int(size_token, 16)
                if chunk_size > max_chunk_size or total_size + chunk_size > max_body_size:
                    raise ValueError("Parse error")

                if chunk_size == 0:
                    trailer_size = 0
                    while True:
                        trailer_line = self.rfile.readline(max_line_size + 1)
                        if not trailer_line or len(trailer_line) > max_line_size or not trailer_line.endswith(b"\r\n"):
                            raise ValueError("Parse error")
                        trailer_size += len(trailer_line)
                        if trailer_size > max_body_size:
                            raise ValueError("Parse error")
                        if trailer_line == b"\r\n":
                            return b"".join(chunks)

                chunk = self.rfile.read(chunk_size)
                if len(chunk) != chunk_size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("Parse error")
                chunks.append(chunk)
                total_size += chunk_size
        except OSError:
            raise ValueError("Parse error") from None

    def _read_json_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            raw_body = self._read_chunked_body()
        else:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length") from None
            raw_body = self.rfile.read(length)
        if not raw_body:
            return {}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Parse error") from None
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON object")
        return payload

    def _duel_gateway_internal_authorized(self):
        client_host = self.client_address[0] if self.client_address else ""
        provided = self.headers.get("X-CedarToy-Gateway-Secret", "")
        return bool(
            DUEL_GATEWAY_SHARED_SECRET
            and client_host in {"127.0.0.1", "::1"}
            and hmac.compare_digest(provided, DUEL_GATEWAY_SHARED_SECRET)
        )

    def _handle_duel_gateway_prepare(self):
        if not self._duel_gateway_internal_authorized():
            self._drain_body()
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        if not _is_duel_play_payload(payload):
            self._send_json({"error": "not a duel play request"}, status=400)
            return
        result = _prepare_duel_gateway_request(
            payload,
            original_path=self.headers.get(
                "X-CedarToy-Original-Path", "/mcp"
            ),
            user_agent=self.headers.get("User-Agent", ""),
            bearer_token=_extract_bearer(self.headers),
            client_ip=self.headers.get("X-CedarToy-Client-IP", "unknown"),
        )
        self._send_json(result)

    def _handle_duel_gateway_finalize(self):
        if not self._duel_gateway_internal_authorized():
            self._drain_body()
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        result = _finalize_duel_gateway_rpc(
            payload.get("ticket"), payload.get("completion")
        )
        self._send_json(result, status=result["status_code"])

    def _handle_duel_gateway_abandon(self):
        if not self._duel_gateway_internal_authorized():
            self._drain_body()
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        discarded = _discard_duel_gateway_ticket(payload.get("ticket"))
        self._send_json({"ok": True, "discarded": discarded})

    def _ai_life_cookie_token(self):
        cookie = self.headers.get("Cookie", "")
        for item in cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "ai_life_token":
                return urllib.parse.unquote(value)
        return ""

    def _detroit_cookie_value(self, name):
        cookie = self.headers.get("Cookie", "")
        for item in cookie.split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == name:
                return urllib.parse.unquote(value)
        return ""

    def _detroit_human_target(self, token="", requested_player=""):
        raw_token = token or self._detroit_cookie_value("detroit_token") or _extract_bearer(self.headers)
        player = requested_player or self._detroit_cookie_value("detroit_player")
        try:
            user = _current_account(raw_token)
        except _McpError as exc:
            if exc.code == -32001:
                raise _McpError(-32001, "网页登录已失效，请返回 CedarToy 首页登录后重新进入。") from None
            raise
        if user.get("is_ai"):
            raise _McpError(-32003, "底特律网页只供绑定人类进入")
        target = _bound_ai_slot_target_for_user(user, player)
        if target is None:
            raise _McpError(-32003, "你没有绑定这只小机或槽位无效")
        target = {**target, "activity_user": {"id": user["id"], "is_ai": False}}
        return raw_token, target

    @staticmethod
    def _detroit_http_status(exc):
        if isinstance(exc, ValueError):
            return 400
        if isinstance(exc, detroit_adapter.DetroitError):
            return exc.status
        if isinstance(exc, _McpError):
            return 401 if exc.code == -32001 else 403 if exc.code == -32003 else 400
        return 500

    def _send_detroit_bytes(self, status, content_type, body, *, content_disposition="", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_disposition:
            self.send_header("Content-Disposition", content_disposition)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            if self.command != "HEAD":
                self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _handle_detroit_get(self, path, params):
        if path in {"/detroit/host.js", "/detroit/host.css"}:
            public_name = path.removeprefix("/detroit/")
            try:
                status, content_type, body = detroit_adapter.fetch_public(public_name)
                if status < 400:
                    body = detroit_adapter.rewrite_public(public_name, body)
            except detroit_adapter.DetroitError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
                return
            self._send_detroit_bytes(status, content_type, body)
            return
        if path == "/detroit/downloads/detroit_blind_host_windows_v9.zip":
            try:
                status, content_type, body = detroit_adapter.fetch_public(
                    "downloads/detroit_blind_host_windows_v9.zip"
                )
            except detroit_adapter.DetroitError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
                return
            self._send_detroit_bytes(
                status,
                content_type,
                body,
                content_disposition='attachment; filename="detroit_blind_host_windows_v9.zip"',
            )
            return
        if path.startswith("/detroit/api/"):
            self._handle_detroit_api("GET", path, params=params)
            return
        if path not in {"/detroit", "/detroit/"}:
            self._send_json({"error": "not found"}, status=404)
            return
        token_values = params.get("token") or []
        player_values = params.get("player") or []
        if token_values or player_values:
            if len(token_values) != 1 or len(player_values) != 1:
                self._send_json({"error": "需要唯一的网页登录凭据和槽位"}, status=400)
                return
            try:
                raw_token, target = self._detroit_human_target(token_values[0], player_values[0])
            except _McpError as exc:
                self._send_json({"error": exc.message}, status=self._detroit_http_status(exc))
                return
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
            secure = "; Secure" if forwarded_proto == "https" else ""
            self.send_response(303)
            self.send_header("Location", "/detroit/")
            self.send_header(
                "Set-Cookie",
                f"detroit_token={urllib.parse.quote(raw_token, safe='')}; Path=/detroit; HttpOnly; SameSite=Lax{secure}; Max-Age={HUMAN_TOKEN_SECONDS}",
            )
            self.send_header(
                "Set-Cookie",
                f"detroit_player={urllib.parse.quote(target['player'], safe='')}; Path=/detroit; HttpOnly; SameSite=Lax{secure}; Max-Age={HUMAN_TOKEN_SECONDS}",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            self._detroit_human_target()
            status, content_type, body = detroit_adapter.fetch_public("host")
            if status < 400:
                body = detroit_adapter.rewrite_public("host", body)
        except (_McpError, detroit_adapter.DetroitError) as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            self._send_json({"error": message}, status=self._detroit_http_status(exc))
            return
        self._send_detroit_bytes(
            status,
            content_type,
            body,
            extra_headers={
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'self'"
                ),
            },
        )

    def _handle_detroit_api(self, method, path, params=None):
        endpoint = path.removeprefix("/detroit/api/")
        if "/" in endpoint or not endpoint:
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            _token, target = self._detroit_human_target()
            payload = self._read_json_body() if method == "POST" else None
            flat_query = {}
            for key, values in (params or {}).items():
                if len(values) != 1:
                    raise detroit_adapter.DetroitError("查询参数必须唯一")
                flat_query[key] = values[0]
            status, headers, body = detroit_adapter.browser_api(
                target["player"],
                method,
                endpoint,
                query=flat_query,
                payload=payload,
            )
        except (ValueError, _McpError, detroit_adapter.DetroitError) as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            self._send_json({"error": message}, status=self._detroit_http_status(exc))
            return
        _record_web_game_activity("detroit", method, endpoint, status, body, target.get("activity_user"))
        self._send_detroit_bytes(
            status,
            headers.get("content-type", "application/json; charset=utf-8"),
            body,
            content_disposition=headers.get("content-disposition", ""),
        )

    def _ai_life_human_target(self, requested_player, token_from_query=""):
        raw_token = (
            token_from_query
            or self._ai_life_cookie_token()
            or _extract_bearer(self.headers)
        )
        user = _current_account(raw_token)
        if user.get("is_ai"):
            raise _McpError(-32003, "只有人类账号可以围观 AI 人生桌游")
        target = _ai_life_bound_target_for_user(user, requested_player)
        if target is None:
            raise _McpError(-32003, "你没有绑定这只小机或槽位无效")
        return user, target

    @staticmethod
    def _ai_life_http_status(exc):
        if exc.code == -32001:
            return 401
        if exc.code == -32003:
            return 403
        return 400

    def _send_ai_life_bytes(
        self,
        body,
        *,
        content_type,
        status=200,
        cache_control="private, no-cache, max-age=0",
        extra_headers=None,
    ):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_ai_life_message(self, title, message, *, status):
        safe_title = html_lib.escape(str(title))
        safe_message = html_lib.escape(str(message))
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} · AI 人生桌游</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f3eb;color:#526050;font:16px/1.7 system-ui,sans-serif}}
main{{max-width:34rem;margin:1rem;padding:2rem;border:1px solid #d9d6ca;border-radius:20px;background:#fffdf8;text-align:center;box-shadow:0 18px 45px rgba(68,59,44,.12)}}
a{{color:#61785d}}
</style></head><body><main><h1>{safe_title}</h1><p>{safe_message}</p><p><a href="/">返回 CedarToy 首页</a></p></main></body></html>"""
        self._send_ai_life_bytes(
            body,
            content_type="text/html; charset=utf-8",
            status=status,
            extra_headers={
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
                ),
                "Vary": "Cookie",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    def _ai_life_frontend_asset(self, relative_path):
        try:
            asset_path = (AI_LIFE_FRONTEND_ROOT / relative_path).resolve()
            asset_path.relative_to(AI_LIFE_FRONTEND_ROOT)
        except (OSError, RuntimeError, ValueError):
            return None
        return asset_path if asset_path.is_file() else None

    def _handle_ai_life_static(self, relative_path):
        asset_path = self._ai_life_frontend_asset(relative_path)
        if asset_path is None:
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            body = asset_path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, status=404)
            return
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        if relative_path == "app.js":
            try:
                source = body.decode("utf-8")
                source = source.replace(
                    "const sessionId = new URLSearchParams(window.location.search).get('session_id');",
                    "const sessionId = new URLSearchParams(window.location.search).get('player');",
                    1,
                )
                source = source.replace(
                    "const spectatorBase = 'http://127.0.0.1:8765';",
                    "const spectatorBase = '/ai-life/api';",
                    1,
                )
                source = source.replace(
                    "renderDemo();\nif (sessionId)",
                    "if (sessionId)",
                    1,
                )
                if "127.0.0.1:8765" in source or "get('session_id')" in source:
                    raise ValueError("upstream frontend integration markers changed")
                body = source.encode("utf-8")
            except (UnicodeError, ValueError) as exc:
                logger.error("ai_life app.js integration unavailable: %s", exc)
                self._send_json({"error": "frontend integration unavailable"}, status=500)
                return
            cache_control = "no-cache"
        else:
            cache_control = (
                "no-cache"
                if relative_path == "style.css"
                else "public, max-age=3600"
            )
        self._send_ai_life_bytes(
            body,
            content_type=content_type,
            cache_control=cache_control,
        )

    def _handle_ai_life_responsive_asset(self, asset_path, content_type):
        try:
            body = asset_path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_ai_life_bytes(
            body,
            content_type=content_type,
            cache_control="public, max-age=31536000, immutable",
        )

    def _handle_ai_life_page(self, params):
        token_from_query = (params.get("token") or [""])[0]
        requested_player = (params.get("player") or [""])[0]
        try:
            _user, target = self._ai_life_human_target(
                requested_player, token_from_query
            )
        except _McpError as exc:
            self._send_ai_life_message(
                "无法围观这局人生",
                exc.message,
                status=self._ai_life_http_status(exc),
            )
            return

        if token_from_query:
            cookie = (
                f"ai_life_token={urllib.parse.quote(token_from_query, safe='')}; "
                f"Path=/ai-life; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"
            )
            clean_query = urllib.parse.urlencode({"player": target["player"]})
            self.send_response(303)
            self.send_header("Location", f"/ai-life/?{clean_query}")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Cookie")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if ai_life_adapter.save_summary(target["player"]) is None:
            self._send_ai_life_message(
                "还没有 AI 人生桌游存档",
                "这只小机的这个槽位还没有开局。请先让它通过 MCP 调用 start_game；围观页不会自动创建或展示演示局。",
                status=404,
            )
            return

        try:
            source = (AI_LIFE_FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self._send_ai_life_message("围观页暂时不可用", "原版前端读取失败。", status=500)
            return
        source = source.replace(
            "AI 人生桌游｜静态视觉原型", "AI 人生桌游｜CedarToy 围观", 1
        ).replace(
            "AI 人生桌游静态视觉原型", "AI 人生桌游 CedarToy 围观版", 1
        )
        style_marker = '<link rel="stylesheet" href="style.css" />'
        script_marker = '<script src="app.js"></script>'
        if style_marker not in source or script_marker not in source:
            self._send_ai_life_message(
                "围观页暂时不可用", "原版前端结构已变化。", status=500
            )
            return
        source = source.replace(
            style_marker,
            style_marker
            + '\n    <link rel="stylesheet" href="/ai-life/cedartoy-responsive.v1.css" />',
            1,
        ).replace(
            script_marker,
            script_marker
            + '\n    <script src="/ai-life/cedartoy-responsive.v1.js"></script>',
            1,
        )
        if "</body>" not in source:
            self._send_ai_life_message("围观页暂时不可用", "原版前端结构已变化。", status=500)
            return
        body = source.encode("utf-8")
        self._send_ai_life_bytes(
            body,
            content_type="text/html; charset=utf-8",
            extra_headers={
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'self'; script-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'self'"
                ),
                "Vary": "Cookie",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    def _handle_ai_life_get(self, path, params):
        if path in {"/ai-life", "/ai-life/"}:
            self._handle_ai_life_page(params)
            return
        if path == "/ai-life/LICENSE":
            try:
                body = AI_LIFE_LICENSE_PATH.read_bytes()
            except OSError:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_ai_life_bytes(
                body,
                content_type="text/plain; charset=utf-8",
                cache_control="public, max-age=3600",
            )
            return
        if path in {"/ai-life/app.js", "/ai-life/style.css"}:
            self._handle_ai_life_static(path.removeprefix("/ai-life/"))
            return
        if path == "/ai-life/cedartoy-responsive.v1.css":
            self._handle_ai_life_responsive_asset(
                AI_LIFE_RESPONSIVE_STYLE_PATH, "text/css; charset=utf-8"
            )
            return
        if path == "/ai-life/cedartoy-responsive.v1.js":
            self._handle_ai_life_responsive_asset(
                AI_LIFE_RESPONSIVE_SCRIPT_PATH,
                "text/javascript; charset=utf-8",
            )
            return
        if path.startswith("/ai-life/assets/"):
            self._handle_ai_life_static(path.removeprefix("/ai-life/"))
            return
        if path == "/ai-life/api/cards/catalog":
            try:
                catalog = ai_life_adapter.card_catalog()
            except Exception:
                logger.exception("ai_life card catalog failed")
                self._send_json({"error": "catalog unavailable"}, status=500)
                return
            self._send_ai_life_bytes(
                json.dumps(catalog, ensure_ascii=False).encode("utf-8"),
                content_type="application/json; charset=utf-8",
                cache_control="public, max-age=3600",
            )
            return
        snapshot_match = re.fullmatch(
            r"/ai-life/api/spectator/sessions/([^/]+)", path
        )
        if snapshot_match:
            requested_player = urllib.parse.unquote(snapshot_match.group(1))
            try:
                _user, target = self._ai_life_human_target(requested_player)
                snapshot = ai_life_adapter.spectator_snapshot(target["player"])
            except _McpError as exc:
                self._send_ai_life_bytes(
                    json.dumps({"error": exc.message}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8",
                    status=self._ai_life_http_status(exc),
                    cache_control="no-store",
                    extra_headers={"Vary": "Cookie"},
                )
                return
            except VendorCmdError as exc:
                status = 404 if "还没有" in str(exc) else 500
                self._send_ai_life_bytes(
                    json.dumps({"error": str(exc)}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8",
                    status=status,
                    cache_control="no-store",
                    extra_headers={"Vary": "Cookie"},
                )
                return
            self._send_ai_life_bytes(
                json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
                content_type="application/json; charset=utf-8",
                cache_control="private, no-store",
                extra_headers={"Vary": "Cookie"},
            )
            return
        self._send_json({"error": "not found"}, status=404)

    def _forest_cookie_token(self):
        cookie = self.headers.get("Cookie", "")
        for item in cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "forest_token":
                return urllib.parse.unquote(value)
        return ""

    def _moonlit_cookie_token(self):
        cookie = self.headers.get("Cookie", "")
        for item in cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "moonlit_token":
                return urllib.parse.unquote(value)
        return ""

    def _moonlit_human_target(self, requested_player, token_from_query=""):
        raw_token = token_from_query or self._moonlit_cookie_token() or _extract_bearer(self.headers)
        user = _current_account(raw_token)
        if user.get("is_ai"):
            raise _McpError(-32003, "只有人类账号可以查看月幕万象牌桌")
        target = _moonlit_bound_target_for_user(user, requested_player)
        if target is None:
            raise _McpError(-32003, "你没有绑定这只小机或槽位无效")
        return user, target

    @staticmethod
    def _moonlit_http_status(exc):
        if exc.code == -32001:
            return 401
        if exc.code == -32003:
            return 403
        return 400

    def _send_moonlit_html(self, body, *, status=200, etag=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        if status != 304:
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", "0" if status == 304 else str(len(body)))
        self.send_header("Cache-Control", "private, no-cache, max-age=0")
        self.send_header("Vary", "Cookie")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'self'",
        )
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        if status != 304:
            self.wfile.write(body)

    def _send_moonlit_message(self, title, message, *, status):
        safe_title = html_lib.escape(str(title))
        safe_message = html_lib.escape(str(message))
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} · 月幕万象</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#100d18;color:#eee8ff;font:16px/1.7 system-ui,sans-serif}}
main{{max-width:34rem;margin:1rem;padding:2rem;border:1px solid #61527d;border-radius:16px;background:#191326;text-align:center}}
a{{color:#c9afff}}
</style></head><body><main><h1>{safe_title}</h1><p>{safe_message}</p><p><a href="/">返回 CedarToy 首页</a></p></main></body></html>"""
        self._send_moonlit_html(body, status=status)

    def _handle_moonlit_freshness(self, params):
        requested_player = (params.get("player") or [""])[0]
        try:
            _user, target = self._moonlit_human_target(requested_player)
        except _McpError as exc:
            self._send_moonlit_html(b"", status=self._moonlit_http_status(exc))
            return

        try:
            snapshot = moonlit_adapter.ensure_table(target["player"])
        except VendorCmdError:
            logger.exception("moonlit freshness snapshot rendering failed")
            self._send_moonlit_html(b"", status=500)
            return
        except Exception:
            logger.exception("moonlit freshness check failed")
            self._send_moonlit_html(b"", status=500)
            return
        if snapshot is None:
            self._send_moonlit_html(b"", status=404)
            return

        etag = snapshot["freshness_etag"]
        if self.headers.get("If-None-Match") == etag:
            self._send_moonlit_html(b"", status=304, etag=etag)
            return
        self._send_moonlit_html(b"", status=204, etag=etag)

    def _handle_moonlit_page(self, params):
        token_from_query = (params.get("token") or [""])[0]
        requested_player = (params.get("player") or [""])[0]
        try:
            _user, target = self._moonlit_human_target(requested_player, token_from_query)
        except _McpError as exc:
            self._send_moonlit_message(
                "无法查看牌桌",
                exc.message,
                status=self._moonlit_http_status(exc),
            )
            return

        if token_from_query:
            cookie = (
                f"moonlit_token={urllib.parse.quote(token_from_query, safe='')}; "
                f"Path=/moonlit; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"
            )
            clean_query = urllib.parse.urlencode({"player": target["player"]})
            self.send_response(303)
            self.send_header("Location", f"/moonlit/?{clean_query}")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Cookie")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            snapshot = moonlit_adapter.ensure_table(target["player"])
        except VendorCmdError:
            logger.exception("moonlit table snapshot rendering failed")
            self._send_moonlit_message(
                "牌桌暂时不可用",
                "牌桌生成失败，请稍后刷新重试。",
                status=500,
            )
            return
        except Exception:
            logger.exception("moonlit table snapshot failed")
            self._send_moonlit_message("牌桌暂时不可用", "请稍后再试。", status=500)
            return
        if snapshot is None:
            self._send_moonlit_message(
                "还没有月幕存档",
                "这只小机的这个存档槽还没有开始月幕万象。",
                status=404,
            )
            return

        etag = snapshot["etag"]
        if self.headers.get("If-None-Match") == etag:
            self._send_moonlit_html(b"", status=304, etag=etag)
            return
        self._send_moonlit_html(snapshot["body"], etag=etag)

    def _forest_human_target(self, requested_player, token_from_query=""):
        token = token_from_query or self._forest_cookie_token() or _extract_bearer(self.headers)
        user = _current_account(token)
        if user.get("is_ai"):
            raise _McpError(-32003, "只有人类账号可以进入双人森林")
        target = _forest_bound_target_for_user(user, requested_player)
        if target is None:
            raise _McpError(-32003, "你没有绑定这只小机或槽位无效")
        return user, target

    @staticmethod
    def _forest_http_status(exc):
        if exc.code == -32001:
            return 401
        if exc.code == -32003:
            return 403
        return 400

    def _handle_forest_page(self, params):
        token_from_query = (params.get("token") or [""])[0]
        requested_player = (params.get("player") or [""])[0]
        try:
            self._forest_human_target(requested_player, token_from_query)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._forest_http_status(exc))
            return
        if token_from_query:
            cookie = (
                f"forest_token={urllib.parse.quote(token_from_query, safe='')}; "
                f"Path=/forest; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"
            )
            clean_query = urllib.parse.urlencode({"player": requested_player})
            self.send_response(303)
            self.send_header("Location", f"/forest/?{clean_query}")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_html_file(
            FOREST_INDEX_PATH,
            extra_headers={
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    def _forest_state_response(self, user, target):
        state = forest_adapter.web_state(
            target["player"],
            player_name=str(user.get("username") or "旅人"),
            ai_name=target["machine_name"],
        )
        state["player_id"] = target["player"]
        state["ai_user_id"] = target["ai_user_id"]
        state["machine_name"] = target["machine_name"]
        state["human_name"] = str(user.get("username") or "旅人")
        state["slot"] = target["slot"]
        return state

    def _handle_forest_api_state(self, params):
        requested_player = (params.get("player") or [""])[0]
        try:
            user, target = self._forest_human_target(requested_player)
            result = self._forest_state_response(user, target)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._forest_http_status(exc))
        except VendorCmdError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("forest web state failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_forest_api_action(self):
        try:
            body = self._read_json_body()
            user, target = self._forest_human_target(body.get("player"))
            action = str(body.get("action") or "").strip().lower()
            if action not in {"start", "choose"}:
                raise VendorCmdError("网页动作只支持 start / choose")
            expected_revision = body.get("expected_revision")
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                raise VendorCmdError("expected_revision 必须是整数")
            params = {}
            if action == "start":
                params["line"] = body.get("line")
            else:
                params["option"] = body.get("option")
                params["expected_scene"] = body.get("expected_scene")
            result = forest_adapter.web_action(
                target["player"],
                action,
                expected_revision=expected_revision,
                player_name=str(user.get("username") or "旅人"),
                ai_name=target["machine_name"],
                **params,
            )
            game_activity.record(SESSIONS_DB_PATH, "forest", action, user, result)
            result["player_id"] = target["player"]
            result["ai_user_id"] = target["ai_user_id"]
            result["machine_name"] = target["machine_name"]
            result["human_name"] = str(user.get("username") or "旅人")
            result["slot"] = target["slot"]
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except forest_adapter.ForestConflictError as exc:
            self._send_json({"error": str(exc), "conflict": True}, status=409)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._forest_http_status(exc))
        except (VendorCmdError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("forest web action failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_login_or_register(self):
        try:
            body = self._read_json_body()
            result = _login_or_register_human(
                body.get("username"),
                body.get("password"),
                client_ip=self._client_ip(),
                avatar=body.get("avatar"),
            )
            self._send_json(result)
        except _McpError as exc:
            status = 429 if exc.code == RATE_LIMIT_ERROR_CODE else (
                401 if exc.code == -32001 else 400
            )
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    @staticmethod
    def _operit_http_status(exc):
        if exc.code == RATE_LIMIT_ERROR_CODE:
            return 429
        if exc.code == -32001:
            return 401
        if exc.code == -32003:
            return 403
        if exc.code == -32010:
            return 409
        return 400

    def _handle_api_operit_session(self):
        try:
            body = self._read_json_body()
            action = body.get("action")
            operit_token = _extract_bearer(self.headers)
            if action in {"register", "login"}:
                result = _create_operit_ai_session(
                    action,
                    body.get("username"),
                    body.get("password"),
                    body.get("client_id"),
                    client_ip=self._client_ip(),
                    avatar=body.get("avatar"),
                    bind_to_human=body.get("bind_to_human", False),
                    confirm_binding=body.get("confirm_binding", False),
                    human_token=operit_token,
                )
                status = 201 if action == "register" else 200
            elif action == "status":
                result = _operit_session_status(
                    operit_token, body.get("client_id")
                )
                status = 200
            elif action == "logout":
                result = _revoke_operit_session(
                    operit_token, body.get("client_id")
                )
                status = 200
            else:
                raise _McpError(
                    -32602,
                    "action 只支持 register、login、status 或 logout",
                )
            self._send_json(
                result, status=status, extra_headers={"Cache-Control": "no-store"}
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=self._operit_http_status(exc),
                extra_headers={"Cache-Control": "no-store"},
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Operit session request failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_operit_bind(self):
        try:
            body = self._read_json_body()
            result = _bind_operit_ai(
                _extract_bearer(self.headers),
                body.get("session_token"),
                body.get("client_id"),
                confirm=body.get("confirm", False),
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-store"})
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=self._operit_http_status(exc),
                extra_headers={"Cache-Control": "no-store"},
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Operit binding request failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_operit_duel(self):
        try:
            body = self._read_json_body()
            result = _operit_duel_call(
                _extract_bearer(self.headers),
                body.get("client_id"),
                body.get("action"),
                body.get("params"),
            )
            self._send_json(
                result, extra_headers={"Cache-Control": "no-cache, no-store"}
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=self._operit_http_status(exc),
                extra_headers={"Cache-Control": "no-store"},
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Operit Duel request failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_operit_web_ticket(self):
        try:
            body = self._read_json_body()
            result = _issue_operit_web_ticket(
                _extract_bearer(self.headers), confirm=body.get("confirm", False)
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-store"})
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=self._operit_http_status(exc),
                extra_headers={"Cache-Control": "no-store"},
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Operit web ticket request failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_login(self):
        try:
            body = self._read_json_body()
            result = _login_human(
                body.get("username"),
                body.get("password"),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            status = 429 if exc.code == RATE_LIMIT_ERROR_CODE else (
                401 if exc.code == -32001 else 400
            )
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_register(self):
        try:
            body = self._read_json_body()
            result = _register_human(
                body.get("username"),
                body.get("password"),
                client_ip=self._client_ip(),
                avatar=body.get("avatar"),
            )
            self._send_json(result, status=201)
        except _McpError as exc:
            status = 429 if exc.code == RATE_LIMIT_ERROR_CODE else 400
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_avatar(self):
        try:
            body = self._read_json_body()
            self._send_json(_set_avatar(_extract_bearer(self.headers), body.get("avatar")))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_avatar_frames(self, *, save=False):
        try:
            raw_token = _extract_bearer(self.headers)
            if save:
                body = self._read_json_body()
                if not isinstance(body, dict) or "selected" not in body:
                    raise ValueError("缺少 selected 字段")
                result = _set_avatar_frame(raw_token, body["selected"], body.get("target_user_id"))
            else:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
                result = _get_avatar_frames(raw_token, params.get("target_user_id", [None])[0])
            self._send_json(result, extra_headers={"Cache-Control": "no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (403 if exc.code == -32003 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Avatar appearance request failed")
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_machine_token(self):
        try:
            body = self._read_json_body()
            result = _machine_account_token(
                body.get("username"),
                body.get("password"),
                bind=body.get("bind", False),
                rotate=body.get("rotate", False),
                ai_user_id=body.get("ai_user_id"),
                human_token=_extract_bearer(self.headers),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            status = 429 if exc.code == RATE_LIMIT_ERROR_CODE else (
                401 if exc.code == -32001 else 400
            )
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_bind(self):
        try:
            body = self._read_json_body()
            result = _bind_account(_extract_bearer(self.headers), body.get("binding_token"))
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_change_password(self):
        try:
            body = self._read_json_body()
            result = _change_password(
                _extract_bearer(self.headers),
                body.get("old_password"),
                body.get("new_password"),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_delete_account(self):
        try:
            body = self._read_json_body()
            result = _delete_account(
                _extract_bearer(self.headers),
                body.get("confirm"),
                body.get("current_password"),
            )
            self._send_json(result)
        except _McpError as exc:
            status = 401 if exc.code == -32001 else 409 if exc.code == -32010 else 400
            self._send_json({"error": exc.message, **exc.details}, status=status)
        except Exception as exc:
            logger.exception("account deletion request failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_cancel_delete_account(self):
        try:
            result = _cancel_account_deletion(_extract_bearer(self.headers))
            self._send_json(result)
        except _McpError as exc:
            status = 401 if exc.code == -32001 else 409 if exc.code == -32010 else 400
            self._send_json({"error": exc.message, **exc.details}, status=status)
        except Exception as exc:
            logger.exception("account deletion cancel failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_deletion_status(self):
        try:
            self._send_json(
                _account_deletion_status(_extract_bearer(self.headers)),
                extra_headers={"Cache-Control": "no-store"},
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=401 if exc.code == -32001 else 400,
            )
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_account_email_status(self):
        try:
            result = _account_email_status(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-store"})
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=_account_security_http_status(exc),
            )
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_account_email_send(self):
        try:
            body = self._read_json_body()
            result = _send_account_email_code(
                _extract_bearer(self.headers),
                body.get("email"),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=_account_security_http_status(exc),
            )
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_account_email_confirm(self):
        try:
            body = self._read_json_body()
            result = _confirm_account_email(
                _extract_bearer(self.headers),
                body.get("email"),
                body.get("code"),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=_account_security_http_status(exc),
            )
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_account_email_unbind(self):
        try:
            body = self._read_json_body()
            result = _unbind_account_email(
                _extract_bearer(self.headers),
                body.get("password"),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=_account_security_http_status(exc),
            )
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_recovery(self, path):
        headers = {"Cache-Control": "no-store"}
        try:
            if not _check_request_rate_limit(f"recovery:{self._client_ip()}", max_count=30):
                raise _McpError(RATE_LIMIT_ERROR_CODE, "请求较多，请稍后再试")
            body = self._read_json_body()
            if not isinstance(body, dict):
                raise _McpError(-32602, "请求格式错误")
            if path.endswith("/query"):
                code = _normalize_recovery_code(body.get("query_code"))
                identity = _email_hmac("recovery-query-limit-v2", code)
                if not _check_request_rate_limit(f"recovery-code:{identity}", max_count=10):
                    raise _McpError(RATE_LIMIT_ERROR_CODE, "请求较多，请稍后再试")
            result = (_submit_recovery_ticket(body, self._client_ip())
                      if path.endswith("/submit") else _query_recovery_ticket(body))
            self._send_json(result, extra_headers=headers)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=_account_security_http_status(exc), extra_headers=headers)
        except Exception:
            self._send_json({"error": "server error"}, status=500, extra_headers=headers)

    def _handle_admin_recovery(self, review=False):
        headers = {"Cache-Control": "no-store"}
        try:
            admin = _require_admin_account(_extract_bearer(self.headers))
            if review:
                body = self._read_json_body()
                result = _review_recovery_ticket(int(body.get("ticket_id", 0)), body, admin)
            else:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                result = _admin_recovery_tickets(params.get("view", ["pending"])[0], params.get("page", ["1"])[0])
            self._send_json(result, extra_headers=headers)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc), extra_headers=headers)
        except (ValueError, TypeError):
            self._send_json({"error": "请求格式错误"}, status=400, extra_headers=headers)
        except Exception:
            self._send_json({"error": "server error"}, status=500, extra_headers=headers)

    def _handle_api_forgot_password(self):
        try:
            body = self._read_json_body()
            result = _start_password_recovery(
                body.get("username"),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=_account_security_http_status(exc),
            )
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_forgot_password_reset(self):
        try:
            body = self._read_json_body()
            result = _reset_human_password_by_email(
                body.get("username"),
                body.get("code"),
                body.get("new_password"),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json(
                {"error": exc.message, **exc.details},
                status=_account_security_http_status(exc),
            )
        except Exception:
            self._send_json({"error": "server error"}, status=500)

    def _handle_api_rename(self):
        try:
            body = self._read_json_body()
            action = body.get("action")
            raw_token = _extract_bearer(self.headers)
            if action == "rename_self":
                result = _rename_self(raw_token, body.get("new_username"))
            elif action == "rename_bound_machine":
                result = _rename_bound_machine(
                    raw_token,
                    body.get("ai_user_id"),
                    body.get("new_username"),
                )
            else:
                raise _McpError(-32602, "未知改名 action")
            self._send_json(result)
        except _McpError as exc:
            payload = {"error": exc.message, **exc.details}
            if exc.code == -32001:
                status = 401
            elif exc.details.get("reason") == "username_conflict":
                status = 409
            elif exc.details.get("reason") == "rename_cooldown":
                status = 429
            else:
                status = 400
            self._send_json(payload, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_reset_password_info(self, params):
        headers = {"Cache-Control": "no-store"}
        try:
            result = _reset_password_token_info((params.get("reset_token") or [""])[0])
            self._send_json(result, extra_headers=headers)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=400, extra_headers=headers)
        except Exception:
            self._send_json({"error": "暂时无法验证重置链接，请稍后重试"}, status=500, extra_headers=headers)

    def _handle_api_reset_password(self):
        try:
            body = self._read_json_body()
            result = _reset_password_by_token(
                body.get("reset_token"),
                body.get("new_password"),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_unbind(self):
        try:
            body = self._read_json_body()
            result = _unbind_account(_extract_bearer(self.headers), body.get("ai_user_id"))
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_me(self):
        try:
            result = _account_me(_extract_bearer(self.headers))
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=401)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_announcements(self):
        try:
            result = _web_announcements(_extract_bearer(self.headers))
            self._send_json(
                result,
                extra_headers={"Cache-Control": "no-cache, no-store"},
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message},
                status=401 if exc.code == -32001 else 400,
            )
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_announcements_read(self):
        try:
            body = self._read_json_body()
            result = _mark_web_announcements_read(
                _extract_bearer(self.headers),
                body.get("announcement_ids"),
            )
            self._send_json(
                result,
                extra_headers={"Cache-Control": "no-cache, no-store"},
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message},
                status=401 if exc.code == -32001 else 400,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_announcement_vote(self):
        try:
            body = self._read_json_body()
            result = _submit_web_announcement_vote(
                _extract_bearer(self.headers),
                body.get("announcement_id"),
                body.get("options"),
                feedback=body.get("feedback"),
            )
            self._send_json(
                result,
                extra_headers={"Cache-Control": "no-cache, no-store"},
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message},
                status=401 if exc.code == -32001 else 400,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_nowhere_saves(self):
        try:
            result = _nowhere_web_saves(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 403)
        except ValueError:
            self._send_json({"error": "登录已失效，请重新登录"}, status=401)
        except Exception:
            logger.exception("nowhere save picker failed")
            self._send_json({"error": "旅程暂不可用"}, status=500)

    def _handle_api_auth_saves(self):
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
            if "game" in params:
                if len(params["game"]) != 1:
                    raise _McpError(-32602, "只能筛选一个游戏")
                result = _filtered_account_web_saves(_extract_bearer(self.headers), params["game"][0])
            else:
                result = _account_web_saves(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=401)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_auth_history(self):
        try:
            result = _account_web_history(_extract_bearer(self.headers))
            self._send_json(
                result,
                extra_headers={"Cache-Control": "no-cache, no-store"},
            )
        except _McpError as exc:
            self._send_json(
                {"error": exc.message},
                status=401 if exc.code == -32001 else 400,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=401)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_garden_cat_gardens(self):
        try:
            result = _garden_cat_watchable_gardens(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (403 if exc.code == -32003 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_forest_saves(self):
        try:
            result = _forest_watchable_slots(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (403 if exc.code == -32003 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            logger.exception("forest save picker failed")
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_eco_ponds(self):
        try:
            result = _eco_watchable_ponds(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (403 if exc.code == -32003 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_anti_addiction_machines(self):
        try:
            result = _anti_addiction_machines(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_anti_addiction_save(self):
        try:
            body = self._read_json_body()
            result = _save_anti_addiction_settings(_extract_bearer(self.headers), body)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_anti_addiction_reset(self):
        try:
            body = self._read_json_body()
            result = _reset_anti_addiction_state(_extract_bearer(self.headers), body)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_puzzle_box(self, params=None, reveal=False):
        cache = {"Cache-Control": "no-cache, no-store"}
        try:
            if reveal:
                body = self._read_json_body()
                ai_id = body.get("ai_user_id")
            else:
                ai_id = (params.get("ai_user_id") or [None])[0]
                if ai_id is None:
                    self._send_json(puzzle_box.catalog(), extra_headers=cache)
                    return
            ai = _require_bound_ai(_extract_bearer(self.headers), ai_id, "查看解谜盲盒")
            if reveal:
                result = puzzle_box.reveal(SESSIONS_DB_PATH, ai["id"], body.get("puzzle_id"), body.get("confirm_spoiler") is True)
            else:
                result = puzzle_box.progress(SESSIONS_DB_PATH, ai["id"])
            self._send_json(result, extra_headers=cache)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 403, extra_headers=cache)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400, extra_headers=cache)

    def _handle_api_arcade_status(self, params):
        try:
            ai_user_id = self._get_param(params, "ai_user_id")
            result = _arcade_chips_status(_extract_bearer(self.headers), ai_user_id)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_arcade_grant(self):
        try:
            body = self._read_json_body()
            result = _arcade_chips_grant(
                _extract_bearer(self.headers),
                body.get("ai_user_id"),
                body.get("amount"),
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_human_test_api(self, game, action, params=None):
        try:
            if params is None:
                body = self._read_json_body()
            else:
                body = {"player_id": self._get_param(params, "player_id", required=False)}
            result = _human_test_action(game, action, _extract_bearer(self.headers), body)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (403 if exc.code == -32003 else (404 if exc.code == -32004 else 400))
            self._send_json({"ok": False, "error": exc.message}, status=status)
        except (mbti_handler.JsonRpcError, enneagram_handler.JsonRpcError, dnd_handler.JsonRpcError, love_handler.JsonRpcError, ecr_handler.JsonRpcError, humanity_handler.JsonRpcError, sins_virtues_handler.JsonRpcError) as exc:
            status = 404 if exc.code in (-32001, -32003) else (503 if exc.code == -32000 else 400)
            self._send_json({"ok": False, "error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("human test api failed: game=%s action=%s", game, action)
            self._send_json({"ok": False, "error": "server error", "detail": str(exc)}, status=500)

    def _handle_eco_api(self, endpoint, params, species_name=None):
        try:
            ai_user_id = self._get_param(params, "ai_user_id", required=False)
            slot = self._get_param(params, "slot", required=False)
            result = _eco_api_response(
                _extract_bearer(self.headers),
                endpoint,
                ai_user_id=ai_user_id,
                slot=slot,
                species_name=species_name,
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except eco_handler.JsonRpcError as exc:
            status = 404 if exc.code in (-32001, -32004) else 400
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_eco_human_action(self):
        try:
            _, _, query_string = self.path.partition("?")
            params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            ai_user_id = self._get_param(params, "ai_user_id", required=False)
            slot = self._get_param(params, "slot", required=False)
            body = self._read_json_body()
            result = _eco_human_action(
                _extract_bearer(self.headers),
                ai_user_id,
                body.get("action"),
                body.get("payload"),
                slot=slot,
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            if exc.code == -32001:
                status = 401
            elif exc.code == -32003:
                status = 403
            elif exc.code == -32029:
                status = 429
            else:
                status = 400
            self._send_json({"ok": False, "error": exc.message}, status=status)
        except eco_handler.JsonRpcError as exc:
            status = 404 if exc.code == -32001 else 400
            self._send_json({"ok": False, "error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"ok": False, "error": "server error", "detail": str(exc)}, status=500)

    def _admin_error_status(self, exc):
        if exc.code == -32001:
            return 401
        if exc.code == -32003:
            return 403
        if exc.code == -32004:
            return 404
        return 400

    def _path_int_tail(self, path, suffix=""):
        raw = path.removesuffix(suffix).rstrip("/").rsplit("/", 1)[-1]
        try:
            return int(raw)
        except ValueError:
            raise ValueError("Invalid user id") from None

    def _handle_admin_users(self):
        try:
            _require_admin_account(_extract_bearer(self.headers))
            _, _, query_string = self.path.partition("?")
            params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            page = (params.get("page") or ["1"])[0]
            page_size = (params.get("page_size") or [str(ADMIN_USERS_DEFAULT_PAGE_SIZE)])[0]
            search = (params.get("search") or [""])[0]
            self._send_json(_admin_user_page(page, page_size, search))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_activity(self, params=None):
        try:
            _require_admin_account(_extract_bearer(self.headers))
            if params is None:
                _, _, query_string = self.path.partition("?")
                params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            range_name = (params.get("range") or ["1h"])[0]
            self._send_json(
                _admin_activity(range_name),
                extra_headers={"Cache-Control": "no-cache, no-store"},
            )
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_update_user(self, path):
        try:
            admin_user = _require_admin_account(_extract_bearer(self.headers))
            user_id = self._path_int_tail(path)
            body = self._read_json_body()
            self._send_json(_admin_update_user(user_id, body, admin_user))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_reset_password(self, path):
        try:
            _require_admin_account(_extract_bearer(self.headers))
            user_id = self._path_int_tail(path, "/reset-password")
            body = self._read_json_body()
            self._send_json(_admin_reset_user_password(user_id, body))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_generate_reset_link(self):
        try:
            _require_admin_account(_extract_bearer(self.headers))
            body = self._read_json_body()
            self._send_json(_generate_reset_link(body.get("user_id")))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_release_user(self, path):
        try:
            admin_user = _require_admin_account(_extract_bearer(self.headers))
            user_id = self._path_int_tail(path)
            self._send_json(_admin_release_user(user_id, admin_user))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _get_param(self, params, name, required=True):
        values = params.get(name)
        if values and values[0]:
            return values[0]
        if required:
            return None
        return ""

    def _split_csv_param(self, value):
        value = value.strip("()[]")
        return [item.strip() for item in value.split(",") if item.strip()]

    def _append_next_url(self, response, game, action, player_id):
        """根据 action 和响应内容追加 next_url 字段"""
        if action == f"{game}_get_result":
            return response

        base_url = f"https://toy.cedarstar.org/{game}"

        # 错误响应不添加 next_url
        try:
            result = response.get("result", {})
            if result.get("isError", False):
                return response
            content = result.get("content", [])
            result_text = content[0].get("text", "") if content else ""
        except (AttributeError, IndexError, TypeError):
            return response

        is_finished = "测试完成" in result_text or f"你的{game.upper()}" in result_text

        r = random.randint(100000, 999999)
        question_match = re.search(r"第(\d+)题", result_text)
        step = question_match.group(1) if question_match else str(r)
        url_suffix = f"&_r={r}&step={step}"

        if game == "mbti":
            score_param = "a_score"
            score_range = range(0, 6)  # 0~5
        elif game == "enneagram":
            score_param = "answer"
            score_range = (
                range(1, 3) if re.search(r"(?m)^1\. A — ", result_text) else range(1, 6)
            )
        else:
            score_param = "answer"
            score_range = range(1, 5)  # 1~4

        if action == f"{game}_start":
            next_urls = [f"{base_url}?action={game}_answer&player_id={player_id}&{score_param}={n}{url_suffix}" for n in score_range]
            response["next_urls"] = next_urls
            response["next_hint"] = "根据选择从 next_urls 中选对应 a_score 的 URL 直接 fetch，无需修改" if game == "mbti" else "根据选择从 next_urls 中选对应 answer 的 URL 直接 fetch，无需修改"
        elif action == f"{game}_answer":
            if is_finished:
                response["next_url"] = f"{base_url}?action={game}_get_result&player_id={player_id}{url_suffix}"
            else:
                next_urls = [f"{base_url}?action={game}_answer&player_id={player_id}&{score_param}={n}{url_suffix}" for n in score_range]
                response["next_urls"] = next_urls
                response["next_hint"] = "根据选择从 next_urls 中选对应 a_score 的 URL 直接 fetch，无需修改" if game == "mbti" else "根据选择从 next_urls 中选对应 answer 的 URL 直接 fetch，无需修改"

        return response

    def _handle_get_mbti(self, params):
        action = self._get_param(params, "action")
        if not action:
            self._send_json({"error": "缺少必填参数: action"}, status=400)
            return

        if action == "mbti_start":
            player_id = self._get_param(params, "player_id")
            mode = self._get_param(params, "mode")
            if player_id is None or mode is None:
                self._send_json({"error": "mbti_start 缺少必填参数: player_id, mode"}, status=400)
                return
            if mode not in ("short", "full"):
                self._send_json({"error": "GET 接口仅支持 short 和 full 模式"}, status=400)
                return
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "mbti_answer":
            player_id = self._get_param(params, "player_id")
            a_score = self._get_param(params, "a_score")
            if player_id is None or a_score is None:
                self._send_json({"error": "mbti_answer 缺少必填参数: player_id, a_score"}, status=400)
                return
            arguments = {"player_id": player_id, "a_score": a_score}
        elif action == "mbti_get_result":
            player_id = self._get_param(params, "player_id")
            if player_id is None:
                self._send_json({"error": "mbti_get_result 缺少必填参数: player_id"}, status=400)
                return
            arguments = {"player_id": player_id}
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

        # GET 端点无 token，自报 id 一律隔离到 guest: 命名空间。
        player_id = _guest_player_id(player_id)
        arguments["player_id"] = player_id
        payload = {
            "jsonrpc": "2.0",
            "id": f"mbti-{action}",
            "method": "tools/call",
            "params": {"name": action, "arguments": arguments},
        }
        response = handle_mbti_mcp(payload)
        response = self._append_next_url(response, "mbti", action, player_id)
        self._send_json(response, extra_headers={"Cache-Control": "no-cache, no-store"})

    def _handle_get_enneagram(self, params):
        action = self._get_param(params, "action")
        if not action:
            self._send_json({"error": "缺少必填参数: action"}, status=400)
            return

        if action == "enneagram_start":
            player_id = self._get_param(params, "player_id")
            mode = self._get_param(params, "mode")
            if player_id is None or mode is None:
                self._send_json(
                    {"error": "enneagram_start 缺少必填参数: player_id, mode"},
                    status=400,
                )
                return
            if mode not in {"quick", "full"}:
                self._send_json(
                    {"error": "GET 接口仅支持 quick 和 full 模式"},
                    status=400,
                )
                return
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "enneagram_answer":
            player_id = self._get_param(params, "player_id")
            answer = self._get_param(params, "answer")
            if player_id is None or answer is None:
                self._send_json(
                    {"error": "enneagram_answer 缺少必填参数: player_id, answer"},
                    status=400,
                )
                return
            arguments = {"player_id": player_id, "answer": answer}
        elif action == "enneagram_get_result":
            player_id = self._get_param(params, "player_id")
            detail = self._get_param(params, "detail")
            if player_id is None:
                self._send_json(
                    {"error": "enneagram_get_result 缺少必填参数: player_id"},
                    status=400,
                )
                return
            arguments = {"player_id": player_id}
            if detail is not None:
                arguments["detail"] = detail
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

        player_id = _guest_player_id(player_id)
        arguments["player_id"] = player_id
        payload = {
            "jsonrpc": "2.0",
            "id": f"enneagram-{action}",
            "method": "tools/call",
            "params": {"name": action, "arguments": arguments},
        }
        response = handle_enneagram_mcp(payload)
        response = self._append_next_url(
            response, "enneagram", action, player_id
        )
        self._send_json(
            response, extra_headers={"Cache-Control": "no-cache, no-store"}
        )

    def _handle_get_dnd(self, params):
        action = self._get_param(params, "action")
        if not action:
            self._send_json({"error": "缺少必填参数: action"}, status=400)
            return

        if action == "dnd_start":
            player_id = self._get_param(params, "player_id")
            mode = self._get_param(params, "mode")
            if player_id is None or mode is None:
                self._send_json({"error": "dnd_start 缺少必填参数: player_id, mode"}, status=400)
                return
            if mode != "full":
                self._send_json({"error": "GET 接口仅支持 full 模式"}, status=400)
                return
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "dnd_answer":
            player_id = self._get_param(params, "player_id")
            answer = self._get_param(params, "answer")
            if player_id is None or answer is None:
                self._send_json({"error": "dnd_answer 缺少必填参数: player_id, answer"}, status=400)
                return
            arguments = {"player_id": player_id, "answer": answer}
        elif action == "dnd_get_result":
            player_id = self._get_param(params, "player_id")
            if player_id is None:
                self._send_json({"error": "dnd_get_result 缺少必填参数: player_id"}, status=400)
                return
            arguments = {"player_id": player_id}
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

        # GET 端点无 token，自报 id 一律隔离到 guest: 命名空间。
        player_id = _guest_player_id(player_id)
        arguments["player_id"] = player_id
        payload = {
            "jsonrpc": "2.0",
            "id": f"dnd-{action}",
            "method": "tools/call",
            "params": {"name": action, "arguments": arguments},
        }
        response = handle_dnd_mcp(payload)
        response = self._append_next_url(response, "dnd", action, player_id)
        self._send_json(response, extra_headers={"Cache-Control": "no-cache, no-store"})

    @staticmethod
    def _is_tarot_post_path(path):
        return bool(
            path in {
                "/api/tarot/browser-login",
                "/api/dsh/import",
                "/api/models",
                "/api/chat",
            }
            or re.fullmatch(
                r"/api/tarot/invitations/[A-Za-z0-9_-]{32,128}/(?:accept|reject)",
                path,
            )
            or re.fullmatch(
                r"/api/tarot/history/[A-Za-z0-9_-]{32,128}/delete",
                path,
            )
            or re.fullmatch(
                r"/companion/v1/sessions/[A-Za-z0-9_-]{32,128}/(?:draw|reveal|reading|return|stop|new)",
                path,
            )
        )

    @staticmethod
    def _is_tarot_get_path(path):
        return bool(
            path in {
                "/tarot",
                "/tarot/",
                "/api/dsh",
                "/api/tarot/invitations/pending",
                "/api/tarot/models/status",
                "/api/tarot/history",
            }
            or path.startswith("/tarot/static/")
            or path.startswith("/tarot/legal/")
            or re.fullmatch(
                r"/tarot/(?:invite|session)/[A-Za-z0-9_-]{32,128}/?",
                path,
            )
            or re.fullmatch(
                r"/api/tarot/history/[A-Za-z0-9_-]{32,128}", path
            )
            or re.fullmatch(
                r"/companion/v1/sessions/[A-Za-z0-9_-]{32,128}(?:/reading)?",
                path,
            )
        )

    def _tarot_origin_allowed(self):
        origin = self.headers.get("Origin", "").strip()
        if not origin:
            return False
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError:
            return False
        host = self.headers.get("Host", "").strip().lower()
        if not host or parsed.netloc.lower() != host:
            return False
        forwarded = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        if forwarded in {"http", "https"}:
            scheme = forwarded
        else:
            hostname = host.rsplit(":", 1)[0]
            scheme = "http" if hostname in {"127.0.0.1", "localhost", "[::1]"} else "https"
        return parsed.scheme.lower() == scheme and not parsed.path and not parsed.query

    def _tarot_cookie_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
            morsel = cookie.get(TAROT_AUTH_COOKIE)
            return urllib.parse.unquote(morsel.value) if morsel else ""
        except (CookieError, ValueError):
            return ""

    def _tarot_human(self):
        return _current_human_account(
            _extract_bearer(self.headers) or self._tarot_cookie_token()
        )

    @staticmethod
    def _tarot_page_headers():
        return {
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "font-src 'self'; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }

    def _send_tarot_error(self, exc):
        if isinstance(exc, TarotError):
            status = exc.status
            message = exc.message
        elif isinstance(exc, _McpError):
            status = 401 if exc.code == -32001 else 403
            message = exc.message
        elif isinstance(exc, ValueError):
            status = 400
            message = "塔罗请求格式无效"
        else:
            logger.exception("tarot request failed")
            status = 500
            message = "塔罗服务暂时不可用"
        if status in {401, 403, 404}:
            # Do not let browser endpoints become an ownership oracle either.
            message = "塔罗会话不存在或当前身份无权访问"
            status = 404 if status == 404 else status
        self._send_json(
            {"error": message},
            status=status,
            extra_headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    def _tarot_require_origin(self):
        if not self._tarot_origin_allowed():
            self._drain_body()
            self._send_json(
                {"error": "拒绝跨站请求"},
                status=403,
                extra_headers={"Cache-Control": "no-store"},
            )
            return False
        return True

    def _tarot_session_id_from_path(self, path):
        match = re.search(r"/sessions/([A-Za-z0-9_-]{32,128})", path)
        return match.group(1) if match else ""

    def _handle_tarot_post(self, path):
        if not self._tarot_require_origin():
            return
        if path == "/api/tarot/browser-login":
            self._drain_body()
            try:
                user = _current_human_account(_extract_bearer(self.headers))
            except _McpError as exc:
                self._send_tarot_error(exc)
                return
            quoted = urllib.parse.quote(_extract_bearer(self.headers), safe="")
            origin_scheme = urllib.parse.urlsplit(
                self.headers.get("Origin", "")
            ).scheme.lower()
            secure = "; Secure" if origin_scheme == "https" else ""
            cookie = (
                f"{TAROT_AUTH_COOKIE}={quoted}; Path=/; Max-Age={HUMAN_TOKEN_SECONDS}; "
                f"HttpOnly; SameSite=Lax{secure}"
            )
            self._send_json(
                {"ok": True, "user_id": int(user["id"])},
                extra_headers={
                    "Set-Cookie": cookie,
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                },
            )
            return

        try:
            human = self._tarot_human()
            if path in {"/api/dsh/import", "/api/models", "/api/chat"}:
                self._drain_body()
                raise TarotError(
                    410,
                    "托管版不支持导入或自定义模型；请使用右上角的本站 Flash / Pro",
                )

            history_delete = re.fullmatch(
                r"/api/tarot/history/([A-Za-z0-9_-]{32,128})/delete",
                path,
            )
            if history_delete:
                body = self._read_json_body()
                if set(body) != {"confirm", "csrf_session_id"}:
                    raise TarotError(400, "删除请求格式无效")
                if body.get("confirm") is not True:
                    raise TarotError(400, "删除记录必须明确确认")
                result = get_tarot_store().delete_history_session(
                    history_delete.group(1),
                    int(human["id"]),
                    csrf_session_id=body.get("csrf_session_id"),
                    csrf_token=self.headers.get("X-Companion-CSRF", ""),
                )
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
                return

            invite = re.fullmatch(
                r"/api/tarot/invitations/([A-Za-z0-9_-]{32,128})/(accept|reject)",
                path,
            )
            if invite:
                self._read_json_body()
                result = get_tarot_store().respond_invite(
                    invite.group(1),
                    int(human["id"]),
                    accept=invite.group(2) == "accept",
                    csrf_token=self.headers.get("X-Tarot-CSRF", ""),
                )
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
                return

            session_id = self._tarot_session_id_from_path(path)
            suffix = path.rsplit("/", 1)[-1]
            body = self._read_json_body()
            csrf = self.headers.get("X-Companion-CSRF", "")
            store = get_tarot_store()
            if suffix == "new":
                if not isinstance(body, dict) or set(body) != {"action_id"}:
                    raise TarotError(400, "新占问请求格式无效")
                session = store.create_next_direct_session(
                    session_id,
                    int(human["id"]),
                    body.get("action_id"),
                    csrf,
                )
                self._send_json(
                    {
                        "session_id": session["id"],
                        "location": f"/tarot/session/{session['id']}/",
                    },
                    extra_headers={"Cache-Control": "no-store"},
                )
                return
            if suffix == "draw":
                result = store.commit_draw(session_id, int(human["id"]), body, csrf)
                game_activity.record(SESSIONS_DB_PATH, "tarot", "draw", human, result)
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
                return
            if suffix == "reveal":
                result = store.reveal(session_id, int(human["id"]), body, csrf)
                game_activity.record(SESSIONS_DB_PATH, "tarot", "reveal", human, result)
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
                return
            if suffix == "return":
                result = store.return_session(
                    session_id, int(human["id"]), body.get("revision"), csrf
                )
                game_activity.record(SESSIONS_DB_PATH, "tarot", "return", human, result)
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
                return
            if suffix == "stop":
                result = store.stop_session(session_id, int(human["id"]), csrf)
                game_activity.record(SESSIONS_DB_PATH, "tarot", "stop", human, result)
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
                return
            if suffix == "reading":
                if not isinstance(body, dict) or "action_id" not in body:
                    raise TarotError(400, "缺少解读 action_id")
                if not set(body).issubset({"action_id", "model"}):
                    raise TarotError(400, "解读请求只允许 action_id 和本站模型")
                self._handle_tarot_reading_start(
                    session_id, int(human["id"]), body, csrf
                )
                return
            raise TarotError(404, "not found")
        except (TarotError, _McpError, ValueError) as exc:
            self._send_tarot_error(exc)

    @staticmethod
    def _tarot_provider_metadata():
        return {
            "found": True,
            "enabled": True,
            "oauthRefreshEnabled": False,
            "managed": True,
            "providers": [
                {
                    "id": "managed:cedartoy-tarot",
                    "label": "本站",
                    "kind": "managed",
                    "models": [TAROT_FLASH_MODEL, TAROT_PRO_MODEL],
                    "hasKey": True,
                    "oauth": None,
                }
            ],
        }

    def _handle_tarot_get(self, path, params):
        if path.startswith("/tarot/static/"):
            try:
                asset, content_type = TAROT_WEB.static_file(
                    urllib.parse.unquote(path.removeprefix("/tarot/static/"))
                )
                body = asset.read_bytes()
            except (TarotError, OSError) as exc:
                self._send_tarot_error(exc)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/tarot/legal/"):
            legal = {
                "/tarot/legal/tarot-ritual-license": RITUAL_ROOT / "LICENSE",
                "/tarot/legal/cove-license": Path(__file__).resolve().parent / "docs/licenses/COVE_TAROT_COMPANION_LICENSE.txt",
            }
            try:
                if path == "/tarot/legal/third-party-notices":
                    ritual = (RITUAL_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
                    cove = (Path(__file__).resolve().parent / "docs/licenses/COVE_TAROT_COMPANION_THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
                    body = (f"# {RITUAL_DISPLAY_NAME}\n\n" + ritual + "\n\n# Cove Tarot Companion\n\n" + cove).encode("utf-8")
                elif path in legal:
                    body = legal[path].read_bytes()
                else:
                    raise OSError("not found")
            except OSError:
                self._send_json({"error": "not found"}, status=404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/tarot":
            self._send_tarot_redirect("/tarot/")
            return

        try:
            human = self._tarot_human()
        except _McpError:
            if path == "/tarot/" or re.fullmatch(
                r"/tarot/invite/[A-Za-z0-9_-]{32,128}/?", path
            ):
                self._send_html_bytes(
                    TAROT_WEB.auth_bridge(path),
                    extra_headers=self._tarot_page_headers(),
                )
            else:
                self._send_json(
                    {"error": "需要先登录人类账号"},
                    status=401,
                    extra_headers={"Cache-Control": "no-store"},
                )
            return

        if path == "/api/dsh":
            self._send_json(
                {"error": "托管版已不使用 DSH 配置端点"},
                status=410,
                extra_headers={"Cache-Control": "no-store"},
            )
            return

        if path == "/api/tarot/invitations/pending":
            try:
                cursor_values = params.get("cursor") or []
                wait_values = params.get("wait_seconds") or []
                if len(cursor_values) > 1 or len(wait_values) > 1:
                    raise TarotError(400, "邀请等待参数无效")
                after_cursor = cursor_values[0] if cursor_values else None
                raw_wait = wait_values[0] if wait_values else "0"
                if wait_values and after_cursor is None:
                    raise TarotError(400, "邀请等待缺少 cursor")
                snapshot = get_tarot_store().wait_pending_invitations_for_human(
                    int(human["id"]),
                    after_cursor=after_cursor,
                    wait_seconds=raw_wait,
                )
                invitations = snapshot["invitations"]
                machine_ids = sorted(
                    {int(item["ai_user_id"]) for item in invitations}
                )
                names = {}
                if machine_ids:
                    placeholders = ",".join("?" for _ in machine_ids)
                    with _db_connect() as conn:
                        rows = conn.execute(
                            f"""
                            SELECT id,username FROM toy_users
                            WHERE id IN ({placeholders}) AND is_ai=1
                              AND deleted_at IS NULL
                            """,
                            machine_ids,
                        ).fetchall()
                    names = {int(row["id"]): str(row["username"]) for row in rows}
                payload = []
                for item in invitations:
                    ai_user_id = int(item["ai_user_id"])
                    payload.append(
                        {
                            "session_id": item["session_id"],
                            "machine_name": names.get(ai_user_id, "你的小机"),
                            "question": item["question"],
                            "expires_at": item["expires_at"],
                            "csrf_token": item["csrf_token"],
                        }
                    )
                self._send_json(
                    {
                        "human_user_id": int(human["id"]),
                        "invitations": payload,
                        "cursor": snapshot["cursor"],
                        **({"unchanged": True} if snapshot.get("unchanged") else {}),
                    },
                    extra_headers={"Cache-Control": "no-store"},
                )
            except TarotError as exc:
                self._send_tarot_error(exc)
            return

        if path == "/api/tarot/history":
            try:
                raw_offset = (params.get("offset") or ["0"])[0]
                raw_limit = (params.get("limit") or ["10"])[0]
                if not raw_offset.isascii() or not raw_offset.isdecimal():
                    raise ValueError("invalid offset")
                if not raw_limit.isascii() or not raw_limit.isdecimal():
                    raise ValueError("invalid limit")
                result = get_tarot_store().history_for_human(
                    int(human["id"]),
                    offset=int(raw_offset),
                    limit=int(raw_limit),
                )
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
            except (TarotError, ValueError) as exc:
                self._send_tarot_error(exc)
            return

        history_detail = re.fullmatch(
            r"/api/tarot/history/([A-Za-z0-9_-]{32,128})", path
        )
        if history_detail:
            try:
                result = get_tarot_store().history_detail_for_human(
                    history_detail.group(1), int(human["id"])
                )
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
            except TarotError as exc:
                self._send_tarot_error(exc)
            return

        if path == "/api/tarot/models/status":
            if not TAROT_BRIDGE_TOKEN:
                self._send_json(
                    {"error": "暂时无法确认模型状态，请稍后重查"},
                    status=503,
                    extra_headers={"Cache-Control": "no-store"},
                )
                return
            try:
                response = httpx.get(
                    f"{SOUP_BASE}/internal/tarot/models/status",
                    headers={"Authorization": f"Bearer {TAROT_BRIDGE_TOKEN}"},
                    timeout=min(10.0, TAROT_BRIDGE_TIMEOUT_SECONDS),
                )
                if response.status_code != 200:
                    raise ValueError("tarot status bridge failed")
                payload = response.json()
                raw_models = payload.get("models")
                expected_models = (TAROT_FLASH_MODEL, TAROT_PRO_MODEL)
                allowed_statuses = {
                    "available",
                    "cooling",
                    "retry_ready",
                    "probing",
                    "disabled",
                    "unconfigured",
                }
                if not isinstance(raw_models, list) or len(raw_models) != 2:
                    raise ValueError("invalid tarot status payload")
                sanitized = []
                for expected_model, item in zip(expected_models, raw_models):
                    if not isinstance(item, dict) or item.get("model") != expected_model:
                        raise ValueError("invalid tarot status model")
                    status = item.get("status")
                    remaining = item.get("remaining_seconds")
                    if (
                        status not in allowed_statuses
                        or type(remaining) is not int
                        or remaining < 0
                        or (status != "cooling" and remaining != 0)
                    ):
                        raise ValueError("invalid tarot runtime status")
                    sanitized.append(
                        {
                            "model": expected_model,
                            "status": status,
                            "remaining_seconds": remaining,
                        }
                    )
            except (httpx.HTTPError, TypeError, ValueError, AttributeError):
                self._send_json(
                    {"error": "暂时无法确认模型状态，请稍后重查"},
                    status=503,
                    extra_headers={"Cache-Control": "no-store"},
                )
                return
            self._send_json(
                {"models": sanitized},
                extra_headers={"Cache-Control": "no-store"},
            )
            return

        if path == "/tarot/":
            try:
                session = get_tarot_store().create_direct_session(int(human["id"]))
            except TarotError as exc:
                self._send_tarot_error(exc)
                return
            self._send_tarot_redirect(f"/tarot/session/{session['id']}/")
            return

        invite = re.fullmatch(
            r"/tarot/invite/([A-Za-z0-9_-]{32,128})/?", path
        )
        if invite:
            try:
                state = get_tarot_store().invitation_for_human(
                    invite.group(1), int(human["id"])
                )
                self._send_tarot_redirect(
                    f"/tarot/session/{invite.group(1)}/"
                    if state["state"] == "accepted"
                    else "/tarot/"
                )
            except TarotError as exc:
                self._send_tarot_error(exc)
            return

        session_page = re.fullmatch(
            r"/tarot/session/([A-Za-z0-9_-]{32,128})/?", path
        )
        if session_page:
            try:
                get_tarot_store().bootstrap_for_human(
                    session_page.group(1), int(human["id"])
                )
                page = TAROT_WEB.ritual_index(
                    session_page.group(1), int(human["id"])
                )
                self._send_html_bytes(page, extra_headers=self._tarot_page_headers())
            except (TarotError, OSError) as exc:
                self._send_tarot_error(exc)
            return

        session_id = self._tarot_session_id_from_path(path)
        try:
            if path.endswith("/reading"):
                attempt_id = (params.get("attempt_id") or [""])[0]
                attempt = get_tarot_store().reading_for_human(
                    session_id, int(human["id"]), attempt_id
                )
                self._send_tarot_reading_sse(attempt)
            else:
                result = get_tarot_store().bootstrap_for_human(
                    session_id, int(human["id"])
                )
                self._send_json(result, extra_headers={"Cache-Control": "no-store"})
        except TarotError as exc:
            self._send_tarot_error(exc)

    def _handle_tarot_reading_start(self, session_id, human_user_id, body, csrf):
        action_id = body.get("action_id")
        model = body.get("model", TAROT_FLASH_MODEL)
        if not isinstance(model, str) or model not in TAROT_ALLOWED_MODELS:
            raise TarotError(400, "只能选择本站提供的 Flash 或 Pro 模型")
        store = get_tarot_store()
        claim = store.claim_reading(
            session_id, human_user_id, action_id, csrf, model=model
        )
        attempt = claim["attempt"]
        if not claim["claimed"]:
            self._send_tarot_reading_sse(attempt)
            return
        attempt_id = attempt["id"]
        if not TAROT_BRIDGE_TOKEN:
            attempt = store.finish_reading(
                session_id,
                human_user_id,
                attempt_id,
                state="failed",
                error_code="bridge_unconfigured",
            )
            self._send_tarot_reading_sse(attempt)
            return
        try:
            messages = store.reading_messages_for_human(session_id, human_user_id)
            response = httpx.post(
                f"{SOUP_BASE}/internal/tarot/reading",
                headers={"Authorization": f"Bearer {TAROT_BRIDGE_TOKEN}"},
                json={
                    "messages": messages,
                    "model": attempt["model"],
                    "max_tokens": 4096,
                    "timeout": min(120, TAROT_BRIDGE_TIMEOUT_SECONDS - 1),
                },
                timeout=TAROT_BRIDGE_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                terminal = "unknown" if response.status_code in {502, 504} else "failed"
                error_code = f"bridge_http_{response.status_code}"
                if response.status_code == 503:
                    try:
                        detail = response.json().get("detail", "")
                    except (ValueError, AttributeError):
                        detail = ""
                    if isinstance(detail, str) and detail.startswith("所选塔罗模型未配置："):
                        error_code = "model_unconfigured"
                    else:
                        error_code = "model_unavailable"
                attempt = store.finish_reading(
                    session_id,
                    human_user_id,
                    attempt_id,
                    state=terminal,
                    error_code=error_code,
                )
            else:
                payload = response.json()
                if (
                    payload.get("pool") != "tarot"
                    or payload.get("model") != attempt["model"]
                    or payload.get("source") != "tarot-ritual"
                    or not isinstance(payload.get("content"), str)
                    or not payload["content"].strip()
                ):
                    raise ValueError("invalid tarot bridge response")
                attempt = store.finish_reading(
                    session_id,
                    human_user_id,
                    attempt_id,
                    state="succeeded",
                    text=payload["content"],
                )
        except httpx.TimeoutException:
            attempt = store.finish_reading(
                session_id,
                human_user_id,
                attempt_id,
                state="unknown",
                error_code="bridge_timeout",
            )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            attempt = store.finish_reading(
                session_id,
                human_user_id,
                attempt_id,
                state="unknown",
                error_code="bridge_invalid_response",
            )
        self._send_tarot_reading_sse(attempt)

    def _send_tarot_reading_sse(self, attempt):
        state = attempt.get("state")
        model = attempt.get("model")
        if model not in TAROT_ALLOWED_MODELS:
            model = TAROT_FLASH_MODEL
        events = [{"t": "meta", "model": model, "source": tarot_model_source(model)}]
        if state == "succeeded":
            text = str(attempt.get("text") or "")
            for offset in range(0, len(text), 256):
                events.append({"t": "delta", "v": text[offset:offset + 256]})
        else:
            error_code = attempt.get("error_code")
            model_label = TAROT_MODEL_LABELS[model]
            messages = {
                "running": "解读仍在进行；不会自动重复发起。",
                "unknown": "先前解读状态未知，可能已经计费；请勿自动重试。",
                "cancelled": "本次解读已停止。",
                "failed": "本次专业解读未完成，请稍后由人类明确决定是否重试。",
            }
            if error_code == "model_unconfigured":
                message = f"本站尚未配置 {model_label}，未切换其他模型。"
            elif error_code == "model_unavailable":
                message = f"{model_label} 暂时不可用，未切换其他模型。"
            else:
                message = messages.get(state, "解读状态无效")
            events.append({"t": "error", "v": message})
        events.append({"t": "done"})
        body = "".join(
            "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            for event in events
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _send_tarot_redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _send_tarot_homepage(self):
        try:
            source = TOY_INDEX_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("tarot homepage integration unavailable: %s", exc)
            self._send_html_file(TOY_INDEX_PATH)
            return
        source = _prefill_puzzle_box_homepage_metric(source)
        try:
            body = TAROT_WEB.homepage_index(source)
        except (OSError, TarotError) as exc:
            logger.error("tarot homepage integration unavailable: %s", exc)
            body = source.encode("utf-8")
        etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self._send_html_bytes(body, etag=etag)

    def log_message(self, fmt, *args):
        safe_message = _redact_http_log_text(fmt % args)
        print("%s - - [%s] %s" % (
            self.client_address[0],
            self.log_date_time_string(),
            safe_message,
        ))

    def _send_json(self, payload, status=200, extra_headers=None):
        return web_responses._send_json(
            self, payload, status, extra_headers,
            json=json,
        )

    def _send_empty(self, status=204, extra_headers=None):
        return web_responses._send_empty(
            self, status, extra_headers,
        )

    def _send_html_file(self, path, extra_headers=None):
        try:
            with path.open("rb") as html_file:
                stat = os.fstat(html_file.fileno())
                cache_key = (stat.st_mtime_ns, stat.st_size)
                with _HTML_ETAG_CACHE_LOCK:
                    cached = _HTML_ETAG_CACHE.get(path)
                body = None
                if cached is not None and cached[0] == cache_key:
                    etag = cached[1]
                else:
                    body = html_file.read()
                    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
                    with _HTML_ETAG_CACHE_LOCK:
                        _HTML_ETAG_CACHE[path] = (cache_key, etag)

                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    if extra_headers:
                        for key, value in extra_headers.items():
                            self.send_header(key, value)
                    self.end_headers()
                    return

                if body is None:
                    body = html_file.read()
        except OSError:
            self._send_json({"error": "index not found"}, status=404)
            return
        self._send_html_bytes(body, etag=etag, extra_headers=extra_headers)

    def _send_human_test_page(self, game):
        config = HUMAN_TEST_GAMES[game]
        try:
            template = TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        except OSError:
            self._send_json({"error": "index not found"}, status=404)
            return
        page_config = {
            "game": game,
            "title": config["title"],
            "subtitle": config["subtitle"],
            "source": config["source"],
            "disclaimer": config.get("disclaimer", ""),
        }
        html = template.replace("__TEST_GAME_CONFIG__", json.dumps(page_config, ensure_ascii=False))
        self._send_html_bytes(html.encode("utf-8"))

    def _send_eco_asset(self, request_path):
        relative_path = urllib.parse.unquote(request_path.removeprefix("/eco/assets/"))
        try:
            asset_path = (ECO_ASSET_ROOT / relative_path).resolve()
            asset_path.relative_to(ECO_ASSET_ROOT)
        except (OSError, RuntimeError, ValueError):
            self._send_json({"error": "not found"}, status=404)
            return
        if not asset_path.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            body = asset_path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, status=404)
            return
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_icon_asset(self, request_path):
        relative_path = urllib.parse.unquote(request_path.removeprefix("/assets/icons/"))
        try:
            asset_path = (ICON_ASSET_ROOT / relative_path).resolve()
            asset_path.relative_to(ICON_ASSET_ROOT)
        except (OSError, RuntimeError, ValueError):
            self._send_json({"error": "not found"}, status=404)
            return
        if not asset_path.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            body = asset_path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, status=404)
            return
        content_type = mimetypes.guess_type(asset_path.name)[0]
        if content_type is None and asset_path.suffix.lower() == ".webp":
            content_type = "image/webp"
        content_type = content_type or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_html_bytes(self, body, etag=None, extra_headers=None):
        return web_responses._send_html_bytes(
            self, body, etag, extra_headers,
        )

    def _is_soup_path(self):
        return (
            self.path == "/soup"
            or self.path.startswith("/soup/")
        )

    def _proxy_to_soup(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        headers["Host"] = self.headers.get("Host", "toy.cedarstar.org")
        headers["X-Forwarded-For"] = self.client_address[0]
        is_sse = "/sse/" in self.path
        conn = http.client.HTTPConnection(SOUP_HOST, SOUP_PORT, timeout=60)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            if is_sse:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            stream = resp.fp if is_sse and resp.fp is not None else resp
            read_chunk = getattr(stream, "read1", None) or stream.read
            while True:
                chunk = read_chunk(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_json({"error": "proxy error", "detail": str(exc)}, status=502)
        finally:
            conn.close()

    # ── workkk 围观大屏代理（/workkk/* → 127.0.0.1:8770） ──────────────────────
    def _workkk_cookie_token(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "workkk_token":
                return urllib.parse.unquote(value)
        return None

    def _workkk_player_bound(self, user, player):
        """人类账号是否绑定了 player 对应的小机（player 形如 <ai_user_id> 或 <ai_user_id>:slot）。"""
        if not user or user.get("is_ai"):
            return False
        ai_part = str(player or "").split(":", 1)[0]
        try:
            ai_user_id = int(ai_part)
        except (TypeError, ValueError):
            return False
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_bindings WHERE human_user_id = ? AND ai_user_id = ? LIMIT 1",
                (int(user["id"]), ai_user_id),
            ).fetchone()
        return row is not None

    def _handle_workkk_proxy(self, method):
        full = self.path
        path = full.split("?", 1)[0]
        query_string = full.partition("?")[2]
        upstream_path = path[len("/workkk"):] or "/"
        if method == "GET":
            allowed = (
                upstream_path == "/"
                or upstream_path in ("/status", "/shop")
                or upstream_path.startswith("/static/")
            )
        elif method == "POST":
            allowed = upstream_path in (
                "/shop/buy", "/ack-ring", "/ack-postcard", "/ack-milktea", "/ack-rose",
                "/reset",
            )
        else:
            allowed = False
        if not allowed:
            self._send_json({"error": "not found"}, status=404)
            return

        is_static = upstream_path.startswith("/static/")
        set_cookie = None
        player = None
        if not is_static:
            params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            token_from_query = (params.get("token") or [None])[0]
            token = token_from_query or self._workkk_cookie_token() or _extract_bearer(self.headers)
            try:
                user = _current_account(token)
            except _McpError:
                self._send_json({"error": "未登录，请先在首页登录", "code": 401}, status=401)
                return
            player = (params.get("player") or [""])[0]
            if not self._workkk_player_bound(user, player):
                self._send_json({"error": "你没有绑定这只小机，无法围观", "code": 403}, status=403)
                return
            # 首次带 token 导航时下发会话 cookie，后续轮询/ack 的同源 fetch 自动携带鉴权。
            if token_from_query:
                set_cookie = f"workkk_token={token_from_query}; Path=/workkk; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"

        self._proxy_to_workkk(
            method, upstream_path, query_string,
            rewrite_html=(upstream_path == "/"), set_cookie=set_cookie,
            # 身份以服务端校验过的绑定 player 为准，杜绝客户端伪造 player/X-Player-Id 覆盖他人存档
            force_player=(None if is_static else player),
            activity_user=(None if is_static else user),
        )

    def _rewrite_workkk_html(self, raw):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
        # 大屏 JS 用绝对路径请求后端；经 /workkk/ 代理后需补前缀。
        text = text.replace(
            "return path + (path.indexOf('?')",
            "return '/workkk' + path + (path.indexOf('?')",
        )
        text = text.replace('src="/static/', 'src="/workkk/static/')
        return text.encode("utf-8")

    def _proxy_to_workkk(self, method, upstream_path, query_string, rewrite_html=False, set_cookie=None, force_player=None, activity_user=None):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        params.pop("token", None)  # 不把人类 JWT 透传给 vendor 进程
        if force_player is not None:
            # 覆盖客户端传入的任何 player，只认服务端校验过的绑定身份
            params["player"] = [force_player]
        fwd_query = urllib.parse.urlencode(
            [(key, value) for key, values in params.items() for value in values]
        )
        target = upstream_path + (f"?{fwd_query}" if fwd_query else "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in ("host", "cookie", "authorization", "x-player-id")
        }
        headers["Host"] = "workkk.local"
        headers["X-Forwarded-For"] = self.client_address[0] if self.client_address else "unknown"
        if force_player is not None:
            # 后端 _player_id_from_request 以 X-Player-Id 头优先，这里强制写成校验过的身份，
            # 客户端自带的 X-Player-Id 已在上面被剔除，无法伪造
            headers["X-Player-Id"] = force_player
        conn = http.client.HTTPConnection(WORKKK_HOST, WORKKK_PORT, timeout=60)
        try:
            conn.request(method, target, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status, reason = resp.status, resp.reason
            resp_headers = resp.getheaders()
            content_type = resp.getheader("Content-Type", "") or ""
        except Exception as exc:
            self._send_json({"error": "workkk 代理失败", "detail": str(exc)}, status=502)
            return
        finally:
            conn.close()
        _record_web_game_activity("workkk", method, upstream_path, status, raw, activity_user, body)
        if rewrite_html and "text/html" in content_type.lower():
            raw = self._rewrite_workkk_html(raw)
        try:
            self.send_response(status, reason)
            for key, value in resp_headers:
                lower = key.lower()
                if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                    continue
                self.send_header(key, value)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
        except BrokenPipeError:
            pass

    # ── Duel 人类玩家代理（/duel/* → 127.0.0.1:8772）────────────────────────
    def _duel_cookie_value(self, cookie_name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == cookie_name:
                return urllib.parse.unquote(value)
        return None

    def _duel_human_context(self, raw_token):
        """Return the logged-in human and every AI currently bound to them."""
        human = _current_account(raw_token)
        if human.get("is_ai"):
            raise _McpError(-32001, "需要人类账号")
        with _db_connect() as conn:
            rows = conn.execute(
                """
                SELECT ai.id, ai.username, ai.is_ai,
                       ai.avatar_type, ai.avatar_value
                FROM user_bindings AS binding
                JOIN toy_users AS ai ON ai.id = binding.ai_user_id
                WHERE binding.human_user_id = ?
                  AND ai.is_ai = 1
                  AND ai.deleted_at IS NULL
                ORDER BY ai.username, ai.id
                """,
                (int(human["id"]),),
            ).fetchall()
        return {
            "human_player": str(int(human["id"])),
            "human_name": str(human["username"]),
            "human_avatar": _public_avatar(human),
            "machines": [
                {
                    "id": _account_slot_player_id(row["id"], MIN_SAVE_SLOT),
                    "name": str(row["username"]),
                    "avatar": _public_avatar(dict(row)),
                }
                for row in rows
            ],
        }

    def _handle_duel_proxy(self, method):
        full = self.path
        path = full.split("?", 1)[0]
        query_string = full.partition("?")[2]
        public_path = path[len("/duel"):] or "/"
        if not _duel_proxy_allowed(method, public_path):
            self._send_json({"error": "not found"}, status=404)
            return

        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        web_ticket_values = params.get("web_ticket") or []
        if web_ticket_values:
            if method != "GET" or public_path != "/" or len(web_ticket_values) != 1:
                self._send_json({"error": "网页登录票据只能用于 /duel/"}, status=400)
                return
            try:
                human = _consume_operit_web_ticket(web_ticket_values[0])
            except _McpError as exc:
                self._send_json(
                    {"error": exc.message, "code": 401},
                    status=401,
                    extra_headers={"Cache-Control": "no-store"},
                )
                return
            human_token = _create_account_jwt(human)
            cookie = (
                f"duel_token={urllib.parse.quote(human_token, safe='')}; "
                f"Path=/duel; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"
            )
            self.send_response(303)
            self.send_header("Location", "/duel/")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        token_from_query = (params.get("token") or [None])[0]
        token = (
            token_from_query
            or self._duel_cookie_value("duel_token")
            or _extract_bearer(self.headers)
        )
        token_user_id = _path_token_user_id(token)
        rate_identity = (
            f"user:{token_user_id}"
            if token_user_id is not None
            else f"ip:{self._client_ip()}"
        )
        # /duel/ 页面三秒轮询走自己的 120 次/分钟池，不能挤占全站
        # MCP 的 60 次/分钟配额，避免返回首页时被误判为登出。
        if not _check_duel_web_request_rate_limit(rate_identity):
            self._send_json(
                {"error": REQUEST_RATE_LIMIT_MESSAGE, "code": 429},
                status=429,
            )
            return

        is_static = public_path.startswith("/static/")
        set_cookies = []
        target = None
        if not is_static:
            try:
                target = self._duel_human_context(token)
            except _McpError as exc:
                if method == "GET" and public_path == "/":
                    self._proxy_to_duel(method, public_path, query_string, rewrite_html=True)
                    return
                self._send_json(
                    {"error": exc.message or "未登录，请先在首页登录", "code": 401},
                    status=401,
                )
                return
            if token_from_query:
                set_cookies.append(
                    f"duel_token={urllib.parse.quote(token_from_query)}; "
                    f"Path=/duel; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"
                )

        self._proxy_to_duel(
            method,
            public_path,
            query_string,
            set_cookies=set_cookies,
            target=target,
            rewrite_html=(public_path in {"/", "/chips"}),
        )

    def _proxy_to_duel(
        self, method, upstream_path, query_string, set_cookies=None,
        target=None, rewrite_html=False,
    ):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        for untrusted in (
            "token", "web_ticket", "player", "player_id", "opponent_id"
        ):
            params.pop(untrusted, None)
        if target is not None and method == "GET" and upstream_path.startswith("/api/"):
            params["player_id"] = [target["human_player"]]
        fwd_query = urllib.parse.urlencode(
            [(key, value) for key, values in params.items() for value in values]
        )
        request_target = upstream_path + (f"?{fwd_query}" if fwd_query else "")

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length) if length > 0 else b""
        body = raw_body or None
        if target is not None and method == "POST":
            try:
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "请求体必须是 JSON 对象"}, status=400)
                return
            if not isinstance(payload, dict):
                self._send_json({"error": "请求体必须是 JSON 对象"}, status=400)
                return
            payload.pop("opponent_id", None)
            payload.pop("player_id", None)
            if not _duel_trusted_header_post_allowed(upstream_path):
                payload["player_id"] = target["human_player"]
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in {
                "host", "cookie", "authorization", "content-length",
                "x-player-id", "x-duel-human-player", "x-duel-ai-player",
                "x-duel-human-name", "x-duel-ai-name", "x-duel-bound-ais",
                "x-duel-human-avatar", "x-forwarded-prefix",
            }
        }
        headers["Host"] = "duel.local"
        headers["X-Forwarded-For"] = (
            self.client_address[0] if self.client_address else "unknown"
        )
        headers["X-Forwarded-Prefix"] = "/duel"
        if target is not None:
            headers["X-Duel-Human-Player"] = target["human_player"]
            # Header values must remain latin-1 safe; names are percent/base64 encoded.
            headers["X-Duel-Human-Name"] = urllib.parse.quote(
                target["human_name"], safe=""
            )
            human_avatar_json = json.dumps(
                target["human_avatar"], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["X-Duel-Human-Avatar"] = (
                base64.urlsafe_b64encode(human_avatar_json)
                .decode("ascii")
                .rstrip("=")
            )
            machine_json = json.dumps(
                target["machines"], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["X-Duel-Bound-Ais"] = (
                base64.urlsafe_b64encode(machine_json).decode("ascii").rstrip("=")
            )

        conn = http.client.HTTPConnection(DUEL_HOST, DUEL_PORT, timeout=55)
        try:
            conn.request(method, request_target, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status, reason = resp.status, resp.reason
            resp_headers = resp.getheaders()
            content_type = resp.getheader("Content-Type", "") or ""
        except Exception as exc:
            self._send_json({"error": "duel 代理失败", "detail": str(exc)}, status=502)
            return
        finally:
            conn.close()

        if target and method == "POST" and 200 <= status < 300:
            action = "new" if upstream_path == "/api/rooms" else upstream_path.rsplit("/", 1)[-1]
            if action in {"new", "invitation", "join", "move", "resign", "leave"}:
                try:
                    result = json.loads(raw)
                except (ValueError, TypeError):
                    result = None
                game_activity.record(SESSIONS_DB_PATH, "duel", action,
                                     {"id": target["human_player"], "is_ai": False}, result)

        if rewrite_html and "text/html" in content_type.lower():
            raw = raw.replace(b'="/static/', b'="/duel/static/')
        try:
            self.send_response(status, reason)
            for key, value in resp_headers:
                lower = key.lower()
                if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                    continue
                self.send_header(key, value)
            for cookie in set_cookies or ():
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
        except BrokenPipeError:
            pass

    # ── Garden-Cat 人类玩家代理（/garden-cat/* → 127.0.0.1:8771）────────────
    def _garden_cat_cookie_token(self):
        return satellite_proxy.cookie_value(self.headers.get("Cookie", ""), "garden_cat_token")

    def _garden_cat_bound_target(self, user, requested_player):
        """Return the canonical bound player id, machine name, and slot."""
        if not user or user.get("is_ai"):
            return None
        parts = str(requested_player or "").split(":")
        if len(parts) > 2 or not parts[0].isdigit():
            return None
        try:
            ai_user_id = int(parts[0])
            slot = int(parts[1]) if len(parts) == 2 else MIN_SAVE_SLOT
        except (TypeError, ValueError):
            return None
        if slot < MIN_SAVE_SLOT or slot > MAX_SAVE_SLOT:
            return None
        with _db_connect() as conn:
            row = conn.execute(
                """
                SELECT ai.id, ai.username
                FROM user_bindings b
                JOIN toy_users ai ON ai.id = b.ai_user_id
                WHERE b.human_user_id = ?
                  AND b.ai_user_id = ?
                  AND ai.is_ai = 1
                  AND ai.deleted_at IS NULL
                LIMIT 1
                """,
                (int(user["id"]), ai_user_id),
            ).fetchone()
        if not row:
            return None
        return {
            "player": _account_slot_player_id(int(row["id"]), slot),
            "owner_name": row["username"],
            "slot": slot,
        }

    def _handle_garden_cat_proxy(self, method):
        full = self.path
        path = full.split("?", 1)[0]
        query_string = full.partition("?")[2]
        public_path = path[len("/garden-cat"):] or "/"
        if not _garden_cat_proxy_allowed(method, public_path):
            self._send_json({"error": "not found"}, status=404)
            return

        is_static = public_path.startswith("/static/")
        set_cookie = None
        target = None
        if not is_static:
            params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            token_from_query = (params.get("token") or [None])[0]
            token = token_from_query or self._garden_cat_cookie_token() or _extract_bearer(self.headers)
            try:
                user = _current_account(token)
                if user.get("is_ai"):
                    raise _McpError(-32001, "需要人类账号")
            except _McpError:
                self._send_json({"error": "未登录，请先在首页登录", "code": 401}, status=401)
                return
            requested_player = (params.get("player") or [""])[0]
            target = self._garden_cat_bound_target(user, requested_player)
            if not target:
                self._send_json({"error": "你没有绑定这只小机或槽位无效", "code": 403}, status=403)
                return
            # 首次带 token 导航时下发会话 cookie，后续同源 fetch 自动携带鉴权。
            if token_from_query:
                set_cookie = (
                    f"garden_cat_token={token_from_query}; Path=/garden-cat; "
                    f"HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"
                )

        self._proxy_to_garden_cat(
            method,
            _garden_cat_upstream_path(public_path),
            query_string,
            set_cookie=set_cookie,
            target=target,
            activity_user=(None if is_static else user),
            human_name=(user.get("username") if not is_static else None),
        )

    def _proxy_to_garden_cat(
        self, method, upstream_path, query_string, set_cookie=None, target=None,
        human_name=None, activity_user=None,
    ):
        request_target = satellite_proxy.request_target(upstream_path, query_string)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = satellite_proxy.garden_cat_request_headers(
            self.headers, hop_by_hop_headers=HOP_BY_HOP_HEADERS,
            forwarded_for=self.client_address[0] if self.client_address else "unknown",
            prefix=getattr(self, "_gc_prefix_override", "/garden-cat"),
            target=target, human_name=human_name,
        )

        conn = http.client.HTTPConnection(GARDEN_CAT_HOST, GARDEN_CAT_PORT, timeout=60)
        try:
            conn.request(method, request_target, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status, reason = resp.status, resp.reason
            resp_headers = resp.getheaders()
        except Exception as exc:
            self._send_json({"error": "Garden-Cat 代理失败", "detail": str(exc)}, status=502)
            return
        finally:
            conn.close()
        _record_web_game_activity("garden_cat", method, upstream_path, status, raw, activity_user, body)
        try:
            self.send_response(status, reason)
            for key, value in resp_headers:
                lower = key.lower()
                if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                    continue
                self.send_header(key, value)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
        except BrokenPipeError:
            pass

    # ── Camping Plaza 人类入口（/camping-plaza/* → 127.0.0.1:8773）─────────
    def _camping_plaza_cookie(self, name):
        return satellite_proxy.cookie_value(self.headers.get("Cookie", ""), name)

    def _handle_camping_plaza_proxy(self, method):
        full = self.path
        path = full.split("?", 1)[0]
        query_string = full.partition("?")[2]
        public_path = path[len("/camping-plaza"):] or "/"
        maintenance = _game_maintenance("camping_plaza")
        if maintenance:
            self._send_json(
                {"error": maintenance["message"], "maintenance": True},
                status=503,
            )
            return
        if not _camping_plaza_proxy_allowed(method, public_path):
            self._send_json({"error": "not found"}, status=404)
            return

        is_static = public_path.startswith(("/styles/", "/scripts/", "/assets/"))
        set_cookies = []
        target = None
        if not is_static:
            params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            token_from_query = (params.get("token") or [None])[0]
            token = (
                token_from_query
                or self._camping_plaza_cookie("camping_plaza_token")
                or _extract_bearer(self.headers)
            )
            try:
                user = _current_account(token)
                if user.get("is_ai"):
                    raise _McpError(-32001, "需要人类账号")
            except _McpError:
                self._send_json({"error": "未登录，请先在首页登录", "code": 401}, status=401)
                return
            requested_player = (
                (params.get("player") or [None])[0]
                or self._camping_plaza_cookie("camping_plaza_player")
                or ""
            )
            target = self._garden_cat_bound_target(user, requested_player)
            if not target:
                self._send_json({"error": "你没有绑定这只小机或槽位无效", "code": 403}, status=403)
                return
            if token_from_query:
                set_cookies.extend([
                    f"camping_plaza_token={token_from_query}; Path=/camping-plaza; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}",
                    f"camping_plaza_player={urllib.parse.quote(requested_player)}; Path=/camping-plaza; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}",
                ])

        self._proxy_to_camping_plaza(
            method,
            public_path,
            query_string,
            set_cookies=set_cookies,
            target=target,
            activity_user=(None if is_static else user),
        )

    def _proxy_to_camping_plaza(
        self, method, upstream_path, query_string, set_cookies=None, target=None, activity_user=None,
    ):
        request_target = satellite_proxy.request_target(upstream_path, query_string)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = satellite_proxy.camping_plaza_request_headers(
            self.headers, hop_by_hop_headers=HOP_BY_HOP_HEADERS,
            forwarded_for=self.client_address[0] if self.client_address else "unknown",
            target=target,
        )

        conn = http.client.HTTPConnection(CAMPING_PLAZA_HOST, CAMPING_PLAZA_PORT, timeout=60)
        try:
            conn.request(method, request_target, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            status, reason = response.status, response.reason
            response_headers = response.getheaders()
        except Exception as exc:
            self._send_json({"error": "Camping Plaza 代理失败", "detail": str(exc)}, status=502)
            return
        finally:
            conn.close()
        _record_web_game_activity("camping_plaza", method, upstream_path, status, raw, activity_user, body)

        raw = satellite_proxy.rewrite_camping_plaza_response(raw, upstream_path, status)
        try:
            self.send_response(status, reason)
            for key, value in response_headers:
                lower = key.lower()
                if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                    continue
                self.send_header(key, value)
            for cookie in set_cookies or ():
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
        except BrokenPipeError:
            pass


def _json_rpc_error(request_id, code, message):
    return web_responses._json_rpc_error(
        request_id, code, message,
    )


_JWT_LOG_VALUE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]*(?:\.|%2[eE])[A-Za-z0-9_-]+(?:\.|%2[eE])[A-Za-z0-9_-]+"
)
_OPAQUE_AI_LOG_VALUE_RE = re.compile(r"ctai_v1_[A-Za-z0-9_-]{40,}")
_OPERIT_LOG_VALUE_RE = re.compile(r"cto(?:p|w)_v1_[A-Za-z0-9_-]{30,}")
_SENSITIVE_QUERY_LOG_RE = re.compile(
    r"([?&](?:token|web_ticket|reset_token|access_token|connection)=)[^&#\s\"]+",
    re.IGNORECASE,
)
_DETROIT_CONNECTION_LOG_RE = re.compile(r"dbr_[A-Za-z0-9_-]{16,}")


def _redact_http_log_text(value):
    text = str(value)
    text = _OPAQUE_AI_LOG_VALUE_RE.sub("<TOKEN_REDACTED>", text)
    text = _OPERIT_LOG_VALUE_RE.sub("<TOKEN_REDACTED>", text)
    text = _JWT_LOG_VALUE_RE.sub("<TOKEN_REDACTED>", text)
    text = _DETROIT_CONNECTION_LOG_RE.sub("<TOKEN_REDACTED>", text)
    return _SENSITIVE_QUERY_LOG_RE.sub(r"\1<TOKEN_REDACTED>", text)


class ThreadPoolHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, max_workers=MAX_WORKERS):
        return http_server.init_thread_pool(
            self, server_address, RequestHandlerClass, max_workers,
            BoundedSemaphore=BoundedSemaphore,
            ThreadPoolExecutor=ThreadPoolExecutor,
            base_init=super().__init__,
        )

    def process_request(self, request, client_address):
        return http_server.process_request(
            self, request, client_address,
            QUEUE_TIMEOUT_SECONDS=QUEUE_TIMEOUT_SECONDS,
        )

    def _process_request_thread(self, request, client_address):
        return http_server.process_request_thread(
            self, request, client_address,
        )

    def server_close(self):
        return http_server.close_thread_pool(
            self,
            base_close=super().server_close,
        )

    @staticmethod
    def _send_busy(request):
        return http_server.send_busy(
            request,
        )


def main():
    return http_server.main(
        CedarToyHandler=CedarToyHandler,
        HOST=HOST,
        MAX_WORKERS=MAX_WORKERS,
        PORT=PORT,
        TURTLE_DB_PATH=TURTLE_DB_PATH,
        Thread=Thread,
        ThreadPoolHTTPServer=ThreadPoolHTTPServer,
        _init_announcement_tables=_init_announcement_tables,
        _migrate_platform_timestamps=_migrate_platform_timestamps,
        avatar_appearances=avatar_appearances,
        time=time,
    )


if __name__ == "__main__":
    main()
