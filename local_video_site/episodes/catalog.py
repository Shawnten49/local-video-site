from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from local_video_site.core import config
from local_video_site.episodes.naming import meaningful_episode_title


LOGGER = logging.getLogger(__name__)
BILIBILI_EPISODE_RE = re.compile(r"^/bangumi/play/ep(?P<ep_id>\d+)")
BILIBILI_SEASON_RE = re.compile(r"^/bangumi/play/ss(?P<season_id>\d+)")
BILIBILI_SEASON_API = "https://api.bilibili.com/pgc/view/web/season"
_NUMBER_RE = re.compile(r"\d+")


class EpisodeCatalogError(RuntimeError):
    def __init__(self, message: str, code: str = "catalog_failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EpisodeCandidate:
    episode_number: int
    title: str
    source_url: str
    source_tab_key: str = ""
    source_tab_title: str = ""
    source_tab_item_title: str = ""
    source_season_name: str = ""
    source_episode_number: Optional[int] = None
    source_tab_item_count: Optional[int] = None


@dataclass(frozen=True)
class EpisodeCatalog:
    provider: str
    detected_series_name: str
    detected_season_name: str
    source_url: str
    episodes: List[EpisodeCandidate]
    warning: str = ""
    selection_mode: str = ""
    accepted_count: int = 0
    excluded_count: int = 0


@dataclass(frozen=True)
class EpisodeTab:
    tab_sequence: int
    tab_key: str
    tab_title: str
    season_name: str
    season_number: int
    source_url: str
    catalog: Optional[EpisodeCatalog] = None
    warning: str = ""
    complete: bool = True

    @property
    def status(self) -> str:
        return "ready" if self.catalog is not None else "failed"

    @property
    def is_complete(self) -> bool:
        return self.catalog is not None and self.complete


def _canonical_episode_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((
        "https",
        "www.bilibili.com",
        parsed.path,
        "",
        "",
    ))


def _number_from_title(value: str, fallback: int) -> int:
    match = _NUMBER_RE.search(str(value or ""))
    if not match:
        return fallback
    try:
        return max(1, int(match.group(0)))
    except ValueError:
        return fallback


def _bilibili_episode_title(
    item: Dict,
    result: Dict,
    episode_number: int,
    item_count: int,
) -> str:
    long_title = str(item.get("long_title") or "").strip()
    raw_title = str(item.get("title") or "").strip()
    informative = (
        meaningful_episode_title(long_title)
        or meaningful_episode_title(raw_title)
    )
    if informative:
        return informative
    context_title = (
        meaningful_episode_title(str(result.get("title") or ""))
        or meaningful_episode_title(
            str(result.get("season_title") or "")
        )
    )
    if not context_title:
        return raw_title or "第{}集".format(episode_number)
    if item_count <= 1:
        return context_title
    if raw_title:
        return "{} · {}".format(context_title, raw_title)
    return "{} · 第{}集".format(context_title, episode_number)


def _section_type(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _main_bilibili_episodes(payload_result: Dict) -> tuple:
    raw_episodes = payload_result.get("episodes") or []
    if not isinstance(raw_episodes, list):
        raise EpisodeCatalogError(
            "Bilibili 剧集列表格式无法识别",
            "catalog_invalid",
        )
    sections = payload_result.get("section") or []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            title = " ".join(
                str(section.get("title") or "").strip().split()
            ).casefold()
            if title not in {"正片", "tv"}:
                continue
            section_episodes = section.get("episodes") or []
            if isinstance(section_episodes, list) and section_episodes:
                excluded = max(
                    0,
                    len(raw_episodes) - len(section_episodes),
                )
                return (
                    section_episodes,
                    "bilibili_main_section",
                    excluded,
                )

    typed_episodes = [
        item
        for item in raw_episodes
        if isinstance(item, dict) and "section_type" in item
    ]
    if typed_episodes:
        main = [
            item
            for item in raw_episodes
            if isinstance(item, dict)
            and _section_type(item.get("section_type")) == 0
            and not bool(item.get("is_view_hide"))
        ]
        return (
            main,
            "bilibili_section_type",
            max(0, len(raw_episodes) - len(main)),
        )

    # Older payloads may omit section_type. In that case episode rows are
    # already the main publication list, so retain compatibility.
    return (
        [
            item for item in raw_episodes
            if isinstance(item, dict)
            and not bool(item.get("is_view_hide"))
        ],
        "bilibili_legacy_all",
        0,
    )


def _catalog_from_bilibili_payload(
    payload: Dict,
    source_url: str,
) -> EpisodeCatalog:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise EpisodeCatalogError(
            "Bilibili 未返回剧集列表，可能需要登录或完成页面验证",
            "catalog_unavailable",
        )
    raw_episodes, selection_mode, excluded_count = (
        _main_bilibili_episodes(result)
    )
    episodes = []
    seen = set()
    for index, item in enumerate(raw_episodes, start=1):
        if not isinstance(item, dict):
            continue
        ep_id = str(item.get("ep_id") or "").strip()
        if not ep_id or ep_id in seen:
            continue
        link = str(item.get("link") or "").strip()
        path = link if link else "/bangumi/play/ep{}".format(ep_id)
        full_url = urljoin("https://www.bilibili.com/", path)
        if "bilibili.com/bangumi/play/ep" not in full_url:
            continue
        seen.add(ep_id)
        episode_number = _number_from_title(
            str(item.get("title") or ""),
            index,
        )
        title = _bilibili_episode_title(
            item,
            result,
            episode_number,
            len(raw_episodes),
        )
        source_tab_item_title = str(item.get("title") or "").strip()
        episodes.append(EpisodeCandidate(
            episode_number=episode_number,
            title=title,
            source_url=_canonical_episode_url(full_url),
            source_tab_key="ss{}".format(result.get("season_id") or ""),
            source_tab_title=str(
                result.get("season_title") or ""
            ).strip(),
            source_tab_item_title=source_tab_item_title,
            source_season_name=str(
                result.get("season_title") or ""
            ).strip(),
            source_episode_number=episode_number,
            source_tab_item_count=len(raw_episodes),
        ))
    episodes.sort(key=lambda item: item.episode_number)
    if not episodes:
        raise EpisodeCatalogError(
            "没有识别到可下载的正片剧集",
            "catalog_empty",
        )
    # Bilibili occasionally repeats an ordinal for trailers or previews.
    normalized = []
    used = set()
    for sequence, item in enumerate(episodes, start=1):
        episode_number = item.episode_number
        if episode_number in used:
            episode_number = sequence
        while episode_number in used:
            episode_number += 1
        used.add(episode_number)
        normalized.append(EpisodeCandidate(
            episode_number=episode_number,
            title=item.title,
            source_url=item.source_url,
            source_tab_key=item.source_tab_key,
            source_tab_title=item.source_tab_title,
            source_tab_item_title=item.source_tab_item_title,
            source_season_name=item.source_season_name,
            source_episode_number=item.source_episode_number,
            source_tab_item_count=item.source_tab_item_count,
        ))
    warnings = []
    if excluded_count:
        warnings.append(
            "已按 TV/正片筛选，排除 {} 个预告、PV 或主题曲条目。"
            .format(excluded_count)
        )
    if selection_mode == "bilibili_legacy_all":
        warnings.append(
            "接口未提供分区字段，已按旧版剧集列表解析。"
        )
    if len(normalized) == 1:
        warnings.append(
            "只识别到一集，请确认页面是否包含完整季剧集列表。"
        )
    return EpisodeCatalog(
        provider="bilibili",
        detected_series_name=str(result.get("title") or "").strip(),
        detected_season_name=str(
            result.get("season_title") or ""
        ).strip(),
        source_url=_canonical_episode_url(source_url),
        episodes=normalized,
        warning=" ".join(warnings),
        selection_mode=selection_mode,
        accepted_count=len(normalized),
        excluded_count=excluded_count,
    )


def _fetch_bilibili_season(
    identifier: str,
    parameter: str = "ep_id",
) -> Dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }
    with httpx.Client(
        timeout=15.0,
        follow_redirects=True,
        headers=headers,
    ) as client:
        last_error = None
        payload = None
        for attempt in range(3):
            try:
                response = client.get(
                    BILIBILI_SEASON_API,
                    params={parameter: identifier},
                )
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        if payload is None:
            raise EpisodeCatalogError(
                "Bilibili 剧集接口无响应：{}".format(last_error),
                "catalog_api_error",
            )
    if payload.get("code") not in (0, "0"):
        raise EpisodeCatalogError(
            "Bilibili 剧集接口返回错误：{}".format(
                payload.get("message") or payload.get("code")
            ),
            "catalog_api_error",
        )
    return payload


def _browser_rows_to_episodes(rows: List[Dict]) -> List[EpisodeCandidate]:
    number_rows = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "").strip()
        text = " ".join(str(row.get("text") or "").strip().split())
        classes = str(row.get("className") or "")
        if (
            not href
            or "/bangumi/play/ep" not in href
            or not re.fullmatch(r"\d+", text)
        ):
            continue
        if "EpisodeVirtualList_numberItem" in classes:
            number_rows.append((href, text))
    if not number_rows:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            href = str(row.get("href") or "").strip()
            text = " ".join(str(row.get("text") or "").strip().split())
            if (
                href
                and "/bangumi/play/ep" in href
                and re.fullmatch(r"\d+", text)
            ):
                number_rows.append((href, text))
    episodes = []
    seen = set()
    for href, text in number_rows:
        canonical = _canonical_episode_url(href)
        if canonical in seen:
            continue
        seen.add(canonical)
        number = max(1, int(text))
        episodes.append(EpisodeCandidate(
            episode_number=number,
            title="第{}集".format(number),
            source_url=canonical,
        ))
    episodes.sort(key=lambda item: item.episode_number)
    return episodes


