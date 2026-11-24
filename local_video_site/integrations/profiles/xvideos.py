from __future__ import annotations

import re

from .models import SiteProfile, default_hls_master_candidates


def _metadata_factory(**kwargs):
    from local_video_site.integrations.sites.xvideos import XvideosMetadataFetcher

    return XvideosMetadataFetcher(**kwargs)


def _parser_version() -> int:
    from local_video_site.integrations.sites.xvideos import XVIDEOS_PARSER_VERSION

    return XVIDEOS_PARSER_VERSION


XVIDEOS_PROFILE = SiteProfile(
    key="xvideos",
    display_name="xVideos",
    parser_version=_parser_version(),
    source_domain="xvideos.com",
    # xvideos.red is an official alternate domain and serves the same
    # /video.<id>/... pages, so metadata extraction can use the existing
    # xVideos parser without a separate provider.
    video_domains=("xvideos.com", "xvideos.red"),
    video_path_patterns=(re.compile(r"^/video\.[^/]+(?:/|$)"),),
    media_domains=("xvideos-cdn.com", "xvideos.com"),
    metadata_factory=_metadata_factory,
    category_source_kind="xvideos_tag",
    category_fallback_kind="xvideos_fallback",
    avatar_hosts=("xvideos-cdn.com",),
    profile_hosts=("xvideos.com",),
    profile_path_prefixes=("/pornstars/", "/models/"),
    actor_direct_image_selectors=(
        ".profile-pic img[src]",
    ),
    hls_master_candidates=default_hls_master_candidates,
    capabilities={
        "metadata": True,
        "actors": True,
        "categories": True,
        "actor_avatars": True,
    },
)
