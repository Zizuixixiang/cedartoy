"""Import support for Tarot server tests in a partial CedarToy worktree.

The Tarot HTTP boundary lives in the monolithic ``server`` module, whose
unrelated eco and ci-yu-wu adapters require separate upstream checkouts.  CI
and isolated feature worktrees do not always contain those checkouts.  Stub
only those missing adapter imports so the tests still execute the real server,
Tarot adapter, HTTP handlers, SQLite store and auth boundary under test.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _unavailable_handler(*_args, **_kwargs):
    raise AssertionError("unrelated game adapter invoked by isolated Tarot tests")


def install_missing_optional_game_stubs() -> None:
    missing = []
    if not (ROOT / "vendor" / "ci-yu-wu" / "engine.py").is_file():
        missing.append("ciyuwu_adapter.handler")
    if not (ROOT / "eco" / "engine.py").is_file():
        missing.append("eco_adapter.handler")

    for module_name in missing:
        if module_name in sys.modules:
            continue
        module = types.ModuleType(module_name)
        module.handle_mcp = _unavailable_handler
        module.JsonRpcError = RuntimeError
        sys.modules[module_name] = module
