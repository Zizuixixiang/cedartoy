"""Bounded warm subprocesses; every child has one immutable player/home."""
import atexit
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import select
import signal
import shutil
import subprocess
import tempfile
import threading
import time

from vendor_cmd_adapter.base import VendorCmdError
from . import storage

PROJECT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


class Worker:
    def __init__(self, player):
        python = os.environ.get("NOWHERE_PYTHON", str(PROJECT / ".venv-nowhere/bin/python"))
        if not Path(python).is_file():
            raise VendorCmdError("乌有乡运行环境未安装：需要 Python >=3.11，请配置 NOWHERE_PYTHON（见 docs/NOWHERE.md）")
        self.home = tempfile.mkdtemp(prefix="cedartoy-nowhere-")
        self.busy = False
        self.used = time.monotonic()
        env = os.environ.copy()
        env.update(NOWHERE_HOME=self.home, NOWHERE_TRAVELER_NAME=player,
                   NOWHERE_SAVE_ROOT=str(storage.ROOT), PYTHONUNBUFFERED="1",
                   MPLCONFIGDIR=str(Path(self.home) / "matplotlib"),
                   PYTHONPATH=str(PROJECT) + os.pathsep + str(PROJECT / "vendor/nowhere"))
        # Upstream stderr is untrusted: drain it without retaining or logging
        # raw bytes. Detailed value-free exception metadata travels over IPC.
        self.proc = subprocess.Popen([python, "-m", "nowhere_adapter.worker", player],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=self.home, env=env,
                                     start_new_session=True)
        self.stderr_thread = threading.Thread(target=self._drain_stderr, args=(player,), daemon=True)
        self.stderr_thread.start()

    def _drain_stderr(self, player):
        reported = False
        while self.proc.stderr.read(4096):
            if not reported:
                LOGGER.warning("Nowhere worker stderr suppressed player=%s (content omitted)", player)
                reported = True

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.stderr_thread.join(timeout=5)
        self.proc.stderr.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def call(self, payload):
        deadline = time.monotonic() + 150
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        fd = self.proc.stdin.fileno()
        os.set_blocking(fd, False)
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                raise VendorCmdError("乌有乡工作进程超时；未提交私人存档")
            written = os.write(fd, data[:65536])
            data = data[written:]
        output = bytearray()
        fd = self.proc.stdout.fileno()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise VendorCmdError("乌有乡动作超时；未提交私人存档")
            block = os.read(fd, 65536)
            if not block:
                raise VendorCmdError("乌有乡工作进程不可用；请检查 Python 版本、依赖和上游版本")
            output.extend(block)
            if len(output) > 48 * 1024 * 1024:
                raise VendorCmdError("乌有乡响应过大")
            if output.endswith(b"\n"):
                return json.loads(output)


class Pool:
    def __init__(self):
        self.condition = threading.Condition()
        self.workers = {}

    @contextmanager
    def acquire(self, player):
        limit = max(1, int(os.environ.get("NOWHERE_MAX_WORKERS", "2")))
        with self.condition:
            deadline = time.monotonic() + 15
            while True:
                worker = self.workers.get(player)
                if worker is not None and not worker.busy:
                    break
                if worker is None:
                    idle = [(p, w) for p, w in self.workers.items() if not w.busy]
                    if len(self.workers) >= limit and idle:
                        key, old = min(idle, key=lambda pair: pair[1].used)
                        old.close()
                        del self.workers[key]
                    if len(self.workers) < limit:
                        worker = self.workers[player] = Worker(player)
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VendorCmdError("乌有乡正在处理其他旅程，请稍后重试")
                self.condition.wait(remaining)
            worker.busy = True
        try:
            yield worker
        except (OSError, ValueError, VendorCmdError) as exc:
            with self.condition:
                worker.close()
                self.workers.pop(player, None)
            if isinstance(exc, VendorCmdError):
                raise
            raise VendorCmdError("乌有乡工作进程通信失败；未提交私人存档") from exc
        finally:
            with self.condition:
                worker.busy = False
                worker.used = time.monotonic()
                self.condition.notify_all()

    def close(self):
        with self.condition:
            for worker in self.workers.values():
                worker.close()
            self.workers.clear()

    def discard(self, player):
        # Caller holds this player's stable storage lock.
        with self.condition:
            worker = self.workers.pop(player, None)
            if worker is not None:
                worker.close()
            self.condition.notify_all()


POOL = Pool()
atexit.register(POOL.close)
