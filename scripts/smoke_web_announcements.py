#!/usr/bin/env python3
"""Isolated smoke test for the CedarToy homepage announcement API."""

import json
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import announcements  # noqa: E402
import server  # noqa: E402


def request_json(base_url, path, *, method="GET", token="", payload=None):
    body = None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def create_account_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE toy_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_ai INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                last_active_at TEXT DEFAULT (datetime('now', 'localtime')),
                deleted_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO toy_users (username, password_hash, is_ai)
            VALUES (?, ?, 0)
            """,
            ("smoke_human", server._hash_password("smoke-pass")),
        )


def create_sessions_db(path):
    with sqlite3.connect(path) as conn:
        announcements.init_db(conn)
        conn.execute(
            """
            INSERT INTO announcements
                (id, type, title, content, options, multiple, target_game, created_at)
            VALUES
                ('web-smoke-1', 'notice', 'Smoke Notice', 'Shown once', NULL, 0, 'all',
                 '2026-07-30 12:00:00')
            """
        )
        # Machine-side identity must not suppress the human homepage notification.
        conn.execute(
            """
            INSERT INTO announcement_reads (player_id, announcement_id, votes, read_at)
            VALUES ('1', 'web-smoke-1', NULL, '2026-07-30 12:01:00')
            """
        )


def assert_read_identities(sessions_db):
    with sqlite3.connect(sessions_db) as conn:
        identities = {
            row[0]
            for row in conn.execute(
                """
                SELECT player_id
                FROM announcement_reads
                WHERE announcement_id = 'web-smoke-1'
                """
            )
        }
    assert identities == {"1", "human:1"}, identities


def run_direct_smoke(sessions_db):
    guest = server._web_announcements("")
    assert guest["authenticated"] is False, guest
    assert guest["unread_count"] == 0, guest
    assert len(guest["announcements"]) == 1, guest
    assert guest["announcements"][0]["unread"] is False, guest

    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM announcement_reads"
        ).fetchone()[0] == 1, "guest query wrote an announcement_reads row"

    login = server._login_or_register_human(
        "smoke_human",
        "smoke-pass",
        client_ip="127.0.0.1",
    )
    token = login["token"]
    first = server._web_announcements(token)
    assert first["authenticated"] is True, first
    assert first["unread_count"] == 1, first
    assert first["announcements"][0]["unread"] is True, first

    marked = server._mark_web_announcements_read(token, ["web-smoke-1"])
    assert marked["marked"] == 1, marked
    second = server._web_announcements(token)
    assert second["unread_count"] == 0, second
    assert second["announcements"][0]["unread"] is False, second
    assert_read_identities(sessions_db)


def main():
    with tempfile.TemporaryDirectory(prefix="cedartoy-announcement-smoke-") as temp_dir:
        temp_root = Path(temp_dir)
        account_db = temp_root / "accounts.db"
        sessions_db = temp_root / "sessions.db"
        create_account_db(account_db)
        create_sessions_db(sessions_db)

        server.TURTLE_DB_PATH = account_db
        server.SESSIONS_DB_PATH = sessions_db
        announcements.DB_PATH = str(sessions_db)
        try:
            httpd = server.ThreadPoolHTTPServer(
                ("127.0.0.1", 0),
                server.CedarToyHandler,
                max_workers=4,
            )
        except PermissionError:
            run_direct_smoke(sessions_db)
            print("PASS (direct fallback; local sockets unavailable): login unread=1, read once then unread=0, guest unread=0/no read")
            return
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{httpd.server_port}"

        try:
            status, guest = request_json(base_url, "/api/announcements")
            assert status == 200, guest
            assert guest["authenticated"] is False, guest
            assert guest["unread_count"] == 0, guest
            assert len(guest["announcements"]) == 1, guest
            assert guest["announcements"][0]["unread"] is False, guest

            with sqlite3.connect(sessions_db) as conn:
                assert conn.execute(
                    "SELECT COUNT(*) FROM announcement_reads"
                ).fetchone()[0] == 1, "guest query wrote an announcement_reads row"

            status, login = request_json(
                base_url,
                "/api/auth/login_or_register",
                method="POST",
                payload={"username": "smoke_human", "password": "smoke-pass"},
            )
            assert status == 200, login
            token = login["token"]

            status, first = request_json(
                base_url,
                "/api/announcements",
                token=token,
            )
            assert status == 200, first
            assert first["authenticated"] is True, first
            assert first["unread_count"] == 1, first
            assert first["announcements"][0]["unread"] is True, first

            status, marked = request_json(
                base_url,
                "/api/announcements/read",
                method="POST",
                token=token,
                payload={"announcement_ids": ["web-smoke-1"]},
            )
            assert status == 200, marked
            assert marked["marked"] == 1, marked

            status, second = request_json(
                base_url,
                "/api/announcements",
                token=token,
            )
            assert status == 200, second
            assert second["unread_count"] == 0, second
            assert second["announcements"][0]["unread"] is False, second

            assert_read_identities(sessions_db)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    print("PASS: login unread=1, read once then unread=0, guest unread=0/no read")


if __name__ == "__main__":
    main()
