from __future__ import annotations

from pathlib import Path
from typing import Optional


MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".ts": "video/mp2t",
    ".avi": "video/x-msvideo",
    ".rmvb": "video/vnd.rn-realvideo",
}


def media_type_for_path(path) -> Optional[str]:
    return MEDIA_TYPES.get(Path(path).suffix.lower())
