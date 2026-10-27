#!/usr/bin/env python3
"""Run browser checks against disposable loopback accounts/services, never production."""
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_toy.test_autochess_web import Fixture

if __name__ == '__main__':
    with Fixture() as fixture:
        result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs'))], cwd=Path(__file__).resolve().parents[1], env={
            **os.environ, 'AUTOCHESS_BROWSER_ORIGIN': fixture.origin,
            'AUTOCHESS_BROWSER_TOKEN': fixture.tokens['101'],
        })
        raise SystemExit(result.returncode)
