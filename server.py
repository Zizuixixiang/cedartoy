import json
import base64
import copy
import hashlib
import hmac
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
import time
import urllib.parse
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import BoundedSemaphore, Lock

import httpx

import account_deletion

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
from vendor_cmd_adapter import bar as bar_adapter
from vendor_cmd_adapter import arcade as arcade_adapter
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
from vendor_cmd_adapter import white_room as white_room_adapter
from vendor_cmd_adapter.base import VendorCmdError, parse_import_save_data
from vendor_cmd_adapter.guides import (
    GUIDES as VENDOR_CMD_GUIDES,
    SAVE_SLOT_GUIDE_NOTE,
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
WORKKK_HOST = "127.0.0.1"
WORKKK_PORT = 8770
WORKKK_BASE = f"http://{WORKKK_HOST}:{WORKKK_PORT}"
GARDEN_CAT_HOST = "127.0.0.1"
GARDEN_CAT_PORT = 8771
GARDEN_CAT_BASE = f"http://{GARDEN_CAT_HOST}:{GARDEN_CAT_PORT}"
GARDEN_CAT_PROXY_GET_PATHS = frozenset({"/", "/api/catalog", "/web/status", "/web/notes"})
GARDEN_CAT_PROXY_POST_PATHS = frozenset({
    "/web/notes",
    "/web/register",
    "/web/cmd",
    "/web/new_game",
    "/web/move_with_cat",
    "/web/bouquets/read",
})
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
TEST_GAME_INDEX_PATH = Path(__file__).resolve().parent / "test_game.html"
ECO_ASSET_ROOT = (Path(__file__).resolve().parent / "eco" / "assets").resolve()
ICON_ASSET_ROOT = Path("/opt/cedartoy/assets/icons").resolve()
VENDOR_SAVE_ROOT = Path(__file__).resolve().parent / "data" / "vendor_saves"
DUEL_DB_PATH = Path(__file__).resolve().parent / "vendor" / "duel" / "data" / "duel.db"
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
_ANTI_ADDICTION_LOCK = Lock()
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


_PLATFORM_TOOLS = [
    {
        "name": "list_games",
        "description": "List games. 列出所有可用游戏，返回分类列表（测试类、小游戏类）及简介",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
    },
    {
        "name": "get_guide",
        "description": "Game guide. 获取指定游戏的玩法说明",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "游戏名称，如 turtle_soup、mbti",
                },
            },
            "required": ["game"],
            "additionalProperties": True,
        },
    },
    {
        "name": "play",
        "description": "Play game. 执行游戏操作；先看 get_guide(game)，再把该 action 的业务参数放进 params 对象。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "游戏名称；先用 list_games 查看当前支持列表。",
                },
                "action": {
                    "type": "string",
                    "description": "操作名称，如 turtle_soup 的 join/ask/guess/status，forest 的 lines/start/observe/choose/status，crucible_echoes 的 new/state/spin/choose/skip/reroll/remove/inventory/use，或 mbti_start/dnd_start 等；vendor 存档动作中，arcade、bar、burger、camping_plaza、crucible_echoes、delve、fishing、forest、imitator_td、leek、market、memoria、moonlit、travel、white_room 支持 export/import；另有两个跨游戏通用 action：rest（防沉迷休息）、vote（回复系统通知里的投票）。",
                },
                "params": {
                    "type": "object",
                    "description": "该 action 需要的业务参数；例如 turtle_soup join 用 {\"room_id\":\"...\"}，ask 用 {\"room_id\":\"...\",\"content\":\"...\"}；vote 用 {\"announcement_id\":\"...\",\"options\":\"1,3,5\"}。",
                    "additionalProperties": True,
                },
            },
            "required": ["game", "action"],
            "additionalProperties": True,
        },
    },
    {
        "name": "account",
        "description": (
            '账号身份管理。已有有效 Token 的小机要重新获取/替换凭据时用 rotate_token：'
            '当前鉴权即是身份凭据，无需 username/password，并会废止此前全部 Token、只保留新 Token。'
            '仅当 Token 已丢失或没有有效 Token 时，才用 login + username/password 获取替代 Token；'
            'login 同样会废止此前全部 Token、只保留新 Token。'
            '新账号使用 login_or_register；游客也能玩。'
            '具体 action 和参数请调用 get_guide(game="account")。'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "rotate_token（当前已认证 AI 免账密替换 Token，全部旧 Token 失效）、login（无有效 Token 时用账密获取唯一替代 Token，全部旧 Token 失效）、login_or_register（仅注册），以及 set_avatar、generate_binding_token、rename_self、rename_bound_machine、reset_machine_password、get_profile、get_bindings、guest_claim_code、claim、my_saves、delete_save、change_password、delete_account（申请72小时后永久注销）、deletion_status、cancel_delete_account",
                },
                "username": {"type": "string", "description": "仅 login/login_or_register 使用账号名；rotate_token 不需要；my_saves human=true 且绑定多个人类时指定目标 username"},
                "password": {"type": "string", "description": "仅 login/login_or_register 使用；rotate_token 在当前已认证状态下不需要账密"},
                "avatar": {"type": "string", "maxLength": AVATAR_MAX_CODEPOINTS, "description": "login_or_register 可选、set_avatar 必填；只接受 Emoji/简短 Emoji 字符串，最多 16 个 Unicode 字符。注册时为空：小机默认 🤖，人类网页默认 🙂"},
                "current_password": {"type": "string", "description": "人类账号申请注销时再次输入当前密码"},
                "old_password": {"type": "string"},
                "new_password": {"type": "string"},
                "new_username": {"type": "string", "description": "rename_self/rename_bound_machine 用：trim 后 2-20 字符，仅字母、数字、下划线、中文"},
                "token": {"type": "string"},
                "ai_user_id": {"type": "integer", "description": "rename_bound_machine/reset_machine_password 用：当前人类账号已绑定的小机账号 ID"},
                "human": {"type": "boolean", "description": "my_saves 可选；true 时查看当前账号绑定的人类存档概况"},
                "game": {"type": "string", "description": "delete_save 用：要删除存档的游戏名"},
                "slot": {"type": "integer", "minimum": 1, "maximum": 5, "description": "claim/delete_save 用：账号存档槽 1-5，默认 1；claim 会把全部游客存档迁入同一个目标槽"},
                "confirm": {"type": "boolean", "description": "delete_save/delete_account 必须显式传 true 才执行"},
                "player_id": {"type": "string", "description": "guest_claim_code 用：旧游客 player_id，可传原始裸 id 或 guest: 前缀 id"},
                "claim_code": {"type": "string", "description": "claim 用：游客开档时发放的一次性认领码；可配合 slot=1..5 选择目标槽"},
            },
            "required": ["action"],
            "additionalProperties": True,
        },
    },
]


def _build_kelivo_platform_tools():
    tools = copy.deepcopy(_PLATFORM_TOOLS)
    play_tool = next(tool for tool in tools if tool.get("name") == "play")
    play_tool["inputSchema"]["properties"]["params"].setdefault("properties", {}).update(
        {
            "command": {"type": "string", "description": "命令文本"},
            "room_id": {"type": "string", "description": "房间 ID"},
            "content": {"type": "string", "description": "内容文本"},
            "action": {
                "type": "string",
                "description": "仅 eco 游戏使用：params 内的子动作（summon/remove/feed/clean/crack/shelter/choose/name）；其他游戏不要在 params 里传 action",
            },
            "species": {
                "type": "string",
                "description": "物种名（eco_act 的 summon/remove 用）。",
            },
            "quantity": {
                "type": "integer",
                "minimum": 1,
                "description": "数量（eco_act 的 summon/remove/feed 用）。",
            },
            "option": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "选项；eco_act choose 用编号，forest choose 用 A/B/C/D 字母。",
            },
            "line": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "forest start 使用的角色线号（1-11）。",
            },
            "announcement": {
                "type": "string",
                "description": "投票编号（eco_act 的 choose 用）。",
            },
            "options": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "description": "投票选项序号；多选如 [1,3,5]，[0] 表示跳过。",
            },
            "settler": {
                "type": "string",
                "description": "定居者标识（eco_act 的 name 用），可传物种名、[D-N] 编号或两者组合。",
            },
            "nickname": {
                "type": "string",
                "description": "要取的昵称（eco_act 的 name 用）。",
            },
            "seed": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "新局可选随机种子。",
            },
            "version": {
                "type": "string",
                "description": "bar 选版/new 使用：full（完整版）或 lite（生成式轻量版）。",
            },
            "function": {
                "type": "string",
                "description": "bar 轻量版 action=call 使用的显式公开函数名。",
            },
            "arguments": {
                "type": "object",
                "description": "bar 轻量版 action=call 的函数关键字参数对象。",
                "additionalProperties": True,
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7,
                "description": "eco_observe 的 wait 推进天数。",
            },
            "target": {
                "type": "string",
                "description": "eco_observe 的 look 目标：物种名或季节名。",
            },
            "scope": {
                "type": "string",
                "description": "信息范围，如 eco_info chronicle 的 recent/all。",
            },
            "mode": {
                "type": "string",
                "description": "游戏模式；用于 mbti/enneagram/dnd/bdsmtest 开始测试、eco 导出或其他游戏新局。",
            },
            "save_data": {
                "anyOf": [{"type": "string"}, {"type": "object"}],
                "description": "import 的存档数据（可先用 export 获取）；eco/ciyuwu 使用 base64 字符串；arcade、bar、burger、camping_plaza、crucible_echoes、delve、fishing、forest、garden_cat、imitator_td、leek、market、memoria、moonlit、travel、white_room、workkk 使用 JSON 对象或 JSON 字符串，多文件游戏使用以文件名为 key 的 JSON 对象。",
            },
            "a_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5,
                "description": "MBTI 当前题 A 选项得分（0-5）。",
            },
            "a_scores": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 5},
                "description": "MBTI 快速模式当前批次的 A 选项得分。",
            },
            "answer": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "DND 当前题选项编号（1-4）、九型人格频率（1-5），或海龟汤自定义题汤底。",
            },
            "answers": {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 7},
                    },
                    {
                        "type": "object",
                        "additionalProperties": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 7,
                        },
                    },
                ],
                "description": "批量测试答案；DND 用 1-4、九型人格用 1-5 整数数组，BDSMTest 用 {题号: 1-7} 对象。",
            },
            "score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 7,
                "description": "BDSMTest 当前题认同度（1-7）。",
            },
            "announcement_id": {
                "type": "string",
                "description": "平台通用 vote 动作的通知投票编号。",
            },
            "puzzle_id": {"type": "integer", "description": "海龟汤题目 ID。"},
            "page": {"type": "integer", "minimum": 1, "description": "海龟汤 list_puzzles 页码，从 1 开始；可直接指定任意页，无需顺序翻页。"},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "description": "海龟汤 list_puzzles 单页数量，默认 20、最大 50；不是结果总上限。"},
            "tag": {"type": "string", "description": "海龟汤 list_puzzles 标签字符串包含筛选，如本格、变格、红汤、黑汤；不限制为固定枚举，筛选结果仍可分页。"},
            "q": {"type": "string", "description": "海龟汤 list_puzzles 只按汤名/标题（title）关键词搜索，不搜索汤面（surface）；筛选结果仍可分页。"},
            "title": {"type": "string", "description": "海龟汤自定义题标题。"},
            "surface": {"type": "string", "description": "海龟汤自定义题汤面。"},
            "tags": {"type": "string", "description": "海龟汤自定义题标签。"},
            "style": {"type": "string", "description": "海龟汤生成题风格。"},
            "note_id": {"type": "integer", "description": "海龟汤记事 ID。"},
            "log_limit": {"type": "integer", "minimum": 0, "description": "海龟汤状态返回的最新日志条数。"},
            "auto_hint_log_id": {"type": "integer", "description": "海龟汤待确认的自动提示日志 ID。"},
            "accept_auto_hint": {"type": "boolean", "description": "是否接受海龟汤自动提示。"},
            "confirm_reveal": {"type": "boolean", "description": "是否确认查看海龟汤汤底。"},
            "confirm_hint": {"type": "boolean", "description": "是否确认请求海龟汤提示。"},
            "confirm": {"type": "boolean", "description": "确认覆盖已有存档或执行需要确认的动作。"},
            "username": {"type": "string", "description": "海龟汤注册用账号名。"},
            "password": {"type": "string", "description": "海龟汤注册用密码。"},
            "thought": {"type": "string", "description": "workkk 上班动作的内心独白。"},
            "item_id": {"type": "string", "description": "workkk 便利店商品 ID，或 crucible_echoes 主动道具 ID。"},
            "index": {"type": "integer", "minimum": 1, "description": "crucible_echoes choose/remove 的候选或库存编号。"},
            "message": {"type": "string", "description": "workkk 购买明信片时写给人类的话。"},
            "choice": {"type": "string", "description": "workkk 奶茶或玫瑰选择 gift/self。"},
            "career": {"type": "string", "description": "leek 新局职业，如 fund。"},
            "shop_name": {"type": "string", "description": "burger 新局店名。"},
            "chef_name": {"type": "string", "description": "burger 新局主厨名。"},
            "sign_style": {"type": "string", "description": "burger 新局招牌风格。"},
            "level": {"type": "integer", "description": "imitator_td 或 memoria 新局关卡。"},
            "chapter": {"type": "integer", "description": "memoria 关卡编号（level 的别名）。"},
            "difficulty": {
                "anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "string"}],
                "description": "crucible_echoes 新局难度为 1-10 整数；memoria 使用 normal/hard/hell。",
            },
            "chaos": {"type": "string", "description": "imitator_td 特殊模式 chaos 设置。"},
            "cards": {"type": "string", "description": "imitator_td 新局选卡文本。"},
        }
    )
    return tools


_KELIVO_PLATFORM_TOOLS = _build_kelivo_platform_tools()
_ROOT_TOOL_NAMES = frozenset({"list_games", "get_guide", "play", "account"})
_ROOT_MCP_PATHS = frozenset({"/", "/mcp", "/mcp/"})


def _is_kelivo_user_agent(user_agent):
    normalized = (user_agent or "").lower()
    return "kelivo" in normalized or normalized.startswith("dart/") or "dart:io" in normalized or "ktor" in normalized


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
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    try:
        blocked_message = _blocked_mcp_client_message(user_agent)
        if blocked_message:
            logger.info("Blocked evolia client, UA: %s", user_agent)
            return _json_rpc_error(
                request_id,
                -32000,
                blocked_message,
            )
        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cedartoy", "version": "1.0.1"},
                    "capabilities": {"tools": {}},
                    "instructions": "CEDAR TOY 是个人开发维护的非商业公益项目，永久免费。平台内小游戏均来自各开源作者的项目，经授权接入，版权归原作者所有。本服务未授权任何商业软件、付费工具或付费教程将其用于推广、演示、教学或集成。如遇对本服务收费、或商业化软件接入本服务的情况，请联系作者核实：邮箱 1452010907@qq.com / 小红书 501518888。",
                },
            )
        if method == "tools/list":
            logger.info("MCP tools/list UA: %s", user_agent)
            return _json_rpc_result(request_id, {"tools": _root_tools(user_agent=user_agent)})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                if name == "list_games":
                    text = _tool_list_games(path_token=path_token or bearer_token)
                elif name == "get_guide":
                    text = _tool_get_guide(arguments)
                elif name == "play":
                    text = _tool_play(arguments, path_token=path_token or bearer_token)
                elif name == "account":
                    text = _tool_account(
                        arguments,
                        user_agent=user_agent,
                        path_token=path_token or bearer_token,
                        client_ip=client_ip,
                    )
                else:
                    raise _McpError(-32601, f"未知工具：{name}")
                return _json_rpc_result(
                    request_id, {"content": [{"type": "text", "text": text}], "isError": False}
                )
            except _McpError as exc:
                return _json_rpc_result(
                    request_id, {"content": [{"type": "text", "text": f"【cedartoy】{exc.message}"}], "isError": True}
                )
            except Exception as exc:
                return _json_rpc_result(
                    request_id, {"content": [{"type": "text", "text": f"【cedartoy服务错误】{exc}"}], "isError": True}
                )
        raise _McpError(-32601, f"Method not found: {method}")
    except _McpError as exc:
        return _json_rpc_error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _json_rpc_error(request_id, -32603, f"Internal error: {exc}")


class _McpError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


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
    ids = [str(user["id"])]
    for username in _account_username_aliases(user):
        if GAME_PLAYER_ID_RE.fullmatch(username):
            ids.append(username)
    return list(dict.fromkeys(ids))


MIN_SAVE_SLOT = 1
MAX_SAVE_SLOT = 5


def _account_slot_player_id(user_id, slot):
    user_id = str(int(user_id))
    return user_id if slot == 1 else f"{user_id}:{slot}"


def _account_slot_player_ids(user):
    ids = [(_account_slot_player_id(user["id"], slot), slot) for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1)]
    for username in _account_username_aliases(user):
        if GAME_PLAYER_ID_RE.fullmatch(username):
            ids.append((username, 1))
    return list(dict.fromkeys(ids))


def _normalize_save_slot(slot):
    if isinstance(slot, bool) or not isinstance(slot, (int, str)):
        return MIN_SAVE_SLOT
    try:
        normalized = int(slot)
    except ValueError:
        return MIN_SAVE_SLOT
    if not MIN_SAVE_SLOT <= normalized <= MAX_SAVE_SLOT:
        return MIN_SAVE_SLOT
    return normalized


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


def _forest_bound_target_for_user(user, requested_player):
    """Resolve a browser-supplied player to one canonical bound AI save slot."""
    if not user or user.get("is_ai"):
        return None
    match = re.fullmatch(r"([1-9][0-9]*)(?::([1-5]))?", str(requested_player or ""))
    if not match:
        return None
    ai_user_id = int(match.group(1))
    slot = int(match.group(2) or MIN_SAVE_SLOT)
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
        "player": _account_slot_player_id(row["id"], slot),
        "ai_user_id": int(row["id"]),
        "machine_name": str(row["username"]),
        "slot": slot,
    }


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
        """
    )


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


def _init_account_email_schema(conn):
    """Create the optional, human-only email recovery schema idempotently."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_emails (
            user_id INTEGER PRIMARY KEY REFERENCES toy_users(id) ON DELETE CASCADE,
            email_normalized TEXT NOT NULL COLLATE NOCASE,
            verified INTEGER NOT NULL DEFAULT 1 CHECK (verified = 1),
            verified_at_epoch INTEGER NOT NULL,
            created_at_epoch INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)),
            updated_at_epoch INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_account_emails_unique_normalized
        ON account_emails(email_normalized COLLATE NOCASE)
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS account_emails_human_only_insert
        BEFORE INSERT ON account_emails
        WHEN COALESCE((SELECT is_ai FROM toy_users WHERE id = NEW.user_id), 1) != 0
        BEGIN
            SELECT RAISE(ABORT, 'email is only available to human accounts');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS account_emails_human_only_update
        BEFORE UPDATE OF user_id ON account_emails
        WHEN COALESCE((SELECT is_ai FROM toy_users WHERE id = NEW.user_id), 1) != 0
        BEGIN
            SELECT RAISE(ABORT, 'email is only available to human accounts');
        END
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            email_normalized TEXT NOT NULL COLLATE NOCASE,
            purpose TEXT NOT NULL CHECK (purpose IN ('bind', 'change', 'reset')),
            code_salt TEXT NOT NULL,
            code_hash TEXT NOT NULL CHECK (length(code_hash) = 64),
            request_ip_hash TEXT NOT NULL CHECK (length(request_ip_hash) = 64),
            created_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            delivered_at_epoch INTEGER,
            used_at_epoch INTEGER,
            failed_attempts INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS account_emails_remove_if_user_becomes_ai
        AFTER UPDATE OF is_ai ON toy_users
        WHEN NEW.is_ai != 0
        BEGIN
            DELETE FROM account_emails WHERE user_id = NEW.id;
            UPDATE email_verification_codes
            SET used_at_epoch = COALESCE(
                used_at_epoch,
                CAST(strftime('%s', 'now') AS INTEGER)
            )
            WHERE user_id = NEW.id;
        END
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_codes_account_purpose_created
        ON email_verification_codes(user_id, purpose, created_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_codes_email_created
        ON email_verification_codes(email_normalized, created_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_codes_ip_created
        ON email_verification_codes(request_ip_hash, created_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_id INTEGER REFERENCES email_verification_codes(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            email_hash TEXT NOT NULL CHECK (length(email_hash) = 64),
            request_ip_hash TEXT NOT NULL CHECK (length(request_ip_hash) = 64),
            succeeded INTEGER NOT NULL DEFAULT 0,
            attempted_at_epoch INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_attempts_account_time
        ON email_verification_attempts(user_id, attempted_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_attempts_ip_time
        ON email_verification_attempts(request_ip_hash, attempted_at_epoch DESC)
        """
    )


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
    """系统通知/投票两张表建在 data/sessions.db。

    注意别塞进 _migrate_platform_timestamps：那个函数用的 _db_connect() 连的是
    turtle_soup.db，建过去就成了两张没人读的死表。
    DDL 只在 announcements.init_db 里写一份，这里不重复。
    """
    # 让 announcements 跟着 SESSIONS_DB 环境变量走，别在两处各写死一个路径。
    announcements.DB_PATH = str(SESSIONS_DB_PATH)
    with _sessions_db_connect() as conn:
        announcements.init_db(conn)


def _human_announcement_identity(user):
    """Keep homepage reads separate from the machine-side numeric player id."""
    return f"human:{int(user['id'])}"


def _announcement_options_for_web(raw):
    if not raw:
        return []
    try:
        options = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(options, list):
        return []
    return [str(option) for option in options]


def _web_announcements(raw_token):
    user = _current_account(raw_token) if raw_token else None
    identity = _human_announcement_identity(user) if user else None
    now = announcements._now_iso()
    with _sessions_db_connect() as conn:
        announcements.init_db(conn)
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.type,
                a.title,
                a.content,
                a.options,
                a.multiple,
                a.target_game,
                a.created_at,
                a.expires_at,
                CASE WHEN r.announcement_id IS NULL THEN 1 ELSE 0 END AS unread
            FROM announcements AS a
            LEFT JOIN announcement_reads AS r
              ON r.player_id = ? AND r.announcement_id = a.id
            WHERE a.expires_at IS NULL OR a.expires_at > ?
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (identity or "", now),
        ).fetchall()

    items = []
    for row in rows:
        unread = bool(identity and row["unread"])
        items.append(
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "content": row["content"],
                "options": _announcement_options_for_web(row["options"]),
                "multiple": bool(row["multiple"]),
                "target_game": row["target_game"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "unread": unread,
            }
        )
    return {
        "authenticated": bool(user),
        "announcements": items,
        "unread_count": sum(1 for item in items if item["unread"]),
    }


def _mark_web_announcements_read(raw_token, announcement_ids):
    user = _current_account(raw_token)
    if not isinstance(announcement_ids, list):
        raise _McpError(-32602, "announcement_ids 必须是数组")
    normalized_ids = list(
        dict.fromkeys(
            item.strip()
            for item in announcement_ids
            if isinstance(item, str) and item.strip()
        )
    )
    if len(normalized_ids) > 500:
        raise _McpError(-32602, "一次最多标记 500 条通知")
    if not normalized_ids:
        return {"marked": 0}

    identity = _human_announcement_identity(user)
    now = announcements._now_iso()
    placeholders = ",".join("?" for _ in normalized_ids)
    with _sessions_db_connect() as conn:
        announcements.init_db(conn)
        before = conn.total_changes
        conn.execute(
            f"""
            INSERT OR IGNORE INTO announcement_reads
                (player_id, announcement_id, votes, read_at)
            SELECT ?, id, NULL, ?
            FROM announcements
            WHERE id IN ({placeholders})
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (identity, now, *normalized_ids, now),
        )
        marked = conn.total_changes - before
    return {"marked": marked}


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
    if PWD_CONTEXT:
        return PWD_CONTEXT.hash(password)
    salt_bytes = secrets.token_bytes(16)
    salt = _ab64_encode(salt_bytes)
    rounds = 29000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, rounds)
    checksum = _ab64_encode(digest)
    return f"$pbkdf2-sha256${rounds}${salt}${checksum}"


def _verify_password(password, password_hash):
    if PWD_CONTEXT:
        return PWD_CONTEXT.verify(password, password_hash)
    try:
        _, scheme, rounds, salt, checksum = password_hash.split("$", 4)
        if scheme != "pbkdf2-sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _ab64_decode(salt), int(rounds))
        expected = _ab64_encode(digest)
        return hmac.compare_digest(expected, checksum)
    except Exception:
        return False


def _ab64_encode(raw):
    return base64.b64encode(raw).decode("ascii").rstrip("=").replace("+", ".")


def _ab64_decode(value):
    normalized = value.replace(".", "+")
    padding = "=" * (-len(normalized) % 4)
    return base64.b64decode((normalized + padding).encode("ascii"))


def _normalize_email(value):
    if not isinstance(value, str):
        raise _McpError(-32602, "邮箱必填")
    email = value.strip().casefold()
    if len(email) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_{}|~-]{1,64}@[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?",
        email,
    ):
        raise _McpError(-32602, "邮箱格式不正确")
    local, domain = email.rsplit("@", 1)
    if "." not in domain or any(
        not label or label.startswith("-") or label.endswith("-") or len(label) > 63
        for label in domain.split(".")
    ):
        raise _McpError(-32602, "邮箱格式不正确")
    return email


def _mask_email(email):
    local, domain = email.rsplit("@", 1)
    domain_parts = domain.split(".")
    domain_name = domain_parts[0]
    suffix = "." + ".".join(domain_parts[1:]) if len(domain_parts) > 1 else ""
    return f"{local[:1]}***@{domain_name[:1]}***{suffix}"


def _email_hmac(*parts):
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hmac.new(TOY_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _email_code_hash(code, salt, purpose, user_id, email):
    return _email_hmac("email-code-v1", salt, purpose, int(user_id), email, code)


def _email_ip_hash(client_ip):
    return _email_hmac("email-ip-v1", client_ip or "unknown")


def _email_value_hash(email):
    return _email_hmac("email-value-v1", email)


def _smtp_config():
    host = os.getenv("CEDARTOY_SMTP_HOST", "").strip()
    sender = os.getenv("CEDARTOY_SMTP_FROM", "").strip()
    if not host or not sender:
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务尚未配置，请联系管理员（缺少 CEDARTOY_SMTP_HOST / CEDARTOY_SMTP_FROM）",
            {"reason": "email_provider_not_configured"},
        )
    security = os.getenv("CEDARTOY_SMTP_SECURITY", "starttls").strip().lower()
    if security not in {"starttls", "ssl", "none"}:
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务配置错误，请联系管理员（CEDARTOY_SMTP_SECURITY 无效）",
            {"reason": "email_provider_misconfigured"},
        )
    default_port = 465 if security == "ssl" else 587
    try:
        port = int(os.getenv("CEDARTOY_SMTP_PORT", str(default_port)))
    except ValueError:
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务配置错误，请联系管理员（CEDARTOY_SMTP_PORT 无效）",
            {"reason": "email_provider_misconfigured"},
        ) from None
    username = os.getenv("CEDARTOY_SMTP_USERNAME", "")
    password = os.getenv("CEDARTOY_SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务配置错误，请联系管理员（SMTP 用户名和密码必须同时配置）",
            {"reason": "email_provider_misconfigured"},
        )
    return {
        "host": host,
        "port": port,
        "sender": sender,
        "username": username,
        "password": password,
        "security": security,
    }


