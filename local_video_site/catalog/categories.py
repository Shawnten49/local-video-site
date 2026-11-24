from __future__ import annotations

import logging
import random
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import jieba
from local_video_site.integrations.profiles import get_site_profile, resolve_site_profile

from local_video_site import database
from local_video_site.integrations.metadata import fetch_site_metadata, metadata_language, metadata_parser_version, metadata_provider_for_url
from local_video_site.integrations.sites.xhamster import XHamsterCategoryFetcher


jieba.setLogLevel(logging.WARNING)
PACKAGE_ROOT = Path(__file__).resolve().parent
STOPWORDS_PATH = PACKAGE_ROOT / "category_stopwords.txt"

_CODE_RE = re.compile(
    r"\b(?:[A-Za-z]{2,}[A-Za-z0-9]*(?:[-_]\d{2,}|\d{2,})[A-Za-z0-9_-]*"
    r"|\d{2,}[A-Za-z]{2,}[A-Za-z0-9_-]*)\b"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_EDGE_RE = re.compile(r"^[\W_]+|[\W_]+$", flags=re.UNICODE)

_BUILTIN_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "feat",
    "featuring",
    "episode",
    "for",
    "from",
    "full",
    "hd",
    "in",
    "movie",
    "of",
    "on",
    "or",
    "part",
    "porn",
    "the",
    "to",
    "video",
    "videos",
    "vol",
    "best",
    "com",
    "www",
    "with",
    "在线观看",
    "在线",
    "观看",
    "完整",
    "完整版",
    "电影",
    "视频",
}


def load_stopwords() -> set:
    values = set(_BUILTIN_STOPWORDS)
    if STOPWORDS_PATH.is_file():
        for raw_line in STOPWORDS_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip().lower()
            if line and not line.startswith("#"):
                values.add(line)
    return values


STOPWORDS = load_stopwords()


def _cache_matches_metadata_language(cache_item: Dict, provider: str) -> bool:
    expected = metadata_language(provider)
    if not expected:
        return True
    return str(cache_item.get("metadata_language") or "").lower() == expected


def _clean_token(value: str) -> str:
    return _EDGE_RE.sub("", unicodedata.normalize("NFKC", value or "")).strip()


def tokenize_title(title: str) -> List[Tuple[str, str]]:
    """Return (display_name, normalized_name) pairs in title order."""

    value = unicodedata.normalize("NFKC", title or "")
    result: List[Tuple[str, str]] = []
    seen = set()

    def add(token: str) -> None:
        cleaned = _clean_token(token)
        if not cleaned:
            return
        normalized = cleaned.casefold()
        if normalized in STOPWORDS or normalized.isdigit():
            return
        if len(cleaned) < 2:
            return
        if normalized in seen:
            return
        seen.add(normalized)
        result.append((cleaned, normalized))

    for match in _CODE_RE.finditer(value):
        add(match.group(0))
    value_without_codes = _CODE_RE.sub(" ", value)

    for match in _CJK_RE.finditer(value_without_codes):
        for token in jieba.cut(match.group(0), cut_all=False):
            add(token)
    non_cjk = _CJK_RE.sub(" ", value_without_codes)
    for token in _LATIN_RE.findall(non_cjk):
        add(token)

    return result


