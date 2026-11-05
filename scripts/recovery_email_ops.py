"""Local-only recovery email preflight and conservative retry."""
from contextlib import closing
import hmac
import os
from pathlib import Path
import sqlite3
import secrets
from cedar_backend.account_recovery import RECOVERY_LINK_LIFETIME_SECONDS
import subprocess
import time

SMTP_KEYS = tuple("CEDARTOY_SMTP_" + k for k in
                  ("HOST", "FROM", "PORT", "SECURITY", "USERNAME", "PASSWORD"))

class MailOperationError(Exception):
    pass

def load_service_mail_environment(root, db_path):
    """Copy only mail settings in memory. Never return or print their values."""
    try:
        pid = subprocess.check_output(
            ["supervisorctl", "-c", "/etc/supervisor/supervisord.conf", "pid", "cedartoy"],
            stderr=subprocess.DEVNULL, timeout=10, text=True).strip()
        if not pid.isascii() or not pid.isdigit() or int(pid) <= 1:
            raise ValueError()
        proc = Path("/proc") / pid
        if (proc / "cwd").resolve() != root:
            raise ValueError()
        command = (proc / "cmdline").read_bytes().split(b"\0")
        if not any(arg in (b"server.py", str(root / "server.py").encode()) for arg in command):
            raise ValueError()
        allowed = set(SMTP_KEYS) | {"TURTLE_SOUP_DB"}
        values = {}
        for entry in (proc / "environ").read_bytes().split(b"\0"):
            key, separator, value = entry.partition(b"=")
            name = key.decode("utf-8", "replace")
            if separator and name in allowed:
                values[name] = value.decode("utf-8")
        actual = Path(values.get("TURTLE_SOUP_DB", str(root / "turtle-soup/backend/turtle_soup.db")))
        if not actual.is_absolute():
            actual = root / actual
        if actual.resolve() != Path(db_path).resolve():
            raise ValueError()
        for key in SMTP_KEYS:
            os.environ.pop(key, None)
            if key in values:
                os.environ[key] = values[key]
    except (OSError, ValueError, subprocess.SubprocessError):
        raise MailOperationError("无法安全加载当前服务邮件环境；未审核或发送。请检查服务状态、进程权限及账号库。") from None

def read_connection(db_path):
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn

def status(db_path, ticket_id):
    with closing(read_connection(db_path)) as conn:
        row = conn.execute("SELECT email_status, email_error FROM account_recovery_tickets WHERE id=?",
                           (ticket_id,)).fetchone()
        return dict(row) if row else {}

def preflight(backend, ticket_id, root=None):
    with closing(read_connection(backend.TURTLE_DB_PATH)) as conn:
        row = conn.execute("SELECT contact_email FROM account_recovery_tickets WHERE id=?",
                           (ticket_id,)).fetchone()
    if row and row[0]:
        if root is not None:
            load_service_mail_environment(root, backend.TURTLE_DB_PATH)
        backend._smtp_config()

def resend(backend, ticket_id, reviewer, *, send=False):
    """Retry only a known pre-SMTP failure, preserving every original credential."""
    preflight(backend, ticket_id)
    uri = Path(backend.TURTLE_DB_PATH).resolve().as_uri() + ("?mode=rw" if send else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if send else "BEGIN")
            row = conn.execute("SELECT * FROM account_recovery_tickets WHERE id=?", (ticket_id,)).fetchone()
            if not row:
                raise MailOperationError("工单不存在")
            ticket = dict(row)
            if (ticket["status"] != "approved" or ticket["email_status"] != "failed"
                    or ticket["email_error"] != "email_provider_not_configured"):
                raise MailOperationError("仅可补发已批准且因未配置而明确未投递的通知；已发送、发送中或结果不明不可重试")
            now = int(time.time())
            if (not ticket["contact_email"] or ticket["reset_token_version"] != 2
                    or not ticket["reset_token_id"] or not ticket["claim_until_epoch"]
                    or ticket["claim_until_epoch"] <= now):
                raise MailOperationError("缺少原始有效找回信息，不能补发")
            user = conn.execute("""SELECT username FROM toy_users WHERE id=? AND is_ai=0
                AND deleted_at IS NULL AND deletion_requested_at_epoch IS NULL
                AND scheduled_delete_at_epoch IS NULL""", (ticket["user_id"],)).fetchone()
            if not user:
                raise MailOperationError("目标账号不可找回")
            reset = conn.execute("""SELECT token, user_id, used,
                CAST(strftime('%s', expires_at) AS INTEGER) AS expires_epoch
                FROM password_reset_tokens WHERE id=?""", (ticket["reset_token_id"],)).fetchone()
            token = backend._recovery_reset_token(ticket)
            if (not reset or reset["used"] or reset["user_id"] != ticket["user_id"]
                    or not reset["expires_epoch"] or reset["expires_epoch"] <= now
                    or not hmac.compare_digest(reset["token"], backend._reset_token_hash(token))):
                raise MailOperationError("原找回链接失效或签名不匹配；未改动凭据或发送")
            result = {"ok": True, "ticket_id": ticket_id, "dry_run": not send,
                      "original_token_valid": True, "reviewer": reviewer,
                      "recipient": backend._mask_email(ticket["contact_email"])}
            if not send:
                return result
            changed = conn.execute("""UPDATE account_recovery_tickets
                SET email_status='sending', email_error='', email_attempted_at_epoch=?,
                    email_finished_at_epoch=NULL
                WHERE id=? AND status='approved' AND email_status='failed'
                    AND email_error='email_provider_not_configured'""", (now, ticket_id))
            if changed.rowcount != 1:
                raise MailOperationError("通知状态已变化，未发送")
            conn.commit()
        finally:
            if conn.in_transaction:
                conn.rollback()
    # Crash or timeout after reservation is uncertain; never auto-retry.
    backend._deliver_recovery_notification(
        ticket_id, ticket["contact_email"], "approved", ticket["admin_note"],
        backend._reset_url(token), verified_username=user["username"])
    result.update(status(backend.TURTLE_DB_PATH, ticket_id))
    result["ok"] = result.get("email_status") == "sent"
    return result

