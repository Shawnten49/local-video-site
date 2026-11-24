from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from local_video_site import database


TRANSPORT_MODES = {"auto", "hls", "direct-range", "yt-dlp"}
LIFECYCLE_STATES = {
    "UNKNOWN",
    "TASK_QUALIFIED",
    "CANDIDATE",
    "VERIFIED",
    "QUARANTINED",
    "DISABLED",
}
BREAKER_STATES = {"CLOSED", "OPEN", "HALF_OPEN"}
BUILTIN_SITE_KEYS = {"pornone", "porntrex", "xvideos", "xhamster"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_domains(value) -> List[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").splitlines()
    result = []
    for item in raw:
        domain = str(item or "").strip().lower().strip(".")
        if not domain:
            continue
        if "://" in domain or "/" in domain or " " in domain:
            raise ValueError("域名格式无效: {}".format(item))
        result.append(domain)
    return sorted(set(result))


def normalize_site_key(value: str) -> str:
    key = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,190}", key):
        raise ValueError("站点键格式无效")
    return key


def site_key_for_url(value: str) -> str:
    try:
        hostname = (urlsplit(str(value or "")).hostname or "").lower()
    except ValueError:
        hostname = ""
    if not hostname:
        raise ValueError("无法识别下载来源站点")
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT site_key, domains_json FROM transport_site_policies"
        ).fetchall()
    for row in rows:
        domains = json.loads(row["domains_json"] or "[]")
        if any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in domains
        ):
            return row["site_key"]
    key = hostname[4:] if hostname.startswith("www.") else hostname
    return normalize_site_key(key)


