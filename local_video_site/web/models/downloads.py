from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class DownloadTaskItem(BaseModel):
    metadata_url: str
    download_url: str = ""


class DownloadTaskCreate(BaseModel):
    output_dir: str
    quality: str = "site"
    access_level: int = 0
    index_into_library: bool = True
    mode: Optional[str] = None
    urls: Optional[List[str]] = None
    items: Optional[List[DownloadTaskItem]] = None


class DownloadTaskDeleteSelection(BaseModel):
    password: str = ""


class EpisodeBatchDiscover(BaseModel):
    series_name: str
    season_name: str
    season_number: int = 1
    first_episode_url: str
    output_root: str
    quality: str = "site"
    access_level: int = 0
    continuous_parse: bool = False
    aggregate_single_season: bool = False
    preserve_original_name: bool = False
    allow_source_series_mismatch: bool = False


class EpisodeBatchConfirm(BaseModel):
    revision: int
    selected_item_ids: List[int] = []
