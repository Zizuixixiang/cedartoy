"""Directory replacement and interrupted runner regressions; /tmp only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from save_locks import save_lock_path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = r'''
import json, os, sys, time
from pathlib import Path
p = json.load(sys.stdin)
directory = Path(p['save_dir'])
control = Path(p['extra']['control'])
old = json.loads((directory / 'state.json').read_text())
(control / 'loaded').write_text(str(os.getpid()))
deadline = time.monotonic() + 15
while not (control / 'release').exists():
    if time.monotonic() > deadline:
        raise RuntimeError('test runner release timeout')
    time.sleep(.01)
old['value'] += 1
(directory / 'state.json').write_text(json.dumps(old))
(control / 'written').touch()
'''
WORKER = r'''
import json, shutil, sys
from pathlib import Path
from save_locks import save_directory_locks
from vendor_cmd_adapter import base
root, mode, runner = sys.argv[1:]
root = Path(root)
base.SAVE_ROOT = root / 'saves'
directory = base.SAVE_ROOT / 'test' / 'guest:lockaudit'
if mode == 'writer':
    base.VendorCmdGame('test', '.', runner).run('guest:lockaudit', 'write', extra={'control': str(root)})
else:
    (root / 'delete-started').touch()
    from cedar_backend.save_management import _delete_vendor_save_dir
    _delete_vendor_save_dir('test', 'guest:lockaudit', DIRECTORY_VENDOR_GAMES=('test',),
                            VENDOR_SAVE_ROOT=base.SAVE_ROOT, nowhere_storage=None, shutil=shutil)
    with save_directory_locks(directory):
        # Nested import must retain the same inode and must not deadlock.
        base.import_json_saves('test', 'guest:lockaudit', {'value': 100}, {'state.json': 'state.json'})
        result = base.export_json_saves('test', 'guest:lockaudit', {'state.json': 'state.json'})
        assert json.loads(result) == {'value': 100}
    (root / 'deleted').touch()
'''


class SaveDirectoryLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cedar-stable-lock-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'saves/test/guest:lockaudit'
        self.directory.mkdir(parents=True)
        (self.directory / 'state.json').write_text('{"value": 1}')
        self.processes = []
        self.runner_pid = None
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        (self.root / 'release').touch()
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        # The deliberately orphaned runner has bounded execution; terminate it
        # only if it has not completed the controlled write.
        if self.runner_pid and not (self.root / 'written').exists():
            try:
                os.kill(self.runner_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def start(self, mode):
        process = subprocess.Popen(
            [sys.executable, '-c', WORKER, str(self.root), mode, RUNNER],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        )
        self.processes.append(process)
        return process

    def wait_file(self, name):
        deadline = time.monotonic() + 8
        while not (self.root / name).exists():
            if time.monotonic() > deadline:
                self.fail(f'timed out waiting for {name}')
            time.sleep(.01)

    def check_replace_during_write(self, kill_parent):
        writer = self.start('writer')
        self.wait_file('loaded')
        self.runner_pid = int((self.root / 'loaded').read_text())
        lock_path = save_lock_path(self.directory)
        inode = lock_path.stat().st_ino
        if kill_parent:
            writer.kill()
            writer.communicate(timeout=5)
        deleter = self.start('delete')
        self.wait_file('delete-started')
        time.sleep(.15)
        self.assertIsNone(deleter.poll(), 'replacement bypassed the running writer')
        self.assertFalse((self.root / 'deleted').exists())
        (self.root / 'release').touch()
        out, err = deleter.communicate(timeout=8)
        self.assertEqual(deleter.returncode, 0, (out, err))
        if not kill_parent:
            out, err = writer.communicate(timeout=8)
            self.assertEqual(writer.returncode, 0, (out, err))
        self.assertTrue((self.root / 'written').exists())
        self.assertEqual(json.loads((self.directory / 'state.json').read_text()), {'value': 100})
        self.assertEqual(lock_path.stat().st_ino, inode)

    def test_delete_then_import_waits_for_running_command(self):
        self.check_replace_during_write(kill_parent=False)

    def test_killed_parent_does_not_unlock_live_runner(self):
        self.check_replace_during_write(kill_parent=True)
