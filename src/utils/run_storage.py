"""Atomic, process-safe updates to a run's audit records."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading


_locks = {}
_guard = threading.Lock()


@contextmanager
def run_lock(run_dir):
    """Serialize registry updates from the application and its MCP process."""
    root = Path(run_dir).resolve()
    with _guard:
        lock = _locks.setdefault(str(root), threading.RLock())
    with lock:
        with open(root / '.audit.lock', 'a+b') as handle:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def write_json_atomic(path, data):
    return write_text_atomic(path, json.dumps(data, indent=2, default=str))
