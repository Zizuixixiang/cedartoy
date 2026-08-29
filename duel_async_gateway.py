"""Thin async edge for CedarToy MCP POST requests.

Nginx sends only candidate MCP POSTs here. Non-Duel requests are proxied byte-for-
byte to CedarToy. Duel calls use a short prepare/finalize handshake with CedarToy,
while the potentially long request goes directly from this ASGI process to Duel.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from starlette.responses import Response


CEDARTOY_ORIGIN = os.getenv("DUEL_GATEWAY_CEDARTOY_ORIGIN", "http://127.0.0.1:8002")
DUEL_ORIGIN = os.getenv("DUEL_GATEWAY_DUEL_ORIGIN", "http://127.0.0.1:8772")
SHARED_SECRET = os.getenv("DUEL_GATEWAY_SHARED_SECRET", "")
PREPARE_TIMEOUT_SECONDS = 5.0
DUEL_TIMEOUT_SECONDS = 55.0
PROXY_TIMEOUT_SECONDS = 65.0
MAX_INTERNAL_CONCURRENCY = 40
MAX_CEDARTOY_CONNECTIONS = 80


def _bounded_int_env(name, default, minimum, maximum):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是 {minimum}–{maximum} 的整数") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum}–{maximum} 的整数")
    return value


MAX_DUEL_CONCURRENCY = _bounded_int_env(
    "DUEL_GATEWAY_MAX_CONCURRENCY", 200, 100, 500
)

HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"proxy-connection",
    b"te",
    b"trailers",
    b"transfer-encoding",
    b"upgrade",
}
ROOT_MCP_PATHS = {"/", "/mcp", "/mcp/"}
RESERVED_SINGLE_SEGMENT_PATHS = {
    "/mbti",
    "/enneagram",
    "/dnd",
    "/love",
    "/ecr",
    "/humanity",
    "/sins_virtues",
    "/duel",
    "/workkk",
    "/garden-cat",
    "/camping-plaza",
}


def _raw_path(request):
    raw = request.scope.get("raw_path")
    if isinstance(raw, bytes):
        return raw.decode("latin-1")
    return request.url.path


def _request_target(request):
    target = _raw_path(request)
    query = request.scope.get("query_string", b"")
    if query:
        target += "?" + query.decode("latin-1")
    return target


def _client_ip(request):
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def _is_root_mcp_path(path):
    if path in ROOT_MCP_PATHS:
        return True
    stripped = path.strip("/")
    return bool(
        stripped
        and "/" not in stripped
        and path not in RESERVED_SINGLE_SEGMENT_PATHS
    )


def _duel_rpc_payload(raw_body, path):
    if not _is_root_mcp_path(path):
        return None
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict) or params.get("name") != "play":
        return None
    arguments = params.get("arguments")
    if not isinstance(arguments, dict) or arguments.get("game") != "duel":
        return None
    return payload


def _forward_request_headers(request):
    return [
        (key, value)
        for key, value in request.headers.raw
        if key.lower() not in HOP_BY_HOP_HEADERS
    ]


async def _raw_http_request(client, method, url, *, body, headers, timeout):
    outgoing = client.build_request(
        method,
        url,
        content=body,
        headers=headers,
        timeout=httpx.Timeout(timeout),
    )
    upstream = await client.send(outgoing, stream=True)
    try:
        if upstream.is_stream_consumed:
            raw = upstream.content
        else:
            chunks = [chunk async for chunk in upstream.aiter_raw()]
            raw = b"".join(chunks)
        response_headers = [
            (key, value)
            for key, value in upstream.headers.raw
            if key.lower() not in HOP_BY_HOP_HEADERS
        ]
        return upstream.status_code, response_headers, raw
    finally:
        await upstream.aclose()


def _raw_response(status_code, headers, body):
    response = Response(content=body, status_code=status_code)
    response.raw_headers = list(headers)
    return response


def _json_response(body, status_code=200):
    if body is None:
        return Response(
            status_code=status_code,
            headers={"Access-Control-Allow-Origin": "*"},
        )
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(
        content=raw,
        status_code=status_code,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Mcp-Session-Id",
        },
    )


def _gateway_tool_error(request_id, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {"type": "text", "text": f"【cedartoy服务错误】{message}"}
            ],
            "isError": True,
        },
    }


def _internal_headers(request, secret):
    headers = {
        "Content-Type": "application/json",
        "X-CedarToy-Gateway-Secret": secret,
        "X-CedarToy-Original-Path": _raw_path(request),
        "X-CedarToy-Client-IP": _client_ip(request),
        "User-Agent": request.headers.get("user-agent", ""),
    }
    authorization = request.headers.get("authorization")
    if authorization:
        headers["Authorization"] = authorization
    return headers


async def _internal_json_call(client, url, body, headers):
    status, _response_headers, raw = await _raw_http_request(
        client,
        "POST",
        url,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        timeout=PREPARE_TIMEOUT_SECONDS,
    )
    if status >= 400:
        raise RuntimeError(f"CedarToy internal endpoint HTTP {status}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CedarToy internal endpoint returned non-JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("CedarToy internal endpoint returned invalid JSON")
    return data


async def _proxy_to_cedartoy(request, body):
    client = request.app.state.cedartoy_client
    status, headers, raw = await _raw_http_request(
        client,
        request.method,
        CEDARTOY_ORIGIN + _request_target(request),
        body=body,
        headers=_forward_request_headers(request),
        timeout=PROXY_TIMEOUT_SECONDS,
    )
    return _raw_response(status, headers, raw)


async def _handle_duel(request, payload):
    request_id = payload.get("id")
    if "id" not in payload:
        return _json_response(None, 202)
    secret = request.app.state.shared_secret
    if not secret:
        return _json_response(
            _gateway_tool_error(request_id, "duel async gateway 尚未配置共享密钥")
        )
    cedartoy_client = request.app.state.cedartoy_client
    duel_client = request.app.state.duel_client
    internal_headers = _internal_headers(request, secret)
    try:
        async with request.app.state.internal_slots:
            prepared = await _internal_json_call(
                cedartoy_client,
                CEDARTOY_ORIGIN + "/_internal/duel-gateway/prepare",
                payload,
                internal_headers,
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        return _json_response(
            _gateway_tool_error(request_id, f"duel 网关认证失败：{exc}")
        )

    if prepared.get("kind") == "response":
        return _json_response(
            prepared.get("body"), int(prepared.get("status_code", 200))
        )
    if prepared.get("kind") != "ready":
        return _json_response(
            _gateway_tool_error(request_id, "duel 网关认证响应无效")
        )

    backend_body = json.dumps(
        prepared.get("backend_payload"), ensure_ascii=False
    ).encode("utf-8")
    try:
        status, _headers, raw = await _raw_http_request(
            duel_client,
            "POST",
            DUEL_ORIGIN + "/mcp/play",
            body=backend_body,
            headers={"Content-Type": "application/json"},
            timeout=DUEL_TIMEOUT_SECONDS,
        )
        try:
            backend_data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            completion = {"kind": "invalid_json"}
        else:
            completion = {
                "kind": "response",
                "status_code": status,
                "data": backend_data,
            }
    except httpx.HTTPError as exc:
        completion = {"kind": "transport_error", "message": str(exc)}

    try:
        async with request.app.state.internal_slots:
            finalized = await _internal_json_call(
                cedartoy_client,
                CEDARTOY_ORIGIN + "/_internal/duel-gateway/finalize",
                {"ticket": prepared.get("ticket"), "completion": completion},
                {
                    "Content-Type": "application/json",
                    "X-CedarToy-Gateway-Secret": secret,
                },
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        return _json_response(
            _gateway_tool_error(request_id, f"duel 网关收尾失败：{exc}")
        )
    if not finalized.get("ok") or not isinstance(finalized.get("body"), dict):
        return _json_response(
            _gateway_tool_error(
                request_id,
                str(finalized.get("message") or "duel 网关收尾响应无效"),
            )
        )
    return _json_response(
        finalized["body"], int(finalized.get("status_code", 200))
    )


def create_app(
    *,
    http_client=None,
    cedartoy_client=None,
    duel_client=None,
    shared_secret=None,
    concurrency=None,
):
    if http_client is not None:
        if cedartoy_client is not None or duel_client is not None:
            raise ValueError(
                "http_client 不能与 cedartoy_client/duel_client 同时传入"
            )
        cedartoy_client = http_client
        duel_client = http_client
    owns_cedartoy_client = cedartoy_client is None
    owns_duel_client = duel_client is None
    configured_secret = SHARED_SECRET if shared_secret is None else shared_secret
    configured_concurrency = concurrency or MAX_DUEL_CONCURRENCY

    @asynccontextmanager
    async def lifespan(application):
        if not application.state.shared_secret:
            raise RuntimeError("DUEL_GATEWAY_SHARED_SECRET 未配置")
        if application.state.cedartoy_client is None:
            application.state.cedartoy_client = httpx.AsyncClient(
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=MAX_CEDARTOY_CONNECTIONS,
                    max_keepalive_connections=40,
                ),
            )
        if application.state.duel_client is None:
            application.state.duel_client = httpx.AsyncClient(
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=configured_concurrency,
                    max_keepalive_connections=40,
                ),
            )
        try:
            yield
        finally:
            if (
                owns_cedartoy_client
                and application.state.cedartoy_client is not None
            ):
                await application.state.cedartoy_client.aclose()
            if owns_duel_client and application.state.duel_client is not None:
                await application.state.duel_client.aclose()

    application = FastAPI(
        title="CedarToy Duel Async Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.cedartoy_client = cedartoy_client
    application.state.duel_client = duel_client
    application.state.shared_secret = configured_secret
    application.state.duel_slots = asyncio.Semaphore(configured_concurrency)
    application.state.internal_slots = asyncio.Semaphore(MAX_INTERNAL_CONCURRENCY)

    @application.get("/health")
    async def health():
        return {"ok": True, "service": "cedartoy-duel-async-gateway"}

    @application.post("/{path:path}")
    async def gateway_post(request: Request, path: str):
        del path
        raw_body = await request.body()
        duel_payload = _duel_rpc_payload(raw_body, request.url.path)
        if duel_payload is None:
            try:
                return await _proxy_to_cedartoy(request, raw_body)
            except httpx.HTTPError as exc:
                return _json_response(
                    {"error": "CedarToy proxy failed", "detail": str(exc)},
                    502,
                )
        async with request.app.state.duel_slots:
            return await _handle_duel(request, duel_payload)

    return application


app = create_app()
