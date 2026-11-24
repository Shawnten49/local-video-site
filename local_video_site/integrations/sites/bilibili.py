from __future__ import annotations

import logging
import re
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

import httpx


LOGGER = logging.getLogger(__name__)
BILIBILI_PARSER_VERSION = 1
EPISODE_PATH_RE = re.compile(r"^/bangumi/play/ep(?P<ep_id>\d+)")
SEASON_PATH_RE = re.compile(r"^/bangumi/play/ss(?P<season_id>\d+)")
SEASON_API = "https://api.bilibili.com/pgc/view/web/season"
PLAYVIEW_API = "https://api.bilibili.com/ogv/player/playview"
_WBI_MIXIN_KEY_ENC_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
BILIBILI_QUALITY_HEIGHTS = {
    16: 360,
    32: 480,
    64: 720,
    74: 720,
    80: 1080,
    112: 1080,
    116: 1080,
    120: 2160,
}
RELEASE_DATE_RE = re.compile(
    r'"release_date"\s*:\s*"(?P<year>\d{4})-\d{2}-\d{2}"'
)


@dataclass
class BilibiliFetchResult:
    categories: List[Dict] = field(default_factory=list)
    actors: List[Dict] = field(default_factory=list)
    http_status: Optional[int] = None
    error: Optional[str] = None
    source: str = "api"
    title: str = ""


def episode_id_from_url(value: str) -> Optional[str]:
    publication = publication_id_from_url(value)
    if publication and publication[0] == "ep_id":
        return publication[1]
    return None


def publication_id_from_url(value: str) -> Optional[Tuple[str, str]]:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
        return None
    match = EPISODE_PATH_RE.match(parsed.path)
    if match:
        return "ep_id", match.group("ep_id")
    match = SEASON_PATH_RE.match(parsed.path)
    if match:
        return "season_id", match.group("season_id")
    return None


def _wbi_key(img_url: str, sub_url: str) -> str:
    raw = "".join(
        value.rsplit("/", 1)[-1].split(".", 1)[0]
        for value in (img_url, sub_url)
    )
    return "".join(raw[index] for index in _WBI_MIXIN_KEY_ENC_TABLE)[:32]


def fetch_bilibili_playview(
    url: str,
    cookies: Dict[str, str],
    timeout: float = 20.0,
) -> Dict:
    publication = publication_id_from_url(url)
    if not publication or publication[0] != "ep_id":
        raise ValueError("Bilibili playview requires an episode URL")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Referer": url,
    }
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
        cookies=cookies,
    ) as client:
        nav = client.get(
            "https://api.bilibili.com/x/web-interface/nav"
        )
        nav.raise_for_status()
        wbi = ((nav.json().get("data") or {}).get("wbi_img") or {})
        key = _wbi_key(
            str(wbi.get("img_url") or ""),
            str(wbi.get("sub_url") or ""),
        )
        params = {
            "x-bili-locale-json": json.dumps(
                {
                    "c_locale": {"language": "zh-Hans"},
                    "always_translate": False,
                },
                separators=(",", ":"),
            ),
            "biz_type": "ogv",
            "scene": "normal",
            "from_client": "BROWSER",
            "ep_id": publication[1],
            "qn": "120",
            "fnver": "0",
            "fnval": "4048",
            "drm_tech_type": "2",
            "version_name": "4.10.12",
            "app_id": "100",
            "web_location": "666.25",
            "x-bili-device-req-json": json.dumps(
                {
                    "platform": "web",
                    "device": "pc",
                    "spmid": "666.25",
                    "mobi_app": "web_cn",
                },
                separators=(",", ":"),
            ),
            "wts": str(round(time.time())),
        }
        params = {
            key_name: "".join(
                char
                for char in str(value)
                if char not in "!'()*"
            )
            for key_name, value in sorted(params.items())
        }
        params["w_rid"] = hashlib.md5(
            (urlencode(params) + key).encode()
        ).hexdigest()
        response = client.get(PLAYVIEW_API, params=params)
        response.raise_for_status()
        payload = response.json()
    if payload.get("code") not in (0, "0"):
        raise RuntimeError(
            "Bilibili playview failed: {}".format(
                payload.get("message") or payload.get("code")
            )
        )
    result = payload.get("result") or payload.get("data") or {}
    video_info = result.get("video_info") or result
    if not isinstance(video_info, dict):
        raise RuntimeError("Bilibili playview returned no video info")
    return video_info


