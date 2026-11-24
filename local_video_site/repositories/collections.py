from __future__ import annotations

from datetime import datetime
from typing import Dict, Set

from ..db.connection import connect


class CollectionDeleteConflict(RuntimeError):
    def __init__(self, message: str, code: str = "scope_changed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CollectionDeleteUnitOfWork:
    def soft_delete_season(
        self,
        season_id: int,
        expected_video_ids: Set[int],
    ) -> Dict:
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            season = connection.execute(
                """
                SELECT s.id
                FROM video_seasons s
                JOIN video_series sr ON sr.id = s.series_id
                WHERE s.id = ? AND s.is_deleted = 0
                """,
                (int(season_id),),
            ).fetchone()
            if season is None:
                connection.rollback()
                return {"already_deleted": True}
            rows = connection.execute(
                """
                SELECT e.video_id, v.is_deleted
                FROM video_episodes e
                LEFT JOIN videos v ON v.id = e.video_id
                WHERE e.season_id = ? AND e.video_id IS NOT NULL
                """,
                (int(season_id),),
            ).fetchall()
            active_ids = {
                int(row["video_id"])
                for row in rows
                if not int(row["is_deleted"] or 0)
            }
            if active_ids:
                connection.rollback()
                raise CollectionDeleteConflict(
                    "该季还有 {} 个视频未软删除: {}".format(
                        len(active_ids),
                        ", ".join(
                            str(value)
                            for value in sorted(active_ids)[:10]
                        ),
                    ),
                    "season_has_active_videos",
                )
            actual_ids = {int(row["video_id"]) for row in rows}
            if actual_ids != set(int(value) for value in expected_video_ids):
                connection.rollback()
                raise CollectionDeleteConflict(
                    "该季关联的视频范围已经发生变化",
                    "scope_changed",
                )
            now = _now()
            connection.execute(
                """
                UPDATE video_seasons
                SET is_deleted = 1, deleted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(season_id)),
            )
            connection.execute(
                """
                UPDATE video_episodes
                SET status = 'deleted', updated_at = ?
                WHERE season_id = ?
                """,
                (now, int(season_id)),
            )
            return {"video_count": len(rows)}

    def soft_delete_series(
        self,
        series_id: int,
        expected_season_ids: Set[int],
    ) -> Dict:
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            series = connection.execute(
                """
                SELECT id FROM video_series
                WHERE id = ? AND is_deleted = 0
                """,
                (int(series_id),),
            ).fetchone()
            if series is None:
                connection.rollback()
                return {"already_deleted": True}
            rows = connection.execute(
                """
                SELECT id, is_deleted FROM video_seasons
                WHERE series_id = ?
                """,
                (int(series_id),),
            ).fetchall()
            active_ids = {
                int(row["id"])
                for row in rows
                if not int(row["is_deleted"] or 0)
            }
            if active_ids:
                connection.rollback()
                raise CollectionDeleteConflict(
                    "该视频集还有 {} 个季未软删除: {}".format(
                        len(active_ids),
                        ", ".join(
                            str(value)
                            for value in sorted(active_ids)[:10]
                        ),
                    ),
                    "series_has_active_seasons",
                )
            actual_ids = {int(row["id"]) for row in rows}
            if actual_ids != set(int(value) for value in expected_season_ids):
                connection.rollback()
                raise CollectionDeleteConflict(
                    "该视频集的季范围已经发生变化",
                    "scope_changed",
                )
            now = _now()
            connection.execute(
                """
                UPDATE video_series
                SET is_deleted = 1, deleted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(series_id)),
            )
            return {"season_count": len(rows)}


collection_delete_uow = CollectionDeleteUnitOfWork()
