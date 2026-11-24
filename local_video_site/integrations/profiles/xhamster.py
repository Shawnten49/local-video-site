from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import SiteProfile, default_hls_master_candidates


def _metadata_factory(**kwargs):
    from local_video_site.integrations.sites.xhamster import XHamsterCategoryFetcher

    return XHamsterCategoryFetcher(**kwargs)


def _parser_version() -> int:
    from local_video_site import database

    return database.CATEGORY_PARSER_VERSION


def stable_hls_url(value: str) -> str:
    parsed = urlparse(value)
    marker = "/media="
    if marker in parsed.path:
        return "media=" + parsed.path.split(marker, 1)[1]
    return parsed._replace(query="", fragment="", params="").geturl()


def hls_master_candidates(url: str) -> list:
    parsed = urlparse(url)
    candidates = []
    match = re.search(
        r"-([0-9]{3,4})p\.m3u8$",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if match:
        candidates.append(parsed.path[: match.start()] + ".m3u8")
    basename_match = re.search(
        r"(?P<quality>(?:[0-9]{3,4}x[0-9]{3,4}|[0-9]{3,4}p))"
        r"(?P<suffix>\..+)$",
        parsed.path.rsplit("/", 1)[-1],
        flags=re.IGNORECASE,
    )
    if basename_match:
        directory = parsed.path.rsplit("/", 1)[0] + "/"
        candidates.append(
            directory + "_TPL_" + basename_match.group("suffix")
        )
    directory = parsed.path.rsplit("/", 1)[0] + "/"
    candidates.extend(
        [
            directory + "_TPL_.av1.mp4.m3u8",
            directory + "_TPL_.mp4.m3u8",
            directory + "master.m3u8",
            directory + "hls.m3u8",
        ]
    )
    result = []
    for path in candidates:
        if path == parsed.path:
            continue
        candidate = parsed._replace(path=path).geturl()
        if candidate not in result:
            result.append(candidate)
    return result or default_hls_master_candidates(url)


XHAMSTER_PROFILE = SiteProfile(
    key="xhamster",
    display_name="xHamster",
    parser_version=_parser_version(),
    source_domain="xhamster.com",
    video_domains=("xhamster.com",),
    video_path_patterns=(re.compile(r"^/videos/"),),
    media_domains=("xhcdn.com", "xhamster.com"),
    metadata_factory=_metadata_factory,
    category_source_kind="xhamster",
    category_fallback_kind="xhamster_fallback",
    avatar_hosts=("xhcdn.com",),
    profile_hosts=("xhamster.com",),
    profile_path_prefixes=("/pornstars/",),
    actor_direct_image_selectors=(
        ".profile-pic img[src]",
    ),
    actor_background_image_selectors=(
        ".landing-info__logo-image",
    ),
    actor_embedded_image_paths=(
        ("infoComponent", "pornstarTop"),
    ),
    hls_stable_url=stable_hls_url,
    hls_master_candidates=hls_master_candidates,
    capabilities={
        "metadata": True,
        "actors": True,
        "categories": True,
        "actor_avatars": True,
        "hls_master_inference": True,
    },
)
