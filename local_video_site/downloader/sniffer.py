from __future__ import annotations

import ast
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from local_video_site.core import config


_MEDIA_EXTENSIONS = (
    ".m3u8",
    ".mpd",
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".mkv",
    ".flv",
)
_SEGMENT_EXTENSIONS = (".ts", ".m4s", ".aac", ".vtt", ".key")
_VIDEO_CONTENT_TYPES = (
    "video/",
    "audio/",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
)
_AD_MARKERS = (
    "advert",
    "/ads/",
    "preroll",
    "midroll",
    "doubleclick",
    "adserver",
    "/creatives/",
    "/roomad/",
    "nsimg.net",
    "popads",
    "trafficjunky",
    "exoclick",
    "juicyads",
    "tsyndicate",
    "wakuhyfeserucu.com",
    "dirtyporxy.com",
)
_QUALITY_WIDTHS = {
    2160: 3840,
    1440: 2560,
    1080: 1920,
    720: 1280,
    480: 854,
    360: 640,
    240: 426,
}
_GENERIC_PLAY_SELECTORS = (
    ".dplayer-play-icon",
    "#kt_player .fp-play",
    ".fp-play",
    ".vjs-big-play-button",
    ".jw-icon-display",
    ".plyr__control--overlaid",
    "[class*='play-button']",
    "[class*='play-btn']",
)
_PLAYER_CONTAINER_SELECTORS = (
    "#dplayerId",
    "#kt_player",
    ".kt_player",
    ".dplayer",
    ".video-js",
    ".jwplayer",
    ".plyr",
)
_IGNORED_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "cookie",
    "host",
    "if-range",
    "proxy-connection",
    "range",
    "transfer-encoding",
}
_PLAYER_CONFIG_URL_RE = re.compile(
    r"""(?P<key>[A-Za-z_][A-Za-z0-9_-]*_url)\s*:\s*"""
    r"""(?P<quote>['"])(?P<url>.*?)(?P=quote)""",
    flags=re.DOTALL,
)


