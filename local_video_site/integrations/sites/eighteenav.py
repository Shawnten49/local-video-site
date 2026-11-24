from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, unquote, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from local_video_site.integrations.browser import BrowserSession


LOGGER = logging.getLogger(__name__)
EIGHTEENAV_HOST = "18av.mm-cg.com"
EIGHTEENAV_PARSER_VERSION = 2
_ACTOR_PATH_RE = re.compile(
    r"^/(?:zh/)?([a-z0-9-]+)_avperformer/"
    r"([0-9]+)/(.+?)/[0-9]+\.html$",
    flags=re.IGNORECASE,
)
_CATEGORY_PATH_RE = re.compile(
    r"^/(?:zh/)?([a-z0-9-]+)_category/"
    r"([0-9]+)/(.+?)/[0-9]+\.html$",
    flags=re.IGNORECASE,
)


def _clean(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _absolute_url(value: Optional[str], base: str = "https://18av.mm-cg.com") -> str:
    raw = str(value or "").strip()
    return urljoin(base, raw) if raw else ""


def _host_matches(value: str) -> bool:
    try:
        hostname = (urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return (
        hostname == EIGHTEENAV_HOST
        or hostname.endswith("." + EIGHTEENAV_HOST)
    )


def deduplicate_actors(actors: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for item in actors:
        name = _clean(item.get("name"))
        external_id = item.get("external_id")
        key = (
            ("id", str(external_id))
            if external_id is not None
            else ("name", name.casefold())
        )
        if not name or key in seen:
            continue
        seen.add(key)
        actor = {
            "name": name,
            "slug": _clean(item.get("slug")) or None,
            "url": item.get("url"),
        }
        if external_id is not None:
            actor["external_id"] = int(external_id)
        avatar_url = _absolute_url(item.get("avatar_url"))
        if (
            avatar_url
            and "nowprinting" not in avatar_url.casefold()
        ):
            actor["avatar_url"] = avatar_url
        result.append(actor)
    return result


def deduplicate_categories(categories: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for item in categories:
        name = _clean(item.get("name"))
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append({
            "name": name,
            "slug": _clean(item.get("slug")) or None,
            "type": item.get("type") or "category",
            "url": item.get("url"),
        })
    return result


def actors_from_payload(payload: Dict) -> List[Dict]:
    actors = []
    for link in payload.get("actorLinks") or []:
        if not isinstance(link, dict):
            continue
        href = _absolute_url(link.get("href"))
        if not _host_matches(href):
            continue
        try:
            path = urlsplit(href).path
        except ValueError:
            continue
        match = _ACTOR_PATH_RE.match(path)
        if not match:
            continue
        profile_kind, external_id, raw_slug = match.groups()
        slug = _clean(unquote(raw_slug))
        name = _clean(link.get("name")) or slug
        if not name:
            continue
        actors.append({
            "name": name,
            "slug": slug,
            "external_id": int(external_id),
            "url": (
                "https://{}/zh/{}_avperformer/{}/{}/{}.html".format(
                    EIGHTEENAV_HOST,
                    profile_kind.lower(),
                    int(external_id),
                    quote(slug, safe=""),
                    1,
                )
            ),
            "avatar_url": link.get("avatar_url"),
        })
    return deduplicate_actors(actors)


def categories_from_payload(payload: Dict) -> tuple:
    categories = []
    for link in payload.get("categoryLinks") or []:
        if not isinstance(link, dict):
            continue
        href = _absolute_url(link.get("href"))
        if not _host_matches(href):
            continue
        try:
            path = urlsplit(href).path
        except ValueError:
            continue
        match = _CATEGORY_PATH_RE.match(path)
        if not match:
            continue
        category_kind, category_id, raw_slug = match.groups()
        slug = _clean(unquote(raw_slug))
        name = _clean(link.get("name")) or slug
        if not name:
            continue
        categories.append({
            "name": name,
            "slug": slug,
            "type": "category",
            "url": (
                "https://{}/zh/{}_category/{}/{}/{}.html".format(
                    EIGHTEENAV_HOST,
                    category_kind.lower(),
                    int(category_id),
                    quote(slug, safe=""),
                    1,
                )
            ),
        })
    source = "dom" if categories else "jsonld"
    if not categories:
        keywords = payload.get("jsonLdKeywords") or ""
        if isinstance(keywords, list):
            values = keywords
        else:
            values = str(keywords).split(",")
        for raw_name in values:
            name = _clean(raw_name)
            if name:
                categories.append({
                    "name": name,
                    "slug": None,
                    "type": "tag",
                    "url": None,
                })
    return deduplicate_categories(categories), source


@dataclass
class EighteenAvFetchResult:
    categories: List[Dict]
    http_status: Optional[int]
    error: Optional[str] = None
    source: str = "dom"
    actors: List[Dict] = field(default_factory=list)
    title: str = ""
    language: str = ""


class EighteenAvMetadataFetcher(BrowserSession):
    def __init__(
        self,
        port: int = 9333,
        timeout_ms: int = 30000,
        attempts: int = 3,
        logger: Optional[logging.Logger] = None,
        profile_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            port=port,
            timeout_ms=timeout_ms,
            logger=logger,
            profile_dir=profile_dir,
        )
        self.attempts = max(1, attempts)
        self.page = None

    def __enter__(self):
        super().__enter__()
        self.page = self.new_page(block_images=False)
        return self

    def fetch(self, url: str) -> EighteenAvFetchResult:
        if not _host_matches(url):
            return EighteenAvFetchResult([], None, "Not an 18AV video URL")
        last_status = None
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                last_status = response.status if response else None
                try:
                    self.page.wait_for_selector(
                        ".posts-inner-details-text, "
                        ".actor-right-details-images",
                        timeout=min(self.timeout_ms, 10000),
                    )
                except PlaywrightTimeoutError:
                    pass
                payload = self.page.evaluate(
                    """
                    () => {
                      const clean = value => (value || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                      const actorRoot = document.querySelector(
                        '.actor-right-details-images'
                      );
                      const actorLinks = actorRoot ? Array.from(
                        actorRoot.querySelectorAll(
                          'a[href*="_avperformer/"]'
                        )
                      ).map(link => ({
                        name: clean(link.textContent),
                        href: link.getAttribute('href') || '',
                        avatar_url: (
                          link.querySelector('img[src]')
                            ?.getAttribute('src') || ''
                        )
                      })) : [];
                      const categoryLinks = Array.from(
                        document.querySelectorAll(
                          '.posts-message a[href*="_category/"]'
                        )
                      ).map(link => ({
                        name: clean(link.textContent),
                        href: link.getAttribute('href') || ''
                      }));
                      let jsonLd = {};
                      for (const node of document.querySelectorAll(
                        'script[type="application/ld+json"]'
                      )) {
                        try {
                          const parsed = JSON.parse(node.textContent || '{}');
                          if (parsed && parsed['@type'] === 'VideoObject') {
                            jsonLd = parsed;
                            break;
                          }
                        } catch (_) {}
                      }
                      return {
                        title: clean(
                          document.querySelector('.archive-title h1')
                            ?.textContent
                          || jsonLd.name
                          || document.title
                        ),
                        description: clean(jsonLd.description || ''),
                        jsonLdKeywords: jsonLd.keywords || '',
                        actorLinks,
                        categoryLinks
                      };
                    }
                    """
                )
                actors = actors_from_payload(payload)
                categories, source = categories_from_payload(payload)
                if actors or categories:
                    return EighteenAvFetchResult(
                        categories=categories,
                        http_status=last_status,
                        source=source,
                        actors=actors,
                        title=payload.get("title", ""),
                        language="zh",
                    )
                last_error = "18AV page returned no actors or categories"
            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                last_error = str(exc)
            if attempt < self.attempts:
                time.sleep(min(0.5 * attempt, 2.0))
        return EighteenAvFetchResult([], last_status, last_error)

    def fetch_many(self, urls: List[str], concurrency: int = 1):
        return [self.fetch(url) for url in urls]
