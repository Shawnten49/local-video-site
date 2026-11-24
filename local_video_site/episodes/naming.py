from __future__ import annotations

import re
import unicodedata
from pathlib import Path


_SEASON_RE = re.compile(r"第\s*([0-9]+|[一二三四五六七八九十百两]+)\s*季")
_GENERIC_EPISODE_TITLE_RE = re.compile(
    r"^(?:"
    r"正片|正剧|tv|正片\s*\d*|"
    r"原版|中文版|国语版|粤语版|日语版|英语版|"
    r"预告|花絮|pv|episode|"
    r"ep\s*\d+|episode\s*\d+|"
    r"第?\s*\d+\s*[集话期]?"
    r")$",
    flags=re.IGNORECASE,
)
_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if "百" in value:
        left, right = value.split("百", 1)
        hundreds = _DIGITS.get(left, 1) * 100
        return hundreds + (_chinese_number(right) if right else 0)
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _DIGITS.get(left, 1) if left else 1
        ones = _DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _DIGITS.get(value, 0)


def season_number_from_label(value: str, fallback: int = 1) -> int:
    text = unicodedata.normalize("NFKC", str(value or ""))
    match = _SEASON_RE.search(text)
    if not match:
        return max(1, int(fallback or 1))
    parsed = _chinese_number(match.group(1))
    return parsed if parsed > 0 else max(1, int(fallback or 1))


def meaningful_episode_title(value: str) -> str:
    collapsed = " ".join(str(value or "").split())
    if not collapsed:
        return ""
    normalized = unicodedata.normalize("NFKC", collapsed)
    if _GENERIC_EPISODE_TITLE_RE.fullmatch(normalized):
        return ""
    return collapsed


def is_generic_episode_title(value: str) -> bool:
    normalized = unicodedata.normalize(
        "NFKC",
        " ".join(str(value or "").split()),
    )
    return bool(
        normalized
        and _GENERIC_EPISODE_TITLE_RE.fullmatch(normalized)
    )


def episode_display_title(
    value: str = "",
    relative_path: str = "",
    video_path: str = "",
) -> str:
    title = meaningful_episode_title(value)
    if title:
        return title
    for path_value in (relative_path, video_path):
        if not path_value:
            continue
        title = meaningful_episode_title(Path(str(path_value)).stem)
        if title:
            return title
    return ""
