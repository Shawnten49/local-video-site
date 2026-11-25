from __future__ import annotations

import os
from pathlib import Path
from typing import List


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = Path(
    os.environ.get("LOCAL_VIDEO_DATA_DIR", PACKAGE_ROOT / "data")
).expanduser()
DATABASE_PATH = Path(
    os.environ.get("LOCAL_VIDEO_DATABASE", DATA_DIR / "library.db")
).expanduser()
THUMBNAIL_DIR = Path(
    os.environ.get("LOCAL_VIDEO_THUMBNAILS", DATA_DIR / "thumbnails")
).expanduser()
COVER_DIR = Path(
    os.environ.get("LOCAL_VIDEO_COVERS", DATA_DIR / "covers")
).expanduser()
COVER_PREVIEW_DIR = Path(
    os.environ.get(
        "LOCAL_VIDEO_COVER_PREVIEW_DIR",
        DATA_DIR / "cover-preview-cache",
    )
).expanduser()
COVER_PREVIEW_MAX_BYTES = int(
    os.environ.get(
        "LOCAL_VIDEO_COVER_PREVIEW_MAX_BYTES",
        256 * 1024 * 1024,
    )
)
SERIES_COVER_DIR = Path(
    os.environ.get(
        "LOCAL_VIDEO_SERIES_COVERS",
        DATA_DIR / "series-covers",
    )
).expanduser()
SEASON_COVER_DIR = Path(
    os.environ.get(
        "LOCAL_VIDEO_SEASON_COVERS",
        DATA_DIR / "season-covers",
    )
).expanduser()
ACTOR_AVATAR_DIR = Path(
    os.environ.get("LOCAL_VIDEO_ACTOR_AVATARS", DATA_DIR / "actor-avatars")
).expanduser()
DELETE_PASSWORD_PATH = Path(
    os.environ.get(
        "LOCAL_VIDEO_DELETE_PASSWORD_FILE",
        DATA_DIR / ".delete-password",
    )
).expanduser()
DELETE_QUARANTINE_DIR = Path(
    os.environ.get(
        "LOCAL_VIDEO_DELETE_QUARANTINE",
        DATA_DIR / "delete-quarantine",
    )
).expanduser()
SCAN_STATUS_PATH = Path(
    os.environ.get("LOCAL_VIDEO_SCAN_STATUS", DATA_DIR / "scan-status.json")
).expanduser()
LOG_DIR = Path(
    os.environ.get("LOCAL_VIDEO_LOG_DIR", DATA_DIR / "logs")
).expanduser()
LOG_PATH = Path(
    os.environ.get("LOCAL_VIDEO_LOG_PATH", LOG_DIR / "server.log")
).expanduser()
DOWNLOAD_TASK_LOG_DIR = Path(
    os.environ.get("LOCAL_VIDEO_DOWNLOAD_LOG_DIR", DATA_DIR / "download-task-logs")
).expanduser()
DOWNLOAD_CONTROL_DIR = Path(
    os.environ.get("LOCAL_VIDEO_DOWNLOAD_CONTROL_DIR", DATA_DIR / "download-task-controls")
).expanduser()
PREVIEW_DIR = Path(
    os.environ.get("LOCAL_VIDEO_PREVIEW_DIR", DATA_DIR / "previews")
).expanduser()
SUBTITLE_DIR = Path(
    os.environ.get("LOCAL_VIDEO_SUBTITLE_DIR", DATA_DIR / "subtitles")
).expanduser()
PLAYBACK_TOKEN_SECRET_PATH = Path(
    os.environ.get(
        "LOCAL_VIDEO_PLAYBACK_TOKEN_SECRET",
        DATA_DIR / ".playback-token-secret",
    )
).expanduser()
DEFAULT_ROOTS = [
    PROJECT_ROOT / "download",
    PROJECT_ROOT / "download-full",
    PROJECT_ROOT / "download-hub",
    PROJECT_ROOT / "download-other",
]


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _settings():
    from local_video_site.settings import get_runtime_settings

    return get_runtime_settings()


