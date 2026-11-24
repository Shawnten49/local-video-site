from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from local_video_site.core import config
from ..db.connection import connect
from ..db.transaction import now_iso as _now
from local_video_site.episodes.naming import is_generic_episode_title, meaningful_episode_title, season_number_from_label
from local_video_site.core.search import parse_search_query
from .videos import video_repository, video_with_preview_state


def repair_confirmed_episode_batch_selections() -> Dict:
    now = _now()
    selected_batches = 0
    selected_items = 0
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batches = connection.execute(
            """
            SELECT batch_id
            FROM episode_download_batches
            WHERE status = 'confirmed'
            ORDER BY id
            """
        ).fetchall()
        for batch in batches:
            batch_id = str(batch["batch_id"])
            connection.execute(
                """
                UPDATE episode_download_items
                SET download_task_id = (
                    SELECT task.id
                    FROM download_tasks task
                    WHERE task.normalized_metadata_url =
                        episode_download_items.normalized_source_url
                      AND task.deleted_at IS NULL
                    ORDER BY task.id DESC
                    LIMIT 1
                )
                WHERE batch_id = ?
                  AND download_task_id IS NULL
                """,
                (batch_id,),
            )
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(
                           CASE WHEN download_task_id IS NOT NULL
                           THEN 1 ELSE 0 END
                       ) AS linked,
                       SUM(
                           CASE
                               WHEN (
                                   download_task_id IS NOT NULL
                               ) <> (selected = 1)
                               THEN 1 ELSE 0
                           END
                       ) AS mismatches
                FROM episode_download_items
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            total = int(counts["total"] or 0)
            linked = int(counts["linked"] or 0)
            mismatches = int(counts["mismatches"] or 0)
            if (
                linked <= 0
                or linked >= total
                or mismatches <= 0
            ):
                continue
            connection.execute(
                """
                UPDATE episode_download_items
                SET selected = CASE
                        WHEN download_task_id IS NOT NULL THEN 1
                        ELSE 0
                    END,
                    updated_at = ?
                WHERE batch_id = ?
                """,
                (now, batch_id),
            )
            selected_batches += 1
            selected_items += linked
        deleted = connection.execute(
            """
            DELETE FROM video_episodes
            WHERE status = 'planned'
              AND video_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM episode_download_items unselected
                  JOIN episode_download_batches unselected_batch
                    ON unselected_batch.batch_id = unselected.batch_id
                  WHERE unselected.normalized_source_url =
                        video_episodes.normalized_source_url
                    AND unselected.selected = 0
                    AND unselected_batch.status = 'confirmed'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM episode_download_items selected
                  JOIN episode_download_batches selected_batch
                    ON selected_batch.batch_id = selected.batch_id
                  WHERE selected.normalized_source_url =
                        video_episodes.normalized_source_url
                    AND selected.selected = 1
                    AND selected_batch.status = 'confirmed'
              )
            """
        )
        connection.commit()
    return {
        "selected_batches": selected_batches,
        "selected_items": selected_items,
        "episodes_removed": max(0, deleted.rowcount),
    }
