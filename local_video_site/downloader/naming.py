from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional
from urllib.parse import urlparse

from local_video_site.core import config
from local_video_site.integrations.profiles import list_site_profiles


SITE_NAMES = {
    "bilibili.com": "哔哩哔哩",
    "iqiyi.com": "爱奇艺",
    "v.qq.com": "腾讯视频",
    "youku.com": "优酷",
    "mgtv.com": "芒果TV",
    "sohu.com": "搜狐视频",
    "ixigua.com": "西瓜视频",
    "douyin.com": "抖音",
    "kuaishou.com": "快手",
    "youtube.com": "YouTube",
    "porntrex.com": "Porntrex.com",
    "acfun.cn": "AcFun",
    "miguvideo.com": "咪咕视频",
    "1905.com": "1905电影网",
}

_SITE_PATTERNS = (
    r"腾讯视频",
    r"爱奇艺",
    r"iQIYI",
    r"优酷",
    r"Youku",
    r"哔哩哔哩",
    r"[Bb]ilibili",
    r"芒果TV",
    r"搜狐视频",
    r"西瓜视频",
    r"YouTube",
    r"YouTube Music",
    r"Porntrex(?:\.com)?",
    r"抖音",
    r"快手",
    r"AcFun",
    r"咪咕视频",
    r"1905电影网",
)

_EPISODE_PATTERNS = (
    re.compile(r"(?i)\bS\d{1,2}\s*E\d{1,4}\b"),
    re.compile(r"(?i)\bEP(?:ISODE)?\s*[._-]?\s*\d{1,4}\b"),
    re.compile(r"第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[集话回期篇]"),
    re.compile(r"更新至\s*第?\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[集话回期篇]?"),
    re.compile(r"(?<!\d)[0-9]{1,4}\s*[集话期](?!\d)"),
)

_GENERIC_NOISE = (
    r"在线观看",
    r"免费观看",
    r"在线播放",
    r"高清完整版",
    r"完整版",
    r"超清",
    r"蓝光",
    r"高清",
    r"1080P",
    r"720P",
    r"4K",
    r"全集",
)

_ELLIPSIS = "…"
_SEPARATOR_RE = re.compile(r"\s*(?:[_|｜]|\s+-\s+)\s*")
_SITE_LIKE_SUFFIX_RE = re.compile(
    r"\s*(?:[-_|｜]\s*)[^\-_|｜]{1,24}"
    r"(?:视频|影视|影院|TV|网|平台|播放器)\s*$",
    flags=re.IGNORECASE,
)
_KNOWN_SITE_SUFFIXES = (
    re.compile(
        r"\s+18AV(?:在線|在线).*$",
        flags=re.IGNORECASE,
    ),
)
_BOILERPLATE_MARKERS = (
    "在线观看",
    "免費影片",
    "免费影片",
    "免費AV",
    "在线看",
    "線上a片",
    "线上a片",
    "高清完整版",
    "免费av",
)
_OPENING_BRACKETS = {
    "[": "]",
    "(": ")",
    "（": "）",
    "【": "】",
    "《": "》",
}
_CLOSING_BRACKETS = {
    closing: opening
    for opening, closing in _OPENING_BRACKETS.items()
}
_TRUNCATION_BOUNDARIES = frozenset(
    " \t\r\n,，;；:：|｜/／-—–)]）】》"
)


def site_name_for_url(url: str) -> Optional[str]:
    for profile in list_site_profiles():
        if profile.matches_host(url, profile.video_domains):
            return profile.display_name
    hostname = (urlparse(url).hostname or "").lower()
    for domain, name in SITE_NAMES.items():
        if hostname == domain or hostname.endswith("." + domain):
            return name
    return None


def _strip_known_sites(value: str) -> str:
    result = value
    profile_patterns = tuple(
        re.escape(profile.display_name) + r"(?:\.com)?"
        for profile in list_site_profiles()
    )
    for pattern in profile_patterns + _SITE_PATTERNS:
        result = re.sub(
            rf"(?:\s*(?:[-_|｜:：/]\s*)?){pattern}"
            rf"(?:\s+(?:best|hd|porn|videos?)"
            rf"(?:\s*(?:\.{{3}}|…))?)?\s*$",
            "",
            result,
            flags=re.IGNORECASE,
        )
    return result


def _strip_generic_noise(value: str) -> str:
    result = value
    for pattern in _GENERIC_NOISE:
        result = re.sub(
            rf"(?:[\s\-_|｜【】\[\]()（）]*){pattern}"
            rf"(?:[\s\-_|｜【】\[\]()（）]*)",
            " ",
            result,
            flags=re.IGNORECASE,
        )
    result = re.sub(
        r"[【\[(（]\s*(?:高清|超清|蓝光|完整版|在线观看)\s*[】\])）]",
        " ",
        result,
        flags=re.IGNORECASE,
    )
    return result


