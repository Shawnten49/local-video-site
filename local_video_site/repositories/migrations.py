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


def migration_candidate_page(
    direction: str,
    query: str = "",
    page: int = 1,
    page_size: int = 40,
) -> Dict:
    if direction not in {"to_external", "to_local"}:
        raise ValueError("不支持的迁移方向")
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    clauses = [
        "v.is_deleted = 0",
        "v.missing = 0",
        "v.preview_status NOT IN ('pending', 'processing')",
        "v.metadata_status NOT IN ('pending', 'processing')",
        """
        NOT EXISTS (
            SELECT 1 FROM video_migrations vm
            WHERE vm.video_id = v.id
              AND vm.status IN ('pending', 'running', 'needs_cleanup')
        )
        """,
        """
        NOT EXISTS (
            SELECT 1 FROM download_tasks dt
            WHERE dt.video_id = v.id
              AND dt.status IN (
                  'pending', 'running', 'pausing', 'paused', 'deleting'
              )
        )
        """,
        """
        NOT EXISTS (
            SELECT 1 FROM video_rename_operations vr
            WHERE vr.video_id = v.id
        )
        """,
    ]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("v.id = ?")
            params.append(parsed_query.id_value)
    elif parsed_query.raw:
        escaped = (
            parsed_query.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                v.title LIKE ? ESCAPE '\\'
                OR v.default_title LIKE ? ESCAPE '\\'
                OR v.path LIKE ? ESCAPE '\\'
                OR COALESCE(v.original_url, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        params.extend([
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
        ])
    if direction == "to_local":
        clauses.extend([
            "v.storage_volume_id IS NOT NULL",
            "sv.online_state = 'online'",
        ])
    else:
        clauses.append("v.storage_volume_id IS NULL")
        root_clauses = []
        for root in config.configured_roots():
            root_text = str(root.resolve()).rstrip("/")
            prefixes = {root_text}
            if root_text.startswith("/private/"):
                prefixes.add(root_text[len("/private"):])
            for prefix in sorted(prefixes):
                root_clauses.append(
                    "(v.path = ? OR substr(v.path, 1, ?) = ?)"
                )
                params.extend([
                    prefix,
                    len(prefix) + 1,
                    prefix + "/",
                ])
        if root_clauses:
            clauses.append("({})".format(" OR ".join(root_clauses)))
        else:
            clauses.append("0 = 1")
    where = " AND ".join(clauses)
    with connect() as connection:
        total = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            """.format(where),
            params,
        ).fetchone()[0])
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT v.*,
                   sv.mount_path AS storage_mount_path,
                   sv.root_relative_path AS storage_root_relative_path
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
def get_videos_by_ids(video_ids: List[int]) -> List[Dict]:
    normalized = sorted({
        int(value) for value in video_ids if value is not None
    })
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT v.*,
                   sv.mount_path AS storage_mount_path,
                   sv.root_relative_path AS storage_root_relative_path
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE v.id IN ({})
            """.format(placeholders),
            normalized,
        ).fetchall()
    return [dict(row) for row in rows]
def auto_migration_video_ids(cutoff_at: str) -> List[int]:
    clauses = [
        "v.created_at <= ?",
        "v.is_deleted = 0",
        "v.missing = 0",
        "v.storage_volume_id IS NULL",
        "v.preview_status NOT IN ('pending', 'processing')",
        "v.metadata_status NOT IN ('pending', 'processing')",
        """
        NOT EXISTS (
            SELECT 1 FROM video_migrations vm
            WHERE vm.video_id = v.id
              AND vm.status IN ('pending', 'running', 'needs_cleanup')
        )
        """,
        """
        NOT EXISTS (
            SELECT 1 FROM download_tasks dt
            WHERE dt.video_id = v.id
              AND dt.status IN (
                  'pending', 'running', 'pausing', 'paused', 'deleting'
              )
        )
        """,
        """
        NOT EXISTS (
            SELECT 1 FROM video_rename_operations vr
            WHERE vr.video_id = v.id
        )
        """,
    ]
    params: List = [str(cutoff_at)]
    root_clauses = []
    for root in config.configured_roots():
        root_text = str(root.resolve()).rstrip("/")
        prefixes = {root_text}
        if root_text.startswith("/private/"):
            prefixes.add(root_text[len("/private"):])
        for prefix in sorted(prefixes):
            root_clauses.append("(v.path = ? OR substr(v.path, 1, ?) = ?)")
            params.extend([
                prefix,
                len(prefix) + 1,
                prefix + "/",
            ])
    if root_clauses:
        clauses.append("({})".format(" OR ".join(root_clauses)))
    else:
        clauses.append("0 = 1")
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT v.id FROM video_catalog v
            WHERE {}
            ORDER BY v.created_at ASC, v.id ASC
            """.format(" AND ".join(clauses)),
            params,
        ).fetchall()
    return [int(row["id"]) for row in rows]
