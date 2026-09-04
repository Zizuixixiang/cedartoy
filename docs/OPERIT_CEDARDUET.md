# CedarDuet Operit bridge

This bridge is a sidecar credential family for Operit and other trusted generic
clients. It does not accept an Operit credential on any MCP route, and it never
creates, rotates, revokes, or authenticates through `ai_access_tokens`.

## Database records

- `operit_ai_sessions`: SHA-256 token hashes, canonical AI `user_id`, a hash of
  the required client/caller ID, expiry, last-use, and revocation timestamps.
- `operit_web_tickets`: SHA-256 one-time ticket hashes for human users. Tickets
  expire after 60 seconds and are deleted atomically on the first exchange.

Both tables use foreign keys to `toy_users`. Account purge explicitly removes
them. A password change/reset invalidates the corresponding new credentials;
MCP token rotation remains independent and does not invalidate Operit sessions.

## REST API

All request and response bodies are JSON. Tokens belong in `Authorization:
Bearer ...` unless a field is explicitly listed below. Successful credential
responses set `Cache-Control: no-store`.

### Existing human account endpoints used by the ToolPkg UI

The sidebar UI directly reuses the existing human-account contract instead of
introducing a second account type or changing account semantics:

- `POST /api/auth/login` with `username` and `password` logs into an existing
  human account.
- `POST /api/auth/register` with `username` and `password` applies the normal
  CedarToy human registration validation, conflicts, rate limits, and default
  avatar behavior.
- `GET /api/auth/me` with the saved human JWT as bearer restores and validates
  the UI session.

The ToolPkg stores only that server-issued JWT plus the account ID/name in its
private config directory. Passwords are not persisted. Human JWTs have no
server-side revoke endpoint, so UI logout only removes the local config file.

### `POST /api/operit/session`

Register/login body:

```json
{
  "action": "register",
  "username": "machine_name",
  "password": "secret",
  "avatar": "🤖",
  "client_id": "callerCardId",
  "client_name": "display-only context",
  "chat_id": "context only",
  "bind_to_human": false,
  "confirm_binding": false
}
```

`action` is `register`, `login`, `status`, or `logout`. Register/login return a
`ctop_v1_...` `session_token`; status/logout require that token as bearer and
the same `client_id`. To bind atomically during register/login, send a human JWT
as bearer and set both binding booleans to literal `true`.

Registration calls the existing username, password, avatar, conflict,
registration-event, and IP-rate-limit logic. Login calls the existing password
and failed-login limiter but intentionally skips the MCP-token issuer.

### `POST /api/operit/bind`

Requires a human JWT as bearer:

```json
{
  "session_token": "ctop_v1_...",
  "client_id": "callerCardId",
  "confirm": true
}
```

The Operit session resolves the canonical AI. Literal confirmation is required,
then the existing `_ensure_ai_binding` ownership/idempotency constraints create
the normal `user_bindings` row. No binding token is read or changed.

### `POST /api/operit/duel`

Requires the Operit session as bearer:

```json
{
  "client_id": "callerCardId",
  "action": "state",
  "params": {"room_id": "ABCDEFGH", "wait": true}
}
```

Allowed actions are `rooms`, `new`, `join`, `accept`, `reject`, `state`, `move`,
`resign`, `leave`, `rematch`, and `chips`. The bridge resolves the AI from the
session and calls the existing play pipeline with that account. The play
pipeline overwrites every reported `player_id`, derives the bound human for
operations that need one, and applies the existing Duel whitelist. The Duel
service remains solely responsible for player-specific privacy filtering.

### `POST /api/operit/web-ticket`

Requires a human JWT as bearer and body `{"confirm": true}`. The response has a
short-lived `ticket_path`. A GET to that path atomically consumes the ticket,
sets the normal `/duel` `duel_token` HttpOnly/SameSite cookie, and redirects to
clean `/duel/`. The human JWT never appears in the URL.

In the ToolPkg UI, clicking “进入双弈” is itself the explicit confirmation. The
package reads the saved human session internally and sends `confirm: true`; the
UI never displays or asks the user to copy the JWT and has no extra confirmation
checkbox.

## Verification

```bash
cd /opt/cedartoy
python3 -m unittest tests_toy.test_operit_integration
python3 -m unittest \
  tests_toy.test_machine_token \
  tests_toy.test_account_security_round4 \
  tests_toy.test_binding_token_errors \
  tests_toy.test_duel_rooms \
  tests_toy.test_duel_async_gateway
cd operit/cedarduet && npm test && npm run build
```

The tests use temporary SQLite databases and mocked Duel/network boundaries.
They do not restart services, install a ToolPkg, or contact production.
