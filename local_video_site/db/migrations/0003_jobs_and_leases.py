from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


jobs_and_leases_migration = Migration(
    BASELINE_VERSION + 2,
    "create background integrity jobs and worker leases",
    (
        """
        CREATE TABLE IF NOT EXISTS runtime_integrity_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_by TEXT,
            requested_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            run_id INTEGER,
            issue_count INTEGER,
            error_detail TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_integrity_jobs_status
        ON runtime_integrity_jobs(status, id)
        """,
        """
        CREATE TABLE IF NOT EXISTS worker_leases (
            lease_key TEXT PRIMARY KEY,
            worker_name TEXT NOT NULL,
            job_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_worker_leases_expiry
        ON worker_leases(expires_at)
        """,
    ),
)
