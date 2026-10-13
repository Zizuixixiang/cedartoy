"""Explicit offline migration; no schema changes, vacuum or backup operations."""
from pathlib import Path
import sqlite3

from .crypto import Cipher, PREFIX, answer_context, digest_context, parse_answers, unavailable, validate_digest


def migrate(db_path, *, apply=False):
    cipher = Cipher()  # Fail before opening the database if configuration is invalid.
    uri = Path(db_path).resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    counts = dict(answer_fields=0, digests=0, encrypted_fields=0, applied=apply)
    try:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        # Accept only the two historical plaintext shapes. Unknown versions and
        # corrupt ciphertext must never be interpreted as legacy plaintext.
        for row in conn.execute("SELECT * FROM deeptalk_rounds").fetchall():
            for role in ("human", "ai"):
                column = role + "_answers"
                value, context = row[column], answer_context(row, role)
                if isinstance(value, str) and value.startswith(PREFIX):
                    parse_answers(cipher.decrypt(value, context))
                    counts["encrypted_fields"] += 1
                    continue
                parse_answers(value)
                counts["answer_fields"] += 1
                if apply:
                    encrypted = cipher.encrypt(value, context)
                    if cipher.decrypt(encrypted, context) != value:
                        raise unavailable()
                    conn.execute(f"UPDATE deeptalk_rounds SET {column}=? WHERE id=?", (encrypted, row["id"]))
        for row in conn.execute("""SELECT r.id,r.human_id,r.ai_id,q.round_id,q.role,q.request_key,q.digest
                FROM deeptalk_requests q LEFT JOIN deeptalk_rounds r ON r.id=q.round_id""").fetchall():
            if row["id"] is None or row["role"] not in {"human", "ai"}:
                raise unavailable()
            value, context = row["digest"], digest_context(row, row["role"], row["request_key"])
            if isinstance(value, str) and value.startswith(PREFIX):
                validate_digest(cipher.decrypt(value, context))
                counts["encrypted_fields"] += 1
                continue
            validate_digest(value)
            counts["digests"] += 1
            if apply:
                encrypted = cipher.encrypt(value, context)
                if cipher.decrypt(encrypted, context) != value:
                    raise unavailable()
                conn.execute("UPDATE deeptalk_requests SET digest=? WHERE round_id=? AND role=? AND request_key=?",
                             (encrypted, row["id"], row["role"], row["request_key"]))
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
