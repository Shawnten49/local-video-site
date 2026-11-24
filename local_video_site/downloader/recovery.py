from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlparse


MEDIA_REFRESH_MARKER = "MEDIA_REFRESH_REQUIRED"
EXPIRY_STATUSES = (401, 403, 410)


class MediaRefreshRequired(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(
            "{}: {}".format(MEDIA_REFRESH_MARKER, reason)
        )


def extract_media_expiry(value: str) -> Optional[datetime]:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return None

    query_candidates = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {
            "deadline",
            "expires",
            "expiry",
            "exp",
            "valid_until",
            "validuntil",
            "e",
        } and re.fullmatch(r"1[0-9]{9}", item):
            query_candidates.append(int(item))

    # Explicit query parameters are authoritative. Some media URLs contain
    # long identifiers in their path that happen to look like Unix timestamps.
    # For example, Bilibili paths may include media id 1249670979 while the
    # real expiry is supplied as deadline=1795115701.
    if query_candidates:
        candidates = query_candidates
    else:
        candidates = [
            int(segment)
            for segment in parsed.path.split("/")
            if re.fullmatch(r"1[0-9]{9}", segment)
        ]
    if not candidates:
        return None
    timestamp = max(candidates)
    try:
        return datetime.fromtimestamp(timestamp).astimezone()
    except (OSError, OverflowError, ValueError):
        return None


def media_identity(
    url: str,
    quality: str = "",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> str:
    try:
        path = unquote(urlparse(str(url or "")).path)
    except ValueError:
        path = ""
    basename = path.rsplit("/", 1)[-1].lower()
    parts = [
        basename,
        str(quality or "").lower(),
        str(width or ""),
        str(height or ""),
    ]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def error_indicates_expiry(error) -> bool:
    text = str(error or "").upper()
    if MEDIA_REFRESH_MARKER in text:
        return True
    return any(
        marker in text
        for marker in (
            "HTTP 401",
            "HTTP 403",
            "HTTP 410",
            "HTTP ERROR 401",
            "HTTP ERROR 403",
            "HTTP ERROR 410",
            "410: GONE",
            "403: FORBIDDEN",
            "URL EXPIRED",
            "SIGNATURE EXPIRED",
        )
    )


def partial_size(output_dir: Path, basename: str) -> int:
    total = 0
    prefix = basename + "."
    try:
        candidates = output_dir.iterdir()
    except OSError:
        return 0
    for path in candidates:
        if not path.is_file() or not path.name.startswith(prefix):
            continue
        if ".part" not in path.name and not path.name.endswith(".ytdl"):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total
