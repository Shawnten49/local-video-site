from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from local_video_site.downloader.naming import safe_filename_component

from local_video_site import database
from local_video_site.identity.permissions.content import validate_access_level
from local_video_site.downloads.tasks import create_tasks, normalize_url, normalise_requested_quality, validate_output_dir
from local_video_site.episodes.catalog import EpisodeCandidate, EpisodeCatalog, EpisodeCatalogError, EpisodeTab, discover_episode_catalog, discover_episode_tabs
from local_video_site.episodes.naming import season_number_from_label


class EpisodeBatchError(ValueError):
    def __init__(self, message: str, code: str = "batch_error"):
        super().__init__(message)
        self.code = code


def _safe_segment(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", normalized)
    normalized = normalized.strip(" .")
    if not normalized or normalized in {".", ".."}:
        normalized = fallback
    return normalized


_SEASON_EPISODE_LABEL_RE = re.compile(
    r"^第\s*([0-9一二三四五六七八九十百两]+)\s*集$"
)
_SEASON_EPISODE_EN_RE = re.compile(
    r"^(?:episode|ep|e)[\s._-]*(\d{1,4})$",
    re.IGNORECASE,
)
_MEDIA_SUFFIX_RE = re.compile(
    r"\.(?:mp4|m4v|mkv|webm|mov|avi|flv|ts)$",
    re.IGNORECASE,
)
_GENERIC_ORIGINAL_NAME_RE = re.compile(
    r"^(?:正片|正剧|tv|正片\s*\d*|episode(?:\s*\d+)?|ep(?:\s*\d+)?|\d+)$",
    re.IGNORECASE,
)
_CONTEXTUAL_ORIGINAL_NAME_RE = re.compile(
    r"^(?:原版|中文版|英文版|国语版?|粤语版?|日语版?|英语版?|"
    r"配音版?|字幕版?|普通话版?|台配版?)$",
    re.IGNORECASE,
)


def normalize_season_name(
    value: str,
    season_number: int,
) -> tuple:
    """Repair a season field accidentally entered as an episode label."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = _SEASON_EPISODE_LABEL_RE.match(normalized)
    if match:
        label = "第{}季".format(match.group(1))
        return label, season_number_from_label(label, season_number)
    match = _SEASON_EPISODE_EN_RE.match(normalized)
    if match:
        number = max(1, int(match.group(1)))
        return "第{}季".format(number), number
    return normalized, max(1, int(season_number or 1))


def episode_file_stem(episode_number: int) -> str:
    number = max(1, int(episode_number))
    width = 3 if number <= 999 else len(str(number))
    return str(number).zfill(width)


def _truncate_utf8(value: str, max_bytes: int = 180) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def _meaningful_original_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = _MEDIA_SUFFIX_RE.sub("", normalized).strip(" .")
    if not normalized:
        return ""
    if _GENERIC_ORIGINAL_NAME_RE.fullmatch(normalized):
        return ""
    if _SEASON_EPISODE_LABEL_RE.fullmatch(normalized):
        return ""
    if _SEASON_EPISODE_EN_RE.fullmatch(normalized):
        return ""
    return normalized


def _contextual_original_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = _MEDIA_SUFFIX_RE.sub("", normalized).strip(" .")
    if _CONTEXTUAL_ORIGINAL_NAME_RE.fullmatch(normalized):
        return normalized
    return ""


def _original_file_stem(
    episode: EpisodeCandidate,
    source_tab_title: str,
    page_item_count: int,
) -> str:
    page_item_count = (
        episode.source_tab_item_count
        if episode.source_tab_item_count is not None
        else page_item_count
    )
    season_title = _meaningful_original_title(
        episode.source_season_name or source_tab_title
    )
    episode_title = _meaningful_original_title(episode.title)
    contextual_title = _contextual_original_title(episode.title)
    source_number = (
        episode.source_episode_number
        if episode.source_episode_number is not None
        else episode.episode_number
    )
    if page_item_count == 1 and season_title:
        candidate = season_title
    elif page_item_count > 1 and contextual_title and season_title:
        candidate = "{} - {}".format(season_title, contextual_title)
    elif episode_title:
        candidate = episode_title
    elif season_title:
        candidate = "{} - 第{}集".format(
            season_title,
            episode_file_stem(source_number),
        )
    else:
        candidate = "第{}集".format(
            episode_file_stem(source_number)
        )
    return safe_filename_component(
        _MEDIA_SUFFIX_RE.sub("", candidate).strip(" ."),
        fallback=episode_file_stem(episode.episode_number),
    )


def _unique_original_stem(base: str, used: set) -> str:
    candidate = base
    sequence = 1
    while (
        unicodedata.normalize("NFKC", candidate).casefold()
        in used
    ):
        sequence += 1
        suffix = " ({})".format(sequence)
        candidate = _truncate_utf8(
            base,
            max(1, 180 - len(suffix.encode("utf-8"))),
        ).rstrip() + suffix
    used.add(unicodedata.normalize("NFKC", candidate).casefold())
    return candidate


def _batch_id() -> str:
    return "episode-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )


def _discovery_id() -> str:
    return "discovery-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )


def _existing_task(normalized_url: str) -> Optional[Dict]:
    return database.find_download_task_by_metadata_url(normalized_url)


def _normalized_source_name(value: str) -> str:
    return re.sub(
        r"[\s·・:：\-—_]+",
        "",
        unicodedata.normalize("NFKC", str(value or "")).casefold(),
    )


def _validate_source_series_name(
    detected_series_name: str,
    target_series_name: str,
    allow_mismatch: bool,
) -> None:
    detected = _normalized_source_name(detected_series_name)
    target = _normalized_source_name(target_series_name)
    if (
        allow_mismatch
        or not detected
        or not target
        or detected in target
        or target in detected
    ):
        return
    raise EpisodeBatchError(
        "来源视频集“{}”与目标视频集“{}”不一致。"
        "如确认要自定义目标视频集，请勾选“允许来源与目标视频集不同”。".format(
            detected_series_name,
            target_series_name,
        ),
        "source_series_mismatch",
    )


def _preview_items(
    catalog: EpisodeCatalog,
    series_name: str,
    season_name: str,
    root: Path,
    source_tab_key: str = "",
    source_tab_title: str = "",
    file_name_mode: str = "sequence",
) -> List[Dict]:
    file_name_mode = (
        "original"
        if str(file_name_mode or "").strip().lower() == "original"
        else "sequence"
    )
    relative_directory = str(Path(series_name) / season_name)
    season_directory = root / series_name / season_name
    try:
        season_directory.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise EpisodeBatchError("剧集目录超出了保存根目录") from exc

    items = []
    seen_urls = set()
    used_original_stems = set()
    page_item_count = len(catalog.episodes)
    for episode in catalog.episodes:
        sequence_stem = episode_file_stem(episode.episode_number)
        try:
            normalized_url = normalize_url(episode.source_url)
        except ValueError as exc:
            file_stem = sequence_stem
            if file_name_mode == "original":
                file_stem = _unique_original_stem(
                    _original_file_stem(
                        episode,
                        source_tab_title,
                        page_item_count,
                    ),
                    used_original_stems,
                )
            items.append({
                "episode_number": episode.episode_number,
                "source_url": episode.source_url,
                "normalized_source_url": episode.source_url,
                "display_title": "{} {} {}".format(
                    series_name,
                    season_name,
                    episode_file_stem(episode.episode_number),
                ),
                "episode_title": episode.title,
                "file_name": "{}.mp4".format(file_stem),
                "relative_path": "",
                "absolute_path": "",
                "selected": False,
                "validation_status": "invalid",
                "validation_message": str(exc),
                "file_name_mode": file_name_mode,
                "source_tab_key": (
                    episode.source_tab_key or source_tab_key
                ),
                "source_tab_title": (
                    episode.source_tab_title or source_tab_title
                ),
                "source_tab_item_title": (
                    episode.source_tab_item_title
                ),
                "source_season_name": (
                    episode.source_season_name or source_tab_title
                ),
                "source_episode_number": (
                    episode.source_episode_number
                    if episode.source_episode_number is not None
                    else episode.episode_number
                ),
            })
            continue
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        file_stem = sequence_stem
        if file_name_mode == "original":
            file_stem = _unique_original_stem(
                _original_file_stem(
                    episode,
                    source_tab_title,
                    page_item_count,
                ),
                used_original_stems,
            )
        file_name = "{}.mp4".format(file_stem)
        relative_path = str(Path(relative_directory) / file_name)
        absolute_path = str((root / relative_path).resolve())
        validation_status = "ready"
        validation_message = ""
        if Path(absolute_path).exists():
            validation_status = "exists"
            validation_message = "目标文件已经存在"
        else:
            existing_episode = database.get_video_episode_by_source_url(
                normalized_url
            )
            if existing_episode is not None:
                validation_status = "existing_episode"
                validation_message = (
                    "视频已在 {} / {} 中"
                ).format(
                    existing_episode.get("series_name"),
                    existing_episode.get("season_name"),
                )
            else:
                existing_task = _existing_task(normalized_url)
                if existing_task is not None:
                    expected_dir = (
                        root / relative_directory
                    ).resolve()
                    existing_dir = Path(
                        str(existing_task.get("output_dir") or "")
                    ).expanduser().resolve()
                    if existing_dir != expected_dir:
                        validation_status = "existing_task_elsewhere"
                        validation_message = (
                            "相同视频已有任务 #{}，但目标目录不同：{}"
                        ).format(
                            existing_task["id"],
                            existing_dir,
                        )
                    else:
                        validation_status = "existing_task"
                        validation_message = "已存在下载任务 #{}".format(
                            existing_task["id"]
                        )
        items.append({
            "episode_number": episode.episode_number,
            "source_url": episode.source_url,
            "normalized_source_url": normalized_url,
            "display_title": "{} {} {}".format(
                series_name,
                season_name,
                sequence_stem,
            ),
            "episode_title": episode.title,
            "file_name": file_name,
            "relative_path": relative_path,
            "absolute_path": absolute_path,
            "selected": validation_status == "ready",
            "validation_status": validation_status,
            "validation_message": validation_message,
            "file_name_mode": file_name_mode,
            "source_tab_key": (
                episode.source_tab_key or source_tab_key
            ),
            "source_tab_title": (
                episode.source_tab_title or source_tab_title
            ),
            "source_tab_item_title": (
                episode.source_tab_item_title
            ),
            "source_season_name": (
                episode.source_season_name or source_tab_title
            ),
            "source_episode_number": (
                episode.source_episode_number
                if episode.source_episode_number is not None
                else episode.episode_number
            ),
        })
    return items


def _create_catalog_batch(
    catalog: EpisodeCatalog,
    series_name: str,
    season_name: str,
    season_number: int,
    root: Path,
    quality: str,
    access_level: int,
    created_by_user_id: Optional[int],
    created_by_username: Optional[str],
    source_tab_key: str = "",
    source_tab_title: str = "",
    file_name_mode: str = "sequence",
) -> Dict:
    items = _preview_items(
        catalog,
        series_name,
        season_name,
        root,
        source_tab_key=source_tab_key,
        source_tab_title=source_tab_title,
        file_name_mode=file_name_mode,
    )
    if not items:
        raise EpisodeBatchError(
            "没有生成可确认的剧集预览",
            "preview_empty",
        )
    batch_id = _batch_id()
    database.create_episode_download_batch(
        batch_id=batch_id,
        series_name=series_name,
        season_name=season_name,
        season_number=season_number,
        first_episode_url=catalog.source_url,
        provider=catalog.provider,
        output_root=str(root),
        quality=quality,
        file_name_mode=file_name_mode,
        items=items,
        access_level=access_level,
        created_by_user_id=created_by_user_id,
        created_by_username=created_by_username,
    )
    return database.get_episode_download_batch(batch_id)


def _aggregate_tabs(
    tabs: List[EpisodeTab],
) -> tuple:
    episodes = []
    seen = set()
    warning = []
    for tab in tabs:
        if tab.catalog is None:
            continue
        for episode in tab.catalog.episodes:
            try:
                key = normalize_url(episode.source_url)
            except ValueError:
                key = episode.source_url
            if key in seen:
                warning.append(
                    "重复视频已去重：{}".format(episode.source_url)
                )
                continue
            seen.add(key)
            episodes.append(EpisodeCandidate(
                episode_number=len(episodes) + 1,
                title=episode.title,
                source_url=episode.source_url,
                source_tab_key=episode.source_tab_key or tab.tab_key,
                source_tab_title=(
                    episode.source_tab_title or tab.tab_title
                ),
                source_tab_item_title=episode.source_tab_item_title,
                source_season_name=(
                    episode.source_season_name or tab.season_name
                ),
                source_episode_number=(
                    episode.source_episode_number
                    if episode.source_episode_number is not None
                    else episode.episode_number
                ),
                source_tab_item_count=len(tab.catalog.episodes),
            ))
    catalog = EpisodeCatalog(
        provider="bilibili",
        detected_series_name=(
            tabs[0].catalog.detected_series_name
            if tabs and tabs[0].catalog
            else ""
        ),
        detected_season_name="",
        source_url=tabs[0].source_url,
        episodes=episodes,
        warning=" ".join(warning),
        selection_mode="bilibili_continuous_aggregate",
    )
    return catalog, warning


def discover_batch(
    series_name: str,
    season_name: str,
    season_number: int,
    first_episode_url: str,
    output_root: str,
    quality: str = "site",
    access_level: int = 0,
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
    continuous_parse: bool = False,
    aggregate_single_season: bool = False,
    preserve_original_name: bool = False,
    allow_source_series_mismatch: bool = False,
) -> Dict:
    if preserve_original_name and not aggregate_single_season:
        raise EpisodeBatchError(
            "保留原始名称必须先启用汇总成单季",
            "invalid_file_name_mode",
        )
    if aggregate_single_season and not continuous_parse:
        raise EpisodeBatchError(
            "汇总成单季必须先启用连续解析",
            "invalid_discovery_mode",
        )
    try:
        season_number = max(1, int(season_number))
    except (TypeError, ValueError) as exc:
        raise EpisodeBatchError("季序号必须是正整数") from exc
    series_name = _safe_segment(series_name, "未命名系列")
    season_name, season_number = normalize_season_name(
        season_name,
        season_number,
    )
    season_name = _safe_segment(season_name, "第{}季".format(season_number))
    season_number = season_number_from_label(season_name, season_number)
    try:
        root = validate_output_dir(output_root)
    except ValueError as exc:
        raise EpisodeBatchError(str(exc), "output_root_invalid") from exc
    try:
        quality = normalise_requested_quality(quality)
        access_level = validate_access_level(access_level)
    except ValueError as exc:
        raise EpisodeBatchError(str(exc), "batch_invalid") from exc
    if continuous_parse:
        return _discover_continuous(
            series_name=series_name,
            season_name=season_name,
            season_number=season_number,
            first_episode_url=first_episode_url,
            root=root,
            quality=quality,
            access_level=access_level,
            created_by_user_id=created_by_user_id,
            created_by_username=created_by_username,
            aggregate_single_season=aggregate_single_season,
            preserve_original_name=preserve_original_name,
            allow_source_series_mismatch=(
                allow_source_series_mismatch
            ),
        )
    try:
        catalog = discover_episode_catalog(first_episode_url)
    except EpisodeCatalogError as exc:
        raise EpisodeBatchError(str(exc), exc.code) from exc
    _validate_source_series_name(
        catalog.detected_series_name,
        series_name,
        allow_source_series_mismatch,
    )
    return _create_catalog_batch(
        catalog=catalog,
        series_name=series_name,
        season_name=season_name,
        season_number=season_number,
        root=root,
        quality=quality,
        access_level=access_level,
        created_by_user_id=created_by_user_id,
        created_by_username=created_by_username,
        file_name_mode="sequence",
    )


def _discover_continuous(
    series_name: str,
    season_name: str,
    season_number: int,
    first_episode_url: str,
    root: Path,
    quality: str,
    access_level: int,
    created_by_user_id: Optional[int],
    created_by_username: Optional[str],
    aggregate_single_season: bool,
    preserve_original_name: bool,
    allow_source_series_mismatch: bool,
) -> Dict:
    try:
        tabs = discover_episode_tabs(first_episode_url)
    except EpisodeCatalogError as exc:
        raise EpisodeBatchError(str(exc), exc.code) from exc

    warnings = [
        tab.warning for tab in tabs if str(tab.warning or "").strip()
    ]
    source_item_count = sum(
        len(tab.catalog.episodes)
        for tab in tabs
        if tab.catalog is not None
    )
    tab_records = []
    child_batches = []
    provider = "bilibili"
    discovery_source_url = tabs[0].source_url
    detected_series_name = (
        tabs[0].catalog.detected_series_name
        if tabs and tabs[0].catalog is not None
        else ""
    )
    _validate_source_series_name(
        detected_series_name,
        series_name,
        allow_source_series_mismatch,
    )

    if aggregate_single_season:
        aggregate_catalog, duplicate_warnings = _aggregate_tabs(tabs)
        warnings.extend(duplicate_warnings)
        if not aggregate_catalog.episodes:
            raise EpisodeBatchError(
                "所有选集 Tab 都没有可汇总的剧集",
                "preview_empty",
            )
        batch = _create_catalog_batch(
            catalog=aggregate_catalog,
            series_name=series_name,
            season_name=season_name,
            season_number=season_number,
            root=root,
            quality=quality,
            access_level=access_level,
            created_by_user_id=created_by_user_id,
            created_by_username=created_by_username,
            source_tab_key="aggregate",
            source_tab_title=season_name,
            file_name_mode=(
                "original" if preserve_original_name else "sequence"
            ),
        )
        child_batches.append(batch)
        provider = aggregate_catalog.provider
        for tab in tabs:
            tab_records.append({
                "tab_sequence": tab.tab_sequence,
                "tab_key": tab.tab_key,
                "tab_title": tab.tab_title,
                "season_name": tab.season_name,
                "season_number": tab.season_number,
                "source_url": tab.source_url,
                "source_item_count": (
                    len(tab.catalog.episodes)
                    if tab.catalog is not None
                    else 0
                ),
                "batch_id": (
                    batch["batch_id"]
                    if tab.catalog is not None
                    else None
                ),
                "status": tab.status,
                "warning": tab.warning,
            })
        mode = "continuous_aggregate"
        target_season_name = season_name
        target_season_number = season_number
    else:
        mode = "continuous_grouped"
        target_season_name = None
        target_season_number = None
        for tab in tabs:
            raw_name = (
                tab.season_name
                or tab.tab_title
                or "第{}季".format(tab.tab_sequence)
            )
            normalized_name, normalized_number = normalize_season_name(
                raw_name,
                tab.tab_sequence,
            )
            normalized_name = _safe_segment(
                normalized_name,
                "第{}季".format(tab.tab_sequence),
            )
            batch = None
            warning = tab.warning
            status = tab.status
            if tab.catalog is not None:
                try:
                    batch = _create_catalog_batch(
                        catalog=tab.catalog,
                        series_name=series_name,
                        season_name=normalized_name,
                        season_number=normalized_number,
                        root=root,
                        quality=quality,
                        access_level=access_level,
                        created_by_user_id=created_by_user_id,
                        created_by_username=created_by_username,
                        source_tab_key=tab.tab_key,
                        source_tab_title=tab.tab_title,
                        file_name_mode="sequence",
                    )
                    child_batches.append(batch)
                    provider = tab.catalog.provider
                except EpisodeBatchError as exc:
                    status = "failed"
                    warning = "{} {}".format(
                        warning,
                        str(exc),
                    ).strip()
            tab_records.append({
                "tab_sequence": tab.tab_sequence,
                "tab_key": tab.tab_key,
                "tab_title": tab.tab_title,
                "season_name": normalized_name,
                "season_number": normalized_number,
                "source_url": tab.source_url,
                "source_item_count": (
                    len(tab.catalog.episodes)
                    if tab.catalog is not None
                    else 0
                ),
                "batch_id": batch["batch_id"] if batch else None,
                "status": status,
                "warning": warning,
            })
            if warning:
                warnings.append(warning)

    if not child_batches:
        raise EpisodeBatchError(
            "所有选集 Tab 都没有生成可确认的预览",
            "preview_empty",
        )
    complete = all(
        tab.is_complete
        for tab in tabs
    )
    item_count = sum(
        int(batch.get("item_count") or 0)
        for batch in child_batches
    )
    discovery_id = _discovery_id()
    database.create_episode_download_discovery(
        discovery_id=discovery_id,
        provider=provider,
        source_url=discovery_source_url,
        series_name=series_name,
        output_root=str(root),
        quality=quality,
        access_level=access_level,
        mode=mode,
        aggregate_single_season=aggregate_single_season,
        preserve_original_name=preserve_original_name,
        file_name_mode=(
            "original" if preserve_original_name else "sequence"
        ),
        target_season_name=target_season_name,
        target_season_number=target_season_number,
        tabs=tab_records,
        source_item_count=source_item_count,
        item_count=item_count,
        complete=complete,
        warning=" ".join(dict.fromkeys(
            warning for warning in warnings if warning
        )),
        created_by_user_id=created_by_user_id,
        created_by_username=created_by_username,
    )
    result = database.get_episode_download_discovery(discovery_id)
    if result is None:
        raise EpisodeBatchError("连续解析预览写入失败")
    result["discovery_mode"] = mode
    return result


def confirm_batch(
    batch_id: str,
    selected_item_ids: List[int],
    revision: int,
    task_batch_id: Optional[str] = None,
) -> Dict:
    batch = database.get_episode_download_batch(batch_id)
    if batch is None:
        raise EpisodeBatchError("批量任务不存在", "batch_not_found")
    if batch["status"] == "confirmed":
        return _confirmed_report(batch)
    if batch["status"] != "preview":
        raise EpisodeBatchError(
            "当前批次状态不能确认",
            "invalid_batch_status",
        )
    if int(revision) != int(batch["revision"]):
        raise EpisodeBatchError(
            "预览内容已经变化，请刷新后重新确认",
            "revision_conflict",
        )
    selected_ids = {int(value) for value in selected_item_ids}
    selected = [
        item for item in batch["items"]
        if int(item["id"]) in selected_ids
    ]
    if not selected:
        raise EpisodeBatchError("请至少选择一集")
    invalid = [
        item for item in selected
        if item["validation_status"] not in {"ready", "existing_task"}
    ]
    if invalid:
        raise EpisodeBatchError(
            "所选剧集中存在无法下载的条目，请重新选择",
            "invalid_selection",
        )
    database.set_episode_download_selection(batch_id, selected_ids)

    try:
        root = validate_output_dir(batch["output_root"])
    except ValueError as exc:
        raise EpisodeBatchError(str(exc), "output_root_invalid") from exc
    relative_directory = str(
        Path(batch["series_name"]) / batch["season_name"]
    )
    try:
        season_directory = validate_output_dir(
            str(root / batch["series_name"] / batch["season_name"])
        )
    except ValueError as exc:
        raise EpisodeBatchError(str(exc), "output_root_invalid") from exc
    season_data = database.upsert_video_season(
        series_name=batch["series_name"],
        season_name=batch["season_name"],
        season_number=int(batch["season_number"]),
        provider=batch["provider"],
        source_url=batch["first_episode_url"],
        relative_directory=relative_directory,
    )
    season_id = int(season_data["season"]["id"])

    prepared = []
    for item in selected:
        episode_id = database.create_video_episode(
            season_id=season_id,
            episode_number=int(item["episode_number"]),
            display_title=item["display_title"],
            source_url=item["source_url"],
            normalized_source_url=item["normalized_source_url"],
            relative_path=item["relative_path"],
            absolute_path=item["absolute_path"],
            download_task_id=item.get("download_task_id"),
            episode_title=item.get("episode_title"),
        )
        existing_video = database.get_video_by_path(
            Path(item["absolute_path"])
        )
        if existing_video is not None:
            database.attach_episode_video(
                episode_id,
                int(existing_video["id"]),
                Path(item["absolute_path"]),
            )
        prepared.append({
            "metadata_url": item["source_url"],
            "download_url": item["source_url"],
            "title": item["display_title"],
            "display_title": item["display_title"],
            "output_stem": Path(item["file_name"]).stem,
            "episode_id": episode_id,
            "episode_batch_id": batch_id,
            "priority": 100 if int(item["episode_number"]) == 1 else 0,
            "_item_id": int(item["id"]),
            "_episode_id": episode_id,
        })

    try:
        report = create_tasks(
            output_dir=str(season_directory),
            items=prepared,
            preferred_quality=batch["quality"],
            batch_id=task_batch_id or batch_id,
            access_level=int(batch.get("access_level") or 0),
            created_by_user_id=batch.get("created_by_user_id"),
            created_by_username=batch.get("created_by_username"),
        )
    except ValueError as exc:
        database.update_episode_download_batch(
            batch_id,
            status="failed",
            error_code="task_create_failed",
            error_detail=str(exc),
        )
        raise EpisodeBatchError(str(exc), "task_create_failed") from exc
    if report["invalid"]:
        database.update_episode_download_batch(
            batch_id,
            status="failed",
            error_code="task_invalid",
            error_detail=str(report["invalid"]),
        )
        raise EpisodeBatchError("部分剧集 URL 无效，未创建完整批次")

    for item in prepared:
        task = database.find_download_task_by_metadata_url(
            normalize_url(item["metadata_url"])
        )
        if task is None:
            continue
        database.set_episode_download_task(
            batch_id=batch_id,
            normalized_source_url=normalize_url(item["metadata_url"]),
            download_task_id=int(task["id"]),
            episode_id=int(item["_episode_id"]),
        )
        if task.get("video_id") and task.get("file_path"):
            database.attach_episode_video(
                int(item["_episode_id"]),
                int(task["video_id"]),
                Path(task["file_path"]),
            )
    database.update_episode_download_batch(
        batch_id,
        status="confirmed",
        confirmed_at=datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
    )
    confirmed = database.get_episode_download_batch(batch_id)
    return _confirmed_report(confirmed or batch, report=report)


def _discovery_report(
    discovery: Dict,
    report: Optional[Dict] = None,
    errors: Optional[List[Dict]] = None,
) -> Dict:
    if errors is None:
        try:
            stored_errors = json.loads(
                discovery.get("error_detail") or "[]"
            )
        except (TypeError, ValueError):
            stored_errors = []
        errors = stored_errors if isinstance(stored_errors, list) else []
    batch_ids = sorted({
        str(group["batch_id"])
        for group in discovery.get("groups", [])
        if group.get("batch_id")
    })
    task_ids = sorted({
        int(item["download_task_id"])
        for item in discovery.get("items", [])
        if item.get("download_task_id") is not None
    })
    return {
        "discovery_id": discovery["discovery_id"],
        "status": discovery["status"],
        "selected": len(task_ids),
        "task_ids": task_ids,
        "batch_ids": batch_ids,
        "created": int((report or {}).get("created") or 0),
        "duplicates": int((report or {}).get("duplicates") or 0),
        "errors": errors,
    }


def confirm_episode_discovery(
    discovery_id: str,
    selected_item_ids: List[int],
    revision: int,
) -> Dict:
    discovery = database.get_episode_download_discovery(discovery_id)
    if discovery is None:
        raise EpisodeBatchError(
            "连续批量任务不存在",
            "batch_not_found",
        )
    if discovery["status"] in {"confirmed", "partial"}:
        return _discovery_report(discovery)
    if discovery["status"] != "preview":
        raise EpisodeBatchError(
            "当前连续批量任务状态不能确认",
            "invalid_batch_status",
        )
    if int(revision) != int(discovery["revision"]):
        raise EpisodeBatchError(
            "预览内容已经变化，请刷新后重新确认",
            "revision_conflict",
        )

    selected_ids = {int(value) for value in selected_item_ids}
    items_by_id = {
        int(item["id"]): item
        for item in discovery.get("items", [])
    }
    if not selected_ids:
        raise EpisodeBatchError("请至少选择一集")
    if any(item_id not in items_by_id for item_id in selected_ids):
        raise EpisodeBatchError(
            "所选剧集中存在不属于当前连续任务的条目",
            "invalid_selection",
        )
    invalid = [
        items_by_id[item_id]
        for item_id in selected_ids
        if items_by_id[item_id]["validation_status"]
        not in {"ready", "existing_task"}
    ]
    if invalid:
        raise EpisodeBatchError(
            "所选剧集中存在无法下载的条目，请重新选择",
            "invalid_selection",
        )

    errors = []
    aggregate_report = {
        "created": 0,
        "duplicates": 0,
    }
    successful_batches = 0
    for group in discovery.get("groups", []):
        batch_id = str(group.get("batch_id") or "").strip()
        if not batch_id:
            continue
        group_selected_ids = [
            int(item["id"])
            for item in group.get("items", [])
            if int(item["id"]) in selected_ids
        ]
        if not group_selected_ids:
            continue
        batch = database.get_episode_download_batch(batch_id)
        if batch is None:
            errors.append({
                "tab_title": group.get("tab_title"),
                "batch_id": batch_id,
                "error": "批次不存在",
            })
            continue
        try:
            report = confirm_batch(
                batch_id=batch_id,
                selected_item_ids=group_selected_ids,
                revision=int(batch["revision"]),
                task_batch_id=discovery_id,
            )
        except EpisodeBatchError as exc:
            errors.append({
                "tab_title": group.get("tab_title"),
                "batch_id": batch_id,
                "error": str(exc),
                "code": exc.code,
            })
            continue
        successful_batches += 1
        aggregate_report["created"] += int(report.get("created") or 0)
        aggregate_report["duplicates"] += int(
            report.get("duplicates") or 0
        )

    confirmed_at = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    if errors and successful_batches:
        status = "partial"
        error_code = "partial_failure"
    elif errors:
        status = "failed"
        error_code = "confirm_failed"
    else:
        status = "confirmed"
        error_code = None
    database.update_episode_download_discovery(
        discovery_id,
        status=status,
        error_code=error_code,
        error_detail=(
            json.dumps(errors, ensure_ascii=False)
            if errors
            else None
        ),
        confirmed_at=confirmed_at,
    )
    updated = database.get_episode_download_discovery(discovery_id)
    if updated is None:
        raise EpisodeBatchError("连续批量任务读取失败")
    if status == "failed":
        raise EpisodeBatchError(
            "所有选集 Tab 都未能生成下载任务",
            "confirm_failed",
        )
    return _discovery_report(updated, aggregate_report, errors)


def _confirmed_report(batch: Dict, report: Optional[Dict] = None) -> Dict:
    task_ids = sorted({
        int(item["download_task_id"])
        for item in batch.get("items", [])
        if item.get("download_task_id") is not None
    })
    return {
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "selected": len(task_ids),
        "task_ids": task_ids,
        "created": int((report or {}).get("created") or 0),
        "duplicates": int((report or {}).get("duplicates") or 0),
    }


def backfill_episode_titles(
    provider: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict:
    logger = logger or logging.getLogger(__name__)
    path_repaired = database.repair_generic_episode_titles_from_paths()
    seasons = database.episode_seasons_missing_titles()
    if provider:
        seasons = [
            season for season in seasons
            if season.get("provider") == provider
        ]
    report = {
        "seasons": 0,
        "updated": path_repaired,
        "path_repaired": path_repaired,
        "failed": 0,
        "errors": [],
    }
    for season in seasons:
        try:
            catalog = discover_episode_catalog(season["source_url"])
            titles = {
                int(episode.episode_number): episode.title
                for episode in catalog.episodes
                if str(episode.title or "").strip()
            }
            updated = database.update_episode_titles(
                int(season["id"]),
                titles,
            )
            report["seasons"] += 1
            report["updated"] += updated
            logger.info(
                "Episode titles backfilled season_id=%s series=%s "
                "season=%s updated=%s",
                season["id"],
                season["series_name"],
                season["name"],
                updated,
            )
        except Exception as exc:
            report["failed"] += 1
            report["errors"].append({
                "season_id": season["id"],
                "series": season["series_name"],
                "season": season["name"],
                "error": str(exc),
            })
            logger.exception(
                "Episode title backfill failed season_id=%s",
                season["id"],
            )
    return report
