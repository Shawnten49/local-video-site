from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from local_video_site.integrations.browser import BrowserSession


LOGGER = logging.getLogger(__name__)
XVIDEOS_HOST = "xvideos.com"
XVIDEOS_PARSER_VERSION = 3
VIDEO_PATH_RE = re.compile(r"^/video\.[^/]+(?:/|$)")
ACTOR_PATH_RE = re.compile(r"^/(pornstars|models)/([^/?#]+)/?$")
TAG_PATH_RE = re.compile(r"^/tags/([^/?#]+)/?$")
_META_STOPWORDS = {
    "xvideos",
    "xvideos.com",
    "x videos",
    "x video",
    "porn",
    "video",
    "videos",
}


def _absolute_url(value: Optional[str], base: str = "https://www.xvideos.com") -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return urljoin(base, raw)


def is_xvideos_url(value: str) -> bool:
    from local_video_site.integrations.profiles import resolve_site_profile

    return resolve_site_profile(value).key == "xvideos"


def deduplicate_categories(categories: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for item in categories:
        name = " ".join(str(item.get("name") or "").split()).strip()
        slug = str(item.get("slug") or "").strip() or None
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "name": name,
                "slug": slug,
                "type": item.get("type") or "tag",
                "url": item.get("url"),
            }
        )
    return result


