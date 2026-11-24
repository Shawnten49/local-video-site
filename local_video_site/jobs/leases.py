from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from local_video_site.core import config
from local_video_site.db.connection import connect


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


class JobLeaseManager:
    def acquire(self, worker_name: str, job_id: str) -> bool:
        now = _now()
        expires = now + timedelta(seconds=config.job_lease_seconds())
        lease_key = "{}:{}".format(worker_name, job_id)
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM worker_leases WHERE expires_at <= ?",
                (_iso(now),),
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO worker_leases (
                    lease_key, worker_name, job_id,
                    acquired_at, heartbeat_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    lease_key,
                    worker_name,
                    job_id,
                    _iso(now),
                    _iso(now),
                    _iso(expires),
                ),
            )
            return cursor.rowcount == 1

    def renew(self, worker_name: str, job_id: str) -> bool:
        now = _now()
        expires = now + timedelta(seconds=config.job_lease_seconds())
        lease_key = "{}:{}".format(worker_name, job_id)
        with connect() as connection:
            cursor = connection.execute(
                """
                UPDATE worker_leases
                SET heartbeat_at = ?, expires_at = ?
                WHERE lease_key = ?
                """,
                (_iso(now), _iso(expires), lease_key),
            )
            return cursor.rowcount == 1

    def release(self, worker_name: str, job_id: str) -> None:
        lease_key = "{}:{}".format(worker_name, job_id)
        with connect() as connection:
            connection.execute(
                "DELETE FROM worker_leases WHERE lease_key = ?",
                (lease_key,),
            )

    def active(self) -> list:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM worker_leases
                WHERE expires_at > ?
                ORDER BY worker_name, job_id
                """,
                (_iso(_now()),),
            ).fetchall()
        return [dict(row) for row in rows]


job_lease_manager = JobLeaseManager()
