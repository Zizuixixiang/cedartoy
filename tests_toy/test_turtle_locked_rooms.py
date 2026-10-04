"""Run soup integration tests in isolation from the main server's module names."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class TurtleLockedRoomsTests(unittest.TestCase):
    def test_http_sqlite_regressions(self):
        root = Path(__file__).resolve().parents[1]
        python = os.getenv('SOUP_TEST_PYTHON', sys.executable)
        probe = subprocess.run([python, '-c', 'import aiosqlite, jose, passlib, httpx'], capture_output=True)
        if probe.returncode and 'SOUP_TEST_PYTHON' not in os.environ:
            service_python = Path('/opt/cedarstar/venv/bin/python')
            if service_python.exists():
                python = str(service_python)
        result = subprocess.run(
            [python, '-m', 'unittest', 'discover', '-s', 'turtle-soup/backend/tests', '-p', 'test_locked_rooms.py', '-v'],
            cwd=root, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