def _policy_row(site_key: str) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT p.*, s.tasks_created, s.tasks_completed, s.tasks_failed,
                   s.range_attempts, s.range_successes, s.range_failures,
                   s.qualification_successes, s.qualification_failures,
                   s.ytdlp_fallbacks, s.resume_attempts, s.resume_successes,
                   s.url_refresh_successes, s.url_refresh_failures,
                   s.rate_limit_events, s.breaker_opens,
                   s.content_mismatches, s.downloaded_bytes,
                   s.largest_file_bytes
            FROM transport_site_policies p
            LEFT JOIN transport_site_stats s ON s.site_key = p.site_key
            WHERE p.site_key = ?
            """,
            (site_key,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["domains"] = json.loads(item.get("domains_json") or "[]")
    return item


def ensure_site_for_url(url: str, task_id: Optional[int] = None) -> Dict:
    site_key = site_key_for_url(url)
    policy = _policy_row(site_key)
    if policy:
        return policy
    now = _now()
    hostname = (urlsplit(url).hostname or site_key).lower()
    domains = [hostname[4:] if hostname.startswith("www.") else hostname]
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT OR IGNORE INTO transport_site_policies (
                site_key, display_name, domains_json, transport_mode,
                lifecycle_state, manual_override, is_builtin,
                qualification_enabled, initial_connections,
                max_connections, tls_backend, policy_version,
                breaker_state, created_at, updated_at
            ) VALUES (?, ?, ?, 'auto', 'UNKNOWN', 0, 0, 1, 2, 4,
                      'auto', 1, 'CLOSED', ?, ?)
            """,
            (
                site_key,
                domains[0],
                json.dumps(domains),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO transport_site_stats (
                site_key, updated_at
            ) VALUES (?, ?)
            """,
            (site_key, now),
        )
        connection.commit()
    record_event(
        site_key,
        "site.discovered",
        task_id=task_id,
        details={"url": url, "domains": domains},
    )
    return _policy_row(site_key)


def resolve_transport_for_task(task: Dict) -> Dict:
    site_key = str(task.get("transport_site_key") or "").strip()
    if not site_key:
        policy = ensure_site_for_url(task["url"], task_id=task.get("id"))
    else:
        policy = _policy_row(site_key)
        if not policy:
            policy = ensure_site_for_url(task["url"], task_id=task.get("id"))
    state = policy["lifecycle_state"]
    mode = policy["transport_mode"]
    manual_direct = bool(policy.get("manual_override")) and mode == "direct-range"
    if (
        state in {"DISABLED", "QUARANTINED"}
        and not manual_direct
    ) or mode == "yt-dlp":
        child_mode = "yt-dlp"
    elif policy["breaker_state"] == "OPEN":
        disabled_until = policy.get("disabled_until")
        if disabled_until and disabled_until > _now():
            child_mode = "yt-dlp"
        else:
            now = _now()
            with database.connect() as connection:
                connection.execute(
                    """
                    UPDATE transport_site_policies
                    SET breaker_state = 'HALF_OPEN', updated_at = ?
                    WHERE site_key = ? AND breaker_state = 'OPEN'
                    """,
                    (now, policy["site_key"]),
                )
            policy["breaker_state"] = "HALF_OPEN"
            child_mode = "qualify" if mode == "auto" else mode
    elif mode == "hls":
        child_mode = "hls"
    elif mode == "direct-range" and state in {"VERIFIED", "CANDIDATE"}:
        child_mode = "direct-range"
    elif mode == "direct-range":
        child_mode = "direct-range"
    elif mode == "auto" and state == "VERIFIED":
        child_mode = "direct-range"
    else:
        child_mode = "qualify"
    return {
        "policy": policy,
        "site_key": policy["site_key"],
        "child_mode": child_mode,
    }


def list_policies(
    query: str = "",
    state: str = "",
    mode: str = "",
) -> List[Dict]:
    clauses = ["1 = 1"]
    params = []
    if query:
        value = "%{}%".format(query)
        clauses.append(
            "(p.site_key LIKE ? OR p.display_name LIKE ? OR p.domains_json LIKE ?)"
        )
        params.extend([value, value, value])
    if state:
        clauses.append("p.lifecycle_state = ?")
        params.append(state)
    if mode:
        clauses.append("p.transport_mode = ?")
        params.append(mode)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT p.*, s.tasks_created, s.tasks_completed, s.tasks_failed,
                   s.range_attempts, s.range_successes, s.range_failures,
                   s.qualification_successes, s.qualification_failures,
                   s.ytdlp_fallbacks, s.resume_attempts, s.resume_successes,
                   s.url_refresh_successes, s.url_refresh_failures,
                   s.rate_limit_events, s.breaker_opens,
                   s.content_mismatches, s.downloaded_bytes,
                   s.largest_file_bytes
            FROM transport_site_policies p
            LEFT JOIN transport_site_stats s ON s.site_key = p.site_key
            WHERE {}
            ORDER BY
                CASE p.lifecycle_state
                    WHEN 'VERIFIED' THEN 0
                    WHEN 'CANDIDATE' THEN 1
                    WHEN 'TASK_QUALIFIED' THEN 2
                    WHEN 'QUARANTINED' THEN 3
                    WHEN 'DISABLED' THEN 4
                    ELSE 5
                END,
                p.site_key
            """.format(" AND ".join(clauses)),
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["domains"] = json.loads(item.get("domains_json") or "[]")
        result.append(item)
    return result


def get_policy(site_key: str) -> Optional[Dict]:
    return _policy_row(normalize_site_key(site_key))


def create_policy(data: Dict) -> Dict:
    site_key = normalize_site_key(data.get("site_key"))
    domains = _parse_domains(data.get("domains"))
    if not domains:
        raise ValueError("至少需要一个域名")
    mode = str(data.get("transport_mode") or "auto")
    state = str(data.get("lifecycle_state") or "UNKNOWN").upper()
    if mode not in TRANSPORT_MODES or state not in LIFECYCLE_STATES:
        raise ValueError("传输模式或生命周期状态无效")
    if state == "VERIFIED" and not str(data.get("note") or "").strip():
        raise ValueError("创建 VERIFIED 站点时必须填写备注")
    existing = get_policy(site_key)
    if existing:
        raise ValueError("站点键已存在")
    for policy in list_policies():
        if set(policy["domains"]) & set(domains):
            raise ValueError("域名已属于其他站点配置")
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO transport_site_policies (
                site_key, display_name, domains_json, transport_mode,
                lifecycle_state, manual_override, is_builtin,
                qualification_enabled, initial_connections,
                max_connections, tls_backend, policy_version,
                breaker_state, status_note, promoted_at, promoted_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, 1, 'CLOSED',
                      ?, ?, ?, ?, ?)
            """,
            (
                site_key,
                str(data.get("display_name") or site_key).strip(),
                json.dumps(domains),
                mode,
                state,
                1 if data.get("qualification_enabled", True) else 0,
                max(1, min(2, int(data.get("initial_connections") or 2))),
                max(2, min(4, int(data.get("max_connections") or 4))),
                str(data.get("tls_backend") or "auto"),
                str(data.get("note") or ""),
                now if state == "VERIFIED" else None,
                "manual" if state == "VERIFIED" else None,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO transport_site_stats (site_key, updated_at) VALUES (?, ?)",
            (site_key, now),
        )
        connection.commit()
    record_audit(site_key, "create", None, data, data.get("note"), "", "")
    return get_policy(site_key)


def record_event(
    site_key: str,
    event_type: str,
    task_id: Optional[int] = None,
    severity: str = "info",
    reason_code: str = "",
    details: Optional[Dict] = None,
) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO transport_site_events (
                site_key, task_id, event_type, severity, reason_code,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site_key,
                task_id,
                event_type,
                severity,
                reason_code,
                json.dumps(details or {}, ensure_ascii=False),
                _now(),
            ),
        )


def record_audit(
    site_key: str,
    action: str,
    old_value,
    new_value,
    note: str = "",
    client_address: str = "",
    request_id: str = "",
) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO transport_policy_audit (
                site_key, action, old_value_json, new_value_json, note,
                client_address, request_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site_key,
                action,
                json.dumps(old_value, ensure_ascii=False, default=str),
                json.dumps(new_value, ensure_ascii=False, default=str),
                note,
                client_address,
                request_id,
                _now(),
            ),
        )


def record_task_created(site_key: str, task_id: int) -> None:
    now = _now()
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE transport_site_stats
            SET tasks_created = tasks_created + 1, updated_at = ?
            WHERE site_key = ?
            """,
            (now, site_key),
        )
    record_event(site_key, "task.created", task_id=task_id)