def reconcile_episode_batch_library() -> Dict:
    """Repair seasons and episode links from confirmed batch records."""

    now = _now()
    repaired_seasons = 0
    repaired_episodes = 0
    skipped_source_conflicts = 0
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batches = connection.execute(
            """
            SELECT * FROM episode_download_batches
            WHERE status = 'confirmed'
            ORDER BY id
            """
        ).fetchall()
        for batch in batches:
            normalized_series = unicodedata.normalize(
                "NFKC",
                str(batch["series_name"]),
            ).strip().casefold()
            series = connection.execute(
                """
                SELECT * FROM video_series
                WHERE provider = ? AND normalized_name = ?
                """,
                (batch["provider"], normalized_series),
            ).fetchone()
            if series is None:
                continue
            season_number = season_number_from_label(
                batch["season_name"],
                int(batch["season_number"]),
            )
            relative_directory = str(
                Path(batch["series_name"]) / batch["season_name"]
            )
            season = connection.execute(
                """
                SELECT * FROM video_seasons
                WHERE series_id = ? AND relative_directory = ?
                """,
                (int(series["id"]), relative_directory),
            ).fetchone()
            same_number = connection.execute(
                """
                SELECT * FROM video_seasons
                WHERE series_id = ? AND season_number = ?
                """,
                (int(series["id"]), int(season_number)),
            ).fetchone()
            if (
                season is not None
                and same_number is not None
                and int(season["id"]) != int(same_number["id"])
            ):
                continue
            if season is None and same_number is not None:
                if same_number["relative_directory"] != relative_directory:
                    conflicting_number = season_number_from_label(
                        same_number["name"],
                        int(same_number["season_number"]),
                    )
                    occupied = connection.execute(
                        """
                        SELECT 1 FROM video_seasons
                        WHERE series_id = ? AND season_number = ?
                          AND id <> ?
                        """,
                        (
                            int(series["id"]),
                            int(conflicting_number),
                            int(same_number["id"]),
                        ),
                    ).fetchone()
                    if (
                        conflicting_number == int(season_number)
                        or occupied is not None
                    ):
                        continue
                    connection.execute(
                        """
                        UPDATE video_seasons
                        SET season_number = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            int(conflicting_number),
                            now,
                            int(same_number["id"]),
                        ),
                    )
                    repaired_seasons += 1
                    same_number = None
                else:
                    season = same_number
            if season is None:
                cursor = connection.execute(
                    """
                    INSERT INTO video_seasons (
                        series_id, name, season_number,
                        relative_directory, source_url,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(series["id"]),
                        batch["season_name"],
                        int(season_number),
                        relative_directory,
                        batch["first_episode_url"],
                        now,
                        now,
                    ),
                )
                season_id = int(cursor.lastrowid or 0)
                repaired_seasons += 1
            else:
                season_id = int(season["id"])
                if (
                    int(season["season_number"]) != int(season_number)
                    or season["name"] != batch["season_name"]
                    or season["relative_directory"] != relative_directory
                ):
                    connection.execute(
                        """
                        UPDATE video_seasons
                        SET name = ?, season_number = ?,
                            relative_directory = ?, source_url = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            batch["season_name"],
                            int(season_number),
                            relative_directory,
                            batch["first_episode_url"],
                            now,
                            season_id,
                        ),
                    )
                    repaired_seasons += 1
            if int(batch["season_number"]) != int(season_number):
                connection.execute(
                    """
                    UPDATE episode_download_batches
                    SET season_number = ?, updated_at = ?
                    WHERE batch_id = ?
                    """,
                    (int(season_number), now, batch["batch_id"]),
                )
            items = connection.execute(
                """
                SELECT i.*, t.video_id, t.file_path
                FROM episode_download_items i
                LEFT JOIN download_tasks t
                  ON t.id = i.download_task_id
                WHERE i.batch_id = ?
                  AND i.selected = 1
                ORDER BY i.episode_number
                """,
                (batch["batch_id"],),
            ).fetchall()
            representative_id = None
            metadata_id = None
            for item in items:
                episode = connection.execute(
                    """
                    SELECT * FROM video_episodes
                    WHERE season_id = ? AND episode_number = ?
                    """,
                    (season_id, int(item["episode_number"])),
                ).fetchone()
                source_owner = connection.execute(
                    """
                    SELECT id, season_id, episode_number
                    FROM video_episodes
                    WHERE normalized_source_url = ?
                    LIMIT 1
                    """,
                    (item["normalized_source_url"],),
                ).fetchone()
                if source_owner is not None and (
                    episode is None
                    or int(source_owner["id"]) != int(episode["id"])
                ):
                    # The same Bilibili episode can be referenced by duplicate
                    # or incorrectly named batches. Keep its original season
                    # association instead of aborting the entire reconciliation.
                    skipped_source_conflicts += 1
                    continue
                video_id = (
                    int(item["video_id"])
                    if item["video_id"] is not None
                    else None
                )
                status = "available" if video_id is not None else "planned"
                if episode is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO video_episodes (
                            season_id, episode_number, display_title,
                            episode_title, source_url, normalized_source_url,
                            relative_path, absolute_path, video_id,
                            download_task_id, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            season_id,
                            int(item["episode_number"]),
                            item["display_title"],
                            item["episode_title"],
                            item["source_url"],
                            item["normalized_source_url"],
                            item["relative_path"],
                            item["absolute_path"],
                            video_id,
                            item["download_task_id"],
                            status,
                            now,
                            now,
                        ),
                    )
                    episode_id = int(cursor.lastrowid or 0)
                    repaired_episodes += 1
                else:
                    episode_id = int(episode["id"])
                    connection.execute(
                        """
                        UPDATE video_episodes
                        SET display_title = ?,
                            episode_title = COALESCE(
                                ?,
                                episode_title
                            ),
                            source_url = ?,
                            normalized_source_url = ?, relative_path = ?,
                            absolute_path = ?,
                            video_id = COALESCE(?, video_id),
                            download_task_id = COALESCE(
                                ?,
                                download_task_id
                            ),
                            status = CASE
                                WHEN ? IS NOT NULL THEN 'available'
                                ELSE status
                            END,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            item["display_title"],
                            item["episode_title"],
                            item["source_url"],
                            item["normalized_source_url"],
                            item["relative_path"],
                            item["absolute_path"],
                            video_id,
                            item["download_task_id"],
                            video_id,
                            now,
                            episode_id,
                        ),
                    )
                if item["download_task_id"] is not None:
                    connection.execute(
                        """
                        UPDATE download_tasks
                        SET episode_id = ?, episode_batch_id = ?,
                            display_title = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            episode_id,
                            batch["batch_id"],
                            item["display_title"],
                            now,
                            int(item["download_task_id"]),
                        ),
                    )
                if video_id is not None and representative_id is None:
                    representative_id = video_id
                if (
                    video_id is not None
                    and int(item["episode_number"]) == 1
                ):
                    representative_id = video_id
                    metadata_id = video_id
            connection.execute(
                """
                UPDATE video_seasons
                SET representative_video_id = COALESCE(
                        representative_video_id,
                        ?
                    ),
                    metadata_video_id = COALESCE(metadata_video_id, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    representative_id,
                    metadata_id or representative_id,
                    now,
                    season_id,
                ),
            )
        connection.commit()
    return {
        "seasons": repaired_seasons,
        "episodes": repaired_episodes,
        "source_conflicts": skipped_source_conflicts,
    }
