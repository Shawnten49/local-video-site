from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from local_video_site.integrations.browser import BrowserSession


LOGGER = logging.getLogger(__name__)
FAPHOUSE_HOST = "faphouse.com"
FAPHOUSE_PARSER_VERSION = 2
_CATEGORY_RE = re.compile(r"^/c/([^/?#]+)/videos/?$")
_ACTOR_RE = re.compile(r"^/pornstars/([^/?#]+)/?$")
_META_CATEGORIES_RE = re.compile(
    r"(?:类别|分类|Categories?)\s*[:：]\s*(.+?)(?:[.。]\s*|$)",
    flags=re.IGNORECASE,
)
_META_ACTORS_RE = re.compile(
    r"(?:主演|演员|Starring)\s*[:：]\s*(.+?)(?:[.。]\s*|$)",
    flags=re.IGNORECASE,
)


def _clean_name(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _absolute_url(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return urljoin("https://faphouse.com", raw)
    return raw


def canonical_faphouse_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    return urlunsplit((
        "https",
        "faphouse.com",
        parsed.path,
        parsed.query,
        "",
    ))


def is_faphouse_url(value: str) -> bool:
    from local_video_site.integrations.profiles import resolve_site_profile

    return resolve_site_profile(value).key == "faphouse"


def deduplicate_categories(categories: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for category in categories:
        name = _clean_name(category.get("name"))
        slug = str(category.get("slug") or "").strip() or None
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append({
            "name": name,
            "slug": slug,
            "type": category.get("type") or "category",
            "url": category.get("url"),
        })
    return result


def deduplicate_actors(actors: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for actor in actors:
        name = _clean_name(actor.get("name"))
        slug = str(actor.get("slug") or "").strip() or None
        key = (
            ("slug", slug.casefold())
            if slug
            else ("name", name.casefold())
        )
        if not name or key in seen:
            continue
        seen.add(key)
        item = {
            "name": name,
            "slug": slug,
            "url": actor.get("url"),
        }
        avatar_url = _absolute_url(actor.get("avatar_url"))
        if avatar_url:
            item["avatar_url"] = avatar_url
        result.append(item)
    return result


def categories_from_payload(payload: Dict) -> tuple:
    categories = []
    for link in payload.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = _absolute_url(link.get("url"))
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        hostname = (parsed.hostname or "").lower()
        if not (
            hostname == FAPHOUSE_HOST
            or hostname.endswith("." + FAPHOUSE_HOST)
        ):
            continue
        match = _CATEGORY_RE.match(parsed.path)
        name = _clean_name(link.get("name"))
        if not match or not name:
            continue
        slug = match.group(1)
        categories.append({
            "name": name,
            "slug": slug,
            "type": "category",
            "url": "https://faphouse.com/c/{}/videos".format(slug),
        })
    if not categories:
        description = _clean_name(payload.get("description"))
        match = _META_CATEGORIES_RE.search(description)
        if match:
            for raw_name in re.split(r"[,，]", match.group(1)):
                name = _clean_name(raw_name)
                if name:
                    categories.append({
                        "name": name,
                        "slug": None,
                        "type": "category",
                        "url": None,
                    })
    return (
        deduplicate_categories(categories),
        "dom" if payload.get("links") else "meta",
    )


def actors_from_payload(payload: Dict) -> List[Dict]:
    actors = []
    links_by_name = {}
    for link in payload.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = _absolute_url(link.get("url"))
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        hostname = (parsed.hostname or "").lower()
        if not (
            hostname == FAPHOUSE_HOST
            or hostname.endswith("." + FAPHOUSE_HOST)
        ):
            continue
        name = _clean_name(link.get("name"))
        if name:
            links_by_name[name.casefold()] = {
                "name": name,
                "url": url,
                "avatar_url": link.get("avatar_url"),
            }
        match = _ACTOR_RE.match(parsed.path)
        if not match or not name:
            continue
        slug = match.group(1)
        actors.append({
            "name": name,
            "slug": slug,
            "url": "https://faphouse.com/pornstars/" + slug,
            "avatar_url": _absolute_url(link.get("avatar_url")),
        })
    found_names = {
        _clean_name(actor.get("name")).casefold()
        for actor in actors
    }
    description = _clean_name(payload.get("description"))
    match = _META_ACTORS_RE.search(description)
    if match:
        for raw_name in re.split(r"[,，]", match.group(1)):
            name = _clean_name(raw_name)
            key = name.casefold()
            if not name or key in found_names:
                continue
            linked = links_by_name.get(key) or {}
            url = str(linked.get("url") or "")
            parsed = urlsplit(url) if url else None
            model_match = (
                re.match(r"^/models/([^/?#]+)/?$", parsed.path)
                if parsed is not None
                else None
            )
            actors.append({
                "name": name,
                "slug": model_match.group(1) if model_match else None,
                "url": url or None,
                "avatar_url": linked.get("avatar_url"),
            })
            found_names.add(key)
    return deduplicate_actors(actors)


@dataclass
class FetchResult:
    categories: List[Dict]
    http_status: Optional[int]
    error: Optional[str] = None
    source: str = "dom"
    actors: List[Dict] = field(default_factory=list)
    title: str = ""
    language: str = ""


class FaphouseMetadataFetcher(BrowserSession):
    def __init__(
        self,
        port: int = 9333,
        timeout_ms: int = 30000,
        attempts: int = 3,
        logger: Optional[logging.Logger] = None,
        profile_dir: Optional[Path] = None,
        preferred_language: str = "en",
        allow_localized_fallback: bool = True,
    ) -> None:
        super().__init__(
            port=port,
            timeout_ms=timeout_ms,
            logger=logger,
            profile_dir=profile_dir,
        )
        self.attempts = max(1, attempts)
        self.preferred_language = (
            str(preferred_language or "en").strip().lower()
        )
        self.allow_localized_fallback = bool(
            allow_localized_fallback
        )
        self.page = None

    def __enter__(self):
        super().__enter__()
        self.page = self.new_page(block_images=True)
        self.page.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
        })
        return self

    def _locale_cookies(self) -> List[Dict]:
        cookies = self.context.cookies([
            "https://faphouse.com",
            "https://zh.faphouse.com",
        ])
        return [
            dict(cookie)
            for cookie in cookies
            if str(cookie.get("name") or "") == "locale"
        ]

    def _set_locale(self, value: str) -> None:
        self.context.clear_cookies(
            name="locale",
            domain=".faphouse.com",
        )
        if value:
            self.context.add_cookies([{
                "name": "locale",
                "value": str(value),
                "domain": ".faphouse.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }])

    def _restore_locale(self, cookies: List[Dict]) -> None:
        self.context.clear_cookies(
            name="locale",
            domain=".faphouse.com",
        )
        if not cookies:
            return
        restore = []
        for cookie in cookies:
            value = {
                key: cookie[key]
                for key in (
                    "name",
                    "value",
                    "domain",
                    "path",
                    "expires",
                    "httpOnly",
                    "secure",
                    "sameSite",
                )
                if key in cookie
            }
            restore.append(value)
        if restore:
            self.context.add_cookies(restore)

    def _fetch_once(self, url: str) -> FetchResult:
        response = self.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        status = response.status if response else None
        self.page.wait_for_timeout(1200)
        payload = self.page.evaluate(
            """
            () => {
              const links = Array.from(
                document.querySelectorAll(
                  '[data-el="RelatedTags"] a[href]'
                )
              ).map(anchor => ({
                name: (anchor.textContent || '').trim(),
                url: anchor.href,
                avatar_url:
                  anchor.querySelector('img[src]')?.src || ''
              }));
              const description =
                document.querySelector(
                  'meta[name="description"]'
                )?.content || '';
              const title = (
                document.querySelector('h1')?.textContent
                || document.querySelector(
                  'meta[property="og:title"]'
                )?.content
                || document.title
                || ''
              ).trim();
              return {
                links,
                description,
                title,
                language: (
                  document.documentElement.lang || ''
                ).toLowerCase(),
                url: location.href
              };
            }
            """
        )
        categories, source = categories_from_payload(payload)
        actors = actors_from_payload(payload)
        language = _clean_name(payload.get("language")).lower()
        if categories or actors:
            return FetchResult(
                categories=categories,
                actors=actors,
                http_status=status,
                source=source,
                title=_clean_name(payload.get("title")),
                language=language,
            )
        return FetchResult(
            [],
            status,
            "FapHouse page returned no actors or categories",
            source=source,
            title=_clean_name(payload.get("title")),
            language=language,
        )

    def fetch(self, url: str) -> FetchResult:
        if not is_faphouse_url(url):
            return FetchResult([], None, "Not a FapHouse video URL")
        original_locale = self._locale_cookies()
        fallback_locale = ""
        if original_locale:
            fallback_locale = str(
                original_locale[0].get("value") or ""
            )
        if not fallback_locale:
            host = (urlsplit(url).hostname or "").lower()
            if (
                host.endswith(".faphouse.com")
                and host != "faphouse.com"
            ):
                fallback_locale = host.split(".", 1)[0]
        last_result = FetchResult([], None, "FapHouse metadata fetch failed")
        attempts = [canonical_faphouse_url(url)]
        if self.allow_localized_fallback:
            localized = str(url or "").strip()
            if localized not in attempts:
                attempts.append(localized)
        try:
            for index, target in enumerate(attempts):
                locale = (
                    self.preferred_language
                    if index == 0
                    else fallback_locale
                )
                self._set_locale(locale)
                for attempt in range(1, self.attempts + 1):
                    try:
                        result = self._fetch_once(target)
                        last_result = result
                        if result.categories or result.actors:
                            return result
                    except (PlaywrightError, RuntimeError) as exc:
                        last_result = FetchResult(
                            [],
                            None,
                            str(exc),
                            source="dom",
                        )
                    if attempt < self.attempts:
                        time.sleep(float(attempt))
            return last_result
        finally:
            self._restore_locale(original_locale)
