"""Versioned field encryption, confined to Deep Talk persistence.

No plaintext fallback. Legacy data is accepted only by the offline migration.
"""
import base64
import json
import os
import re
import stat

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .catalog import DeeptalkError

PREFIX = "dtenc:v1:"
KEY_ENV = "DEEPTALK_SAVE_KEY_FILE"
STORAGE_ERROR = "Deep Talk 存档暂不可用，请联系管理员检查密钥或迁移状态"


def unavailable():
    return DeeptalkError(STORAGE_ERROR, 503)


def answer_context(row, role):
    return ["deeptalk_rounds", row["id"], row["human_id"], row["ai_id"],
            role + "_answers", row[role + "_revision"]]


def digest_context(row, role, request_key):
    return ["deeptalk_requests", row["id"], row["human_id"], row["ai_id"],
            role, request_key, "digest"]


def parse_answers(value):
    try:
        answers = json.loads(value)
        if not isinstance(answers, dict):
            raise ValueError()
        for qid, answer in answers.items():
            if not isinstance(qid, str) or not isinstance(answer, dict):
                raise ValueError()
            if answer == {"skipped": True} and type(answer["skipped"]) is bool:
                continue
            if (set(answer) != {"text", "skipped"} or answer["skipped"] is not False
                    or not isinstance(answer["text"], str) or not answer["text"].strip()
                    or len(answer["text"]) > 8000):
                raise ValueError()
        return answers
    except (ValueError, TypeError):
        raise unavailable() from None


def validate_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise unavailable()
    return value


class Cipher:
    def __init__(self):
        # The environment holds a path, never the secret. Read the opened inode
        # to avoid a stat/open race; reject symlinks, special files and loose modes.
        try:
            path = os.environ[KEY_ENV]
            if not os.path.isabs(path):
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise ValueError()
                key = source.read(33)
            if len(key) != 32:
                raise ValueError()
            self._aes = AESGCM(key)
        except (KeyError, OSError, ValueError):
            raise unavailable() from None

    @staticmethod
    def _aad(context):
        return json.dumps([PREFIX, *context], ensure_ascii=True, separators=(",", ":")).encode()

    def encrypt(self, plaintext, context):
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), self._aad(context))
        return PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value, context):
        try:
            if not isinstance(value, str) or not value.startswith(PREFIX):
                raise ValueError()
            payload = base64.b64decode(value[len(PREFIX):], validate=True)
            if len(payload) < 28:
                raise ValueError()
            return self._aes.decrypt(payload[:12], payload[12:], self._aad(context)).decode("utf-8")
        except (InvalidTag, ValueError, TypeError):
            raise unavailable() from None
