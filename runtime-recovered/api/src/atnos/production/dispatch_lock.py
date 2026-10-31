"""OS-released per-order start lock; the execution lease remains authoritative."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

_threads: dict[str, threading.Lock] = {}
_guard = threading.Lock()


@contextmanager
def dispatch_lock(path: Path, timeout: float = 30):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _guard:
        thread_lock = _threads.setdefault(str(path.resolve()), threading.Lock())
    if not thread_lock.acquire(timeout=timeout):
        raise TimeoutError("Production start is already being dispatched")
    stream = None
    locked = False
    try:
        stream = os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), "r+b")
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Production start is already being dispatched")
                time.sleep(.05)
        yield
    finally:
        if stream:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        thread_lock.release()
