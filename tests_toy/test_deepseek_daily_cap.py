"""Run the wallet guard and affected scheduler regressions in isolation."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class DeepSeekDailyCapTests(unittest.TestCase):
    def test_provider_and_scheduler_regressions(self):
        root = Path(__file__).resolve().parents[1]
        python = os.getenv("SOUP_TEST_PYTHON", sys.executable)
        probe = subprocess.run([python, "-c", "import httpx, fastapi"], capture_output=True)
        if probe.returncode and "SOUP_TEST_PYTHON" not in os.environ:
            service_python = Path("/opt/cedarstar/venv/bin/python")
            if service_python.exists():
                python = str(service_python)
        for pattern in ("test_provider_daily_calls.py", "test_judge_resilience.py"):
            with self.subTest(pattern=pattern):
                result = subprocess.run(
                    [python, "-m", "unittest", "discover", "-s", "turtle-soup/backend/tests", "-p", pattern, "-v"],
                    cwd=root, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