def _clean_names(values) -> List[str]:
    result = []
    seen = set()
    if isinstance(values, str):
        values = values.splitlines()
    for value in values or []:
        name = " ".join(str(value or "").replace("\u3000", " ").split())
        normalized = name.casefold()
        if not name or normalized in seen:
            continue
        seen.add(normalized)
        result.append(name)
    return result


def categories_from_result(
    result: Dict,
    release_year: Optional[str] = None,
) -> List[Dict]:
    categories = []
    seen = set()
    for name in _clean_names(result.get("styles") or []):
        normalized = name.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        categories.append({
            "name": name,
            "slug": name,
            "type": "bilibili_style",
            "url": None,
        })
    if release_year and release_year.casefold() not in seen:
        categories.append({
            "name": str(release_year),
            "slug": str(release_year),
            "type": "bilibili_year",
            "url": None,
        })
    return categories


def actors_from_result(result: Dict) -> List[Dict]:
    return [
        {
            "name": name,
            "slug": None,
            "url": None,
        }
        for name in _clean_names(result.get("actors") or "")
    ]


def metadata_from_payload(
    payload: Dict,
    http_status: Optional[int] = 200,
    release_year: Optional[str] = None,
) -> BilibiliFetchResult:
    if payload.get("code") not in (0, "0"):
        return BilibiliFetchResult(
            http_status=http_status,
            error="Bilibili 接口返回错误：{}".format(
                payload.get("message") or payload.get("code")
            ),
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        return BilibiliFetchResult(
            http_status=http_status,
            error="Bilibili 未返回番剧详情",
        )
    categories = categories_from_result(result, release_year)
    actors = actors_from_result(result)
    if not categories and not actors:
        return BilibiliFetchResult(
            http_status=http_status,
            error="Bilibili 页面没有返回演员或分类信息",
            title=str(result.get("title") or ""),
        )
    return BilibiliFetchResult(
        categories=categories,
        actors=actors,
        http_status=http_status,
        source="api",
        title=str(result.get("title") or ""),
    )


class BilibiliMetadataFetcher:
    def __init__(
        self,
        timeout_ms: int = 30000,
        logger: Optional[logging.Logger] = None,
        **_kwargs,
    ) -> None:
        self.timeout = max(1.0, float(timeout_ms) / 1000.0)
        self.logger = logger or LOGGER

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def close(self) -> None:
        return None

    def fetch(self, url: str) -> BilibiliFetchResult:
        publication = publication_id_from_url(url)
        if not publication:
            return BilibiliFetchResult(
                error="不是 Bilibili 番剧剧集或季地址",
            )
        parameter, identifier = publication
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        }
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = client.get(
                    SEASON_API,
                    params={parameter: identifier},
                )
                response.raise_for_status()
                payload = response.json()
                release_year = None
                try:
                    page_response = client.get(url)
                    page_response.raise_for_status()
                    match = RELEASE_DATE_RE.search(page_response.text)
                    if match:
                        release_year = match.group("year")
                except (httpx.HTTPError, ValueError):
                    pass
        except (httpx.HTTPError, ValueError) as exc:
            self.logger.warning(
                "Bilibili metadata request failed %s=%s error=%s",
                parameter,
                identifier,
                exc,
            )
            return BilibiliFetchResult(error=str(exc))
        return metadata_from_payload(
            payload,
            response.status_code,
            release_year=release_year,
        )
