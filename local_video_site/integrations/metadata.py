from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from local_video_site.integrations.profiles import get_site_profile
from local_video_site.integrations.profiles.registry import metadata_profile_for_url


@dataclass
class SiteMetadataResult:
    categories: List[Dict]
    actors: List[Dict]
    http_status: Optional[int]
    source: str
    error: Optional[str] = None
    provider: str = ""
    title: str = ""
    language: str = ""


def metadata_provider_for_url(value: str) -> Optional[str]:
    profile = metadata_profile_for_url(value)
    return profile.key if profile is not None else None


def metadata_parser_version(provider: str) -> int:
    return get_site_profile(provider).parser_version


def metadata_language(provider: str) -> str:
    return get_site_profile(provider).metadata_language


def fetch_site_metadata(
    url: str,
    logger: Optional[logging.Logger] = None,
) -> SiteMetadataResult:
    profile = metadata_profile_for_url(url)
    if profile is not None:
        factory_kwargs = {"logger": logger}
        if profile.metadata_language:
            factory_kwargs.update({
                "preferred_language": profile.metadata_language,
                "allow_localized_fallback": (
                    profile.metadata_localized_fallback
                ),
            })
        with profile.create_metadata_fetcher(**factory_kwargs) as fetcher:
            result = fetcher.fetch(url)
        return SiteMetadataResult(
            categories=result.categories,
            actors=result.actors,
            http_status=result.http_status,
            source=result.source,
            error=result.error,
            provider=profile.key,
            title=getattr(result, "title", ""),
            language=getattr(result, "language", ""),
        )
    return SiteMetadataResult(
        categories=[],
        actors=[],
        http_status=None,
        source="unsupported",
        error="不支持的元数据网站",
    )