def configured_roots() -> List[Path]:
    raw = os.environ.get("LOCAL_VIDEO_ROOTS", "").strip()
    if not raw:
        return [path.resolve() for path in DEFAULT_ROOTS]
    return [
        Path(item).expanduser().resolve()
        for item in raw.split(os.pathsep)
        if item.strip()
    ]


def scan_on_startup() -> bool:
    return _settings().get_bool("scan.on_startup")


def actor_avatar_sync_enabled() -> bool:
    return _settings().get_bool("metadata.avatar_sync_enabled")


def actor_avatar_sync_interval_seconds() -> int:
    return _settings().get_int(
        "metadata.avatar_sync_interval_seconds"
    )


def actor_avatar_sync_batch_size() -> int:
    return _settings().get_int("metadata.avatar_sync_batch_size")


def actor_avatar_failed_retry_seconds() -> int:
    return _settings().get_int(
        "metadata.avatar_failed_retry_seconds"
    )


def scan_wait_for_resources() -> bool:
    return _settings().get_bool("scan.wait_for_resources")


def lan_mode() -> bool:
    return _env_bool("LOCAL_VIDEO_LAN_MODE", False)


def category_fetch_on_startup() -> bool:
    return _settings().get_bool("scan.category_fetch_on_startup")


def log_level() -> str:
    return _settings().get_string("logging.level").upper()


def log_retention_days() -> int:
    return _settings().get_int("logging.retention_days")


def minimum_video_bytes() -> int:
    return _settings().get_int("scan.min_video_bytes")


def playback_cache_max_bytes() -> int:
    return _settings().get_int("playback.cache_max_bytes")


def playback_cache_dir() -> Path:
    configured = os.environ.get("LOCAL_VIDEO_PLAYBACK_CACHE")
    if configured:
        return Path(configured).expanduser()
    return DATA_DIR / "playback-cache"


def playback_cache_max_age_days() -> int:
    return _settings().get_int("playback.cache_max_age_days")


def playback_cache_protect_seconds() -> int:
    return _settings().get_int(
        "playback.cache_protect_seconds"
    )


def external_playback_token_seconds() -> int:
    return _settings().get_int(
        "playback.external_token_seconds"
    )


def ffmpeg_concurrency() -> int:
    return _settings().get_int("media.ffmpeg_concurrency")


def ffmpeg_path() -> str:
    return _settings().get_string("media.ffmpeg_path")


def ffmpeg_interactive_wait_seconds() -> float:
    return _settings().get_float(
        "media.ffmpeg_interactive_wait_seconds"
    )


def job_lease_seconds() -> int:
    return _settings().get_int("jobs.lease_seconds")


def playback_remux_disk_reserve_bytes() -> int:
    return _settings().get_int(
        "playback.remux_disk_reserve_bytes"
    )


def normalize_ts_on_download() -> bool:
    return _settings().get_bool("download.normalize_ts")


def normalize_ts_on_import() -> bool:
    return _settings().get_bool("import.normalize_ts")


def video_trim_worker_enabled() -> bool:
    return _settings().get_bool("jobs.trim_worker_enabled")


def video_format_worker_enabled() -> bool:
    return _settings().get_bool("jobs.format_worker_enabled")


def video_format_poll_seconds() -> float:
    return _settings().get_float("media.format_poll_seconds")


def video_format_timeout_seconds() -> int:
    return _settings().get_int("media.format_timeout_seconds")


def video_format_disk_reserve_bytes() -> int:
    return _settings().get_int(
        "media.format_disk_reserve_bytes"
    )


def video_format_batch_max() -> int:
    return _settings().get_int("media.format_batch_max")


def video_delete_worker_enabled() -> bool:
    return _settings().get_bool("jobs.delete_worker_enabled")


def integrity_worker_enabled() -> bool:
    return _settings().get_bool("jobs.integrity_worker_enabled")


def integrity_worker_poll_seconds() -> float:
    return _settings().get_float("jobs.integrity_poll_seconds")