def redact_url(url: str) -> str:
    """Keep a URL usable for diagnostics without exposing query credentials."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return "<invalid-url>"
    secret_keys = {
        "auth",
        "auth_key",
        "authorization",
        "expires",
        "key",
        "sig",
        "signature",
        "token",
    }
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "<redacted>" if key.lower() in secret_keys else value))
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


@dataclass
class MediaCandidate:
    url: str
    source: str
    content_type: str = ""
    resource_type: str = ""
    content_length: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    score: int = 0
    order: int = 0
    matches_page_video: bool = False
    width: int = 0
    height: int = 0
    declared_height: int = 0
    bandwidth: int = 0
    quality_name: str = ""
    audio_url: str = ""
    audio_content_type: str = ""

    @property
    def kind(self) -> str:
        path = urlparse(self.url).path.lower()
        content_type = self.content_type.lower()
        if ".m3u8" in path or "mpegurl" in content_type:
            return "hls"
        if ".mpd" in path or "dash+xml" in content_type:
            return "dash"
        return "file"

    @property
    def evidence(self) -> str:
        values = [self.source or "unknown"]
        if self.resource_type:
            values.append(self.resource_type)
        if self.quality_name:
            values.append("declared={}".format(self.quality_name))
        if self.matches_page_video:
            values.append("page_id_match")
        return ":".join(values)

    @property
    def quality_height(self) -> int:
        if self.declared_height:
            return self.declared_height
        if self.width > 0:
            long_side = max(self.width, self.height)
            for nominal_height in (2160, 1440, 1080, 720, 480, 360, 240):
                nominal_width = _QUALITY_WIDTHS[nominal_height]
                if long_side >= int(nominal_width * 0.95):
                    return nominal_height
        return self.height

    @property
    def quality_label(self) -> str:
        height = self.quality_height
        if not height:
            return "unknown"
        for nominal in (2160, 1440, 1080, 720, 480, 360, 240):
            if height >= nominal:
                return "{}p".format(nominal)
        return "{}p".format(height)


def _path_extension(url: str) -> str:
    path = urlparse(url).path.lower().rstrip("/")
    for extension in _MEDIA_EXTENSIONS + _SEGMENT_EXTENSIONS:
        if path.endswith(extension):
            return extension
    return ""


def classify_media(
    url: str,
    content_type: str = "",
    resource_type: str = "",
    source: str = "network",
    order: int = 0,
    page_url: str = "",
    content_length: int = 0,
    declared_height: int = 0,
    quality_name: str = "",
) -> Optional[MediaCandidate]:
    if (
        not url
        or url.startswith(("blob:", "data:", "about:"))
        or is_advertisement_url(url)
    ):
        return None

    path = urlparse(url).path.lower()
    lowered_type = (content_type or "").lower().split(";", 1)[0].strip()
    extension = _path_extension(url)
    if page_url and media_candidate_key(url) == media_candidate_key(page_url):
        return None
    if (
        not extension
        and lowered_type in {"text/html", "application/xhtml+xml"}
    ):
        return None
    if path.endswith((".html", ".htm")) and not (
        "mpegurl" in lowered_type or "dash+xml" in lowered_type
    ):
        return None
    is_manifest = extension in (".m3u8", ".mpd") or (
        "mpegurl" in lowered_type or "dash+xml" in lowered_type
    )
    manifest_by_content_type = is_manifest and extension not in (".m3u8", ".mpd")
    is_file = extension in _MEDIA_EXTENSIONS
    is_media_type = lowered_type.startswith(_VIDEO_CONTENT_TYPES)
    is_segment = extension in _SEGMENT_EXTENSIONS
    host = (urlparse(url).hostname or "").lower()
    basename = path.rsplit("/", 1)[-1]
    is_bilibili_sr_video = bool(
        extension == ".m4s"
        and (host == "bilivideo.com" or host.endswith(".bilivideo.com"))
        and "/upgcxcode/" in path
        and "_sr2-" in basename
    )
    width = 0
    height = 0
    bandwidth = 0
    if (
        extension in (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".flv")
        or "multi=" in path
    ):
        resolution_matches = list(re.finditer(
            r"(?<![0-9])([1-9][0-9]{2,4})x([1-9][0-9]{2,4})"
            r"(?:_([1-9][0-9]{0,4})k)?",
            path,
            flags=re.IGNORECASE,
        ))
        resolution_match = max(
            resolution_matches,
            key=lambda item: int(item.group(2)),
            default=None,
        )
        if resolution_match:
            width = int(resolution_match.group(1))
            height = int(resolution_match.group(2))
            if resolution_match.group(3):
                bandwidth = int(resolution_match.group(3)) * 1000

    if is_segment and not is_bilibili_sr_video:
        return None
    if is_bilibili_sr_video:
        is_file = True
        declared_height = max(declared_height, 2160)
        quality_name = quality_name or "4K"
    if not (is_manifest or is_file or is_media_type or resource_type == "media"):
        return None

    score = 10
    if extension == ".m3u8":
        score += 100
    elif extension == ".mpd":
        score += 90
    elif manifest_by_content_type:
        # Some CDNs expose a manifest through an extensionless or .mp4-looking
        # URL and identify it only through Content-Type.
        score += 100
    elif extension in (".mp4", ".m4v", ".mov"):
        score += 65
    elif extension in (".webm", ".mkv", ".flv"):
        score += 55
    elif lowered_type.startswith("video/"):
        score += 45
    elif lowered_type.startswith("audio/"):
        score += 20

    if "master" in path or "manifest" in path:
        score += 12
    if path.rstrip("/").endswith("/hls.m3u8"):
        score += 12
    if "_tpl_" in path:
        score += 24
    quality_match = re.search(
        r"[-_/]([0-9]{3,4})p(?:\.[a-z0-9]+)?$",
        path.rstrip("/"),
        re.IGNORECASE,
    )
    if quality_match:
        height = int(quality_match.group(1))
        if height < config.sniffer_minimum_video_height():
            return None
    try:
        declared_height = int(declared_height or 0)
    except (TypeError, ValueError):
        declared_height = 0
    if not declared_height and quality_name:
        declared_match = re.search(
            r"([0-9]{3,4})\s*p",
            str(quality_name),
            flags=re.IGNORECASE,
        )
        if declared_match:
            declared_height = int(declared_match.group(1))
    quality_height = declared_height or height
    minimum_height = config.sniffer_minimum_video_height()
    preferred_max_height = config.sniffer_preferred_max_video_height()
    if quality_height and quality_height < minimum_height:
        return None
    if quality_height:
        quality_weight = 1 if is_manifest else 4
        score += (
            min(quality_height, preferred_max_height) // 100
        ) * quality_weight
        if quality_height == preferred_max_height:
            score += 2
    if bandwidth and extension in (".mp4", ".m4v", ".mov"):
        score += min(bandwidth, 10_000) // 1000
    if source == "network":
        score += 10
    elif source == "player-config":
        score += 100
    if resource_type == "media":
        score += 15
    elif source in ("video-element", "performance"):
        score += 5
    video_id = video_id_from_page(page_url)
    matches_page_video = bool(
        (video_id and video_id in path)
        or urls_share_page_identity(page_url, url)
        or is_bilibili_sr_video
    )
    if matches_page_video:
        score += 60
    if is_manifest:
        pass
    elif (
        content_length
        and content_length < config.sniffer_minimum_video_bytes()
    ):
        return None
    elif content_length >= 10 * 1024 * 1024:
        score += 35
    elif content_length >= 1024 * 1024:
        score += 15

    return MediaCandidate(
        url=url,
        source=source,
        content_type=content_type or "",
        resource_type=resource_type or "",
        content_length=content_length,
        score=score,
        order=order,
        matches_page_video=matches_page_video,
        width=width,
        height=height,
        declared_height=declared_height,
        bandwidth=bandwidth,
        quality_name=str(quality_name or "").strip(),
    )


def is_advertisement_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in _AD_MARKERS)


def video_id_from_page(page_url: str) -> Optional[str]:
    try:
        parts = [part for part in urlparse(page_url or "").path.split("/") if part]
    except ValueError:
        return None
    for part in parts:
        embedded = re.fullmatch(r"video([A-Za-z0-9_-]{3,64})", part)
        if embedded:
            return embedded.group(1)
    for marker in ("video", "videos", "detail"):
        if marker not in parts:
            continue
        index = parts.index(marker)
        if index + 1 < len(parts):
            value = parts[index + 1]
            if re.fullmatch(r"[A-Za-z0-9_-]{3,64}", value):
                return value
    return None


def urls_share_page_identity(page_url: str, media_url: str) -> bool:
    """Match alternate CDN URLs whose numeric video IDs differ."""

    try:
        page_parts = [
            part
            for part in urlparse(page_url or "").path.split("/")
            if part
        ]
        media_path = urlparse(media_url or "").path
    except ValueError:
        return False
    if not page_parts or not media_path:
        return False
    numeric_identity_re = re.compile(r"(?<!\d)-?\d{6,}(?!\d)")
    page_numeric_ids = set(
        numeric_identity_re.findall(
            urlparse(page_url or "").path
        )
    )
    media_numeric_ids = set(
        numeric_identity_re.findall(media_path)
    )
    if (
        page_numeric_ids
        and page_numeric_ids.issubset(media_numeric_ids)
    ):
        return True
    title_slugs = [
        part
        for part in page_parts
        if len(re.sub(r"[^A-Za-z0-9]+", "", part)) >= 12
        and not re.fullmatch(r"video[A-Za-z0-9_-]+", part, re.IGNORECASE)
    ]
    if not title_slugs:
        return False
    page_tokens = {
        token
        for token in re.split(
            r"[^a-z0-9]+",
            max(title_slugs, key=len).lower(),
        )
        if len(token) >= 3
    }
    media_name = media_path.rsplit("/", 1)[-1]
    media_name = re.sub(
        r"[-_](?:[0-9]{3,4}p|4k)(?:\.[^.]+)?$",
        "",
        media_name,
        flags=re.IGNORECASE,
    )
    media_tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", media_name.lower())
        if len(token) >= 3
    }
    if len(page_tokens) < 2 or len(media_tokens) < 2:
        return False
    overlap = len(page_tokens & media_tokens)
    return (
        overlap >= 2
        and overlap / len(page_tokens) >= 0.8
        and overlap / len(media_tokens) >= 0.6
    )


def media_size_from_headers(headers: Dict[str, str]) -> int:
    content_range = headers.get("content-range", "")
    if content_range:
        match = re.search(r"/([0-9]+)\s*$", content_range)
        if match:
            return int(match.group(1))
    try:
        return int(headers.get("content-length", "0") or 0)
    except ValueError:
        return 0


def extract_player_config_sources(
    script_text: str,
    page_url: str,
) -> List[Dict]:
    """Extract current-page player URLs paired with visible quality labels."""

    text = str(script_text or "")
    page_video_id = video_id_from_page(page_url)
    result = []
    seen = set()

    def add_source(raw_url: str, label: str = "", resolution=0) -> None:
        if not raw_url:
            return
        try:
            absolute_url = urljoin(page_url, raw_url)
            parsed = urlparse(absolute_url)
        except ValueError:
            return
        if parsed.scheme not in {"http", "https"}:
            return
        path = parsed.path.lower()
        if not (
            any(path.endswith(extension) for extension in _MEDIA_EXTENSIONS)
            or "/get_file/" in path
            or "/hls/" in path
        ):
            return
        if page_video_id and page_video_id not in absolute_url:
            return
        label = str(label or "").strip()
        try:
            declared_height = int(resolution or 0)
        except (TypeError, ValueError):
            declared_height = 0
        if not declared_height:
            resolution_match = re.search(
                r"(?<![0-9])([0-9]{3,4})(?:p)?(?![0-9])",
                label,
                flags=re.IGNORECASE,
            )
            if resolution_match:
                declared_height = int(resolution_match.group(1))
        identity = (absolute_url, label.casefold())
        if identity in seen:
            return
        seen.add(identity)
        result.append({
            "url": absolute_url,
            "label": label,
            "res": declared_height,
        })

    for match in _PLAYER_CONFIG_URL_RE.finditer(text):
        key = match.group("key")
        raw_url = match.group("url").replace("\\/", "/").strip()
        label_match = re.search(
            r"{}\s*:\s*(['\"])(?P<label>.*?)\1".format(
                re.escape(key + "_text")
            ),
            text,
            flags=re.DOTALL,
        )
        label = label_match.group("label").strip() if label_match else ""
        add_source(raw_url, label)

    decoder = json.JSONDecoder()
    source_list_re = re.compile(
        r"""["']?sources["']?\s*:\s*""",
        flags=re.IGNORECASE,
    )
    for match in source_list_re.finditer(text):
        start = text.find("[", match.end())
        if start < 0:
            continue
        items = None
        try:
            items, _ = decoder.raw_decode(text[start:])
        except (TypeError, ValueError):
            try:
                items = ast.literal_eval(text[start:].split("]", 1)[0] + "]")
            except (SyntaxError, ValueError):
                items = None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_url = (
                item.get("file")
                or item.get("src")
                or item.get("url")
                or ""
            )
            label = (
                item.get("label")
                or item.get("quality")
                or item.get("name")
                or ""
            )
            resolution = (
                item.get("res")
                or item.get("height")
                or item.get("video_height")
                or 0
            )
            add_source(str(raw_url), str(label), resolution)
    return result


