"""Stable per-save locks, outside directories that deletion/claim can replace."""
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import os
from pathlib import Path
import threading


_held = threading.local()


def save_lock_path(save_dir, *, namespace="state"):
    directory = Path(save_dir).resolve()
    key = hashlib.sha256((directory.name + "\0" + namespace).encode()).hexdigest()
    return directory.parent.parent / ".save-locks" / directory.parent.name / (key + ".lock")


@contextmanager
def save_directory_locks(*save_dirs, namespace="state"):
    """Serialize load/mutate/save and lifecycle changes; nested calls may reuse locks.

    Never unlink these lock files, even after deleting a save. A waiter may still
    hold an open descriptor. Acquire all source/target directories together when
    moving saves. The thread-local ownership is reset after fork.
    """
    if getattr(_held, "pid", None) != os.getpid():
        _held.pid, _held.paths = os.getpid(), {}
    paths = sorted({save_lock_path(path, namespace=namespace) for path in save_dirs})
    with ExitStack() as stack:
        for path in paths:
            if path in _held.paths:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open("a+b"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _held.paths[path] = handle.fileno()
            stack.callback(_held.paths.pop, path)
        # A command runner must inherit these descriptors so that killing its
        # parent does not unlock a save while the runner is still writing it.
        yield tuple(_held.paths[path] for path in paths)