def create_episode_download_batch(
    batch_id: str,
    series_name: str,
    season_name: str,
    season_number: int,
    first_episode_url: str,
    provider: str,
    output_root: str,
    quality: str,
    items: List[Dict],
    file_name_mode: str = "sequence",
    access_level: int = 0,
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
) -> None:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO episode_download_batches (
                batch_id, series_name, season_name, season_number,
                first_episode_url, provider, output_root, quality,
                file_name_mode,
                access_level, created_by_user_id, created_by_username,
                status, item_count, revision,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preview', ?, 1, ?, ?
            )
            """,
            (
                batch_id,
                series_name,
                season_name,
                int(season_number),
                first_episode_url,
                provider,
                output_root,
                quality,
                file_name_mode,
                int(access_level),
                created_by_user_id,
                created_by_username,
                len(items),
                now,
                now,
            ),
        )
        for item in items:
            connection.execute(
                """
                INSERT INTO episode_download_items (
                    batch_id, episode_number, source_url,
                    normalized_source_url, display_title, episode_title,
                    file_name,
                    relative_path, absolute_path, selected,
                    validation_status, validation_message,
                    source_tab_key, source_tab_title,
                    source_tab_item_title,
                    source_season_name, source_episode_number,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    batch_id,
                    int(item["episode_number"]),
                    item["source_url"],
                    item["normalized_source_url"],
                    item["display_title"],
                    item.get("episode_title"),
                    item["file_name"],
                    item["relative_path"],
                    item["absolute_path"],
                    1 if item.get("selected") else 0,
                    item["validation_status"],
                    item.get("validation_message"),
                    item.get("source_tab_key"),
                    item.get("source_tab_title"),
                    item.get("source_tab_item_title"),
                    item.get("source_season_name"),
                    item.get("source_episode_number"),
                    now,
                    now,
                ),
            )
        connection.commit()