def _strip_edge_noise(value: str) -> str:
    """Remove punctuation at an edge without splitting balanced brackets."""

    result = str(value or "").strip()
    while result:
        character = result[0]
        if character.isspace():
            result = result[1:].lstrip()
            continue
        if character in _OPENING_BRACKETS:
            closing = _OPENING_BRACKETS[character]
            closing_index = result.find(closing, 1)
            if 0 < closing_index < len(result) - 1:
                break
        if character not in "_-|｜:：,，.。·•[]()（）【】《》":
            break
        result = result[1:].lstrip()

    while result:
        character = result[-1]
        if character.isspace():
            result = result[:-1].rstrip()
            continue
        if character in _CLOSING_BRACKETS:
            opening = _CLOSING_BRACKETS[character]
            if opening in result[:-1]:
                break
        if character not in "_-|｜:：,，.。·•[]()（）【】《》":
            break
        result = result[:-1].rstrip()
    return result


def _truncate_utf8(
    value: str,
    max_bytes: Optional[int] = None,
) -> str:
    max_bytes = (
        config.max_filename_bytes()
        if max_bytes is None
        else max_bytes
    )
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def truncate_readable_filename(value: str) -> str:
    """Trim long names at a readable boundary while keeping a byte safety cap."""

    if (
        len(value) <= config.readable_filename_chars()
        and len(value.encode("utf-8")) <= config.max_filename_bytes()
    ):
        return value

    candidate = value[:config.readable_filename_chars()]
    suffix = _ELLIPSIS
    while (
        candidate
        and len((candidate + suffix).encode("utf-8"))
        > config.max_filename_bytes()
    ):
        candidate = candidate[:-1]

    minimum_boundary = max(1, int(len(candidate) * 0.6))
    boundary = max(
        (
            index
            for index in range(len(candidate) - 1, minimum_boundary - 1, -1)
            if candidate[index] in _TRUNCATION_BOUNDARIES
        ),
        default=-1,
    )
    if boundary >= minimum_boundary:
        candidate = candidate[:boundary]
    candidate = candidate.rstrip(
        " \t\r\n,，;；:：|｜/／-—–()]）】》"
    )
    while (
        candidate
        and unicodedata.combining(candidate[-1])
    ):
        candidate = candidate[:-1]
    return (candidate or value[:1]).rstrip() + suffix


def truncate_at_hash(value: str) -> str:
    """Remove hashtag text and everything after the first hash character."""

    return (value or "").split("#", 1)[0].strip()


def safe_filename_component(value: str, fallback: str = "video") -> str:
    """Return a single safe filename component without path separators."""

    result = unicodedata.normalize("NFKC", str(value or ""))
    result = truncate_at_hash(result)
    result = result.replace("/", "／").replace("\\", "＼")
    result = re.sub(r"[\x00-\x1f<>:\"|?*]", " ", result)
    result = re.sub(r"\s+", " ", result).strip(" .")
    result = truncate_readable_filename(result)
    result = _truncate_utf8(result)
    return result or fallback


def clean_display_title(
    raw_title: str,
    source_url: Optional[str] = None,
    fallback: str = "video",
) -> str:
    """Return a readable title without changing its semantic punctuation."""

    return clean_series_title(
        raw_title,
        source_url=source_url,
        fallback=fallback,
    )


def clean_series_title(
    raw_title: str,
    source_url: Optional[str] = None,
    fallback: str = "video",
) -> str:
    """Turn a first-episode page title into a safe, reusable series name."""

    value = unicodedata.normalize("NFKC", raw_title or "")
    value = truncate_at_hash(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = _strip_known_sites(value)
    for pattern in _KNOWN_SITE_SUFFIXES:
        value = pattern.sub("", value)
    value = _SITE_LIKE_SUFFIX_RE.sub("", value)

    episode_match = next(
        (match for pattern in _EPISODE_PATTERNS for match in pattern.finditer(value)),
        None,
    )
    if episode_match:
        before_episode = value[: episode_match.start()].strip()
        if before_episode:
            value = before_episode
        else:
            value = value[: episode_match.start()] + value[episode_match.end() :]

    value = _strip_generic_noise(value)

    site_name = site_name_for_url(source_url) if source_url else None
    if site_name:
        value = re.sub(
            rf"(?:\s*(?:[-_|｜:：/]\s*)?){re.escape(site_name)}\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        )

    # Streaming sites commonly put the real title in the first segment.
    segments = [part.strip() for part in _SEPARATOR_RE.split(value) if part.strip()]
    if len(segments) > 1 and episode_match:
        value = segments[0]

    value = _strip_edge_noise(value)
    value = re.sub(r"\s+", " ", value).strip()
    return safe_filename_component(value, fallback=fallback)


def _title_candidate_score(value: str) -> tuple:
    lowered = value.casefold()
    boilerplate_penalty = sum(
        1 for marker in _BOILERPLATE_MARKERS if marker.casefold() in lowered
    )
    overlong_penalty = max(0, len(value) - 120)
    return (
        -boilerplate_penalty,
        -overlong_penalty,
        len(value),
    )


def choose_best_title(
    candidates: Iterable[str],
    source_url: Optional[str] = None,
) -> str:
    cleaned = [
        clean_series_title(candidate, source_url=source_url)
        for candidate in candidates
        if candidate and candidate.strip()
    ]
    cleaned = [value for value in cleaned if value != "video"]
    if not cleaned:
        return "video"
    return max(cleaned, key=_title_candidate_score)
