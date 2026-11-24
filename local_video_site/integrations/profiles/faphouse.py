from __future__ import annotations

import re

from .models import SiteProfile


def _metadata_factory(**kwargs):
    from local_video_site.integrations.sites.faphouse import (
        FaphouseMetadataFetcher,
    )

    return FaphouseMetadataFetcher(**kwargs)


def _parser_version() -> int:
    from local_video_site.integrations.sites.faphouse import (
        FAPHOUSE_PARSER_VERSION,
    )

    return FAPHOUSE_PARSER_VERSION


FAPHOUSE_PROFILE = SiteProfile(
    key="faphouse",
    display_name="FapHouse",
    parser_version=_parser_version(),
    source_domain="faphouse.com",
    video_domains=("faphouse.com",),
    video_path_patterns=(re.compile(r"^/videos/[^/?#]+/?$"),),
    media_domains=("faphouse.com", "flixcdn.com"),
    metadata_factory=_metadata_factory,
    category_source_kind="faphouse_category",
    category_fallback_kind="faphouse_meta",
    avatar_hosts=("xhcdn.com", "flixcdn.com"),
    profile_hosts=("faphouse.com",),
    profile_path_prefixes=("/pornstars/", "/models/"),
    metadata_language="en",
    metadata_localized_fallback=True,
    metadata_title_is_canonical=False,
    capabilities={
        "metadata": True,
        "actors": True,
        "categories": True,
        "actor_avatars": True,
    },
)