def backfill_task_site_keys() -> int:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, url
            FROM download_tasks
            WHERE transport_site_key IS NULL
               OR transport_site_key = ''
            ORDER BY id ASC
            """
        ).fetchall()
    updated = 0
    for row in rows:
        try:
            policy = ensure_site_for_url(row["url"], task_id=row["id"])
        except ValueError:
            continue
        with database.connect() as connection:
            connection.execute(
                "UPDATE download_tasks SET transport_site_key = ? WHERE id = ?",
                (policy["site_key"], row["id"]),
            )
        record_task_created(policy["site_key"], int(row["id"]))
        updated += 1
    return updated


def rebuild_transport_stats_from_tasks() -> int:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT p.site_key,
                   COUNT(dt.id) AS tasks_created,
                   SUM(CASE WHEN dt.status = 'success' THEN 1 ELSE 0 END)
                       AS tasks_completed,
                   SUM(CASE WHEN dt.status = 'failed' THEN 1 ELSE 0 END)
                       AS tasks_failed,
                   SUM(CASE WHEN dt.transport_kind = 'direct-range'
                            THEN 1 ELSE 0 END) AS range_attempts,
                   SUM(CASE WHEN dt.transport_kind = 'direct-range'
                                  AND dt.status = 'success'
                            THEN 1 ELSE 0 END) AS range_successes,
                   SUM(CASE WHEN dt.transport_kind = 'direct-range'
                                  AND dt.status = 'failed'
                            THEN 1 ELSE 0 END) AS range_failures,
                   SUM(CASE WHEN dt.resume_count > 0 THEN 1 ELSE 0 END)
                       AS resume_attempts,
                   SUM(CASE WHEN dt.resume_count > 0 AND dt.status = 'success'
                            THEN 1 ELSE 0 END) AS resume_successes,
                   SUM(CASE WHEN dt.media_refresh_count > 0 AND dt.status = 'success'
                            THEN 1 ELSE 0 END) AS url_refresh_successes,
                   SUM(CASE WHEN dt.status = 'success'
                            THEN dt.downloaded_bytes ELSE 0 END)
                       AS downloaded_bytes,
                   MAX(CASE WHEN dt.status = 'success'
                            THEN dt.downloaded_bytes ELSE 0 END)
                       AS largest_file_bytes
            FROM transport_site_policies p
            LEFT JOIN download_tasks dt
              ON dt.transport_site_key = p.site_key
             AND dt.deleted_at IS NULL
            GROUP BY p.site_key
            """
        ).fetchall()
    now = _now()
    with database.connect() as connection:
        for row in rows:
            connection.execute(
                """
                INSERT INTO transport_site_stats (
                    site_key, tasks_created, tasks_completed, tasks_failed,
                    range_attempts, range_successes, range_failures,
                    resume_attempts, resume_successes,
                    url_refresh_successes, downloaded_bytes,
                    largest_file_bytes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_key) DO UPDATE SET
                    tasks_created = excluded.tasks_created,
                    tasks_completed = excluded.tasks_completed,
                    tasks_failed = excluded.tasks_failed,
                    range_attempts = excluded.range_attempts,
                    range_successes = excluded.range_successes,
                    range_failures = excluded.range_failures,
                    resume_attempts = excluded.resume_attempts,
                    resume_successes = excluded.resume_successes,
                    url_refresh_successes = excluded.url_refresh_successes,
                    downloaded_bytes = excluded.downloaded_bytes,
                    largest_file_bytes = excluded.largest_file_bytes,
                    updated_at = excluded.updated_at
                """,
                (
                    row["site_key"],
                    int(row["tasks_created"] or 0),
                    int(row["tasks_completed"] or 0),
                    int(row["tasks_failed"] or 0),
                    int(row["range_attempts"] or 0),
                    int(row["range_successes"] or 0),
                    int(row["range_failures"] or 0),
                    int(row["resume_attempts"] or 0),
                    int(row["resume_successes"] or 0),
                    int(row["url_refresh_successes"] or 0),
                    int(row["downloaded_bytes"] or 0),
                    int(row["largest_file_bytes"] or 0),
                    now,
                ),
            )
    return len(rows)