def renew(backend, ticket_id, reviewer, *, expected_token_id=None, send=False):
    """Issue once from an explicitly observed expired token; never retry SMTP."""
    if send and expected_token_id is None:
        raise MailOperationError("续发必须指定预检返回的 --expected-token-id")
    preflight(backend, ticket_id)
    uri = Path(backend.TURTLE_DB_PATH).resolve().as_uri() + ("?mode=rw" if send else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if send else "BEGIN")
            admin = conn.execute("""SELECT id FROM toy_users WHERE id=? AND is_admin=1
                AND deleted_at IS NULL AND deletion_requested_at_epoch IS NULL
                AND scheduled_delete_at_epoch IS NULL""", (reviewer["id"],)).fetchone()
            if not admin:
                raise MailOperationError("操作人不是有效管理员")
            row = conn.execute("SELECT * FROM account_recovery_tickets WHERE id=?", (ticket_id,)).fetchone()
            if not row:
                raise MailOperationError("工单不存在")
            ticket = dict(row)
            now = int(time.time())
            if (ticket["status"] != "approved" or ticket["completed_at_epoch"] is not None
                    or ticket["email_status"] != "sent" or ticket["email_error"]
                    or not ticket["reviewed_at_epoch"] or not ticket["reviewed_by"]
                    or not ticket["claim_until_epoch"] or ticket["claim_until_epoch"] <= now
                    or now >= ticket["reviewed_at_epoch"] + 7 * 86400):
                raise MailOperationError("仅可续发原领取窗口内已批准、未完成且上次明确发送成功的工单")
            if (not ticket["contact_email"] or ticket["reset_token_version"] != 2
                    or not ticket["reset_token_id"] or not ticket["reset_nonce"]):
                raise MailOperationError("缺少原始找回信息")
            if expected_token_id is not None and ticket["reset_token_id"] != expected_token_id:
                raise MailOperationError("链接记录已变化；拒绝重复或过时的续发操作")
            user = conn.execute("""SELECT username FROM toy_users WHERE id=? AND is_ai=0
                AND deleted_at IS NULL AND deletion_requested_at_epoch IS NULL
                AND scheduled_delete_at_epoch IS NULL""", (ticket["user_id"],)).fetchone()
            if not user:
                raise MailOperationError("目标账号不可找回")
            reset = conn.execute("""SELECT token, user_id, used,
                CAST(strftime('%s', expires_at) AS INTEGER) AS expires_epoch
                FROM password_reset_tokens WHERE id=?""", (ticket["reset_token_id"],)).fetchone()
            if (not reset or reset["used"] or reset["user_id"] != ticket["user_id"]
                    or not reset["expires_epoch"] or reset["expires_epoch"] > now
                    or not hmac.compare_digest(reset["token"],
                                               backend._reset_token_hash(backend._recovery_reset_token(ticket)))):
                raise MailOperationError("原链接未过期、已使用或无法核验；未续发")
            result = {"ok": True, "ticket_id": ticket_id, "dry_run": not send,
                      "expected_token_id": ticket["reset_token_id"],
                      "recipient": backend._mask_email(ticket["contact_email"]),
                      "reviewer": reviewer, "claim_until_epoch": ticket["claim_until_epoch"]}
            if not send:
                return result
            # Same v2 derivation as approval/query; raw credentials live only in memory.
            ticket["reset_nonce"] = secrets.token_urlsafe(32)
            token = backend._recovery_reset_token(ticket)
            _, new_id, expires = backend._create_reset_token(
                conn, ticket["user_id"], lifetime_seconds=RECOVERY_LINK_LIFETIME_SECONDS, token=token)
            conn.execute("UPDATE password_reset_tokens SET used=1 WHERE id=?", (ticket["reset_token_id"],))
            changed = conn.execute("""UPDATE account_recovery_tickets SET
                reset_nonce=?, reset_token_id=?, email_status='sending', email_error='',
                email_attempted_at_epoch=?, email_finished_at_epoch=NULL
                WHERE id=? AND status='approved' AND email_status='sent' AND reset_token_id=?""",
                (ticket["reset_nonce"], new_id, now, ticket_id, expected_token_id))
            if changed.rowcount != 1:
                raise MailOperationError("工单已变化；未续发")
            conn.commit()
            result["expires_at_epoch"] = expires
        finally:
            if conn.in_transaction:
                conn.rollback()
    # Reservation survives crashes and uncertain outcomes. No automatic retries.
    backend._deliver_recovery_notification(
        ticket_id, ticket["contact_email"], "approved", ticket["admin_note"],
        backend._reset_url(token), verified_username=user["username"])
    result.update(status(backend.TURTLE_DB_PATH, ticket_id))
    result["ok"] = result.get("email_status") == "sent"
    return result