def _normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _find_or_create_category(
    connection,
    category_ids: Dict[str, int],
    name: str,
    slug: Optional[str],
    external_type: Optional[str],
    source_domain: Optional[str],
    now: str,
) -> int:
    normalized = _normalized_name(name)
    category_id = category_ids.get(normalized)
    if category_id is not None:
        return category_id
    slug_row = None
    if slug and source_domain:
        slug_row = connection.execute(
            """
            SELECT id, name, normalized_name
            FROM categories
            WHERE source_domain = ? AND external_slug = ?
            """,
            (source_domain, slug),
        ).fetchone()
    if slug_row is not None:
        name_row = connection.execute(
            """
            SELECT id
            FROM categories
            WHERE normalized_name = ? AND id <> ?
            """,
            (normalized, int(slug_row["id"])),
        ).fetchone()
        if name_row is not None:
            category_id = int(name_row["id"])
            connection.execute(
                """
                UPDATE OR IGNORE video_categories
                SET category_id = ?
                WHERE category_id = ?
                """,
                (category_id, int(slug_row["id"])),
            )
            connection.execute(
                "DELETE FROM video_categories WHERE category_id = ?",
                (int(slug_row["id"]),),
            )
            connection.execute(
                "DELETE FROM categories WHERE id = ?",
                (int(slug_row["id"]),),
            )
            connection.execute(
                """
                UPDATE categories
                SET external_slug = ?,
                    external_type = ?,
                    source_domain = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    slug,
                    external_type,
                    source_domain,
                    now,
                    category_id,
                ),
            )
        else:
            category_id = int(slug_row["id"])
            connection.execute(
                """
                UPDATE categories
                SET name = ?, normalized_name = ?,
                    external_type = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    normalized,
                    external_type,
                    now,
                    category_id,
                ),
            )
        previous_normalized = str(
            slug_row["normalized_name"] or ""
        ).strip().casefold()
        if previous_normalized:
            category_ids[previous_normalized] = category_id
        category_ids[normalized] = category_id
        return category_id
    existing = connection.execute(
        "SELECT id FROM categories WHERE normalized_name = ?",
        (normalized,),
    ).fetchone()
    if existing:
        category_id = existing["id"]
        if slug or source_domain:
            connection.execute(
                """
                UPDATE categories
                SET external_slug = COALESCE(external_slug, ?),
                    external_type = COALESCE(external_type, ?),
                    source_domain = COALESCE(source_domain, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (slug, external_type, source_domain, now, category_id),
            )
    else:
        cursor = connection.execute(
            """
            INSERT INTO categories (
                name, normalized_name, external_slug, source_domain,
                external_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                normalized,
                slug,
                source_domain,
                external_type,
                now,
                now,
            ),
        )
        category_id = cursor.lastrowid
    category_ids[normalized] = category_id
    return category_id


def rebuild_category_index(clear: bool = False) -> int:
    """Rebuild all automatic category mappings in one transaction."""

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cache = database.category_fetch_cache_map()
    with database.connect() as connection:
        videos = connection.execute(
            """
            SELECT id, title, original_url FROM videos
            """
        ).fetchall()
        connection.execute("DELETE FROM video_categories")
        if clear:
            connection.execute("DELETE FROM categories")
        category_ids: Dict[str, int] = {}
        for video in videos:
            original_url = video["original_url"] or ""
            cache_item = cache.get(int(video["id"])) or {}
            official_categories = cache_item.get("categories") or []
            provider = metadata_provider_for_url(original_url)
            use_official = bool(
                provider
                and cache_item.get("status") == "fetched"
                and int(cache_item.get("parser_version") or 0)
                >= metadata_parser_version(provider)
                and _cache_matches_metadata_language(cache_item, provider)
                and official_categories
            )
            if use_official:
                profile = get_site_profile(provider)
                source_kind = profile.category_source_kind
                terms = [
                    (
                        item.get("name", ""),
                        item.get("slug"),
                        profile.source_domain or None,
                        item.get("type") or "category",
                    )
                    for item in official_categories
                ]
            elif provider:
                source_kind = get_site_profile(provider).category_fallback_kind
                terms = [
                    (name, None, None, None)
                    for name, _ in tokenize_title(video["title"])
                ]
            else:
                source_kind = "title"
                terms = [
                    (name, None, None, None)
                    for name, _ in tokenize_title(video["title"])
                ]

            seen_categories = set()
            for display_name, slug, source_domain, external_type in terms:
                normalized_name = _normalized_name(display_name)
                if not normalized_name or normalized_name in seen_categories:
                    continue
                seen_categories.add(normalized_name)
                category_id = _find_or_create_category(
                    connection=connection,
                    category_ids=category_ids,
                    name=display_name,
                    slug=slug,
                    external_type=external_type,
                    source_domain=source_domain,
                    now=now,
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO video_categories (
                        video_id, category_id, source_kind,
                        source_url, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        video["id"],
                        category_id,
                        source_kind,
                        original_url or None,
                        now,
                    ),
                )
    return len(category_ids)