def record_qualification(
    site_key: str,
    task_id: int,
    success: bool,
    reason: str = "",
) -> None:
    now = _now()
    field = (
        "qualification_successes"
        if success
        else "qualification_failures"
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE transport_site_stats SET {} = {} + 1, updated_at = ? "
            "WHERE site_key = ?".format(field, field),
            (now, site_key),
        )
        if success:
            connection.execute(
                """
                UPDATE transport_site_policies
                SET lifecycle_state = CASE
                        WHEN lifecycle_state = 'UNKNOWN'
                        THEN 'TASK_QUALIFIED'
                        ELSE lifecycle_state
                    END,
                    consecutive_failures = 0,
                    updated_at = ?
                WHERE site_key = ?
                """,
                (now, site_key),
            )
    record_event(
        site_key,
        "qualification.passed" if success else "qualification.failed",
        task_id=task_id,
        severity="info" if success else "warning",
        reason_code=reason,
    )


def record_ytdlp_fallback(
    site_key: str,
    task_id: int,
    reason: str,
) -> None:
    now = _now()
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE transport_site_stats
            SET ytdlp_fallbacks = ytdlp_fallbacks + 1, updated_at = ?
            WHERE site_key = ?
            """,
            (now, site_key),
        )
    record_event(
        site_key,
        "ytdlp.fallback",
        task_id=task_id,
        severity="warning",
        reason_code=reason[:500],
    )


def learn_transport_mode(
    site_key: str,
    task_id: Optional[int],
    transport_kind: str,
) -> bool:
    if transport_kind != "hls":
        return False
    now = _now()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE transport_site_policies
            SET transport_mode = 'hls',
                policy_version = policy_version + 1,
                updated_at = ?
            WHERE site_key = ?
              AND transport_mode = 'auto'
              AND manual_override = 0
            """,
            (now, site_key),
        )
    if not cursor.rowcount:
        return False
    record_event(
        site_key,
        "transport.hls_confirmed",
        task_id=task_id,
        details={"transport_kind": "hls", "transport_mode": "hls"},
    )
    return True


