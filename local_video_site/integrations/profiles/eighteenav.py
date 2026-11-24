from __future__ import annotations

import re

from .models import SiteProfile


def _metadata_factory(**kwargs):
    from local_video_site.integrations.sites.eighteenav import (
        EighteenAvMetadataFetcher,
    )

    return EighteenAvMetadataFetcher(**kwargs)


def _parser_version() -> int:
    from local_video_site.integrations.sites.eighteenav import (
        EIGHTEENAV_PARSER_VERSION,
    )

    return EIGHTEENAV_PARSER_VERSION


EIGHTEENAV_PROFILE = SiteProfile(
    key="18av",
    display_name="18AV",
    parser_version=_parser_version(),
    source_domain="18av.mm-cg.com",
    video_domains=("18av.mm-cg.com",),
    video_path_patterns=(
        re.compile(
            r"^/(?:zh/)?(?:uncensored_content|censored_content|"
            r"reducing-mosaic_content|chinese_content)/[^/]+/[^/]+\.html$"
        ),
    ),
    media_domains=("18av.mm-cg.com", "streamfastpro.com"),
    metadata_factory=_metadata_factory,
    category_source_kind="18av_category",
    category_fallback_kind="18av_title",
    avatar_hosts=("pics.dmm.co.jp",),
    profile_hosts=("18av.mm-cg.com",),
    profile_path_prefixes=(
        "/zh/uncensored_avperformer/",
        "/zh/censored_avperformer/",
        "/zh/chinese_avperformer/",
        "/zh/reducing-mosaic-uncensored_avperformer/",
        "/zh/reducing-mosaic-censored_avperformer/",
        "/zh/reducing-mosaic-chinese_avperformer/",
    ),
    capabilities={
        "metadata": True,
        "actors": True,
        "categories": True,
        "actor_avatars": True,
    },
)