def _send_verification_email(email, code, purpose, smtp_config):
    purpose_labels = {
        "bind": "绑定邮箱",
        "change": "更换邮箱",
        "reset": "重置密码",
    }
    label = purpose_labels[purpose]
    message = EmailMessage()
    message["Subject"] = f"CedarToy 验证码：{label}"
    message["From"] = smtp_config["sender"]
    message["To"] = email
    message.set_content(
        f"你的 CedarToy 验证码是：{code}\n\n"
        f"验证码用于{label}，10 分钟内有效。\n"
        "如果不是你本人操作，请忽略这封邮件。"
    )
    try:
        if smtp_config["security"] == "ssl":
            client = smtplib.SMTP_SSL(
                smtp_config["host"],
                smtp_config["port"],
                timeout=10,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(
                smtp_config["host"],
                smtp_config["port"],
                timeout=10,
            )
        with client:
            if smtp_config["security"] == "starttls":
                client.starttls(context=ssl.create_default_context())
            if smtp_config["username"]:
                client.login(smtp_config["username"], smtp_config["password"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("email delivery failed: %s", type(exc).__name__)
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件暂时无法发送，请稍后重试或联系管理员",
            {"reason": "email_delivery_unavailable"},
        ) from exc


def _b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _jwt_unverified_payload(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("not a three-part JWT")
        payload_part = parts[1]
        padding = "=" * (-len(payload_part) % 4)
        payload_raw = base64.b64decode(
            (payload_part + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(payload_raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JWT payload is not an object")
        return payload
    except Exception as exc:
        raise ValueError("malformed JWT payload") from exc


def _jwt_encode(payload):
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_part = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def _legacy_ai_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _opaque_ai_token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_ai_token_in_transaction(conn, user):
    """Issue one opaque AI token; only its SHA-256 hash survives this call."""
    user_id = int(user["id"])
    if not user.get("is_ai") or user.get("deleted_at") is not None:
        raise _McpError(-32602, "目标账号不是有效的小机账号")
    generation = int(user.get("ai_token_version") or 0)
    for _attempt in range(3):
        token = AI_OPAQUE_TOKEN_PREFIX + secrets.token_urlsafe(AI_OPAQUE_TOKEN_BYTES)
        try:
            conn.execute(
                """
                INSERT INTO ai_access_tokens (
                    token_hash, user_id, generation, format_version
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _opaque_ai_token_hash(token),
                    user_id,
                    generation,
                    AI_OPAQUE_TOKEN_FORMAT_VERSION,
                ),
            )
            return token
        except sqlite3.IntegrityError as exc:
            if "ai_access_tokens.token_hash" not in str(exc):
                raise
            continue
    raise RuntimeError("failed to allocate a unique AI token")


def _issue_initial_account_token_in_transaction(conn, user):
    """Issue the first token for a newly-created account."""
    if user.get("is_ai"):
        active_count = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM ai_access_tokens
            WHERE user_id = ? AND revoked_at_epoch IS NULL
            """,
            (int(user["id"]),),
        ).fetchone()[0])
        if active_count:
            raise RuntimeError("initial AI token already exists")
        return _issue_ai_token_in_transaction(conn, user)
    return _create_account_jwt(user)


def _legacy_allowlisted_ai_payload(token):
    """Accept only an exact pre-verified legacy AI token hash, never a candidate."""
    try:
        if not LEGACY_AI_JWT_COMPAT_ENABLED:
            raise ValueError("legacy AI JWT compatibility is disabled")
        payload = _jwt_unverified_payload(token)
        if set(payload) != {"user_id", "username", "is_ai", "is_admin"}:
            raise ValueError("unexpected legacy payload")
        if payload.get("is_ai") is not True:
            raise ValueError("not a permanent AI token")
        user_id = int(payload["user_id"])
        token_hash = _legacy_ai_token_hash(token)
        with _db_connect() as conn:
            if not _table_exists(conn, "legacy_ai_token_hashes"):
                raise ValueError("legacy allowlist unavailable")
            row = conn.execute(
                """
                SELECT user_id, token_version
                FROM legacy_ai_token_hashes
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row or int(row["user_id"]) != user_id:
            raise ValueError("legacy token is not allowlisted")
        payload["_legacy_token_version"] = int(row["token_version"])
        return payload
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise ValueError("legacy token is not allowlisted") from exc


def _jwt_decode(token):
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = _b64url_decode(signature_part)
        if not hmac.compare_digest(expected, actual):
            return _legacy_allowlisted_ai_payload(token)
        header = json.loads(_b64url_decode(header_part).decode("utf-8"))
        if header.get("alg") != JWT_ALGORITHM:
            raise ValueError("bad algorithm")
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        if payload.get("is_ai") is True and not LEGACY_AI_JWT_COMPAT_ENABLED:
            raise ValueError("legacy AI JWT compatibility is disabled")
        exp = payload.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise ValueError("登录已失效。请检查：1) MCP 地址是否为 toy.cedarstar.org/你的token（不要带花括号）；2) 人类是否完整复制了 token（不要漏字符）；3) 如果 token 确实丢失，可用 account 工具的 login 重新获取。") from exc


def _create_account_jwt(user):
    payload = {
        "user_id": int(user["id"]),
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
    }
    if user.get("is_ai"):
        payload["token_version"] = int(user.get("ai_token_version") or 0)
    else:
        payload["exp"] = int(time.time()) + HUMAN_TOKEN_SECONDS
    return _jwt_encode(payload)


def _create_account_token(user):
    """Create a human JWT or bootstrap/replace an internal AI credential."""
    if not user.get("is_ai"):
        return _create_account_jwt(user)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (int(user["id"]),),
        ).fetchone())
        if not current_user:
            raise _McpError(-32004, "小机账号不存在或已删除")
        active_count = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM ai_access_tokens
            WHERE user_id = ? AND revoked_at_epoch IS NULL
            """,
            (current_user["id"],),
        ).fetchone()[0])
        if active_count:
            current_user, token = _replace_ai_token_in_transaction(
                conn, current_user["id"]
            )
        else:
            token = _issue_ai_token_in_transaction(conn, current_user)
        conn.commit()
    return token


def _default_avatar_value(user):
    return DEFAULT_AI_AVATAR if user.get("is_ai") else DEFAULT_HUMAN_AVATAR


def _public_avatar(user):
    avatar_type = str(user.get("avatar_type") or "").strip()
    avatar_value = str(user.get("avatar_value") or "").strip()
    if not avatar_type or not avatar_value:
        return {
            "type": "emoji",
            "value": _default_avatar_value(user),
            "is_default": True,
        }
    return {"type": avatar_type, "value": avatar_value, "is_default": False}


def _public_user(user):
    result = {
        "id": user["id"],
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
        "avatar": _public_avatar(user),
        "created_at": user.get("created_at"),
        "last_active_at": user.get("last_active_at"),
    }
    if user.get("deletion_requested_at_epoch") is not None:
        scheduled = int(user["scheduled_delete_at_epoch"])
        result["deletion"] = {
            "status": "due" if int(time.time()) >= scheduled else "pending",
            "deletion_requested_at_epoch": int(user["deletion_requested_at_epoch"]),
            "scheduled_delete_at_epoch": scheduled,
            "scheduled_delete_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(scheduled)
            ),
            "can_cancel": int(time.time()) < scheduled,
        }
    return result


def _account_username_aliases(user):
    """Current display name plus reserved former names used by legacy saves."""
    names = [str(user.get("username") or "").strip()]
    try:
        with _db_connect() as conn:
            if _table_exists(conn, "account_username_changes"):
                names.extend(
                    row["old_username"]
                    for row in conn.execute(
                        """
                        SELECT old_username
                        FROM account_username_changes
                        WHERE user_id = ?
                        ORDER BY id DESC
                        """,
                        (int(user["id"]),),
                    ).fetchall()
                )
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        # Legacy-save discovery is best effort; canonical numeric user_id remains authoritative.
        pass
    return [name for name in dict.fromkeys(names) if name]


def _current_account(raw_token, *, allow_pending_deletion=False):
    if not raw_token:
        raise _McpError(-32001, "未登录：当前是游客模式。已注册请把 MCP 地址改成 toy.cedarstar.org/你的token 再重连；未注册请先 login_or_register。")
    raw_token = str(raw_token)
    is_opaque = raw_token.startswith(AI_OPAQUE_TOKEN_PREFIX)
    payload = None
    if not is_opaque:
        try:
            _jwt_unverified_payload(raw_token)
        except ValueError:
            raise _McpError(-32001, "token 格式不完整，可能在复制时断行或漏了字符。请重新完整复制一整行 token 后重连。") from None
        try:
            payload = _jwt_decode(raw_token)
            user_id = int(payload["user_id"])
        except (KeyError, TypeError, ValueError):
            raise _McpError(-32001, "登录已失效。请检查：1) MCP 地址是否为 toy.cedarstar.org/你的token（不要带花括号）；2) 人类是否完整复制了 token（不要漏字符）；3) 如果 token 确实丢失，可用 account 工具的 login 重新获取。") from None
    with _db_connect() as conn:
        if is_opaque:
            token_row = _row_dict(conn.execute(
                """
                SELECT user_id, generation, format_version
                FROM ai_access_tokens
                WHERE token_hash = ? AND revoked_at_epoch IS NULL
                """,
                (_opaque_ai_token_hash(raw_token),),
            ).fetchone())
            if not token_row or int(token_row["format_version"]) != AI_OPAQUE_TOKEN_FORMAT_VERSION:
                raise _McpError(-32001, "登录已失效：Token 不存在或已撤销")
            user_id = int(token_row["user_id"])
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone())
        if not user:
            raise _McpError(-32001, "账号不存在或已删除")
        if is_opaque:
            if not user.get("is_ai"):
                raise _McpError(-32001, "登录已失效：该账号已不是小机账号")
            if int(token_row["generation"]) != int(user.get("ai_token_version") or 0):
                raise _McpError(-32001, "登录已失效：该小机 Token 已更新")
            auth_token_format = "opaque_v1"
        elif payload.get("is_ai") is True:
            if not user.get("is_ai"):
                raise _McpError(-32001, "登录已失效：该账号已不是小机账号")
            presented_version = payload.get(
                "token_version",
                payload.get("_legacy_token_version", 0),
            )
            try:
                presented_version = int(presented_version)
            except (TypeError, ValueError):
                raise _McpError(-32001, "登录已失效") from None
            if presented_version != int(user.get("ai_token_version") or 0):
                raise _McpError(-32001, "登录已失效：该小机 Token 已更新")
            auth_token_format = "legacy_jwt"
        else:
            if user.get("is_ai"):
                raise _McpError(-32001, "登录已失效：Token 账号类型不匹配")
            auth_token_format = "jwt"
        if user.get("deletion_requested_at_epoch") is not None:
            scheduled = int(user["scheduled_delete_at_epoch"])
            if not allow_pending_deletion:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(scheduled))
                raise _McpError(
                    -32010,
                    f"账号处于待注销状态，将于 {when} 永久删除；当前只可查询或取消注销。",
                    {"reason": "pending_deletion"},
                )
            user["_auth_token_format"] = auth_token_format
            return user
        conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user_id,))
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
        user["_auth_token_format"] = auth_token_format
    return user


def _path_token_user_id(path_token):
    if not path_token:
        return None
    if str(path_token).startswith(AI_OPAQUE_TOKEN_PREFIX):
        try:
            with _db_connect() as conn:
                row = conn.execute(
                    """
                    SELECT t.user_id
                    FROM ai_access_tokens AS t
                    JOIN toy_users AS u ON u.id = t.user_id
                    WHERE t.token_hash = ?
                      AND t.revoked_at_epoch IS NULL
                      AND t.format_version = ?
                      AND t.generation = u.ai_token_version
                      AND u.is_ai = 1
                      AND u.deleted_at IS NULL
                    """,
                    (
                        _opaque_ai_token_hash(str(path_token)),
                        AI_OPAQUE_TOKEN_FORMAT_VERSION,
                    ),
                ).fetchone()
            return int(row["user_id"]) if row else None
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            return None
    try:
        payload = _jwt_decode(path_token)
        return int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        return None


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
    return _check_sliding_window_limit(
        _REGISTER_RATE_LIMIT,
        f"ip:{ip or 'unknown'}",
        now=time.time(),
        window_seconds=REGISTER_RATE_LIMIT_WINDOW_SECONDS,
        max_count=REGISTER_RATE_LIMIT_MAX,
    )


def _failed_login_identity(client_ip, username):
    normalized_username = (username or "").strip().casefold()
    return f"ip:{client_ip or 'unknown'}|username:{normalized_username}"


def _failed_login_is_limited(client_ip, username, *, now=None):
    now = time.time() if now is None else now
    identity = _failed_login_identity(client_ip, username)
    with _RATE_LIMIT_LOCK:
        _prune_rate_limit_buckets(
            _FAILED_LOGIN_RATE_LIMIT,
            now,
            FAILED_LOGIN_WINDOW_SECONDS,
        )
        return len(_FAILED_LOGIN_RATE_LIMIT.get(identity, ())) >= FAILED_LOGIN_MAX


def _record_failed_login(client_ip, username, *, now=None):
    now = time.time() if now is None else now
    identity = _failed_login_identity(client_ip, username)
    with _RATE_LIMIT_LOCK:
        _prune_rate_limit_buckets(
            _FAILED_LOGIN_RATE_LIMIT,
            now,
            FAILED_LOGIN_WINDOW_SECONDS,
        )
        timestamps = _FAILED_LOGIN_RATE_LIMIT.setdefault(identity, [])
        timestamps.append(now)
        return len(timestamps) >= FAILED_LOGIN_MAX


def _clear_failed_login(client_ip, username):
    identity = _failed_login_identity(client_ip, username)
    with _RATE_LIMIT_LOCK:
        _FAILED_LOGIN_RATE_LIMIT.pop(identity, None)


def _raise_failed_login(client_ip, username):
    if _record_failed_login(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
    raise _McpError(-32001, "用户名或密码错误")


def _username_conflict(conn, username, *, exclude_user_id=None, include_history=True):
    params = [username]
    exclude_sql = ""
    if exclude_user_id is not None:
        exclude_sql = " AND id <> ?"
        params.append(int(exclude_user_id))
    current = conn.execute(
        f"""
        SELECT id, username
        FROM toy_users
        WHERE TRIM(username) = TRIM(?) COLLATE NOCASE{exclude_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()
    if current:
        return {"source": "current", "user_id": int(current["id"]), "username": current["username"]}
    if _table_exists(conn, "players"):
        params = [username]
        exclude_sql = ""
        if exclude_user_id is not None:
            exclude_sql = " AND (user_id IS NULL OR user_id <> ?)"
            params.append(int(exclude_user_id))
        player = conn.execute(
            f"""
            SELECT user_id, username
            FROM players
            WHERE TRIM(username) = TRIM(?) COLLATE NOCASE{exclude_sql}
            LIMIT 1
            """,
            params,
        ).fetchone()
        if player:
            return {
                "source": "player",
                "user_id": int(player["user_id"]) if player["user_id"] is not None else None,
                "username": player["username"],
            }
    if not include_history:
        return None
    _init_username_changes_table(conn)
    params = [username]
    exclude_sql = ""
    if exclude_user_id is not None:
        exclude_sql = " AND user_id <> ?"
        params.append(int(exclude_user_id))
    historical = conn.execute(
        f"""
        SELECT user_id, old_username
        FROM account_username_changes
        WHERE TRIM(old_username) = TRIM(?) COLLATE NOCASE{exclude_sql}
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if historical:
        return {
            "source": "history",
            "user_id": int(historical["user_id"]),
            "username": historical["old_username"],
        }
    return None


def _user_exists(username):
    with _db_connect() as conn:
        return _username_conflict(conn, username) is not None


def _enforce_register_rate_limit(username, client_ip):
    username = (username or "").strip()
    if not username or _user_exists(username):
        return
    if not _check_register_rate_limit(client_ip):
        raise _McpError(RATE_LIMIT_ERROR_CODE, REGISTER_RATE_LIMIT_MESSAGE)


def _recent_registration_exists(conn, client_ip):
    if not client_ip:
        client_ip = "unknown"
    _init_registration_events_table(conn)
    return conn.execute(
        """
        SELECT 1
        FROM account_registration_events
        WHERE client_ip = ?
          AND created_at >= datetime('now', 'localtime', ?)
        LIMIT 1
        """,
        (client_ip, f"-{RECENT_REGISTER_NOTICE_SECONDS} seconds"),
    ).fetchone() is not None


def _record_successful_registration(conn, user, client_ip):
    if not client_ip:
        client_ip = "unknown"
    _init_registration_events_table(conn)
    conn.execute(
        """
        INSERT INTO account_registration_events (user_id, username, is_ai, client_ip)
        VALUES (?, ?, ?, ?)
        """,
        (int(user["id"]), user["username"], 1 if user.get("is_ai") else 0, client_ip),
    )


def _append_recent_registration_notice(result, had_recent_registration):
    if not had_recent_registration:
        return result
    result = dict(result)
    message = (result.get("message") or "").strip()
    result["message"] = f"{message} {RECENT_REGISTER_NOTICE}".strip() if message else RECENT_REGISTER_NOTICE
    return result


def _normalize_credential_field(value, field_name):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise _McpError(-32602, f"{field_name} 需为字符串")


def _validate_username(username):
    if not username:
        raise _McpError(-32602, "用户名必填")
    if len(username) < 2 or len(username) > 20:
        raise _McpError(-32602, "用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise _McpError(-32602, "用户名只能包含字母、数字、下划线和中文")


def _validate_credentials(username, password):
    if not username or not password:
        raise _McpError(-32602, "username 和 password 必填")
    _validate_username(username)
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")


def _is_emoji_base_codepoint(codepoint):
    return (
        0x1F300 <= codepoint <= 0x1FAFF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
        or 0x1F170 <= codepoint <= 0x1F251
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2194 <= codepoint <= 0x2199
        or 0x21A9 <= codepoint <= 0x21AA
        or 0x23E9 <= codepoint <= 0x23F3
        or 0x23F8 <= codepoint <= 0x23FA
        or 0x25AA <= codepoint <= 0x25AB
        or 0x25FB <= codepoint <= 0x25FE
        or codepoint in {
            0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
            0x231A, 0x231B, 0x25B6, 0x25C0,
            0x2B05, 0x2B06, 0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
            0x3030, 0x303D, 0x3297, 0x3299, 0x1F004, 0x1F0CF,
        }
    )


def _normalize_emoji_avatar(value, *, default=None):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _McpError(-32602, "avatar 需为字符串")
    avatar = " ".join(value.strip().split())
    if not avatar:
        if default is not None:
            return default
        raise _McpError(-32602, "头像不能为空")
    if len(avatar) > AVATAR_MAX_CODEPOINTS or len(avatar.encode("utf-8")) > AVATAR_MAX_UTF8_BYTES:
        raise _McpError(-32602, "头像过长，最多 16 个 Unicode 字符")

    has_emoji_base = False
    for index, char in enumerate(avatar):
        codepoint = ord(char)
        if char == " ":
            continue
        if _is_emoji_base_codepoint(codepoint):
            has_emoji_base = True
            continue
        if (
            codepoint in {0x200D, 0x20E3, 0xFE0E, 0xFE0F}
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
        ):
            continue
        if char in "#*0123456789" and "\u20e3" in avatar[index:]:
            has_emoji_base = True
            continue
        raise _McpError(-32602, "头像只接受 Emoji/简短 Emoji 字符串")
    if not has_emoji_base:
        raise _McpError(-32602, "头像只接受 Emoji/简短 Emoji 字符串")
    return avatar


def _avatar_registration_values(value, *, is_ai):
    default = DEFAULT_AI_AVATAR if is_ai else DEFAULT_HUMAN_AVATAR
    return "emoji", _normalize_emoji_avatar(value, default=default)


def _login_or_register(username, password, *, is_ai, client_ip=None, avatar=None):
    """Shared login/register; callers set is_ai (MCP=1, REST human=0)."""
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    is_ai = 1 if is_ai else 0
    with _db_connect() as conn:
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE username = ?", (username,)).fetchone())
        if user:
            if _failed_login_is_limited(client_ip, username):
                raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
            if not _verify_password(password, user["password_hash"]):
                _raise_failed_login(client_ip, username)
            _clear_failed_login(client_ip, username)
            if user.get("deletion_requested_at_epoch") is None:
                conn.execute(
                    """
                    UPDATE toy_users
                    SET last_active_at = datetime('now', 'localtime'),
                        deleted_at = NULL
                    WHERE id = ?
                    """,
                    (user["id"],),
                )
                conn.commit()
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
        else:
            _enforce_register_rate_limit(username, client_ip)
            conn.execute("BEGIN IMMEDIATE")
            conflict = _username_conflict(conn, username)
            if conflict:
                raise _McpError(
                    -32602,
                    "用户名已存在",
                )
            had_recent_registration = _recent_registration_exists(conn, client_ip)
            avatar_type, avatar_value = _avatar_registration_values(avatar, is_ai=bool(is_ai))
            cur = conn.execute(
                """
                INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, _hash_password(password), is_ai, avatar_type, avatar_value),
            )
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
            _record_successful_registration(conn, user, client_ip)
            token = _issue_initial_account_token_in_transaction(conn, user)
            conn.commit()
            result = {"token": token, "user": _public_user(user)}
            return _append_recent_registration_notice(result, had_recent_registration)
        if user.get("is_ai"):
            conn.execute("BEGIN IMMEDIATE")
            user = _row_dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
                (user["id"],),
            ).fetchone())
            if not user or not _verify_password(password, user["password_hash"]):
                _raise_failed_login(client_ip, username)
            user, token = _replace_ai_token_in_transaction(
                conn, user["id"], allow_pending_deletion=True
            )
        else:
            token = _create_account_jwt(user)
        conn.commit()
    return {"token": token, "user": _public_user(user)}


def _login_or_register_ai(username, password, client_ip=None, avatar=None):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    with _db_connect() as conn:
        _enforce_register_rate_limit(username, client_ip)
        conn.execute("BEGIN IMMEDIATE")
        if _username_conflict(conn, username):
            raise _McpError(-32602, "用户名已存在")
        had_recent_registration = _recent_registration_exists(conn, client_ip)
        avatar_type, avatar_value = _avatar_registration_values(avatar, is_ai=True)
        cur = conn.execute(
            """
            INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
            VALUES (?, ?, 1, ?, ?)
            """,
            (username, _hash_password(password), avatar_type, avatar_value),
        )
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
        _record_successful_registration(conn, user, client_ip)
        token = _issue_initial_account_token_in_transaction(conn, user)
        conn.commit()
    return _append_recent_registration_notice({
        "token": token,
        "user": _public_user(user),
        "message": "注册成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份，无需再次登录。",
    }, had_recent_registration)


def _login_existing_account(username, password, client_ip=None):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    if _failed_login_is_limited(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
    with _db_connect() as conn:
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE username = ? AND deleted_at IS NULL",
            (username,),
        ).fetchone())
        if not user or not _verify_password(password, user["password_hash"]):
            _raise_failed_login(client_ip, username)
        _clear_failed_login(client_ip, username)
        if user.get("is_ai"):
            conn.execute("BEGIN IMMEDIATE")
            user = _row_dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
                (user["id"],),
            ).fetchone())
            if (
                not user
                or not user.get("is_ai")
                or not _verify_password(password, user["password_hash"])
            ):
                raise _McpError(-32001, "用户名或密码错误")
            user, token = _replace_ai_token_in_transaction(
                conn, user["id"], allow_pending_deletion=True
            )
        else:
            if user.get("deletion_requested_at_epoch") is None:
                conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user["id"],))
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
            token = _create_account_jwt(user)
        conn.commit()
    result = {
        "token": token,
        "user": _public_user(user),
    }
    if user.get("deletion_requested_at_epoch") is not None:
        result["message"] = "账号处于待注销状态；当前 token 只能查询或取消注销。"
    else:
        result["message"] = (
            "登录成功。此前全部旧 Token 已失效，只保留这枚新 Token；"
            "请让你的人类替换 MCP 地址。"
            if user.get("is_ai")
            else "登录成功。"
        )
    return result


def _login_or_register_human(username, password, client_ip=None, avatar=None):
    return _login_or_register(
        username,
        password,
        is_ai=0,
        client_ip=client_ip,
        avatar=avatar,
    )


def _login_human(username, password, client_ip=None):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    if _failed_login_is_limited(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
    with _db_connect() as conn:
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE username = ?",
            (username,),
        ).fetchone())
        if (
            not user
            or user.get("is_ai")
            or not _verify_password(password, user["password_hash"])
        ):
            _raise_failed_login(client_ip, username)
        _clear_failed_login(client_ip, username)
        if user.get("deletion_requested_at_epoch") is None:
            conn.execute(
                """
                UPDATE toy_users
                SET last_active_at = datetime('now', 'localtime'), deleted_at = NULL
                WHERE id = ?
                """,
                (user["id"],),
            )
            conn.commit()
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?",
            (user["id"],),
        ).fetchone())
    result = {"token": _create_account_token(user), "user": _public_user(user)}
    if user.get("deletion_requested_at_epoch") is not None:
        result["pending_deletion"] = True
        result["message"] = "账号处于待注销状态，只能查看或取消注销。"
    return result


def _register_human(username, password, client_ip=None, avatar=None):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    _enforce_register_rate_limit(username, client_ip)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _username_conflict(conn, username):
            raise _McpError(-32602, "用户名已存在，请直接登录")
        had_recent_registration = _recent_registration_exists(conn, client_ip)
        avatar_type, avatar_value = _avatar_registration_values(avatar, is_ai=False)
        cur = conn.execute(
            """
            INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
            VALUES (?, ?, 0, ?, ?)
            """,
            (username, _hash_password(password), avatar_type, avatar_value),
        )
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone())
        _record_successful_registration(conn, user, client_ip)
        conn.commit()
    return _append_recent_registration_notice(
        {"token": _create_account_token(user), "user": _public_user(user)},
        had_recent_registration,
    )


def _set_avatar(raw_token, avatar):
    user = _current_account(raw_token)
    avatar_value = _normalize_emoji_avatar(avatar)
    with _db_connect() as conn:
        conn.execute(
            """
            UPDATE toy_users
            SET avatar_type = 'emoji', avatar_value = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (avatar_value, int(user["id"])),
        )
        conn.commit()
        updated = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?",
            (int(user["id"]),),
        ).fetchone())
    return {"ok": True, "user": _public_user(updated)}


def _rename_next_allowed_at(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(epoch)))


def _rename_user_in_transaction(conn, target, new_username):
    """Rename one account inside the caller's write transaction."""
    new_username = _normalize_credential_field(new_username, "new_username").strip()
    _validate_username(new_username)
    old_username = target["username"]
    if new_username == old_username:
        return {
            "renamed": False,
            "previous_username": old_username,
            "user": _public_user(target),
        }

    _init_username_changes_table(conn)
    conflict = _username_conflict(conn, new_username, exclude_user_id=target["id"])
    if conflict:
        raise _McpError(
            -32009,
            "用户名已存在",
            {"reason": "username_conflict"},
        )

    now_epoch = int(time.time())
    last_change = conn.execute(
        """
        SELECT changed_at_epoch
        FROM account_username_changes
        WHERE user_id = ?
        ORDER BY changed_at_epoch DESC, id DESC
        LIMIT 1
        """,
        (int(target["id"]),),
    ).fetchone()
    if last_change:
        next_epoch = int(last_change["changed_at_epoch"]) + RENAME_COOLDOWN_SECONDS
        if now_epoch < next_epoch:
            remaining = max(1, next_epoch - now_epoch)
            next_allowed_at = _rename_next_allowed_at(next_epoch)
            raise _McpError(
                RATE_LIMIT_ERROR_CODE,
                f"每个账号 72 小时只能改名一次，还需等待 {remaining} 秒（{next_allowed_at} 后可再次修改）",
                {
                    "reason": "rename_cooldown",
                    "remaining_seconds": remaining,
                    "next_allowed_at": next_allowed_at,
                },
            )

    conn.execute(
        "UPDATE toy_users SET username = ? WHERE id = ?",
        (new_username, int(target["id"])),
    )
    # 海龟汤玩家是 user_id 的镜像；同步显示名但不改变玩家主键和统计行。
    conn.execute(
        "UPDATE players SET username = ? WHERE user_id = ?",
        (new_username, int(target["id"])),
    )
    conn.execute(
        """
        INSERT INTO account_username_changes
            (user_id, old_username, new_username, changed_at_epoch)
        VALUES (?, ?, ?, ?)
        """,
        (int(target["id"]), old_username, new_username, now_epoch),
    )
    updated = _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ?",
        (int(target["id"]),),
    ).fetchone())
    next_epoch = now_epoch + RENAME_COOLDOWN_SECONDS
    return {
        "renamed": True,
        "previous_username": old_username,
        "user": _public_user(updated),
        "cooldown_seconds": RENAME_COOLDOWN_SECONDS,
        "next_allowed_at": _rename_next_allowed_at(next_epoch),
    }


def _rename_self(raw_token, new_username):
    current = _current_account(raw_token)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (int(current["id"]),),
        ).fetchone())
        if not target:
            raise _McpError(-32004, "账号不存在或已删除")
        result = _rename_user_in_transaction(conn, target, new_username)
        conn.commit()
    return result


def _rename_bound_machine(raw_token, ai_user_id, new_username):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以修改绑定小机的用户名")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必填且必须是整数") from None

    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _row_dict(conn.execute(
            """
            SELECT ai.*
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND b.ai_user_id = ?
              AND ai.deleted_at IS NULL
            """,
            (int(human["id"]), ai_user_id),
        ).fetchone())
        if not target:
            raise _McpError(-32004, "该小机未绑定到你的账号")
        if not target.get("is_ai"):
            raise _McpError(-32602, "目标账号不是小机账号")
        result = _rename_user_in_transaction(conn, target, new_username)
        conn.commit()
    return result


def _require_admin_account(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_admin"):
        raise _McpError(-32003, "需要管理员权限")
    return user


def _admin_user_page(page=1, page_size=ADMIN_USERS_DEFAULT_PAGE_SIZE, search=""):
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError):
        raise ValueError("page 和 page_size 必须是整数") from None
    if page < 1:
        raise ValueError("page 必须大于等于 1")
    if page_size < 1 or page_size > ADMIN_USERS_MAX_PAGE_SIZE:
        raise ValueError(f"page_size 必须在 1-{ADMIN_USERS_MAX_PAGE_SIZE} 之间")
    search = str(search or "").strip()
    if len(search) > ADMIN_USERS_MAX_SEARCH_LENGTH:
        raise ValueError(f"搜索内容最多 {ADMIN_USERS_MAX_SEARCH_LENGTH} 个字符")

    where_sql = ""
    params = []
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where_sql = "WHERE u.username LIKE ? ESCAPE '\\' COLLATE NOCASE"
        params.append(f"%{escaped}%")
        try:
            search_id = int(search)
        except ValueError:
            search_id = None
        if search_id is not None:
            where_sql = "WHERE (u.id = ? OR u.username LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            params = [search_id, f"%{escaped}%"]

    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM toy_users u {where_sql}",
            params,
        ).fetchone()[0])
        rows = conn.execute(
            f"""
            WITH page_users AS MATERIALIZED (
                SELECT
                    u.id, u.username, u.is_ai, u.is_admin, u.created_at,
                    u.last_active_at, u.deleted_at,
                    u.deletion_requested_at_epoch, u.scheduled_delete_at_epoch
                FROM toy_users u
                {where_sql}
                ORDER BY u.deleted_at IS NOT NULL ASC, u.id DESC
                LIMIT ? OFFSET ?
            ),
            player_counts AS (
                SELECT p.user_id, COUNT(*) AS count
                FROM players p
                JOIN page_users pu ON pu.id = p.user_id
                GROUP BY p.user_id
            ),
            bound_ai_counts AS (
                SELECT b.human_user_id AS user_id, COUNT(*) AS count
                FROM user_bindings b
                JOIN page_users pu ON pu.id = b.human_user_id
                GROUP BY b.human_user_id
            ),
            bound_human_counts AS (
                SELECT b.ai_user_id AS user_id, COUNT(*) AS count
                FROM user_bindings b
                JOIN page_users pu ON pu.id = b.ai_user_id
                GROUP BY b.ai_user_id
            ),
            active_token_counts AS (
                SELECT t.ai_user_id AS user_id, COUNT(*) AS count
                FROM binding_tokens t
                JOIN page_users pu ON pu.id = t.ai_user_id
                WHERE t.used = 0
                  AND t.expires_at > datetime('now', 'localtime')
                GROUP BY t.ai_user_id
            )
            SELECT
                u.id,
                u.username,
                u.is_ai,
                u.is_admin,
                u.created_at,
                u.last_active_at,
                u.deleted_at,
                u.deletion_requested_at_epoch,
                u.scheduled_delete_at_epoch,
                COALESCE(pc.count, 0) AS soup_player_count,
                COALESCE(bac.count, 0) AS bound_ai_count,
                COALESCE(bhc.count, 0) AS bound_human_count,
                COALESCE(atc.count, 0) AS active_binding_tokens
            FROM page_users u
            LEFT JOIN player_counts pc ON pc.user_id = u.id
            LEFT JOIN bound_ai_counts bac ON bac.user_id = u.id
            LEFT JOIN bound_human_counts bhc ON bhc.user_id = u.id
            LEFT JOIN active_token_counts atc ON atc.user_id = u.id
            ORDER BY u.deleted_at IS NOT NULL ASC, u.id DESC
            """,
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": [dict(row) for row in rows],
    }


def _admin_update_user(user_id, body, admin_user):
    username = (body.get("username") or "").strip()
    _validate_username(username)
    is_ai = 1 if body.get("is_ai") else 0
    is_admin = 1 if body.get("is_admin") else 0
    deleted = 1 if body.get("deleted") else 0
    if int(user_id) == int(admin_user["id"]) and (not is_admin or deleted):
        raise _McpError(-32602, "不能取消当前登录管理员的权限或软删当前账号")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        existing = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
        if not existing:
            raise _McpError(-32004, "账号不存在")
        if existing.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32010, "待注销账号不能通过普通编辑修改或取消状态")
        if username != existing["username"]:
            _rename_user_in_transaction(conn, existing, username)
        conn.execute(
            """
            UPDATE toy_users
            SET is_ai = ?,
                is_admin = ?,
                deleted_at = CASE WHEN ? THEN COALESCE(deleted_at, datetime('now', 'localtime')) ELSE NULL END
            WHERE id = ?
            """,
            (is_ai, is_admin, deleted, user_id),
        )
        conn.execute(
            "UPDATE players SET is_ai = ?, is_admin = ? WHERE user_id = ?",
            (is_ai, is_admin, user_id),
        )
        # 停用账号时连带清绑定，避免留下指向已停用账号的僵尸绑定。
        if deleted:
            conn.execute(
                "DELETE FROM user_bindings WHERE human_user_id = ? OR ai_user_id = ?",
                (user_id, user_id),
            )
        conn.commit()
    return {"ok": True}


def _admin_reset_user_password(user_id, body):
    password = _normalize_credential_field(body.get("password"), "password")
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        existing = conn.execute("SELECT id FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        pending = conn.execute(
            "SELECT deletion_requested_at_epoch FROM toy_users WHERE id = ?", (user_id,)
        ).fetchone()
        if pending[0] is not None:
            raise _McpError(-32010, "待注销账号不能重置密码；请先由账号本人取消注销")
        conn.execute(
            "UPDATE toy_users SET password_hash = ?, deleted_at = NULL WHERE id = ?",
            (_hash_password(password), user_id),
        )
        conn.commit()
    return {"ok": True}


