from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from local_video_site.core import config


class FFmpegBusyError(RuntimeError):
    """Raised when an interactive FFmpeg operation cannot obtain a slot."""


class _RuntimeState:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.semaphore = threading.BoundedSemaphore(limit)
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()


_STATE_LOCK = threading.Lock()
_STATE: Optional[_RuntimeState] = None


def _state() -> _RuntimeState:
    global _STATE
    limit = config.ffmpeg_concurrency()
    with _STATE_LOCK:
        if _STATE is None or _STATE.limit != limit:
            _STATE = _RuntimeState(limit)
        return _STATE


@contextmanager
def ffmpeg_slot(
    operation: str,
    wait_seconds: Optional[float] = None,
) -> Iterator[None]:
    del operation
    state = _state()
    if wait_seconds is None:
        acquired = state.semaphore.acquire()
    else:
        acquired = state.semaphore.acquire(
            timeout=max(0.0, float(wait_seconds))
        )
    if not acquired:
        raise FFmpegBusyError("FFmpeg 并发已达到上限，请稍后重试")
    with state.lock:
        state.active += 1
        state.peak = max(state.peak, state.active)
    try:
        yield
    finally:
        with state.lock:
            state.active = max(0, state.active - 1)
        state.semaphore.release()


def ffmpeg_runtime_status() -> Dict:
    state = _state()
    with state.lock:
        return {
            "limit": int(state.limit),
            "active": int(state.active),
            "peak": int(state.peak),
        }


def reset_ffmpeg_runtime_for_tests() -> None:
    global _STATE
    with _STATE_LOCK:
        _STATE = None
