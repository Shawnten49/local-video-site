from __future__ import annotations

import threading
import time

from fastapi import Request


DELETE_ATTEMPT_LOCK = threading.Lock()
DELETE_ATTEMPTS: dict = {}


def delete_rate_key(request: Request, target_id: int) -> tuple:
    client = request.client.host if request.client else ""
    return client, int(target_id)


def delete_rate_allowed(key: tuple) -> bool:
    cutoff = time.monotonic() - 600
    with DELETE_ATTEMPT_LOCK:
        attempts = [
            stamp for stamp in DELETE_ATTEMPTS.get(key, [])
            if stamp >= cutoff
        ]
        DELETE_ATTEMPTS[key] = attempts
        return len(attempts) < 5


def record_delete_failure(key: tuple) -> None:
    with DELETE_ATTEMPT_LOCK:
        DELETE_ATTEMPTS.setdefault(key, []).append(time.monotonic())


def clear_delete_failures(key: tuple) -> None:
    with DELETE_ATTEMPT_LOCK:
        DELETE_ATTEMPTS.pop(key, None)
