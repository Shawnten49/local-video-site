from __future__ import annotations

from ..schema import (
    _apply_legacy_schema,
    _ensure_episode_discovery_schema,
    _ensure_episode_title_columns,
    _ensure_external_scan_columns,
    _ensure_media_policy_columns,
    _ensure_playback_cache_columns,
    _ensure_series_delete_columns,
    _ensure_video_format_columns,
    _ensure_video_satellite_schema,
    _ensure_video_trim_columns,
)
from .runner import BASELINE_NAME, BASELINE_VERSION


def baseline_functions() -> tuple:
    return (
        _apply_legacy_schema,
        _ensure_media_policy_columns,
        _ensure_episode_title_columns,
        _ensure_episode_discovery_schema,
        _ensure_video_trim_columns,
        _ensure_external_scan_columns,
        _ensure_series_delete_columns,
        _ensure_video_format_columns,
        _ensure_playback_cache_columns,
        _ensure_video_satellite_schema,
    )


baseline_apply = _apply_legacy_schema
name = BASELINE_NAME
version = BASELINE_VERSION
prior_checksums = (
    "18429ad901bfedf532f44aade9a2f66a1b9e5b1862c89fcc4ed492bfb7bd7fb0",
    "615a72b36c1d2bafca7d6ae550f9b064ed79a470a2a787589c6452a2445d8a7f",
    "a47e4cf28147f38f9b816862a633b7128bcde751153f8178958b4724b8fb4eef",
    "fc449caff515642a799fd0c748e05974fd1553d2dc0ee92bbb1f2b390de20de2",
)
