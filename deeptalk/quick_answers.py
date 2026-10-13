"""Imported, human-only suggestions; never part of a round snapshot."""
import hashlib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from deeptalk.catalog import TOPICS

DATA_DIR = Path(__file__).with_name("quick_answers")


@lru_cache(maxsize=10)
def load_options(topic_id):
    if topic_id not in TOPICS:
        return {}
    try:
        data = json.loads((DATA_DIR / f"{topic_id}.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected question mapping")
        for item in data.values():
            if not isinstance(item, dict):
                raise ValueError("Expected quick answer mapping")
            options = item["options"]
            free_text_only = item.get("free_text_only", False)
            if (item["mode"] not in ("single", "multiple")
                    or not isinstance(free_text_only, bool)
                    or not isinstance(item["question_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["question_sha256"])
                    or not isinstance(options, list)
                    or (len(options) != 0 if free_text_only else not 2 <= len(options) <= 5)
                    or any(not isinstance(s, str) or not s.strip() or s != s.strip() for s in options)
                    or len(set(options)) != len(options)):
                raise ValueError("Invalid quick answers")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning("Quick answers unavailable for %s", topic_id)
        return {}


def attach_to_view(view):
    """Use the authorized snapshot text, including for pre-feature rounds.

    A changed or missing historical question falls back to free text. Only the
    browser view calls this; Store, MCP, guides and polling remain unchanged.
    """
    choices = load_options(view["topic_id"])
    for question in view.get("questions", []):
        item = choices.get(str(question["id"]))
        digest = hashlib.sha256(question["text"].encode("utf-8")).hexdigest()
        if (item and not item.get("free_text_only", False) and item["options"]
                and item["question_sha256"] == digest):
            question["quick_answers"] = {"mode": item["mode"], "options": list(item["options"])}
    return view