def record_task_result(task: Dict, success: bool) -> None:
    site_key = str(task.get("transport_site_key") or "").strip()
    if not site_key:
        return
    now = _now()
    transport = str(task.get("transport_kind") or "")
    downloaded = int(task.get("downloaded_bytes") or 0)
    updates = [
        "tasks_completed = tasks_completed + ?" if success
        else "tasks_failed = tasks_failed + 1",
        "updated_at = ?",
    ]
    params = [1 if success else now, now, site_key] if success else [now, site_key]
    breaker_opened = False
    breaker_closed = False
    with database.connect() as connection:
        connection.execute(
            "UPDATE transport_site_stats SET {} WHERE site_key = ?"
            .format(", ".join(updates)),
            params,
        )
        if transport == "direct-range":
            field = "range_successes" if success else "range_failures"
            connection.execute(
                "UPDATE transport_site_stats SET {} = {} + 1 WHERE site_key = ?"
                .format(field, field),
                (site_key,),
            )
        if success:
            connection.execute(
                """
                UPDATE transport_site_stats
                SET downloaded_bytes = downloaded_bytes + ?,
                    largest_file_bytes = MAX(largest_file_bytes, ?)
                WHERE site_key = ?
                """,
                (downloaded, downloaded, site_key),
            )
            connection.execute(
                "UPDATE transport_site_policies "
                "SET last_success_at = ?, consecutive_failures = 0, updated_at = ? "
                "WHERE site_key = ?",
                (now, now, site_key),
            )
            breaker = connection.execute(
                """
                UPDATE transport_site_policies
                SET breaker_state = 'CLOSED', disabled_until = NULL,
                    updated_at = ?
                WHERE site_key = ? AND breaker_state = 'HALF_OPEN'
                """,
                (now, site_key),
            )
            breaker_closed = bool(breaker.rowcount)
        else:
            connection.execute(
                """
                UPDATE transport_site_policies
                SET last_failure_at = ?,
                    consecutive_failures = consecutive_failures + 1,
                    updated_at = ?
                WHERE site_key = ?
                """,
                (now, now, site_key),
            )
            disabled_until = (
                datetime.now().astimezone() + timedelta(minutes=30)
            ).isoformat(timespec="seconds")
            breaker = connection.execute(
                """
                UPDATE transport_site_policies
                SET breaker_state = 'OPEN',
                    disabled_until = ?,
                    updated_at = ?
                WHERE site_key = ? AND lifecycle_state != 'DISABLED'
                  AND breaker_state = 'CLOSED'
                  AND consecutive_failures >= 3
                """,
                (disabled_until, now, site_key),
            )
            if breaker.rowcount:
                breaker_opened = True
                connection.execute(
                    "UPDATE transport_site_stats "
                    "SET breaker_opens = breaker_opens + 1 WHERE site_key = ?",
                    (site_key,),
                )
    if breaker_opened:
        record_event(
            site_key,
            "breaker.opened",
            task_id=task.get("id"),
            severity="warning",
            reason_code="consecutive-failures",
        )
    if breaker_closed:
        record_event(
            site_key,
            "breaker.closed",
            task_id=task.get("id"),
            severity="info",
        )
    if success:
        learn_transport_mode(site_key, task.get("id"), transport)
        maybe_promote_candidate(site_key)


def maybe_promote_candidate(site_key: str) -> bool:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT normalized_url) AS distinct_successes,
                   SUM(CASE WHEN resume_count > 0 THEN 1 ELSE 0 END)
                       AS resume_successes
            FROM download_tasks
            WHERE transport_site_key = ?
              AND status = 'success'
              AND transport_kind = 'direct-range'
              AND deleted_at IS NULL
            """,
            (site_key,),
        ).fetchone()
    if (
        int(row["distinct_successes"] or 0) < 3
        or int(row["resume_successes"] or 0) < 1
    ):
        return False
    now = _now()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE transport_site_policies
            SET lifecycle_state = 'CANDIDATE', updated_at = ?
            WHERE site_key = ? AND lifecycle_state = 'TASK_QUALIFIED'
            """,
            (now, site_key),
        )
    if cursor.rowcount:
        record_event(site_key, "site.candidate", severity="info")
        return True
    return False


