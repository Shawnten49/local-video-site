from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class CoverSelection(BaseModel):
    timestamp: float


class DeleteVideoSelection(BaseModel):
    password: str
    return_to: str = ""


class RenameVideoSelection(BaseModel):
    title: str
    expected_path: str = ""
    return_to: str = ""


class VideoAccessLevelUpdate(BaseModel):
    access_level: int


class VideoDisplayTitleUpdate(BaseModel):
    display_title: Optional[str]
    expected_updated_at: str = ""


class VideoFileNameUpdate(BaseModel):
    file_stem: str
    expected_path: str = ""


class VideoTrimPreviewRequest(BaseModel):
    start_seconds: float


class VideoTrimCreate(BaseModel):
    start_seconds: float
    mode: str
    expected_path: str = ""
    expected_mtime_ns: Optional[int] = None


class VideoFormatJobCreate(BaseModel):
    operation: str = "container_remux"
    confirmed: bool = False
    confirm_lossy: bool = False
    confirmation_version: int = 1
    subtitle_mode: str = "auto"
    subtitle_stream_indexes: List[int] = []
    default_subtitle_stream_index: Optional[int] = None


class VideoStandardizeCreate(BaseModel):
    confirmed: bool = False
    confirm_lossy: bool = False
    expected_path: str = ""
    expected_mtime_ns: Optional[str] = None
    subtitle_mode: str = "auto"
    subtitle_stream_indexes: List[int] = []
    default_subtitle_stream_index: Optional[int] = None