def _find_or_create_actor(
    connection,
    actor_ids: Dict[str, int],
    name: str,
    external_id: Optional[int],
    slug: Optional[str],
    source_domain: Optional[str],
    profile_url: Optional[str],
    avatar_source_url: Optional[str],
    now: str,
) -> int:
    normalized = _normalized_name(name)
    actor_id = actor_ids.get(normalized)
    existing = connection.execute(
        """
        SELECT id, avatar_source_url
        FROM actors
        WHERE normalized_name = ?
        """,
        (normalized,),
    ).fetchone()
    if existing:
        actor_id = existing["id"]
        avatar_source = str(avatar_source_url or "").strip() or None
        avatar_changed = bool(
            avatar_source
            and avatar_source != (existing["avatar_source_url"] or "")
        )
        connection.execute(
            """
            UPDATE actors
            SET external_id = COALESCE(?, external_id),
                external_slug = COALESCE(external_slug, ?),
                source_domain = COALESCE(source_domain, ?),
                profile_url = COALESCE(profile_url, ?),
                avatar_source_url = COALESCE(?, avatar_source_url),
                avatar_status = CASE
                    WHEN ? THEN 'pending'
                    ELSE avatar_status
                END,
                avatar_error = CASE
                    WHEN ? THEN NULL
                    ELSE avatar_error
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                int(external_id) if external_id is not None else None,
                slug,
                source_domain,
                profile_url,
                avatar_source,
                1 if avatar_changed else 0,
                1 if avatar_changed else 0,
                now,
                actor_id,
            ),
        )
    else:
        cursor = connection.execute(
            """
            INSERT INTO actors (
                name, normalized_name, external_id, external_slug,
                source_domain, profile_url, avatar_source_url,
                avatar_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                name,
                normalized,
                int(external_id) if external_id is not None else None,
                slug,
                source_domain,
                profile_url,
                avatar_source_url,
                now,
                now,
            ),
        )
        actor_id = cursor.lastrowid
    actor_ids[normalized] = actor_id
    return actor_id


