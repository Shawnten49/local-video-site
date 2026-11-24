from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807


@dataclass(frozen=True)
class ParsedSearchQuery:
    raw: str
    is_id: bool
    id_value: Optional[int]


def parse_search_query(value: object) -> ParsedSearchQuery:
    """Parse pure ASCII digits as an exact integer ID search."""

    raw = str(value or "").strip()
    if not raw or not raw.isascii() or not raw.isdecimal():
        return ParsedSearchQuery(raw=raw, is_id=False, id_value=None)
    normalized_digits = raw.lstrip("0") or "0"
    if (
        len(normalized_digits) > 19
        or int(normalized_digits) > MAX_SQLITE_INTEGER
    ):
        return ParsedSearchQuery(raw=raw, is_id=True, id_value=None)
    return ParsedSearchQuery(
        raw=raw,
        is_id=True,
        id_value=int(normalized_digits),
    )