def video_delete_poll_seconds() -> float:
    return _settings().get_float("jobs.delete_poll_seconds")


def movi_player_mode() -> str:
    return _settings().get_string("playback.movi_player_mode").lower()


def movi_player_asset_base() -> str:
    return "/static/vendor/movi-player/0.4.1"


def movi_player_load_timeout_seconds() -> int:
    return _settings().get_int(
        "playback.movi_load_timeout_seconds"
    )


def movi_player_buffer_mb() -> int:
    return _settings().get_int("playback.movi_buffer_mb")


def download_worker_enabled() -> bool:
    return _settings().get_bool("jobs.download_worker_enabled")


def migration_worker_enabled() -> bool:
    return _settings().get_bool("jobs.migration_worker_enabled")


def external_scan_worker_enabled() -> bool:
    return _settings().get_bool(
        "jobs.external_scan_worker_enabled"
    )


def external_scan_poll_seconds() -> float:
    return _settings().get_float("scan.external_poll_seconds")


def external_scan_max_files() -> int:
    return _settings().get_int("scan.external_max_files")


def storage_volume_refresh_seconds() -> float:
    return _settings().get_float("storage.volume_refresh_seconds")


def migration_poll_seconds() -> int:
    return _settings().get_int("storage.migration_poll_seconds")


def migration_chunk_bytes() -> int:
    return _settings().get_int("storage.migration_chunk_bytes")


def migration_min_free_bytes() -> int:
    return _settings().get_int(
        "storage.migration_min_free_bytes"
    )


def migration_verification_mode() -> str:
    return _settings().get_string(
        "storage.migration_verification_mode"
    )


def migration_auto_age_days() -> int:
    return _settings().get_int(
        "storage.migration_auto_age_days"
    )


def external_volume_roots() -> List[Path]:
    raw = os.environ.get("LOCAL_VIDEO_VOLUME_ROOTS", "/Volumes").strip()
    return [
        Path(item).expanduser()
        for item in raw.split(os.pathsep)
        if item.strip()
    ]


def download_poll_seconds() -> float:
    return _settings().get_float("download.poll_seconds")


def download_task_attempts() -> int:
    return _settings().get_int("download.task_attempts")


def download_task_timeout_seconds() -> int:
    return _settings().get_int("download.task_timeout_seconds")


def download_stall_timeout_seconds() -> int:
    return _settings().get_int("download.stall_timeout_seconds")


def hls_manifest_timeout_seconds() -> int:
    return _settings().get_int("hls.manifest_timeout_seconds")


def hls_segment_timeout_seconds() -> int:
    return _settings().get_int("hls.segment_timeout_seconds")


def hls_initial_concurrency() -> int:
    return _settings().get_int("hls.initial_concurrency")


def hls_max_concurrency() -> int:
    return _settings().get_int("hls.max_concurrency")


def hls_batch_size() -> int:
    return _settings().get_int("hls.batch_size")


def hls_segment_retries() -> int:
    return _settings().get_int("hls.segment_retries")


def hls_batch_retries() -> int:
    return _settings().get_int("hls.batch_retries")


def hls_checkpoint_batches() -> int:
    return _settings().get_int("hls.checkpoint_batches")


def hls_checkpoint_min_bytes() -> int:
    return _settings().get_int("hls.checkpoint_min_bytes")


def hls_rate_limit_window_seconds() -> int:
    return _settings().get_int("hls.rate_limit_window_seconds")


def hls_rate_limit_threshold() -> int:
    return _settings().get_int("hls.rate_limit_threshold")


def hls_rate_limit_cooldown_seconds() -> int:
    return _settings().get_int("hls.rate_limit_cooldown_seconds")


def hls_max_requests_per_second() -> float:
    return _settings().get_float("hls.max_requests_per_second")


def range_chunk_size_bytes() -> int:
    return _settings().get_int("range.chunk_size_bytes")


def range_min_file_bytes() -> int:
    return _settings().get_int("range.min_file_bytes")


