from __future__ import annotations

import os
import threading
from typing import Optional, Tuple

from .registry import SETTING_DEFINITIONS
from .service import SettingsService


_LOCK = threading.Lock()
_SERVICE: Optional[SettingsService] = None
_FINGERPRINT: Optional[Tuple] = None


def _environment_fingerprint() -> Tuple:
    return tuple(
        (item.env_name, os.environ.get(item.env_name))
        for item in SETTING_DEFINITIONS
        if item.env_name
    )


def _context_fingerprint() -> Tuple:
    from local_video_site.core import config

    return (str(config.DATABASE_PATH), _environment_fingerprint())


def get_runtime_settings() -> SettingsService:
    global _SERVICE, _FINGERPRINT
    fingerprint = _context_fingerprint()
    with _LOCK:
        if _SERVICE is None or _FINGERPRINT != fingerprint:
            _SERVICE = SettingsService()
            _FINGERPRINT = fingerprint
    return _SERVICE


def reset_runtime_settings() -> None:
    global _SERVICE, _FINGERPRINT
    with _LOCK:
        _SERVICE = None
        _FINGERPRINT = None
