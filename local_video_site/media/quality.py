from __future__ import annotations

import re
from typing import Optional


STANDARD_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240)
QUALITY_RE = re.compile(r"^\s*([0-9]{3,4})\s*p?\s*$", re.IGNORECASE)
QUALITY_SEARCH_RE = re.compile(r"([0-9]{3,4})\s*p", re.IGNORECASE)
STANDARD_WIDTHS = {
    2160: 3840,
    1440: 2560,
    1080: 1920,
    720: 1280,
    480: 854,
    360: 640,
    240: 426,
}


def _positive_integer(value) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _quality_from_resolution(
    width: Optional[int],
    height: Optional[int],
) -> Optional[str]:
    if width is not None:
        long_side = max(
            value for value in (width, height) if value is not None
        )
        for nominal_height in STANDARD_HEIGHTS:
            nominal_width = STANDARD_WIDTHS.get(nominal_height)
            if nominal_width and long_side >= int(nominal_width * 0.95):
                return "{}p".format(nominal_height)
    if height is not None:
        for nominal_height in STANDARD_HEIGHTS:
            if height >= nominal_height:
                return "{}p".format(nominal_height)
        return "{}p".format(height)
    return None


def normalise_quality_label(
    value=None,
    width=None,
    height=None,
) -> Optional[str]:
    """Return a compact, stable quality label for UI and persistence."""

    resolved_height = _positive_integer(height)
    resolved_width = _positive_integer(width)
    resolution_quality = _quality_from_resolution(
        resolved_width,
        resolved_height,
    )
    if resolution_quality:
        return resolution_quality

    raw = str(value or "").strip().lower()
    if "4k" in raw or "2160p" in raw:
        return "2160p"
    match = QUALITY_RE.match(raw)
    if match:
        return "{}p".format(int(match.group(1)))
    match = QUALITY_SEARCH_RE.search(raw)
    if match:
        return "{}p".format(int(match.group(1)))
    return None


def resolution_label(width=None, height=None) -> Optional[str]:
    resolved_width = _positive_integer(width)
    resolved_height = _positive_integer(height)
    if resolved_width is None or resolved_height is None:
        return None
    return "{}×{}".format(resolved_width, resolved_height)