def _fetch_bilibili_from_browser(source_url: str) -> EpisodeCatalog:
    # Importing lazily keeps unit tests and ordinary startup independent of
    # Playwright initialisation.
    from local_video_site.integrations.browser import BrowserSession

    with BrowserSession(
        port=9333,
        logger=LOGGER,
        profile_dir=config.PROJECT_ROOT / ".chrome-cdp-category-profile",
    ) as session:
        page = session.new_page(block_images=True)
        page.goto(source_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        try:
            tv_tab = page.get_by_role("button", name=re.compile(
                r"^\s*(?:TV|正片)\s*$",
                re.IGNORECASE,
            ), exact=True)
            if tv_tab.count():
                tv_tab.first.click(timeout=3000)
                page.wait_for_timeout(1000)
        except Exception:
            pass
        rows = page.evaluate(
            """
            () => Array.from(
              document.querySelectorAll(
                '[class*="PaginatedEpList_root"] '
                + 'a[href*="/bangumi/play/ep"]'
              )
            ).map((anchor) => ({
              href: anchor.href,
              text: anchor.getAttribute('title') || anchor.textContent || '',
              className: anchor.className || ''
            }))
            """
        )
    episodes = _browser_rows_to_episodes(rows)
    if not episodes:
        raise EpisodeCatalogError(
            "页面中没有找到剧集链接，请确认已登录且页面可以正常打开",
            "catalog_empty",
        )
    return EpisodeCatalog(
        provider="bilibili",
        detected_series_name="",
        detected_season_name="",
        source_url=_canonical_episode_url(source_url),
        episodes=episodes,
        warning=(
            "只识别到一集，请确认页面是否包含完整季剧集列表。"
            if len(episodes) == 1
            else ""
        ),
        selection_mode="bilibili_tv_dom",
        accepted_count=len(episodes),
        excluded_count=max(
            0,
            len(rows or []) - len(episodes),
        ),
    )


def _continuous_season_entries(
    payload: Dict,
    source_url: str,
) -> List[Dict]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise EpisodeCatalogError(
            "Bilibili 未返回剧集列表，可能需要登录或完成页面验证",
            "catalog_unavailable",
        )
    entries = []
    seen = set()
    raw_seasons = result.get("seasons") or []
    if isinstance(raw_seasons, list):
        for item in raw_seasons:
            if not isinstance(item, dict):
                continue
            season_id = str(item.get("season_id") or "").strip()
            if not season_id or season_id in seen:
                continue
            seen.add(season_id)
            title = str(item.get("season_title") or "").strip()
            entries.append({
                "season_id": season_id,
                "title": title or "第{}季".format(len(entries) + 1),
                "source_url": _canonical_episode_url(
                    "/bangumi/play/ss{}".format(season_id)
                ),
                "seed_payload": (
                    payload
                    if str(result.get("season_id") or "") == season_id
                    else None
                ),
            })
    current_id = str(result.get("season_id") or "").strip()
    if current_id and current_id not in seen:
        entries.insert(0, {
            "season_id": current_id,
            "title": (
                str(
                    result.get("season_title")
                    or result.get("title")
                    or ""
                ).strip()
                or "第1季"
            ),
            "source_url": _canonical_episode_url(source_url),
            "seed_payload": payload,
        })
    if not entries:
        raise EpisodeCatalogError(
            "没有识别到可连续解析的选集 Tab",
            "catalog_empty",
        )
    return entries[:config.discovery_max_tabs()]


def _tab_from_payload(
    sequence: int,
    entry: Dict,
    payload: Dict,
) -> EpisodeTab:
    catalog = _catalog_from_bilibili_payload(
        payload,
        entry["source_url"],
    )
    title = (
        catalog.detected_season_name
        or str(entry.get("title") or "").strip()
        or "第{}季".format(sequence)
    )
    return EpisodeTab(
        tab_sequence=sequence,
        tab_key="ss{}".format(entry["season_id"]),
        tab_title=title,
        season_name=title,
        season_number=sequence,
        source_url=entry["source_url"],
        catalog=catalog,
    )


def discover_episode_tabs(source_url: str) -> List[EpisodeTab]:
    """Discover every season tab while preserving the site's tab order."""

    parsed = urlsplit(str(source_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EpisodeCatalogError("请输入完整的 HTTP/HTTPS 视频地址")
    hostname = (parsed.hostname or "").lower()
    episode_match = BILIBILI_EPISODE_RE.match(parsed.path)
    season_match = BILIBILI_SEASON_RE.match(parsed.path)
    if not hostname.endswith("bilibili.com") or not (
        episode_match or season_match
    ):
        raise EpisodeCatalogError(
            "当前仅支持 Bilibili 番剧剧集或季地址"
            "（/bangumi/play/ep... 或 /bangumi/play/ss...）",
            "provider_unsupported",
        )
    source_url = _canonical_episode_url(source_url)
    try:
        if episode_match:
            seed_payload = _fetch_bilibili_season(
                episode_match.group("ep_id"),
                "ep_id",
            )
        else:
            seed_payload = _fetch_bilibili_season(
                season_match.group("season_id"),
                "season_id",
            )
        entries = _continuous_season_entries(
            seed_payload,
            source_url,
        )
    except (httpx.HTTPError, ValueError, EpisodeCatalogError) as exc:
        try:
            fallback = _fetch_bilibili_from_browser(source_url)
        except Exception as fallback_exc:
            raise EpisodeCatalogError(
                "连续剧集解析失败：{}；浏览器降级失败：{}".format(
                    exc,
                    fallback_exc,
                ),
                "catalog_failed",
            ) from fallback_exc
        return [
            EpisodeTab(
                tab_sequence=1,
                tab_key="current",
                tab_title=fallback.detected_season_name or "当前选集",
                season_name=fallback.detected_season_name or "当前选集",
                season_number=1,
                source_url=fallback.source_url,
                catalog=fallback,
                warning=(
                    "季度接口不可用，已降级为浏览器当前选集解析：{}"
                    .format(exc)
                ),
                complete=False,
            )
        ]

    tabs: List[EpisodeTab] = []
    pending = []
    total_episodes = 0
    for sequence, entry in enumerate(entries, start=1):
        seed_payload = entry.get("seed_payload")
        if isinstance(seed_payload, dict) and seed_payload:
            try:
                tab = _tab_from_payload(sequence, entry, seed_payload)
            except EpisodeCatalogError as exc:
                tabs.append(EpisodeTab(
                    tab_sequence=sequence,
                    tab_key="ss{}".format(entry["season_id"]),
                    tab_title=entry["title"],
                    season_name=entry["title"],
                    season_number=sequence,
                    source_url=entry["source_url"],
                    warning=str(exc),
                ))
            else:
                total_episodes += len(tab.catalog.episodes)
                tabs.append(tab)
            continue
        pending.append((sequence, entry))

    if (
        pending
        and total_episodes < config.discovery_max_episodes()
    ):
        with ThreadPoolExecutor(
            max_workers=min(
                config.discovery_workers(),
                len(pending),
            )
        ) as executor:
            futures = {
                executor.submit(
                    _fetch_bilibili_season,
                    entry["season_id"],
                    "season_id",
                ): (sequence, entry)
                for sequence, entry in pending
            }
            resolved = {}
            for future in as_completed(futures):
                sequence, entry = futures[future]
                resolved[sequence] = (entry, future)
            for sequence in sorted(resolved):
                entry, future = resolved[sequence]
                try:
                    payload = future.result()
                    tab = _tab_from_payload(sequence, entry, payload)
                except Exception as exc:
                    tabs.append(EpisodeTab(
                        tab_sequence=sequence,
                        tab_key="ss{}".format(entry["season_id"]),
                        tab_title=entry["title"],
                        season_name=entry["title"],
                        season_number=sequence,
                        source_url=entry["source_url"],
                        warning=str(exc),
                    ))
                    continue
                total_episodes += len(tab.catalog.episodes)
                tabs.append(tab)
                if (
                    total_episodes
                    >= config.discovery_max_episodes()
                ):
                    break

    tabs.sort(key=lambda item: item.tab_sequence)
    if not any(tab.catalog is not None for tab in tabs):
        raise EpisodeCatalogError(
            "所有选集 Tab 都没有识别到可下载的正片剧集",
            "catalog_empty",
        )
    return tabs


def discover_episode_catalog(source_url: str) -> EpisodeCatalog:
    parsed = urlsplit(str(source_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EpisodeCatalogError("请输入完整的 HTTP/HTTPS 视频地址")
    hostname = (parsed.hostname or "").lower()
    episode_match = BILIBILI_EPISODE_RE.match(parsed.path)
    season_match = BILIBILI_SEASON_RE.match(parsed.path)
    if not hostname.endswith("bilibili.com") or not (
        episode_match or season_match
    ):
        raise EpisodeCatalogError(
            "当前仅支持 Bilibili 番剧剧集或季地址"
            "（/bangumi/play/ep... 或 /bangumi/play/ss...）",
            "provider_unsupported",
        )
    source_url = _canonical_episode_url(source_url)
    api_error: Optional[Exception] = None
    try:
        if episode_match:
            payload = _fetch_bilibili_season(
                episode_match.group("ep_id"),
                "ep_id",
            )
        else:
            payload = _fetch_bilibili_season(
                season_match.group("season_id"),
                "season_id",
            )
        return _catalog_from_bilibili_payload(payload, source_url)
    except (httpx.HTTPError, ValueError, EpisodeCatalogError) as exc:
        api_error = exc
        LOGGER.warning(
            "Bilibili season API failed; falling back to browser: %s",
            exc,
        )
    try:
        return _fetch_bilibili_from_browser(source_url)
    except Exception as exc:
        detail = str(api_error or exc)
        raise EpisodeCatalogError(
            "剧集解析失败：{}".format(detail),
            "catalog_failed",
        ) from exc
