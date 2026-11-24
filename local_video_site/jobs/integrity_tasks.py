from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Dict, Optional

from local_video_site.core import config
from local_video_site.db.connection import connect
from local_video_site.jobs.integrity import persist_integrity_report, run_runtime_integrity_check
from local_video_site.jobs.runtime import JobOutcome, JobResult, JobRunner


LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_integrity_job(requested_by: str = "") -> Dict:
    job_id = "integrity-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO runtime_integrity_jobs (
                job_id, status, requested_by, requested_at
            ) VALUES (?, 'pending', ?, ?)
            """,
            (job_id, requested_by, _now()),
        )
    return get_integrity_job(job_id) or {}


def get_integrity_job(job_id: str) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM runtime_integrity_jobs WHERE job_id = ?",
            (str(job_id),),
        ).fetchone()
    return dict(row) if row else None


def list_integrity_jobs(limit: int = 20) -> list:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM runtime_integrity_jobs
            ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(100, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_integrity_job() -> Optional[Dict]:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM runtime_integrity_jobs
            WHERE status = 'pending'
            ORDER BY id ASC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        now = _now()
        connection.execute(
            """
            UPDATE runtime_integrity_jobs
            SET status = 'running', started_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, int(row["id"])),
        )
        result = dict(row)
        result.update({"status": "running", "started_at": now})
        return result


def process_integrity_job(job: Dict) -> JobResult:
    try:
        report = run_runtime_integrity_check()
        run_id = persist_integrity_report(report)
        with connect() as connection:
            connection.execute(
                """
                UPDATE runtime_integrity_jobs
                SET status = 'success', finished_at = ?,
                    run_id = ?, issue_count = ?, error_detail = NULL
                WHERE id = ?
                """,
                (
                    _now(),
                    run_id,
                    int(report["issue_count"]),
                    int(job["id"]),
                ),
            )
        return JobResult(
            JobOutcome.SUCCESS,
            "checks={} issues={}".format(
                len(report["checks"]),
                report["issue_count"],
            ),
        )
    except Exception as exc:
        with connect() as connection:
            connection.execute(
                """
                UPDATE runtime_integrity_jobs
                SET status = 'failed', finished_at = ?, error_detail = ?
                WHERE id = ?
                """,
                (_now(), str(exc)[:1000], int(job["id"])),
            )
        return JobResult(JobOutcome.FAILED, str(exc))


def recover_interrupted_integrity_jobs() -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE runtime_integrity_jobs
            SET status = 'pending', started_at = NULL
            WHERE status = 'running'
            """
        )
        return int(cursor.rowcount)


class IntegrityWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.runner = JobRunner(
            name="runtime-integrity-worker",
            stop_event=self.stop_event,
            claim_job=claim_integrity_job,
            process_job=process_integrity_job,
            poll_seconds=config.integrity_worker_poll_seconds,
            logger=self.logger,
        )

    @property
    def thread(self):
        return self.runner.thread

    def start(self):
        self.runner.start()
        return self

    def stop(self) -> None:
        self.runner.stop(timeout=15)
