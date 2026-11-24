from __future__ import annotations

import re

from .models import SiteProfile


def _metadata_factory(**kwargs):
    from local_video_site.integrations.sites.bilibili import BilibiliMetadataFetcher

    return BilibiliMetadataFetcher(**kwargs)


def _parser_version() -> int:
    from local_video_site.integrations.sites.bilibili import BILIBILI_PARSER_VERSION

    return BILIBILI_PARSER_VERSION


BILIBILI_PROFILE = SiteProfile(
    key="bilibili",
    display_name="Bilibili",
    parser_version=_parser_version(),
    source_domain="bilibili.com",
    video_domains=("bilibili.com",),
    video_path_patterns=(
        re.compile(r"^/bangumi/play/(?:ep|ss)\d+"),
    ),
    metadata_factory=_metadata_factory,
    category_source_kind="bilibili_style",
    category_fallback_kind="bilibili_style",
    capabilities={
        "metadata": True,
        "actors": True,
        "categories": True,
        "episode_catalog": True,
    },
)
