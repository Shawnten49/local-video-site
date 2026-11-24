from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Pattern, Sequence
from urllib.parse import urlparse, urlsplit


MetadataFactory = Callable[..., object]
UrlTransform = Callable[[str], str]
UrlListTransform = Callable[[str], List[str]]


def host_matches(hostname: str, domain: str) -> bool:
    hostname = (hostname or "").lower().rstrip(".")
    domain = (domain or "").lower().strip(".")
    return bool(
        hostname
        and domain
        and (hostname == domain or hostname.endswith("." + domain))
    )


def default_stable_url(value: str) -> str:
    parsed = urlparse(value)
    return parsed._replace(query="", fragment="", params="").geturl()


def default_hls_master_candidates(url: str) -> List[str]:
    parsed = urlparse(url)
    path = parsed.path
    directory = path.rsplit("/", 1)[0] + "/"
    basename = path.rsplit("/", 1)[-1]
    candidates = []
    match = re.search(
        r"-([0-9]{3,4})p\.m3u8$",
        basename,
        flags=re.IGNORECASE,
    )
    if match:
        candidates.append(path[: -len(basename)] + ".m3u8")
    candidates.extend(
        [
            directory + "master.m3u8",
            directory + "hls.m3u8",
        ]
    )
    result = []
    for candidate in candidates:
        if candidate == path:
            continue
        transformed = parsed._replace(path=candidate)
        value = transformed.geturl()
        if value not in result:
            result.append(value)
    return result


@dataclass(frozen=True)
class SiteProfile:
    key: str
    display_name: str
    parser_version: int
    source_domain: str
    video_domains: Sequence[str] = field(default_factory=tuple)
    video_path_patterns: Sequence[Pattern] = field(default_factory=tuple)
    media_domains: Sequence[str] = field(default_factory=tuple)
    metadata_factory: Optional[MetadataFactory] = None
    category_source_kind: str = "title"
    category_fallback_kind: str = "title"
    avatar_hosts: Sequence[str] = field(default_factory=tuple)
    profile_hosts: Sequence[str] = field(default_factory=tuple)
    profile_path_prefixes: Sequence[str] = field(
        default_factory=lambda: ("/pornstars/", "/models/")
    )
    actor_direct_image_selectors: Sequence[str] = field(
        default_factory=tuple
    )
    actor_background_image_selectors: Sequence[str] = field(
        default_factory=tuple
    )
    actor_embedded_image_paths: Sequence[Sequence[str]] = field(
        default_factory=tuple
    )
    hls_stable_url: UrlTransform = default_stable_url
    hls_master_candidates: UrlListTransform = default_hls_master_candidates
    metadata_language: str = ""
    metadata_localized_fallback: bool = True
    metadata_title_is_canonical: bool = True
    capabilities: Dict[str, bool] = field(default_factory=dict)

    @property
    def metadata_supported(self) -> bool:
        return self.metadata_factory is not None

    def matches_video_url(self, value: str) -> bool:
        try:
            parsed = urlsplit((value or "").strip())
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        hostname = (parsed.hostname or "").lower()
        if not any(
            host_matches(hostname, domain)
            for domain in self.video_domains
        ):
            return False
        return (
            not self.video_path_patterns
            or any(pattern.match(parsed.path) for pattern in self.video_path_patterns)
        )

    def matches_host(self, value: str, domains: Sequence[str]) -> bool:
        try:
            hostname = (urlsplit(value or "").hostname or "").lower()
        except ValueError:
            return False
        return any(host_matches(hostname, domain) for domain in domains)

    def matches_media_url(self, value: str) -> bool:
        return self.matches_host(value, self.media_domains)

    def matches_avatar_url(self, value: str) -> bool:
        try:
            parsed = urlsplit(str(value or "").strip())
        except ValueError:
            return False
        return bool(
            parsed.scheme == "https"
            and self.matches_host(value, self.avatar_hosts)
        )

    def matches_profile_url(self, value: str) -> bool:
        try:
            parsed = urlsplit(str(value or "").strip())
        except ValueError:
            return False
        return bool(
            parsed.scheme == "https"
            and self.matches_host(value, self.profile_hosts)
            and any(
                parsed.path.startswith(prefix)
                for prefix in self.profile_path_prefixes
            )
        )

    def create_metadata_fetcher(self, **kwargs):
        if self.metadata_factory is None:
            raise RuntimeError(
                "{} does not provide metadata parsing".format(self.key)
            )
        return self.metadata_factory(**kwargs)