def deduplicate_actors(actors: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for item in actors:
        name = " ".join(str(item.get("name") or "").split()).strip()
        slug = str(item.get("slug") or "").strip() or None
        external_id = item.get("external_id")
        key = (
            ("id", str(external_id))
            if external_id is not None
            else ("slug", slug.casefold())
            if slug
            else ("name", name.casefold())
        )
        if not name or key in seen:
            continue
        seen.add(key)
        actor = {
            "name": name,
            "slug": slug,
            "url": item.get("url"),
        }
        if external_id is not None:
            actor["external_id"] = int(external_id)
        if item.get("avatar_url"):
            actor["avatar_url"] = item["avatar_url"]
        if item.get("description"):
            actor["description"] = item["description"]
        result.append(actor)
    return result


def _category_from_link(link: Dict) -> Optional[Dict]:
    href = str(link.get("href") or "").strip()
    try:
        parsed = urlsplit(href)
    except ValueError:
        return None
    match = TAG_PATH_RE.match(parsed.path)
    name = " ".join(str(link.get("name") or "").split()).strip()
    if not match or not name:
        return None
    slug = match.group(1)
    return {
        "name": name,
        "slug": slug,
        "type": "tag",
        "url": "https://www.xvideos.com/tags/" + slug,
    }


def _actor_from_link(link: Dict) -> Optional[Dict]:
    href = str(link.get("href") or "").strip()
    try:
        parsed = urlsplit(href)
    except ValueError:
        return None
    match = ACTOR_PATH_RE.match(parsed.path)
    name = " ".join(str(link.get("name") or "").split()).strip()
    if not match or not name:
        return None
    profile_kind, slug = match.groups()
    actor = {
        "name": name,
        "slug": slug,
        "url": "https://www.xvideos.com/{}/{}".format(
            profile_kind,
            slug,
        ),
    }
    external_id = str(link.get("external_id") or "").strip()
    if external_id.isdigit():
        actor["external_id"] = int(external_id)
    return actor


def categories_from_payload(payload: Dict) -> tuple:
    categories = []
    for name in payload.get("videoTags") or []:
        slug = str(name or "").strip()
        if not slug:
            continue
        categories.append(
            {
                "name": slug,
                "slug": slug,
                "type": "tag",
                "url": "https://www.xvideos.com/tags/" + slug,
            }
        )
    for link in payload.get("tagLinks") or []:
        category = _category_from_link(link)
        if category:
            categories.append(category)
    keywords = str(payload.get("metaKeywords") or "")
    for raw_name in keywords.split(","):
        name = " ".join(raw_name.split()).strip()
        if not name or name.casefold() in _META_STOPWORDS:
            continue
        categories.append(
            {
                "name": name,
                "slug": None,
                "type": "tag",
                "url": None,
            }
        )
    return (
        deduplicate_categories(categories),
        "initials" if payload.get("videoTags") else "dom",
    )


def actors_from_payload(payload: Dict) -> List[Dict]:
    actors = [
        actor
        for link in payload.get("actorLinks") or []
        if (actor := _actor_from_link(link))
    ]
    return deduplicate_actors(actors)


@dataclass
class XvideosFetchResult:
    categories: List[Dict]
    http_status: Optional[int]
    error: Optional[str] = None
    source: str = "dom"
    actors: List[Dict] = field(default_factory=list)
    title: str = ""


class XvideosMetadataFetcher(BrowserSession):
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
        self.actor_page = None

    def __enter__(self):
        super().__enter__()
        self.page = self.new_page(block_images=True)
        self.actor_page = self.new_page(block_images=False)
        return self

    def _enrich_actor_avatars(self, actors: List[Dict]) -> None:
        if self.actor_page is None:
            return
        for actor in actors[:20]:
            profile_url = actor.get("url")
            if not profile_url:
                continue
            try:
                self.actor_page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                self.actor_page.wait_for_timeout(500)
                payload = self.actor_page.evaluate(
                    """
                    () => ({
                      description:
                        document.querySelector('meta[name="description"]')
                          ?.content || '',
                      avatarUrl:
                        document.querySelector('.profile-pic img[src]')
                          ?.getAttribute('src') || ''
                    })
                    """
                )
                avatar_url = _absolute_url(payload.get("avatarUrl"))
                if avatar_url:
                    actor["avatar_url"] = avatar_url
                if payload.get("description"):
                    actor["description"] = payload["description"]
            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                self.logger.warning(
                    "xVideos actor profile fetch failed actor=%s error=%s",
                    actor.get("name"),
                    exc,
                )

    def fetch(self, url: str) -> XvideosFetchResult:
        if not is_xvideos_url(url):
            return XvideosFetchResult([], None, "Not an xVideos video URL")
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
                        ".video-metadata.video-tags-list",
                        timeout=min(self.timeout_ms, 10000),
                    )
                except PlaywrightTimeoutError:
                    pass
                payload = self.page.evaluate(
                    """
                    () => {
                      const root = document.querySelector(
                        '.video-metadata.video-tags-list'
                      );
                      const actorLinks = root ? Array.from(
                        root.querySelectorAll(
                          'li.model a[href*="/pornstars/"]'
                          + ', li.model a[href*="/models/"]'
                        )
                      ).map(link => ({
                        name: (
                          link.querySelector('.name')?.textContent
                          || link.textContent
                          || ''
                        ).trim(),
                        href: link.getAttribute('href') || '',
                        external_id: link.getAttribute('data-id') || ''
                      })) : [];
                      const tagLinks = root ? Array.from(
                        root.querySelectorAll(
                          'a.is-keyword[href*="/tags/"], '
                          + 'a[href^="/tags/"]'
                        )
                      ).map(link => ({
                        name: (link.textContent || '').trim(),
                        href: link.getAttribute('href') || ''
                      })) : [];
                      return {
                        title: (
                          document.querySelector('h1')?.textContent
                          || document.querySelector(
                            'meta[property="og:title"]'
                          )?.content
                          || document.title
                          || ''
                        ).trim(),
                        actorLinks,
                        tagLinks,
                        videoTags:
                          window.xv?.conf?.dyn?.video_tags || [],
                        metaKeywords:
                          document.querySelector(
                            'meta[name="keywords"]'
                          )?.content || ''
                      };
                    }
                    """
                )
                categories, source = categories_from_payload(payload)
                actors = actors_from_payload(payload)
                self._enrich_actor_avatars(actors)
                if categories or actors:
                    return XvideosFetchResult(
                        categories,
                        last_status,
                        source=source,
                        actors=actors,
                        title=payload.get("title", ""),
                    )
                last_error = "No xVideos actors or tags found"
            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                last_error = str(exc)
            if attempt < self.attempts:
                time.sleep(min(0.5 * attempt, 2.0))
        return XvideosFetchResult([], last_status, last_error)

    def fetch_many(self, urls: List[str], concurrency: int = 1):
        results = []
        for index, url in enumerate(urls):
            results.append(self.fetch(url))
            if index + 1 < len(urls):
                time.sleep(0.5)
        return results
