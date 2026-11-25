"""Compatibility facade for repository and schema access.

Application code should import domain repositories directly. This module keeps
the historical ``database.<name>`` interface stable while existing callers are
migrated incrementally.
"""

from .db.connection import connect
from .db.migrations import (
    applied_migrations,
    legacy_identifier_checksum,
    run_migrations,
    source_checksum,
)
from .db.schema import (
    ACTOR_METADATA_VERSION,
    CATEGORY_PARSER_VERSION,
    PLAYBACK_NORMALIZATION_VERSION,
    VIDEO_ASSET_COLUMNS,
    VIDEO_MEDIA_COLUMNS,
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
    baseline_schema_functions,
    ensure_media_policy_schema,
    ensure_subtitle_schema,
    init_db,
)
from .db.transaction import now_iso as _now
from .repositories.accounts import *  # noqa: F401,F403
from .repositories.actors import *  # noqa: F401,F403
from .repositories.actors import (  # noqa: F401
    _actor_with_avatar_state,
)
from .repositories.categories import *  # noqa: F401,F403
from .repositories.categories import (  # noqa: F401
    _normalize_manual_category_name,
)
from .repositories.download_batches import *  # noqa: F401,F403
from .repositories.downloads import *  # noqa: F401,F403
from .repositories.downloads import (  # noqa: F401
    _DOWNLOAD_TASK_UPDATES,
    _remove_task_partial_files,
    _settle_download_run,
)
from .repositories.episodes import (  # noqa: F401
    _apply_planned_episode_policy,
    _ensure_video_satellites,
)
from .repositories.episodes import *  # noqa: F401,F403
from .repositories.favorites import *  # noqa: F401,F403
from .repositories.integrity import *  # noqa: F401,F403
from .repositories.integrity import (  # noqa: F401
    _repair_orphan_video_dependencies,
)
from .repositories.jobs import *  # noqa: F401,F403
from .repositories.migrations import *  # noqa: F401,F403
from .repositories.series import *  # noqa: F401,F403
from .repositories.storage import *  # noqa: F401,F403
from .repositories.subtitles import *  # noqa: F401,F403
from .repositories.storage import (  # noqa: F401
    _repair_storage_paths_relative_to_mount,
)
from .repositories.video_assets import *  # noqa: F401,F403
from .repositories.video_catalog import *  # noqa: F401,F403
from .repositories.video_catalog import (  # noqa: F401
    _VIDEO_ORDER_BY,
    _admin_video_filters,
    _video_with_preview_state,
    _video_filter,
)
from .repositories.videos import *  # noqa: F401,F403
