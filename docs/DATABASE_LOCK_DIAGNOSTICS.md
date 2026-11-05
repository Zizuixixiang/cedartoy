# Account database lock diagnostics

This code-only change does not change SQLite journal mode or production data.

## Behavior

Authentication still checks current user, token version and deletion status. Only subsequent last_active_at bookkeeping is best effort: timestamps younger than 60 seconds skip writing; each UPDATE/COMMIT lock wait is limited to 100ms. Busy/locked errors roll back before returning the already verified identity. Other errors propagate. An EXCLUSIVE lock that blocks identity reads still prevents authentication.

## Evidence

Main and Turtle Soup stderr logs contain account_db JSON with UTC time, PID, native thread, connection, safe filename/function/line, duration, SQLite error and transaction state. lock_wait_failed includes local successful-but-uncommitted write candidates and a best-effort /proc/locks snapshot. slow_database_call and slow_write_transaction cover durations over one second. auth_activity_deferred identifies optional bookkeeping failures. account_db_job gives soup cleanup start/end and duration; outcome=ok means the function returned, not that every internally caught individual cleanup succeeded.

No SQL text, parameters, request body, credentials, account IDs or full database paths are emitted by new diagnostics. Database events are limited per kind/callsite to one per 10 seconds; auth deferrals to ten per minute. Existing supervisor rotation applies.

A kernel lock PID is evidence at capture time, not proof it blocked the entire wait. Open transaction candidates are not proven blockers. Correlate PID/time/source and job events across both logs. Connections outside the instrumented helpers, unusual SQL forms, PID namespaces or locks released before sampling can prevent attribution. Never promise every lock will be attributable.

## Tests and rollback

Run tests_toy.test_auth_activity, test_db_observability and test_garden_picker_diagnostics under python3 -m unittest. Run tests_toy.test_soup_db_diagnostics with /opt/cedarstar/venv/bin/python for the real aiosqlite driver. The cross-process test uses a temporary database and verifies an external writer PID. No production contention is injected.

Restart cedartoy and turtle-soup with the explicit supervisor config to activate. Original code is in artifacts/db-lock-rollback-20261105-033252; the saved server.py preserves unrelated pre-existing changes. Database restore is unnecessary.