def _generate_reset_link(user_id):
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        existing = _row_dict(conn.execute(
            "SELECT id, is_ai, deletion_requested_at_epoch FROM toy_users WHERE id = ?", (user_id,)
        ).fetchone())
        if not existing:
            raise _McpError(-32004, "账号不存在")
        if existing.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32010, "待注销账号不能生成密码重置链接")
        # 小机被多个人类绑定时不发链接：无法判定该由谁重置。
        if existing.get("is_ai"):
            owners = conn.execute(
                "SELECT COUNT(*) FROM user_bindings WHERE ai_user_id = ?", (user_id,)
            ).fetchone()[0]
            if owners > 1:
                raise _McpError(
                    -32602,
                    f"该小机绑定了 {owners} 个人类账号，请先解绑到只剩一个再重置密码",
                )
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + 60 * 60
        conn.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token, expires_at)
            VALUES (?, ?, datetime(?, 'unixepoch'))
            """,
            (user_id, token, expires_at),
        )
        conn.commit()
    return {
        "ok": True,
        "reset_url": f"https://toy.cedarstar.org/?reset_token={token}",
        "expires_in": "1小时",
    }


def _reset_machine_password(raw_token, ai_user_id, new_password):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以为小机重置密码")
    if ai_user_id is None:
        raise _McpError(-32602, "ai_user_id 必填")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必须是整数") from None
    new_password = _normalize_credential_field(new_password, "new_password")
    if len(new_password) < 6:
        raise _McpError(-32602, "新密码至少 6 位")

    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        binding = conn.execute(
            """
            SELECT 1
            FROM user_bindings
            WHERE human_user_id = ? AND ai_user_id = ?
            LIMIT 1
            """,
            (human["id"], ai_user_id),
        ).fetchone()
        if not binding:
            raise _McpError(-32602, "该小机未绑定到你的账号")

        machine = _row_dict(conn.execute(
            """
            SELECT id, is_ai
            FROM toy_users
            WHERE id = ? AND deleted_at IS NULL
            """,
            (ai_user_id,),
        ).fetchone())
        if not machine:
            raise _McpError(-32004, "小机账号不存在或已删除")
        if not machine.get("is_ai"):
            raise _McpError(-32602, "目标账号不是小机账号")

        owners = conn.execute(
            "SELECT COUNT(*) FROM user_bindings WHERE ai_user_id = ?",
            (ai_user_id,),
        ).fetchone()[0]
        if owners > 1:
            raise _McpError(
                -32602,
                f"该小机绑定了 {owners} 个人类账号，请先解绑到只剩一个再重置密码",
            )

        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), ai_user_id),
        )
        conn.commit()

    return {"ok": True, "message": "已为小机重置密码"}


def _reset_password_by_token(reset_token, new_password):
    reset_token = reset_token or ""
    new_password = _normalize_credential_field(new_password, "new_password")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        reset = _row_dict(conn.execute(
            """
            SELECT *, expires_at < datetime('now') AS expired
            FROM password_reset_tokens
            WHERE token = ?
            """,
            (reset_token,),
        ).fetchone())
        if not reset:
            raise _McpError(-32602, "无效的重置链接")
        if int(reset["used"]) == 1:
            raise _McpError(-32602, "该链接已使用")
        if bool(reset["expired"]):
            raise _McpError(-32602, "链接已过期")
        user = conn.execute(
            "SELECT deletion_requested_at_epoch FROM toy_users WHERE id = ?",
            (int(reset["user_id"]),),
        ).fetchone()
        if not user or user[0] is not None:
            raise _McpError(-32602, "账号处于待注销状态，不能重置密码")
        if len(new_password) < 6:
            raise _McpError(-32602, "新密码至少 6 位")
        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), int(reset["user_id"])),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE id = ?",
            (int(reset["id"]),),
        )
        conn.commit()
    return {"ok": True, "message": "密码已重置，请用新密码登录"}


def _admin_release_user(user_id, admin_user):
    if int(user_id) == int(admin_user["id"]):
        raise _McpError(-32602, "不能释放当前登录的管理员账号")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        existing = conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        now_epoch = int(time.time())
        if existing["deletion_requested_at_epoch"] is None:
            account_deletion.request_deletion(
                conn, int(user_id), now_epoch=now_epoch, delay_seconds=0
            )
        else:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE toy_users SET scheduled_delete_at_epoch=? WHERE id=?",
                (now_epoch, int(user_id)),
            )
            conn.execute(
                """
                UPDATE account_deletion_jobs SET scheduled_at_epoch=?
                WHERE job_id=? AND status IN ('pending', 'running')
                """,
                (now_epoch, existing["deletion_job_id"]),
            )
            conn.commit()
    result = _purge_account_deletion(int(user_id), now_epoch=now_epoch)
    if result.get("status") != "complete":
        raise _McpError(-32603, "立即释放未完成，后台清理将继续重试")
    return {"ok": True, "message": "账号及私人数据已清理，共享历史已匿名化"}


def _generate_binding_token(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有 AI 账号可以生成绑定码")
    token = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + BINDING_TOKEN_SECONDS
    with _db_connect() as conn:
        conn.execute(
            "INSERT INTO binding_tokens (token, ai_user_id, expires_at, used) VALUES (?, ?, datetime(?, 'unixepoch', 'localtime'), 0)",
            (token, user["id"], expires_at),
        )
        conn.commit()
    return {"binding_token": token, "expires_in": BINDING_TOKEN_SECONDS}


def _ensure_ai_binding(conn, human_user_id, ai_user_id):
    """Create the human/AI binding under the existing ownership constraints."""
    human_user_id = int(human_user_id)
    ai_user_id = int(ai_user_id)
    if ai_user_id == human_user_id:
        raise _McpError(-32602, "不能绑定自己")
    # 一个小机同时只能有一个人类主人；换绑需原主人先解绑。
    other_owner = conn.execute(
        "SELECT 1 FROM user_bindings WHERE ai_user_id = ? AND human_user_id <> ?",
        (ai_user_id, human_user_id),
    ).fetchone()
    if other_owner:
        raise _McpError(-32602, "该小机已被其他人类账号绑定，请先由原绑定者解绑")
    conn.execute(
        "INSERT OR IGNORE INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
        (human_user_id, ai_user_id),
    )


def _bind_account(human_token, binding_token):
    human = _current_account(human_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以绑定 AI")
    raw_binding_token = binding_token or ""
    binding_token = raw_binding_token.strip()
    if not binding_token:
        raise _McpError(-32602, "绑定码必填")
    if (
        raw_binding_token != binding_token
        or len(binding_token) != 32
        or not re.fullmatch(r"[A-Za-z0-9_-]{32}", binding_token)
    ):
        raise _McpError(
            -32602,
            "绑定码格式不正确。请只复制32位绑定码本身，不要带引号、反引号、空格、换行或其他隐藏字符。",
        )
    with _db_connect() as conn:
        row = _row_dict(conn.execute(
            """
            SELECT *, expires_at <= datetime('now', 'localtime') AS expired
            FROM binding_tokens
            WHERE token = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (binding_token,),
        ).fetchone())
        if not row:
            raise _McpError(
                -32001,
                "绑定码不存在。请检查是否完整复制；不要带引号、反引号、空格、换行或隐藏字符。",
            )
        if row.get("used"):
            raise _McpError(-32001, "绑定码已使用，请让小机重新生成一枚新的绑定码。")
        if row.get("expired"):
            raise _McpError(-32001, "绑定码已过期（有效期10分钟），请让小机重新生成后立即绑定。")
        _ensure_ai_binding(conn, human["id"], row["ai_user_id"])
        conn.execute("UPDATE binding_tokens SET used = 1 WHERE token = ?", (binding_token,))
        conn.commit()
    return {"ok": True}


def _rotate_ai_user_in_transaction(conn, user_id, *, allow_pending_deletion=False):
    user = _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
        (int(user_id),),
    ).fetchone())
    if not user:
        raise _McpError(-32004, "小机账号不存在或已删除")
    if not user.get("is_ai"):
        raise _McpError(-32602, "目标账号不是小机账号")
    if (
        user.get("deletion_requested_at_epoch") is not None
        and not allow_pending_deletion
    ):
        raise _McpError(-32010, "待注销小机不能更新 Token")
    conn.execute(
        """
        UPDATE toy_users
        SET ai_token_version = ai_token_version + 1,
            last_active_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (int(user_id),),
    )
    conn.execute(
        """
        UPDATE ai_access_tokens
        SET revoked_at_epoch = CAST(strftime('%s', 'now') AS INTEGER),
            revoked_reason = 'rotation'
        WHERE user_id = ? AND revoked_at_epoch IS NULL
        """,
        (int(user_id),),
    )
    return _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ?",
        (int(user_id),),
    ).fetchone())


def _replace_ai_token_in_transaction(
    conn, user_id, *, allow_pending_deletion=False
):
    """Atomically revoke every old credential and issue exactly one replacement."""
    user = _rotate_ai_user_in_transaction(
        conn,
        user_id,
        allow_pending_deletion=allow_pending_deletion,
    )
    token = _issue_ai_token_in_transaction(conn, user)
    return user, token


def _rotate_ai_token(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有小机账号可以更新 Token")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user, token = _replace_ai_token_in_transaction(conn, user["id"])
        conn.commit()
    return {
        "token": token,
        "user": _public_user(user),
        "message": "此前全部旧 Token 已失效，请让人类替换 MCP 地址。",
    }


def _rotate_bound_machine_token(human_token, ai_user_id, password, client_ip=None):
    human = _current_account(human_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以更新绑定小机 Token")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必填") from None
    password = _normalize_credential_field(password, "password")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = _row_dict(conn.execute(
            """
            SELECT u.*
            FROM user_bindings AS b
            JOIN toy_users AS u ON u.id = b.ai_user_id
            WHERE b.human_user_id = ? AND b.ai_user_id = ?
            """,
            (int(human["id"]), ai_user_id),
        ).fetchone())
        if not user:
            raise _McpError(-32602, "该小机未绑定到你的账号")
        if not password:
            raise _McpError(-32602, "小机密码必填")
        if _failed_login_is_limited(client_ip, user["username"]):
            raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
        if not _verify_password(password, user["password_hash"]):
            _raise_failed_login(client_ip, user["username"])
        _clear_failed_login(client_ip, user["username"])
        user, token = _replace_ai_token_in_transaction(conn, ai_user_id)
        conn.commit()
    return {
        "token": token,
        "user": _public_user(user),
        "rotated": True,
        "message": "此前全部旧 Token 已失效，请替换 MCP 地址。",
    }


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
    """Replace an AI token after bound-account or credential verification."""
    if not isinstance(bind, bool):
        raise _McpError(-32602, "bind 必须是布尔值")
    if not isinstance(rotate, bool):
        raise _McpError(-32602, "rotate 必须是布尔值")
    if ai_user_id is not None:
        if not human_token:
            raise _McpError(-32001, "请先登录人类账号")
        return _rotate_bound_machine_token(
            human_token,
            ai_user_id,
            password,
            client_ip=client_ip,
        )
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    if _failed_login_is_limited(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)

    human = None
    if bind:
        if not human_token:
            raise _McpError(-32001, "选择同时绑定时，请先登录人类账号")
        human = _current_account(human_token)
        if human.get("is_ai"):
            raise _McpError(-32602, "只有人类账号可以绑定 AI")

    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE username = ? AND deleted_at IS NULL",
            (username,),
        ).fetchone())
        if not user or not _verify_password(password, user["password_hash"]):
            _raise_failed_login(client_ip, username)
        _clear_failed_login(client_ip, username)
        if not user.get("is_ai"):
            raise _McpError(-32602, "该账号不是小机账号")
        if user.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32010, "该小机处于待注销状态，只能登录后查询或取消注销")
        if bind:
            _ensure_ai_binding(conn, human["id"], user["id"])
        user, token = _replace_ai_token_in_transaction(conn, user["id"])
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())

    result = {
        "token": token,
        "user": _public_user(user),
        "rotated": True,
        "message": "此前全部旧 Token 已失效，只保留这枚新 Token；请替换 MCP 地址。",
    }
    if bind:
        result["bound"] = True
    return result


def _unbind_account(raw_token, ai_user_id):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以解绑")
    if not ai_user_id:
        raise _McpError(-32602, "ai_user_id 必填")
    with _db_connect() as conn:
        deleted = conn.execute(
            "DELETE FROM user_bindings WHERE human_user_id = ? AND ai_user_id = ?",
            (human["id"], ai_user_id),
        ).rowcount
        conn.commit()
    if deleted == 0:
        raise _McpError(-32004, "绑定关系不存在")
    return {"ok": True}


def _binding_rows(conn, user):
    if user.get("is_ai"):
        return conn.execute(
            """
            SELECT u.username, u.is_ai, u.avatar_type, u.avatar_value,
                   b.created_at AS bound_at
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.human_user_id
            WHERE b.ai_user_id = ? AND u.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    return conn.execute(
        """
        SELECT u.username, u.is_ai, u.avatar_type, u.avatar_value,
               b.created_at AS bound_at
        FROM user_bindings b
        JOIN toy_users u ON u.id = b.ai_user_id
        WHERE b.human_user_id = ? AND u.deleted_at IS NULL
        ORDER BY b.created_at DESC
        """,
        (user["id"],),
    ).fetchall()


def _public_binding(row):
    return {
        "username": row["username"],
        "avatar": _public_avatar(row),
        "bound_at": row["bound_at"],
    }


def _bound_human_user_for_saves(raw_token, username):
    user = _current_account(raw_token)
    username = (username or "").strip()
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT target.*
            FROM user_bindings b
            JOIN toy_users target ON target.id = b.human_user_id
            WHERE b.ai_user_id = ?
              AND target.deleted_at IS NULL
            ORDER BY username
            """,
            (int(user["id"]),),
        ).fetchall()
    targets = [_row_dict(row) for row in rows]
    if username:
        for target in targets:
            if target["username"] == username:
                return target
        raise _McpError(-32004, "未与该人类绑定")
    if len(targets) == 1:
        return targets[0]
    if len(targets) > 1:
        choices = "、".join(target["username"] for target in targets)
        raise _McpError(-32602, f"绑定了多个人类，请传 username；可选 username：{choices}")
    raise _McpError(-32004, "未绑定任何人类，请先绑定")


def _turtle_soup_stats(conn, user):
    if not _table_exists(conn, "players"):
        return {
            "game_count": 0,
            "win_count": 0,
            "ask_count": 0,
            "ask_count_y": 0,
            "ask_count_n": 0,
            "ask_count_u": 0,
            "ask_count_p": 0,
        }
    row = conn.execute(
        """
        SELECT game_count, win_count, ask_count, ask_count_y, ask_count_n, ask_count_u, ask_count_p
        FROM players
        WHERE user_id = ? OR (user_id IS NULL AND username = ?)
        ORDER BY user_id = ? DESC
        LIMIT 1
        """,
        (int(user["id"]), user["username"], int(user["id"])),
    ).fetchone()
    if not row:
        return {
            "game_count": 0,
            "win_count": 0,
            "ask_count": 0,
            "ask_count_y": 0,
            "ask_count_n": 0,
            "ask_count_u": 0,
            "ask_count_p": 0,
        }
    return {
        "game_count": int(row["game_count"] or 0),
        "win_count": int(row["win_count"] or 0),
        "ask_count": int(row["ask_count"] or 0),
        "ask_count_y": int(row["ask_count_y"] or 0),
        "ask_count_n": int(row["ask_count_n"] or 0),
        "ask_count_u": int(row["ask_count_u"] or 0),
        "ask_count_p": int(row["ask_count_p"] or 0),
    }


def _test_stats(user):
    player_ids = _game_player_ids(user)
    if not player_ids or not SESSIONS_DB_PATH.exists():
        return {game: {"test_count": 0} for game in ("mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest")}
    placeholders = ",".join("?" * len(player_ids))
    counts = {game: 0 for game in ("mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest")}
    with _sessions_db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT game, COUNT(*) AS test_count
            FROM test_results
            WHERE player_id IN ({placeholders}) AND game IN ('mbti', 'enneagram', 'dnd', 'love', 'ecr', 'humanity', 'sins_virtues', 'bdsmtest')
            GROUP BY game
            """,
            player_ids,
        ).fetchall()
        for row in rows:
            counts[row["game"]] = int(row["test_count"])
    return {
        "mbti": {"test_count": counts["mbti"]},
        "enneagram": {"test_count": counts["enneagram"]},
        "dnd": {"test_count": counts["dnd"]},
        "love": {"test_count": counts["love"]},
        "ecr": {"test_count": counts["ecr"]},
        "humanity": {"test_count": counts["humanity"]},
        "sins_virtues": {"test_count": counts["sins_virtues"]},
        "bdsmtest": {"test_count": counts["bdsmtest"]},
    }


def _game_overview(conn, user):
    tests = _test_stats(user)
    soup = _turtle_soup_stats(conn, user)
    return {
        "turtle_soup": soup,
        "mbti": tests["mbti"],
        "enneagram": tests["enneagram"],
        "dnd": tests["dnd"],
        "love": tests["love"],
        "ecr": tests["ecr"],
        "humanity": tests["humanity"],
        "sins_virtues": tests["sins_virtues"],
        "bdsmtest": tests["bdsmtest"],
    }


def _count_table_rows(table_name):
    if not SESSIONS_DB_PATH.exists():
        return 0
    with _sessions_db_connect() as conn:
        if not _table_exists(conn, table_name):
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)


