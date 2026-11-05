"""Load shared SQLite diagnostics without changing the backend import search path."""
import importlib.util
from pathlib import Path
import sys

_PATH = Path(__file__).resolve().parents[2] / 'cedar_backend' / 'db_observability.py'
_NAME = '_cedar_account_db_observability'
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, _PATH)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
ObservedConnection = sys.modules[_NAME].ObservedConnection


def caller_site():
    frame = sys._getframe(1)
    for _ in range(20):
        if frame is None:
            break
        path = Path(frame.f_code.co_filename)
        if path.name not in ('database.py', 'db_diagnostics.py'):
            return '%s:%s:%s' % (path.name, frame.f_code.co_name, frame.f_lineno)
        frame = frame.f_back
    return 'unknown'
