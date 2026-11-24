from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.storage.deletion import delete_video_authorized
from local_video_site.jobs.runtime import JobResult, JobRunner, JobStateMachine
from local_video_site.repositories.collections import (
    CollectionDeleteConflict,
    collection_delete_uow,
)
from local_video_site.core.search import parse_search_query


LOGGER = logging.getLogger(__name__)
SCOPE_LABELS = {
    "video": "删除视频",
    "season": "删除视频季",
    "series": "删除视频集",
}
SOURCE_LABELS = {
    "video_management": "视频管理",
    "season_management": "视频季管理",
    "series_management": "视频集管理",
}
STATUS_LABELS = {
    "pending": "等待执行",
    "blocked": "等待下级任务",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
    "cancelled": "已取消",
}
STAGE_LABELS = {
    "waiting_dependencies": "等待依赖",
    "preflight": "检查删除条件",
    "deleting_files": "删除视频文件",
    "deleting_derivatives": "删除派生资源",
    "soft_deleting_video": "软删除视频",
    "verifying_children": "校验下级数据",
    "soft_deleting_season": "软删除视频季",
    "soft_deleting_series": "软删除视频集",
    "finished": "完成",
}
class DeleteTaskError(RuntimeError):
    def __init__(self, message: str, code: str = "delete_task_failed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _batch_id() -> str:
    return "delete-{}-{}".format(
        datetime.now().strftime("%Y%m%d-%H%M%S"),
        uuid.uuid4().hex[:8],
    )


def _job_id() -> str:
    return "del-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )


def _target(connection, scope_type: str, target_id: int) -> Dict:
    if scope_type == "video":
        row = connection.execute(
            """
            SELECT v.id, v.title AS name, v.is_deleted, v.updated_at,
                   v.path, v.size
            FROM videos v WHERE v.id = ?
            """,
            (int(target_id),),
        ).fetchone()
    elif scope_type == "season":
        row = connection.execute(
            """
            SELECT s.id, s.name, s.is_deleted, s.updated_at,
                   s.series_id
            FROM video_seasons s
            JOIN video_series sr ON sr.id = s.series_id
            WHERE s.id = ? AND sr.is_deleted = 0
            """,
            (int(target_id),),
        ).fetchone()
    elif scope_type == "series":
        row = connection.execute(
            """
            SELECT id, name, is_deleted, updated_at
            FROM video_series WHERE id = ?
            """,
            (int(target_id),),
        ).fetchone()
    else:
        raise DeleteTaskError("不支持的删除范围", "invalid_scope")
    if row is None:
        raise DeleteTaskError("删除目标不存在", "target_missing")
    return dict(row)


def _ensure_targets_not_active(
    connection,
    targets: List[tuple],
) -> None:
    for scope_type, target_id in targets:
        row = connection.execute(
            """
            SELECT id FROM video_delete_jobs
            WHERE scope_type = ? AND target_id = ?
              AND status IN ('pending', 'running', 'blocked')
            LIMIT 1
            """,
            (scope_type, int(target_id)),
        ).fetchone()
        if row:
            raise DeleteTaskError(
                "目标已有活动删除任务",
                "delete_task_active",
            )


def _insert_job(
    connection,
    *,
    batch_id: str,
    scope_type: str,
    target: Dict,
    source_action: str,
    status: str,
    stage: str,
    root_job_id: Optional[int] = None,
    parent_job_id: Optional[int] = None,
    expected: Optional[Dict] = None,
    requested_by_user_id: Optional[int] = None,
    requested_by_username: str = "",
    verified_at: str = "",
) -> int:
    now = _now()
    cursor = connection.execute(
        """
        INSERT INTO video_delete_jobs (
            job_id, batch_id, root_job_id, parent_job_id,
            scope_type, target_id, target_name, source_action,
            status, stage, progress_percent, expected_snapshot_json,
            verification_method, verified_at,
            requested_by_user_id, requested_by_username,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'password',
                  ?, ?, ?, ?, ?)
        """,
        (
            _job_id(),
            batch_id,
            root_job_id,
            parent_job_id,
            scope_type,
            int(target["id"]),
            str(target.get("name") or target["id"]),
            source_action,
            status,
            stage,
            json.dumps(
                expected or {},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            verified_at,
            requested_by_user_id,
            requested_by_username,
            now,
            now,
        ),
    )
    job_id = int(cursor.lastrowid)
    if root_job_id is None:
        connection.execute(
            "UPDATE video_delete_jobs SET root_job_id = ? WHERE id = ?",
            (job_id, job_id),
        )
    return job_id


def _dependency(connection, job_id: int, depends_on_job_id: int) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO video_delete_job_dependencies (
            job_id, depends_on_job_id, created_at
        ) VALUES (?, ?, ?)
        """,
        (int(job_id), int(depends_on_job_id), _now()),
    )


def create_delete_batch(
    scope_type: str,
    target_id: int,
    source_action: str,
    requested_by_user_id: Optional[int] = None,
    requested_by_username: str = "",
    expected_updated_at: str = "",
) -> Dict:
    if scope_type not in SCOPE_LABELS:
        raise DeleteTaskError("不支持的删除范围", "invalid_scope")
    batch_id = _batch_id()
    verified_at = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        root_target = _target(connection, scope_type, target_id)
        if int(root_target.get("is_deleted") or 0):
            return {
                "batch_id": batch_id,
                "created": 0,
                "already_deleted": True,
                "video_jobs": 0,
                "season_jobs": 0,
                "series_jobs": 0,
            }
        if (
            expected_updated_at
            and str(root_target.get("updated_at") or "")
            != str(expected_updated_at)
        ):
            raise DeleteTaskError(
                "删除目标已经发生变化，请刷新后重试",
                "target_changed",
            )

        video_ids = set()
        seasons = []
        if scope_type == "video":
            video_ids.add(int(target_id))
        elif scope_type == "season":
            rows = connection.execute(
                """
                SELECT video_id FROM video_episodes
                WHERE season_id = ? AND video_id IS NOT NULL
                """,
                (int(target_id),),
            ).fetchall()
            video_ids.update(int(row["video_id"]) for row in rows)
        else:
            rows = connection.execute(
                """
                SELECT * FROM video_seasons
                WHERE series_id = ? AND is_deleted = 0
                ORDER BY season_number, id
                """,
                (int(target_id),),
            ).fetchall()
            seasons = [dict(row) for row in rows]
            for season in seasons:
                rows = connection.execute(
                    """
                    SELECT video_id FROM video_episodes
                    WHERE season_id = ? AND video_id IS NOT NULL
                    """,
                    (int(season["id"]),),
                ).fetchall()
                video_ids.update(int(row["video_id"]) for row in rows)

        target_pairs = [(scope_type, int(target_id))]
        target_pairs.extend(("season", int(item["id"])) for item in seasons)
        target_pairs.extend(("video", int(value)) for value in video_ids)
        _ensure_targets_not_active(connection, target_pairs)

        snapshot = {
            "video_ids": sorted(video_ids),
            "season_ids": sorted(int(item["id"]) for item in seasons),
        }
        root_id = _insert_job(
            connection,
            batch_id=batch_id,
            scope_type=scope_type,
            target=root_target,
            source_action=source_action,
            status=(
                "blocked"
                if (
                    (scope_type == "season" and video_ids)
                    or (scope_type == "series" and seasons)
                )
                else "pending"
            ),
            stage="waiting_dependencies",
            expected=snapshot,
            requested_by_user_id=requested_by_user_id,
            requested_by_username=requested_by_username,
            verified_at=verified_at,
        )

        video_jobs = {}
        for video_id in sorted(video_ids):
            if scope_type == "video" and video_id == int(target_id):
                video_jobs[video_id] = root_id
                continue
            video = _target(connection, "video", video_id)
            vid_job = _insert_job(
                connection,
                batch_id=batch_id,
                scope_type="video",
                target=video,
                source_action=source_action,
                status="pending",
                stage="waiting_dependencies",
                root_job_id=root_id,
                parent_job_id=(
                    root_id if scope_type == "season" else None
                ),
                expected={"video_id": video_id},
                requested_by_user_id=requested_by_user_id,
                requested_by_username=requested_by_username,
                verified_at=verified_at,
            )
            video_jobs[video_id] = vid_job
            if scope_type == "season":
                _dependency(connection, root_id, vid_job)

        season_jobs = {}
        for season in seasons:
            season_job = _insert_job(
                connection,
                batch_id=batch_id,
                scope_type="season",
                target=season,
                source_action=source_action,
                status="blocked",
                stage="waiting_dependencies",
                root_job_id=root_id,
                parent_job_id=root_id,
                expected={
                    "video_ids": sorted({
                        int(row["video_id"])
                        for row in connection.execute(
                            """
                            SELECT video_id FROM video_episodes
                            WHERE season_id = ? AND video_id IS NOT NULL
                            """,
                            (int(season["id"]),),
                        ).fetchall()
                    })
                },
                requested_by_user_id=requested_by_user_id,
                requested_by_username=requested_by_username,
                verified_at=verified_at,
            )
            season_jobs[int(season["id"])] = season_job
            _dependency(connection, root_id, season_job)
            episode_videos = connection.execute(
                """
                SELECT DISTINCT video_id FROM video_episodes
                WHERE season_id = ? AND video_id IS NOT NULL
                """,
                (int(season["id"]),),
            ).fetchall()
            for row in episode_videos:
                connection.execute(
                    """
                    UPDATE video_delete_jobs
                    SET parent_job_id = ?
                    WHERE id = ? AND parent_job_id IS NULL
                    """,
                    (season_job, video_jobs[int(row["video_id"])]),
                )
                _dependency(
                    connection,
                    season_job,
                    video_jobs[int(row["video_id"])],
                )

        connection.commit()
    return {
        "batch_id": batch_id,
        "created": 1 + len(video_jobs) + len(season_jobs),
        "already_deleted": False,
        "video_jobs": len(video_jobs),
        "season_jobs": len(season_jobs),
        "series_jobs": 1 if scope_type == "series" else 0,
    }


def _decorate(row: Dict) -> Dict:
    item = dict(row)
    item["scope_label"] = SCOPE_LABELS.get(
        item.get("scope_type"),
        item.get("scope_type"),
    )
    item["source_label"] = SOURCE_LABELS.get(
        item.get("source_action"),
        item.get("source_action"),
    )
    item["status_label"] = STATUS_LABELS.get(
        item.get("status"),
        item.get("status"),
    )
    item["stage_label"] = STAGE_LABELS.get(
        item.get("stage"),
        item.get("stage"),
    )
    return item


def list_delete_jobs(
    query: str = "",
    scope_type: str = "",
    status: str = "",
    source_action: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = ["1 = 1"]
    params = []
    parsed = parse_search_query(query)
    if parsed.is_id:
        if parsed.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("(j.id = ? OR j.target_id = ?)")
            params.extend([parsed.id_value, parsed.id_value])
    elif parsed.raw:
        escaped = (
            parsed.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                j.batch_id LIKE ? ESCAPE '\\'
                OR j.target_name LIKE ? ESCAPE '\\'
                OR COALESCE(j.error_code, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        like = "%{}%".format(escaped)
        params.extend([like, like, like])
    if scope_type:
        clauses.append("j.scope_type = ?")
        params.append(scope_type)
    if status:
        clauses.append("j.status = ?")
        params.append(status)
    if source_action:
        clauses.append("j.source_action = ?")
        params.append(source_action)
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    with database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM video_delete_jobs j WHERE {}".format(
                " AND ".join(clauses)
            ),
            params,
        ).fetchone()[0]
        pages = max(1, (int(total) + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT j.*,
                   (
                       SELECT COUNT(*) FROM video_delete_job_dependencies d
                       WHERE d.job_id = j.id
                   ) AS dependency_count,
                   (
                       SELECT COUNT(*) FROM video_delete_job_dependencies d
                       JOIN video_delete_jobs parent
                         ON parent.id = d.depends_on_job_id
                       WHERE d.job_id = j.id
                         AND parent.status = 'success'
                   ) AS dependency_success
            FROM video_delete_jobs j
            WHERE {}
            ORDER BY
                CASE j.status
                    WHEN 'running' THEN 0
                    WHEN 'failed' THEN 1
                    WHEN 'blocked' THEN 2
                    WHEN 'pending' THEN 3
                    ELSE 4
                END,
                j.id DESC
            LIMIT ? OFFSET ?
            """.format(" AND ".join(clauses)),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_decorate(dict(row)) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def delete_job_counts() -> Dict:
    result = {status: 0 for status in STATUS_LABELS}
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count "
            "FROM video_delete_jobs GROUP BY status"
        ).fetchall()
    for row in rows:
        if row["status"] in result:
            result[row["status"]] = int(row["count"])
    result["total"] = sum(result.values())
    result["active"] = (
        result["pending"] + result["running"] + result["blocked"]
    )
    return result


def refresh_dependency_statuses() -> None:
    now = _now()
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.id,
                   COUNT(d.depends_on_job_id) AS total,
                   SUM(CASE WHEN p.status = 'success' THEN 1 ELSE 0 END)
                       AS succeeded,
                   SUM(CASE WHEN p.status IN ('failed', 'cancelled')
                            THEN 1 ELSE 0 END) AS failed
            FROM video_delete_jobs j
            LEFT JOIN video_delete_job_dependencies d
              ON d.job_id = j.id
            LEFT JOIN video_delete_jobs p
              ON p.id = d.depends_on_job_id
            WHERE j.status IN ('pending', 'blocked')
            GROUP BY j.id
            """
        ).fetchall()
        for row in rows:
            total = int(row["total"] or 0)
            succeeded = int(row["succeeded"] or 0)
            failed = int(row["failed"] or 0)
            if failed:
                status = "blocked"
                code = "dependency_failed"
                detail = "存在失败的下级删除任务"
            elif total == 0 or succeeded == total:
                status = "pending"
                code = None
                detail = None
            else:
                status = "blocked"
                code = "waiting_dependencies"
                detail = "等待下级删除任务"
            connection.execute(
                """
                UPDATE video_delete_jobs
                SET status = ?, stage = 'waiting_dependencies',
                    error_code = ?, error_detail = ?, updated_at = ?
                WHERE id = ? AND status IN ('pending', 'blocked')
                """,
                (status, code, detail, now, int(row["id"])),
            )


def claim_delete_job() -> Optional[Dict]:
    refresh_dependency_statuses()
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT j.*
            FROM video_delete_jobs j
            WHERE j.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1
                  FROM video_delete_job_dependencies d
                  JOIN video_delete_jobs p ON p.id = d.depends_on_job_id
                  WHERE d.job_id = j.id AND p.status <> 'success'
              )
            ORDER BY
                CASE j.scope_type
                    WHEN 'video' THEN 0
                    WHEN 'season' THEN 1
                    ELSE 2
                END,
                j.id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE video_delete_jobs
            SET status = 'running', stage = 'preflight',
                progress_percent = 5,
                attempt_count = attempt_count + 1,
                started_at = COALESCE(started_at, ?),
                error_code = NULL, error_detail = NULL,
                finished_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(row["id"])),
        )
        connection.commit()
    result = dict(row)
    result.update({
        "status": "running",
        "stage": "preflight",
        "attempt_count": int(result.get("attempt_count") or 0) + 1,
    })
    return result


def get_delete_job(job_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_delete_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    return _decorate(dict(row)) if row else None


def get_delete_jobs_by_ids(job_ids: List[int]) -> List[Dict]:
    normalized = sorted({int(value) for value in job_ids})
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM video_delete_jobs
            WHERE id IN ({})
            """.format(placeholders),
            normalized,
        ).fetchall()
    return [_decorate(dict(row)) for row in rows]


