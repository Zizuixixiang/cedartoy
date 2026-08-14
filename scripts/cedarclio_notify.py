#!/usr/bin/env python3
"""Send one system notification through CedarClio's configured notification API."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


CEDARCLIO_ROOT = Path("/opt/cedarclio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", choices=("alert", "warning", "info"), default="info")
    parser.add_argument("--text", required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, str(CEDARCLIO_ROOT))
    try:
        from bot.telegram_notify import send_telegram_main_user_text

        asyncio.run(send_telegram_main_user_text(args.text, level=args.level))
    except Exception:
        logging.exception("CedarClio notification failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
