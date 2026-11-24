"""Generic media candidate normalization and selection."""

from .catalog import MediaCatalog
from .quality import (
    format_selector_for_candidate,
    preferred_quality_height,
    select_media_candidate,
    yt_dlp_format_selector,
)
from .policy import (
    MediaPolicy,
    MediaSelectionPlan,
    build_selection_plan,
    resolve_quality_target,
)

__all__ = [
    "MediaCatalog",
    "MediaPolicy",
    "MediaSelectionPlan",
    "build_selection_plan",
    "format_selector_for_candidate",
    "preferred_quality_height",
    "resolve_quality_target",
    "select_media_candidate",
    "yt_dlp_format_selector",
]
