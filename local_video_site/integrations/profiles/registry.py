from __future__ import annotations

from typing import Optional, Tuple

from .bilibili import BILIBILI_PROFILE
from .eighteenav import EIGHTEENAV_PROFILE
from .faphouse import FAPHOUSE_PROFILE
from .models import SiteProfile
from .xhamster import XHAMSTER_PROFILE
from .xvideos import XVIDEOS_PROFILE


GENERIC_PROFILE = SiteProfile(
    key="generic",
    display_name="Generic",
    parser_version=1,
    source_domain="",
    capabilities={},
)

_PROFILES: Tuple[SiteProfile, ...] = (
    BILIBILI_PROFILE,
    EIGHTEENAV_PROFILE,
    FAPHOUSE_PROFILE,
    XVIDEOS_PROFILE,
    XHAMSTER_PROFILE,
)
_PROFILE_BY_KEY = {profile.key: profile for profile in _PROFILES}


def list_site_profiles() -> Tuple[SiteProfile, ...]:
    return _PROFILES


def get_site_profile(key: str) -> SiteProfile:
    return _PROFILE_BY_KEY.get(str(key or ""), GENERIC_PROFILE)


def resolve_site_profile(
    value: str,
    require_metadata: bool = False,
) -> SiteProfile:
    for profile in _PROFILES:
        if profile.matches_video_url(value):
            if not require_metadata or profile.metadata_supported:
                return profile
    return GENERIC_PROFILE


def metadata_profile_for_url(value: str) -> Optional[SiteProfile]:
    profile = resolve_site_profile(value, require_metadata=True)
    return None if profile is GENERIC_PROFILE else profile


def resolve_media_profile(value: str) -> SiteProfile:
    for profile in _PROFILES:
        if profile.matches_media_url(value):
            return profile
    return GENERIC_PROFILE


def resolve_profile_profile(value: str) -> SiteProfile:
    for profile in _PROFILES:
        if profile.matches_profile_url(value):
            return profile
    return GENERIC_PROFILE
