"""Temporary stateless MCP probe. Standard library only; no CedarToy imports."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = 8784
PATHS = {"/mcp-test", "/mcp-test/"}
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MAX_BODY = 65536
# https://developers.openai.com/plugins/reference#_meta-fields-on-tool-descriptor
TOOL = {
    "name": "test_ping",
    "description": "Return pong without reading or changing user data.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
    "securitySchemes": [{"type": "noauth"}],
    "_meta": {"securitySchemes": [{"type": "noauth"}]},
}


def error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def dispatch(payload):
    request_id = payload["id"]
    method = payload["method"]
    params = payload.get("params", {})
    if not isinstance(params, dict):
        return error(request_id, -32602, "Invalid params")
    if method == "initialize":
        version = params.get("protocolVersion")
        result = {
            "protocolVersion": version if version in VERSIONS else VERSIONS[-1],
            "serverInfo": {"name": "cedartoy-mcp-test-probe", "version": "1.0.0"},
            "capabilities": {"tools": {}},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        if params.get("name") != "test_ping":
            return error(request_id, -32602, "Unknown tool")
        if params.get("arguments", {}) != {}:
            return error(request_id, -32602, "test_ping takes no arguments")
        result = {"content": [{"type": "text", "text": "pong"}], "isError": False}
    else:
        return error(request_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class ProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CedarToyMcpProbe/1.0"
    timeout = 10

    def reply(self, status, payload=None, **headers):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def check_request(self):
        if self.path.split("?", 1)[0] not in PATHS:
            self.reply(404, {"error": "Not found"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {
            "https://toy.cedarstar.org",
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }:
            self.reply(403, {"error": "Invalid Origin"})
            return False
        version = self.headers.get("MCP-Protocol-Version")
        if version is not None and version not in VERSIONS:
            self.reply(400, {"error": "Unsupported MCP-Protocol-Version"})
            return False
        return True

    def read_body(self):
        encoding = self.headers.get("Transfer-Encoding", "").lower()
        if encoding:
            if encoding != "chunked" or "Content-Length" in self.headers:
                raise ValueError("Invalid framing")
            body = bytearray()
            while True:
                line = self.rfile.readline(128)
                if not line.endswith(b"\r\n"):
                    raise ValueError("Invalid chunk")
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size < 0 or len(body) + size > MAX_BODY:
                    raise ValueError("Body too large")
                if size == 0:
                    # Trailers are not used by this probe.
                    if self.rfile.readline(8192) != b"\r\n":
                        raise ValueError("Unsupported trailer")
                    return bytes(body)
                part = self.rfile.read(size)
                if len(part) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("Incomplete chunk")
                body.extend(part)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_BODY:
            raise ValueError("Invalid body size")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("Incomplete body")
        return body

    def do_POST(self):
        if not self.check_request():
            return
        try:
            payload = json.loads(self.read_body().decode("utf-8"))
        except (ValueError, OSError):
            self.reply(400, error(None, -32700, "Parse error"))
            return
        if (not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0"
                or not isinstance(payload.get("method"), str)):
            self.reply(400, error(None, -32600, "Invalid Request"))
            return
        # Log method only: no arguments, credentials, or request bodies.
        self.log_message("MCP method=%r", payload["method"])
        if "id" not in payload:
            self.reply(202)
            return
        if type(payload["id"]) not in (str, int):
            self.reply(400, error(None, -32600, "Invalid request id"))
            return
        self.reply(200, dispatch(payload))

    def do_GET(self):
        if self.check_request():
            self.reply(405, {"error": "Use POST JSON-RPC; no GET SSE stream"}, Allow="POST, OPTIONS")

    do_DELETE = do_GET

    def do_OPTIONS(self):
        if self.check_request():
            self.reply(204, Allow="POST, OPTIONS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    with ThreadingHTTPServer((HOST, args.port), ProbeHandler) as httpd:
        print(f"MCP probe listening on http://{HOST}:{httpd.server_port}/mcp-test", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