def range_initial_connections() -> int:
    return _settings().get_int("range.initial_connections")


def range_max_connections() -> int:
    return _settings().get_int("range.max_connections")


def range_chunk_retries() -> int:
    return _settings().get_int("range.chunk_retries")


def range_transient_gateway_retries() -> int:
    return _settings().get_int("range.transient_gateway_retries")


def range_max_total_retries() -> int:
    return _settings().get_int("range.max_total_retries")


def range_request_start_interval_seconds() -> float:
    return _settings().get_float(
        "range.request_start_interval_seconds"
    )


def range_progress_interval_seconds() -> float:
    return _settings().get_float(
        "range.progress_interval_seconds"
    )


def range_gateway_slice_bytes() -> int:
    return _settings().get_int("range.gateway_slice_bytes")


def range_rate_limit_window_seconds() -> int:
    return _settings().get_int("range.rate_limit_window_seconds")


def range_rate_limit_threshold() -> int:
    return _settings().get_int("range.rate_limit_threshold")


def range_rate_limit_cooldown_seconds() -> int:
    return _settings().get_int("range.rate_limit_cooldown_seconds")


def range_manifest_total_tolerance_bytes() -> int:
    return _settings().get_int(
        "range.manifest_total_tolerance_bytes"
    )


def ytdlp_concurrent_fragments() -> int:
    return _settings().get_int(
        "download.ytdlp_concurrent_fragments"
    )


def download_cdp_port() -> int:
    return _settings().get_int("download.cdp_port")


def download_keep_awake_enabled() -> bool:
    return _settings().get_bool("download.keep_awake_enabled")


def download_keep_awake_flags() -> str:
    raw = os.environ.get(
        "LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE_FLAGS",
        "-ims",
    ).strip()
    if (
        raw.startswith("-")
        and 1 < len(raw) <= 6
        and all(character in "dimsu" for character in raw[1:])
    ):
        return raw
    return "-ims"


def preview_generation_enabled() -> bool:
    return _settings().get_bool("media.preview_enabled")


def preview_width() -> int:
    return _settings().get_int("media.preview_width")


def preview_duration() -> float:
    return _settings().get_float("media.preview_duration_seconds")


def preview_crf() -> int:
    return _settings().get_int("media.preview_crf")


def min_password_length() -> int:
    return _settings().get_int("security.min_password_length")


def password_hash_iterations() -> int:
    return _settings().get_int(
        "security.password_hash_iterations"
    )


def session_days() -> int:
    return _settings().get_int("security.session_days")


def remember_session_days() -> int:
    return _settings().get_int("security.session_remember_days")


def login_max_failures() -> int:
    return _settings().get_int("security.login_max_failures")


def login_failure_window_minutes() -> int:
    return _settings().get_int(
        "security.login_failure_window_minutes"
    )


def delete_verification_min_length() -> int:
    return _settings().get_int(
        "security.delete_verification_min_length"
    )


def delete_hash_iterations() -> int:
    return _settings().get_int("security.delete_hash_iterations")


def auto_bootstrap_admin_enabled() -> bool:
    return _env_bool("LOCAL_VIDEO_AUTO_BOOTSTRAP_ADMIN", True)