def sanitize_request_headers(headers: Dict[str, str]) -> Dict[str, str]:
    result = {}
    for key, value in (headers or {}).items():
        lower = key.lower()
        if lower in _IGNORED_HEADERS or lower.startswith("sec-"):
            continue
        result[key] = value
    return result


def media_candidate_key(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return str(url or "")
    return urlunparse(
        parsed._replace(query="", fragment="")
    ).rstrip("/")


def _has_widevine_protection(item: Dict) -> bool:
    return bool(item.get("widevine_pssh"))


def _bilibili_playview_candidates(
    video_info: Dict,
    page_url: str,
    headers: Dict[str, str],
    start_order: int,
) -> Tuple[List[MediaCandidate], Dict]:
    dash = (video_info or {}).get("dash") or {}
    videos = [
        item for item in dash.get("video") or []
        if isinstance(item, dict)
    ]
    audios = [
        item for item in dash.get("audio") or []
        if isinstance(item, dict)
    ]
    protected_videos = [
        item for item in videos if _has_widevine_protection(item)
    ]
    protected_audios = [
        item for item in audios if _has_widevine_protection(item)
    ]
    clear_audios = [
        item for item in audios if not _has_widevine_protection(item)
    ]
    audio = max(
        clear_audios,
        key=lambda item: int(item.get("bandwidth") or 0),
        default={},
    )
    audio_url = str(
        audio.get("baseUrl")
        or audio.get("base_url")
        or ""
    ).strip()
    candidates = []
    if not audio_url:
        return candidates, {
            "protected_videos": len(protected_videos),
            "protected_audios": len(protected_audios),
            "clear_audio": False,
        }

    from local_video_site.integrations.sites.bilibili import BILIBILI_QUALITY_HEIGHTS

    for item in videos:
        if _has_widevine_protection(item):
            continue
        url = str(
            item.get("baseUrl")
            or item.get("base_url")
            or ""
        ).strip()
        if not url:
            continue
        try:
            format_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            format_id = 0
        declared_height = BILIBILI_QUALITY_HEIGHTS.get(format_id, 0)
        candidates.append(MediaCandidate(
            url=url,
            source="player-config",
            content_type=str(item.get("mimeType") or ""),
            resource_type="media",
            headers=dict(headers),
            score=300,
            order=start_order + len(candidates),
            matches_page_video=True,
            width=int(item.get("width") or 0),
            height=int(item.get("height") or 0),
            declared_height=declared_height,
            bandwidth=int(item.get("bandwidth") or 0),
            quality_name=(
                "{}p".format(declared_height)
                if declared_height
                else ""
            ),
            audio_url=audio_url,
            audio_content_type=str(audio.get("mimeType") or ""),
        ))
    return candidates, {
        "protected_videos": len(protected_videos),
        "protected_audios": len(protected_audios),
        "clear_audio": True,
    }


class MediaSniffer:
    def __init__(self, page, logger: Optional[logging.Logger] = None):
        self.page = page
        self.logger = logger or logging.getLogger(__name__)
        self._candidates: Dict[str, MediaCandidate] = {}
        self._order = 0
        self.advertisements_seen = 0
        self._ignore_bilibili_sr2 = False
        self._bilibili_playview_cache: Optional[Dict] = None
        page.on("response", self._on_response)

    def _record(self, candidate: Optional[MediaCandidate]) -> None:
        if not candidate:
            return
        key = media_candidate_key(candidate.url)
        current = self._candidates.get(key)
        if current is None:
            self._candidates[key] = candidate
            return
        merged = candidate if candidate.score > current.score else current
        if (
            current.source == "network"
            and candidate.source == "player-config"
        ):
            merged.url = current.url
        elif (
            candidate.source == "network"
            and current.source == "player-config"
        ):
            merged.url = candidate.url
        merged.headers = dict(current.headers, **candidate.headers)
        merged.matches_page_video = (
            candidate.matches_page_video or current.matches_page_video
        )
        merged.width = current.width or candidate.width
        merged.height = current.height or candidate.height
        merged.declared_height = (
            candidate.declared_height or current.declared_height
        )
        merged.bandwidth = current.bandwidth or candidate.bandwidth
        merged.quality_name = candidate.quality_name or current.quality_name
        merged.audio_url = candidate.audio_url or current.audio_url
        merged.audio_content_type = (
            candidate.audio_content_type or current.audio_content_type
        )
        merged.score = max(current.score, candidate.score)
        self._candidates[key] = merged

    def _on_response(self, response) -> None:
        try:
            if (
                self._ignore_bilibili_sr2
                and "_sr2-" in urlparse(response.url).path
            ):
                return
            headers = response.headers
            content_type = headers.get("content-type", "")
            request = response.request
            if is_advertisement_url(response.url):
                self.advertisements_seen += 1
                self.logger.info(
                    "Ignored advertisement media request: %s",
                    redact_url(response.url),
                )
                return
            try:
                content_length = media_size_from_headers(headers)
            except (TypeError, ValueError):
                content_length = 0
            candidate = classify_media(
                url=response.url,
                content_type=content_type,
                resource_type=request.resource_type,
                source="network",
                order=self._order,
                page_url=self.page.url,
                content_length=content_length,
            )
            self._order += 1
            if candidate:
                candidate.headers = sanitize_request_headers(request.headers)
                self._record(candidate)
                self.logger.info(
                    "Media request detected: %s (%s)",
                    redact_url(candidate.url),
                    candidate.kind,
                )
        except Exception:
            self.logger.debug("Could not inspect a network response", exc_info=True)

    def _record_bilibili_playview(self, video_info: Dict) -> None:
        request_headers = {
            "Referer": self.page.url,
            "User-Agent": self.page.evaluate("navigator.userAgent"),
        }
        playview_candidates, drm = _bilibili_playview_candidates(
            video_info,
            self.page.url,
            request_headers,
            self._order,
        )
        if drm["protected_videos"]:
            self._ignore_bilibili_sr2 = True
            for key, candidate in list(self._candidates.items()):
                if "_sr2-" in urlparse(candidate.url).path:
                    self._candidates.pop(key, None)
        if drm["protected_videos"] or drm["protected_audios"]:
            self.logger.warning(
                "Bilibili reported Widevine-protected streams "
                "videos=%s audios=%s; using clear streams only",
                drm["protected_videos"],
                drm["protected_audios"],
            )
        if not drm["clear_audio"]:
            self.logger.warning(
                "Bilibili playview returned no clear audio stream; "
                "direct DASH download will fall back to yt-dlp"
            )
        for candidate in playview_candidates:
            self._record(candidate)
            self._order += 1

    def scan_page(self) -> None:
        try:
            dom_urls = self.page.evaluate(
                """
                () => {
                  const media = [];
                  const add = (value, res = 0, label = "") => {
                    if (!value || value.startsWith('blob:') || value.startsWith('data:'))
                      return;
                    try {
                      media.push({
                        url: new URL(value, document.baseURI).href,
                        label,
                        res,
                      });
                    } catch (_) {}
                  };
                  document.querySelectorAll('video').forEach((video) => {
                    const res = Number(video.videoHeight) || 0;
                    add(video.currentSrc, res);
                    add(video.src, res);
                    video.querySelectorAll('source').forEach((source) => {
                      if (!source.src) return;
                      try {
                        media.push({
                          url: new URL(source.src, document.baseURI).href,
                          label: source.label || "",
                          res: Number(source.getAttribute('res')) || 0,
                        });
                      } catch (_) {}
                    });
                  });
                  return media;
                }
                """
            )
            for item in dom_urls or []:
                if isinstance(item, str):
                    item = {"url": item}
                url = item.get("url") if isinstance(item, dict) else ""
                if not url:
                    continue
                self._record(
                    classify_media(
                        url=url,
                        resource_type="media",
                        source="video-element",
                        order=self._order,
                        page_url=self.page.url,
                        declared_height=(
                            item.get("res", 0)
                            if isinstance(item, dict)
                            else 0
                        ),
                        quality_name=(
                            item.get("label", "")
                            if isinstance(item, dict)
                            else ""
                        ),
                    )
                )
                self._order += 1
        except Exception:
            self.logger.debug("DOM media scan failed", exc_info=True)

        try:
            parsed = urlparse(self.page.url)
            host = (parsed.hostname or "").lower()
            if (
                (host == "bilibili.com" or host.endswith(".bilibili.com"))
                and re.match(r"^/bangumi/play/ep\d+", parsed.path)
            ):
                from local_video_site.integrations.sites.bilibili import fetch_bilibili_playview

                cookie_rows = self.page.context.cookies([self.page.url])
                cookies = {
                    str(item.get("name")): str(item.get("value"))
                    for item in cookie_rows
                    if item.get("name")
                }
                if self._bilibili_playview_cache is None:
                    self._bilibili_playview_cache = fetch_bilibili_playview(
                        self.page.url,
                        cookies,
                    )
                    self._record_bilibili_playview(
                        self._bilibili_playview_cache,
                    )
        except Exception:
            self.logger.debug(
                "Bilibili playview media scan failed",
                exc_info=True,
            )

        try:
            performance_urls = self.page.evaluate(
                """
                () => performance.getEntriesByType('resource')
                  .map((entry) => entry.name)
                  .filter((name) =>
                    /(^|\\/)[^/]+\\.(m3u8|mpd|mp4|m4v|mov|webm|mkv|flv)(\\?|$)/i.test(name)
                    || (
                      /upgcxcode\\//i.test(name)
                      && /_sr2-[^/]+\\.m4s(\\?|$)/i.test(name)
                    )
                    || (
                      /upgcxcode\\//i.test(name)
                      && /-1-[0-9]+\\.m4s(\\?|$)/i.test(name)
                    )
                  )
                """
            )
            urls = list(dict.fromkeys(performance_urls or []))
            for url in urls:
                if (
                    self._ignore_bilibili_sr2
                    and "_sr2-" in urlparse(url).path
                ):
                    continue
                candidate = classify_media(
                    url=url,
                    source="performance",
                    order=self._order,
                    page_url=self.page.url,
                )
                self._record(candidate)
                self._order += 1
        except Exception:
            self.logger.debug("Performance media scan failed", exc_info=True)

        try:
            scripts = self.page.evaluate(
                """
                () => Array.from(document.scripts)
                  .map(script => script.textContent || '')
                  .filter(text => (/_url\\s*:/.test(text)
                    || /["']sources["']\\s*:/.test(text))
                    && /(m3u8|mp4|get_file)/i.test(text))
                """
            )
            for script_text in scripts or []:
                for item in extract_player_config_sources(
                    script_text,
                    self.page.url,
                ):
                    self._record(
                        classify_media(
                            url=item["url"],
                            resource_type="media",
                            source="player-config",
                            order=self._order,
                            page_url=self.page.url,
                            declared_height=item.get("res", 0),
                            quality_name=item.get("label", ""),
                        )
                    )
                    self._order += 1
        except Exception:
            self.logger.debug(
                "Player configuration scan failed",
                exc_info=True,
            )

    def activate_video(self, play_selector: Optional[str] = None) -> None:
        try:
            if play_selector:
                self.page.locator(play_selector).first.click(timeout=2500)
        except Exception:
            self.logger.debug("Configured play selector did not become clickable")

        if self._video_count():
            if not self._try_play_videos() and self._click_visible(
                "video",
                description="video element",
            ):
                self.page.wait_for_timeout(500)
                self._try_play_videos()
            return

        for selector in _GENERIC_PLAY_SELECTORS:
            if self._click_visible(selector, description="play control"):
                self.page.wait_for_timeout(500)
                if self._video_count():
                    self._try_play_videos()
                    return

        # Some players such as DPlayer create their <video> element only after
        # the placeholder itself receives a user click.
        for selector in _PLAYER_CONTAINER_SELECTORS:
            if self._click_visible(selector, description="player container"):
                self.page.wait_for_timeout(800)
                if self._click_visible(
                    "{} video".format(selector),
                    description="player video",
                ):
                    self.page.wait_for_timeout(500)
                self._try_play_videos()
                return

    def _video_count(self) -> int:
        try:
            return self.page.locator("video").count()
        except Exception:
            return 0

    def _click_visible(self, selector: str, description: str) -> bool:
        try:
            locator = self.page.locator(selector).first
            if not locator.count() or not locator.is_visible():
                return False
            locator.click(timeout=2500, force=True)
            self.logger.info("Clicked %s: %s", description, selector)
            return True
        except Exception:
            self.logger.debug("Could not click %s: %s", description, selector, exc_info=True)
            return False

    def _try_play_videos(self) -> bool:
        try:
            state = self.page.evaluate(
                """
                async () => {
                  const videos = Array.from(document.querySelectorAll('video'));
                  if (!videos.length) return {count: 0, played: false, ready: 0};
                  let ready = 0;
                  for (const video of videos) {
                    try {
                      video.muted = true;
                  await Promise.race([
                    video.play().catch(() => {}),
                    new Promise((resolve) => setTimeout(resolve, 800)),
                  ]);
                    } catch (_) {}
                    ready = Math.max(ready, video.readyState);
                  }
                  return {count: videos.length, played: true, ready};
                }
                """
            )
            if state and state.get("played"):
                self.logger.info("Muted browser playback started for media discovery")
            return bool(state and state.get("ready", 0) > 0)
        except Exception:
            self.logger.debug("Could not start video playback", exc_info=True)
            return False

    def discover(
        self,
        wait_seconds: float = 12.0,
        settle_seconds: float = 2.0,
        play_selector: Optional[str] = None,
        auto_play: bool = True,
    ) -> List[MediaCandidate]:
        # Network/player candidates can arrive before activation. Use them
        # first so an advertisement-blocked player cannot stall discovery.
        self.scan_page()
        if auto_play and not self.primary_candidates:
            self.activate_video(play_selector=play_selector)

        started_at = time.monotonic()
        page_video_id = video_id_from_page(self.page.url)
        deadline = started_at + wait_seconds
        fallback_deadline = started_at + max(wait_seconds, 30.0)
        next_activation_at = min(deadline, time.monotonic() + 2.0)
        while (
            time.monotonic() < deadline
            or (
                not self.primary_candidates
                and (self.advertisements_seen > 0 or page_video_id)
                and time.monotonic() < fallback_deadline
            )
        ):
            self.scan_page()
            if self.primary_candidates:
                break
            now = time.monotonic()
            if auto_play and now >= next_activation_at:
                self.logger.debug("Retrying delayed player activation")
                self.activate_video(play_selector=play_selector)
                next_activation_at = now + 2.0
            time.sleep(0.5)

        if settle_seconds > 0:
            time.sleep(settle_seconds)
        self.scan_page()
        return self.candidates

    @property
    def candidates(self) -> List[MediaCandidate]:
        return sorted(
            self._candidates.values(),
            key=lambda candidate: (
                candidate.score,
                candidate.quality_height,
                candidate.order,
            ),
            reverse=True,
        )

    @property
    def primary_candidates(self) -> List[MediaCandidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.matches_page_video or candidate.kind in ("hls", "dash")
        ]
