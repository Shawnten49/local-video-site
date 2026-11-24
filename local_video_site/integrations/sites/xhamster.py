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
XHAMSTER_HOST = "xhamster.com"
CATEGORY_SELECTOR = (
    '[data-role="video-tags-list"] a[href*="/categories/"], '
    '[data-role="video-tags-list"] a[href*="/tags/"]'
)
_SLUG_RE = re.compile(r"^/(?:[^/]+/)?categories/([^/]+)/?$")
_TAG_RE = re.compile(r"^/(?:[^/]+/)?tags/([^/]+)/?$")
_ACTOR_RE = re.compile(r"^/(?:[^/]+/)?pornstars/([^/]+)/?$")
_META_CATEGORY_RE = re.compile(
    r"Category:\s*(.+?)(?:\.\s|\.$)",
    flags=re.IGNORECASE,
)
_META_ACTORS_RE = re.compile(
    r"Starring:\s*(.+?)(?:\.\s|\.$)",
    flags=re.IGNORECASE,
)


def _absolute_xhamster_url(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return urljoin("https://xhamster.com", raw)
    return raw


def is_xhamster_url(value: str) -> bool:
    from local_video_site.integrations.profiles import resolve_site_profile

    return resolve_site_profile(value).key == "xhamster"


def _category_from_url(url: str, name: str) -> Optional[Dict]:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if hostname != XHAMSTER_HOST and not hostname.endswith("." + XHAMSTER_HOST):
        return None
    category_match = _SLUG_RE.match(parsed.path)
    tag_match = _TAG_RE.match(parsed.path)
    cleaned_name = " ".join((name or "").split()).strip()
    is_hd = parsed.path == "/hd"
    if (not category_match and not tag_match and not is_hd) or not cleaned_name:
        return None
    slug = (
        tag_match.group(1)
        if tag_match
        else category_match.group(1)
        if category_match
        else None
    )
    if tag_match:
        external_type = "tag"
    elif is_hd:
        external_type = "special"
    else:
        external_type = "category"
    return {
        "name": cleaned_name,
        "slug": slug,
        "type": external_type,
        "url": (
            url
            if is_hd
            else "https://xhamster.com/{}{}".format(
                "tags/" if external_type == "tag" else "categories/",
                slug,
            )
        ),
    }


def parse_meta_categories(value: str) -> List[Dict]:
    match = _META_CATEGORY_RE.search(value or "")
    if not match:
        return []
    result = []
    seen = set()
    for raw_name in match.group(1).split(","):
        name = " ".join(raw_name.split()).strip()
        normalized = name.casefold()
        if not name or normalized in seen:
            continue
        seen.add(normalized)
        result.append({
            "name": name,
            "slug": None,
            "type": "category",
            "url": None,
        })
    return result


def _actor_from_url(url: str, name: str) -> Optional[Dict]:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower()
    if hostname != XHAMSTER_HOST and not hostname.endswith("." + XHAMSTER_HOST):
        return None
    match = _ACTOR_RE.match(parsed.path)
    cleaned_name = " ".join((name or "").split()).strip()
    if not match or not cleaned_name:
        return None
    slug = match.group(1)
    return {
        "name": cleaned_name,
        "slug": slug,
        "url": "https://xhamster.com/pornstars/" + slug,
    }


def parse_meta_actors(value: str) -> List[Dict]:
    match = _META_ACTORS_RE.search(value or "")
    if not match:
        return []
    result = []
    seen = set()
    for raw_name in match.group(1).split(","):
        name = " ".join(raw_name.split()).strip()
        normalized = name.casefold()
        if not name or normalized in seen:
            continue
        seen.add(normalized)
        result.append({
            "name": name,
            "slug": None,
            "url": None,
        })
    return result


def deduplicate_categories(categories: List[Dict]) -> List[Dict]:
    result = []
    seen = set()
    for category in categories:
        name = " ".join(str(category.get("name") or "").split()).strip()
        slug = str(category.get("slug") or "").strip() or None
        # The categories table is unique by normalized display name. Use the
        # same key here so SEO/meta entries cannot duplicate richer state items.
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
        name = " ".join(str(actor.get("name") or "").split()).strip()
        slug = str(actor.get("slug") or "").strip() or None
        key = ("slug", slug.casefold()) if slug else ("name", name.casefold())
        if not name or key in seen:
            continue
        seen.add(key)
        item = {
            "name": name,
            "slug": slug,
            "url": actor.get("url"),
        }
        if actor.get("external_id") is not None:
            item["external_id"] = actor.get("external_id")
        avatar_url = _absolute_xhamster_url(actor.get("avatar_url"))
        if avatar_url:
            item["avatar_url"] = avatar_url
        result.append(item)
    return result


def categories_from_payload(payload: Dict) -> tuple:
    categories = []
    for tag in payload.get("stateTags") or []:
        if not (tag.get("isCategory") or tag.get("isTag")):
            continue
        name = " ".join(str(tag.get("name") or "").split()).strip()
        if not name:
            continue
        url = _absolute_xhamster_url(tag.get("url"))
        external_type = "tag" if tag.get("isTag") else "category"
        if url and urlsplit(url).path == "/hd":
            external_type = "special"
        categories.append({
            "name": name,
            "slug": str(tag.get("slug") or "").strip() or None,
            "type": external_type,
            "url": url,
        })
    for link in payload.get("links") or []:
        category = _category_from_url(
            link.get("url", ""),
            link.get("name", ""),
        )
        if category:
            categories.append(category)
    categories.extend(
        parse_meta_categories(payload.get("description", ""))
    )
    categories = deduplicate_categories(categories)
    source = "state" if payload.get("stateTags") else "dom"
    return categories, source


def actors_from_payload(payload: Dict) -> List[Dict]:
    state_actors = []
    for tag in payload.get("stateTags") or []:
        if not tag.get("isPornstar"):
            continue
        name = " ".join(str(tag.get("name") or "").split()).strip()
        if not name:
            continue
        item = {
            "name": name,
            "slug": tag.get("slug"),
            "url": _absolute_xhamster_url(tag.get("url")),
        }
        if tag.get("id") is not None:
            item["external_id"] = tag.get("id")
        avatar_url = _absolute_xhamster_url(tag.get("thumbUrl"))
        if avatar_url:
            item["avatar_url"] = avatar_url
        state_actors.append(item)
    actors = deduplicate_actors(state_actors)
    if actors:
        return actors
    for link in payload.get("actorLinks") or []:
        actor = _actor_from_url(
            link.get("url", ""),
            link.get("name", ""),
        )
        if actor:
            actors.append(actor)
    actors = deduplicate_actors(actors)
    if actors:
        return actors
    return parse_meta_actors(payload.get("description", ""))


@dataclass
class FetchResult:
    categories: List[Dict]
    http_status: Optional[int]
    error: Optional[str] = None
    source: str = "dom"
    actors: List[Dict] = field(default_factory=list)
    title: str = ""


class XHamsterCategoryFetcher(BrowserSession):
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
        self.has_document = False

    def __enter__(self):
        super().__enter__()
        self.page = self.new_page(block_images=True)
        return self

    def fetch(self, url: str) -> FetchResult:
        if not is_xhamster_url(url):
            return FetchResult([], None, "Not an xHamster video URL")
        last_status = None
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                if not self.has_document:
                    response = self.page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    last_status = response.status if response else None
                    self.page.wait_for_timeout(300)
                    self.has_document = True
                    payload = self.page.evaluate(
                        """
                        () => {
                          const root = document.querySelector(
                            '[data-role="video-tags-list"]'
                          );
                          const links = root ? Array.from(
                            root.querySelectorAll(
                              'a[href*="/categories/"], a[href*="/tags/"]'
                            )
                          ).map(a => ({
                            name: (a.textContent || '').trim(),
                            url: a.href
                          })) : [];
                          const actorLinks = root ? Array.from(
                            root.querySelectorAll('a[href*="/pornstars/"]')
                          ).map(a => ({
                            name: (a.textContent || '').trim(),
                            url: a.href
                          })) : [];
                          const description = document.querySelector(
                            'meta[name="description"]'
                          )?.content || '';
                          const stateTags =
                            window.initials?.videoTagsComponent?.tags || [];
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
                            actorLinks,
                            description,
                            stateTags,
                            title
                          };
                        }
                        """
                    )
                else:
                    payload = self.page.evaluate(
                        """
                        async (url) => {
                          const response = await fetch(url, {
                            credentials: 'include',
                            redirect: 'follow'
                          });
                          const html = await response.text();
                          const doc = new DOMParser().parseFromString(
                            html,
                            'text/html'
                          );
                          const initialsTags = (documentNode) => {
                            const scripts = Array.from(
                              documentNode.scripts || []
                            );
                            const script =
                              documentNode.querySelector('#initials-script')
                              || scripts.find(
                                node => /window\\.initials\\s*=/.test(
                                  node.textContent || ''
                                )
                              );
                            if (!script) return [];
                            const text = script.textContent || '';
                            const match =
                              /window\\.initials\\s*=\\s*/.exec(text);
                            if (!match) return [];
                            const start = match.index + match[0].length;
                            let depth = 0;
                            let inString = false;
                            let escaped = false;
                            let quote = '';
                            for (
                              let index = start;
                              index < text.length;
                              index += 1
                            ) {
                              const character = text[index];
                              if (inString) {
                                if (escaped) {
                                  escaped = false;
                                } else if (character === '\\\\') {
                                  escaped = true;
                                } else if (character === quote) {
                                  inString = false;
                                }
                                continue;
                              }
                              if (character === '"' || character === "'") {
                                inString = true;
                                quote = character;
                              } else if (character === '{') {
                                depth += 1;
                              } else if (character === '}') {
                                depth -= 1;
                                if (depth === 0) {
                                  try {
                                    const initials = JSON.parse(
                                      text.slice(start, index + 1)
                                    );
                                    return (
                                      initials?.videoTagsComponent?.tags || []
                                    );
                                  } catch (error) {
                                    return [];
                                  }
                                }
                              }
                            }
                            return [];
                          };
                          const root = doc.querySelector(
                            '[data-role="video-tags-list"]'
                          );
                          const links = root ? Array.from(
                            root.querySelectorAll(
                              'a[href*="/categories/"], a[href*="/tags/"]'
                            )
                          ).map(a => ({
                            name: (a.textContent || '').trim(),
                            url: new URL(a.getAttribute('href'), url).href
                          })) : [];
                          const actorLinks = root ? Array.from(
                            root.querySelectorAll('a[href*="/pornstars/"]')
                          ).map(a => ({
                            name: (a.textContent || '').trim(),
                            url: new URL(a.getAttribute('href'), url).href
                          })) : [];
                          const description = doc.querySelector(
                            'meta[name="description"]'
                          )?.content || '';
                          const stateTags = initialsTags(doc);
                          return {
                            status: response.status,
                            links,
                            actorLinks,
                            description,
                            stateTags,
                            title: (
                              doc.querySelector('h1')?.textContent
                              || doc.querySelector(
                                'meta[property="og:title"]'
                              )?.content
                              || doc.title
                              || ''
                            ).trim()
                          };
                        }
                        """,
                        url,
                    )
                    last_status = payload.get("status")
                categories, source = categories_from_payload(payload)
                actors = actors_from_payload(payload)
                if categories or actors:
                    return FetchResult(
                        categories,
                        last_status,
                        source=source,
                        actors=actors,
                        title=payload.get("title", ""),
                    )
                last_error = "No category tags found"
                self.has_document = False
            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                last_error = str(exc)
                self.has_document = False
            if attempt < self.attempts:
                time.sleep(min(0.5 * attempt, 2.0))
        return FetchResult([], last_status, last_error or "Category fetch failed")

    def fetch_many(
        self,
        urls: List[str],
        concurrency: int = 2,
    ) -> List[FetchResult]:
        if not urls:
            return []
        if not self.has_document:
            first = self.fetch(urls[0])
            remaining = urls[1:]
            if not remaining:
                return [first]
            results = [first]
        else:
            remaining = urls
            results = []

        for offset in range(0, len(remaining), max(1, concurrency)):
            batch = remaining[offset:offset + max(1, concurrency)]
            try:
                payloads = self.page.evaluate(
                    """
                    async ({urls, concurrency}) => {
                      const results = new Array(urls.length);
                      let nextIndex = 0;
                      async function worker() {
                        while (nextIndex < urls.length) {
                          const index = nextIndex++;
                          const url = urls[index];
                          try {
                            const response = await fetch(url, {
                              credentials: 'include',
                              redirect: 'follow'
                            });
                            const html = await response.text();
                            const doc = new DOMParser().parseFromString(
                              html,
                              'text/html'
                            );
                            const initialsTags = (documentNode) => {
                              const scripts = Array.from(
                                documentNode.scripts || []
                              );
                              const script =
                                documentNode.querySelector(
                                  '#initials-script'
                                )
                                || scripts.find(
                                  node => /window\\.initials\\s*=/.test(
                                    node.textContent || ''
                                  )
                                );
                              if (!script) return [];
                              const text = script.textContent || '';
                              const match =
                                /window\\.initials\\s*=\\s*/.exec(text);
                              if (!match) return [];
                              const start = match.index + match[0].length;
                              let depth = 0;
                              let inString = false;
                              let escaped = false;
                              let quote = '';
                              for (
                                let index = start;
                                index < text.length;
                                index += 1
                              ) {
                                const character = text[index];
                                if (inString) {
                                  if (escaped) {
                                    escaped = false;
                                  } else if (character === '\\\\') {
                                    escaped = true;
                                  } else if (character === quote) {
                                    inString = false;
                                  }
                                  continue;
                                }
                                if (character === '"' || character === "'") {
                                  inString = true;
                                  quote = character;
                                } else if (character === '{') {
                                  depth += 1;
                                } else if (character === '}') {
                                  depth -= 1;
                                  if (depth === 0) {
                                    try {
                                      const initials = JSON.parse(
                                        text.slice(start, index + 1)
                                      );
                                      return (
                                        initials?.videoTagsComponent?.tags
                                        || []
                                      );
                                    } catch (error) {
                                      return [];
                                    }
                                  }
                                }
                              }
                              return [];
                            };
                            const root = doc.querySelector(
                              '[data-role="video-tags-list"]'
                            );
                            const links = root ? Array.from(
                              root.querySelectorAll(
                                'a[href*="/categories/"], a[href*="/tags/"]'
                              )
                            ).map(a => ({
                              name: (a.textContent || '').trim(),
                              url: new URL(a.getAttribute('href'), url).href
                            })) : [];
                            const actorLinks = root ? Array.from(
                              root.querySelectorAll(
                                'a[href*="/pornstars/"]'
                              )
                            ).map(a => ({
                              name: (a.textContent || '').trim(),
                              url: new URL(a.getAttribute('href'), url).href
                            })) : [];
                            const description = doc.querySelector(
                              'meta[name="description"]'
                            )?.content || '';
                            const stateTags = initialsTags(doc);
                            results[index] = {
                              url,
                              status: response.status,
                              links,
                              actorLinks,
                              description,
                              stateTags
                            };
                          } catch (error) {
                            results[index] = {
                              url,
                              status: null,
                              links: [],
                              actorLinks: [],
                              description: '',
                              error: String(error)
                            };
                          }
                        }
                      }
                      await Promise.all(
                        Array.from(
                          {length: Math.min(concurrency, urls.length)},
                          () => worker()
                        )
                      );
                      return results;
                    }
                    """,
                    {"urls": batch, "concurrency": max(1, concurrency)},
                )
            except PlaywrightError as exc:
                self.has_document = False
                payloads = [
                    {
                        "url": url,
                        "status": None,
                        "links": [],
                        "description": "",
                        "error": str(exc),
                    }
                    for url in batch
                ]

            for payload in payloads:
                categories, source = categories_from_payload(payload)
                actors = actors_from_payload(payload)
                error = payload.get("error")
                if not categories and not actors and not error:
                    error = "No category tags found"
                results.append(
                    FetchResult(
                        categories,
                        payload.get("status"),
                        error,
                        source=source,
                        actors=actors,
                    )
                )
        return results