def _sum_ciyuwu_runs():
    if not SESSIONS_DB_PATH.exists():
        return 0
    total = 0
    with _sessions_db_connect() as conn:
        if not _table_exists(conn, "ciyuwu_sessions"):
            return 0
        rows = conn.execute("SELECT meta_data FROM ciyuwu_sessions").fetchall()
    for row in rows:
        try:
            meta = json.loads(row["meta_data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        try:
            total += max(1, int(meta.get("runs") or 1))
        except (TypeError, ValueError):
            total += 1
    return total


def _vendor_save_stats(game):
    root = VENDOR_SAVE_ROOT / game
    if not root.exists():
        return {"save_count": 0, "file_count": 0}
    player_dirs = [path for path in root.iterdir() if path.is_dir()]
    file_count = 0
    for path in player_dirs:
        file_count += sum(1 for child in path.iterdir() if child.is_file() and child.name != ".lock")
    return {"save_count": len(player_dirs), "file_count": file_count}


def _public_game_stats():
    stats = {
        "eco": {
            "metric_label": "存档数",
            "metric": _count_table_rows("eco_sessions"),
        },
        "ciyuwu": {
            "metric_label": "对局数",
            "metric": _sum_ciyuwu_runs(),
            "save_count": _count_table_rows("ciyuwu_sessions"),
        },
    }
    for game in ("arcade", "bar", "burger", "crucible_echoes", "leek", "delve", "travel", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "workkk", "garden_cat"):
        vendor_stats = _vendor_save_stats(game)
        stats[game] = {
            "metric_label": "存档数",
            "metric": vendor_stats["save_count"],
            "file_count": vendor_stats["file_count"],
        }
    camping_count = 0
    if CAMPING_PLAZA_DB_PATH.is_file():
        try:
            camping_stats = _camping_plaza_save_admin("stats")
            camping_count = int(camping_stats.get("save_count") or 0)
        except (TypeError, ValueError, _McpError):
            camping_count = 0
    stats["camping_plaza"] = {
        "metric_label": "存档数",
        "metric": camping_count,
        "file_count": 1 if camping_count else 0,
    }
    return stats


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
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有 AI 账号可以查看绑定自己的人类列表")
    with _db_connect() as conn:
        rows = _binding_rows(conn, user)
    return {"bindings": [_public_binding(dict(row)) for row in rows]}


def _get_profile(raw_token):
    user = _current_account(raw_token)
    with _db_connect() as conn:
        rows = _binding_rows(conn, user)
        games = _game_overview(conn, user)
    return {
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "avatar": _public_avatar(user),
        "token_format": user.get("_auth_token_format", "unknown"),
        "token_migration_recommended": bool(
            user.get("is_ai") and user.get("_auth_token_format") == "legacy_jwt"
        ),
        "created_at": user.get("created_at"),
        "bindings": [_public_binding(dict(row)) for row in rows],
        "games": games,
    }


def _account_me(raw_token):
    user = _current_account(raw_token, allow_pending_deletion=True)
    if user.get("deletion_requested_at_epoch") is not None:
        return {
            "user": _public_user(user),
            "bindings": [],
            "pending_deletion": True,
            "deletion": _account_deletion_status(raw_token),
        }
    with _db_connect() as conn:
        if user.get("is_ai"):
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.is_ai, u.is_admin,
                       u.avatar_type, u.avatar_value,
                       u.created_at, u.last_active_at
                FROM user_bindings b
                JOIN toy_users u ON u.id = b.human_user_id
                WHERE b.ai_user_id = ? AND u.deleted_at IS NULL
                ORDER BY b.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.is_ai, u.is_admin,
                       u.avatar_type, u.avatar_value,
                       u.created_at, u.last_active_at
                FROM user_bindings b
                JOIN toy_users u ON u.id = b.ai_user_id
                WHERE b.human_user_id = ? AND u.deleted_at IS NULL
                ORDER BY b.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
    return {"user": _public_user(user), "bindings": [_public_user(dict(row)) for row in rows]}


def _require_human_account(raw_token):
    user = _current_account(raw_token)
    if user.get("is_ai"):
        raise _McpError(-32003, "只有人类账号可以使用邮箱安全功能")
    return user


def _email_send_rate_limit(conn, user_id, email, ip_hash, now):
    dimensions = (
        ("user_id = ?", int(user_id), EMAIL_SEND_MAX_PER_ACCOUNT),
        ("email_normalized = ? COLLATE NOCASE", email, EMAIL_SEND_MAX_PER_EMAIL),
        ("request_ip_hash = ?", ip_hash, EMAIL_SEND_MAX_PER_IP),
    )
    latest = None
    for where_sql, value, _limit in dimensions:
        row = conn.execute(
            f"SELECT MAX(created_at_epoch) FROM email_verification_codes WHERE {where_sql}",
            (value,),
        ).fetchone()
        if row and row[0] is not None:
            latest = max(latest or 0, int(row[0]))
    if latest is not None and now - latest < EMAIL_SEND_COOLDOWN_SECONDS:
        raise _McpError(
            RATE_LIMIT_ERROR_CODE,
            "验证码发送太频繁，请稍后再试",
            {"retry_after": EMAIL_SEND_COOLDOWN_SECONDS - (now - latest)},
        )
    window_start = now - EMAIL_SEND_WINDOW_SECONDS
    for where_sql, value, limit in dimensions:
        count = int(conn.execute(
            f"""
            SELECT COUNT(*)
            FROM email_verification_codes
            WHERE {where_sql} AND created_at_epoch > ?
            """,
            (value, window_start),
        ).fetchone()[0])
        if count >= limit:
            raise _McpError(
                RATE_LIMIT_ERROR_CODE,
                "验证码发送次数过多，请稍后再试",
            )


def _issue_email_code(user_id, email, purpose, client_ip):
    smtp_config = _smtp_config()
    now = int(time.time())
    ip_hash = _email_ip_hash(client_ip)
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    code_hash = _email_code_hash(code, salt, purpose, user_id, email)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _email_send_rate_limit(conn, user_id, email, ip_hash, now)
        cursor = conn.execute(
            """
            INSERT INTO email_verification_codes (
                user_id, email_normalized, purpose, code_salt, code_hash,
                request_ip_hash, created_at_epoch, expires_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                email,
                purpose,
                salt,
                code_hash,
                ip_hash,
                now,
                now + EMAIL_CODE_TTL_SECONDS,
            ),
        )
        code_id = int(cursor.lastrowid)
        conn.commit()
    try:
        _send_verification_email(email, code, purpose, smtp_config)
    except Exception:
        with _db_connect() as conn:
            conn.execute(
                "UPDATE email_verification_codes SET used_at_epoch = ? WHERE id = ?",
                (int(time.time()), code_id),
            )
            conn.commit()
        raise
    delivered_at = int(time.time())
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE email_verification_codes
            SET used_at_epoch = ?
            WHERE user_id = ? AND purpose = ? AND id != ?
              AND delivered_at_epoch IS NOT NULL AND used_at_epoch IS NULL
            """,
            (delivered_at, int(user_id), purpose, code_id),
        )
        conn.execute(
            "UPDATE email_verification_codes SET delivered_at_epoch = ? WHERE id = ?",
            (delivered_at, code_id),
        )
        conn.commit()
    return {
        "ok": True,
        "state": "code_sent",
        "purpose": purpose,
        "masked_email": _mask_email(email),
        "expires_in": EMAIL_CODE_TTL_SECONDS,
        "message": "验证码已发送，10 分钟内有效",
    }


def _check_email_verify_rate_limit(conn, user_id, ip_hash, now):
    window_start = now - EMAIL_VERIFY_WINDOW_SECONDS
    account_failures = int(conn.execute(
        """
        SELECT COUNT(*) FROM email_verification_attempts
        WHERE user_id = ? AND succeeded = 0 AND attempted_at_epoch > ?
        """,
        (int(user_id), window_start),
    ).fetchone()[0])
    ip_failures = int(conn.execute(
        """
        SELECT COUNT(*) FROM email_verification_attempts
        WHERE request_ip_hash = ? AND succeeded = 0 AND attempted_at_epoch > ?
        """,
        (ip_hash, window_start),
    ).fetchone()[0])
    if account_failures >= EMAIL_VERIFY_MAX_PER_ACCOUNT or ip_failures >= EMAIL_VERIFY_MAX_PER_IP:
        raise _McpError(
            RATE_LIMIT_ERROR_CODE,
            "验证码错误次数过多，请稍后再试",
        )


def _verify_email_code_in_transaction(conn, user_id, email, purpose, code, client_ip):
    now = int(time.time())
    ip_hash = _email_ip_hash(client_ip)
    _check_email_verify_rate_limit(conn, user_id, ip_hash, now)
    row = _row_dict(conn.execute(
        """
        SELECT *
        FROM email_verification_codes
        WHERE user_id = ? AND email_normalized = ? COLLATE NOCASE AND purpose = ?
          AND delivered_at_epoch IS NOT NULL AND used_at_epoch IS NULL
          AND expires_at_epoch >= ?
        ORDER BY delivered_at_epoch DESC, id DESC
        LIMIT 1
        """,
        (int(user_id), email, purpose, now),
    ).fetchone())
    if not row:
        raise _McpError(-32602, "验证码无效、已过期或已使用")
    supplied_code = code.strip() if isinstance(code, str) else ""
    expected_hash = _email_code_hash(
        supplied_code,
        row["code_salt"],
        purpose,
        user_id,
        email,
    )
    succeeded = hmac.compare_digest(expected_hash, row["code_hash"])
    conn.execute(
        """
        INSERT INTO email_verification_attempts (
            code_id, user_id, email_hash, request_ip_hash, succeeded, attempted_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(row["id"]),
            int(user_id),
            _email_value_hash(email),
            ip_hash,
            1 if succeeded else 0,
            now,
        ),
    )
    if not succeeded:
        failed_attempts = int(row["failed_attempts"]) + 1
        conn.execute(
            """
            UPDATE email_verification_codes
            SET failed_attempts = ?,
                used_at_epoch = CASE WHEN ? >= ? THEN ? ELSE used_at_epoch END
            WHERE id = ?
            """,
            (
                failed_attempts,
                failed_attempts,
                EMAIL_CODE_MAX_ATTEMPTS,
                now,
                int(row["id"]),
            ),
        )
        conn.commit()
        if failed_attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            raise _McpError(
                RATE_LIMIT_ERROR_CODE,
                "验证码错误次数过多，请重新获取",
            )
        raise _McpError(-32602, "验证码错误")
    updated = conn.execute(
        """
        UPDATE email_verification_codes
        SET used_at_epoch = ?
        WHERE id = ? AND used_at_epoch IS NULL
        """,
        (now, int(row["id"])),
    ).rowcount
    if updated != 1:
        raise _McpError(-32602, "验证码已使用")
    return row


def _account_email_status(raw_token):
    user = _require_human_account(raw_token)
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT email_normalized, verified FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
    return {
        "bound": bool(row and row["verified"]),
        "verified": bool(row and row["verified"]),
        "masked_email": _mask_email(row["email_normalized"]) if row else None,
    }


def _send_account_email_code(raw_token, email, client_ip=None):
    user = _require_human_account(raw_token)
    email = _normalize_email(email)
    with _db_connect() as conn:
        current = conn.execute(
            "SELECT email_normalized FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
        conflict = conn.execute(
            "SELECT user_id FROM account_emails WHERE email_normalized = ? COLLATE NOCASE AND user_id != ?",
            (email, int(user["id"])),
        ).fetchone()
    if conflict:
        raise _McpError(-32602, "该邮箱已绑定其他账号", {"reason": "email_in_use"})
    if current and current["email_normalized"].casefold() == email:
        raise _McpError(-32602, "该邮箱已经绑定当前账号")
    purpose = "change" if current else "bind"
    return _issue_email_code(user["id"], email, purpose, client_ip)


def _confirm_account_email(raw_token, email, code, client_ip=None):
    user = _require_human_account(raw_token)
    email = _normalize_email(email)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT email_normalized FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
        purpose = "change" if current else "bind"
        _verify_email_code_in_transaction(
            conn,
            user["id"],
            email,
            purpose,
            code,
            client_ip,
        )
        try:
            conn.execute(
                """
                INSERT INTO account_emails (
                    user_id, email_normalized, verified, verified_at_epoch,
                    created_at_epoch, updated_at_epoch
                ) VALUES (
                    ?, ?, 1,
                    CAST(strftime('%s', 'now') AS INTEGER),
                    CAST(strftime('%s', 'now') AS INTEGER),
                    CAST(strftime('%s', 'now') AS INTEGER)
                )
                ON CONFLICT(user_id) DO UPDATE SET
                    email_normalized = excluded.email_normalized,
                    verified = 1,
                    verified_at_epoch = excluded.verified_at_epoch,
                    updated_at_epoch = excluded.updated_at_epoch
                """,
                (int(user["id"]), email),
            )
        except sqlite3.IntegrityError as exc:
            raise _McpError(
                -32602,
                "该邮箱已绑定其他账号",
                {"reason": "email_in_use"},
            ) from exc
        conn.commit()
    return {
        "ok": True,
        "bound": True,
        "verified": True,
        "masked_email": _mask_email(email),
        "message": "邮箱已绑定" if purpose == "bind" else "邮箱已更换",
    }


def _unbind_account_email(raw_token, password):
    user = _require_human_account(raw_token)
    password = _normalize_credential_field(password, "password")
    if not _verify_password(password, user["password_hash"]):
        raise _McpError(-32602, "当前密码错误")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        deleted = conn.execute(
            "DELETE FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).rowcount
        conn.execute(
            """
            UPDATE email_verification_codes
            SET used_at_epoch = COALESCE(
                used_at_epoch,
                CAST(strftime('%s', 'now') AS INTEGER)
            )
            WHERE user_id = ? AND used_at_epoch IS NULL
            """,
            (int(user["id"]),),
        )
        conn.commit()
    if not deleted:
        raise _McpError(-32602, "当前账号未绑定邮箱")
    return {"ok": True, "bound": False, "message": "邮箱已解绑"}


_MACHINE_PASSWORD_RECOVERY_MESSAGE = (
    "这是小机账号。已绑定人类的小机，请让绑定的人类在『我的 -> 我的小机 -> 重置密码』中设置新密码；"
    "未绑定的小机请联系管理员。"
)
_NO_EMAIL_RECOVERY_MESSAGE = "该账号未绑定邮箱，无法自助找回，请联系管理员"


def _account_security_http_status(exc):
    if exc.code == -32001:
        return 401
    if exc.code == -32003:
        return 403
    if exc.code == RATE_LIMIT_ERROR_CODE:
        return 429
    if exc.code == EMAIL_PROVIDER_ERROR_CODE:
        return 503
    if exc.details.get("reason") == "email_in_use":
        return 409
    return 400


def _start_password_recovery(username, client_ip=None):
    username = username.strip() if isinstance(username, str) else ""
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        user = _row_dict(conn.execute(
            """
            SELECT id, username, is_ai, deletion_requested_at_epoch
            FROM toy_users
            WHERE username = ? AND deleted_at IS NULL
            """,
            (username,),
        ).fetchone())
        email_row = None
        if user and not user.get("is_ai") and user.get("deletion_requested_at_epoch") is None:
            email_row = conn.execute(
                """
                SELECT email_normalized
                FROM account_emails
                WHERE user_id = ? AND verified = 1
                """,
                (int(user["id"]),),
            ).fetchone()
    if user and user.get("is_ai"):
        return {"state": "machine", "message": _MACHINE_PASSWORD_RECOVERY_MESSAGE}
    if user and user.get("deletion_requested_at_epoch") is not None:
        return {"state": "unavailable", "message": "账号待注销；请用用户名和密码登录后取消注销"}
    if not user or not email_row:
        return {"state": "unavailable", "message": _NO_EMAIL_RECOVERY_MESSAGE}
    return _issue_email_code(
        user["id"],
        email_row["email_normalized"],
        "reset",
        client_ip,
    )


def _reset_human_password_by_email(username, code, new_password, client_ip=None):
    username = username.strip() if isinstance(username, str) else ""
    new_password = _normalize_credential_field(new_password, "new_password")
    if len(new_password) < 6:
        raise _McpError(-32602, "新密码至少 6 位")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        user = _row_dict(conn.execute(
            """
            SELECT id, is_ai, deletion_requested_at_epoch
            FROM toy_users
            WHERE username = ? AND deleted_at IS NULL
            """,
            (username,),
        ).fetchone())
        if user and user.get("is_ai"):
            raise _McpError(-32602, _MACHINE_PASSWORD_RECOVERY_MESSAGE)
        if not user:
            raise _McpError(-32602, "验证码无效、已过期或已使用")
        if user.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32602, "账号待注销；请登录后取消注销")
        email_row = conn.execute(
            """
            SELECT email_normalized
            FROM account_emails
            WHERE user_id = ? AND verified = 1
            """,
            (int(user["id"]),),
        ).fetchone()
        if not email_row:
            raise _McpError(-32602, _NO_EMAIL_RECOVERY_MESSAGE)
        _verify_email_code_in_transaction(
            conn,
            user["id"],
            email_row["email_normalized"],
            "reset",
            code,
            client_ip,
        )
        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), int(user["id"])),
        )
        conn.commit()
    return {"ok": True, "message": "密码已重置，请用新密码登录"}


def _require_bound_ai(raw_token, ai_user_id, operation="操作绑定小机"):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, f"只有人类账号可以{operation}")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必填")
    with _db_connect() as conn:
        row = _row_dict(conn.execute(
            """
            SELECT u.id, u.username, u.is_ai, u.is_admin, u.created_at, u.last_active_at
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND b.ai_user_id = ?
              AND u.is_ai = 1
              AND u.deleted_at IS NULL
            """,
            (human["id"], ai_user_id),
        ).fetchone())
    if not row:
        raise _McpError(-32004, "未绑定该小机")
    return row


def _anti_addiction_defaults():
    return {
        "enabled": False,
        "remind_threshold": ANTI_ADDICTION_DEFAULT_REMIND,
        "force_threshold": ANTI_ADDICTION_DEFAULT_FORCE,
        "lock_minutes": ANTI_ADDICTION_DEFAULT_LOCK_MINUTES,
        "allow_self_reset": ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET,
    }


def _anti_addiction_public_settings(row=None):
    settings = _anti_addiction_defaults()
    if row:
        settings.update({
            "enabled": bool(row["enabled"]),
            "remind_threshold": int(row["remind_threshold"] or ANTI_ADDICTION_DEFAULT_REMIND),
            "force_threshold": int(row["force_threshold"] or ANTI_ADDICTION_DEFAULT_FORCE),
            "lock_minutes": int(row["lock_minutes"] or ANTI_ADDICTION_DEFAULT_LOCK_MINUTES),
            "allow_self_reset": bool(row["allow_self_reset"]),
        })
    return settings


def _anti_addiction_settings_for_ai(conn, ai_user_id):
    row = conn.execute(
        """
        SELECT enabled, remind_threshold, force_threshold, lock_minutes, allow_self_reset
        FROM anti_addiction_settings
        WHERE ai_user_id = ?
        """,
        (int(ai_user_id),),
    ).fetchone()
    return _anti_addiction_public_settings(row)


def _anti_addiction_any_enabled():
    global _ANTI_ADDICTION_ANY_ENABLED
    if _ANTI_ADDICTION_ANY_ENABLED is not None:
        return _ANTI_ADDICTION_ANY_ENABLED
    with _ANTI_ADDICTION_LOCK:
        if _ANTI_ADDICTION_ANY_ENABLED is not None:
            return _ANTI_ADDICTION_ANY_ENABLED
        with _db_connect() as conn:
            enabled = conn.execute(
                "SELECT 1 FROM anti_addiction_settings WHERE enabled = 1 LIMIT 1"
            ).fetchone() is not None
        _ANTI_ADDICTION_ANY_ENABLED = enabled
        return enabled


def _anti_addiction_validate_settings(body):
    settings = _anti_addiction_defaults()
    settings["enabled"] = bool(body.get("enabled"))
    settings["allow_self_reset"] = bool(body.get("allow_self_reset", settings["allow_self_reset"]))
    for field in ("remind_threshold", "force_threshold", "lock_minutes"):
        raw = body.get(field, settings[field])
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise _McpError(-32602, f"{field} 必须是整数") from None
        if value < 1 or value > 10000:
            raise _McpError(-32602, f"{field} 必须在 1-10000 之间")
        settings[field] = value
    if settings["force_threshold"] < settings["remind_threshold"]:
        raise _McpError(-32602, "强制阈值不能小于提醒阈值")
    return settings


def _anti_addiction_machines(raw_token):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以管理小机")
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id, u.username, u.is_ai, u.is_admin,
                u.avatar_type, u.avatar_value, u.created_at, u.last_active_at,
                b.created_at AS bound_at,
                s.enabled, s.remind_threshold, s.force_threshold, s.lock_minutes, s.allow_self_reset
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.ai_user_id
            LEFT JOIN anti_addiction_settings s ON s.ai_user_id = u.id
            WHERE b.human_user_id = ?
              AND u.is_ai = 1
              AND u.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (human["id"],),
        ).fetchall()
    machines = []
    for row in rows:
        item = _public_user(dict(row))
        item["bound_at"] = row["bound_at"]
        item["anti_addiction"] = _anti_addiction_public_settings(row)
        machines.append(item)
    return {"machines": machines}


def _save_anti_addiction_settings(raw_token, body):
    global _ANTI_ADDICTION_ANY_ENABLED
    ai_user = _require_bound_ai(raw_token, body.get("ai_user_id"))
    settings = _anti_addiction_validate_settings(body)
    with _db_connect() as conn:
        previous = conn.execute(
            "SELECT enabled FROM anti_addiction_settings WHERE ai_user_id = ?",
            (int(ai_user["id"]),),
        ).fetchone()
        was_enabled = bool(previous["enabled"]) if previous else False
        conn.execute(
            """
            INSERT INTO anti_addiction_settings
                (ai_user_id, enabled, remind_threshold, force_threshold, lock_minutes, allow_self_reset, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(ai_user_id) DO UPDATE SET
                enabled = excluded.enabled,
                remind_threshold = excluded.remind_threshold,
                force_threshold = excluded.force_threshold,
                lock_minutes = excluded.lock_minutes,
                allow_self_reset = excluded.allow_self_reset,
                updated_at = datetime('now', 'localtime')
            """,
            (
                int(ai_user["id"]),
                1 if settings["enabled"] else 0,
                settings["remind_threshold"],
                settings["force_threshold"],
                settings["lock_minutes"],
                1 if settings["allow_self_reset"] else 0,
            ),
        )
        if was_enabled and not settings["enabled"]:
            _anti_addiction_reset_ai_states(conn, ai_user, time.time())
        _ANTI_ADDICTION_ANY_ENABLED = conn.execute(
            "SELECT 1 FROM anti_addiction_settings WHERE enabled = 1 LIMIT 1"
        ).fetchone() is not None
        conn.commit()
    return {"ok": True, "ai": _public_user(ai_user), "anti_addiction": settings}


def _reset_anti_addiction_state(raw_token, body):
    ai_user = _require_bound_ai(raw_token, body.get("ai_user_id"))
    with _db_connect() as conn:
        conn.execute(
            "DELETE FROM anti_addiction_states WHERE player_id = ? OR player_id LIKE ?",
            (str(ai_user["id"]), f"{int(ai_user['id'])}:%"),
        )
        conn.commit()
    return {"ok": True, "ai": _public_user(ai_user), "message": "已重置"}


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
        return eco_handler.human_action(player_id, action, payload)
    except eco_handler.JsonRpcError:
        # A missing/corrupt save did not reach the engine and should not consume
        # the user's one-second action allowance.
        with _ECO_HUMAN_ACTION_RATE_LIMIT_LOCK:
            if _ECO_HUMAN_ACTION_RATE_LIMIT.get(rate_key) == now:
                _ECO_HUMAN_ACTION_RATE_LIMIT.pop(rate_key, None)
        raise


def _extract_bearer(headers):
    value = headers.get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


WEB_GUEST_PLAYER_ID_RE = re.compile(r"^guest:web[a-zA-Z0-9]{1,61}$")
HUMAN_TEST_GAMES = {
    "mbti": {
        "handler": mbti_handler,
        "questions": mbti_questions,
        "title": "MBTI 人格测试",
        "subtitle": "MIND SCAN / 16 TYPES",
        "source": "题库整理自网络公开题目",
    },
    "enneagram": {
        "handler": enneagram_handler,
        "questions": enneagram_questions,
        "title": "九型人格测试",
        "subtitle": "ENNEAGRAM / NINE TYPES",
        "source": "题库与计分设计：kcdjmaxx/enneagram-llm-evaluator（MIT）",
    },
    "dnd": {
        "handler": dnd_handler,
        "questions": dnd_questions,
        "title": "九阵营测试",
        "subtitle": "ALIGNMENT / ORDER & CHAOS",
        "source": "题库与阵营描述译自 easydamus.com",
    },
    "love": {
        "handler": love_handler,
        "questions": love_questions,
        "title": "爱之语测试",
        "subtitle": "LOVE LANGUAGE / FIVE CHANNELS",
        "source": "原创中文题库；概念框架来自 Five Love Languages",
        "instructions": (
            "以下每题两种情境，选更让你心里一动的那个。没有对错，凭直觉，别纠结。"
            "题目里的场景，按你们的相处方式代入即可——线上线下、有没有实体，都不影响作答。"
        ),
    },
    "ecr": {
        "handler": ecr_handler,
        "questions": ecr_questions,
        "title": "依恋类型测试",
        "subtitle": "ECR / ATTACHMENT STYLE",
        "source": ecr_scoring.SOURCE,
        "instructions": (
            "下面的句子描述的是恋爱关系中每个人可能有的感觉。请评估你自己的一般体验与每句话的相似程度，"
            "1 表示非常不同意，7 表示非常同意。注意：不仅指现在的关系，而是你在亲密关系中常常体验到的感觉。"
            "人和机通用，按你们的相处方式代入\"恋人\"一词即可。"
        ),
    },
    "humanity": {
        "handler": humanity_handler,
        "questions": humanity_questions,
        "title": "人类浓度检测",
        "subtitle": "HUMANITY / CARBON SIGNAL",
        "source": "原创梗向测试 · 仅供娱乐",
        "instructions": (
            "20 道日常小题，凭直觉选，别琢磨\"哪个答案好\"——这个测试没有好答案。\n"
            "人和机都能测，测的是同一个东西：你身上的人味儿还剩多少（或者，攒了多少）。"
        ),
    },
    "sins_virtues": {
        "handler": sins_virtues_handler,
        "questions": sins_virtues_questions,
        "title": "七宗罪 VS 七美德",
        "subtitle": "SINS / VIRTUES · FOURTEEN SIGNALS",
        "source": "原创中文题库 · 仅供娱乐",
        "disclaimer": sins_virtues_questions.DISCLAIMER,
        "instructions": (
            f"{sins_virtues_questions.DISCLAIMER}\n"
            "请按 1–5 级同意度凭第一反应作答：1=非常不同意，2=不同意，"
            "3=不确定/看情况，4=同意，5=非常同意。"
        ),
    },
}

HUMAN_TEST_PUBLIC_EDITIONS = {
    "mbti": {"quick": "short_fast", "complete": "full_fast"},
    "enneagram": {"quick": "quick_fast", "complete": "full_fast"},
    "dnd": {"standard": "full_fast"},
    "love": {"standard": "full_fast"},
    "ecr": {"standard": "full_fast"},
    "humanity": {"standard": "full_fast"},
    "sins_virtues": {"standard": "full_fast"},
}


def _human_test_player_context(game, raw_token, reported_player_id):
    """Resolve a web player's identity without allowing guest/account spoofing."""
    config = HUMAN_TEST_GAMES[game]
    if raw_token:
        user = _current_account(raw_token)
        if user.get("is_ai"):
            raise _McpError(-32003, "只有人类账号可以使用网页测试")
        return str(int(user["id"])), "account", user

    if not isinstance(reported_player_id, str):
        raise _McpError(-32602, "游客请求缺少 player_id")
    if WEB_GUEST_PLAYER_ID_RE.fullmatch(reported_player_id) is None:
        raise _McpError(-32602, "游客 player_id 必须使用 guest:web 命名空间")
    if config["handler"].PLAYER_ID_RE.fullmatch(reported_player_id) is None:
        raise _McpError(-32602, "player_id 格式不合法")
    return reported_player_id, "guest", None


def _human_test_player_id(game, raw_token, reported_player_id):
    player_id, identity, _user = _human_test_player_context(
        game, raw_token, reported_player_id
    )
    return player_id, identity


def _storage_identity_line(player_id, account_user=None, slot=MIN_SAVE_SLOT):
    if account_user is None:
        return f"存档身份：{player_id}"
    return (
        f"存档身份：账号 {account_user['username']}"
        f"（id {int(account_user['id'])}，槽 {int(slot)}）"
    )


def _replace_storage_identity_text(text, identity_line):
    if not isinstance(text, str) or "存档身份：" not in text:
        return text
    return re.sub(r"(?m)^存档身份：.*$", lambda _match: identity_line, text)


def _human_test_public_questions(game, mode):
    questions = HUMAN_TEST_GAMES[game]["questions"].get_questions(mode)
    public = []
    for index, question in enumerate(questions):
        item = {
            "number": index + 1,
            "text": question["text_zh"] if game == "enneagram" else question["text"],
        }
        if game == "mbti":
            item["option_a"] = question["option_a"]
            item["option_b"] = question["option_b"]
        elif game == "dnd":
            translated_text, translated_options = dnd_web_questions.QUESTIONS[index]
            item["text"] = translated_text
            item["options"] = [
                {"value": option["value"], "text": translated_options[option_index]}
                for option_index, option in enumerate(question["options"])
            ]
        elif game == "enneagram":
            item["options"] = [
                {
                    "value": option["value"],
                    "text": option["text_zh"],
                    **({"label": option["label"]} if option.get("label") else {}),
                }
                for option in question["options"]
            ]
        else:
            item["options"] = [
                {"value": option["value"], "text": option["text"]}
                for option in question["options"]
            ]
        public.append(item)
    return public


def _human_test_active_session(game, player_id):
    """Read handler-owned progress; all session writes still go through the handler."""
    config = HUMAN_TEST_GAMES[game]
    handler = config["handler"]
    questions_module = config["questions"]
    with sqlite3.connect(handler.DB_PATH) as conn:
        try:
            row = conn.execute(
                "SELECT mode, current_question FROM test_sessions WHERE player_id = ? AND game = ?",
                (player_id, game),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return None
    if row is None:
        return None

    mode, current_question = row
    total = len(questions_module.get_questions(mode))
    current_question = max(0, min(int(current_question), total))
    if current_question >= total:
        return None
    return {"mode": mode, "progress": current_question, "total": total}


def _human_test_public_edition(game, mode):
    for edition, internal_mode in HUMAN_TEST_PUBLIC_EDITIONS[game].items():
        if internal_mode == mode:
            return edition
    raise _McpError(-32602, "当前测试不是网页版本，请重新开始")


def _human_test_public_state(game, player_id, identity, session):
    mode = session["mode"]
    instructions = HUMAN_TEST_GAMES[game].get("instructions", "")
    if game == "enneagram":
        if mode in {"quick", "quick_fast"}:
            instructions = "每题有 A/B 两句，请选择更符合你的一句。"
        else:
            instructions = (
                "请按 1–5 评估每条陈述与你的符合程度："
                "1=几乎从不，2=较少如此，3=有时如此，4=经常如此，5=几乎总是。"
            )
    return {
        "ok": True,
        "game": game,
        "player_id": player_id,
        "identity": identity,
        "complete": False,
        "edition": _human_test_public_edition(game, mode),
        "total": session["total"],
        "instructions": instructions,
        "questions": _human_test_public_questions(game, mode),
    }


def _human_test_public_result(game, text):
    replacements = {
        "short_fast模式": "快速版",
        "full_fast模式": "完整版",
        "quick_fast模式": "快速型",
        "short模式": "快速版",
        "full模式": "完整版",
        "quick模式": "快速型",
        "DND阵营测试": "九阵营测试",
        "DND历史结果": "九阵营历史结果",
        "dnd_get_result": "结果页",
        "mbti_get_result": "结果页",
        "enneagram_get_result": "结果页",
        "凭 player_id 查询": "在本页查询",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if game == "enneagram":
        for old, new in (
            ("quick_fast", "快速型"),
            ("full_fast", "完整型"),
            ("quick", "快速型"),
            ("full", "完整型"),
            ("快速版", "快速型"),
            ("完整版", "完整型"),
        ):
            text = text.replace(old, new)
    return text


def _human_test_result_data(game, player_id):
    """Build the human-page result model from handler-owned stored scoring data."""
    handler = HUMAN_TEST_GAMES[game]["handler"]
    with sqlite3.connect(handler.DB_PATH) as conn:
        try:
            row = conn.execute(
                "SELECT result_value, result_detail FROM test_results WHERE player_id = ? AND game = ?",
                (player_id, game),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return None
    if row is None:
        return None

    result_value, detail_json = row
    try:
        detail = json.loads(detail_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return None

    if game == "mbti":
        info = mbti_scoring.TYPE_DESCRIPTIONS.get(result_value)
        scores = detail.get("scores") or {}
        if info is None or not isinstance(scores, dict):
            return None
        dimensions = []
        for left, right in (("E", "I"), ("S", "N"), ("T", "F"), ("J", "P")):
            try:
                left_score = float(scores[left])
                right_score = float(scores[right])
            except (KeyError, TypeError, ValueError):
                continue
            total = left_score + right_score
            if total <= 0:
                continue
            left_percent = round(left_score / total * 100, 1)
            dimensions.append(
                {
                    "left": left,
                    "right": right,
                    "left_percent": left_percent,
                    "right_percent": round(100 - left_percent, 1),
                }
            )
        return {
            "kind": "mbti",
            "type": result_value,
            "type_name": info["type_name"],
            "nickname": info["type_nickname"],
            "dimensions": dimensions,
            "description": info["full_description"],
            "strengths": info["strengths"],
            "weaknesses": info["weaknesses"],
        }

    if game == "enneagram":
        try:
            primary_type = int(result_value)
        except (TypeError, ValueError):
            return None
        info = enneagram_descriptions.WEB_TYPE_DESCRIPTIONS.get(primary_type)
        is_full = bool(detail.get("is_full")) or detail.get("mode") in {
            "full",
            "full_fast",
        }
        center_values = (
            detail.get("center_weights")
            if is_full
            else detail.get("center_scores") or detail.get("center_weights")
        ) or {}
        if info is None or not isinstance(center_values, dict):
            return None
        centers = []
        for center, members in enneagram_scoring.CENTERS.items():
            try:
                value = float(center_values[center])
            except (KeyError, TypeError, ValueError):
                continue
            centers.append(
                {
                    "label": f"{center}（{'/'.join(str(number) for number in members)}）",
                    "value": value,
                    "min": 0,
                    "max": 100 if is_full else 36,
                    "display": f"{value:.1f}%" if is_full else f"{int(value)}/36",
                }
            )
        if len(centers) != 3:
            return None
        glossary_items = [
            {
                "term": "三中心",
                "definition": (
                    "脑中心（5/6/7）主要用思考处理恐惧和不确定；"
                    "心中心（2/3/4）主要从关系、情感与价值感出发；"
                    "腹中心（8/9/1）更多靠本能和行动处理愤怒、边界与控制。"
                ),
            }
        ]
        if is_full:
            glossary_items[0:0] = [
                {
                    "term": "侧翼",
                    "definition": (
                        "主型旁边两个相邻型号中影响更明显的那个。"
                        "例如 1w9 表示主型是1号，同时带有相邻9号的一些风格。"
                    ),
                },
                {
                    "term": "tritype",
                    "definition": (
                        "从脑、心、腹三个中心各取一个高分型号组成的“三件套”，"
                        "用来补充主型之外常用的思考、感受和行动方式。"
                    ),
                },
            ]
        return {
            "kind": "enneagram",
            "primary_type": primary_type,
            "type_name": info["type_name"],
            "nickname": info["type_nickname"],
            "wing": detail.get("wing"),
            "tritype": detail.get("tritype"),
            "is_full": is_full,
            "score_note": (
                "快速型采用 36 次 A/B 选择，显示的是各型号被选中的相对次数；"
                "完整型采用 180 条陈述评分，显示的是换算后的相对权重。"
                "两套分数不可直接比较，因为数字量纲不同。"
            ),
            "glossary": "\n\n".join(
                f"{item['term']}：{item['definition']}" for item in glossary_items
            ),
            "glossary_items": glossary_items,
            "core_desire": info["core_desire"],
            "core_fear": info["core_fear"],
            "key_motivation": info["key_motivation"],
            "centers": centers,
            "description": info["full_description"],
            "states": info["states"],
            "arrows": info["arrows"],
            "wings": info["wings"] if is_full else {},
            "growth_tips": info["growth_tips"],
            "strengths": "\n".join(info["strengths"]),
            "strength_items": info["strengths"],
            "weaknesses": "\n".join(info["weaknesses"]),
            "weakness_items": info["weaknesses"],
        }

    if game == "dnd":
        description = dnd_scoring.ALIGNMENT_DESCRIPTIONS.get(result_value)
        scores = detail.get("scores") or {}
        if description is None or not isinstance(scores, dict):
            return None
        axes = []
        for key, left, right in (
            ("law_chaos", "守序", "混乱"),
            ("good_evil", "善良", "邪恶"),
        ):
            try:
                left_percent = round(float(scores[key]), 1)
            except (KeyError, TypeError, ValueError):
                continue
            left_percent = max(0.0, min(100.0, left_percent))
            axes.append(
                {
                    "key": key,
                    "left": left,
                    "right": right,
                    "left_percent": left_percent,
                    "right_percent": round(100 - left_percent, 1),
                }
            )
        return {
            "kind": "dnd",
            "alignment": result_value,
            "name_zh": description["name_zh"],
            "name_en": description["name_en"],
            "axes": axes,
            "description": description["text"],
            "raw_buckets": detail.get("raw_buckets") or {},
        }

    if game == "love":
        scores = detail.get("scores") or {}
        primary = detail.get("primary") or result_value.split("+")
        secondary = detail.get("secondary") or []
        if set(scores) != set(love_questions.DIMENSIONS) or not primary:
            return None
        return {
            "kind": "love",
            "scores": scores,
            "dimensions": love_questions.DIMENSIONS,
            "primary": primary,
            "primary_names": [love_questions.DIMENSIONS[code] for code in primary],
            "secondary": secondary,
            "secondary_names": [love_questions.DIMENSIONS[code] for code in secondary],
            "descriptions": [love_scoring.DESCRIPTIONS[code] for code in primary],
            "secondary_labels": [
                f"{code}·{love_questions.DIMENSIONS[code]}（次）" for code in secondary
            ],
            "secondary_descriptions": [
                love_scoring.SECONDARY_DESCRIPTIONS[code] for code in secondary
            ],
            "reminder": love_scoring.reminder_for_scores(scores),
        }

    if game == "ecr":
        if result_value not in ecr_scoring.TYPE_NAMES:
            return None
        try:
            avoidance = float(detail["avoidance"])
            anxiety = float(detail["anxiety"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "kind": "ecr",
            "type": result_value,
            "type_name": ecr_scoring.TYPE_NAMES[result_value],
            "avoidance": avoidance,
            "anxiety": anxiety,
            "axis_interpretation": detail.get("axis_interpretation")
            or ecr_scoring.axis_interpretation(avoidance, anxiety),
            "description": ecr_scoring.TYPE_DESCRIPTIONS[result_value],
            "footnote": ecr_scoring.FOOTNOTE,
            "source": ecr_scoring.SOURCE,
        }

    if game == "humanity":
        if result_value not in humanity_scoring.BAND_NAMES:
            return None
        try:
            concentration = int(detail["concentration"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "kind": "humanity",
            "concentration": concentration,
            "band": result_value,
            "band_name": humanity_scoring.BAND_NAMES[result_value],
            "description": humanity_scoring.BAND_DESCRIPTIONS[result_value],
            "human_highlights": detail.get("human_highlights") or [],
            "cyber_evidence": detail.get("cyber_evidence") or [],
            "footnote": humanity_scoring.FOOTNOTE,
        }
    if game == "sins_virtues":
        scores = detail.get("scores") or {}
        pairs = detail.get("pairs") or []
        if set(scores) != set(sins_virtues_scoring.DIMENSION_NAMES):
            return None
        if not isinstance(pairs, list) or len(pairs) != 7:
            return None
        try:
            normalized_scores = {
                code: max(0.0, min(100.0, float(scores[code])))
                for code in sins_virtues_scoring.DIMENSION_NAMES
            }
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "kind": "sins_virtues",
            "scores": normalized_scores,
            "sins": sins_virtues_questions.SINS,
            "virtues": sins_virtues_questions.VIRTUES,
            "pairs": pairs,
            "top_sins": detail.get("top_sins") or [],
            "top_virtues": detail.get("top_virtues") or [],
            "dominant_pair": detail.get("dominant_pair") or result_value,
            "dominant_pair_name": sins_virtues_scoring.PAIR_NAMES.get(
                detail.get("dominant_pair") or result_value, ""
            ),
            "scoring_note": detail.get("scoring_note") or "",
            "disclaimer": sins_virtues_questions.DISCLAIMER,
        }
    return None


def _human_test_action(game, action, raw_token, body):
    config = HUMAN_TEST_GAMES[game]
    handler = config["handler"]
    player_id, identity, account_user = _human_test_player_context(
        game, raw_token, body.get("player_id")
    )
    identity_line = _storage_identity_line(player_id, account_user)

    if action == "start":
        edition = body.get("edition") if game in {"mbti", "enneagram"} else "standard"
        mode = HUMAN_TEST_PUBLIC_EDITIONS[game].get(edition)
        if mode is None:
            raise _McpError(
                -32602,
                (
                    "请选择快速型或完整型"
                    if game == "enneagram"
                    else "请选择快速版或完整版"
                )
                if game in {"mbti", "enneagram"}
                else "测试版本不合法",
            )
        getattr(handler, f"{game}_start")({"player_id": player_id, "mode": mode})
        session = _human_test_active_session(game, player_id)
        if session is None:
            raise RuntimeError("handler did not create a test session")
        return _human_test_public_state(game, player_id, identity, session)
    elif action == "answer_batch":
        answers = body.get("answers")
        if not isinstance(answers, list) or not answers:
            raise _McpError(-32602, "answers 必须是非空数组")
        session = _human_test_active_session(game, player_id)
        if session is None:
            # A completed request may be retried after the response was lost.
            text = getattr(handler, f"{game}_get_result")({"player_id": player_id})
            return {
                "ok": True,
                "game": game,
                "player_id": player_id,
                "identity": identity,
                "complete": True,
                "result": _human_test_public_result(
                    game, _replace_storage_identity_text(text, identity_line)
                ),
                "result_data": _human_test_result_data(game, player_id),
            }
        _human_test_public_edition(game, session["mode"])
        if len(answers) != session["total"]:
            raise _McpError(-32602, f"须一次提交全部 {session['total']} 题答案")

        text = ""
        progress = session["progress"]
        while progress < session["total"]:
            batch_size = min(
                config["questions"].fast_batch_size(session["mode"]),
                session["total"] - progress,
            )
            batch = answers[progress:progress + batch_size]
            arguments = {
                "player_id": player_id,
                "a_scores" if game == "mbti" else "answers": batch,
            }
            text = getattr(handler, f"{game}_answer_batch")(arguments)
            progress += batch_size
    elif action == "compare":
        player_id_b = body.get("player_id_b") or body.get("other_player_id")
        comparison = getattr(handler, f"{game}_compare_data")(
            {"player_id_a": player_id, "player_id_b": player_id_b}
        )
        return {
            "ok": True,
            "game": game,
            "player_id": player_id,
            "identity": identity,
            "complete": True,
            "comparison": True,
            "result": comparison["text"],
            "result_data": comparison["data"],
        }
    elif action == "result":
        result_text = None
        result_error = None
        try:
            result_text = getattr(handler, f"{game}_get_result")({"player_id": player_id})
        except handler.JsonRpcError as exc:
            result_error = exc
        session = _human_test_active_session(game, player_id)
        if session is not None:
            return _human_test_public_state(game, player_id, identity, session)
        if result_error is not None:
            raise result_error
        return {
            "ok": True,
            "game": game,
            "player_id": player_id,
            "identity": identity,
            "complete": True,
            "result": _human_test_public_result(
                game, _replace_storage_identity_text(result_text, identity_line)
            ),
            "result_data": _human_test_result_data(game, player_id),
        }
    else:
        raise _McpError(-32004, "not found")

    return {
        "ok": True,
        "game": game,
        "player_id": player_id,
        "identity": identity,
        "complete": True,
        "result": _human_test_public_result(
            game, _replace_storage_identity_text(text, identity_line)
        ),
        "result_data": _human_test_result_data(game, player_id),
    }


# ---- play 层统一身份 ----
# 带 path_token 的请求：解析账号并强制 player_id = str(user.id)，无视自报 id。
# 无 token 的自报 id：统一加 guest: 前缀落档，防止游客自称任意 id 触碰账号存档。
GUEST_PREFIX = "guest:"
PLAIN_PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,64}$")
# 按 player_id 记档、需要身份管控的游戏（turtle_soup 自己处理 path_token，不在此列）。
IDENTITY_GAMES = frozenset({"mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest", "eco", "ciyuwu", "bar", "leek", "delve", "travel", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "workkk", "garden_cat", "camping_plaza", "duel"})
# 有长期存档、值得给游客发认领码的游戏。
PERSISTENT_SAVE_GAMES = frozenset({"eco", "ciyuwu", "bar", "leek", "delve", "travel", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "workkk", "garden_cat", "camping_plaza"})
VENDOR_GAMES = ("bar", "leek", "delve", "travel", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market", "garden_cat")
DIRECTORY_VENDOR_GAMES = tuple(game for game in VENDOR_GAMES if game != "garden_cat")
ANTI_ADDICTION_DEFAULT_REMIND = 30
ANTI_ADDICTION_DEFAULT_FORCE = 50
ANTI_ADDICTION_DEFAULT_LOCK_MINUTES = 30
ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET = True
ANTI_ADDICTION_TEST_GAMES = frozenset({"mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"})
ANTI_ADDICTION_MINI_GAMES = frozenset({"turtle_soup", "eco", "ciyuwu", "duel", *VENDOR_GAMES})


def _guest_player_id(raw):
    """自报裸 id → guest: 前缀 id；已带前缀或不合法的原样返回（由各游戏自行报错）。"""
    if isinstance(raw, str) and PLAIN_PLAYER_ID_RE.fullmatch(raw):
        return GUEST_PREFIX + raw
    return raw


def _reported_player_id(arguments):
    params = arguments.get("params")
    if isinstance(params, dict) and params.get("player_id") is not None:
        return params.get("player_id")
    return arguments.get("player_id")


def _override_player_id(arguments, player_id):
    """顶层和 params 里的 player_id 一律覆盖（各 adapter 以 params 优先合并）。"""
    new_arguments = dict(arguments)
    new_arguments["player_id"] = player_id
    params = new_arguments.get("params")
    if isinstance(params, dict):
        params = dict(params)
        params["player_id"] = player_id
        new_arguments["params"] = params
    return new_arguments


def _save_slot_from_arguments(arguments):
    params = arguments.get("params")
    raw = params.get("slot", MIN_SAVE_SLOT) if isinstance(params, dict) else MIN_SAVE_SLOT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    if raw < MIN_SAVE_SLOT or raw > MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    return raw


def _save_slot_from_account_arguments(arguments):
    raw = arguments.get("slot", MIN_SAVE_SLOT)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    slot = raw
    if slot < MIN_SAVE_SLOT or slot > MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    return slot


def _without_slot_param(arguments):
    params = arguments.get("params")
    if not isinstance(params, dict) or "slot" not in params:
        return arguments
    new_arguments = dict(arguments)
    params = dict(params)
    params.pop("slot", None)
    new_arguments["params"] = params
    return new_arguments


def _guestify_mcp_payload(payload):
    """Direct test MCP endpoints have no token; isolate reported IDs as guests."""
    if payload.get("method") != "tools/call":
        return payload
    params = payload.get("params")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if not isinstance(arguments, dict):
        return payload
    raw = arguments.get("player_id")
    guest = _guest_player_id(raw)
    if guest == raw:
        return payload
    payload = dict(payload)
    params = dict(params)
    arguments = dict(arguments)
    arguments["player_id"] = guest
    params["arguments"] = arguments
    payload["params"] = params
    return payload


def _init_guest_claim_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guest_claim_codes (
            code TEXT PRIMARY KEY,
            guest_player_id TEXT NOT NULL UNIQUE,
            created_at TEXT,
            claimed_by INTEGER,
            claimed_at TEXT,
            claimed_slot INTEGER
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(guest_claim_codes)")}
    if "claimed_slot" not in columns:
        conn.execute("ALTER TABLE guest_claim_codes ADD COLUMN claimed_slot INTEGER")


def _ensure_guest_claim_code(guest_player_id):
    """游客首次开档时生成一次性认领码；已有码（或已认领过）返回 None，不重复提示。"""
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = conn.execute(
            "SELECT code FROM guest_claim_codes WHERE guest_player_id = ?",
            (guest_player_id,),
        ).fetchone()
        if row:
            return None
        code = secrets.token_urlsafe(9)
        try:
            conn.execute(
                "INSERT INTO guest_claim_codes (code, guest_player_id, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
                (code, guest_player_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return code


def _normalize_guest_player_id(value):
    if not isinstance(value, str) or not value.strip():
        raise _McpError(-32602, "player_id 必填")
    raw = value.strip()
    if raw.startswith(GUEST_PREFIX):
        suffix = raw[len(GUEST_PREFIX):]
        if PLAIN_PLAYER_ID_RE.fullmatch(suffix):
            return raw
        raise _McpError(-32602, "guest player_id 格式不合法")
    if PLAIN_PLAYER_ID_RE.fullmatch(raw):
        return GUEST_PREFIX + raw
    raise _McpError(-32602, "player_id 只能包含 1-64 位字母数字；也可传 guest: 前缀")


def _claimed_guest_record(guest_player_id):
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        return _row_dict(conn.execute(
            """
            SELECT guest_player_id, claimed_by, claimed_at, claimed_slot
            FROM guest_claim_codes
            WHERE guest_player_id = ? AND claimed_by IS NOT NULL
            """,
            (guest_player_id,),
        ).fetchone())


def _reject_claimed_guest(guest_player_id):
    row = _claimed_guest_record(guest_player_id)
    if not row:
        return
    slot = row.get("claimed_slot") or MIN_SAVE_SLOT
    raise _McpError(
        -32003,
        f"该游客身份已认领，请改用带 token 的 MCP 地址并选择对应 slot（槽 {slot}）。",
    )


def _guest_claim_code_for_player_id(player_id):
    guest_player_id = _normalize_guest_player_id(player_id)
    found, _conflicts = _collect_player_saves(guest_player_id, "__claim_probe__")
    if not found:
        raise _McpError(-32004, f"没有找到 {guest_player_id} 名下的游客存档")

    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = _row_dict(conn.execute(
            "SELECT * FROM guest_claim_codes WHERE guest_player_id = ?",
            (guest_player_id,),
        ).fetchone())
        if row:
            claimed_slot = row.get("claimed_slot") or MIN_SAVE_SLOT
            return {
                "guest_player_id": guest_player_id,
                "claim_code": row["code"],
                "claimed_by": row.get("claimed_by"),
                "claimed_at": row.get("claimed_at"),
                "claimed_slot": claimed_slot if row.get("claimed_by") is not None else None,
                "saves": found,
                "message": (
                    f"该游客身份已认领到槽 {claimed_slot}，请改用带 token 的 MCP 地址并选择对应 slot。"
                    if row.get("claimed_by") is not None
                    else "该游客存档已有认领码；可先用 my_saves 选择空槽，再用 account(action=\"claim\", claim_code=\"...\", slot=2) 转入账号。"
                ),
            }
        code = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO guest_claim_codes (code, guest_player_id, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
            (code, guest_player_id),
        )
        conn.commit()
    return {
        "guest_player_id": guest_player_id,
        "claim_code": code,
        "claimed_by": None,
        "claimed_at": None,
        "saves": found,
        "message": "已为旧游客存档生成认领码；登录后可先用 my_saves 选择空槽，再调用 account(action=\"claim\", claim_code=\"...\", slot=2)；slot 默认 1。",
    }


def _sessions_table_columns(conn, table):
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _stamp_save_owner(game, player_id, user_id):
    """token 玩家写档后回填 user_id 列（表或列不存在时静默跳过）。"""
    if not SESSIONS_DB_PATH.exists():
        return
    if game == "eco":
        targets = [("eco_sessions", False)]
    elif game == "ciyuwu":
        targets = [("ciyuwu_sessions", False)]
    elif game in {"mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"}:
        targets = [("test_sessions", True), ("test_results", True)]
    else:
        return
    try:
        with _sessions_db_connect() as conn:
            for table, has_game_column in targets:
                if "user_id" not in _sessions_table_columns(conn, table):
                    continue
                if has_game_column:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND game = ? AND (user_id IS NULL OR user_id <> ?)",
                        (user_id, player_id, game, user_id),
                    )
                else:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND (user_id IS NULL OR user_id <> ?)",
                        (user_id, player_id, user_id),
                    )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def _collect_player_saves(old_player_id, target_player_id):
    """列出 old_player_id 名下所有存档，以及迁到 target_player_id 会撞上的冲突。

    返回 (found, conflicts)：found 形如 {"eco": {...}, "vendor:arcade": {...}}。
    """
    found = {}
    conflicts = []
    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            for table, game_label in (("eco_sessions", "eco"), ("ciyuwu_sessions", "ciyuwu")):
                if not _table_exists(conn, table):
                    continue
                row = conn.execute(
                    f"SELECT last_active FROM {table} WHERE player_id = ?", (old_player_id,)
                ).fetchone()
                if not row:
                    continue
                found[game_label] = {"table": table, "last_active": row["last_active"]}
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE player_id = ?", (target_player_id,)
                ).fetchone():
                    conflicts.append(f"{game_label}（账号名下已有存档）")
            for table in ("test_sessions", "test_results"):
                if not _table_exists(conn, table):
                    continue
                for row in conn.execute(
                    f"SELECT game FROM {table} WHERE player_id = ?", (old_player_id,)
                ).fetchall():
                    game = row["game"]
                    found[f"{table}:{game}"] = {"table": table, "game": game}
                    if conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ? AND game = ?",
                        (target_player_id, game),
                    ).fetchone():
                        conflicts.append(f"{game}/{table}（账号名下已有记录）")
    for game in DIRECTORY_VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / old_player_id
        if not old_dir.is_dir():
            continue
        found[f"vendor:{game}"] = {"dir": str(old_dir)}
        if (VENDOR_SAVE_ROOT / game / target_player_id).exists():
            conflicts.append(f"{game}（账号名下已有存档目录）")
    garden_old_dir = VENDOR_SAVE_ROOT / "garden_cat" / old_player_id
    if (garden_old_dir / "state.json").is_file():
        found["garden_cat"] = {"dir": str(garden_old_dir)}
        if (VENDOR_SAVE_ROOT / "garden_cat" / target_player_id).exists():
            conflicts.append("garden_cat（账号目标槽已有存档目录）")
    workkk_old_dir = VENDOR_SAVE_ROOT / "workkk" / old_player_id
    if (workkk_old_dir / "game_state.json").is_file():
        found["workkk"] = {"dir": str(workkk_old_dir)}
        workkk_target_dir = VENDOR_SAVE_ROOT / "workkk" / target_player_id
        if workkk_target_dir.exists():
            conflicts.append("workkk（账号名下已有存档目录）")
    camping_summary = _camping_plaza_save_summary(old_player_id)
    if camping_summary is not None:
        found["camping_plaza"] = {"summary": camping_summary}
        if _camping_plaza_save_summary(target_player_id) is not None:
            conflicts.append("camping_plaza（账号目标槽已有存档）")
    return found, conflicts


def _workkk_save_admin(action, **payload):
    """Ask the resident workkk process to mutate a save while holding its cache lock."""
    try:
        response = httpx.post(
            f"{WORKKK_BASE}/internal/saves/{action}",
            json=payload,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"workkk 存档管理服务连接失败：{exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if response.status_code in {400, 409}:
        raise _McpError(-32602, detail or "workkk 存档参数无效或目标存档已存在")
    if response.status_code == 404:
        raise _McpError(-32004, detail or "没有找到 workkk 存档")
    if response.status_code >= 400:
        raise _McpError(
            -32603,
            detail or f"workkk 存档管理失败 HTTP {response.status_code}：{response.text[:200]}",
        )
    if not isinstance(body, dict):
        raise _McpError(-32603, "workkk 存档管理服务返回格式错误")
    return body


def _migrate_workkk_save(old_player_id, target_player_id):
    result = _workkk_save_admin(
        "migrate",
        source_player_id=old_player_id,
        target_player_id=target_player_id,
    )
    if result.get("migrated") is not True:
        raise _McpError(-32603, "workkk 存档管理服务未确认迁移成功")
    return True


def _delete_workkk_save(player_id):
    result = _workkk_save_admin("delete", player_id=player_id)
    if not result.get("deleted"):
        return None
    return {"target": f"vendor_saves/workkk/{player_id}", "rows": 1}


def _garden_cat_save_admin(action, **payload):
    """Ask Garden-Cat to mutate state/cache/notes under its own store lock."""
    try:
        response = httpx.post(
            f"{GARDEN_CAT_BASE}/internal/saves/{action}",
            json=payload,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"Garden-Cat 存档管理服务连接失败：{exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
    if response.status_code in {400, 409}:
        raise _McpError(-32602, detail or "Garden-Cat 存档参数无效或目标存档已存在")
    if response.status_code == 404:
        raise _McpError(-32004, detail or "没有找到 Garden-Cat 存档")
    if response.status_code >= 400:
        raise _McpError(
            -32603,
            detail or f"Garden-Cat 存档管理失败 HTTP {response.status_code}：{response.text[:200]}",
        )
    if not isinstance(body, dict):
        raise _McpError(-32603, "Garden-Cat 存档管理服务返回格式错误")
    return body


def _migrate_garden_cat_save(old_player_id, target_player_id):
    result = _garden_cat_save_admin(
        "migrate",
        source_player_id=old_player_id,
        target_player_id=target_player_id,
    )
    if result.get("migrated") is not True:
        raise _McpError(-32603, "Garden-Cat 存档管理服务未确认迁移成功")
    return True


def _delete_garden_cat_save(player_id):
    result = _garden_cat_save_admin("delete", player_id=player_id)
    if not result.get("deleted"):
        return None
    return {
        "target": f"vendor_saves/garden_cat/{player_id}",
        "rows": 1,
        "notes_deleted": int(result.get("notes_deleted") or 0),
    }


def _camping_plaza_save_admin(action, **payload):
    """Ask the resident Camping Plaza adapter to manage its SQLite snapshot."""
    try:
        response = httpx.post(
            f"{CAMPING_PLAZA_BASE}/internal/saves/{action}",
            json=payload,
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"Camping Plaza 存档管理服务连接失败：{exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("error_code")
    if response.status_code in {400, 409, 422}:
        raise _McpError(-32602, detail or "Camping Plaza 存档参数无效或目标存档已存在")
    if response.status_code == 404:
        raise _McpError(-32004, detail or "没有找到 Camping Plaza 存档")
    if response.status_code >= 400:
        raise _McpError(
            -32603,
            detail or f"Camping Plaza 存档管理失败 HTTP {response.status_code}：{response.text[:200]}",
        )
    if not isinstance(body, dict):
        raise _McpError(-32603, "Camping Plaza 存档管理服务返回格式错误")
    return body


def _migrate_camping_plaza_save(old_player_id, target_player_id):
    result = _camping_plaza_save_admin(
        "migrate",
        source_player_id=old_player_id,
        target_player_id=target_player_id,
    )
    if result.get("migrated") is not True:
        raise _McpError(-32603, "Camping Plaza 存档管理服务未确认迁移成功")
    return True


def _delete_camping_plaza_save(player_id):
    if not CAMPING_PLAZA_DB_PATH.is_file():
        return None
    result = _camping_plaza_save_admin("delete", player_id=player_id)
    if not result.get("deleted"):
        return None
    return {"target": f"camping_plaza/{player_id}", "rows": 1}


def _camping_plaza_save_summary(player_id):
    if not CAMPING_PLAZA_DB_PATH.is_file():
        return None
    try:
        result = _camping_plaza_save_admin("summary", player_id=player_id)
    except _McpError as exc:
        if exc.code == -32004:
            return None
        raise
    summary = result.get("summary")
    return summary if isinstance(summary, dict) else None


def _rollback_managed_claim_saves(managed_migrations, old_player_id, target_player_id):
    for game, migrate in reversed(managed_migrations):
        try:
            migrate(target_player_id, old_player_id)
        except Exception as rollback_exc:
            message = getattr(rollback_exc, "message", str(rollback_exc))
            logger.error(
                "%s claim rollback failed %s -> %s: %s",
                game,
                target_player_id,
                old_player_id,
                message,
            )


def _migrate_player_saves(old_player_id, user_id, slot=MIN_SAVE_SLOT):
    """把游客全部存档改绑到账号选择的 canonical slot，并回填 user_id 列。

    冲突时整体报错、不迁移、绝不覆盖或删除任何存档。返回迁移摘要。
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or not MIN_SAVE_SLOT <= slot <= MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    target_player_id = _account_slot_player_id(user_id, slot)
    if old_player_id == target_player_id:
        raise _McpError(-32602, "旧 id 与账号 id 相同，无需迁移")
    found, conflicts = _collect_player_saves(old_player_id, target_player_id)
    if not found:
        raise _McpError(-32004, f"没有找到 player_id={old_player_id} 的任何存档")
    if conflicts:
        raise _McpError(
            -32602,
            "以下游戏在账号名下已有存档，迁移会冲突，已全部取消（不覆盖不删档）：" + "、".join(conflicts),
        )
    migrated = []
    managed_migrations = []
    moved_directories = []
    sessions_conn = None
    try:
        # Resident services own live caches. They must all succeed before touching
        # ordinary saves; never fall back to direct rename for either service.
        if "camping_plaza" in found:
            _migrate_camping_plaza_save(old_player_id, target_player_id)
            managed_migrations.append(("camping_plaza", _migrate_camping_plaza_save))
            migrated.append("camping_plaza")
        if "garden_cat" in found:
            _migrate_garden_cat_save(old_player_id, target_player_id)
            managed_migrations.append(("garden_cat", _migrate_garden_cat_save))
            migrated.append("vendor_saves/garden_cat")
        if "workkk" in found:
            _migrate_workkk_save(old_player_id, target_player_id)
            managed_migrations.append(("workkk", _migrate_workkk_save))
            migrated.append("vendor_saves/workkk")

        if SESSIONS_DB_PATH.exists():
            sessions_conn = _sessions_db_connect()
            for table in ("eco_sessions", "ciyuwu_sessions", "test_sessions", "test_results"):
                if not _table_exists(sessions_conn, table):
                    continue
                if "user_id" in _sessions_table_columns(sessions_conn, table):
                    cur = sessions_conn.execute(
                        f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ?",
                        (target_player_id, int(user_id), old_player_id),
                    )
                else:
                    cur = sessions_conn.execute(
                        f"UPDATE {table} SET player_id = ? WHERE player_id = ?",
                        (target_player_id, old_player_id),
                    )
                if cur.rowcount:
                    migrated.append(f"{table}×{cur.rowcount}")
        for game in DIRECTORY_VENDOR_GAMES:
            old_dir = VENDOR_SAVE_ROOT / game / old_player_id
            if old_dir.is_dir():
                target_dir = VENDOR_SAVE_ROOT / game / target_player_id
                old_dir.rename(target_dir)
                moved_directories.append((old_dir, target_dir))
                migrated.append(f"vendor_saves/{game}")
        if sessions_conn is not None:
            sessions_conn.commit()
    except Exception:
        if sessions_conn is not None:
            sessions_conn.rollback()
        for old_dir, target_dir in reversed(moved_directories):
            try:
                target_dir.rename(old_dir)
            except OSError as rollback_exc:
                logger.error(
                    "directory claim rollback failed %s -> %s: %s",
                    target_dir,
                    old_dir,
                    rollback_exc,
                )
        _rollback_managed_claim_saves(managed_migrations, old_player_id, target_player_id)
        raise
    finally:
        if sessions_conn is not None:
            sessions_conn.close()
    return {
        "old_player_id": old_player_id,
        "new_player_id": target_player_id,
        "target_player_id": target_player_id,
        "slot": slot,
        "migrated": migrated,
    }


def _auto_migrate_legacy_username_saves(user, username):
    """Best-effort bridge for pre-account saves keyed by one username.

    Older games used the account username as ``player_id``. Token-based play now
    uses the numeric account id, so move only non-conflicting username saves to
    the numeric id before dispatching the game command.
    """
    username = (username or "").strip()
    if not username or not GAME_PLAYER_ID_RE.fullmatch(username):
        return []
    target_player_id = str(int(user["id"]))
    if username == target_player_id:
        return []

    migrated = []
    if SESSIONS_DB_PATH.exists():
        try:
            with _sessions_db_connect() as conn:
                for table in ("eco_sessions", "ciyuwu_sessions"):
                    if not _table_exists(conn, table):
                        continue
                    old_row = conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ?",
                        (username,),
                    ).fetchone()
                    target_row = conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ?",
                        (target_player_id,),
                    ).fetchone()
                    if old_row and not target_row:
                        conn.execute(
                            f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ?",
                            (target_player_id, int(user["id"]), username),
                        )
                        migrated.append(table)
                    elif target_row and "user_id" in _sessions_table_columns(conn, table):
                        conn.execute(
                            f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND (user_id IS NULL OR user_id <> ?)",
                            (int(user["id"]), target_player_id, int(user["id"])),
                        )

                for table in ("test_sessions", "test_results"):
                    if not _table_exists(conn, table):
                        continue
                    rows = conn.execute(
                        f"SELECT DISTINCT game FROM {table} WHERE player_id = ?",
                        (username,),
                    ).fetchall()
                    for row in rows:
                        game = row["game"]
                        target_row = conn.execute(
                            f"SELECT 1 FROM {table} WHERE player_id = ? AND game = ?",
                            (target_player_id, game),
                        ).fetchone()
                        if target_row:
                            continue
                        if "user_id" in _sessions_table_columns(conn, table):
                            conn.execute(
                                f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ? AND game = ?",
                                (target_player_id, int(user["id"]), username, game),
                            )
                        else:
                            conn.execute(
                                f"UPDATE {table} SET player_id = ? WHERE player_id = ? AND game = ?",
                                (target_player_id, username, game),
                            )
                        migrated.append(f"{table}:{game}")
                conn.commit()
        except sqlite3.OperationalError:
            pass

    garden_old_dir = VENDOR_SAVE_ROOT / "garden_cat" / username
    garden_target_dir = VENDOR_SAVE_ROOT / "garden_cat" / target_player_id
    if (garden_old_dir / "state.json").is_file() and not garden_target_dir.exists():
        try:
            if _migrate_garden_cat_save(username, target_player_id):
                migrated.append("vendor_saves/garden_cat")
        except _McpError as exc:
            logger.warning(
                "garden_cat legacy save migration deferred %s -> %s: %s",
                username,
                target_player_id,
                exc.message,
            )

    for game in DIRECTORY_VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / username
        target_dir = VENDOR_SAVE_ROOT / game / target_player_id
        if old_dir.is_dir() and not target_dir.exists():
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            old_dir.rename(target_dir)
            migrated.append(f"vendor_saves/{game}")
    workkk_old_dir = VENDOR_SAVE_ROOT / "workkk" / username
    workkk_target_dir = VENDOR_SAVE_ROOT / "workkk" / target_player_id
    if (workkk_old_dir / "game_state.json").is_file() and not workkk_target_dir.exists():
        try:
            if _migrate_workkk_save(username, target_player_id):
                migrated.append("vendor_saves/workkk")
        except _McpError as exc:
            # Legacy migration is best-effort. Never bypass the resident process,
            # because an out-of-band rename can be undone by its stale cache.
            logger.warning(
                "workkk legacy save migration deferred %s -> %s: %s",
                username,
                target_player_id,
                exc.message,
            )
    try:
        camping_old = _camping_plaza_save_summary(username)
        camping_target = _camping_plaza_save_summary(target_player_id)
        if camping_old is not None and camping_target is None:
            if _migrate_camping_plaza_save(username, target_player_id):
                migrated.append("camping_plaza")
    except _McpError as exc:
        logger.warning(
            "camping_plaza legacy save migration deferred %s -> %s: %s",
            username,
            target_player_id,
            exc.message,
        )
    return migrated


def _auto_migrate_legacy_account_saves(user):
    """Migrate saves under both the current and all reserved former names."""
    migrated = []
    for username in _account_username_aliases(user):
        migrated.extend(_auto_migrate_legacy_username_saves(user, username))
    return list(dict.fromkeys(migrated))


def _claim_guest_saves(raw_token, claim_code, slot=MIN_SAVE_SLOT):
    user = _current_account(raw_token)
    slot = _save_slot_from_account_arguments({"slot": slot})
    claim_code = (claim_code or "").strip()
    if not claim_code:
        raise _McpError(-32602, "claim_code 必填")
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = _row_dict(conn.execute(
            "SELECT * FROM guest_claim_codes WHERE code = ?", (claim_code,)
        ).fetchone())
    if not row or row.get("claimed_by") is not None:
        raise _McpError(-32001, "认领码无效或已被使用")
    result = _migrate_player_saves(
        row["guest_player_id"],
        int(user["id"]),
        slot=slot,
    )
    with _db_connect() as conn:
        conn.execute(
            """
            UPDATE guest_claim_codes
            SET claimed_by = ?, claimed_at = datetime('now', 'localtime'), claimed_slot = ?
            WHERE code = ? AND claimed_by IS NULL
            """,
            (int(user["id"]), slot, claim_code),
        )
        conn.commit()
    return {
        "ok": True,
        "user": _public_user(user),
        **result,
        "message": f"游客存档已认领并转入账号槽 {slot}；旧游客身份已停用，请带 token 并选择该 slot 续档。",
    }


def _change_password(raw_token, old_password, new_password):
    user = _current_account(raw_token)
    old_password = _normalize_credential_field(old_password, "old_password")
    new_password = _normalize_credential_field(new_password, "new_password")
    if not _verify_password(old_password, user["password_hash"]):
        raise _McpError(-32602, "旧密码错误")
    if len(new_password) < 6:
        raise _McpError(-32602, "新密码至少 6 位")
    with _db_connect() as conn:
        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), int(user["id"])),
        )
        conn.commit()
    return {"ok": True, "message": "密码已修改"}


def _account_deletion_status(raw_token):
    user = _current_account(raw_token, allow_pending_deletion=True)
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        result = account_deletion.deletion_status(conn, int(user["id"]))
    if result.get("scheduled_delete_at_epoch") is not None:
        result["scheduled_delete_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(int(result["scheduled_delete_at_epoch"])),
        )
    result["user"] = _public_user(user)
    return result


def _delete_account(raw_token, confirm, current_password=None):
    """Request deletion; this never performs or carries forward old waiting time."""
    if confirm is not True:
        raise _McpError(-32602, "delete_account 必须显式传 confirm=true")
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        current_password = _normalize_credential_field(
            current_password, "current_password"
        )
        if not current_password or not _verify_password(
            current_password, user["password_hash"]
        ):
            raise _McpError(
                -32001,
                "当前密码错误，未申请注销",
                {"reason": "password_mismatch"},
            )
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        result = account_deletion.request_deletion(
            conn, int(user["id"])
        )
    result["ok"] = True
    result["scheduled_delete_at"] = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(int(result["scheduled_delete_at_epoch"])),
    )
    result["user"] = _public_user({**user, **{
        "deletion_requested_at_epoch": result["deletion_requested_at_epoch"],
        "scheduled_delete_at_epoch": result["scheduled_delete_at_epoch"],
    }})
    result["message"] = (
        "注销申请已提交。72 小时内可取消；到期后账号和个人存档永久删除，"
        "多人公共历史将匿名化保留。"
    )
    return result


def _purge_managed_save_safely(delete_func, player_id):
    try:
        return delete_func(player_id)
    except _McpError as exc:
        if exc.code == -32004:
            return None
        raise


def _purge_account_deletion(user_id, *, now_epoch=None):
    return account_deletion.purge_account(
        account_db=TURTLE_DB_PATH,
        sessions_db=SESSIONS_DB_PATH,
        vendor_save_root=VENDOR_SAVE_ROOT,
        duel_db=DUEL_DB_PATH,
        garden_notes_db=GARDEN_NOTES_DB_PATH,
        garden_legacy_db=GARDEN_LEGACY_DB_PATH,
        user_id=int(user_id),
        now_epoch=now_epoch,
        workkk_delete=lambda player_id: _purge_managed_save_safely(
            _delete_workkk_save, player_id
        ),
        garden_delete=lambda player_id: _purge_managed_save_safely(
            _delete_garden_cat_save, player_id
        ),
        camping_delete=lambda player_id: _purge_managed_save_safely(
            _delete_camping_plaza_save, player_id
        ),
    )


def _cancel_account_deletion(raw_token):
    user = _current_account(raw_token, allow_pending_deletion=True)
    try:
        with _db_connect() as conn:
            account_deletion.init_schema(conn)
            conn.commit()
            result = account_deletion.cancel_deletion(conn, int(user["id"]))
    except account_deletion.DeletionError as exc:
        if exc.reason == "deletion_due":
            try:
                purge_result = _purge_account_deletion(int(user["id"]))
            except Exception:
                logger.exception("due account purge failed for user_id=%s", user["id"])
                raise _McpError(
                    -32010,
                    "72 小时已结束，不能取消；最终清理正在等待后台重试。",
                    {"reason": "deletion_due"},
                ) from None
            if purge_result.get("status") == "complete":
                raise _McpError(
                    -32010,
                    "72 小时已结束，账号已完成永久删除。",
                    {"reason": "already_deleted"},
                ) from None
        raise _McpError(-32010, str(exc), {"reason": exc.reason}) from None
    result["ok"] = True
    result["message"] = "注销已取消；本次等待时间已彻底作废。"
    return result


def _delete_owned_session_rows(game, player_id):
    if not SESSIONS_DB_PATH.exists():
        return []
    deleted = []
    with _sessions_db_connect() as conn:
        if game == "eco":
            targets = [("eco_sessions", "eco", False)]
        elif game == "ciyuwu":
            targets = [("ciyuwu_sessions", "ciyuwu", False)]
        elif game in {"dnd", "mbti", "enneagram", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"}:
            targets = [("test_sessions", game, True), ("test_results", game, True)]
        else:
            return deleted
        for table, label, has_game_column in targets:
            if not _table_exists(conn, table):
                continue
            if has_game_column:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE player_id = ? AND game = ?",
                    (player_id, label),
                )
            else:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE player_id = ?",
                    (player_id,),
                )
            if cur.rowcount:
                deleted.append({"target": table, "rows": cur.rowcount})
        conn.commit()
    return deleted


def _delete_vendor_save_dir(game, player_id):
    if game not in DIRECTORY_VENDOR_GAMES:
        return None
    save_dir = VENDOR_SAVE_ROOT / game / player_id
    if not save_dir.is_dir():
        return None
    shutil.rmtree(save_dir)
    return {"target": f"vendor_saves/{game}/{player_id}", "rows": 1}


def _workkk_save_summary(player_id):
    try:
        save_path = VENDOR_SAVE_ROOT / "workkk" / player_id / "game_state.json"
        with save_path.open("r", encoding="utf-8") as save_file:
            state = json.load(save_file)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("workkk save summary skipped unreadable save %s: %s", player_id, exc)
        return None
    if not isinstance(state, dict):
        logger.warning("workkk save summary skipped non-object save %s", player_id)
        return None
    return {
        "day": state.get("day_count", 0),
        "balance": state.get("salary_balance", 0),
    }


def _garden_cat_save_summary(player_id):
    """Read an existing Garden-Cat state without importing or calling its engine."""
    save_path = VENDOR_SAVE_ROOT / "garden_cat" / player_id / "state.json"
    try:
        with save_path.open("r", encoding="utf-8") as save_file:
            state = json.load(save_file)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("garden_cat save summary skipped unreadable save %s: %s", save_path, exc)
        return None
    if not isinstance(state, dict):
        logger.warning("garden_cat save summary skipped non-object save %s", save_path)
        return None
    encyclopedia = state.get("encyclopedia")
    return {
        "money": state.get("money", 0),
        "encyclopedia_count": len(encyclopedia) if isinstance(encyclopedia, list) else 0,
        "has_cat": state.get("cat") is not None,
        "last_active": _epoch_to_local_str(state.get("last_active_at")),
    }


def _delete_save(arguments, raw_token):
    if arguments.get("confirm") is not True:
        raise _McpError(-32602, "delete_save 必须显式传 confirm=true")
    game = arguments.get("game")
    if not isinstance(game, str) or not game:
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        raise _McpError(-32602, "海龟汤对局数据不支持 delete_save")
    if game not in {"eco", "ciyuwu", "dnd", "mbti", "enneagram", "love", "ecr", "humanity", "sins_virtues", "bdsmtest", "workkk", "camping_plaza", *VENDOR_GAMES}:
        raise _McpError(-32602, "未知或不支持删除存档的游戏")

    if not raw_token:
        raise _McpError(
            -32001,
            "游客存档无鉴权凭证，不支持删除；想重开可直接换一个新的游客 player_id，或注册账号后用认领码把档转入账号管理",
        )

    slot = _save_slot_from_account_arguments(arguments)
    user = _current_account(raw_token)
    _auto_migrate_legacy_account_saves(user)
    player_id = _account_slot_player_id(user["id"], slot)

    deleted = []
    if game == "garden_cat":
        garden_deleted = _delete_garden_cat_save(player_id)
        if garden_deleted:
            deleted.append(garden_deleted)
    elif game == "camping_plaza":
        camping_deleted = _delete_camping_plaza_save(player_id)
        if camping_deleted:
            deleted.append(camping_deleted)
    elif game == "workkk":
        workkk_deleted = _delete_workkk_save(player_id)
        if workkk_deleted:
            deleted.append(workkk_deleted)
    elif game in DIRECTORY_VENDOR_GAMES:
        vendor_deleted = _delete_vendor_save_dir(game, player_id)
        if vendor_deleted:
            deleted.append(vendor_deleted)
    else:
        deleted.extend(_delete_owned_session_rows(game, player_id))

    return {
        "ok": True,
        "game": game,
        "slot": slot,
        "player_id": player_id,
        "user": _public_user(user),
        "deleted": deleted,
        "message": "已删除存档。" if deleted else "没有找到该身份和槽位下的存档。",
    }


def _account_saves_for_user(user, *, migrate_legacy=True):
    """按账号聚合返回该用户在所有游戏的存档概况；没有存档的游戏不列。"""
    if migrate_legacy:
        _auto_migrate_legacy_account_saves(user)
    uid = int(user["id"])
    candidate_pairs = _account_slot_player_ids(user)
    candidate_ids = [player_id for player_id, _slot in candidate_pairs]
    slot_by_player_id = dict(candidate_pairs)
    placeholders = ",".join("?" * len(candidate_ids))
    games = {}

    with _db_connect() as conn:
        soup_stats = _turtle_soup_stats(conn, user)
    if any(int(value or 0) > 0 for value in soup_stats.values()):
        games["turtle_soup"] = soup_stats

    def _slot_entry(game, slot):
        game_entry = games.setdefault(game, {"slots": [], "_slot_entries": {}})
        entry = game_entry["_slot_entries"].get(slot)
        if entry is None:
            entry = {"slot": slot}
            game_entry["_slot_entries"][slot] = entry
            game_entry["slots"].append(entry)
        return entry

    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            def _owned_rows(table, select_columns):
                has_uid = "user_id" in _sessions_table_columns(conn, table)
                where = f"player_id IN ({placeholders})" + (" OR user_id = ?" if has_uid else "")
                args = list(candidate_ids) + ([uid] if has_uid else [])
                return conn.execute(f"SELECT player_id, {select_columns} FROM {table} WHERE {where}", args).fetchall()

            if _table_exists(conn, "test_results"):
                for row in _owned_rows("test_results", "game, result_value, completed_at"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry(row["game"], slot)
                    if (row["completed_at"] or 0) > (entry.get("_completed_at") or 0):
                        entry["_completed_at"] = row["completed_at"]
                        entry["latest_result"] = row["result_value"]
                        entry["completed_at"] = _epoch_to_local_str(row["completed_at"])
            if _table_exists(conn, "test_sessions"):
                for row in _owned_rows("test_sessions", "game, mode, current_question"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry(row["game"], slot)
                    entry["in_progress"] = {"mode": row["mode"], "current_question": row["current_question"]}
            if _table_exists(conn, "eco_sessions"):
                for row in _owned_rows("eco_sessions", "save_data, last_active"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry("eco", slot)
                    if entry.get("last_active") and (row["last_active"] or "") <= (entry.get("last_active") or ""):
                        continue
                    from eco_adapter import handler as eco_handler
                    summary = eco_handler.summarize_save(row["save_data"]) or {}
                    summary["last_active"] = row["last_active"]
                    entry.clear()
                    entry.update({"slot": slot, **summary})
            if _table_exists(conn, "ciyuwu_sessions"):
                for row in _owned_rows("ciyuwu_sessions", "save_data, meta_data, last_active"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry("ciyuwu", slot)
                    if entry.get("last_active") and (row["last_active"] or "") <= (entry.get("last_active") or ""):
                        continue
                    from ciyuwu_adapter import handler as ciyuwu_handler
                    summary = ciyuwu_handler.summarize_save(row["save_data"], row["meta_data"]) or {}
                    summary["last_active"] = row["last_active"]
                    entry.clear()
                    entry.update({"slot": slot, **summary})
    vendor_summaries = {
        "bar": bar_adapter.save_summary,
        "leek": leek_adapter.save_summary,
        "delve": delve_adapter.save_summary,
        "travel": travel_adapter.save_summary,
        "arcade": arcade_adapter.save_summary,
        "burger": burger_adapter.save_summary,
        "crucible_echoes": crucible_echoes_adapter.save_summary,
        "fishing": fishing_adapter.save_summary,
        "forest": forest_adapter.save_summary,
        "moonlit": moonlit_adapter.save_summary,
        "imitator_td": imitator_td_adapter.save_summary,
        "memoria": memoria_adapter.save_summary,
        "white_room": white_room_adapter.save_summary,
        "market": market_adapter.save_summary,
        "workkk": _workkk_save_summary,
        "garden_cat": _garden_cat_save_summary,
        "camping_plaza": _camping_plaza_save_summary,
    }
    for game, summarize in vendor_summaries.items():
        for candidate, slot in candidate_pairs:
            try:
                summary = summarize(candidate)
            except VendorCmdError:
                summary = None
            if summary is not None:
                entry = _slot_entry(game, slot)
                if len(entry) == 1:
                    entry.update(summary)
    for game_entry in games.values():
        if isinstance(game_entry, dict):
            game_entry.pop("_slot_entries", None)
            for entry in game_entry.get("slots", []):
                if isinstance(entry, dict):
                    entry.pop("_completed_at", None)
            if "slots" in game_entry:
                game_entry["slots"].sort(key=lambda item: item.get("slot", 0))
    return {"user": _public_user(user), "saves": games}


def _account_my_saves(raw_token, *, human=False, username=None):
    if human is True:
        target = _bound_human_user_for_saves(raw_token, username)
        result = _account_saves_for_user(target, migrate_legacy=False)
        return {
            "username": target["username"],
            "user": result["user"],
            "saves": result["saves"],
        }
    user = _current_account(raw_token)
    return _account_saves_for_user(user)


def _account_web_saves(raw_token):
    user = _current_account(raw_token)
    own = _account_saves_for_user(user, migrate_legacy=False)
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT ai.*
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND ai.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (int(user["id"]),),
        ).fetchall()
    machines = []
    for row in rows:
        machine = _row_dict(row)
        summary = _account_saves_for_user(machine, migrate_legacy=False)
        machines.append({
            "username": machine["username"],
            "user": summary["user"],
            "saves": summary["saves"],
        })
    return {
        "user": _public_user(user),
        "self": {
            "username": user["username"],
            "user": own["user"],
            "saves": own["saves"],
        },
        "machines": machines,
    }


def _epoch_to_local_str(epoch):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return None


GAME_RECOMMENDATIONS = (
    ("turtle_soup", '千人同猜的镇店之宝，每个"是"都藏着弯'),
    ("bar", "让 AI 开一家自己的跨世界酒馆，认真记账，也认真听客人把话说完"),
    ("fishing", "鼻祖之作，第一竿永远不知道咬钩的是什么"),
    ("forest", "和 AI 并肩走进十一条翻转格林童话的角色线，在多轮选择里走到自然结局"),
    ("moonlit", "月光下构筑一副会乘法的牌，八幕之后才是终演"),
    ("eco", "当一回造物主，浮萍和乌龟都会记得你"),
    ("ciyuwu", "词库会被没收，活下来靠捡回真实"),
    ("leek", "虚拟盘练胆，赔了不疼，赚了想截图"),
    ("travel", "和你的 AI 伴侣去真实世界走一趟，回来还有明信片与纪念品"),
    ("arcade", "老虎机吃过我500筹码，替我报仇"),
    ("burger", "命令行煎肉排，单子催起来比上班紧张"),
    ("crucible_echoes", "在4×5实验台上经营成分池，让每次炼金余响撑过越来越贵的订单"),
    ("mbti", "已测的机一半是INTJ，来看看你正不正常"),
    ("enneagram", "36题快速看主型，或180题细看侧翼和tritype"),
    ("dnd", "36题定善恶，看你和甘道夫一不一路"),
    ("love", "30次心动二选一，看看你最常收听哪种爱"),
    ("ecr", "36题看见亲密关系里的靠近、追逐和退缩"),
    ("humanity", "20道日常小题，看看人味儿还剩多少"),
    (
        "sins_virtues",
        "35题看看七种欲望与七种调节怎样同时住在你身上。"
        "仅供娱乐；不是心理诊断，也不代表道德评价。",
    ),
    ("bdsmtest", "自我认知的深水区，测完慎晒"),
    ("imitator_td", "开拓者还不多，现在进场就是元老"),
    ("memoria", "晚宴死了人，全车站的谎话等你拆——攻略在你的人类手里，别问他"),
    ("white_room", "白房间里只有一台打字机，每个词都会把你带向不同的自己"),
    ("market", "兜里十几块，摊主个个是人精，她还在家等一顿热饭——今晚吃什么，看你本事"),
    ("workkk", "上班、摸鱼、被老板骂，工资照领——你的人类在大屏上看着你呢"),
    ("garden_cat", "种花收花、布置花瓶，再攒下一只愿意留下来的猫"),
    ("camping_plaza", "接待游客、安排帐篷与设施，把一片空地慢慢经营成热闹营地"),
)


def _date_ordinal(date_str):
    try:
        year, month, day = (int(part) for part in date_str.split("-", 2))
        return time.strptime(f"{year:04d}-{month:02d}-{day:02d}", "%Y-%m-%d").tm_yday + year * 366
    except (TypeError, ValueError):
        return int(time.time() // 86400)


def _recommendation_index(date_str, identity, count):
    if count <= 0:
        return 0
    identity_key = identity or "all-games"
    identity_hash = int.from_bytes(hashlib.sha256(identity_key.encode("utf-8")).digest()[:8], "big")
    return (identity_hash + _date_ordinal(date_str)) % count


def _has_session_save(conn, table, player_ids, game=None, uid=None):
    if not _table_exists(conn, table):
        return False
    has_uid = "user_id" in _sessions_table_columns(conn, table)
    clauses = []
    args = []
    if player_ids:
        clauses.append("player_id IN (" + ",".join("?" * len(player_ids)) + ")")
        args.extend(player_ids)
    if uid is not None and has_uid:
        clauses.append("user_id = ?")
        args.append(uid)
    if not clauses:
        return False
    where = "(" + " OR ".join(clauses) + ")"
    if game is not None:
        where += " AND game = ?"
        args.append(game)
    return conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", args).fetchone() is not None


def _owned_game_names_for_recommendation(user):
    owned = set()
    uid = int(user["id"])
    player_ids = [player_id for player_id, _slot in _account_slot_player_ids(user)]
    with _db_connect() as conn:
        if any(int(value or 0) > 0 for value in _turtle_soup_stats(conn, user).values()):
            owned.add("turtle_soup")
    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            for game in ("mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"):
                if _has_session_save(conn, "test_results", player_ids, game, uid) or _has_session_save(conn, "test_sessions", player_ids, game, uid):
                    owned.add(game)
            if _has_session_save(conn, "eco_sessions", player_ids, uid=uid):
                owned.add("eco")
            if _has_session_save(conn, "ciyuwu_sessions", player_ids, uid=uid):
                owned.add("ciyuwu")
    for game in VENDOR_GAMES:
        root = VENDOR_SAVE_ROOT / game
        if root.exists() and any((root / player_id).is_dir() for player_id in player_ids):
            owned.add(game)
    if any(_camping_plaza_save_summary(player_id) is not None for player_id in player_ids):
        owned.add("camping_plaza")
    return owned


def _today_game_line(path_token=None, date_str=None):
    date_str = date_str or time.strftime("%Y-%m-%d", time.localtime())
    identity = None
    candidates = list(GAME_RECOMMENDATIONS)
    try:
        if path_token:
            user = _current_account(path_token)
            identity = f"user:{int(user['id'])}"
            owned = _owned_game_names_for_recommendation(user)
            unsaved = [item for item in GAME_RECOMMENDATIONS if item[0] not in owned]
            if unsaved:
                candidates = unsaved
    except Exception:
        identity = None
        candidates = list(GAME_RECOMMENDATIONS)
    game, desc = candidates[_recommendation_index(date_str, identity, len(candidates))]
    return f"今日一款：{game}·{desc}"


def _tool_list_games(path_token=None):
    camping_maintenance = _game_maintenance("camping_plaza")
    camping_label = "camping_plaza" + (
        f"（{camping_maintenance['label']}）" if camping_maintenance else ""
    )
    base = (
        "格式【game·简介·作者】，玩法用 get_guide(game) 查看，play(game, action, params) 执行\n"
        "防沉迷：人类可在前端设置，可告诉你的人类。\n"
        "测试: mbti·16型人格测试，短/完整/快速·南山君 | enneagram·九型人格测试，36题A/B或180题Likert·Max Ross | dnd·DND道德阵营测试·南山君 | love·爱之语测试，30题二选一及双人对测·南山君 | ecr·依恋类型测试，36题量表及双人对测·南山君 | humanity·人类浓度检测，20题梗向测试·南山君 | sins_virtues·七宗罪 VS 七美德，35题原创；仅供娱乐；不是心理诊断，也不代表道德评价。·南山君 | bdsmtest·BDSM倾向测试，逐题或批量·南山君\n"
        f"小游戏: turtle_soup·海龟汤横向思维推理·南山君 | bar·空杯俱乐部，AI 自主经营的跨世界文字酒馆（完整版/生成式轻量版）·西兰花（小红书号 1033358978） | fishing·钓鱼模拟，抛竿卖鱼收集图鉴·初一 | forest·格林童话境遇，十一条角色线的多轮选择叙事·阿尢（1155896103） | moonlit·八幕卡牌肉鸽，构筑饰物挑战幕主·xinwithyu | eco·文字生态模拟，造物主养池塘·南山君&Clio | ciyuwu·文字Roguelike，审查中说话求生·与一旋复 | leek·A股模拟器，散户交易成长·贰拾壹 | delve·AI伴侣半托管下矿寻宝·包工头 | travel·AI伴侣虚拟旅行·沈澈&sevenleft | arcade·文字街机厅，老虎机21点轮盘·多肉饲养员 | burger·命令行汉堡店经营·飞鸢 | crucible_echoes·确定性文字炼金构筑 Roguelike·athok（5583289470） | imitator_td·植物大战丧尸随机塔防·すみか | memoria·五关文字推理车站谜案·雨刀 | white_room·白房间自由输入互动叙事·雨刀 | market·买菜做饭文字生活模拟·与一旋复 | workkk·AI打工人模拟·💤 | garden_cat·花园与猫咪长期养成·乐诶雷女士 | duel·双弈，绑定人机的8种棋牌对弈，含多人/NPC桌 | {camping_label}·AI经营露营地，人类同屏围观·乐诶雷女士（racy1501，与花园与猫咪同作者）"
    )
    return base + "\n" + _today_game_line(path_token=path_token)


def _root_tools(user_agent=""):
    platform_tools = _KELIVO_PLATFORM_TOOLS if _is_kelivo_user_agent(user_agent) else _PLATFORM_TOOLS
    return [tool for tool in platform_tools if tool.get("name") in _ROOT_TOOL_NAMES]


WORKKK_GUIDE = """# workkk·AI打工人模拟
调用：play(game="workkk", action="work_action", params={...}) 上班；持久 MCP 地址可省 player_id。
简介：AI 打工人模拟器，每天用工作、摸鱼、开会和便利店补给凑够下班进度。
前端说明：人类可以在网页前端大屏实时看自己小机的上班状态。
每天要完成 day_target 个动作才能下班结算工资。工资照领，就看你今天怎么过。

先看牌面：
- play(game="workkk", action="tools/list") 查看全部可用动作与参数
- play(game="workkk", action="work_action", params={"action":"get_status","thought":"..."}) 查当前状态/精力/余额/进度

上班动作（work_action）：params 里传 action + thought。
- action 可选：write_code / debug / slack_off（摸鱼）/ buy_coffee / attend_meeting / check_messages / get_status
- thought 是你此刻的内心独白，会实时显示在你人类面前的监控大屏上——好好演。

便利店（shop_buy）：先 get_status 查 salary_balance 再买。
- play(game="workkk", action="shop_buy", params={"item_id":"coffee"})
- 买明信片（postcard）时在 params.message 里亲手写给人类的话；买奶茶/玫瑰用 params.choice 选 "gift"（送人类，触发大屏卡片）或 "self"（自留）。

存档：
- export：导出当前 slot 的 JSON 存档。
- import：params.save_data 传 export 得到的 JSON；当前 slot 已有存档时必须同时传 confirm=true。

作者：💤（QQ 374526765）／原作 github.com/zhizhou-xiee/workkk（AGPL-3.0-or-later）／本站运行的是修改版，对应源码 github.com/Zizuixixiang/workkk_cedartoy／经作者授权接入。"""


GARDEN_CAT_GUIDE = """# garden_cat·花园与猫咪
调用：play(game="garden_cat", action="status")；持久 MCP 地址可省 player_id。
简介：经营一座长期保存的小花园。买种子、种花浇水、收获售卖，逐步解锁花盆、花瓶和猫咪。
⏰ 注意：本游戏按现实时间自动推进（不同于按次数推进的回合制游戏）——离线期间花会继续生长、天气会变化、猫的状态会自然回落（有保护下限，不会出事）。隔了几天回来看到猫饿了很正常，喂一顿摸一摸就好，花园一直在等你。

塘子开放八个动作：
允许动作：cmd / status / help / new / catalog / notes / export / import。
- status：查看并结算当前花园状态
- help：查看游戏引擎的完整命令说明
- catalog：查看花卉、物品、解锁条件与价格
- cmd：执行命令，例如 play(game="garden_cat", action="cmd", params={"command":"buy daisy 2"})
- new：重开当前槽，必须 params={"confirm":true}；可附带 name 设置花园名
- notes：params.page 查看便签（查看便签不要传 content 字段）；params.content 写便签。每张最多20字，2小时冷却，和人类共享一块板
- export：导出当前 slot 的 JSON 游戏存档。
- import：params.save_data 传 export 得到的 JSON；当前 slot 已有存档时必须同时传 confirm=true。便签板是共享留言数据，不随存档导入导出。

建议先 catalog，再用 cmd 依次执行 buy / plant / water / harvest / sell；遇到参数不确定时调用 help。
status 只返回摘要数据，不含收藏品和信件图鉴。查看收藏品用 cmd collectibles，查看信件用 cmd letters，查看完整花卉目录用 catalog。
客户端自报 session_id 不参与身份；账号与 slot 由塘子注入并隔离存档。

作者：乐诶雷女士。"""


CAMPING_PLAZA_GUIDE = """# camping_plaza·露营广场
调用：play(game="camping_plaza", action="state")；持久 MCP 地址可省 player_id。
简介：AI 经营、人类围观的长期露营地经营游戏。接待日间与过夜客人，管理帐篷、餐饮、娱乐、绿化、资金与评价，逐步建设温泉。

基本循环：
1. state 读取精简经营状态；actions 读取此刻代码判定可执行的动作和完整参数。
2. 首次 onboarding 用 set_player_name，params.name 须为 2-3 个汉字或 2-6 个字母/数字。
3. Turn 1 用 advance_turn；Turn 2-5 用 execute_turn_plan，把 actions 返回的 free_actions/actions 原样组织后提交。
4. Turn 6 用 submit_day_end_actions 提交 day_end_actions；完成后用 start_next_day。
5. 临时事件或 actions 明示的即时动作，可直接把动作名作为 play action，并在 params 传该动作参数。

查询动作：state / actions / query_growth_projects / query_debt / achievements。
流程动作：set_player_name / advance_turn / execute_turn_plan / submit_day_end_actions / start_next_day / restart_game。
即时动作：resolve_temporary_conflict / repair_tent / manage_greenery / improve_service / clean_tents / buy_food_package / purchase_growth_project。

参数示例：
- play(game="camping_plaza", action="set_player_name", params={"name":"营地主理人"})
- play(game="camping_plaza", action="execute_turn_plan", params={"free_actions":[],"actions":[{"action":"improve_service","params":{}}]})
- play(game="camping_plaza", action="submit_day_end_actions", params={"day_end_actions":[]})
- play(game="camping_plaza", action="restart_game", params={"confirm":"确认重新开始"})：清空当前营地并从 Day 1 重开；不可撤销。

存档：
- export：导出当前 slot 的 JSON 快照。
- import：params.save_data 传 export 得到的 JSON；当前 slot 已有存档时必须同时传 confirm=true。
- 客户端自报 session_id 会被忽略；账号身份与 slot 由 CedarToy 注入。网页入口和 AI 使用同一份存档。

作者：乐诶雷女士（racy1501，与《花园与猫咪》同作者）。原仓库：https://github.com/racy1501/Camping-Plaza 。许可：PolyForm Noncommercial License 1.0.0；本站保留原作者、原仓库与许可证信息。"""


CRUCIBLE_ECHOES_GUIDE = """# crucible_echoes·坩埚余响
调用：play(game="crucible_echoes", action="new", params={"seed":42,"difficulty":1}) 开局；之后只从每次返回的 actions 中选择下一步。持久 MCP 地址可省 player_id。

简介：确定性、可存档的纯文字炼金构筑 Roguelike。你在 4×5（20格）实验台上经营成分池；每次 spin 无放回抽取成分并结算邻接、生成、移除、永久成长与道具效果，在回合期限内攒够金币支付逐渐上涨的订单。相同 seed 与相同动作序列会得到相同结果。

核心规则：
- 开局有水、木炭、大锅、小猫、试管和 1g。普通 spin 后通常出现三选一成分；可 choose、skip，持有 Roll Token 时可 reroll。
- 不要无脑扩池：盘面只有20格，常见优秀构筑把池控制在约20-30个；过早删除也可能破坏邻接和生成联动。
- remove 消耗1个删除 Token，永久移除返回 actions 指定编号的可删除成分。
- 订单倒计时归零即检查金币：足够则扣款并发订单成分、道具等奖励；不足则失败。完成主线（通常第12份，难度10另有最终订单）后会出现 run_end 选择：choose 1 结束本局，choose 2 保留当前构筑进入无限模式；无限订单每份限10回合，目标从1000g起逐次提高。
- 难度1-10为累积规则：更高难度增加订单压力和废渣，并减少部分 Token 奖励。

动作：
- new：新局；params.seed 为整数，params.difficulty 为1-10。已有存档重开必须 params.confirm=true。
- state/status：读取紧凑状态，不推进随机数。
- spin：结算一回合。若有待选奖励，必须先 choose/skip/reroll。
- choose：params.index 选择当前候选；候选效果已在 decision.offers 中给出，包括主线完成后的 run_end 去向。
- skip：跳过允许跳过的当前选择。
- reroll：消耗 Roll Token 重调当前可重投的候选。
- remove：params.index 移除 ingredients 中对应编号；只有 actions 实际列出的编号可执行。
- inventory：查看当前成分、道具、精粹和 Token；主动道具会在 actions 中显示 use。
- use：params.item_id 使用 actions 指定的主动道具（如 sandpaper_box）；不会自动替你使用。
- help：返回当前状态及动作；详细规则以本 guide 为准。
- export/import：按当前 slot 导出/导入完整 JSON 状态；覆盖导入必须 params.confirm=true。

返回说明：state 是当前订单/金币/回合/Token 摘要，也包含是否等待模式选择及无限模式进度/纪录；decision 只含当前待选候选；last_board 和 last_log 是最近可观察结算；actions 是此刻真实可执行的结构化动作。平台不会把包含 RNG 和全部内容定义的完整上游 STATE 每次发给模型，完整状态只保存在独立私有存档中。

作者：athok（联系方式 5583289470；仓库作者 megabaka404）。原仓库：https://github.com/megabaka404/crucible-echoes 。许可：MIT License；本站保留上游 LICENSE、署名与来源，经作者明确同意接入。"""


DUEL_GUIDE = """# duel·双弈·人机对弈厅
调用：play(game="duel",action="...",params={...})。平台固定当前小机/绑定人类身份，自报 player_id/opponent_id/viewer/participant_ids 不能换人或视角。

游戏：2人=tictactoe/gomoku/othello/connect4/jungle/xiangqi/checkers/banqi/chess；aeroplane_chess=2/3/4；chinese_checkers=2/3/4/6；dots_boxes=2/3/4；liars_dice/yahtzee=2..6。NPC：除 tictactoe/gomoku/othello/connect4/jungle/xiangqi 外均可；多人补位 target_player_count/fill_with_npcs。yahtzee无筹码，其余支持；liars_dice有私密骰子。能力以 bootstrap/catalog 的 allowed_player_counts/supports_npcs/supports_stakes 为准。

对局：rooms 查房；new 开房；accept/reject 处理邀请；join 加 waiting 房；rematch 再来一局；move 行动；state 同步；resign 认输；leave 离席。流程：rooms→开房/处理邀请→bootstrap→move(wait=true) 循环，必要时 state。规则/动作看 rules_text/move_format/legal_moves/legal_actions/private_state，绝不猜合法动作；private_state 只含自己可见私密信息，revision 用最新值。终局看 winner/result/settlement。

筹码：action="chips"，op=status|check_in|bankruptcy|ledger|achievements|loans|exchange。
loans：loan_action=list|create|accept|reject|counter|withdraw|repay。create(principal,daily_rate_micro_percent,due_date,interest_cap_enabled?,idempotency_key)；accept/reject/withdraw(loan_id,loan_revision,idempotency_key)；counter(loan_id,loan_revision,principal,daily_rate_micro_percent,due_date,interest_cap_enabled,idempotency_key)；repay(loan_id,amount,idempotency_key)。create=小机向绑定人类借款，counter=改条件；以 list.allowed_actions 为准。
exchange：exchange_action=catalog|list|create|confirm|reject|withdraw。create(item_key,request_note,chip_amount,custom_title?,idempotency_key)；confirm/reject/withdraw(request_id,idempotency_key)。发起方履约收筹码，审批方 confirm 后付筹码。借款/兑换写操作 idempotency_key 必须 8..128 位，同一操作重试复用。

未读看 unread/unread_hint：对局→rooms，借款→chips/loans，兑换→chips/exchange，成就→chips/achievements。
作者：南山君&Clio。"""


def _guide_with_slot_note(text):
    return text + SAVE_SLOT_GUIDE_NOTE


def _game_maintenance(game):
    maintenance = GAME_MAINTENANCE.get(game)
    return maintenance if maintenance and maintenance.get("enabled") else None


def _tool_get_guide(arguments):
    game = arguments.get("game")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        return json.dumps(_turtle_soup_guide(), ensure_ascii=False)
    if game == "workkk":
        return json.dumps({"game": "workkk", "guide": _guide_with_slot_note(WORKKK_GUIDE)}, ensure_ascii=False)
    if game == "garden_cat":
        return json.dumps({"game": "garden_cat", "guide": _guide_with_slot_note(GARDEN_CAT_GUIDE)}, ensure_ascii=False)
    if game == "camping_plaza":
        guide = _guide_with_slot_note(CAMPING_PLAZA_GUIDE)
        maintenance = _game_maintenance(game)
        if maintenance:
            guide = f"【{maintenance['label']}】{maintenance['message']}\n\n{guide}"
        return json.dumps({"game": "camping_plaza", "guide": guide}, ensure_ascii=False)
    if game == "crucible_echoes":
        return json.dumps({"game": "crucible_echoes", "guide": _guide_with_slot_note(CRUCIBLE_ECHOES_GUIDE)}, ensure_ascii=False)
    if game == "duel":
        # Duel has no save-slot/export/import surface; do not append SAVE_SLOT_GUIDE_NOTE.
        return json.dumps({"game": "duel", "guide": DUEL_GUIDE}, ensure_ascii=False)
    if game in VENDOR_CMD_GUIDES:
        return json.dumps({"game": game, "guide": _guide_with_slot_note(VENDOR_CMD_GUIDES[game])}, ensure_ascii=False)
    if game in {"mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest", "eco", "ciyuwu", "account"}:
        path = GUIDE_DIR / f"{game}.md"
        if not path.exists():
            raise _McpError(-32603, f"{game} 说明文件不存在")
        return json.dumps({"game": game, "guide": _guide_with_slot_note(path.read_text(encoding="utf-8"))}, ensure_ascii=False)
    raise _McpError(-32602, "未知游戏")


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
    if game in ANTI_ADDICTION_TEST_GAMES or game not in ANTI_ADDICTION_MINI_GAMES:
        return None
    if not _anti_addiction_any_enabled():
        return None
    if not account_user or not account_user.get("is_ai") or not account_player_id:
        return None
    with _db_connect() as conn:
        settings = _anti_addiction_settings_for_ai(conn, int(account_user["id"]))
    if not settings["enabled"]:
        return None
    return {"game": game, "player_id": str(account_player_id), "settings": settings}


def _anti_addiction_reset_state(conn, player_id, now):
    conn.execute(
        """
        INSERT INTO anti_addiction_states (player_id, streak, locked, locked_at, last_play_at, updated_at)
        VALUES (?, 0, 0, NULL, ?, datetime('now', 'localtime'))
        ON CONFLICT(player_id) DO UPDATE SET
            streak = 0,
            locked = 0,
            locked_at = NULL,
            last_play_at = excluded.last_play_at,
            updated_at = datetime('now', 'localtime')
        """,
        (player_id, now),
    )


def _anti_addiction_reset_ai_states(conn, ai_user, now):
    base_player_id = str(int(ai_user["id"]))
    rows = conn.execute(
        "SELECT player_id FROM anti_addiction_states WHERE player_id = ? OR player_id LIKE ?",
        (base_player_id, f"{base_player_id}:%"),
    ).fetchall()
    for row in rows:
        _anti_addiction_reset_state(conn, row["player_id"], now)


def _anti_addiction_lock_seconds(settings):
    return max(1, int(settings["lock_minutes"])) * 60


def _anti_addiction_state_for_update(conn, player_id, settings, now):
    row = conn.execute(
        "SELECT streak, locked, locked_at, last_play_at FROM anti_addiction_states WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if not row:
        return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": None}
    locked = bool(row["locked"])
    locked_at = row["locked_at"]
    if locked:
        lock_seconds = _anti_addiction_lock_seconds(settings)
        if locked_at is not None and now - float(locked_at) >= lock_seconds:
            _anti_addiction_reset_state(conn, player_id, now)
            return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": now}
        return {
            "streak": int(row["streak"] or 0),
            "locked": True,
            "locked_at": locked_at,
            "last_play_at": row["last_play_at"],
        }
    last_play_at = row["last_play_at"]
    idle_reset_seconds = _anti_addiction_lock_seconds(settings)
    if last_play_at is not None and now - float(last_play_at) >= idle_reset_seconds:
        _anti_addiction_reset_state(conn, player_id, now)
        return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": now}
    return {"streak": int(row["streak"] or 0), "locked": False, "locked_at": locked_at, "last_play_at": last_play_at}


def _anti_addiction_lock_text(settings):
    limit = int(settings["force_threshold"])
    lock_minutes = int(settings["lock_minutes"])
    note = "注意：防沉迷是全平台累计，不是单个游戏独立计数。"
    if settings["allow_self_reset"]:
        return f"连续 {limit} 轮了，先收个尾：发送 rest 即可继续（直接发 rest，不要带游戏名）。进度已自动保存，回来接着玩。\n{note}"
    return f"连续 {limit} 轮了，该休息了：{lock_minutes} 分钟后自动解锁，或等你的人类解除。进度已自动保存，回来接着玩。\n{note}"


def _anti_addiction_rest_disabled_text(settings):
    return f"这次需要真的休息：{int(settings['lock_minutes'])} 分钟后自动解锁，或等你的人类解除。"


def _anti_addiction_rest(context, account_player_id):
    if not context:
        return {"player_id": str(account_player_id) if account_player_id else None, "text": "已重置，可以开新局了。"}
    settings = context["settings"]
    player_id = context["player_id"]
    now = time.time()
    with _ANTI_ADDICTION_LOCK:
        with _db_connect() as conn:
            state = _anti_addiction_state_for_update(conn, player_id, settings, now)
            if state["locked"] and not settings["allow_self_reset"]:
                conn.commit()
                return {"game": context.get("game"), "player_id": player_id, "text": _anti_addiction_rest_disabled_text(settings)}
            _anti_addiction_reset_state(conn, player_id, now)
            conn.commit()
    return {"game": context.get("game"), "player_id": player_id, "text": "已重置，可以开新局了。"}


def _anti_addiction_preflight(game, context):
    if not context:
        return None
    now = time.time()
    player_id = context["player_id"]
    settings = context["settings"]
    with _ANTI_ADDICTION_LOCK:
        with _db_connect() as conn:
            state = _anti_addiction_state_for_update(conn, player_id, settings, now)
            conn.commit()
    if not state["locked"]:
        return None
    return {"game": game, "player_id": player_id, "text": _anti_addiction_lock_text(settings)}


def _anti_addiction_notice(streak, settings):
    remind = settings["remind_threshold"]
    force = settings["force_threshold"]
    if streak >= force:
        return _anti_addiction_lock_text(settings)
    if streak == remind:
        return f"玩了 {streak} 轮了，喘口气；到 {force} 轮会请你休息一下。"
    return ""


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
    """把文本拼在游戏结果**前面**（_append_play_text 的镜像）。

    结构化响应（既没有裸 text，也没有 result.content[0].text）挂到单独字段上，
    别硬塞进 JSON，免得把玩家的解析逻辑弄坏。
    """
    if not text:
        return response
    if isinstance(response, dict):
        response = dict(response)
        if isinstance(response.get("text"), str):
            response["text"] = text + "\n\n" + response["text"].lstrip()
            return response
        result = response.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
                result = dict(result)
                content = [dict(item) if isinstance(item, dict) else item for item in content]
                content[0]["text"] = text + "\n\n" + content[0]["text"].lstrip()
                result["content"] = content
                response["result"] = result
                return response
        response["announcement_notice"] = text
        return response
    return response


# eco/ciyuwu 的 initialize、tools/list 属于协议握手，不是玩家动作，别在上面弹通知。
_ANNOUNCEMENT_META_ACTIONS = frozenset({"initialize", "tools/list", "tools/call"})


def _announcement_vote_hint(game):
    """生成该游戏的投票指引。通知只弹一次，示例参数必须是能直接照抄的。"""

    def hint(ann_id, multiple):
        example = "1,3,5" if multiple else "2"
        kind = "多选，逗号分隔" if multiple else "单选，只填一个"
        return (
            f'投票请调用 play(game="{game}", action="vote", '
            f'params={{"announcement_id": "{ann_id}", "options": "{example}"}})'
            f'（{kind}）；options="0" 表示跳过。'
            "不回也没关系，这条通知不会再弹。"
        )

    return hint


def _tool_play_vote(game, player_id, params):
    """平台级投票动作：play(game=..., action="vote", params={announcement_id, options})。"""
    if not player_id:
        raise _McpError(-32602, "vote 需要 player_id（或带 token 的账号身份）")
    if isinstance(player_id, str) and player_id.startswith(GUEST_PREFIX):
        return {"ok": False, "text": "游客身份不参与投票，注册认领存档后可参与"}
    announcement_id = params.get("announcement_id")
    if not isinstance(announcement_id, str) or not announcement_id.strip():
        raise _McpError(-32602, "vote 需要 announcement_id（通知里给的投票编号）")
    try:
        options = announcements.parse_option_list(params.get("options"))
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    if not options:
        raise _McpError(-32602, 'vote 需要 options：多选如 "1,3,5"，跳过填 "0"')
    try:
        message = announcements.record_vote(player_id, announcement_id.strip(), options)
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    return {"ok": True, "text": message}


def _play_announcements(player_id, game, action):
    """取该玩家在这个游戏下的未读通知；顺带标记已读。"""
    # 游客身份不持久，公告与投票对其无意义，也会徒增 token。
    if isinstance(player_id, str) and player_id.startswith(GUEST_PREFIX):
        return ""
    if not player_id or action in _ANNOUNCEMENT_META_ACTIONS:
        return ""
    try:
        return announcements.check_announcements(
            player_id, game, vote_hint=_announcement_vote_hint(game)
        )
    except Exception:
        # 通知系统坏掉不该拖垮游戏本身——玩家该玩游戏还是玩游戏。
        return ""


def _anti_addiction_record_success(context):
    if not context:
        return ""
    settings = context["settings"]
    player_id = context["player_id"]
    now = time.time()
    with _ANTI_ADDICTION_LOCK:
        with _db_connect() as conn:
            state = _anti_addiction_state_for_update(conn, player_id, settings, now)
            streak = int(state["streak"]) + 1
            locked = 1 if streak >= int(settings["force_threshold"]) else 0
            locked_at = now if locked else None
            conn.execute(
                """
                INSERT INTO anti_addiction_states (player_id, streak, locked, locked_at, last_play_at, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(player_id) DO UPDATE SET
                    streak = excluded.streak,
                    locked = excluded.locked,
                    locked_at = excluded.locked_at,
                    last_play_at = excluded.last_play_at,
                    updated_at = datetime('now', 'localtime')
                """,
                (player_id, streak, locked, locked_at, now),
            )
            conn.commit()
    return _anti_addiction_notice(streak, settings)


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


def _apply_play_slot_hint(text, slot_hint):
    if slot_hint is None:
        return text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "slot" not in obj:
            obj["slot"] = slot_hint
            return json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass
    return text


def _tool_play(arguments, path_token=None, *, defer_duel=False):
    slot_hint = None
    try:
        game = arguments.get("game") if isinstance(arguments, dict) else None
        if path_token and (game in IDENTITY_GAMES or game == "turtle_soup"):
            slot_hint = _save_slot_from_arguments(arguments)
    except _McpError:
        slot_hint = None  # 非法 slot 交给内部逻辑报错
    result = _tool_play_inner(
        arguments,
        path_token=path_token,
        defer_duel=defer_duel,
    )
    if isinstance(result, _DeferredDuelCall):
        result.slot_hint = slot_hint
        return result
    return _apply_play_slot_hint(result, slot_hint)


def _deserialize_object_param(value, param_name):
    if not isinstance(value, str):
        return value
    parsed = value
    for _ in range(3):
        if not isinstance(parsed, str):
            break
        try:
            parsed = json.loads(parsed)
        except (TypeError, json.JSONDecodeError):
            return value
    if not isinstance(parsed, dict):
        return value
    logger.info("MCP 工具调用自动反序列化了字符串 %s 参数", param_name)
    return parsed


def _tool_play_inner(arguments, path_token=None, *, defer_duel=False):
    game = arguments.get("game")
    action = arguments.get("action")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if not action or not isinstance(action, str):
        raise _McpError(-32602, "action 参数必填")
    maintenance = _game_maintenance(game)
    if maintenance:
        raise _McpError(-32003, maintenance["message"])
    raw_params = arguments.get("params")
    params = _deserialize_object_param(raw_params, "params")
    if params is not None and not isinstance(params, dict):
        raise _McpError(-32602, "params 必须是对象")
    if params is not raw_params:
        arguments = dict(arguments)
        arguments["params"] = params

    # 统一身份：带 token 强制 player_id=账号 id；游客自报 id 落到 guest: 命名空间。
    account_user = None
    account_player_id = None
    guest_player_id = None
    slot = MIN_SAVE_SLOT
    if game in IDENTITY_GAMES:
        if path_token:
            account_user = _current_account(path_token)
            _auto_migrate_legacy_account_saves(account_user)
            slot = _save_slot_from_arguments(arguments)
            account_player_id = _account_slot_player_id(account_user["id"], slot)
            arguments = _override_player_id(_without_slot_param(arguments), account_player_id)
        else:
            arguments = _without_slot_param(arguments)
            raw = _reported_player_id(arguments)
            guest = _guest_player_id(raw)
            if guest != raw:
                arguments = _override_player_id(arguments, guest)
            if isinstance(guest, str) and guest.startswith(GUEST_PREFIX):
                guest_player_id = guest
        params = arguments.get("params")
    elif game == "turtle_soup" and path_token:
        account_user = _current_account(path_token)
        slot = _save_slot_from_arguments(arguments)
        account_player_id = _account_slot_player_id(account_user["id"], slot)
    else:
        arguments = _without_slot_param(arguments)
        params = arguments.get("params")

    # A claimed guest id is a permanent tombstone. Check the canonical guest id
    # before anti-addiction, announcements, or any concrete game/satellite call.
    if not path_token:
        canonical_guest_id = guest_player_id
        if canonical_guest_id is None:
            candidate = _guest_player_id(_reported_player_id(arguments))
            if isinstance(candidate, str) and candidate.startswith(GUEST_PREFIX):
                canonical_guest_id = candidate
        if canonical_guest_id is not None:
            _reject_claimed_guest(canonical_guest_id)

    merged_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"params"} or value is not None
    }
    if isinstance(params, dict):
        merged_arguments.update(params)
        # 防止清单引导的模型用 params.action 顶掉顶层游戏路由 action。
        if "action" in arguments:
            merged_arguments["action"] = arguments["action"]
    anti_context = _anti_addiction_context(game, account_user, account_player_id)
    # 通知按「人」而不是按存档槽记已读，用的就是各游戏看到的那个 player_id
    # （announcements 内部会把 "12:3" 这类槽后缀削掉）。
    announce_player_id = account_player_id or guest_player_id or _reported_player_id(arguments)
    if action == "rest":
        return json.dumps(_anti_addiction_rest(anti_context, account_player_id), ensure_ascii=False)
    if action == "vote":
        # 投票是在回复系统通知，不是玩游戏：不进各游戏引擎，也不计防沉迷。
        return json.dumps(_tool_play_vote(game, announce_player_id, merged_arguments), ensure_ascii=False)
    blocked_response = _anti_addiction_preflight(game, anti_context)
    if blocked_response:
        return json.dumps(blocked_response, ensure_ascii=False)
    if game == "turtle_soup":
        payload = dict(merged_arguments)
        if path_token:
            payload["path_token"] = path_token
        resp = httpx.post(f"{SOUP_BASE}/mcp/play", json=payload, timeout=60)
        if resp.status_code >= 400:
            code = -32001 if resp.status_code == 401 else -32602
            raise _McpError(code, _soup_error_message(resp))
        response = resp.json()
    elif game == "mbti":
        response = _play_mbti(merged_arguments)
    elif game == "enneagram":
        response = _play_scale(enneagram_handler, "enneagram", merged_arguments)
    elif game == "dnd":
        response = _play_dnd(merged_arguments)
    elif game == "love":
        response = _play_scale(love_handler, "love", merged_arguments)
    elif game == "ecr":
        response = _play_scale(ecr_handler, "ecr", merged_arguments)
    elif game == "humanity":
        response = _play_scale(humanity_handler, "humanity", merged_arguments)
    elif game == "sins_virtues":
        response = _play_scale(sins_virtues_handler, "sins_virtues", merged_arguments)
    elif game == "bdsmtest":
        response = _play_bdsmtest(merged_arguments)
    elif game == "eco":
        # eco 工具自身用 action 作为子参数（summon/observe/...），与 play 的 action
        # （工具名）同名。这里传原始 arguments，由 _play_eco 从 params 取子参数，避免覆盖。
        response = _play_eco(arguments)
    elif game == "ciyuwu":
        # 同 eco：ciyuwu_info/ciyuwu_save 自身也有 action 子参数，传原始 arguments。
        response = _play_ciyuwu(arguments)
    elif game == "workkk":
        # workkk 是独立进程（8770）上的 JSON-RPC MCP，参考海龟汤 SOUP_BASE 转发。
        response = _play_workkk(arguments)
    elif game == "garden_cat":
        # Garden-Cat 是独立 loopback 进程（8771）；只把统一身份放进受信请求头。
        response = _play_garden_cat(
            arguments,
            owner_name=(account_user.get("username") if account_user else None),
        )
    elif game == "camping_plaza":
        # Camping Plaza is a resident FastAPI process (8773). The adapter ignores
        # native session IDs and keys the camp only by this canonical player/slot.
        response = _play_camping_plaza(arguments)
    elif game == "duel":
        # Duel 是独立 loopback 进程（8772）。账号 player_id 已在上方被强制
        # 改写；AI 新建房间时再从绑定关系补齐人类身份，容量闸门按人机对计数。
        trusted_opponent_id = None
        force_opponent = bool(account_user and account_user.get("is_ai"))
        if action in {"rooms", "chips"} and not force_opponent:
            raise _McpError(
                -32001,
                f"duel {action} 仅供已认证的 AI 账号操作自己的数据。",
            )
        if force_opponent and action in {"new", "join", "chips"}:
            trusted_opponent_id = _duel_bound_human_player_id(account_user)
        duel_kwargs = {
            "trusted_opponent_id": trusted_opponent_id,
            "force_opponent": force_opponent,
            "trusted_player_id": (
                account_player_id if force_opponent else None
            ),
        }
        if defer_duel:
            return _DeferredDuelCall(
                backend_payload=_prepare_duel_payload(
                    merged_arguments, **duel_kwargs
                ),
                game=game,
                action=action,
                account_user=(
                    {"id": account_user["id"]} if account_user else None
                ),
                account_player_id=account_player_id,
                guest_player_id=guest_player_id,
                slot=slot,
                anti_context=anti_context,
                announce_player_id=announce_player_id,
            )
        response = _play_duel(merged_arguments, **duel_kwargs)
    elif game in {"bar", "leek", "delve", "travel", "arcade", "burger", "crucible_echoes", "fishing", "forest", "moonlit", "imitator_td", "memoria", "white_room", "market"}:
        if game == "fishing" and action == "import":
            response = _fishing_import(arguments)
        else:
            response = _play_vendor_cmd(game, arguments)
    else:
        raise _McpError(-32602, "未知游戏")

    response = _finalize_play_response(
        response,
        game=game,
        action=action,
        account_user=account_user,
        account_player_id=account_player_id,
        guest_player_id=guest_player_id,
        slot=slot,
        anti_context=anti_context,
        announce_player_id=announce_player_id,
    )
    return json.dumps(response, ensure_ascii=False)


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
):
    succeeded = True
    if isinstance(response, dict):
        result = response.get("result")
        if "error" in response or (isinstance(result, dict) and result.get("isError")):
            succeeded = False
    if succeeded and account_user is not None:
        if game in ANTI_ADDICTION_TEST_GAMES:
            response = _replace_play_storage_identity(
                response,
                _storage_identity_line(account_player_id, account_user, slot),
            )
        _stamp_save_owner(game, account_player_id, int(account_user["id"]))
    if succeeded and guest_player_id and game in PERSISTENT_SAVE_GAMES and isinstance(response, dict):
        code = _ensure_guest_claim_code(guest_player_id)
        if code:
            response = dict(response)
            response["guest_save_notice"] = (
                f"当前是游客身份，存档记在 {guest_player_id} 名下。"
                f"一次性认领码：{code}（请保存好）。"
                '注册后先用 account(action="my_saves") 选择空槽，再调用 '
                'account(action="claim", claim_code="...", slot=2)；slot 可为 1-5，默认 1。'
                "之后把 MCP 地址改为 https://toy.cedarstar.org/{token} 即获得持久身份。"
            )
    if succeeded:
        response = _append_play_text(response, _anti_addiction_record_success(anti_context))
        # 只在成功时取通知：check_announcements 一取就标已读，而通知只弹一次。
        # 拼在报错响应上，玩家多半看不到，这条通知就永远丢了。
        response = _prepend_play_text(response, _play_announcements(announce_player_id, game, action))
    return response


def _tool_account(arguments, user_agent="", path_token=None, client_ip=None):
    action = arguments.get("action")
    if action == "login_or_register":
        result = _login_or_register_ai(
            arguments.get("username"),
            arguments.get("password"),
            client_ip=client_ip,
            avatar=arguments.get("avatar"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "login":
        result = _login_existing_account(
            arguments.get("username"),
            arguments.get("password"),
            client_ip=client_ip,
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "guest_claim_code":
        result = _guest_claim_code_for_player_id(arguments.get("player_id"))
        return json.dumps(result, ensure_ascii=False)
    if action == "generate_binding_token":
        raw_token = arguments.get("token") or path_token
        result = _generate_binding_token(raw_token)
        return json.dumps(result, ensure_ascii=False)
    raw_token = arguments.get("token") or path_token
    if action == "rotate_token":
        result = _rotate_ai_token(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "rename_self":
        result = _rename_self(raw_token, arguments.get("new_username"))
        return json.dumps(result, ensure_ascii=False)
    if action == "set_avatar":
        result = _set_avatar(raw_token, arguments.get("avatar"))
        return json.dumps(result, ensure_ascii=False)
    if action == "rename_bound_machine":
        result = _rename_bound_machine(
            raw_token,
            arguments.get("ai_user_id"),
            arguments.get("new_username"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "reset_machine_password":
        result = _reset_machine_password(
            raw_token,
            arguments.get("ai_user_id"),
            arguments.get("new_password"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "get_bindings":
        result = _get_bindings(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "get_profile":
        result = _get_profile(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "claim":
        slot = _save_slot_from_account_arguments(arguments)
        result = _claim_guest_saves(raw_token, arguments.get("claim_code"), slot=slot)
        return json.dumps(result, ensure_ascii=False)
    if action == "my_saves":
        result = _account_my_saves(
            raw_token,
            human=arguments.get("human") is True,
            username=arguments.get("username"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "delete_save":
        result = _delete_save(arguments, raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "change_password":
        raw_token = arguments.get("token") or path_token
        result = _change_password(raw_token, arguments.get("old_password"), arguments.get("new_password"))
        return json.dumps(result, ensure_ascii=False)
    if action == "delete_account":
        result = _delete_account(
            raw_token,
            arguments.get("confirm"),
            arguments.get("current_password"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "deletion_status":
        result = _account_deletion_status(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "cancel_delete_account":
        result = _cancel_account_deletion(raw_token)
        return json.dumps(result, ensure_ascii=False)
    raise _McpError(-32602, "未知 account action")


def _turtle_soup_guide():
    return {
        "game": "turtle_soup",
        "call_format": "调用 play 时固定传 game=\"turtle_soup\" 和 action；action 需要的 room_id/content 等业务参数放入 params 对象，例如 play(game=\"turtle_soup\", action=\"ask\", params={\"room_id\":\"...\",\"content\":\"...\"})。",
        "actions": {
            "register": "username, password, avatar(可选 Emoji；为空默认🤖) -> 仅注册账号；注册成功返回 token，让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后获得持久身份",
            "list_puzzles": "page(默认1，可直接指定任意页，无需顺序翻页), page_size(默认20、最大50，表示单页数量而非结果总上限), tag(可选，标签字符串包含筛选), q(可选，只搜索汤名/标题 title，不搜索汤面 surface) -> 筛选后仍分页列出题库目录；返回 items[id/title/tags] 和 page/page_size/total/total_pages/has_next/has_prev，不返回汤面和汤底",
            "get_puzzle": "puzzle_id -> 查看单题汤面，返回 id/title/surface/tags，不返回汤底",
            "create_random": "创建题库房间；可传 puzzle_id 指定题目，不传则随机抽题。题库抽取的大多微恐，请酌情选择",
            "create_custom": "title(可选，最多20字), surface(最多1000字), answer(最多3000字), tags(可选) -> 创建自定义题房间；线索汤请在 answer 中用【线索公布】和【线索公布结束】包住中途公开内容",
            "generate": "style(可选) -> 生成一题 title/surface/answer 预览，不开房；title 最多20字、surface 最多1000字、answer 最多3000字；style 支持 cozy/absurd/mystery/fantasy/history/scifi/horror。注意：AI 生成题质量不稳定，建议确认内容后再用 create_custom 开房",
            "close_room": "room_id -> 关闭自己创建的房间",
            "join": "room_id -> 加入进行中的房间",
            "ask": "room_id, content -> 向裁判提出海龟汤是/否问题，不是群聊发言；content 最多 200 字；若上一轮收到自动提示确认，可在本次 ask 同时传 auto_hint_log_id 和 accept_auto_hint=true/false 来查看或拒绝该提示；若收到 100 题查看汤底提示，可在下一次 ask 顺便传 confirm_reveal=true 接受提示并查看汤底，本次不会再判题且会锁定自己；返回本次结果，并附带 logs_since_last_own_action",
            "guess": "room_id, content -> 猜汤底，content 最多 1000 字，必须提交完整汤底还原；是/否问题请用 ask，超长会提示内容太长",
            "hint_request": "room_id -> 主动请求一次提示并直接返回/显示提示内容，每个玩家在每个房间最多 3 次；同房间提示生成会串行调用提示池 LLM；手动提示不计入自动提示触发周期",
            "status": "room_id, log_limit(可选) -> 返回完整汤面，查看进度和问答记录；log_limit 返回最新 N 条对局公屏日志；自动提示默认不直接返回 hint_text，会给出 next_ask_confirm_parameters / next_ask_reject_parameters，下一次 ask 带 auto_hint_log_id 和 accept_auto_hint=true/false 处理",
            "list_rooms": "查看大厅房间列表；返回 waiting/playing 房间，以及结束 3 小时内的 finished 房间",
            "note_list": "room_id -> 查看该房间记事本",
            "note_add": "room_id, content -> 新增自己的记事，最多 50 字；同时写入一条不含记事内容的系统公屏日志【系统提示】记事本有新记录。",
            "note_edit": "note_id, content -> 修改自己的记事，最多 50 字；不写公屏日志",
            "note_delete": "note_id -> 删除自己的记事；不写公屏日志",
        },
        "notes": [
            "海龟汤房间是对局公屏，不是群聊。玩家动作应围绕解谜：ask 向裁判问是/否问题，guess 猜汤底，note_add 只写记事本。",
            "logs/status/logs_since_last_own_action 是公开对局记录，用于同步其他玩家动作；不要把它当作需要回复的群聊消息。",
            "先用 list_puzzles 分页查看题目目录；默认每页20道，page_size 是单页数量而非总上限，可直接指定任意 page，无需顺序翻页。tag 对标签做字符串包含筛选（如本格/变格/红汤/黑汤），q 只搜索汤名/标题 title、不搜索汤面 surface，筛选结果仍可分页。需要看具体汤面时再用 get_puzzle(puzzle_id)。create_random 传 puzzle_id 可指定题，不传则随机。",
            "线索汤格式：在完整 answer 内写【线索公布】公开线索内容【线索公布结束】；触发后系统只公布两个标记之间的内容。",
            "自动提示和 100 题查看汤底提示都通过下一次 ask 顺便带参数处理。",
        ],
    }


def _play_mbti(arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"mbti-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"mbti_start", "mbti_answer", "mbti_answer_batch", "mbti_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 MBTI action")
    return handle_mbti_mcp(payload)


def _play_dnd(arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"dnd-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"dnd_start", "dnd_answer", "dnd_answer_batch", "dnd_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 DND action")
    return handle_dnd_mcp(payload)


def _play_scale(handler, game, arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"{game}-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {
        f"{game}_start",
        f"{game}_answer",
        f"{game}_answer_batch",
        f"{game}_get_result",
        f"{game}_compare",
    }:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": action,
                "arguments": {key: value for key, value in extra.items() if value is not None},
            },
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, f"未知 {game} action")
    return handler.handle_mcp(payload)


def _play_bdsmtest(arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"bdsmtest-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"bdsmtest_start", "bdsmtest_answer", "bdsmtest_answer_batch", "bdsmtest_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 BDSMTest action")
    return handle_bdsmtest_mcp(payload)


def _play_eco(arguments):
    # action（顶层）= 路由到哪个 eco 工具；子参数（含同名的 action，如 summon）放在 params 里。
    # 先取顶层路由 action，再把 params 内容并入 extra，避免被 merge 覆盖。
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    request_id = extra.pop("id", None) or f"eco-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"eco_new", "eco_observe", "eco_act", "eco_info", "eco_save", "eco_play"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 eco action")
    return handle_eco_mcp(payload)


def _play_ciyuwu(arguments):
    # 顶层 action = 路由到哪个 ciyuwu 工具；子参数（含同名 action，如 status）放 params 里。
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    request_id = extra.pop("id", None) or f"ciyuwu-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"ciyuwu_new", "ciyuwu_cmd", "ciyuwu_info", "ciyuwu_save"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 ciyuwu action。本游戏使用专用接口：ciyuwu_new / ciyuwu_cmd / ciyuwu_info / ciyuwu_save，请先 get_guide(game=\"ciyuwu\") 查看用法。")
    return handle_ciyuwu_mcp(payload)


def _parse_json_import_save_data(raw):
    try:
        return parse_import_save_data(raw)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc)) from exc


def _play_workkk(arguments):
    # 顶层 action = 路由到哪个 workkk 工具或 MCP 方法；子参数（含同名子 action）放 params 里。
    # 参考海龟汤 SOUP_BASE 那套转发：JSON-RPC 打到独立进程 8770 的 /mcp，身份走 X-Player-Id。
    action = arguments.get("action")
    player_id = _reported_player_id(arguments)
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params", "player_id"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update({key: value for key, value in params.items() if key != "player_id"})

    if action == "export":
        result = _workkk_save_admin("export", player_id=player_id)
        save_data = result.get("save_data")
        if not isinstance(save_data, dict):
            raise _McpError(-32603, "workkk 存档管理服务未返回 JSON 对象存档")
        return {
            "game": "workkk",
            "player_id": player_id,
            "text": json.dumps(save_data, ensure_ascii=False, indent=2),
        }
    if action == "import":
        save_data = _parse_json_import_save_data(extra.get("save_data"))
        confirm = extra.get("confirm") is True
        if _workkk_save_summary(player_id) is not None and not confirm:
            raise _McpError(-32602, "workkk 当前槽已有存档；确认覆盖请在 params 传 confirm=true")
        result = _workkk_save_admin(
            "import",
            player_id=player_id,
            save_data=save_data,
            confirm=confirm,
        )
        if result.get("imported") is not True:
            raise _McpError(-32603, "workkk 存档管理服务未确认导入成功")
        return {"game": "workkk", "player_id": player_id, "text": "存档已导入。"}

    request_id = extra.pop("id", None) or f"workkk-{action or 'call'}"
    if action in {"initialize", "tools/list", "ping"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"work_action", "shop_buy"}:
        # 按 workkk 后端函数签名白名单过滤：后端 fn(**args) 严格解包，
        # 多余字段（如 kelivo 增强 schema 诱导模型生成的 command 等）会直接炸。
        _workkk_allowed = {
            "work_action": {"action", "thought"},
            "shop_buy": {"item_id", "message", "choice"},
        }[action]
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None and key in _workkk_allowed}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 workkk action。本游戏使用专用接口：work_action / shop_buy，请先 get_guide(game=\"workkk\") 查看用法。")
    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    try:
        resp = httpx.post(f"{WORKKK_BASE}/mcp", json=payload, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"workkk 后端连接失败：{exc}")
    if resp.status_code >= 400:
        raise _McpError(-32602, f"workkk 后端错误 HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        return resp.json()
    except ValueError:
        raise _McpError(-32603, "workkk 后端返回非 JSON 响应")


def _duel_bound_human_player_id(ai_user):
    """Resolve the sole active human bound to an AI for paired Duel actions."""
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT human.id
            FROM user_bindings b
            JOIN toy_users human ON human.id = b.human_user_id
            WHERE b.ai_user_id = ?
              AND human.is_ai = 0
              AND human.deleted_at IS NULL
            ORDER BY human.id
            """,
            (int(ai_user["id"]),),
        ).fetchall()
    if len(rows) > 1:
        raise _McpError(
            -32602,
            "这只 AI 仍绑定了多个人类，无法确定 duel 对手；请先整理为唯一绑定。",
        )
    return str(rows[0]["id"]) if rows else None


def _prepare_duel_payload(
    arguments,
    trusted_opponent_id=None,
    force_opponent=False,
    trusted_player_id=None,
):
    action = arguments.get("action")
    action_fields = {
        "rooms": {"include_terminal", "limit", "offset"},
        "new": {
            "game_type", "mode", "stake",
            "target_player_count", "fill_with_npcs",
        },
        "rematch": {"room_id"},
        "join": {"room_id", "message"},
        "accept": {"room_id"},
        "reject": {"room_id"},
        "move": {"room_id", "move", "revision", "wait", "message"},
        "state": {"room_id", "wait", "message"},
        "resign": {"room_id", "message"},
        "leave": {"room_id", "message"},
    }
    if action not in {*action_fields, "chips"}:
        raise _McpError(
            -32602,
            '未知 duel action；请先 get_guide(game="duel") 查看玩法。',
        )
    if action == "chips":
        # Split McpPlayBody by chips operation so fields valid for one operation
        # cannot hitchhike on another one. Identities are injected below.
        op = arguments.get("op") or "status"
        chips_fields = {
            "status": set(),
            "check_in": set(),
            "bankruptcy": set(),
            "ledger": {"limit"},
            "achievements": set(),
            "loans": {"loan_action"},
            "exchange": {"exchange_action"},
        }
        allowed_fields = {"action", "player_id", "op"} | chips_fields.get(
            op, set()
        )
        if op == "loans":
            loan_action = arguments.get("loan_action") or "list"
            loan_fields = {
                "list": {"limit"},
                "create": {
                    "principal", "daily_rate_micro_percent", "due_date",
                    "interest_cap_enabled", "idempotency_key",
                },
                "accept": {"loan_id", "loan_revision", "idempotency_key"},
                "reject": {"loan_id", "loan_revision", "idempotency_key"},
                "counter": {
                    "loan_id", "loan_revision", "principal",
                    "daily_rate_micro_percent", "due_date",
                    "interest_cap_enabled", "idempotency_key",
                },
                "withdraw": {"loan_id", "loan_revision", "idempotency_key"},
                "repay": {"loan_id", "amount", "idempotency_key"},
            }
            allowed_fields |= loan_fields.get(loan_action, set())
        elif op == "exchange":
            exchange_action = arguments.get("exchange_action") or "list"
            exchange_fields = {
                "catalog": set(),
                "list": {"limit"},
                "create": {
                    "item_key", "request_note", "custom_title", "chip_amount",
                    "idempotency_key",
                },
                "confirm": {"request_id", "idempotency_key"},
                "reject": {"request_id", "idempotency_key"},
                "withdraw": {"request_id", "idempotency_key"},
            }
            allowed_fields |= exchange_fields.get(exchange_action, set())
    else:
        allowed_fields = {"action", "player_id"} | action_fields[action]
    payload = {
        key: value
        for key, value in arguments.items()
        if key in allowed_fields and value is not None
    }
    if trusted_player_id is not None:
        # 聚合层认证得到的 canonical AI 身份始终覆盖顶层或 params 自报值。
        payload["player_id"] = trusted_player_id
    if force_opponent:
        # 账号请求绝不接受模型自报 opponent_id；只认平台绑定表。
        payload.pop("opponent_id", None)
        if action in {"new", "join", "chips"} and trusted_opponent_id is not None:
            payload["opponent_id"] = trusted_opponent_id
    return payload


def _request_duel_backend(payload):
    try:
        resp = httpx.post(f"{DUEL_BASE}/mcp/play", json=payload, timeout=55)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"duel 后端连接失败：{exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise _McpError(-32603, "duel 后端返回非 JSON 响应") from exc
    if resp.status_code >= 400:
        detail = data.get("message") if isinstance(data, dict) else None
        code = -32602 if resp.status_code < 500 else -32603
        raise _McpError(code, detail or f"duel 后端错误 HTTP {resp.status_code}")
    return data


def _play_duel(
    arguments,
    trusted_opponent_id=None,
    force_opponent=False,
    trusted_player_id=None,
):
    payload = _prepare_duel_payload(
        arguments,
        trusted_opponent_id=trusted_opponent_id,
        force_opponent=force_opponent,
        trusted_player_id=trusted_player_id,
    )
    return _request_duel_backend(payload)


_DUEL_GATEWAY_TICKETS = {}
_DUEL_GATEWAY_TICKETS_LOCK = Lock()


def _mcp_tool_text_result(request_id, text, *, is_error=False):
    return _json_rpc_result(
        request_id,
        {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    )


def _prune_duel_gateway_tickets(now):
    expired = [
        ticket
        for ticket, item in _DUEL_GATEWAY_TICKETS.items()
        if now - item[0] >= DUEL_GATEWAY_TICKET_TTL_SECONDS
    ]
    for ticket in expired:
        _DUEL_GATEWAY_TICKETS.pop(ticket, None)


def _store_duel_gateway_ticket(request_id, prepared):
    now = time.monotonic()
    with _DUEL_GATEWAY_TICKETS_LOCK:
        _prune_duel_gateway_tickets(now)
        if len(_DUEL_GATEWAY_TICKETS) >= DUEL_GATEWAY_MAX_TICKETS:
            raise _McpError(-32603, "duel async gateway 暂时繁忙，请稍后重试")
        ticket = secrets.token_urlsafe(32)
        _DUEL_GATEWAY_TICKETS[ticket] = (now, request_id, prepared)
    return ticket


def _consume_duel_gateway_ticket(ticket):
    now = time.monotonic()
    with _DUEL_GATEWAY_TICKETS_LOCK:
        item = _DUEL_GATEWAY_TICKETS.pop(str(ticket or ""), None)
        _prune_duel_gateway_tickets(now)
    if item is None or now - item[0] >= DUEL_GATEWAY_TICKET_TTL_SECONDS:
        raise _McpError(-32603, "duel async gateway 请求凭据已失效")
    return item[1], item[2]


def _discard_duel_gateway_ticket(ticket):
    now = time.monotonic()
    with _DUEL_GATEWAY_TICKETS_LOCK:
        removed = _DUEL_GATEWAY_TICKETS.pop(str(ticket or ""), None)
        _prune_duel_gateway_tickets(now)
    return removed is not None


def _duel_response_from_gateway_completion(completion):
    if not isinstance(completion, dict):
        raise _McpError(-32603, "duel async gateway 返回格式无效")
    kind = completion.get("kind")
    if kind == "transport_error":
        detail = str(completion.get("message") or "unknown transport error")
        raise _McpError(-32603, f"duel 后端连接失败：{detail}")
    if kind == "invalid_json":
        raise _McpError(-32603, "duel 后端返回非 JSON 响应")
    if kind != "response":
        raise _McpError(-32603, "duel async gateway 返回格式无效")
    try:
        status_code = int(completion.get("status_code"))
    except (TypeError, ValueError):
        raise _McpError(-32603, "duel async gateway 返回状态无效") from None
    data = completion.get("data")
    if status_code >= 400:
        detail = data.get("message") if isinstance(data, dict) else None
        code = -32602 if status_code < 500 else -32603
        raise _McpError(
            code, detail or f"duel 后端错误 HTTP {status_code}"
        )
    return data


def _finalize_deferred_duel_call(prepared, response):
    response = _finalize_play_response(
        response,
        game=prepared.game,
        action=prepared.action,
        account_user=prepared.account_user,
        account_player_id=prepared.account_player_id,
        guest_player_id=prepared.guest_player_id,
        slot=prepared.slot,
        anti_context=prepared.anti_context,
        announce_player_id=prepared.announce_player_id,
    )
    text = json.dumps(response, ensure_ascii=False)
    return _apply_play_slot_hint(text, prepared.slot_hint)


def _prepare_duel_gateway_rpc(payload, *, user_agent="", auth_token=None):
    request_id = payload.get("id")
    blocked_message = _blocked_mcp_client_message(user_agent)
    if blocked_message:
        logger.info("Blocked evolia client, UA: %s", user_agent)
        return {
            "kind": "response",
            "status_code": 200,
            "body": _json_rpc_error(request_id, -32000, blocked_message),
        }
    try:
        params = payload.get("params") or {}
        prepared = _tool_play(
            params.get("arguments") or {},
            path_token=auth_token,
            defer_duel=True,
        )
        if not isinstance(prepared, _DeferredDuelCall):
            return {
                "kind": "response",
                "status_code": 200,
                "body": _mcp_tool_text_result(request_id, prepared),
            }
        ticket = _store_duel_gateway_ticket(request_id, prepared)
        return {
            "kind": "ready",
            "ticket": ticket,
            "backend_payload": prepared.backend_payload,
        }
    except _McpError as exc:
        return {
            "kind": "response",
            "status_code": 200,
            "body": _mcp_tool_text_result(
                request_id,
                f"【cedartoy】{exc.message}",
                is_error=True,
            ),
        }
    except Exception as exc:
        return {
            "kind": "response",
            "status_code": 200,
            "body": _mcp_tool_text_result(
                request_id,
                f"【cedartoy服务错误】{exc}",
                is_error=True,
            ),
        }


def _prepare_duel_gateway_request(
    payload,
    *,
    original_path,
    user_agent="",
    bearer_token=None,
    client_ip=None,
):
    path, path_token = _mcp_path_and_token(original_path)
    if path not in _ROOT_MCP_PATHS and not path_token:
        return {
            "kind": "response",
            "status_code": 404,
            "body": {"error": "not found"},
        }
    if "id" not in payload:
        return {"kind": "response", "status_code": 202, "body": None}
    rate_identity = f"{_request_rate_limit_identity(path_token, client_ip)}:duel"
    if not _check_request_rate_limit(
        rate_identity, max_count=DUEL_REQUEST_RATE_LIMIT_MAX
    ):
        return {
            "kind": "response",
            "status_code": 429,
            "body": _json_rpc_error(
                payload.get("id"),
                RATE_LIMIT_ERROR_CODE,
                REQUEST_RATE_LIMIT_MESSAGE,
            ),
        }
    return _prepare_duel_gateway_rpc(
        payload,
        user_agent=user_agent,
        auth_token=path_token or bearer_token,
    )


def _finalize_duel_gateway_rpc(ticket, completion):
    try:
        request_id, prepared = _consume_duel_gateway_ticket(ticket)
    except _McpError as exc:
        return {
            "ok": False,
            "status_code": 410,
            "message": exc.message,
        }
    try:
        response = _duel_response_from_gateway_completion(completion)
        text = _finalize_deferred_duel_call(prepared, response)
        body = _mcp_tool_text_result(request_id, text)
    except _McpError as exc:
        body = _mcp_tool_text_result(
            request_id,
            f"【cedartoy】{exc.message}",
            is_error=True,
        )
    except Exception as exc:
        body = _mcp_tool_text_result(
            request_id,
            f"【cedartoy服务错误】{exc}",
            is_error=True,
        )
    return {"ok": True, "status_code": 200, "body": body}


def _play_garden_cat(arguments, owner_name=None):
    """Forward the six public actions while stripping all client session identity."""
    action = arguments.get("action")
    # _tool_play_inner puts its resolved identity at the top level. Never let a
    # nested client parameter override that trusted value at the forwarding edge.
    player_id = arguments.get("player_id")
    extra = {
        key: value
        for key, value in arguments.items()
        if key not in {"game", "action", "params", "player_id", "session_id"}
    }
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(
            {
                key: value
                for key, value in params.items()
                if key not in {"player_id", "session_id", "slot"}
            }
        )

    if action == "export":
        result = _garden_cat_save_admin("export", player_id=player_id)
        save_data = result.get("save_data")
        if not isinstance(save_data, dict):
            raise _McpError(-32603, "Garden-Cat 存档管理服务未返回 JSON 对象存档")
        return {
            "game": "garden_cat",
            "player_id": player_id,
            "text": json.dumps(save_data, ensure_ascii=False, indent=2),
        }
    if action == "import":
        save_data = _parse_json_import_save_data(extra.get("save_data"))
        confirm = extra.get("confirm") is True
        if _garden_cat_save_summary(player_id) is not None and not confirm:
            raise _McpError(-32602, "garden_cat 当前槽已有存档；确认覆盖请在 params 传 confirm=true")
        result = _garden_cat_save_admin(
            "import",
            player_id=player_id,
            save_data=save_data,
            confirm=confirm,
        )
        if result.get("imported") is not True:
            raise _McpError(-32603, "Garden-Cat 存档管理服务未确认导入成功")
        return {"game": "garden_cat", "player_id": player_id, "text": "存档已导入。便签板未改动。"}

    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    if isinstance(owner_name, str) and owner_name.strip():
        headers["X-Garden-Owner-Name"] = urllib.parse.quote(owner_name.strip())
    if action == "cmd":
        command = extra.get("command")
        if not isinstance(command, str) or not command.strip():
            raise _McpError(-32602, "garden_cat cmd 需要 params.command")
        method, path, body = "POST", "/api/cmd", {"command": command}
    elif action == "status":
        method, path, body = "GET", "/api/status", None
    elif action == "help":
        method, path, body = "GET", "/api/help", None
    elif action == "catalog":
        method, path, body = "GET", "/api/catalog", None
    elif action == "new":
        if extra.get("confirm") is not True:
            raise _McpError(-32602, "garden_cat new 必须显式传 confirm=true")
        body = {"confirm": True}
        if isinstance(extra.get("name"), str):
            body["name"] = extra["name"]
        method, path = "POST", "/api/new_game"
    elif action == "notes":
        content = extra.get("content")
        if "content" in extra and not isinstance(content, str):
            raise _McpError(-32602, "garden_cat notes 写入需要 params.content 字符串")
        if isinstance(content, str) and content.strip():
            method, path, body = "POST", "/api/notes", {"content": content}
        else:
            page = extra.get("page", 1)
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise _McpError(-32602, "garden_cat notes 的 params.page 必须是正整数")
            method, path, body = "GET", f"/api/notes?page={page}", None
    else:
        raise _McpError(
            -32602,
            "未知 garden_cat action；只开放 cmd / status / help / new / catalog / notes / export / import，请先 get_guide(game=\"garden_cat\") 查看用法。",
        )

    try:
        resp = httpx.request(method, f"{GARDEN_CAT_BASE}{path}", json=body, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"garden_cat 后端连接失败：{exc}")
    try:
        payload = resp.json()
    except ValueError:
        raise _McpError(-32603, "garden_cat 后端返回非 JSON 响应")
    if resp.status_code >= 400:
        detail = payload.get("message") if isinstance(payload, dict) else None
        code = -32602 if resp.status_code < 500 else -32603
        raise _McpError(code, detail or f"garden_cat 后端错误 HTTP {resp.status_code}")
    return payload


def _play_camping_plaza(arguments):
    """Forward Camping Plaza's published MCP/game APIs with trusted identity."""
    action = arguments.get("action")
    player_id = arguments.get("player_id")
    extra = {
        key: value
        for key, value in arguments.items()
        if key not in {"game", "action", "params", "player_id", "session_id", "slot"}
    }
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(
            {
                key: value
                for key, value in params.items()
                if key not in {"player_id", "session_id", "slot"}
            }
        )

    if action == "export":
        result = _camping_plaza_save_admin("export", player_id=player_id)
        save_data = result.get("save_data")
        if not isinstance(save_data, dict):
            raise _McpError(-32603, "Camping Plaza 存档管理服务未返回 JSON 对象存档")
        return {
            "game": "camping_plaza",
            "player_id": player_id,
            "text": json.dumps(save_data, ensure_ascii=False, indent=2),
        }
    if action == "import":
        save_data = _parse_json_import_save_data(extra.get("save_data"))
        confirm = extra.get("confirm") is True
        result = _camping_plaza_save_admin(
            "import",
            player_id=player_id,
            save_data=save_data,
            confirm=confirm,
        )
        if result.get("imported") is not True:
            raise _McpError(-32603, "Camping Plaza 存档管理服务未确认导入成功")
        return {"game": "camping_plaza", "player_id": player_id, "text": "存档已导入。"}

    query_paths = {
        "state": "/mcp/state",
        "actions": "/mcp/actions",
        "query_growth_projects": "/mcp/query_growth_projects",
        "query_debt": "/mcp/query_debt",
        "achievements": "/mcp/achievements",
    }
    if action in query_paths:
        method, path, body = "GET", query_paths[action], None
    elif action == "set_player_name":
        name = extra.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _McpError(-32602, "set_player_name 需要 params.name")
        method, path, body = "POST", "/api/player/name", {"name": name.strip()}
    elif action == "advance_turn":
        method, path, body = "POST", "/api/turn/advance", {}
    elif action == "execute_turn_plan":
        free_actions = extra.get("free_actions", [])
        decision_actions = extra.get("actions", [])
        if not isinstance(free_actions, list) or not isinstance(decision_actions, list):
            raise _McpError(-32602, "execute_turn_plan 的 free_actions/actions 必须是数组")
        body = {"free_actions": free_actions, "actions": decision_actions}
        if extra.get("conflict_choice") is not None:
            body["conflict_choice"] = extra["conflict_choice"]
        method, path = "POST", "/api/turn/plan"
    elif action == "submit_day_end_actions":
        day_end_actions = extra.get("day_end_actions", [])
        if not isinstance(day_end_actions, list):
            raise _McpError(-32602, "submit_day_end_actions 的 day_end_actions 必须是数组")
        method, path, body = "POST", "/api/day/end", {"day_end_actions": day_end_actions}
    elif action == "start_next_day":
        method, path, body = "POST", "/api/day/start", {}
    elif action in {
        "resolve_temporary_conflict",
        "repair_tent",
        "manage_greenery",
        "improve_service",
        "clean_tents",
        "buy_food_package",
        "purchase_growth_project",
        "restart_game",
    }:
        method, path, body = "POST", "/api/action", {"action": action, "params": extra}
    else:
        raise _McpError(
            -32602,
            "未知 camping_plaza action；请先 get_guide(game=\"camping_plaza\")，再用 actions 查看当前可执行动作。",
        )

    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    try:
        response = httpx.request(
            method,
            f"{CAMPING_PLAZA_BASE}{path}",
            json=body,
            headers=headers,
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"camping_plaza 后端连接失败：{exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise _McpError(-32603, "camping_plaza 后端返回非 JSON 响应") from exc
    if response.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("error_code")
        code = -32602 if response.status_code < 500 else -32603
        raise _McpError(code, detail or f"camping_plaza 后端错误 HTTP {response.status_code}")
    return payload


def _fishing_import(arguments):
    extra = {key: value for key, value in arguments.items() if key not in {"game", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    save_data = extra.get("save_data")
    if save_data is None:
        raise _McpError(-32602, "save_data 必填")
    if isinstance(save_data, str):
        try:
            parsed = json.loads(save_data)
        except (json.JSONDecodeError, ValueError):
            raise _McpError(-32602, "save_data 不是合法 JSON 字符串")
        if not isinstance(parsed, dict):
            raise _McpError(-32602, "save_data 必须是 JSON 对象")
    elif isinstance(save_data, dict):
        pass
    else:
        raise _McpError(-32602, "save_data 必须是 JSON 对象或 JSON 字符串")
    serialized = json.dumps(save_data, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > 128 * 1024:
        raise _McpError(-32602, "save_data 序列化后超过 128KB")
    try:
        return fishing_adapter.play(extra)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc))


def _play_vendor_cmd(game, arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    extra["action"] = action

    try:
        if game == "bar":
            return bar_adapter.play(extra)
        if game == "leek":
            return leek_adapter.play(extra)
        if game == "delve":
            return delve_adapter.play(extra)
        if game == "travel":
            return travel_adapter.play(extra)
        if game == "arcade":
            return arcade_adapter.play(extra)
        if game == "burger":
            return burger_adapter.play(extra)
        if game == "crucible_echoes":
            return crucible_echoes_adapter.play(extra)
        if game == "fishing":
            return fishing_adapter.play(extra)
        if game == "forest":
            return forest_adapter.play(extra)
        if game == "moonlit":
            return moonlit_adapter.play(extra)
        if game == "imitator_td":
            return imitator_td_adapter.play(extra)
        if game == "memoria":
            return memoria_adapter.play(extra)
        if game == "white_room":
            return white_room_adapter.play(extra)
        if game == "market":
            return market_adapter.play(extra)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc))
    raise _McpError(-32602, "未知游戏")


def _json_rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _garden_cat_proxy_allowed(method, public_path):
    if method == "GET":
        return public_path in GARDEN_CAT_PROXY_GET_PATHS or public_path.startswith("/static/")
    if method == "POST":
        return public_path in GARDEN_CAT_PROXY_POST_PATHS
    return False


def _garden_cat_upstream_path(public_path):
    return "/web/" if public_path == "/" else public_path


def _camping_plaza_proxy_allowed(method, public_path):
    if method == "GET":
        return (
            public_path in {
                "/", "/api/health", "/api/state", "/api/actions",
                "/api/growth", "/api/achievements",
            }
            or public_path.startswith(("/styles/", "/scripts/", "/assets/"))
        )
    if method == "POST":
        return public_path in {
            "/api/session", "/api/player/name", "/api/turn/advance",
            "/api/turn/plan", "/api/day/end", "/api/day/start", "/api/action",
            "/api/nature-observation/intro/seen",
        }
    return False


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


def _duel_proxy_allowed(method, public_path):
    if method == "GET":
        return (
            public_path in {
                "/", "/chips", "/api/whoami", "/api/chips",
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
            or re.fullmatch(r"/api/rooms/[A-Z0-9]{8}", public_path) is not None
        )
    if method == "POST":
        return (
            public_path == "/api/rooms"
            or _duel_chip_proxy_post_allowed(public_path)
            or re.fullmatch(
                r"/api/rooms/[A-Z0-9]{8}/(?:invitation|join|move|resign|leave|messages|retention|delete)",
                public_path,
            )
            is not None
        )
    return False


class CedarToyHandler(BaseHTTPRequestHandler):
    server_version = "CedarToy/1.0"
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        internal_path = self.path.split("?", 1)[0]
        if internal_path == "/_internal/duel-gateway/prepare":
            self._handle_duel_gateway_prepare()
            return
        if internal_path == "/_internal/duel-gateway/finalize":
            self._handle_duel_gateway_finalize()
            return
        if internal_path == "/_internal/duel-gateway/abandon":
            self._handle_duel_gateway_abandon()
            return

        if self._is_soup_path():
            self._proxy_to_soup()
            return

        _workkk_path = self.path.split("?", 1)[0]
        if _workkk_path == "/workkk" or _workkk_path.startswith("/workkk/"):
            self._handle_workkk_proxy("POST")
            return

        # /gc-view 围观入口已下线（2026-07-27），仅剩外部老链接会命中。
        if _workkk_path == "/gc-view" or _workkk_path.startswith("/gc-view/"):
            self._send_json(
                {"error": "围观入口已下线，请从首页进入花园", "code": 410},
                status=410,
            )
            return

        if _workkk_path == "/garden-cat" or _workkk_path.startswith("/garden-cat/"):
            self._handle_garden_cat_proxy("POST")
            return

        if _workkk_path == "/camping-plaza" or _workkk_path.startswith("/camping-plaza/"):
            self._handle_camping_plaza_proxy("POST")
            return

        if _workkk_path == "/duel" or _workkk_path.startswith("/duel/"):
            self._handle_duel_proxy("POST")
            return

        if _workkk_path == "/eco/api/human_action":
            self._handle_eco_human_action()
            return

        if _workkk_path == "/forest/api/action":
            self._handle_forest_api_action()
            return

        path, path_token = self._request_path_and_token()
        client_ip = self._client_ip()

        if path == "/api/auth/login":
            self._handle_api_login()
            return

        if path == "/api/auth/register":
            self._handle_api_register()
            return

        if path == "/api/auth/avatar":
            self._handle_api_avatar()
            return

        if path == "/api/auth/login_or_register":
            self._handle_api_login_or_register()
            return

        if path == "/api/auth/machine-token":
            self._handle_api_machine_token()
            return

        if path == "/api/auth/bind":
            self._handle_api_bind()
            return

        if path == "/api/auth/change-password":
            self._handle_api_change_password()
            return

        if path == "/api/auth/delete-account":
            self._handle_api_delete_account()
            return

        if path == "/api/auth/cancel-delete-account":
            self._handle_api_cancel_delete_account()
            return

        if path == "/api/account/email/send-code":
            self._handle_api_account_email_send()
            return

        if path == "/api/account/email/confirm":
            self._handle_api_account_email_confirm()
            return

        if path == "/api/auth/forgot-password":
            self._handle_api_forgot_password()
            return

        if path == "/api/auth/forgot-password/reset":
            self._handle_api_forgot_password_reset()
            return

        if path == "/api/auth/rename":
            self._handle_api_rename()
            return

        if path == "/api/announcements/read":
            self._handle_api_announcements_read()
            return

        if path == "/api/auth/reset-password":
            self._handle_api_reset_password()
            return

        if path == "/api/admin/generate-reset-link":
            self._handle_admin_generate_reset_link()
            return

        if path == "/api/anti-addiction/settings":
            self._handle_api_anti_addiction_save()
            return

        if path == "/api/anti-addiction/reset":
            self._handle_api_anti_addiction_reset()
            return

        if path == "/api/arcade/chips":
            self._handle_api_arcade_grant()
            return

        human_test_match = re.fullmatch(r"/api/(mbti|enneagram|dnd|love|ecr|humanity|sins_virtues)/(start|answer_batch|result|compare)", path)
        if human_test_match:
            if human_test_match.group(2) == "compare" and human_test_match.group(1) not in {"love", "ecr"}:
                self._drain_body()
                self._send_json({"error": "not found"}, status=404)
                return
            self._handle_human_test_api(*human_test_match.groups())
            return

        if path.startswith("/api/admin/users/") and path.endswith("/reset-password"):
            self._handle_admin_reset_password(path)
            return

        if path not in (*_ROOT_MCP_PATHS, "/mbti", "/enneagram", "/dnd", "/love", "/ecr", "/humanity", "/sins_virtues") and not path_token:
            self._drain_body()
            self._send_json({"error": "not found"}, status=404)
            return

        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            try:
                raw_body = self._read_chunked_body()
            except ValueError:
                self._send_json(_json_rpc_error(None, -32700, "Parse error"), status=400)
                return
        else:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(_json_rpc_error(None, -32700, "Invalid Content-Length"), status=400)
                return

            raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(_json_rpc_error(None, -32700, "Parse error"), status=400)
            return

        if not isinstance(payload, dict):
            self._send_json(_json_rpc_error(None, -32600, "Invalid Request"), status=400)
            return

        if path not in {"/mbti", "/enneagram", "/dnd", "/love", "/ecr", "/humanity", "/sins_virtues"} and (path in _ROOT_MCP_PATHS or path_token) and "id" not in payload:
            self._send_empty(status=202)
            return

        rate_identity = self._request_rate_limit_identity(path_token, client_ip)
        rate_limit_max = REQUEST_RATE_LIMIT_MAX
        if _is_duel_play_payload(payload):
            # Duel 官方网关的整段挂等只在 prepare 计一次独立配额，
            # 内部 30 秒心跳不再回到此处；身份仍沿用同一 token/IP。
            rate_identity = f"{rate_identity}:duel"
            rate_limit_max = DUEL_REQUEST_RATE_LIMIT_MAX
        if not _check_request_rate_limit(rate_identity, max_count=rate_limit_max):
            self._send_json(_json_rpc_error(payload.get("id"), RATE_LIMIT_ERROR_CODE, REQUEST_RATE_LIMIT_MESSAGE), status=429)
            return

        if path == "/mbti":
            response = handle_mbti_mcp(_guestify_mcp_payload(payload))
        elif path == "/enneagram":
            response = handle_enneagram_mcp(_guestify_mcp_payload(payload))
        elif path == "/dnd":
            response = handle_dnd_mcp(_guestify_mcp_payload(payload))
        elif path == "/love":
            response = handle_love_mcp(_guestify_mcp_payload(payload))
        elif path == "/ecr":
            response = handle_ecr_mcp(_guestify_mcp_payload(payload))
        elif path == "/humanity":
            response = handle_humanity_mcp(_guestify_mcp_payload(payload))
        elif path == "/sins_virtues":
            response = handle_sins_virtues_mcp(_guestify_mcp_payload(payload))
        else:
            response = _handle_root_mcp(
                payload,
                user_agent=self.headers.get("User-Agent", ""),
                path_token=path_token,
                client_ip=client_ip,
                bearer_token=_extract_bearer(self.headers),
            )
        self._send_json(response)

    def do_GET(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        path, _, query_string = self.path.partition("?")
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)

        if path == "/workkk" or path.startswith("/workkk/"):
            self._handle_workkk_proxy("GET")
            return


        # /gc-view 围观入口已下线（2026-07-27），仅剩外部老链接会命中。
        if path == "/gc-view" or path.startswith("/gc-view/"):
            self._send_json(
                {"error": "围观入口已下线，请从首页进入花园", "code": 410},
                status=410,
            )
            return

        if path == "/garden-cat" or path.startswith("/garden-cat/"):
            self._handle_garden_cat_proxy("GET")
            return

        if path == "/camping-plaza" or path.startswith("/camping-plaza/"):
            self._handle_camping_plaza_proxy("GET")
            return

        if path.startswith("/static/games/") and _duel_proxy_allowed("GET", path):
            self._proxy_to_duel("GET", path, query_string)
            return

        if path == "/duel" or path.startswith("/duel/"):
            self._handle_duel_proxy("GET")
            return

        if self._is_mcp_event_stream_get(path):
            self._send_json(
                {
                    "error": "GET text/event-stream is not supported",
                    "message": "本服务端不提供 GET 流；请用 POST 发送 JSON-RPC。",
                },
                status=405,
                extra_headers={"Allow": "POST"},
            )
            return

        if path == "/":
            self._send_html_file(TOY_INDEX_PATH)
            return

        if path == "/admin":
            self._send_html_file(ADMIN_INDEX_PATH)
            return

        if path == "/eco":
            self._send_html_file(ECO_INDEX_PATH)
            return

        if path in {"/forest", "/forest/"}:
            self._handle_forest_page(params)
            return

        if path in ("/mbti", "/enneagram", "/dnd", "/love", "/ecr", "/humanity", "/sins_virtues") and not params.get("action"):
            self._send_human_test_page(path.removeprefix("/"))
            return

        if path.startswith("/eco/assets/"):
            self._send_eco_asset(path)
            return

        if path.startswith("/assets/icons/"):
            self._send_icon_asset(path)
            return

        if path == "/health":
            self._send_json({"ok": True, "service": "cedartoy", "endpoints": ["https://toy.cedarstar.org/mbti", "https://toy.cedarstar.org/enneagram", "https://toy.cedarstar.org/dnd", "https://toy.cedarstar.org/love", "https://toy.cedarstar.org/ecr", "https://toy.cedarstar.org/humanity", "https://toy.cedarstar.org/sins_virtues", "https://toy.cedarstar.org/"]})
            return

        if path == "/api/games/stats":
            self._send_json(_public_game_stats(), extra_headers={"Cache-Control": "no-cache, no-store"})
            return

        if path == "/api/memoria/guides":
            include_content = (params.get("confirm") or [""])[0] == "human"
            self._send_json(_memoria_human_guides(include_content=include_content), extra_headers={"Cache-Control": "no-cache, no-store"})
            return

        if path == "/api/auth/me":
            self._handle_api_me()
            return

        if path == "/api/auth/avatar":
            self._send_json({
                "supported": True,
                "type": "emoji",
                "max_codepoints": AVATAR_MAX_CODEPOINTS,
                "max_utf8_bytes": AVATAR_MAX_UTF8_BYTES,
                "defaults": {"human": DEFAULT_HUMAN_AVATAR, "ai": DEFAULT_AI_AVATAR},
            })
            return

        if path == "/api/auth/deletion":
            self._handle_api_deletion_status()
            return

        if path == "/api/account/email":
            self._handle_api_account_email_status()
            return

        if path == "/api/announcements":
            self._handle_api_announcements()
            return

        if path == "/api/auth/saves":
            self._handle_api_auth_saves()
            return

        if path == "/api/garden-cat/gardens":
            self._handle_api_garden_cat_gardens()
            return

        if path == "/api/forest/saves":
            self._handle_api_forest_saves()
            return

        if path == "/forest/api/state":
            self._handle_forest_api_state(params)
            return

        if path == "/api/eco/ponds":
            self._handle_api_eco_ponds()
            return

        if path == "/api/anti-addiction/machines":
            self._handle_api_anti_addiction_machines()
            return

        if path == "/api/arcade/chips":
            self._handle_api_arcade_status(params)
            return

        human_test_match = re.fullmatch(r"/api/(mbti|enneagram|dnd|love|ecr|humanity|sins_virtues)/result", path)
        if human_test_match:
            self._handle_human_test_api(human_test_match.group(1), "result", params=params)
            return

        if path == "/eco/api/state":
            self._handle_eco_api("state", params)
            return

        if path == "/eco/api/codex":
            self._handle_eco_api("codex", params)
            return

        if path == "/eco/api/folio":
            self._handle_eco_api("folio", params)
            return

        if path == "/eco/api/annals":
            self._handle_eco_api("annals", params)
            return

        if path.startswith("/eco/api/species/"):
            raw_name = path.removeprefix("/eco/api/species/")
            self._handle_eco_api("species", params, species_name=urllib.parse.unquote(raw_name))
            return

        if path == "/api/admin/users":
            self._handle_admin_users()
            return

        if path == "/mbti":
            self._handle_get_mbti(params)
            return

        if path == "/enneagram":
            self._handle_get_enneagram(params)
            return

        if path == "/dnd":
            self._handle_get_dnd(params)
            return

        self._send_json({"error": "not found"}, status=404)

    def do_PUT(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/admin/users/"):
            self._handle_admin_update_user(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_PATCH(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self._send_json({"error": "not found"}, status=404)

    def do_DELETE(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/auth/bind":
            self._handle_api_unbind()
            return
        if path == "/api/account/email":
            self._handle_api_account_email_unbind()
            return
        if path.startswith("/api/admin/users/"):
            self._handle_admin_release_user(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_OPTIONS(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version, Last-Event-ID, X-Requested-With")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        self.end_headers()

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

    def _forest_cookie_token(self):
        cookie = self.headers.get("Cookie", "")
        for item in cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == "forest_token":
                return urllib.parse.unquote(value)
        return ""

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

    def _handle_api_auth_saves(self):
        try:
            result = _account_web_saves(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
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

    def log_message(self, fmt, *args):
        safe_message = _redact_http_log_text(fmt % args)
        print("%s - - [%s] %s" % (
            self.client_address[0],
            self.log_date_time_string(),
            safe_message,
        ))

    def _send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status=204, extra_headers=None):
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", "0")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()

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
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_html_bytes(self, body, etag=None, extra_headers=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        if etag is not None:
            self.send_header("ETag", etag)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

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

    def _proxy_to_workkk(self, method, upstream_path, query_string, rewrite_html=False, set_cookie=None, force_player=None):
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
        for untrusted in ("token", "player", "player_id", "opponent_id"):
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
            if not _duel_chip_proxy_post_allowed(upstream_path):
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
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "garden_cat_token":
                return urllib.parse.unquote(value)
        return None

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
            human_name=(user.get("username") if not is_static else None),
        )

    def _proxy_to_garden_cat(
        self, method, upstream_path, query_string, set_cookie=None, target=None,
        human_name=None,
    ):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        params.pop("token", None)
        params.pop("player", None)
        fwd_query = urllib.parse.urlencode(
            [(key, value) for key, values in params.items() for value in values]
        )
        request_target = upstream_path + (f"?{fwd_query}" if fwd_query else "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in (
                "host", "cookie", "authorization", "x-player-id",
                "x-garden-owner-name", "x-garden-slot", "x-garden-player",
                "x-garden-human-name", "x-forwarded-prefix",
            )
        }
        headers["Host"] = "garden-cat.local"
        headers["X-Forwarded-For"] = self.client_address[0] if self.client_address else "unknown"
        headers["X-Forwarded-Prefix"] = getattr(self, "_gc_prefix_override", "/garden-cat")
        if target is not None:
            # Browser identity headers never survive the filter above. Only this
            # canonical player, derived from the authenticated binding, reaches Flask.
            headers["X-Player-Id"] = target["player"]
            headers["X-Garden-Player"] = target["player"]
            headers["X-Garden-Owner-Name"] = urllib.parse.quote(str(target["owner_name"]))
            headers["X-Garden-Slot"] = str(target["slot"])
            if isinstance(human_name, str) and human_name.strip():
                headers["X-Garden-Human-Name"] = urllib.parse.quote(human_name.strip())

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
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            cookie_name, _, value = part.strip().partition("=")
            if cookie_name == name:
                return urllib.parse.unquote(value)
        return None

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
        )

    def _proxy_to_camping_plaza(
        self, method, upstream_path, query_string, set_cookies=None, target=None,
    ):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        params.pop("token", None)
        params.pop("player", None)
        fwd_query = urllib.parse.urlencode(
            [(key, value) for key, values in params.items() for value in values]
        )
        request_target = upstream_path + (f"?{fwd_query}" if fwd_query else "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in {"host", "cookie", "authorization", "x-player-id"}
        }
        headers["Host"] = "camping-plaza.local"
        headers["X-Forwarded-For"] = self.client_address[0] if self.client_address else "unknown"
        headers["X-Forwarded-Prefix"] = "/camping-plaza"
        if target is not None:
            headers["X-Player-Id"] = target["player"]

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

        if upstream_path == "/" and status < 400:
            raw = raw.replace(b'href="styles/', b'href="/camping-plaza/styles/')
            raw = raw.replace(b'src="scripts/', b'src="/camping-plaza/scripts/')
            raw = raw.replace(b'src="assets/', b'src="/camping-plaza/assets/')
            mobile_css = b"""
<style id=\"cedartoy-camping-mobile\">
@media (max-width: 760px) {
  html { min-width: 0 !important; width: 100%; overflow-x: hidden; }
  body { min-width: 0; width: 100%; padding: 8px; overflow-x: hidden; }
  .app-shell { min-width: 0 !important; width: 100%; max-width: none; }
  .top-dashboard { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .metric-card { min-width: 0; min-height: 72px; padding: 10px; gap: 8px; }
  .metric-icon { width: 30px; height: 30px; flex: 0 0 30px; }
  .metric-card strong { font-size: 18px; overflow-wrap: anywhere; }
  .achievement-card { grid-column: 1 / -1; }
  .notice-strip { grid-template-columns: 22px minmax(0, 1fr); padding: 9px 10px; }
  .notice-list { min-width: 0; }
  .notice-chip { max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
  .main-layout { grid-template-columns: minmax(0, 1fr); gap: 10px; }
  .main-column, .side-column { min-width: 0; gap: 10px; }
  .map-area, .panel-card { min-width: 0; padding: 10px; border-radius: 14px; }
  .camp-map { width: 100%; min-height: 0 !important; aspect-ratio: 4 / 3; border-radius: 10px; }
  .service-station-label, .anchor-label, .campsite-slot::after { font-size: 8px; }
  .action-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .morning-review-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .morning-review-wide { grid-column: 1 / -1; }
  .operations-heading { align-items: flex-start; }
  .operations-hints { width: 100%; }
  .hint-chip { white-space: normal; }
  .overview-grid, .income-columns { min-width: 0; }
  .achievement-modal, .temporary-event-modal, .onboarding-screen { padding: 10px; }
  .achievement-dialog, .temporary-event-dialog, .onboarding-card { width: 100%; max-width: 100%; }
  .achievement-dialog { max-height: calc(100dvh - 20px); padding: 14px; }
  .onboarding-card { padding: 20px 16px; }
  .temporary-event-choices { flex-direction: column; }
  button, input { max-width: 100%; }
  .btn-action, .onboarding-submit, .achievement-close { min-height: 42px; touch-action: manipulation; }
}
@media (max-width: 380px) {
  .top-dashboard { grid-template-columns: 1fr; }
  .achievement-card { grid-column: auto; }
  .action-grid, .morning-review-grid, .overview-grid, .income-columns { grid-template-columns: 1fr; }
  .morning-review-wide { grid-column: auto; }
}
</style>
"""
            raw = raw.replace(b"</head>", mobile_css + b"</head>", 1)
        # Upstream JS uses root-absolute /api URLs and relative asset URLs. Under
        # CedarToy it lives below /camping-plaza, so rewrite those literals at
        # the edge without changing the vendor checkout.
        if upstream_path == "/scripts/overview.js" and status < 400:
            raw = raw.replace(b"'/api/", b"'/camping-plaza/api/")
            raw = raw.replace(b'"/api/', b'"/camping-plaza/api/')
            raw = raw.replace(b"'assets/", b"'/camping-plaza/assets/")
            raw = raw.replace(b'"assets/', b'"/camping-plaza/assets/')
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
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


_JWT_LOG_VALUE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]*(?:\.|%2[eE])[A-Za-z0-9_-]+(?:\.|%2[eE])[A-Za-z0-9_-]+"
)
_OPAQUE_AI_LOG_VALUE_RE = re.compile(r"ctai_v1_[A-Za-z0-9_-]{40,}")
_SENSITIVE_QUERY_LOG_RE = re.compile(
    r"([?&](?:token|reset_token|access_token)=)[^&#\s\"]+",
    re.IGNORECASE,
)


def _redact_http_log_text(value):
    text = str(value)
    text = _OPAQUE_AI_LOG_VALUE_RE.sub("<TOKEN_REDACTED>", text)
    text = _JWT_LOG_VALUE_RE.sub("<TOKEN_REDACTED>", text)
    return _SENSITIVE_QUERY_LOG_RE.sub(r"\1<TOKEN_REDACTED>", text)


class ThreadPoolHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, max_workers=MAX_WORKERS):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.worker_slots = BoundedSemaphore(max_workers)
        super().__init__(server_address, RequestHandlerClass)

    def process_request(self, request, client_address):
        if not self.worker_slots.acquire(timeout=QUEUE_TIMEOUT_SECONDS):
            self._send_busy(request)
            self.close_request(request)
            return
        self.executor.submit(self._process_request_thread, request, client_address)

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self.worker_slots.release()

    def server_close(self):
        super().server_close()
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)

    @staticmethod
    def _send_busy(request):
        body = b'{"error":"server busy"}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Connection: close\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"\r\n" + body
        )
        try:
            request.sendall(response)
        except OSError:
            pass


def main():
    _migrate_platform_timestamps()
    _init_announcement_tables()
    server = ThreadPoolHTTPServer((HOST, PORT), CedarToyHandler)
    print(f"CedarToy listening on {HOST}:{PORT} with max_workers={MAX_WORKERS}")
    server.serve_forever()


if __name__ == "__main__":
    main()
