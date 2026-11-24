from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List

from local_video_site.db.connection import connect


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_RELATION_CHECKS = (
    ("video_categories", "video_id", "videos"),
    ("video_actors", "video_id", "videos"),
    ("video_manual_categories", "video_id", "videos"),
    ("favorites", "video_id", "videos"),
    ("user_favorites", "video_id", "videos"),
    ("category_fetch_cache", "video_id", "videos"),
    ("playback_cache_entries", "video_id", "videos"),
)


def _count_check(
    connection,
    name: str,
    query: str,
) -> Dict:
    count = int(connection.execute(query).fetchone()[0])
    return {
        "name": name,
        "ok": count == 0,
        "count": count,
    }


def run_runtime_integrity_check() -> Dict:
    started_at = _now()
    checks: List[Dict] = []
    with connect() as connection:
        checks.extend([
            _count_check(
                connection,
                "orphan_video_media_info",
                """
                SELECT COUNT(*) FROM video_media_info satellite
                WHERE NOT EXISTS (
                    SELECT 1 FROM videos video
                    WHERE video.id = satellite.video_id
                )
                """,
            ),
            _count_check(
                connection,
                "orphan_video_assets",
                """
                SELECT COUNT(*) FROM video_assets satellite
                WHERE NOT EXISTS (
                    SELECT 1 FROM videos video
                    WHERE video.id = satellite.video_id
                )
                """,
            ),
            _count_check(
                connection,
                "orphan_episode_seasons",
                """
                SELECT COUNT(*) FROM video_episodes episode
                WHERE NOT EXISTS (
                    SELECT 1 FROM video_seasons season
                    WHERE season.id = episode.season_id
                )
                """,
            ),
            _count_check(
                connection,
                "orphan_episode_videos",
                """
                SELECT COUNT(*) FROM video_episodes episode
                WHERE episode.video_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM videos video
                      WHERE video.id = episode.video_id
                  )
                """,
            ),
            _count_check(
                connection,
                "orphan_season_series",
                """
                SELECT COUNT(*) FROM video_seasons season
                WHERE NOT EXISTS (
                    SELECT 1 FROM video_series series
                    WHERE series.id = season.series_id
                )
                """,
            ),
            _count_check(
                connection,
                "orphan_user_sessions",
                """
                SELECT COUNT(*) FROM user_sessions session
                WHERE NOT EXISTS (
                    SELECT 1 FROM users user WHERE user.id = session.user_id
                )
                """,
            ),
            _count_check(
                connection,
                "orphan_delete_dependencies",
                """
                SELECT COUNT(*) FROM video_delete_job_dependencies dependency
                WHERE NOT EXISTS (
                    SELECT 1 FROM video_delete_jobs job
                    WHERE job.id = dependency.job_id
                )
                   OR NOT EXISTS (
                    SELECT 1 FROM video_delete_jobs job
                    WHERE job.id = dependency.depends_on_job_id
                )
                """,
            ),
            _count_check(
                connection,
                "orphan_download_episode_videos",
                """
                SELECT COUNT(*) FROM download_tasks task
                WHERE task.video_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM videos video
                      WHERE video.id = task.video_id
                  )
                """,
            ),
        ])
        for table, column, parent_table in _RELATION_CHECKS:
            checks.append(
                _count_check(
                    connection,
                    "orphan_{}".format(table),
                    """
                    SELECT COUNT(*) FROM {table} relation
                    WHERE relation.{column} IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM {parent} parent
                          WHERE parent.id = relation.{column}
                      )
                    """.format(
                        table=table,
                        column=column,
                        parent=parent_table,
                    ),
                )
            )
    issue_count = sum(int(check["count"]) for check in checks)
    return {
        "status": "healthy" if issue_count == 0 else "degraded",
        "ok": issue_count == 0,
        "issue_count": issue_count,
        "checks": checks,
        "started_at": started_at,
        "finished_at": _now(),
    }


def persist_integrity_report(report: Dict) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO runtime_integrity_runs (
                status, issue_count, checks_json, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(report["status"]),
                int(report["issue_count"]),
                json.dumps(report["checks"], ensure_ascii=False),
                str(report["started_at"]),
                str(report["finished_at"]),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Integrity run record was not created")
        run_id = int(cursor.lastrowid)
        connection.execute(
            """
            DELETE FROM runtime_integrity_runs
            WHERE id NOT IN (
                SELECT id FROM runtime_integrity_runs
                ORDER BY id DESC LIMIT 100
            )
            """
        )
        return run_id


def latest_integrity_report() -> Dict:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM runtime_integrity_runs
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        return {}
    payload = dict(row)
    payload["checks"] = json.loads(payload.pop("checks_json"))
    return payload
