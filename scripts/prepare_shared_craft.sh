#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
craft_python="${SHARED_CRAFT_PYTHON:-python3.11}"
if [ -z "${SHARED_CRAFT_PYTHON:-}" ] && [ -x .nowhere-runtime/python/bin/python3.11 ]; then
    craft_python=.nowhere-runtime/python/bin/python3.11
fi
"$craft_python" -c 'import sys; assert sys.version_info >= (3,11), "Python >=3.11 required"'
"$craft_python" -m venv .venv-shared-craft
.venv-shared-craft/bin/python -m pip install -r shared_craft_adapter/requirements.txt
.venv-shared-craft/bin/python -c 'from shared_craft_adapter.service import verify_upstream; verify_upstream()'
