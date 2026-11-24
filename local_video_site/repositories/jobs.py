"""Repository adapters for background jobs and worker leases."""

from __future__ import annotations

from typing import Dict, List, Optional

from ..db.connection import connect
from local_video_site.jobs.integrity_tasks import claim_integrity_job, create_integrity_job, get_integrity_job, list_integrity_jobs, recover_interrupted_integrity_jobs
from local_video_site.jobs.leases import JobLeaseManager, job_lease_manager
from local_video_site.core.search import parse_search_query

__all__ = [
    "JobLeaseManager",
    "claim_integrity_job",
    "create_integrity_job",
    "get_integrity_job",
    "job_lease_manager",
    "list_integrity_jobs",
    "list_video_trim_jobs",
    "recover_interrupted_integrity_jobs",
]


def list_video_trim_jobs(
    query: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = ["1 = 1"]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("(j.id = ? OR j.video_id = ?)")
            params.extend([
                int(parsed_query.id_value),
                int(parsed_query.id_value),
            ])
    elif parsed_query.raw:
        escaped = (
            parsed_query.raw
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                j.job_id LIKE ? ESCAPE '\\'
                OR COALESCE(v.title, '') LIKE ? ESCAPE '\\'
                OR j.source_path LIKE ? ESCAPE '\\'
            )
            """
        )
        params.extend([
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
        ])
    if status:
        clauses.append("j.status = ?")
        params.append(status)
    where = " AND ".join(clauses)
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    with connect() as connection:
        total = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM video_trim_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE {}
            """.format(where),
            params,
        ).fetchone()[0])
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT j.*, COALESCE(v.title, '') AS video_title
            FROM video_trim_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE {}
            ORDER BY j.id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        count_rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM video_trim_jobs
            GROUP BY status
            """
        ).fetchall()
        backup_summary = connection.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(backup_size), 0) AS bytes
            FROM video_trim_jobs
            WHERE backup_status = 'available'
            """
        ).fetchone()
    counts = {row["status"]: int(row["count"]) for row in count_rows}
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "counts": counts,
        "backup_count": int(backup_summary["count"] or 0),
        "backup_bytes": int(backup_summary["bytes"] or 0),
    }