def set_episode_download_selection(
    batch_id: str,
    selected_item_ids: List[int],
) -> None:
    selected_ids = sorted({int(value) for value in selected_item_ids})
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE episode_download_items
            SET selected = 0, updated_at = ?
            WHERE batch_id = ?
            """,
            (now, batch_id),
        )
        if selected_ids:
            placeholders = ", ".join("?" for _ in selected_ids)
            connection.execute(
                """
                UPDATE episode_download_items
                SET selected = 1, updated_at = ?
                WHERE batch_id = ? AND id IN ({})
                """.format(placeholders),
                [now, batch_id, *selected_ids],
            )
        connection.commit()
def get_episode_download_batch(batch_id: str) -> Optional[Dict]:
    with connect() as connection:
        batch = connection.execute(
            "SELECT * FROM episode_download_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if batch is None:
            return None
        items = connection.execute(
            """
            SELECT * FROM episode_download_items
            WHERE batch_id = ?
            ORDER BY episode_number
            """,
            (batch_id,),
        ).fetchall()
    result = dict(batch)
    result["items"] = [
        {
            **dict(item),
            "file_name_mode": batch["file_name_mode"],
        }
        for item in items
    ]
    return result
def update_episode_download_batch(
    batch_id: str,
    **fields,
) -> None:
    allowed = {
        "status",
        "error_code",
        "error_detail",
        "confirmed_at",
        "updated_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values.setdefault("updated_at", _now())
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with connect() as connection:
        connection.execute(
            "UPDATE episode_download_batches SET {} WHERE batch_id = ?".format(
                assignments
            ),
            [*values.values(), batch_id],
        )
def create_episode_download_discovery(
    discovery_id: str,
    provider: str,
    source_url: str,
    series_name: str,
    output_root: str,
    quality: str,
    access_level: int,
    mode: str,
    aggregate_single_season: bool,
    preserve_original_name: bool,
    file_name_mode: str,
    target_season_name: Optional[str],
    target_season_number: Optional[int],
    tabs: List[Dict],
    source_item_count: int,
    item_count: int,
    complete: bool,
    warning: str = "",
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
) -> None:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO episode_download_discoveries (
                discovery_id, provider, source_url, series_name,
                output_root, quality, access_level, mode,
                aggregate_single_season, preserve_original_name,
                file_name_mode, target_season_name,
                target_season_number, status, revision, tab_count,
                source_item_count, item_count, complete, warning,
                created_by_user_id, created_by_username,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preview', 1,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                discovery_id,
                provider,
                source_url,
                series_name,
                output_root,
                quality,
                int(access_level),
                mode,
                1 if aggregate_single_season else 0,
                1 if preserve_original_name else 0,
                file_name_mode,
                target_season_name,
                (
                    int(target_season_number)
                    if target_season_number is not None
                    else None
                ),
                len(tabs),
                int(source_item_count),
                int(item_count),
                1 if complete else 0,
                warning or None,
                created_by_user_id,
                created_by_username,
                now,
                now,
            ),
        )
        for tab in tabs:
            connection.execute(
                """
                INSERT INTO episode_download_discovery_tabs (
                    discovery_id, tab_sequence, tab_key, tab_title,
                    season_name, season_number, source_url,
                    source_item_count, batch_id, status, warning,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    discovery_id,
                    int(tab["tab_sequence"]),
                    tab["tab_key"],
                    tab["tab_title"],
                    tab["season_name"],
                    int(tab["season_number"]),
                    tab["source_url"],
                    int(tab.get("source_item_count") or 0),
                    tab.get("batch_id"),
                    tab.get("status") or "ready",
                    tab.get("warning") or None,
                    now,
                    now,
                ),
            )
        connection.commit()
def get_episode_download_discovery(
    discovery_id: str,
) -> Optional[Dict]:
    with connect() as connection:
        discovery = connection.execute(
            """
            SELECT * FROM episode_download_discoveries
            WHERE discovery_id = ?
            """,
            (discovery_id,),
        ).fetchone()
        if discovery is None:
            return None
        tabs = connection.execute(
            """
            SELECT * FROM episode_download_discovery_tabs
            WHERE discovery_id = ?
            ORDER BY tab_sequence
            """,
            (discovery_id,),
        ).fetchall()
    result = dict(discovery)
    groups = []
    flattened = []
    batch_cache = {}
    attached_item_ids = set()
    for tab in tabs:
        group = dict(tab)
        batch_id = str(group.get("batch_id") or "")
        batch = None
        if batch_id:
            if batch_id not in batch_cache:
                batch_cache[batch_id] = get_episode_download_batch(
                    batch_id
                )
            batch = batch_cache[batch_id]
        group["items"] = []
        for item in list((batch or {}).get("items") or []):
            item_id = int(item["id"])
            if item_id in attached_item_ids:
                continue
            attached_item_ids.add(item_id)
            group["items"].append(item)
            flattened.append(item)
        groups.append(group)
    result["tabs"] = groups
    result["groups"] = groups
    result["items"] = flattened
    return result
def update_episode_download_discovery(
    discovery_id: str,
    **fields,
) -> None:
    allowed = {
        "status",
        "error_code",
        "error_detail",
        "confirmed_at",
        "updated_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values.setdefault("updated_at", _now())
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with connect() as connection:
        connection.execute(
            """
            UPDATE episode_download_discoveries
            SET {}
            WHERE discovery_id = ?
            """.format(assignments),
            [*values.values(), discovery_id],
        )
