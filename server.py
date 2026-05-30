import json
import http.client
import random
import re
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import BoundedSemaphore

import httpx

from dnd.handler import handle_mcp as handle_dnd_mcp
from mbti.handler import handle_mcp as handle_mbti_mcp


HOST = "0.0.0.0"
PORT = 8002
MAX_WORKERS = 50
QUEUE_TIMEOUT_SECONDS = 10
SOUP_HOST = "127.0.0.1"
SOUP_PORT = 8012
SOUP_BASE = f"http://{SOUP_HOST}:{SOUP_PORT}"
GUIDE_DIR = Path(__file__).resolve().parent / "turtle-soup" / "backend" / "guides"
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


_PLATFORM_TOOLS = [
    {
        "name": "list_games",
        "description": "列出所有可用游戏，返回分类列表（测试类、小游戏类）及简介",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_guide",
        "description": "获取指定游戏的玩法说明",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "游戏名称，如 turtle_soup、mbti",
                },
            },
            "required": ["game"],
            "additionalProperties": False,
        },
    },
    {
        "name": "play",
        "description": "执行游戏操作，参数详见 get_guide(game)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "游戏名称",
                },
                "action": {
                    "type": "string",
                    "description": "操作名称",
                },
            },
            "required": ["game", "action"],
            "additionalProperties": True,
        },
    },
]
_ROOT_TOOL_NAMES = frozenset({"list_games", "get_guide", "play"})


def _handle_root_mcp(payload):
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    try:
        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cedartoy", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _json_rpc_result(request_id, {"tools": _root_tools()})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                if name == "list_games":
                    text = _tool_list_games()
                elif name == "get_guide":
                    text = _tool_get_guide(arguments)
                elif name == "play":
                    text = _tool_play(arguments)
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
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _tool_list_games():
    return json.dumps({
        "测试": [
            {"name": "mbti", "display": "MBTI", "desc": "16型人格测试，4种模式可选（短/完整/快速）"},
            {"name": "dnd", "display": "DND阵营测试", "desc": "测试你的D&D道德阵营，守序善良还是混乱邪恶？"},
        ],
        "小游戏": [
            {"name": "turtle_soup", "display": "海龟汤", "desc": "横向思维推理游戏，提问猜汤底"},
        ],
        "提示": "用 get_guide(game) 查看具体玩法，再用 play(game, action, ...) 执行操作",
    }, ensure_ascii=False)


def _root_tools():
    return [tool for tool in _PLATFORM_TOOLS if tool.get("name") in _ROOT_TOOL_NAMES]


def _tool_get_guide(arguments):
    game = arguments.get("game")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        return json.dumps(_turtle_soup_guide(), ensure_ascii=False)
    if game in {"mbti", "dnd"}:
        path = GUIDE_DIR / f"{game}.md"
        if not path.exists():
            raise _McpError(-32603, f"{game} 说明文件不存在")
        return json.dumps({"game": game, "guide": path.read_text(encoding="utf-8")}, ensure_ascii=False)
    raise _McpError(-32602, "未知游戏")


def _tool_play(arguments):
    game = arguments.get("game")
    action = arguments.get("action")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if not action or not isinstance(action, str):
        raise _McpError(-32602, "action 参数必填")
    if game == "turtle_soup":
        resp = httpx.post(f"{SOUP_BASE}/mcp/play", json=arguments, timeout=60)
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)
    if game == "mbti":
        return json.dumps(_play_mbti(arguments), ensure_ascii=False)
    if game == "dnd":
        return json.dumps(_play_dnd(arguments), ensure_ascii=False)
    raise _McpError(-32602, "未知游戏")


