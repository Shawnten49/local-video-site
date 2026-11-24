from __future__ import annotations

import fcntl
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from local_video_site.core import config


class MigrationLockTimeout(RuntimeError):
    pass


_THREAD_LOCK = threading.Lock()


@contextmanager
def migration_lock(timeout_seconds: float = 60.0) -> Iterator[None]:
    timeout = max(0.1, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    if not _THREAD_LOCK.acquire(timeout=timeout):
        raise MigrationLockTimeout("Timed out waiting for migration lock")
    handle = None
    try:
        config.ensure_data_directories()
        lock_path = config.DATA_DIR / "schema-migrations.lock"
        handle = lock_path.open("a+")
        while True:
            try:
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise MigrationLockTimeout(
                        "Timed out waiting for schema migration lock"
                    ) from exc
                time.sleep(0.05)
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        _THREAD_LOCK.release()