def rebuild_actor_index(clear: bool = False) -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cache = database.category_fetch_cache_map()
    with database.connect() as connection:
        videos = connection.execute(
            """
            SELECT id, original_url FROM videos
            """
        ).fetchall()
        connection.execute("DELETE FROM video_actors")
        avatar_snapshot = {}
        if clear:
            avatar_snapshot = {
                row["normalized_name"]: dict(row)
                for row in connection.execute(
                    """
                    SELECT normalized_name, external_id,
                           avatar_source_url, avatar_path, avatar_status,
                           avatar_error, avatar_fetched_at, avatar_attempts
                    FROM actors
                    WHERE avatar_source_url IS NOT NULL
                       OR avatar_path IS NOT NULL
                    """
                ).fetchall()
            }
            connection.execute("DELETE FROM actors")
        actor_ids: Dict[str, int] = {}
        for video in videos:
            item = cache.get(int(video["id"])) or {}
            original_url = video["original_url"] or ""
            provider = metadata_provider_for_url(original_url)
            if (
                item.get("status") != "fetched"
                or not provider
                or int(item.get("parser_version") or 0)
                < metadata_parser_version(provider)
                or not _cache_matches_metadata_language(item, provider)
            ):
                continue
            source_domain = (
                get_site_profile(provider).source_domain or None
            )
            for actor in item.get("actors") or []:
                name = actor.get("name", "")
                actor_id = _find_or_create_actor(
                    connection=connection,
                    actor_ids=actor_ids,
                    name=name,
                    external_id=actor.get("external_id"),
                    slug=actor.get("slug"),
                    source_domain=source_domain,
                    profile_url=actor.get("url"),
                    avatar_source_url=actor.get("avatar_url"),
                    now=now,
                )
                previous_avatar = avatar_snapshot.get(
                    _normalized_name(name)
                )
                if previous_avatar:
                    current = connection.execute(
                        """
                        SELECT avatar_source_url
                        FROM actors
                        WHERE id = ?
                        """,
                        (actor_id,),
                    ).fetchone()
                    current_source = (
                        (current["avatar_source_url"] or "")
                        if current
                        else ""
                    )
                    previous_source = (
                        previous_avatar.get("avatar_source_url") or ""
                    )
                    if not current_source or current_source == previous_source:
                        connection.execute(
                            """
                            UPDATE actors
                            SET external_id = COALESCE(external_id, ?),
                                avatar_source_url = COALESCE(
                                    NULLIF(avatar_source_url, ''),
                                    ?
                                ),
                                avatar_path = ?,
                                avatar_status = ?,
                                avatar_error = ?,
                                avatar_fetched_at = ?,
                                avatar_attempts = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                previous_avatar.get("external_id"),
                                previous_source or None,
                                previous_avatar.get("avatar_path"),
                                previous_avatar.get("avatar_status")
                                or "pending",
                                previous_avatar.get("avatar_error"),
                                previous_avatar.get("avatar_fetched_at"),
                                int(
                                    previous_avatar.get("avatar_attempts")
                                    or 0
                                ),
                                now,
                                actor_id,
                            ),
                        )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO video_actors (
                        video_id, actor_id, source_domain,
                        source_url, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        video["id"],
                        actor_id,
                        source_domain,
                        original_url or None,
                        now,
                    ),
                )
    return len(actor_ids)


def replace_video_metadata(
    video_id: int,
    categories: List[Dict],
    actors: List[Dict],
    provider: str,
    source_url: str,
) -> Dict:
    """Replace one video's metadata after a successful source fetch."""

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    profile = get_site_profile(provider)
    source_domain = profile.source_domain or None
    source_kind = profile.category_source_kind
    category_ids: Dict[str, int] = {}
    actor_ids: Dict[str, int] = {}
    with database.connect() as connection:
        video = connection.execute(
            "SELECT id, original_url FROM videos "
            "WHERE id = ? AND missing = 0 AND is_deleted = 0",
            (int(video_id),),
        ).fetchone()
        if not video:
            raise ValueError("视频不存在")
        connection.execute(
            "DELETE FROM video_categories WHERE video_id = ?",
            (int(video_id),),
        )
        connection.execute(
            "DELETE FROM video_actors WHERE video_id = ?",
            (int(video_id),),
        )
        seen_categories = set()
        for item in categories:
            name = item.get("name", "")
            normalized_name = _normalized_name(name)
            if not normalized_name or normalized_name in seen_categories:
                continue
            seen_categories.add(normalized_name)
            category_id = _find_or_create_category(
                connection=connection,
                category_ids=category_ids,
                name=name,
                slug=item.get("slug"),
                external_type=item.get("type") or "tag",
                source_domain=source_domain,
                now=now,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO video_categories (
                    video_id, category_id, source_kind,
                    source_url, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(video_id),
                    category_id,
                    source_kind,
                    source_url or video["original_url"],
                    now,
                ),
            )
        seen_actors = set()
        for item in actors:
            name = item.get("name", "")
            normalized_name = _normalized_name(name)
            if not normalized_name or normalized_name in seen_actors:
                continue
            seen_actors.add(normalized_name)
            actor_id = _find_or_create_actor(
                connection=connection,
                actor_ids=actor_ids,
                name=name,
                external_id=item.get("external_id"),
                slug=item.get("slug"),
                source_domain=source_domain,
                profile_url=item.get("url"),
                avatar_source_url=item.get("avatar_url"),
                now=now,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO video_actors (
                    video_id, actor_id, source_domain,
                    source_url, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(video_id),
                    actor_id,
                    source_domain,
                    source_url or video["original_url"],
                    now,
                ),
            )
    return {
        "categories": database.categories_for_video(video_id),
        "actors": database.actors_for_video(video_id),
    }


def refresh_site_metadata(
    provider: Optional[str] = None,
    video_id: Optional[int] = None,
    url: Optional[str] = None,
    limit: Optional[int] = None,
    refresh: bool = False,
    retry_failed: bool = False,
    logger: Optional[logging.Logger] = None,
    stop_event=None,
) -> Dict:
    logger = logger or logging.getLogger(__name__)
    clauses = [
        "missing = 0",
        "is_deleted = 0",
        "original_url IS NOT NULL",
        "original_url <> ''",
        """
        (
            NOT EXISTS (
                SELECT 1 FROM video_episodes episode
                WHERE episode.video_id = videos.id
            )
            OR EXISTS (
                SELECT 1
                FROM video_episodes episode
                JOIN video_seasons season
                  ON season.id = episode.season_id
                WHERE episode.video_id = videos.id
                  AND season.metadata_video_id = videos.id
            )
        )
        """,
    ]
    params: List = []
    if video_id is not None:
        clauses.append("id = ?")
        params.append(int(video_id))
    if url:
        clauses.append("original_url = ?")
        params.append(str(url).strip())
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, title, original_url
            FROM videos
            WHERE {}
            ORDER BY id ASC
            """.format(" AND ".join(clauses)),
            params,
        ).fetchall()
    cache = database.category_fetch_cache_map()
    targets = []
    skipped = 0
    for row in rows:
        source_url = row["original_url"] or ""
        detected = metadata_provider_for_url(source_url)
        if not detected or (provider and detected != provider):
            continue
        cached = cache.get(int(row["id"])) or {}
        ready = bool(
            not refresh
            and cached.get("source_url") == source_url
            and cached.get("status") == "fetched"
            and int(cached.get("parser_version") or 0)
            >= metadata_parser_version(detected)
            and _cache_matches_metadata_language(cached, detected)
            and cached.get("categories_json") is not None
            and cached.get("actors_json") is not None
        )
        if ready:
            skipped += 1
            continue
        if (
            not refresh
            and not retry_failed
            and cached.get("source_url") == source_url
            and cached.get("status") == "failed"
            and int(cached.get("parser_version") or 0)
            >= metadata_parser_version(detected)
            and _cache_matches_metadata_language(cached, detected)
        ):
            skipped += 1
            continue
        targets.append((dict(row), detected))
        if limit is not None and len(targets) >= int(limit):
            break

    report = {
        "candidates": len(targets),
        "succeeded": 0,
        "failed": 0,
        "skipped": skipped,
        "xhamster": 0,
        "xvideos": 0,
        "actors_extracted": 0,
        "actors_with_avatars": 0,
        "errors": [],
    }
    for index, (video, detected) in enumerate(targets, start=1):
        if stop_event is not None and stop_event.is_set():
            break
        result = fetch_site_metadata(video["original_url"], logger=logger)
        if result.categories or result.actors:
            database.save_category_fetch_cache(
                video_id=video["id"],
                source_url=video["original_url"],
                status="fetched",
                categories=result.categories,
                actors=result.actors,
                http_status=result.http_status,
                parser_version=metadata_parser_version(detected),
                actor_metadata_version=database.ACTOR_METADATA_VERSION,
                metadata_language=result.language,
            )
            report["succeeded"] += 1
            report[detected] = report.get(detected, 0) + 1
            report["actors_extracted"] += len(result.actors)
            report["actors_with_avatars"] += sum(
                1 for actor in result.actors if actor.get("avatar_url")
            )
            logger.info(
                "Site metadata %s/%s video_id=%s provider=%s "
                "categories=%s actors=%s",
                index,
                len(targets),
                video["id"],
                detected,
                len(result.categories),
                len(result.actors),
            )
        else:
            database.save_category_fetch_cache(
                video_id=video["id"],
                source_url=video["original_url"],
                status="failed",
                actors=[],
                http_status=result.http_status,
                error=result.error,
                parser_version=metadata_parser_version(detected),
                actor_metadata_version=0,
                metadata_language=result.language,
            )
            report["failed"] += 1
            report["errors"].append(
                {
                    "video_id": video["id"],
                    "provider": detected,
                    "url": video["original_url"],
                    "http_status": result.http_status,
                    "error": result.error,
                }
            )
    if report["succeeded"]:
        report["categories"] = rebuild_category_index(clear=False)
        report["actors"] = rebuild_actor_index(clear=False)
    else:
        report["categories"] = 0
        report["actors"] = 0
    return report


def fetch_xhamster_categories(
    limit: Optional[int] = None,
    refresh: bool = False,
    retry_failed: bool = False,
    workers: int = 1,
    logger: Optional[logging.Logger] = None,
    stop_event=None,
) -> Dict:
    logger = logger or logging.getLogger(__name__)
    cache = database.category_fetch_cache_map()
    with database.connect() as connection:
        videos = connection.execute(
            """
            SELECT id, original_url FROM videos
            WHERE missing = 0 AND is_deleted = 0
              AND original_url IS NOT NULL
              AND original_url <> ''
            ORDER BY id ASC
            """
        ).fetchall()
    candidates = []
    skipped = 0
    for video in videos:
        url = video["original_url"]
        if resolve_site_profile(url).key != "xhamster":
            continue
        item = cache.get(int(video["id"])) or {}
        same_url = item.get("source_url") == url
        actors_cached = item.get("actors_json") is not None
        parser_current = (
            int(item.get("parser_version") or 0)
            >= database.CATEGORY_PARSER_VERSION
        )
        actor_metadata_current = (
            int(item.get("actor_metadata_version") or 0)
            >= database.ACTOR_METADATA_VERSION
        )
        if (
            not refresh
            and same_url
            and parser_current
            and actor_metadata_current
            and item.get("status") == "fetched"
            and item.get("categories_json") is not None
            and actors_cached
        ):
            skipped += 1
            continue
        if (
            not refresh
            and not retry_failed
            and same_url
            and parser_current
            and item.get("status") == "failed"
        ):
            skipped += 1
            continue
        candidates.append(video)
        if limit is not None and len(candidates) >= int(limit):
            break

    report = {
        "xhamster_videos": sum(
            1
            for video in videos
            if resolve_site_profile(video["original_url"]).key == "xhamster"
        ),
        "candidates": len(candidates),
        "succeeded": 0,
        "failed": 0,
        "skipped": skipped,
        "dom_categories": 0,
        "meta_categories": 0,
        "actor_pages": 0,
        "actors_extracted": 0,
        "actors_with_avatars": 0,
        "workers": max(1, min(2, int(workers))),
        "errors": [],
    }
    if not candidates:
        return report

    try:
        with XHamsterCategoryFetcher(logger=logger) as fetcher:
            batch_size = report["workers"]
            for offset in range(0, len(candidates), batch_size):
                if stop_event is not None and stop_event.is_set():
                    break
                batch = candidates[offset:offset + batch_size]
                results = fetcher.fetch_many(
                    [video["original_url"] for video in batch],
                    concurrency=batch_size,
                )
                for index, (video, result) in enumerate(
                    zip(batch, results),
                    start=offset + 1,
                ):
                    url = video["original_url"]
                    logger.info(
                        "xHamster categories %s/%s video=%s status=%s count=%s",
                        index,
                        len(candidates),
                        video["id"],
                        result.http_status,
                        len(result.categories),
                    )
                    if result.categories or result.actors:
                        database.save_category_fetch_cache(
                            video_id=video["id"],
                            source_url=url,
                            status="fetched",
                            categories=result.categories,
                            actors=result.actors,
                            http_status=result.http_status,
                            actor_metadata_version=(
                                database.ACTOR_METADATA_VERSION
                            ),
                            metadata_language=getattr(
                                result,
                                "language",
                                "",
                            ),
                        )
                        report["succeeded"] += 1
                        if result.actors:
                            report["actor_pages"] = (
                                report.get("actor_pages", 0) + 1
                            )
                            report["actors_extracted"] = (
                                report.get("actors_extracted", 0)
                                + len(result.actors)
                            )
                            report["actors_with_avatars"] += sum(
                                1
                                for actor in result.actors
                                if actor.get("avatar_url")
                            )
                        report["{}_categories".format(result.source)] = (
                            report.get(
                                "{}_categories".format(result.source),
                                0,
                            ) + 1
                        )
                    else:
                        database.save_category_fetch_cache(
                            video_id=video["id"],
                            source_url=url,
                            status="failed",
                            actors=[],
                            http_status=result.http_status,
                            error=result.error,
                            actor_metadata_version=0,
                            metadata_language=getattr(
                                result,
                                "language",
                                "",
                            ),
                        )
                        report["failed"] += 1
                        report["errors"].append({
                            "video_id": video["id"],
                            "url": url,
                            "http_status": result.http_status,
                            "error": result.error,
                        })
                time.sleep(random.uniform(0.5, 1.5))
    except Exception as exc:
        logger.exception("xHamster category fetcher failed")
        report["failed"] += max(0, len(candidates) - report["succeeded"])
        report["errors"].append({"error": str(exc)})
    return report
