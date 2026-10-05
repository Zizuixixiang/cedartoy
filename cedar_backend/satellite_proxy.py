"""Pure HTTP policy and forwarding helpers for Garden Cat and Camping Plaza.

Authentication, binding resolution, transport, activity recording and response
writes stay in server.CedarToyHandler. Runtime policy values are passed in so
server-level patches remain effective. This module never imports server.
"""

import urllib.parse


GARDEN_CAT_PROXY_GET_PATHS = frozenset({"/", "/api/catalog", "/web/status", "/web/notes"})
GARDEN_CAT_PROXY_POST_PATHS = frozenset({
    "/web/notes",
    "/web/register",
    "/web/cmd",
    "/web/new_game",
    "/web/move_with_cat",
    "/web/bouquets/read",
})


def garden_cat_proxy_allowed(method, public_path, *, get_paths, post_paths):
    if method == "GET":
        return public_path in get_paths or public_path.startswith("/static/")
    if method == "POST":
        return public_path in post_paths
    return False


def garden_cat_upstream_path(public_path):
    return "/web/" if public_path == "/" else public_path


def camping_plaza_proxy_allowed(method, public_path):
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


def cookie_value(raw, name):
    """Keep the legacy first-match and percent-decoding cookie semantics."""
    for part in raw.split(";"):
        cookie_name, _, value = part.strip().partition("=")
        if cookie_name == name:
            return urllib.parse.unquote(value)
    return None


def request_target(upstream_path, query_string):
    params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
    params.pop("token", None)
    params.pop("player", None)
    fwd_query = urllib.parse.urlencode(
        [(key, value) for key, values in params.items() for value in values]
    )
    return upstream_path + (f"?{fwd_query}" if fwd_query else "")


def garden_cat_request_headers(incoming, *, hop_by_hop_headers, forwarded_for,
                               prefix, target=None, human_name=None):
    headers = {
        key: value
        for key, value in incoming.items()
        if key.lower() not in hop_by_hop_headers
        and key.lower() not in (
            "host", "cookie", "authorization", "x-player-id", "x-forwarded-prefix",
        )
        and not key.lower().startswith("x-garden-")
    }
    headers["Host"] = "garden-cat.local"
    headers["X-Forwarded-For"] = forwarded_for
    headers["X-Forwarded-Prefix"] = prefix
    if target is not None:
        # Only identity derived from the authenticated binding reaches upstream.
        headers["X-Player-Id"] = target["player"]
        headers["X-Garden-Player"] = target["player"]
        headers["X-Garden-Owner-Name"] = urllib.parse.quote(str(target["owner_name"]))
        headers["X-Garden-Slot"] = str(target["slot"])
        if isinstance(human_name, str) and human_name.strip():
            headers["X-Garden-Human-Name"] = urllib.parse.quote(human_name.strip())
    return headers


def camping_plaza_request_headers(incoming, *, hop_by_hop_headers, forwarded_for,
                                  target=None):
    headers = {
        key: value
        for key, value in incoming.items()
        if key.lower() not in hop_by_hop_headers
        and key.lower() not in {"host", "cookie", "authorization", "x-player-id"}
        and not key.lower().startswith("x-garden-")
    }
    headers["Host"] = "camping-plaza.local"
    headers["X-Forwarded-For"] = forwarded_for
    headers["X-Forwarded-Prefix"] = "/camping-plaza"
    if target is not None:
        headers["X-Player-Id"] = target["player"]
    return headers


def rewrite_camping_plaza_response(raw, upstream_path, status):
    """Apply the existing edge rewrites, preserving injected bytes exactly."""
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
    return raw
