from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import selectors
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, TextIO

from local_video_site import database
from local_video_site.core import config
from local_video_site.catalog.categories import rebuild_actor_index, rebuild_category_index
from local_video_site.integrations.metadata import fetch_site_metadata, metadata_parser_version, metadata_provider_for_url
from local_video_site.integrations.profiles import get_site_profile
from local_video_site.identity.permissions.content import validate_access_level
from local_video_site.media.info import probe_video
from local_video_site.media.quality import normalise_quality_label, resolution_label
from local_video_site.storage.deletion import delete_video_authorized
from local_video_site.downloads.policies.media import policy_for_task
from local_video_site.downloads.policies.transport import ensure_site_for_url, learn_transport_mode, record_qualification, record_ytdlp_fallback, record_task_result, record_task_created, resolve_transport_for_task
from local_video_site.media.assets.thumbnails import generate_thumbnail
from local_video_site.downloader.errors import permanent_download_error_message
from local_video_site.downloader.sniffer import redact_url
from .progress import (
    DELETE_EXIT_CODE,
    DIRECT_RANGE_CHUNK_RE,
    DIRECT_RANGE_INIT_RE,
    DOWNLOAD_QUALITY_OPTIONS,
    HLS_TRANSPORT_RE,
    MEDIA_RECOVERY_STATE_RE,
    MEDIA_REFRESH_START_RE,
    MEDIA_REFRESH_SUCCESS_RE,
    MEDIA_RANGE_VALIDATION_RE,
    MEDIA_SELECTED_RE,
    PAUSE_EXIT_CODE,
    PROGRESS_RE,
    RATE_LIMIT_PAUSE_EXIT_CODE,
    SHUTDOWN_EXIT_CODE,
    SIZE_TOKEN_RE,
    SPEED_TOKEN_RE,
    STALL_EXIT_CODE,
    TRANSPORT_FALLBACK_RE,
    TRANSPORT_QUALIFICATION_RE,
    _hls_fields_from_line,
    _media_fields_from_line,
    _media_fields_from_summary,
    _parse_progress_bytes,
    _parse_progress_metrics,
    _read_task_summary,
    _transport_kind_from_summary,
    normalise_requested_quality,
    normalize_url,
)


LOGGER = logging.getLogger(__name__)


