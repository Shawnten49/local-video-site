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


def upsert_video_season(
    series_name: str,
    season_name: str,
    season_number: int,
    provider: str,
    source_url: str,
    relative_directory: str,
) -> Dict:
    now = _now()
    season_number = season_number_from_label(season_name, season_number)
    normalized_name = unicodedata.normalize(
        "NFKC",
        series_name,
    ).strip().casefold()
    normalized_season_name = unicodedata.normalize(
        "NFKC",
        season_name,
    ).strip().casefold()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT OR IGNORE INTO video_series (
                name, normalized_name, provider, source_url,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                series_name,
                normalized_name,
                provider,
                source_url,
                now,
                now,
            ),
        )
        series = connection.execute(
            """
            SELECT * FROM video_series
            WHERE provider = ? AND normalized_name = ?
            """,
            (provider, normalized_name),
        ).fetchone()
        if int(series["is_deleted"] or 0):
            connection.execute(
                """
                UPDATE video_series
                SET is_deleted = 0, deleted_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, int(series["id"])),
            )
            series = connection.execute(
                "SELECT * FROM video_series WHERE id = ?",
                (int(series["id"]),),
            ).fetchone()
        seasons = connection.execute(
            "SELECT * FROM video_seasons WHERE series_id = ? ORDER BY id",
            (int(series["id"]),),
        ).fetchall()
        same_number = next(
            (
                row for row in seasons
                if int(row["season_number"]) == int(season_number)
            ),
            None,
        )
        same_name = next(
            (
                row for row in seasons
                if unicodedata.normalize(
                    "NFKC",
                    str(row["name"]),
                ).strip().casefold() == normalized_season_name
            ),
            None,
        )
        if (
            same_number is not None
            and same_name is not None
            and int(same_number["id"]) != int(same_name["id"])
        ):
            connection.rollback()
            raise ValueError(
                "季序号 {} 已被“{}”使用，不能用于“{}”".format(
                    season_number,
                    same_number["name"],
                    season_name,
                )
            )
        target = same_name or same_number
        if target is not None:
            if (
                same_number is not None
                and int(same_number["id"]) != int(target["id"])
            ):
                connection.rollback()
                raise ValueError(
                    "季序号 {} 已被“{}”使用".format(
                        season_number,
                        same_number["name"],
                    )
                )
            connection.execute(
                """
                UPDATE video_seasons
                SET name = ?, season_number = ?, relative_directory = ?,
                    source_url = ?, is_deleted = 0, deleted_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    season_name,
                    int(season_number),
                    relative_directory,
                    source_url,
                    now,
                    int(target["id"]),
                ),
            )
            season_id = int(target["id"])
        else:
            cursor = connection.execute(
                """
                INSERT INTO video_seasons (
                    series_id, name, season_number, relative_directory,
                    source_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(series["id"]),
                    season_name,
                    int(season_number),
                    relative_directory,
                    source_url,
                    now,
                    now,
                ),
            )
            season_id = int(cursor.lastrowid or 0)
        season = connection.execute(
            "SELECT * FROM video_seasons WHERE id = ?",
            (season_id,),
        ).fetchone()
        connection.commit()
    return {
        "series": dict(series),
        "season": dict(season),
    }