def update_policy(
    site_key: str,
    data: Dict,
    client_address: str = "",
    request_id: str = "",
) -> Dict:
    policy = get_policy(site_key)
    if not policy:
        raise ValueError("站点配置不存在")
    expected_version = data.get("expected_policy_version")
    if (
        expected_version is not None
        and int(expected_version) != int(policy["policy_version"])
    ):
        raise ValueError("站点配置已被其他页面修改，请刷新后重试")
    expected_updated_at = str(data.get("expected_updated_at") or "")
    if expected_updated_at and expected_updated_at != policy["updated_at"]:
        raise ValueError("站点配置已被其他页面修改，请刷新后重试")
    note = str(data.get("note") or "").strip()
    fields = {}
    if "display_name" in data:
        fields["display_name"] = str(data["display_name"]).strip()
    if "domains" in data:
        domains = _parse_domains(data["domains"])
        if not domains:
            raise ValueError("至少需要一个域名")
        for other in list_policies():
            if other["site_key"] == site_key:
                continue
            if set(other["domains"]) & set(domains):
                raise ValueError("域名已属于其他站点配置")
        fields["domains_json"] = json.dumps(domains)
    if "display_name" in fields and not fields["display_name"]:
        raise ValueError("站点名称不能为空")
    for key in ("transport_mode", "lifecycle_state", "tls_backend", "status_note"):
        if key in data:
            value = str(data[key] or "")
            if key == "transport_mode" and value not in TRANSPORT_MODES:
                raise ValueError("传输模式无效")
            if key == "lifecycle_state":
                value = value.upper()
                if value not in LIFECYCLE_STATES:
                    raise ValueError("生命周期状态无效")
            if key == "tls_backend" and value not in {"auto", "httpx", "curl_cffi"}:
                raise ValueError("TLS 后端无效")
            fields[key] = value
    requested_state = fields.get("lifecycle_state")
    state_changed = (
        requested_state is not None
        and requested_state != policy["lifecycle_state"]
    )
    if (
        state_changed
        and requested_state in {"VERIFIED", "QUARANTINED", "DISABLED"}
        and not note
    ):
        raise ValueError("修改为 VERIFIED、QUARANTINED 或 DISABLED 时必须填写备注")
    if state_changed and requested_state == "VERIFIED":
        fields["promoted_at"] = _now()
        fields["promoted_by"] = "manual"
    for key in ("initial_connections", "max_connections"):
        if key in data:
            fields[key] = int(data[key])
    initial = int(fields.get("initial_connections", policy["initial_connections"]))
    maximum = int(fields.get("max_connections", policy["max_connections"]))
    if initial < 1 or initial > 2:
        raise ValueError("初始连接数必须在 1–2 之间")
    if maximum < 2 or maximum > 4:
        raise ValueError("最高连接数必须在 2–4 之间")
    if maximum < initial:
        raise ValueError("最高连接数不能小于初始连接数")
    fields["initial_connections"] = initial
    fields["max_connections"] = maximum
    if "breaker_state" in data:
        breaker = str(data["breaker_state"] or "").upper()
        if breaker not in BREAKER_STATES:
            raise ValueError("熔断状态无效")
        fields["breaker_state"] = breaker
        if breaker == "OPEN":
            if not note:
                raise ValueError("手动打开熔断时必须填写备注")
            try:
                cooldown_minutes = max(1, int(data.get("cooldown_minutes") or 30))
            except (TypeError, ValueError):
                cooldown_minutes = 30
            fields["disabled_until"] = (
                datetime.now().astimezone()
                + timedelta(minutes=cooldown_minutes)
            ).isoformat(timespec="seconds")
        elif breaker == "CLOSED":
            fields["disabled_until"] = None
            fields["consecutive_failures"] = 0
        elif breaker == "HALF_OPEN":
            fields["disabled_until"] = None
    fields["manual_override"] = 1
    fields["policy_version"] = int(policy["policy_version"]) + 1
    fields["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in fields)
    with database.connect() as connection:
        connection.execute(
            "UPDATE transport_site_policies SET {} WHERE site_key = ?"
            .format(assignments),
            list(fields.values()) + [site_key],
        )
    updated = get_policy(site_key)
    changed = {
        key: [policy.get(key), updated.get(key)]
        for key in fields
        if key not in {"updated_at", "policy_version"} and policy.get(key) != updated.get(key)
    }
    record_audit(
        site_key,
        "update",
        changed,
        {"policy_version": updated["policy_version"]},
        note,
        client_address,
        request_id,
    )
    return updated


def perform_action(
    site_key: str,
    action: str,
    note: str = "",
    client_address: str = "",
    request_id: str = "",
) -> Dict:
    policy = get_policy(site_key)
    if not policy:
        raise ValueError("站点配置不存在")
    now = _now()
    updates = {}
    if action == "promote_candidate":
        updates = {"lifecycle_state": "CANDIDATE"}
    elif action == "promote_verified":
        if not note:
            raise ValueError("提升为已验证站点时必须填写备注")
        updates = {
            "lifecycle_state": "VERIFIED",
            "promoted_at": now,
            "promoted_by": "manual",
            "manual_override": 1,
        }
    elif action == "demote_unknown":
        updates = {"lifecycle_state": "UNKNOWN", "manual_override": 1}
    elif action == "force_direct_range":
        updates = {"transport_mode": "direct-range", "manual_override": 1}
    elif action == "force_ytdlp":
        updates = {"transport_mode": "yt-dlp", "manual_override": 1}
    elif action == "disable":
        updates = {"lifecycle_state": "DISABLED", "manual_override": 1}
    elif action == "recheck":
        updates = {"breaker_state": "HALF_OPEN", "disabled_until": None}
    elif action == "clear_breaker":
        updates = {
            "breaker_state": "CLOSED",
            "disabled_until": None,
            "consecutive_failures": 0,
        }
    elif action == "reset_stats":
        with database.connect() as connection:
            connection.execute(
                "DELETE FROM transport_site_stats WHERE site_key = ?",
                (site_key,),
            )
            connection.execute(
                "INSERT INTO transport_site_stats (site_key, updated_at) "
                "VALUES (?, ?)",
                (site_key, now),
            )
        updated = get_policy(site_key)
        record_audit(site_key, action, policy, updated, note, client_address, request_id)
        return updated
    else:
        raise ValueError("不支持的站点操作")
    if note:
        updates["status_note"] = note
    if action != "recheck":
        updates["policy_version"] = int(policy["policy_version"]) + 1
    updates["updated_at"] = now
    assignments = ", ".join("{} = ?".format(key) for key in updates)
    with database.connect() as connection:
        connection.execute(
            "UPDATE transport_site_policies SET {} WHERE site_key = ?"
            .format(assignments),
            list(updates.values()) + [site_key],
        )
    updated = get_policy(site_key)
    record_audit(site_key, action, policy, updated, note, client_address, request_id)
    record_event(site_key, "manual.override", reason_code=action)
    return updated


def site_tasks(site_key: str, limit: int = 50) -> List[Dict]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM download_tasks
            WHERE transport_site_key = ? AND deleted_at IS NULL
            ORDER BY id DESC LIMIT ?
            """,
            (normalize_site_key(site_key), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def site_events(site_key: str, limit: int = 50) -> List[Dict]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM transport_site_events
            WHERE site_key = ?
            ORDER BY id DESC LIMIT ?
            """,
            (normalize_site_key(site_key), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def site_audit(site_key: str, limit: int = 50) -> List[Dict]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM transport_policy_audit
            WHERE site_key = ?
            ORDER BY id DESC LIMIT ?
            """,
            (normalize_site_key(site_key), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]
