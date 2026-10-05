"""HTTP shell helpers, with dependencies supplied by server at call time.

The compatibility methods retain their signatures and all per-game handler and
proxy boundaries. Route order and response bytes are preserved.
"""


def do_POST(
    self,
    *,
    __name__,
    DUEL_REQUEST_RATE_LIMIT_MAX,
    RATE_LIMIT_ERROR_CODE,
    REQUEST_RATE_LIMIT_MAX,
    REQUEST_RATE_LIMIT_MESSAGE,
    _ROOT_MCP_PATHS,
    _check_request_rate_limit,
    _extract_bearer,
    _guestify_mcp_payload,
    _handle_root_mcp,
    _is_duel_play_payload,
    _json_rpc_error,
    handle_dnd_mcp,
    handle_ecr_mcp,
    handle_enneagram_mcp,
    handle_humanity_mcp,
    handle_love_mcp,
    handle_mbti_mcp,
    handle_sins_virtues_mcp,
    json,
    nowhere_web,
    re,
    sys,
):
    internal_path = self.path.split("?", 1)[0]
    if internal_path == "/nowhere" or internal_path.startswith("/nowhere/"):
        nowhere_web.serve(self, sys.modules[__name__])
        return
    if internal_path == "/api/puzzle-box/reveal":
        self._handle_puzzle_box(reveal=True)
        return
    if internal_path == "/_internal/duel-gateway/prepare":
        self._handle_duel_gateway_prepare()
        return
    if internal_path == "/_internal/duel-gateway/finalize":
        self._handle_duel_gateway_finalize()
        return
    if internal_path == "/_internal/duel-gateway/abandon":
        self._handle_duel_gateway_abandon()
        return

    if self._is_tarot_post_path(internal_path):
        self._handle_tarot_post(internal_path)
        return

    if self._is_soup_path():
        self._proxy_to_soup()
        return

    if internal_path.startswith("/detroit/api/"):
        self._handle_detroit_api("POST", internal_path)
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

    if path == "/api/operit/session":
        self._handle_api_operit_session()
        return

    if path == "/api/operit/bind":
        self._handle_api_operit_bind()
        return

    if path == "/api/operit/duel":
        self._handle_api_operit_duel()
        return

    if path == "/api/operit/web-ticket":
        self._handle_api_operit_web_ticket()
        return

    if path == "/api/auth/login":
        self._handle_api_login()
        return

    if path == "/api/auth/register":
        self._handle_api_register()
        return

    if path == "/api/auth/avatar":
        self._handle_api_avatar()
        return

    if path == "/api/auth/avatar-frames":
        self._handle_api_avatar_frames(save=True)
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

    if path in ("/api/auth/recovery/submit", "/api/auth/recovery/query"):
        self._handle_api_recovery(path)
        return

    if path == "/api/admin/recovery/review":
        self._handle_admin_recovery(review=True)
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

    if path == "/api/announcements/vote":
        self._handle_api_announcement_vote()
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


def do_GET(
    self,
    *,
    __name__,
    ADMIN_INDEX_PATH,
    AVATAR_MAX_CODEPOINTS,
    AVATAR_MAX_UTF8_BYTES,
    DEFAULT_AI_AVATAR,
    DEFAULT_HUMAN_AVATAR,
    ECO_INDEX_PATH,
    Path,
    _duel_proxy_allowed,
    _memoria_human_guides,
    _public_game_stats,
    nowhere_web,
    re,
    sys,
    urllib,
):
    if self.path.split("?", 1)[0] == "/nowhere" or self.path.startswith("/nowhere/"):
        nowhere_web.serve(self, sys.modules[__name__])
        return
    if self._is_soup_path():
        self._proxy_to_soup()
        return

    path, _, query_string = self.path.partition("?")
    params = urllib.parse.parse_qs(query_string, keep_blank_values=True)

    if path.startswith("/tutorials/"):
        tutorial_root = Path("/var/www/tutorials").resolve()
        relative = urllib.parse.unquote(path.removeprefix("/tutorials/"))
        try:
            tutorial_path = (tutorial_root / relative).resolve()
            tutorial_path.relative_to(tutorial_root)
        except (OSError, RuntimeError, ValueError):
            self._send_json({"error": "not found"}, status=404)
            return
        if tutorial_path.suffix.lower() != ".html" or not tutorial_path.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_html_file(
            tutorial_path,
            extra_headers={"Cache-Control": "public, max-age=300"},
        )
        return

    if path == "/detroit" or path.startswith("/detroit/"):
        self._handle_detroit_get(path, params)
        return

    if self._is_tarot_get_path(path):
        self._handle_tarot_get(path, params)
        return

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

    if path == "/ai-life" or path.startswith("/ai-life/"):
        self._handle_ai_life_get(path, params)
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
        self._send_tarot_homepage()
        return

    if path == "/admin":
        self._send_html_file(ADMIN_INDEX_PATH)
        return

    if path == "/eco":
        self._send_html_file(ECO_INDEX_PATH)
        return

    if path == "/moonlit/freshness":
        self._handle_moonlit_freshness(params)
        return

    if path in {"/moonlit", "/moonlit/"}:
        self._handle_moonlit_page(params)
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

    if path == "/api/puzzle-box/progress":
        self._handle_puzzle_box(params=params)
        return

    if path == "/api/auth/me":
        self._handle_api_me()
        return

    if path == "/api/auth/reset-password":
        self._handle_api_reset_password_info(params)
        return

    if path == "/api/auth/avatar-frames":
        self._handle_api_avatar_frames()
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

    if path == "/api/nowhere/saves":
        self._handle_api_nowhere_saves()
        return

    if path == "/api/auth/saves":
        self._handle_api_auth_saves()
        return

    if path == "/api/auth/history":
        self._handle_api_auth_history()
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

    if path == "/api/admin/activity":
        self._handle_admin_activity(params)
        return

    if path == "/api/admin/recovery":
        self._handle_admin_recovery()
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


def do_DELETE(self, *, __name__, nowhere_web, sys):
    if self.path.split("?", 1)[0] == "/nowhere" or self.path.startswith("/nowhere/"):
        nowhere_web.serve(self, sys.modules[__name__])
        return
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