def _update_job(job_id: int, **fields) -> None:
    allowed = {
        "status", "stage", "progress_percent", "result_json",
        "error_code", "error_detail", "started_at", "finished_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT status FROM video_delete_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        if current is None:
            connection.rollback()
            return
        if "status" in values:
            JobStateMachine.validate(
                str(current["status"]),
                str(values["status"]),
            )
        connection.execute(
            "UPDATE video_delete_jobs SET {} WHERE id = ?".format(
                assignments
            ),
            [*values.values(), int(job_id)],
        )


def _finish(job_id: int, result: Dict) -> None:
    _update_job(
        job_id,
        status="success",
        stage="finished",
        progress_percent=100,
        result_json=json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        error_code=None,
        error_detail=None,
        finished_at=_now(),
    )


def _fail(job_id: int, code: str, detail: str) -> None:
    _update_job(
        job_id,
        status="failed",
        stage="finished",
        error_code=code,
        error_detail=detail[:1000],
        finished_at=_now(),
    )


def _process_video(job: Dict) -> Dict:
    _update_job(
        job["id"],
        stage="deleting_files",
        progress_percent=25,
    )
    result = delete_video_authorized(int(job["target_id"]))
    _update_job(
        job["id"],
        stage="soft_deleting_video",
        progress_percent=90,
    )
    return result


def _process_season(job: Dict) -> Dict:
    season_id = int(job["target_id"])
    expected = json.loads(job.get("expected_snapshot_json") or "{}")
    expected_ids = {
        int(value) for value in expected.get("video_ids", [])
    }
    try:
        result = collection_delete_uow.soft_delete_season(
            season_id,
            expected_ids,
        )
    except CollectionDeleteConflict as exc:
        raise DeleteTaskError(str(exc), exc.code) from exc
    if result.get("already_deleted"):
        return result
    return {"season_id": season_id, **result}


def _process_series(job: Dict) -> Dict:
    series_id = int(job["target_id"])
    expected = json.loads(job.get("expected_snapshot_json") or "{}")
    expected_ids = {
        int(value) for value in expected.get("season_ids", [])
    }
    try:
        result = collection_delete_uow.soft_delete_series(
            series_id,
            expected_ids,
        )
    except CollectionDeleteConflict as exc:
        raise DeleteTaskError(str(exc), exc.code) from exc
    if result.get("already_deleted"):
        return result
    return {"series_id": series_id, **result}


def process_delete_job(job: Dict) -> Dict:
    try:
        if job["scope_type"] == "video":
            result = _process_video(job)
        elif job["scope_type"] == "season":
            _update_job(
                job["id"],
                stage="verifying_children",
                progress_percent=35,
            )
            result = _process_season(job)
        else:
            _update_job(
                job["id"],
                stage="verifying_children",
                progress_percent=35,
            )
            result = _process_series(job)
        _finish(job["id"], result)
    except Exception as exc:
        _fail(
            job["id"],
            getattr(exc, "code", "delete_failed"),
            str(exc),
        )
    finally:
        refresh_dependency_statuses()
    return get_delete_job(job["id"]) or {}


def retry_delete_job(job_id: int) -> bool:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_delete_jobs
            SET status = 'pending', stage = 'waiting_dependencies',
                progress_percent = 0, error_code = NULL,
                error_detail = NULL, finished_at = NULL,
                updated_at = ?
            WHERE id = ? AND status IN ('failed', 'cancelled')
            """,
            (_now(), int(job_id)),
        )
    refresh_dependency_statuses()
    return cursor.rowcount > 0


def retry_failed_batch(batch_id: str) -> int:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_delete_jobs
            SET status = 'pending', stage = 'waiting_dependencies',
                progress_percent = 0, error_code = NULL,
                error_detail = NULL, finished_at = NULL,
                updated_at = ?
            WHERE batch_id = ? AND status IN ('failed', 'cancelled')
            """,
            (_now(), batch_id),
        )
    refresh_dependency_statuses()
    return int(cursor.rowcount)


def recover_interrupted_delete_jobs() -> int:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_delete_jobs
            SET status = 'pending', stage = 'waiting_dependencies',
                progress_percent = 0, updated_at = ?
            WHERE status = 'running'
            """,
            (_now(),),
        )
    refresh_dependency_statuses()
    return cursor.rowcount


class VideoDeleteWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.runner = JobRunner(
            name="video-delete-worker",
            stop_event=self.stop_event,
            claim_job=claim_delete_job,
            process_job=lambda job: JobResult.from_status(
                process_delete_job(job).get("status", "")
            ),
            poll_seconds=config.video_delete_poll_seconds,
            logger=self.logger,
        )

    def start(self):
        self.runner.start()
        return self

    @property
    def thread(self):
        return self.runner.thread

    def stop(self) -> None:
        self.runner.stop(timeout=10)