def _resume_media_url(task: Dict) -> str:
    if not (
        str(task.get("partial_path") or "").strip()
        or str(task.get("checkpoint_path") or "").strip()
    ):
        return ""
    try:
        log_dirs = sorted(
            config.DOWNLOAD_TASK_LOG_DIR.glob(
                "task-{}-*".format(int(task["id"]))
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return ""
    for log_dir in log_dirs:
        summary = _read_task_summary(log_dir, task["url"]) or {}
        kind = str(
            summary.get("media_kind")
            or summary.get("selected_candidate_kind")
            or ""
        ).strip().lower()
        url = str(summary.get("media_url") or "").strip()
        if (
            kind not in {"hls", "dash", "file"}
            or not url.startswith(("http://", "https://"))
            or "<redacted>" in url
            or url == str(task.get("url") or "").strip()
        ):
            continue
        return url
    return ""
def _task_metadata_url(task: Dict) -> str:
    return str(task.get("metadata_url") or task.get("url") or "").strip()
def _is_cross_site_task(task: Dict) -> bool:
    metadata_url = _task_metadata_url(task)
    download_url = str(task.get("url") or "").strip()
    try:
        return normalize_url(metadata_url) != normalize_url(download_url)
    except ValueError:
        return False
def _prepare_cross_site_metadata(
    task: Dict,
    logger: logging.Logger,
):
    if not _is_cross_site_task(task):
        return None
    metadata_url = _task_metadata_url(task)
    provider = metadata_provider_for_url(metadata_url)
    if not provider:
        raise RuntimeError(
            "跨站任务的数据来源网站不受支持: {}".format(metadata_url)
        )
    database.update_download_task(
        task["id"],
        stage="metadata_source",
        message="正在从数据来源页面获取标题、演员和分类",
    )
    try:
        result = fetch_site_metadata(metadata_url, logger=logger)
    except Exception as exc:
        # Metadata prefetch is an optimization. The download child process
        # reads the source-page title separately and must still be allowed to
        # run when the shared browser cannot be used from this worker.
        logger.warning(
            "Download task metadata prefetch failed; continuing download "
            "task_id=%s url=%s error=%s",
            task["id"],
            metadata_url,
            exc,
            extra={"operation": "download.task.metadata.prefetch_failed"},
        )
        database.update_download_task(
            task["id"],
            message="数据来源信息获取失败，继续下载视频",
        )
        return None
    if not (result.title or result.categories or result.actors):
        logger.warning(
            "Download task metadata prefetch empty; continuing download "
            "task_id=%s url=%s error=%s",
            task["id"],
            metadata_url,
            result.error,
            extra={"operation": "download.task.metadata.prefetch_empty"},
        )
        database.update_download_task(
            task["id"],
            message="未获取到数据来源信息，继续下载视频",
        )
        return None
    source_title = str(task.get("title") or "").strip()
    metadata_title = result.title.strip()
    canonical_title = (
        metadata_title
        if (
            get_site_profile(provider).metadata_title_is_canonical
            and metadata_title
        )
        else source_title or metadata_title
    )
    if canonical_title:
        database.update_download_task(
            task["id"],
            title=canonical_title,
            filename_hint=canonical_title,
            message="数据来源信息已获取，准备下载视频",
        )
    task["_prefetched_metadata"] = result
    task["_canonical_title"] = canonical_title
    return result
def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
def _interrupt_process(process: subprocess.Popen, timeout: float = 15.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
    except OSError:
        _terminate_process(process)
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process(process)
def _keep_awake_command(process_id: int) -> Optional[List[str]]:
    if (
        sys.platform != "darwin"
        or not config.download_keep_awake_enabled()
    ):
        return None
    executable = shutil.which("caffeinate")
    if not executable:
        return None
    return [
        executable,
        config.download_keep_awake_flags(),
        "-w",
        str(int(process_id)),
    ]
def _start_keep_awake(
    process_id: int,
    task_id: int,
    logger: logging.Logger,
) -> Optional[subprocess.Popen]:
    command = _keep_awake_command(process_id)
    if not command:
        return None
    try:
        keeper = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        logger.warning(
            "Could not start keep-awake task_id=%s error=%s",
            task_id,
            exc,
            extra={"operation": "download.keep_awake.failed"},
        )
        return None
    logger.info(
        "Keep-awake started task_id=%s flags=%s pid=%s",
        task_id,
        config.download_keep_awake_flags(),
        process_id,
        extra={"operation": "download.keep_awake.started"},
    )
    return keeper
def _stop_keep_awake(
    keeper: Optional[subprocess.Popen],
    task_id: int,
    logger: logging.Logger,
) -> None:
    if keeper is None:
        return
    try:
        keeper.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            keeper.terminate()
            keeper.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                keeper.kill()
            except OSError:
                pass
    logger.info(
        "Keep-awake stopped task_id=%s",
        task_id,
        extra={"operation": "download.keep_awake.stopped"},
    )
def _find_partial_files(task: Dict) -> tuple:
    output_dir = Path(task["output_dir"])
    try:
        files = [path for path in output_dir.iterdir() if path.is_file()]
    except OSError:
        return None, None
    hint = str(task.get("filename_hint") or task.get("title") or "").strip()
    candidates = [
        path
        for path in files
        if (not hint or path.name.startswith(hint))
        and (
            path.name.endswith(".part")
            or path.name.endswith(".ytdl")
            or path.name.endswith(".resume.json")
            or path.name.endswith(".range.json")
        )
    ]
    partial = next(
        (
            path
            for path in sorted(
                candidates,
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if path.name.endswith(".part")
        ),
        None,
    )
    checkpoint = next(
        (
            path
            for path in candidates
            if path.name.endswith((".resume.json", ".range.json"))
        ),
        None,
    )
    return (
        str(partial) if partial else None,
        str(checkpoint) if checkpoint else None,
    )
def _delete_control_marker(task_id: int) -> Path:
    return config.DOWNLOAD_CONTROL_DIR / "task-{}.delete".format(int(task_id))
def _task_files_for_deletion(task: Dict) -> List[Path]:
    root = Path(task["output_dir"]).resolve()
    candidates: List[Path] = []
    for value in (task.get("partial_path"), task.get("checkpoint_path")):
        if not value:
            continue
        path = Path(value)
        if path.is_symlink():
            raise PermissionError("拒绝删除符号链接任务文件")
        resolved = path.resolve()
        if root not in resolved.parents:
            raise PermissionError("任务文件不在下载目录中")
        candidates.append(resolved)

    if task.get("file_path") and not task.get("video_id"):
        path = Path(task["file_path"])
        if path.is_symlink():
            raise PermissionError("拒绝删除符号链接视频文件")
        resolved = path.resolve()
        if root not in resolved.parents:
            raise PermissionError("视频文件不在任务下载目录中")
        candidates.append(resolved)

    hint = str(task.get("filename_hint") or task.get("title") or "").strip()
    if hint:
        try:
            discovered = [path for path in root.iterdir() if path.is_file()]
        except OSError:
            discovered = []
        candidates.extend(
            path
            for path in discovered
            if not path.is_symlink()
            if path.name.startswith(hint)
            and (
                path.name.endswith(".part")
                or path.name.endswith(".ytdl")
                or path.name.endswith(".resume.json")
                or path.name.endswith(".range.json")
            )
        )

    unique: List[Path] = []
    seen = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        unique.append(path)
    return unique
def _delete_task_resources(task: Dict, logger: logging.Logger) -> Dict:
    deleted_files = 0
    deleted_bytes = 0
    video_deleted = False
    video_id = task.get("video_id")
    video_exists = bool(video_id and database.get_video(int(video_id)))
    if video_exists:
        delete_video_authorized(int(video_id or 0))
        video_deleted = True
    else:
        task = {**task, "video_id": None}

    for path in _task_files_for_deletion(task):
        try:
            deleted_bytes += path.stat().st_size
            path.unlink()
            deleted_files += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception(
                "Could not delete download task file task_id=%s path=%s",
                task["id"],
                path,
            )
            raise
    _delete_control_marker(task["id"]).unlink(missing_ok=True)
    pause_marker = config.DOWNLOAD_CONTROL_DIR / "task-{}.pause".format(
        task["id"]
    )
    pause_marker.unlink(missing_ok=True)
    return {
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "video_deleted": video_deleted,
    }
def _run_download_process(
    task: Dict,
    stop_event: threading.Event,
    logger: logging.Logger,
) -> tuple:
    log_dir = (
        config.DOWNLOAD_TASK_LOG_DIR
        / "task-{}-{}".format(
            task["id"],
            datetime.now().strftime("%Y%m%d-%H%M%S"),
        )
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    pause_marker = config.DOWNLOAD_CONTROL_DIR / "task-{}.pause".format(
        task["id"]
    )
    try:
        pause_marker.unlink()
    except FileNotFoundError:
        pass
    database.update_download_task(
        task["id"],
        stage="opening_page",
        log_path=str(log_dir),
        message="正在打开网页并发现视频",
    )
    transport = resolve_transport_for_task(task)
    transport_site_key = transport["site_key"]
    database.update_download_task(
        task["id"],
        transport_site_key=transport_site_key,
    )
    resume_validation = str(task.get("resume_validation") or "")
    media_policy_json = str(
        task.get("media_policy_snapshot_json") or ""
    ).strip()
    if media_policy_json:
        try:
            media_policy = json.loads(media_policy_json)
        except (TypeError, ValueError):
            media_policy = None
    else:
        media_policy = None
    if not isinstance(media_policy, dict):
        media_policy = policy_for_task(
            transport_site_key,
            normalise_requested_quality(task.get("requested_quality")),
        )
        database.update_download_task(
            task["id"],
            media_strategy_key=media_policy["preset_key"],
            media_strategy_version=media_policy["version"],
            media_policy_snapshot_json=json.dumps(
                media_policy,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    media_policy_path = log_dir / "media-policy.json"
    media_policy_path.write_text(
        json.dumps(media_policy, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "local_video_site.downloader.entrypoints.download_video",
        "--output",
        task["output_dir"],
        "--log-dir",
        str(log_dir),
        "--cdp-port",
        str(config.download_cdp_port()),
        "--reuse-cdp",
        "--quality",
        normalise_requested_quality(task.get("requested_quality")),
        "--media-policy-file",
        str(media_policy_path),
        "--transport-mode",
        transport["child_mode"],
        "--tls-backend",
        str(transport["policy"].get("tls_backend") or "auto"),
        "--range-initial-connections",
        str(transport["policy"].get("initial_connections") or 2),
        "--range-max-connections",
        str(transport["policy"].get("max_connections") or 4),
    ]
    if media_policy.get("allow_below_minimum"):
        command.append("--allow-below-minimum-quality")
    if media_policy.get("play_selector"):
        command.extend([
            "--play-selector",
            str(media_policy["play_selector"]),
        ])
    resume_media_url = _resume_media_url(task)
    if resume_media_url:
        command.extend(["--media-url", resume_media_url])
        logger.info(
            "Resuming with previously discovered media URL task_id=%s url=%s",
            task["id"],
            redact_url(resume_media_url),
            extra={"operation": "download.task.resume_media"},
        )
    if task.get("episode_id") and str(task.get("output_stem") or "").strip():
        command.extend([
            "--series-name",
            str(task["output_stem"]).strip(),
        ])
    elif _is_cross_site_task(task):
        command.extend(["--metadata-url", _task_metadata_url(task)])
        title = str(task.get("_canonical_title") or "").strip()
        if title:
            command.extend(["--series-name", title])
    if not int(task.get("index_into_library", 1)):
        command.append("--no-library-registration")
    command.append(task["url"])
    logger.info(
        "Download task process started task_id=%s url=%s output=%s",
        task["id"],
        task["normalized_url"],
        task["output_dir"],
        extra={"operation": "download.task.start"},
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    keep_awake = _start_keep_awake(process.pid, task["id"], logger)
    selector = selectors.DefaultSelector()
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + config.download_task_timeout_seconds()
    last_progress_at = time.monotonic()
    last_heartbeat_at = 0.0
    stall_timeout = config.download_stall_timeout_seconds()
    output_lines = []
    filename_hint = ""
    fallback_recorded = False
    transport_kind_recorded = bool(task.get("transport_kind"))
    try:
        while True:
            if stop_event.is_set():
                _interrupt_process(process)
                return (
                    SHUTDOWN_EXIT_CODE,
                    log_dir,
                    "服务停止，任务已中断",
                )
            if database.download_task_delete_requested(task["id"]):
                database.update_download_task(
                    task["id"],
                    status="deleting",
                    stage="deleting",
                    message="正在停止下载并删除任务文件",
                    heartbeat_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                )
                _interrupt_process(process)
                return (
                    DELETE_EXIT_CODE,
                    log_dir,
                    "下载已停止，正在删除任务文件",
                )
            if database.download_task_pause_requested(task["id"]):
                database.update_download_task(
                    task["id"],
                    status="pausing",
                    stage="pausing",
                    pause_requested=1,
                    message="正在保存断点",
                    heartbeat_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                )
                _interrupt_process(process)
                return (
                    PAUSE_EXIT_CODE,
                    log_dir,
                    "已暂停，断点已保留",
                )
            now = time.monotonic()
            if now - last_progress_at >= stall_timeout:
                database.update_download_task(
                    task["id"],
                    stage="recovering",
                    message="检测到下载停滞，正在保存断点并刷新会话",
                    heartbeat_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                )
                _interrupt_process(process)
                return (
                    STALL_EXIT_CODE,
                    log_dir,
                    "超过 {} 秒没有下载进度".format(stall_timeout),
                )
            if now - last_heartbeat_at >= 5.0:
                database.update_download_task(
                    task["id"],
                    heartbeat_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                )
                last_heartbeat_at = now
            if now > deadline:
                _terminate_process(process)
                return -1, log_dir, "下载超时"
            events = selector.select(timeout=1.0)
            for key, _ in events:
                line = getattr(key.fileobj, "readline")()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                line = line.rstrip()
                output_lines.append(line)
                if len(output_lines) > 500:
                    output_lines.pop(0)
                if "Media discovery started" in line:
                    database.update_download_task(
                        task["id"],
                        stage="discovering_media",
                        message="正在发现并验证视频媒体",
                    )
                match = PROGRESS_RE.search(line)
                if "Final series name:" in line:
                    filename_hint = line.split(
                        "Final series name:",
                        1,
                    )[1].strip()
                    database.update_download_task(
                        task["id"],
                        title=filename_hint,
                        filename_hint=filename_hint,
                    )
                media_fields = _media_fields_from_line(line)
                if media_fields:
                    database.update_download_task(
                        task["id"],
                        stage="downloading",
                        message="已选择视频媒体，准备下载",
                        **media_fields,
                    )
                    logger.info(
                        "Download task media resolved task_id=%s "
                        "quality=%s resolution=%s source=process-output",
                        task["id"],
                        media_fields.get("media_quality"),
                        resolution_label(
                            media_fields.get("media_width"),
                            media_fields.get("media_height"),
                        ),
                        extra={"operation": "download.task.media_resolved"},
                    )
                if not transport_kind_recorded and HLS_TRANSPORT_RE.search(line):
                    database.update_download_task(
                        task["id"],
                        transport_kind="hls",
                    )
                    transport_kind_recorded = True
                    logger.info(
                        "Download task transport resolved task_id=%s "
                        "transport=hls",
                        task["id"],
                        extra={"operation": "download.task.transport_resolved"},
                    )
                hls_fields = _hls_fields_from_line(line)
                if hls_fields:
                    database.update_download_task(
                        task["id"],
                        transport_kind="hls",
                        **hls_fields,
                    )
                recovery_match = MEDIA_RECOVERY_STATE_RE.search(line)
                if recovery_match:
                    resume_validation = (
                        "pending"
                        if int(task.get("downloaded_bytes") or 0) > 0
                        else "not_required"
                    )
                    database.update_download_task(
                        task["id"],
                        media_url_expires_at=recovery_match.group("expires"),
                        media_identity=recovery_match.group("identity"),
                        resume_validation=resume_validation,
                    )
                refresh_start = MEDIA_REFRESH_START_RE.search(line)
                if refresh_start:
                    database.update_download_task(
                        task["id"],
                        stage="refreshing_media",
                        message=(
                            "媒体链接即将或已经过期，正在刷新地址并保留断点"
                        ),
                        media_refresh_count=int(
                            refresh_start.group("count")
                        ),
                    )
                refresh_success = MEDIA_REFRESH_SUCCESS_RE.search(line)
                if refresh_success:
                    database.update_download_task(
                        task["id"],
                        stage="validating_resume",
                        message="媒体地址已刷新，正在校验断点",
                        media_url_expires_at=refresh_success.group("expires"),
                        last_media_refresh_at=datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    )
                range_match = MEDIA_RANGE_VALIDATION_RE.search(line)
                if range_match:
                    status = int(range_match.group("status"))
                    resume_validation = (
                        "success" if status == 206 else "failed"
                    )
                    total_match = re.search(
                        r"/([0-9]+)\s*$",
                        range_match.group("range"),
                    )
                    database.update_download_task(
                        task["id"],
                        resume_validation=resume_validation,
                        resume_total_bytes=(
                            int(total_match.group(1))
                            if total_match
                            else None
                        ),
                        message=(
                            "断点校验通过，正在继续下载"
                            if status == 206
                            else "断点校验未通过"
                        ),
                    )
                direct_init = DIRECT_RANGE_INIT_RE.search(line)
                if direct_init:
                    database.update_download_task(
                        task["id"],
                        transport_kind="direct-range",
                        range_connection_count=int(
                            direct_init.group("connections")
                        ),
                        range_completed_chunks=int(
                            direct_init.group("completed")
                        ),
                        range_total_chunks=int(
                            direct_init.group("total")
                        ),
                    )
                direct_chunk = DIRECT_RANGE_CHUNK_RE.search(line)
                if direct_chunk:
                    database.update_download_task(
                        task["id"],
                        transport_kind="direct-range",
                        range_connection_count=int(
                            direct_chunk.group("connections")
                        ),
                        range_completed_chunks=int(
                            direct_chunk.group("completed")
                        ),
                        range_total_chunks=int(
                            direct_chunk.group("total")
                        ),
                    )
                qualification = TRANSPORT_QUALIFICATION_RE.search(line)
                if qualification:
                    record_qualification(
                        transport_site_key,
                        task["id"],
                        qualification.group("status").lower() == "passed",
                        (qualification.group("reason") or "").strip(),
                    )
                if not fallback_recorded and TRANSPORT_FALLBACK_RE.search(line):
                    fallback_recorded = True
                    record_ytdlp_fallback(
                        transport_site_key,
                        task["id"],
                        line,
                    )
                now = time.monotonic()
                if match and now - last_progress_at >= 1.5:
                    progress = min(100.0, float(match.group(1)))
                    downloaded, total, speed, eta = _parse_progress_metrics(
                        line[match.end():].lstrip(" |"),
                        progress,
                    )
                    database.update_download_task(
                        task["id"],
                        stage="downloading",
                        progress_percent=progress,
                        downloaded_bytes=downloaded,
                        total_bytes=total,
                        speed_bytes_per_sec=speed,
                        eta_seconds=eta,
                        last_progress_at=datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        stall_count=0,
                        message=(
                            "下载进度 {:.1f}% · 已阻止系统睡眠".format(
                                progress
                            )
                            if keep_awake is not None
                            and keep_awake.poll() is None
                            else "下载进度 {:.1f}%".format(progress)
                        ),
                        heartbeat_at=datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        **(
                            {"resume_validation": "success"}
                            if resume_validation == "pending"
                            else {}
                        ),
                    )
                    if resume_validation == "pending":
                        resume_validation = "success"
                    last_progress_at = now
                    last_heartbeat_at = now
            if process.poll() is not None:
                break
        return process.returncode or 0, log_dir, "\n".join(output_lines[-50:])
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process(process)
        _stop_keep_awake(keep_awake, task["id"], logger)
def _sync_video_metadata(
    video_id: int,
    source_url: str,
    logger: logging.Logger,
    prefetched=None,
    technical_only: bool = False,
) -> Dict:
    video = database.get_video(video_id)
    if not video:
        return {
            "status": "failed",
            "error": "视频未写入视频库",
            "category_count": 0,
            "actor_count": 0,
        }
    path = Path(video["path"])
    if technical_only:
        info = probe_video(path)
        if info:
            database.update_video_technical_info(video_id, info)
        if not video.get("thumbnail_path"):
            thumbnail = generate_thumbnail(
                video_path=path,
                size=int(video["size"]),
                mtime_ns=int(video["mtime_ns"]),
                duration=(
                    info.get("duration")
                    if info
                    else video.get("duration")
                ),
            )
            if thumbnail:
                database.update_thumbnail(video_id, str(thumbnail))
        return {
            "status": "inherited" if info else "unavailable",
            "error": None if info else "视频技术信息读取失败",
            "category_count": 0,
            "actor_count": 0,
        }
    thumbnail_path = Path(video.get("thumbnail_path") or "")
    if (
        video.get("metadata_status") == "ready"
        and thumbnail_path.is_file()
    ):
        info = {
            "duration": video.get("duration"),
            "width": video.get("width"),
            "height": video.get("height"),
            "video_codec": video.get("video_codec"),
            "audio_codec": video.get("audio_codec"),
        }
    else:
        info = probe_video(path)
        thumbnail = generate_thumbnail(
            video_path=path,
            size=int(video["size"]),
            mtime_ns=int(video["mtime_ns"]),
            duration=info.get("duration"),
        )
        database.update_metadata(
            video_id=video_id,
            info=info,
            thumbnail_path=str(thumbnail) if thumbnail else None,
            status="ready" if info else "unavailable",
            error=None if info else "视频元数据读取失败",
        )
    metadata_error = None
    if metadata_provider_for_url(source_url):
        if prefetched is None:
            try:
                _refresh_video_metadata_in_subprocess(video_id, logger)
            except Exception as exc:
                metadata_error = str(exc)
                logger.exception(
                    "Download task metadata refresh failed video_id=%s "
                    "provider=%s",
                    video_id,
                    metadata_provider_for_url(source_url),
                    extra={"operation": "download.task.metadata.failed"},
                )
        else:
            try:
                if prefetched.categories or prefetched.actors or prefetched.title:
                    database.save_category_fetch_cache(
                        video_id=video_id,
                        source_url=source_url,
                        status="fetched",
                        categories=prefetched.categories,
                        actors=prefetched.actors,
                        http_status=prefetched.http_status,
                        parser_version=metadata_parser_version(
                            metadata_provider_for_url(source_url) or ""
                        ),
                        metadata_language=getattr(
                            prefetched,
                            "language",
                            "",
                        ),
                    )
                else:
                    metadata_error = (
                        prefetched.error or "未获取到分类或演员"
                    )
                    database.save_category_fetch_cache(
                        video_id=video_id,
                        source_url=source_url,
                        status="failed",
                        actors=[],
                        http_status=prefetched.http_status,
                        error=metadata_error,
                        actor_metadata_version=0,
                    )
            except Exception as exc:
                metadata_error = str(exc)
                logger.exception(
                    "Download task prefetched metadata save failed video_id=%s",
                    video_id,
                    extra={"operation": "download.task.metadata.failed"},
                )
    rebuild_category_index(clear=False)
    rebuild_actor_index(clear=False)
    categories = database.categories_for_video(video_id)
    actors = database.actors_for_video(video_id)
    return {
        "status": "success" if not metadata_error else "failed",
        "error": metadata_error,
        "category_count": len(categories),
        "actor_count": len(actors),
    }
def _metadata_completion_result(metadata: Dict) -> tuple:
    status = str((metadata or {}).get("status") or "failed").strip().lower()
    if status in {"success", "inherited"}:
        return status, None
    error = str(
        (metadata or {}).get("error") or "failed"
    ).strip()
    return status or "failed", error or "failed"
def _apply_task_access_level(task: Dict, video_id: int) -> Dict:
    level = validate_access_level(task.get("access_level", 0))
    return database.set_video_access_level(int(video_id), level)
def _refresh_video_metadata_in_subprocess(
    video_id: int,
    logger: logging.Logger,
) -> None:
    """Run site metadata refresh outside the service asyncio process."""

    command = [
        sys.executable,
        "-m",
        "local_video_site",
        "metadata-refresh",
        "--video-id",
        str(int(video_id)),
        "--refresh",
    ]
    completed = subprocess.run(
        command,
        cwd=str(config.PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        detail = ""
        for line in reversed(completed.stdout.splitlines()):
            try:
                report = json.loads(line)
            except (TypeError, ValueError):
                continue
            errors = [
                str(item) for item in report.get("errors") or []
                if str(item or "").strip()
            ]
            if errors:
                detail = "; ".join(errors)
            else:
                detail = json.dumps(report, ensure_ascii=False)
            break
        if not detail:
            detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "元数据刷新失败{}".format(
                ": {}".format(detail[-2000:]) if detail else ""
            )
        )
    logger.info(
        "Download task metadata refreshed in subprocess video_id=%s",
        video_id,
        extra={"operation": "download.task.metadata.refreshed"},
    )
def _retry_delay_seconds(attempt: int) -> int:
    delays = config.download_retry_delays_seconds()
    return delays[min(max(0, attempt - 1), len(delays) - 1)]


class DownloadTaskWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.thread: Optional[threading.Thread] = None
        self.lock_handle: Optional[TextIO] = None

    def start(self) -> "DownloadTaskWorker":
        config.ensure_data_directories()
        lock_path = config.DATA_DIR / "download-worker.lock"
        self.lock_handle = lock_path.open("a+")
        deadline = time.monotonic() + 15.0
        locked = False
        while time.monotonic() < deadline:
            try:
                fcntl.flock(
                    self.lock_handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                locked = True
                break
            except BlockingIOError:
                time.sleep(0.25)
        if not locked:
            self.lock_handle.close()
            self.lock_handle = None
            self.logger.warning(
                "Download task worker not started because another worker "
                "holds the lock"
            )
            return self
        self.thread = threading.Thread(
            target=self._loop,
            name="videos-download-worker",
            daemon=True,
        )
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
        if self.lock_handle is not None:
            self.lock_handle.close()
            self.lock_handle = None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            deletion = database.next_pending_download_deletion()
            if deletion is not None:
                self._finalize_deletion(deletion)
                continue
            task = database.claim_download_task()
            if task is None:
                self.stop_event.wait(config.download_poll_seconds())
                continue
            self._process(task)

    def _process(self, task: Dict) -> None:
        try:
            download_only = not int(task.get("index_into_library", 1))
            if not download_only:
                _prepare_cross_site_metadata(task, self.logger)
            returncode, log_dir, detail = _run_download_process(
                task,
                self.stop_event,
                self.logger,
            )
            current = database.get_download_task(task["id"]) or task
            if current.get("deleted_at") or current.get("status") == "deleted":
                return
            if (
                returncode == DELETE_EXIT_CODE
                or current.get("delete_requested")
                or current.get("status") == "deleting"
            ):
                self._finalize_deletion(current)
                return
            if returncode == RATE_LIMIT_PAUSE_EXIT_CODE:
                current = database.get_download_task(task["id"]) or task
                partial, checkpoint = _find_partial_files(current)
                database.finish_pause_download_task(
                    task["id"],
                    partial_path=partial,
                    checkpoint_path=checkpoint,
                    message=(
                        "检测到 CDN 限流，已自动暂停。"
                        "建议稍后继续下载。"
                    ),
                )
                self.logger.warning(
                    "Download task auto-paused by CDN rate limit task_id=%s",
                    task["id"],
                    extra={"operation": "download.task.rate_limited"},
                )
                return
            if returncode == PAUSE_EXIT_CODE:
                self._mark_paused(task)
                return
            if returncode == STALL_EXIT_CODE:
                current = database.get_download_task(task["id"]) or task
                partial, checkpoint = _find_partial_files(current)
                outcome = database.record_download_stall(
                    task["id"],
                    detail or "下载进度停滞",
                    partial_path=partial,
                    checkpoint_path=checkpoint,
                )
                self.logger.warning(
                    "Download task stalled task_id=%s outcome=%s "
                    "partial=%s checkpoint=%s",
                    task["id"],
                    outcome,
                    partial,
                    checkpoint,
                    extra={"operation": "download.task.stalled"},
                )
                return
            if returncode == SHUTDOWN_EXIT_CODE:
                database.update_download_task(
                    task["id"],
                    status="pending",
                    stage="waiting",
                    pause_requested=0,
                    message="服务停止，任务等待恢复",
                    heartbeat_at=None,
                )
                return
            summary = _read_task_summary(log_dir, task["url"]) or {}
            output_path = str(summary.get("output_path") or "").strip()
            if returncode == 0 and download_only:
                output_file = Path(output_path).resolve() if output_path else None
                if output_file is None or not output_file.is_file():
                    raise RuntimeError("仅下载任务未生成有效视频文件")
                size = output_file.stat().st_size
                media_fields = _media_fields_from_summary(summary, {})
                transport = _transport_kind_from_summary(summary)
                database.update_download_task(
                    task["id"],
                    title=(
                        str(summary.get("series_name") or "").strip()
                        or task.get("title")
                        or output_file.stem
                    ),
                    file_path=str(output_file),
                    file_name=output_file.name,
                    status="success",
                    stage="finished",
                    progress_percent=100,
                    downloaded_bytes=size,
                    total_bytes=size,
                    message="下载完成，未写入视频库",
                    error_detail=None,
                    metadata_status="not_required",
                    category_count=0,
                    actor_count=0,
                    **media_fields,
                    **(
                        {"transport_kind": transport}
                        if transport
                        else {}
                    ),
                    heartbeat_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    finished_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                )
                completed_task = (
                    database.get_download_task(task["id"]) or task
                )
                record_task_result(completed_task, True)
                self.logger.info(
                    "Download-only task succeeded task_id=%s path=%s",
                    task["id"],
                    output_file,
                    extra={"operation": "download.task.download_only.success"},
                )
                marker = config.DOWNLOAD_CONTROL_DIR / (
                    "task-{}.pause".format(task["id"])
                )
                marker.unlink(missing_ok=True)
                return
            video = (
                database.get_video_by_path(Path(output_path).resolve())
                if output_path
                else None
            )
            if returncode == 0 and video:
                current = database.get_download_task(task["id"]) or task
                if (
                    current.get("delete_requested")
                    or current.get("status") == "deleting"
                ):
                    current["video_id"] = video["id"]
                    current["file_path"] = str(video["path"])
                    self._finalize_deletion(current)
                    return
                episode = database.link_episode_download_task(
                    task["id"],
                    video["id"],
                    Path(video["path"]),
                )
                _apply_task_access_level(task, video["id"])
                media_fields = _media_fields_from_summary(summary, video)
                if media_fields:
                    self.logger.info(
                        "Download task media updated task_id=%s "
                        "quality=%s resolution=%s source=summary",
                        task["id"],
                        media_fields.get("media_quality"),
                        resolution_label(
                            media_fields.get("media_width"),
                            media_fields.get("media_height"),
                        ),
                        extra={"operation": "download.task.media_updated"},
                    )
                summary_transport = _transport_kind_from_summary(summary)
                database.update_download_task(
                    task["id"],
                    title=(
                        episode.get("display_title")
                        if episode
                        else summary.get("series_name") or video["title"]
                    ),
                    file_path=str(video["path"]),
                    file_name=Path(video["path"]).name,
                    video_id=video["id"],
                    stage="metadata",
                    progress_percent=100,
                    downloaded_bytes=int(video["size"]),
                    total_bytes=int(video["size"]),
                    message="视频下载完成，正在获取分类和演员",
                    **media_fields,
                    **(
                        {"transport_kind": summary_transport}
                        if summary_transport
                        else {}
                    ),
                )
                refreshed_video = database.get_video(video["id"]) or video
                inherits_site_metadata = bool(
                    episode
                    and refreshed_video.get("metadata_status") == "inherited"
                )
                metadata = _sync_video_metadata(
                    video["id"],
                    _task_metadata_url(task),
                    self.logger,
                    prefetched=task.get("_prefetched_metadata"),
                    technical_only=inherits_site_metadata,
                )
                refreshed_video = database.get_video(video["id"]) or video
                media_fields.update(
                    _media_fields_from_summary({}, refreshed_video)
                )
                current = database.get_download_task(task["id"]) or task
                if (
                    current.get("delete_requested")
                    or current.get("status") == "deleting"
                ):
                    current["video_id"] = video["id"]
                    current["file_path"] = str(video["path"])
                    self._finalize_deletion(current)
                    return
                metadata_status, metadata_error = (
                    _metadata_completion_result(metadata)
                )
                message = "success"
                if metadata_error:
                    message += "; metadata: {}".format(metadata_error)
                database.update_download_task(
                    task["id"],
                    status="success",
                    stage="finished",
                    progress_percent=100,
                    downloaded_bytes=int(video["size"]),
                    total_bytes=int(video["size"]),
                    message=message,
                    error_detail=metadata_error,
                    metadata_status=metadata_status,
                    category_count=metadata["category_count"],
                    actor_count=metadata["actor_count"],
                    **media_fields,
                    heartbeat_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    finished_at=datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                )
                completed_task = (
                    database.get_download_task(task["id"]) or current
                )
                record_task_result(completed_task, True)
                self.logger.info(
                    "Download task succeeded task_id=%s video_id=%s",
                    task["id"],
                    video["id"],
                    extra={"operation": "download.task.success"},
                )
                marker = config.DOWNLOAD_CONTROL_DIR / (
                    "task-{}.pause".format(task["id"])
                )
                marker.unlink(missing_ok=True)
                return
            current = database.get_download_task(task["id"]) or {}
            if (
                current.get("status") == "pausing"
                or current.get("pause_requested")
            ):
                self._mark_paused(task)
                return
            error = str(summary.get("error") or "").strip()
            if not error:
                error = detail or "下载进程失败"
            self._schedule_failure(task, error)
        except Exception as exc:
            self.logger.exception(
                "Download task worker failure task_id=%s",
                task["id"],
                extra={"operation": "download.task.worker_failed"},
            )
            self._schedule_failure(task, str(exc))

    def _finalize_deletion(self, task: Dict) -> None:
        self.logger.info(
            "Download task deletion started task_id=%s status=%s",
            task["id"],
            task.get("status"),
            extra={"operation": "download.task.delete.start"},
        )
        try:
            result = _delete_task_resources(task, self.logger)
            database.complete_download_task_deletion(task["id"])
        except Exception as exc:
            database.fail_download_task_deletion(task["id"], str(exc))
            self.logger.exception(
                "Download task deletion failed task_id=%s",
                task["id"],
                extra={"operation": "download.task.delete.failed"},
            )
            return
        self.logger.info(
            "Download task deleted task_id=%s files=%s bytes=%s video=%s",
            task["id"],
            result["deleted_files"],
            result["deleted_bytes"],
            result["video_deleted"],
            extra={"operation": "download.task.delete.complete"},
        )

    def _mark_paused(self, task: Dict) -> None:
        current = database.get_download_task(task["id"]) or task
        partial, checkpoint = _find_partial_files(current)
        database.finish_pause_download_task(
            task["id"],
            partial_path=partial,
            checkpoint_path=checkpoint,
            message=(
                "已暂停，断点已保留"
                if partial or checkpoint
                else "已暂停"
            ),
        )
        marker = config.DOWNLOAD_CONTROL_DIR / (
            "task-{}.pause".format(task["id"])
        )
        marker.unlink(missing_ok=True)
        self.logger.info(
            "Download task paused task_id=%s partial=%s checkpoint=%s",
            task["id"],
            partial,
            checkpoint,
            extra={"operation": "download.task.paused"},
        )

    def _schedule_failure(self, task: Dict, error: str) -> None:
        attempts = int(task.get("attempts") or 0) + 1
        max_attempts = int(task.get("max_attempts") or 1)
        now = datetime.now().astimezone()
        terminal_message = permanent_download_error_message(error)
        if terminal_message:
            database.update_download_task(
                task["id"],
                status="failed",
                stage="finished",
                message=terminal_message,
                error_detail=str(error)[:4000],
                attempts=attempts,
                next_retry_at=None,
                heartbeat_at=None,
                finished_at=now.isoformat(timespec="seconds"),
            )
            failed_task = database.get_download_task(task["id"]) or task
            record_task_result(failed_task, False)
            self.logger.warning(
                "Download task failed permanently task_id=%s error=%s",
                task["id"],
                terminal_message,
                extra={"operation": "download.task.permanent_failed"},
            )
            return
        if attempts < max_attempts:
            retry_at = now + timedelta(
                seconds=_retry_delay_seconds(attempts)
            )
            database.update_download_task(
                task["id"],
                status="pending",
                stage="waiting",
                message="第 {} 次失败，等待重试: {}".format(
                    attempts,
                    error,
                )[:1000],
                error_detail=error[:4000],
                attempts=attempts,
                next_retry_at=retry_at.isoformat(timespec="seconds"),
                heartbeat_at=None,
            )
        else:
            database.update_download_task(
                task["id"],
                status="failed",
                stage="finished",
                message=error[:1000],
                error_detail=error[:4000],
                attempts=attempts,
                finished_at=now.isoformat(timespec="seconds"),
            )
            failed_task = database.get_download_task(task["id"]) or task
            record_task_result(failed_task, False)
        self.logger.warning(
            "Download task failed task_id=%s attempts=%s/%s error=%s",
            task["id"],
            attempts,
            max_attempts,
            error,
            extra={"operation": "download.task.failed"},
        )
