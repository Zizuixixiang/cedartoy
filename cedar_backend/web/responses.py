"""HTTP shell helpers, with dependencies supplied by server at call time.

The compatibility methods retain their signatures and all per-game handler and
proxy boundaries. Route order and response bytes are preserved.
"""

from cedar_backend import mcp_diagnostics


def _diagnostic_headers(self):
    trace = getattr(self, "_mcp_diagnostic", None)
    if trace is not None and trace.target and trace.delivery.get("gateway_link_verified"):
        self.send_header(mcp_diagnostics.TRACE_ID_HEADER, trace.fields["trace_id"])


def _send_json(self, payload, status, extra_headers, *, json):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
    if extra_headers:
        for key, value in extra_headers.items():
            self.send_header(key, value)
    _diagnostic_headers(self)
    mcp_diagnostics.observe_main_send(self, "main_headers_write", self.end_headers)
    mcp_diagnostics.observe_main_send(self, "main_body_write", lambda: self.wfile.write(body), len(body))


def _send_empty(self, status, extra_headers):
    self.send_response(status)
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Content-Length", "0")
    if extra_headers:
        for key, value in extra_headers.items():
            self.send_header(key, value)
    _diagnostic_headers(self)
    mcp_diagnostics.observe_main_send(self, "main_headers_write", self.end_headers)
    mcp_diagnostics.observe_main_send(self, "main_body_write", lambda: 0, 0)


def _send_html_bytes(self, body, etag, extra_headers):
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


def _json_rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
