import http.client
import json
import threading
import unittest

import avatar_appearances as appearances
import server
from tests_toy import test_account_avatar as avatar_tests


class AvatarAppearanceTests(unittest.TestCase):
    _connect = avatar_tests.AccountAvatarTests._connect
    _user = avatar_tests.AccountAvatarTests._user

    def setUp(self):
        avatar_tests.AccountAvatarTests.setUp(self)
        with self._connect() as conn:
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for user_id, is_ai in ((1, 0), (2, 0), (3, 1)):
                conn.execute(
                    "INSERT INTO toy_users (id, username, password_hash, is_ai) VALUES (?, ?, 'unused', ?)",
                    (user_id, f"AppearanceUser{user_id}", is_ai),
                )
            appearances.seed_trial(conn)
        self.tokens = {i: server._create_account_token(self._user(i)) for i in (1, 2, 3)}
        self.httpd = server.ThreadPoolHTTPServer(("127.0.0.1", 0), server.CedarToyHandler, max_workers=2)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        avatar_tests.AccountAvatarTests.tearDown(self)

    def request(self, method="GET", body=None, user_id=1, raw_token=None, path="/api/auth/avatar-frames"):
        headers = {"Content-Type": "application/json"}
        token = raw_token if raw_token is not None else self.tokens.get(user_id)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request(method, path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            result = json.loads(response.read())
            return response.status, result
        finally:
            connection.close()

    def test_schema_and_seed_are_idempotent_and_preserve_choice(self):
        with self._connect() as conn:
            for _ in range(2):
                appearances.init_schema(conn)
                appearances.seed_trial(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_avatar_inventory").fetchone()[0], 2)
            self.assertEqual(appearances.selected(conn, 1), "cedartoy_1w_decoration")
            for choice in (None, "cedartoy_1w"):
                appearances.select(conn, 1, choice)
                appearances.init_schema(conn)
                appearances.seed_trial(conn)
                self.assertEqual(appearances.selected(conn, 1), choice)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_get_inventory_requires_auth_and_is_account_scoped(self):
        status, data = self.request()
        self.assertEqual(status, 200)
        self.assertEqual({item["key"] for item in data["items"]}, set(appearances.CATALOG))
        self.assertEqual(data["selected"], "cedartoy_1w_decoration")
        for user_id in (2, 3):
            status, data = self.request(user_id=user_id)
            self.assertEqual(status, 200)
            self.assertEqual(data["items"], [])
            self.assertIsNone(data["selected"])
            self.assertEqual(data["user"]["id"], user_id)
            self.assertEqual(data["machines"], [])
        for raw_token in ("", "invalid"):
            self.assertEqual(self.request(raw_token=raw_token)[0], 401)
            self.assertEqual(self.request("POST", {"selected": None}, raw_token=raw_token)[0], 401)

    def test_save_rejects_unowned_unknown_and_malformed_selection(self):
        for user_id in (2, 3):
            status, _ = self.request("POST", {"selected": "cedartoy_1w", "user_id": 1}, user_id=user_id)
            self.assertEqual(status, 400)
        for body in ({}, {"selected": "unknown"}, {"selected": []}, {"selected": {}}, {"selected": 1}):
            self.assertEqual(self.request("POST", body)[0], 400)
        self.assertEqual(self.request()[1]["selected"], "cedartoy_1w_decoration")
        self.assertEqual(self.request(user_id=2)[1]["selected"], None)

    def test_save_persists_and_emoji_edit_preserves_frame(self):
        for choice in ("cedartoy_1w", None, "cedartoy_1w_decoration", "", "none"):
            expected = choice if choice in appearances.CATALOG else None
            status, data = self.request("POST", {"selected": choice})
            self.assertEqual(status, 200)
            self.assertEqual(data["user"]["avatar_frame"], expected)
            self.assertEqual(self.request()[1]["selected"], expected)
            status, edited = self.request("POST", {"avatar": "🌲"}, path="/api/auth/avatar")
            self.assertEqual(status, 200)
            self.assertEqual(edited["user"]["avatar"]["value"], "🌲")
            self.assertEqual(edited["user"]["avatar_frame"], expected)
            self.assertEqual(server._public_user(self._user(1))["avatar_frame"], expected)
        self.assertEqual(self.request("POST", {"selected": None}, user_id=2)[0], 200)

    def test_bulk_grant_does_not_equip_and_account_delete_cascades(self):
        with self._connect() as conn:
            for _ in range(2):
                appearances.grant(conn, [2, 3], ["cedartoy_1w"])
            self.assertEqual(len(appearances.owned(conn, 2)["items"]), 1)
            self.assertIsNone(appearances.selected(conn, 2))
            appearances.select(conn, 2, "cedartoy_1w")
            conn.execute("DELETE FROM toy_users WHERE id IN (1, 2)")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_avatar_selection").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_avatar_inventory").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_bound_machine_inventory_and_save_are_independent(self):
        with self._connect() as conn:
            conn.execute("INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (1, 3)")
        self.assertEqual(self.request()[1]["machines"], [{"id": 3, "username": "AppearanceUser3"}])
        path = "/api/auth/avatar-frames?target_user_id=3"
        status, data = self.request(path=path)
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["id"], 3)
        self.assertEqual(data["items"], [])
        body = {"target_user_id": 3, "selected": "cedartoy_1w"}
        self.assertEqual(self.request("POST", body)[0], 400, "human inventory cannot supply machine items")
        with self._connect() as conn:
            appearances.grant(conn, [3], ["cedartoy_1w"])
        status, saved = self.request("POST", body)
        self.assertEqual(status, 200)
        self.assertEqual(saved["user"]["id"], 3)
        self.assertEqual(self.request(path=path)[1]["selected"], "cedartoy_1w")
        self.assertEqual(self.request()[1]["selected"], "cedartoy_1w_decoration")
        self.assertEqual(self.request("POST", {**body, "selected": None})[0], 200)

    def test_target_permissions_are_rechecked_after_unbind(self):
        path = "/api/auth/avatar-frames?target_user_id=3"
        body = {"target_user_id": 3, "selected": None}
        for actor, target in ((1, 2), (1, 3), (2, 1), (3, 1), (3, 2), (1, 999)):
            self.assertEqual(self.request(user_id=actor, path=f"/api/auth/avatar-frames?target_user_id={target}")[0], 403)
            self.assertEqual(self.request("POST", {"target_user_id": target, "selected": None}, user_id=actor)[0], 403)
        with self._connect() as conn:
            conn.execute("INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (1, 3)")
        self.assertEqual(self.request(path=path)[0], 200)
        with self._connect() as conn:
            conn.execute("DELETE FROM user_bindings WHERE human_user_id = 1")
        self.assertEqual(self.request("POST", body)[0], 403)
        self.assertEqual(self.request(path=path)[0], 403)
        self.assertEqual(self.request()[1]["machines"], [])
        for invalid in (True, [], {}, 1.5, "", "-1", "invalid"):
            self.assertEqual(self.request("POST", {"target_user_id": invalid, "selected": None})[0], 400)

    def test_pending_or_deleted_machine_cannot_be_targeted(self):
        with self._connect() as conn:
            conn.execute("INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (1, 3)")
        for field in ("deletion_requested_at_epoch", "deleted_at"):
            with self._connect() as conn:
                conn.execute(f"UPDATE toy_users SET {field} = 1 WHERE id = 3")
            self.assertEqual(self.request()[1]["machines"], [])
            self.assertEqual(self.request(path="/api/auth/avatar-frames?target_user_id=3")[0], 403)
            self.assertEqual(self.request("POST", {"target_user_id": 3, "selected": None})[0], 403)
            with self._connect() as conn:
                conn.execute(f"UPDATE toy_users SET {field} = NULL WHERE id = 3")


if __name__ == "__main__":
    unittest.main()