def _turtle_soup_guide():
    return {
        "game": "turtle_soup",
        "actions": {
            "register": "username, password -> 注册/登录",
            "create_random": "创建随机题房间",
            "join": "room_id -> 加入进行中的房间",
            "ask": "room_id, content -> 提问",
            "guess": "room_id, content -> 猜汤底",
            "hint_respond": "room_id, log_id, accept -> 处理提示",
            "status": "room_id -> 查看进度和问答记录",
            "list_rooms": "查看大厅房间列表",
        },
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


def _json_rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class CedarToyHandler(BaseHTTPRequestHandler):
    server_version = "CedarToy/1.0"

    def do_POST(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        if self.path not in ("/", "/mbti", "/dnd"):
            self._send_json({"error": "not found"}, status=404)
            return

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

        if self.path == "/mbti":
            response = handle_mbti_mcp(payload)
        elif self.path == "/dnd":
            response = handle_dnd_mcp(payload)
        else:
            response = _handle_root_mcp(payload)
        self._send_json(response)

    def do_GET(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        path, _, query_string = self.path.partition("?")
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)

        if path in ("/", "/health"):
            self._send_json({"ok": True, "service": "cedartoy", "endpoints": ["https://toy.cedarstar.org/mbti", "https://toy.cedarstar.org/dnd", "https://toy.cedarstar.org/"]})
            return

        if path == "/mbti":
            self._handle_get_mbti(params)
            return

        if path == "/dnd":
            self._handle_get_dnd(params)
            return

        self._send_json({"error": "not found"}, status=404)

    def do_PUT(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self._send_json({"error": "not found"}, status=404)

    def do_DELETE(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self._send_json({"error": "not found"}, status=404)

    def do_OPTIONS(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self.send_response(204)
        self.end_headers()

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
        is_fast = "快速批量" in result_text or "answer_batch" in result_text

        r = random.randint(100000, 999999)
        question_match = re.search(r"第(\d+)题", result_text)
        step = question_match.group(1) if question_match else str(r)
        url_suffix = f"&_r={r}&step={step}"

        # 逐题模式：生成 next_urls 数组
        if game == "mbti":
            score_param = "a_score"
            score_range = range(0, 6)  # 0~5
        else:
            score_param = "answer"
            score_range = range(1, 5)  # 1~4

        if action == f"{game}_start":
            if is_fast:
                # fast 模式保持单个 URL + hint
                next_url = f"{base_url}?action={game}_answer_batch&player_id={player_id}{url_suffix}"
                next_hint = f"末尾加 &{score_param}s=N1,N2,...（支持方括号/圆括号包裹，如 [5,4,3]；short_fast共16个，full_fast每批≤16个，N=0~5，5偏A，0偏B）" if game == "mbti" else f"末尾加 &{score_param}s=N1,N2,...（支持方括号/圆括号包裹，如 [1,2,3]；每批≤16个，N=1~4对应选项序号）"
                next_urls = None
            else:
                # 逐题模式：生成 next_urls 数组
                next_urls = [f"{base_url}?action={game}_answer&player_id={player_id}&{score_param}={n}{url_suffix}" for n in score_range]
                next_url = None
                next_hint = "根据选择从 next_urls 中选对应 a_score 的 URL 直接 fetch，无需修改" if game == "mbti" else "根据选择从 next_urls 中选对应 answer 的 URL 直接 fetch，无需修改"
        elif action in (f"{game}_answer", f"{game}_answer_batch"):
            if is_finished:
                next_url = f"{base_url}?action={game}_get_result&player_id={player_id}{url_suffix}"
                next_hint = None
                next_urls = None
            elif action == f"{game}_answer_batch":
                # fast 模式保持单个 URL + hint
                next_url = f"{base_url}?action={game}_answer_batch&player_id={player_id}{url_suffix}"
                next_hint = f"末尾加 &{score_param}s=N1,N2,...（支持方括号/圆括号包裹，如 [5,4,3]；short_fast共16个，full_fast每批≤16个，N=0~5，5偏A，0偏B）" if game == "mbti" else f"末尾加 &{score_param}s=N1,N2,...（支持方括号/圆括号包裹，如 [1,2,3]；每批≤16个，N=1~4对应选项序号）"
                next_urls = None
            else:
                # 逐题模式：生成 next_urls 数组
                next_urls = [f"{base_url}?action={game}_answer&player_id={player_id}&{score_param}={n}{url_suffix}" for n in score_range]
                next_url = None
                next_hint = "根据选择从 next_urls 中选对应 a_score 的 URL 直接 fetch，无需修改" if game == "mbti" else "根据选择从 next_urls 中选对应 answer 的 URL 直接 fetch，无需修改"
        else:
            return response

        if isinstance(response, dict):
            if next_urls:
                response["next_urls"] = next_urls
            elif next_url:
                response["next_url"] = next_url
            if next_hint:
                response["next_hint"] = next_hint
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
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "mbti_answer":
            player_id = self._get_param(params, "player_id")
            a_score = self._get_param(params, "a_score")
            if player_id is None or a_score is None:
                self._send_json({"error": "mbti_answer 缺少必填参数: player_id, a_score"}, status=400)
                return
            arguments = {"player_id": player_id, "a_score": a_score}
        elif action == "mbti_answer_batch":
            player_id = self._get_param(params, "player_id")
            a_scores = self._get_param(params, "a_scores")
            if player_id is None or a_scores is None:
                self._send_json({"error": "mbti_answer_batch 缺少必填参数: player_id, a_scores"}, status=400)
                return
            arguments = {"player_id": player_id, "a_scores": self._split_csv_param(a_scores)}
        elif action == "mbti_get_result":
            player_id = self._get_param(params, "player_id")
            if player_id is None:
                self._send_json({"error": "mbti_get_result 缺少必填参数: player_id"}, status=400)
                return
            arguments = {"player_id": player_id}
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

        payload = {
            "jsonrpc": "2.0",
            "id": f"mbti-{action}",
            "method": "tools/call",
            "params": {"name": action, "arguments": arguments},
        }
        response = handle_mbti_mcp(payload)
        response = self._append_next_url(response, "mbti", action, player_id)
        self._send_json(response, extra_headers={"Cache-Control": "no-cache, no-store"})

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
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "dnd_answer":
            player_id = self._get_param(params, "player_id")
            answer = self._get_param(params, "answer")
            if player_id is None or answer is None:
                self._send_json({"error": "dnd_answer 缺少必填参数: player_id, answer"}, status=400)
                return
            arguments = {"player_id": player_id, "answer": answer}
        elif action == "dnd_answer_batch":
            player_id = self._get_param(params, "player_id")
            answers = self._get_param(params, "answers")
            if player_id is None or answers is None:
                self._send_json({"error": "dnd_answer_batch 缺少必填参数: player_id, answers"}, status=400)
                return
            arguments = {"player_id": player_id, "answers": self._split_csv_param(answers)}
        elif action == "dnd_get_result":
            player_id = self._get_param(params, "player_id")
            if player_id is None:
                self._send_json({"error": "dnd_get_result 缺少必填参数: player_id"}, status=400)
                return
            arguments = {"player_id": player_id}
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

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
        print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def _send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _is_soup_path(self):
        return (
            self.path == "/soup"
            or self.path.startswith("/soup/")
            or self.path == "/mcp"
            or self.path.startswith("/mcp/")
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
        conn = http.client.HTTPConnection(SOUP_HOST, SOUP_PORT, timeout=60)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            self.end_headers()
            while True:
                chunk = resp.read(8192)
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


def _json_rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class ThreadPoolHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, max_workers=MAX_WORKERS):
        super().__init__(server_address, RequestHandlerClass)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.worker_slots = BoundedSemaphore(max_workers)

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
    server = ThreadPoolHTTPServer((HOST, PORT), CedarToyHandler)
    print(f"CedarToy listening on {HOST}:{PORT} with max_workers={MAX_WORKERS}")
    server.serve_forever()


if __name__ == "__main__":
    main()
