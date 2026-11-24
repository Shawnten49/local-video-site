from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class ExternalScanGroupItem(BaseModel):
    source_path: str
    episode_number: int


class ExternalScanGroup(BaseModel):
    season_name: str
    season_number: int
    items: List[ExternalScanGroupItem] = []


class ExternalScanCreate(BaseModel):
    source_path: str
    scan_mode: str = "batch"
    access_level: int = 0
    series_name: str = ""
    season_name: str = ""
    season_number: int = 1
    groups: List[ExternalScanGroup] = []
    attach_to_series: bool = False
    target_series_id: Optional[int] = None
    attach_to_season: bool = False
    target_season_id: Optional[int] = None


class ExternalScanCommitItem(BaseModel):
    item_id: int
    access_level: int
    episode_number: Optional[int] = None


class ExternalScanCommit(BaseModel):
    items: List[ExternalScanCommitItem] = []


class MigrationBatchCreate(BaseModel):
    mode: str = "manual"
    direction: str = "to_external"
    destination_root: str
    video_ids: List[int] = []
    cutoff_days: int = 30
    password: str = ""
    verification_mode: Optional[str] = None


class ExternalImportCreate(BaseModel):
    source_path: str
    destination_root: str
    access_level: int = 0
    delete_source: bool = False
    verification_mode: str = "sha256"
    password: str = ""


class TransportPolicyCreate(BaseModel):
    site_key: str
    display_name: str = ""
    domains: List[str]
    transport_mode: str = "auto"
    lifecycle_state: str = "UNKNOWN"
    qualification_enabled: bool = True
    initial_connections: int = 2
    max_connections: int = 4
    tls_backend: str = "auto"
    note: str = ""


class TransportPolicyUpdate(BaseModel):
    display_name: Optional[str] = None
    domains: Optional[List[str]] = None
    transport_mode: Optional[str] = None
    lifecycle_state: Optional[str] = None
    initial_connections: Optional[int] = None
    max_connections: Optional[int] = None
    tls_backend: Optional[str] = None
    breaker_state: Optional[str] = None
    cooldown_minutes: Optional[int] = None
    status_note: Optional[str] = None
    note: str = ""
    expected_policy_version: Optional[int] = None
    expected_updated_at: str = ""


class TransportPolicyAction(BaseModel):
    action: str
    note: str = ""


class MediaPolicyUpdate(BaseModel):
    preset_key: Optional[str] = None
    source_order: Optional[List[str]] = None
    identity_mode: Optional[str] = None
    kind_order: Optional[List[str]] = None
    quality_target: Optional[str] = None
    quality_fallback: Optional[str] = None
    min_height: Optional[int] = None
    max_height: Optional[int] = None
    allow_below_minimum: Optional[bool] = None
    allow_page_fallback: Optional[bool] = None
    play_selector: Optional[str] = None
    format_strategy: Optional[str] = None
    prefer_h264: Optional[bool] = None
    merge_output_format: Optional[str] = None
    note: str = ""
    expected_version: Optional[int] = None


class MediaPolicyProbe(BaseModel):
    url: str


class ManualCategoryCreate(BaseModel):
    name: str


class VideoManualCategorySelection(BaseModel):
    manual_category_ids: List[int] = []