def bootstrap_admin_username() -> str:
    return (
        os.environ.get("LOCAL_VIDEO_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .strip()
        or "admin"
    )


def bootstrap_admin_password() -> str:
    return (
        os.environ.get("LOCAL_VIDEO_BOOTSTRAP_ADMIN_PASSWORD", "")
        or "123456"
    )


def allow_default_admin_lan() -> bool:
    return _env_bool("LOCAL_VIDEO_ALLOW_DEFAULT_ADMIN_LAN", False)


def download_max_urls_per_batch() -> int:
    return _settings().get_int("download.max_urls_per_batch")


def download_metrics_stale_seconds() -> float:
    return _settings().get_float("download.metrics_stale_seconds")


def download_retry_delays_seconds() -> tuple:
    return (
        _settings().get_int("download.retry_delay_1_seconds"),
        _settings().get_int("download.retry_delay_2_seconds"),
        _settings().get_int("download.retry_delay_3_seconds"),
    )


def safe_auto_restart_bytes() -> int:
    return _settings().get_int(
        "download.safe_auto_restart_bytes"
    )


def batch_inter_item_delays() -> tuple:
    minimum = _settings().get_float(
        "download.batch_inter_item_min_seconds"
    )
    maximum = _settings().get_float(
        "download.batch_inter_item_max_seconds"
    )
    if maximum < minimum:
        maximum = minimum
    return minimum, maximum


def sniffer_minimum_video_bytes() -> int:
    return _settings().get_int("download.sniffer_min_bytes")


def sniffer_minimum_video_height() -> int:
    return _settings().get_int("download.sniffer_min_height")


def sniffer_preferred_max_video_height() -> int:
    return _settings().get_int("download.sniffer_max_height")


def avatar_max_bytes() -> int:
    return _settings().get_int("metadata.avatar_max_bytes")


def avatar_retries() -> int:
    return _settings().get_int("metadata.avatar_retries")


def max_display_title_chars() -> int:
    return _settings().get_int("media.naming_max_title_chars")


def readable_filename_chars() -> int:
    return _settings().get_int(
        "media.naming_readable_filename_chars"
    )


def max_filename_bytes() -> int:
    return _settings().get_int("media.naming_max_filename_bytes")


def probe_timeout_seconds() -> float:
    return _settings().get_float("media.probe_timeout_seconds")


def thumbnail_timeout_seconds() -> float:
    return _settings().get_float("media.thumbnail_timeout_seconds")


def thumbnail_min_luminance() -> float:
    return _settings().get_float("media.thumbnail_min_luminance")


def preview_probe_timeout_seconds() -> float:
    return _settings().get_float(
        "media.preview_probe_timeout_seconds"
    )


def preview_generation_timeout_seconds() -> float:
    return _settings().get_float(
        "media.preview_generation_timeout_seconds"
    )


def preview_poll_seconds() -> float:
    return _settings().get_float("media.preview_poll_seconds")


def preview_min_luminance() -> float:
    return _settings().get_float("media.preview_min_luminance")


def preview_min_motion() -> float:
    return _settings().get_float("media.preview_min_motion")


def trim_keyframe_window_seconds() -> float:
    return _settings().get_float(
        "media.trim_keyframe_window_seconds"
    )


def trim_probe_timeout_seconds() -> float:
    return _settings().get_float(
        "media.trim_probe_timeout_seconds"
    )


def trim_timeout_seconds() -> int:
    return _settings().get_int("media.trim_timeout_seconds")


def imports_max_files() -> int:
    return _settings().get_int("import.max_files")


def migration_batch_max() -> int:
    return _settings().get_int("storage.migration_batch_max")


def discovery_max_tabs() -> int:
    return _settings().get_int("episode.discovery_max_tabs")


def discovery_max_episodes() -> int:
    return _settings().get_int("episode.discovery_max_episodes")


def discovery_workers() -> int:
    return _settings().get_int("episode.discovery_workers")


def downloads_page_size() -> int:
    return _settings().get_int("ui.downloads_page_size")


def video_management_page_size() -> int:
    return _settings().get_int("ui.video_management_page_size")


def migrations_page_size() -> int:
    return _settings().get_int("ui.migrations_page_size")


def delete_tasks_page_size() -> int:
    return _settings().get_int("ui.delete_tasks_page_size")


def ensure_data_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    COVER_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    SERIES_COVER_DIR.mkdir(parents=True, exist_ok=True)
    SEASON_COVER_DIR.mkdir(parents=True, exist_ok=True)
    ACTOR_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    playback_cache_dir().mkdir(parents=True, exist_ok=True)
    DOWNLOAD_TASK_LOG_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
    DELETE_PASSWORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYBACK_TOKEN_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DELETE_QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
