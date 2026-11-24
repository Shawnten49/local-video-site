from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import validate_access_level
from local_video_site.episodes.naming import season_number_from_label
from local_video_site.jobs.runtime import JobResult, JobRunner
from local_video_site.media.info import probe_video
from local_video_site.media.assets.previews import generate_preview
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock
from local_video_site.media.assets.thumbnails import generate_thumbnail
from local_video_site.storage.volumes import StorageError, register_external_source


LOGGER = logging.getLogger(__name__)
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".webm",
    ".mkv",
    ".mov",
    ".ts",
    ".avi",
    ".rmvb",
}
SCAN_STATUS_LABELS = {
    "pending": "等待扫描",
    "running": "扫描中",
    "review": "等待审核",
    "committing": "正在入库",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "失败",
    "cancelled": "已取消",
}
SCAN_STAGE_LABELS = {
    "queued": "等待处理",
    "discovering": "发现视频",
    "probing": "解析媒体",
    "generating_assets": "生成资源",
    "waiting_review": "等待人工审核",
    "committing": "写入视频库",
    "finished": "处理完成",
}
ITEM_STATUS_LABELS = {
    "pending": "等待解析",
    "probing": "解析中",
    "ready": "可入库",
    "needs_review": "需要处理",
    "selected": "已选择",
    "rejected": "已忽略",
    "imported": "已入库",
    "duplicate": "已存在",
    "failed": "失败",
}
SEASON_COMPONENT_RE = re.compile(
    r"^(?:第\s*[0-9一二三四五六七八九十百两]+\s*季|"
    r"season\s*\d+|s\d{1,2})$",
    re.IGNORECASE,
)
SXXEXX_RE = re.compile(
    r"(?i)(?<![A-Z0-9])S\s*(\d{1,2})[\s._-]*"
    r"(?:E|EP|EPISODE)\s*(\d{1,4})(?!\d)"
)
SE_DOT_RE = re.compile(
    r"(?i)(?<![A-Z0-9])SE[\s._-]*(\d{1,2})"
    r"[\s._-]*[._-][\s._-]*(\d{1,4})(?!\d)"
)
SEASON_EPISODE_RE = re.compile(
    r"(?i)Season[\s._-]*(\d{1,3})[\s._-]*"
    r"(?:Episode|Ep|E)[\s._-]*(\d{1,4})(?!\d)"
)
EPISODE_ONLY_RE = re.compile(
    r"(?i)(?<![A-Z0-9])EP(?:ISODE)?[\s._-]*(\d{1,4})(?!\d)"
)
E_ONLY_RE = re.compile(
    r"(?i)(?:^|[\s._-])E(\d{1,4})(?:$|[\s._-])"
)
CN_EPISODE_RE = re.compile(r"第\s*(\d{1,4})\s*[集话話]")
LEADING_CN_INDEX_RE = re.compile(r"^\s*(\d{1,4})\s*、")
TRAILING_EPISODE_RE = re.compile(r"(?:^|[\s._-])(\d{1,4})$")


@dataclass(frozen=True)
class EpisodeFileInfo:
    season_number: Optional[int]
    episode_number: Optional[int]
    detection_mode: str = ""
    matched_text: str = ""
    warning: str = ""


class ExternalScanError(RuntimeError):
    def __init__(self, message: str, code: str = "external_scan_failed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_scan_id() -> str:
    return "scan-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )


def _is_ignored_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name.startswith(".")
        or name.endswith((".part", ".ytdl", ".range.json", ".resume.json"))
    )


def _iter_video_files(source: Path) -> Iterable[Path]:
    minimum_bytes = config.minimum_video_bytes()
    if source.is_file():
        if (
            not _is_ignored_file(source)
            and source.suffix.lower() in VIDEO_EXTENSIONS
            and source.stat().st_size >= minimum_bytes
        ):
            yield source
        return
    for directory, dirnames, filenames in os.walk(
        str(source),
        followlinks=False,
    ):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if (
                path.is_symlink()
                or _is_ignored_file(path)
                or path.suffix.lower() not in VIDEO_EXTENSIONS
            ):
                continue
            try:
                if path.stat().st_size < minimum_bytes:
                    continue
            except OSError:
                continue
            yield path.resolve()


def _parse_episode_file(path: Path) -> EpisodeFileInfo:
    stem = path.stem
    for pattern, mode, has_season in (
        (SE_DOT_RE, "SE-xx.yy", True),
        (SXXEXX_RE, "SxxExx", True),
        (SEASON_EPISODE_RE, "Season/Episode", True),
    ):
        match = pattern.search(stem)
        if match is None:
            continue
        season_number = int(match.group(1))
        episode_number = int(match.group(2))
        if season_number > 0 and episode_number > 0:
            return EpisodeFileInfo(
                season_number=season_number,
                episode_number=episode_number,
                detection_mode=mode,
                matched_text=match.group(0),
            )
    for pattern, mode in (
        (EPISODE_ONLY_RE, "EPxx"),
        (E_ONLY_RE, "Exx"),
        (CN_EPISODE_RE, "第N集"),
        (LEADING_CN_INDEX_RE, "前导序号、"),
        (TRAILING_EPISODE_RE, "结尾数字"),
    ):
        match = pattern.search(stem)
        if match is None:
            continue
        episode_number = int(match.group(1))
        if (
            mode in {"前导序号、", "结尾数字"}
            and 1900 <= episode_number <= 2099
        ):
            continue
        if episode_number > 0:
            return EpisodeFileInfo(
                season_number=None,
                episode_number=episode_number,
                detection_mode=mode,
                matched_text=match.group(0),
                warning="未自动识别季号，请确认所属季",
            )
    return EpisodeFileInfo(
        season_number=None,
        episode_number=None,
        warning="未自动识别季或集数",
    )


def _episode_number(path: Path) -> Optional[int]:
    return _parse_episode_file(path).episode_number


def _episode_sort_key(path: Path) -> tuple:
    parsed = _parse_episode_file(path)
    return (
        parsed.episode_number is None,
        parsed.episode_number or 10 ** 9,
        _natural_key(path.name),
    )


def validate_scan_source(raw_path: str) -> Dict:
    requested = Path(raw_path or "").expanduser()
    if requested.is_symlink():
        raise ExternalScanError(
            "不支持扫描符号链接",
            "source_symlink",
        )
    try:
        registered = register_external_source(raw_path)
    except StorageError as exc:
        raise ExternalScanError(str(exc), exc.code) from exc
    source = Path(raw_path).expanduser().resolve()
    if source == registered["mount_path"]:
        raise ExternalScanError(
            "请进入外置盘中的具体视频或目录后再扫描",
            "source_is_volume_root",
        )
    if source.is_symlink() or (not source.is_file() and not source.is_dir()):
        raise ExternalScanError(
            "请选择外盘中的视频文件或目录",
            "source_invalid",
        )
    return {**registered, "source": source}


def _natural_key(value: str) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


def _season_number_from_name(value: str, fallback: int) -> int:
    text = str(value or "").strip()
    if re.search(r"第\s*[0-9一二三四五六七八九十百两]+\s*季", text):
        parsed = season_number_from_label(text, 0)
        if parsed > 0:
            return parsed
    match = re.search(
        r"(?i)(?<![A-Z0-9])(?:season|s)[\s._-]*(\d{1,3})",
        text,
    )
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    if match:
        return max(1, int(match.group(1)))
    return max(1, int(fallback or 1))


def _group_item_payload(
    path: Path,
    directory_season_number: Optional[int] = None,
) -> Dict:
    stat = path.stat()
    parsed = _parse_episode_file(path)
    warnings = []
    if parsed.warning:
        warnings.append(parsed.warning)
    if (
        directory_season_number is not None
        and parsed.season_number is not None
        and parsed.season_number != directory_season_number
    ):
        warnings.append(
            "文件标识第{}季，目录推断为第{}季，请确认归属".format(
                parsed.season_number,
                directory_season_number,
            )
        )
    return {
        "source_path": str(path),
        "file_name": path.name,
        "size": stat.st_size,
        "season_number": parsed.season_number,
        "episode_number": parsed.episode_number,
        "detection_mode": parsed.detection_mode,
        "matched_text": parsed.matched_text,
        "warnings": warnings,
    }


def _direct_file_groups(files: List[Path]) -> tuple:
    by_season = {}
    unassigned = []
    for path in files:
        parsed = _parse_episode_file(path)
        if parsed.season_number is None:
            unassigned.append(path)
        else:
            by_season.setdefault(parsed.season_number, []).append(path)
    if not by_season:
        return ([{
            "group_key": "all",
            "season_name": "全季",
            "season_number": 1,
            "number_editable": False,
            "warnings": [],
            "items": [
                _group_item_payload(path)
                for path in sorted(files, key=_episode_sort_key)
            ],
        }], [])

    groups = []
    warnings = []
    for season_number in sorted(by_season):
        season_files = sorted(
            by_season[season_number],
            key=lambda path: (
                _parse_episode_file(path).episode_number or 10 ** 9,
                _natural_key(path.name),
            ),
        )
        groups.append({
            "group_key": "filename-season-{}".format(season_number),
            "season_name": "第{}季".format(season_number),
            "season_number": season_number,
            "number_editable": True,
            "warnings": [],
            "items": [
                _group_item_payload(path, season_number)
                for path in season_files
            ],
        })
    if unassigned:
        next_number = max(by_season) + 1
        groups.append({
            "group_key": "unassigned",
            "season_name": "未分季",
            "season_number": next_number,
            "number_editable": True,
            "warnings": ["部分文件名未包含明确季号，请确认所属季"],
            "items": [
                _group_item_payload(path)
                for path in sorted(unassigned, key=_episode_sort_key)
            ],
        })
        warnings.append("部分文件未识别到季号，已放入未分季")
    return groups, warnings


def _auto_collection_groups(
    source: Path,
    files: List[Path],
) -> Dict:
    if source.is_file() or not files:
        return {
            "grouping_mode": "single_season",
            "groups": [],
            "warnings": [],
        }
    direct_files = []
    by_directory = {}
    for path in files:
        try:
            relative = path.relative_to(source)
        except ValueError:
            direct_files.append(path)
            continue
        if len(relative.parts) <= 1:
            direct_files.append(path)
        else:
            by_directory.setdefault(relative.parts[0], []).append(path)

    if not by_directory:
        groups, warnings = _direct_file_groups(files)
        return {
            "grouping_mode": (
                "multi_season" if len(groups) > 1 else "single_season"
            ),
            "groups": groups,
            "warnings": warnings,
        }

    groups = []
    used_numbers = set()
    warnings = []
    next_fallback = 1
    for directory_name in sorted(by_directory, key=_natural_key):
        while next_fallback in used_numbers:
            next_fallback += 1
        parsed = _season_number_from_name(directory_name, next_fallback)
        while parsed in used_numbers:
            parsed += 1
        used_numbers.add(parsed)
        next_fallback = max(next_fallback + 1, parsed + 1)
        group_warnings = []
        if not SEASON_COMPONENT_RE.match(directory_name.strip()):
            group_warnings.append("目录名称未明确包含季编号，已自动推断")
        groups.append({
            "group_key": "dir-{}".format(len(groups) + 1),
            "season_name": directory_name,
            "season_number": parsed,
            "number_editable": True,
            "warnings": group_warnings,
            "items": [
                _group_item_payload(path, parsed)
                for path in sorted(
                    by_directory[directory_name],
                    key=_episode_sort_key,
                )
            ],
        })
    if direct_files:
        while next_fallback in used_numbers:
            next_fallback += 1
        groups.append({
            "group_key": "unassigned",
            "season_name": "未分季",
            "season_number": next_fallback,
            "number_editable": True,
            "warnings": ["根目录视频需要确认所属季"],
            "items": [
                _group_item_payload(path)
                for path in sorted(
                    direct_files,
                    key=_episode_sort_key,
                )
            ],
        })
        warnings.append("根目录存在未归入季目录的视频")
    return {
        "grouping_mode": "multi_season" if len(groups) > 1 else "single_season",
        "groups": groups,
        "warnings": warnings,
    }


def preview_scan_source(raw_path: str, scan_mode: str = "batch") -> Dict:
    validated = validate_scan_source(raw_path)
    source = validated["source"]
    files = list(_iter_video_files(source))
    if len(files) > config.external_scan_max_files():
        raise ExternalScanError(
            "视频数量超过单次扫描上限 {} 个，请拆分目录".format(
                config.external_scan_max_files()
            ),
            "scan_too_large",
        )
    samples = []
    for path in files[:20]:
        parsed = _parse_episode_file(path)
        samples.append({
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "season_number": parsed.season_number,
            "episode_number": parsed.episode_number,
            "detection_mode": parsed.detection_mode,
            "matched_text": parsed.matched_text,
            "warnings": [parsed.warning] if parsed.warning else [],
        })
    result = {
        "source_path": str(source),
        "source_type": "file" if source.is_file() else "directory",
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "samples": samples,
        "volume": validated["volume"],
    }
    if scan_mode == "collection" and source.is_dir():
        result.update({
            "suggested_series_name": source.name,
            **_auto_collection_groups(source, files),
        })
    return result


def _normalise_collection_groups(
    source: Path,
    files: List[Path],
    raw_groups: Optional[List[Dict]],
    series_name: str,
    season_name: str,
    season_number: int,
) -> Dict:
    auto = _auto_collection_groups(source, files)
    groups = raw_groups or auto["groups"]
    if not groups:
        raise ExternalScanError(
            "没有可用的视频集分组",
            "collection_groups_empty",
        )
    if not raw_groups and len(groups) == 1:
        if season_name:
            groups[0]["season_name"] = season_name
        if season_number:
            groups[0]["season_number"] = int(season_number)

    file_map = {str(path): path for path in files}
    assigned_paths = set()
    used_numbers = set()
    assignments = {}
    normalized_groups = []
    fixed_single_number = bool(
        len(groups) == 1
        and auto["groups"]
        and auto["groups"][0].get("number_editable") is False
    )
    for group_index, raw_group in enumerate(groups, start=1):
        name = str(raw_group.get("season_name") or "").strip()
        if not name:
            raise ExternalScanError(
                "第 {} 个季分组的季名称不能为空".format(group_index),
                "season_name_required",
            )
        try:
            number = int(raw_group.get("season_number"))
        except (TypeError, ValueError) as exc:
            raise ExternalScanError(
                "第 {} 个季分组的季编号无效".format(group_index),
                "season_number_invalid",
            ) from exc
        if number <= 0:
            raise ExternalScanError(
                "季编号必须是正整数",
                "season_number_invalid",
            )
        if fixed_single_number and number != 1:
            raise ExternalScanError(
                "当前目录直接包含视频，季编号固定为 1",
                "single_season_number_fixed",
            )
        if number in used_numbers:
            raise ExternalScanError(
                "季编号 {} 重复".format(number),
                "duplicate_season_number",
            )
        used_numbers.add(number)
        raw_items = raw_group.get("items") or []
        if not raw_items:
            raise ExternalScanError(
                "季“{}”没有视频".format(name),
                "season_group_empty",
            )
        used_episodes = set()
        normalized_items = []
        for item in raw_items:
            raw_path = (
                item.get("source_path")
                if isinstance(item, dict)
                else item
            )
            path = Path(str(raw_path or "")).expanduser().resolve()
            key = str(path)
            if key not in file_map:
                raise ExternalScanError(
                    "提交的视频不属于本次来源目录：{}".format(path.name),
                    "scan_item_outside_source",
                )
            if key in assigned_paths:
                raise ExternalScanError(
                    "视频被重复分配到多个季：{}".format(path.name),
                    "scan_item_duplicate",
                )
            try:
                episode_number = int(
                    item.get("episode_number")
                    if isinstance(item, dict)
                    else _episode_number(path)
                )
            except (TypeError, ValueError) as exc:
                raise ExternalScanError(
                    "视频 {} 的集数无效".format(path.name),
                    "episode_number_invalid",
                ) from exc
            if episode_number <= 0:
                raise ExternalScanError(
                    "视频 {} 的集数必须是正整数".format(path.name),
                    "episode_number_invalid",
                )
            if episode_number in used_episodes:
                raise ExternalScanError(
                    "季“{}”存在重复集数：第{}集".format(
                        name,
                        episode_number,
                    ),
                    "duplicate_episode_number",
                )
            used_episodes.add(episode_number)
            assigned_paths.add(key)
            assignment = {
                "season_name": name,
                "season_number": number,
                "episode_number": episode_number,
            }
            assignments[key] = assignment
            normalized_items.append({
                "source_path": key,
                "episode_number": episode_number,
            })
        normalized_groups.append({
            "season_name": name,
            "season_number": number,
            "items": normalized_items,
        })

    missing = [
        path for path in files if str(path) not in assigned_paths
    ]
    if missing:
        raise ExternalScanError(
            "还有 {} 个视频未分配季：{}".format(
                len(missing),
                "、".join(path.name for path in missing[:5]),
            ),
            "scan_items_unassigned",
        )
    return {
        "series_name": str(series_name or source.name).strip(),
        "groups": normalized_groups,
        "assignments": assignments,
        "grouping_mode": (
            "multi_season"
            if len(normalized_groups) > 1
            else "single_season"
        ),
    }


def _scan_source_url(scan_id: str) -> str:
    return "external-scan://{}".format(scan_id)


def _target_series_row(connection, series_id: int) -> Dict:
    row = connection.execute(
        """
        SELECT sr.*,
               COALESCE(
                   sr.display_name_override, sr.name
               ) AS display_name
        FROM video_series sr
        WHERE sr.id = ?
        """,
        (int(series_id),),
    ).fetchone()
    if row is None:
        raise ExternalScanError(
            "目标视频集不存在",
            "target_series_not_found",
        )
    if int(row["is_deleted"] or 0):
        raise ExternalScanError(
            "目标视频集已删除，不能继续加入",
            "target_series_deleted",
        )
    active_job = connection.execute(
        """
        SELECT id FROM video_delete_jobs
        WHERE scope_type = 'series'
          AND target_id = ?
          AND status IN ('pending', 'running', 'blocked')
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(series_id),),
    ).fetchone()
    if active_job:
        raise ExternalScanError(
            "目标视频集存在活动删除任务，不能继续加入",
            "target_series_deleting",
        )
    return dict(row)


def _target_season_row(connection, season_id: int) -> Dict:
    row = connection.execute(
        """
        SELECT s.*,
               COALESCE(
                   s.display_name_override, s.name
               ) AS display_name,
               sr.id AS target_series_id,
               sr.name AS series_name,
               COALESCE(
                   sr.display_name_override, sr.name
               ) AS series_display_name,
               sr.is_deleted AS series_is_deleted
        FROM video_seasons s
        JOIN video_series sr ON sr.id = s.series_id
        WHERE s.id = ?
        """,
        (int(season_id),),
    ).fetchone()
    if row is None:
        raise ExternalScanError(
            "目标视频季不存在",
            "target_season_not_found",
        )
    if int(row["is_deleted"] or 0):
        raise ExternalScanError(
            "目标视频季已删除，不能继续加入",
            "target_season_deleted",
        )
    if int(row["series_is_deleted"] or 0):
        raise ExternalScanError(
            "目标视频季所属视频集已删除，不能继续加入",
            "target_series_deleted",
        )
    active_job = connection.execute(
        """
        SELECT id FROM video_delete_jobs
        WHERE status IN ('pending', 'running', 'blocked')
          AND (
              (scope_type = 'season' AND target_id = ?)
              OR (scope_type = 'series' AND target_id = ?)
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(season_id), int(row["target_series_id"])),
    ).fetchone()
    if active_job:
        raise ExternalScanError(
            "目标视频季存在活动删除任务，不能继续加入",
            "target_season_deleting",
        )
    return dict(row)


def get_season_assignment_preview(season_id: int) -> Dict:
    with database.connect() as connection:
        target = _target_season_row(connection, int(season_id))
        rows = connection.execute(
            """
            SELECT episode_number
            FROM video_episodes
            WHERE season_id = ?
            ORDER BY episode_number
            """,
            (int(season_id),),
        ).fetchall()
    used = [int(row["episode_number"]) for row in rows]
    return {
        "season": {
            "id": int(target["id"]),
            "name": str(target["name"]),
            "display_name": str(target["display_name"]),
            "season_number": int(target["season_number"]),
        },
        "series": {
            "id": int(target["target_series_id"]),
            "name": str(target["series_name"]),
            "display_name": str(target["series_display_name"]),
        },
        "episode_count": len(used),
        "used_episode_numbers": used,
        "active_delete_job_id": None,
    }


def _validate_target_season_episode_numbers(
    target_season_id: int,
    episode_numbers: Iterable[int],
    scan_id: str,
) -> Dict:
    raw_numbers = [int(number) for number in episode_numbers]
    numbers = sorted(set(raw_numbers))
    if not numbers or any(number <= 0 for number in numbers):
        raise ExternalScanError(
            "加入现有视频季时，每个视频都必须填写有效集数",
            "target_episode_number_required",
        )
    if len(numbers) != len(raw_numbers):
        raise ExternalScanError(
            "本次扫描存在重复集数",
            "duplicate_episode_number",
        )
    with database.connect() as connection:
        target = _target_season_row(connection, int(target_season_id))
        placeholders = ",".join("?" for _ in numbers)
        rows = connection.execute(
            """
            SELECT e.episode_number, e.origin_scan_id,
                   COALESCE(
                       e.episode_title, e.display_title
                   ) AS title
            FROM video_episodes e
            WHERE e.season_id = ?
              AND e.episode_number IN ({})
            ORDER BY e.episode_number
            """.format(placeholders),
            (int(target_season_id), *numbers),
        ).fetchall()
        conflicts = [
            dict(row)
            for row in rows
            if str(row["origin_scan_id"] or "") != str(scan_id)
        ]
        if conflicts:
            labels = "、".join(
                "第{}集".format(int(row["episode_number"]))
                for row in conflicts
            )
            raise ExternalScanError(
                "无法加入“{} · {}”：{}已存在".format(
                    target["series_display_name"],
                    target["display_name"],
                    labels,
                ),
                "target_episode_conflict",
            )
    return target


def _validate_target_series_assignment(
    target_series_id: int,
    season_numbers: Iterable[int] = (),
    scan_id: str = "",
) -> Dict:
    try:
        target_id = int(target_series_id)
    except (TypeError, ValueError) as exc:
        raise ExternalScanError(
            "请选择加入的目标视频集",
            "target_series_required",
        ) from exc
    if target_id <= 0:
        raise ExternalScanError(
            "请选择加入的目标视频集",
            "target_series_required",
        )
    numbers = sorted({
        int(number)
        for number in season_numbers
        if int(number) > 0
    })
    current_source = _scan_source_url(scan_id) if scan_id else ""
    with database.connect() as connection:
        target = _target_series_row(connection, target_id)
        if numbers:
            placeholders = ",".join("?" for _ in numbers)
            rows = connection.execute(
                """
                SELECT id, name, display_name_override,
                       season_number, source_url, is_deleted
                FROM video_seasons
                WHERE series_id = ?
                  AND season_number IN ({})
                ORDER BY season_number
                """.format(placeholders),
                (target_id, *numbers),
            ).fetchall()
            conflicts = [
                dict(row)
                for row in rows
                if not int(row["is_deleted"] or 0)
                and (
                    not current_source
                    or str(row["source_url"] or "") != current_source
                )
            ]
            if conflicts:
                labels = "、".join(
                    "第{}季（{}）".format(
                        int(row["season_number"]),
                        row["display_name_override"] or row["name"],
                    )
                    for row in conflicts
                )
                raise ExternalScanError(
                    "无法加入视频集 #{}：{} 已存在。"
                    "请修改本次扫描的季编号，或选择其他视频集。".format(
                        target_id,
                        labels,
                    ),
                    "target_season_conflict",
                )
    return target


def get_series_assignment_preview(series_id: int) -> Dict:
    target = _validate_target_series_assignment(series_id)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.name,
                   COALESCE(
                       s.display_name_override, s.name
                   ) AS display_name,
                   s.season_number,
                   (
                       SELECT COUNT(*)
                       FROM video_episodes e
                       WHERE e.season_id = s.id
                   ) AS episode_count,
                   (
                       SELECT COUNT(*)
                       FROM video_episodes e
                       JOIN videos v ON v.id = e.video_id
                       WHERE e.season_id = s.id
                         AND v.is_deleted = 0
                   ) AS video_count
            FROM video_seasons s
            WHERE s.series_id = ? AND s.is_deleted = 0
            ORDER BY s.season_number, s.id
            """,
            (int(series_id),),
        ).fetchall()
    return {
        "id": int(target["id"]),
        "name": str(target["name"]),
        "display_name": str(target["display_name"]),
        "provider": str(target["provider"] or ""),
        "updated_at": str(target["updated_at"]),
        "seasons": [dict(row) for row in rows],
    }


def _upsert_scanned_season_in_series(
    series_id: int,
    scan_id: str,
    season_name: str,
    season_number: int,
) -> Dict:
    now = _now()
    source_url = _scan_source_url(scan_id)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            target = _target_series_row(connection, int(series_id))
            existing = connection.execute(
                """
                SELECT * FROM video_seasons
                WHERE series_id = ? AND season_number = ?
                """,
                (int(series_id), int(season_number)),
            ).fetchone()
            relative_directory = str(
                Path(str(target["name"])) / str(season_name)
            )
            if existing is not None:
                if (
                    not int(existing["is_deleted"] or 0)
                    and str(existing["source_url"] or "") != source_url
                ):
                    raise ExternalScanError(
                        "无法加入视频集 #{}：第{}季已存在".format(
                            int(series_id),
                            int(season_number),
                        ),
                        "target_season_conflict",
                    )
                connection.execute(
                    """
                    UPDATE video_seasons
                    SET name = ?, relative_directory = ?, source_url = ?,
                        display_name_override = CASE
                            WHEN is_deleted = 1 THEN NULL
                            ELSE display_name_override
                        END,
                        representative_video_id = CASE
                            WHEN is_deleted = 1 THEN NULL
                            ELSE representative_video_id
                        END,
                        metadata_video_id = CASE
                            WHEN is_deleted = 1 THEN NULL
                            ELSE metadata_video_id
                        END,
                        cover_path = CASE
                            WHEN is_deleted = 1 THEN NULL
                            ELSE cover_path
                        END,
                        preview_path = CASE
                            WHEN is_deleted = 1 THEN NULL
                            ELSE preview_path
                        END,
                        preview_status = CASE
                            WHEN is_deleted = 1 THEN 'pending'
                            ELSE preview_status
                        END,
                        metadata_status = CASE
                            WHEN is_deleted = 1 THEN 'pending'
                            ELSE metadata_status
                        END,
                        is_deleted = 0, deleted_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(season_name),
                        relative_directory,
                        source_url,
                        now,
                        int(existing["id"]),
                    ),
                )
                season_id = int(existing["id"])
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO video_seasons (
                        series_id, name, season_number,
                        relative_directory, source_url,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(series_id),
                        str(season_name),
                        int(season_number),
                        relative_directory,
                        source_url,
                        now,
                        now,
                    ),
                )
                season_id = int(cursor.lastrowid)
            season = connection.execute(
                "SELECT * FROM video_seasons WHERE id = ?",
                (season_id,),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {
        "series": target,
        "season": dict(season),
    }


def create_scan(
    source_path: str,
    scan_mode: str,
    access_level: int = 0,
    series_name: str = "",
    season_name: str = "",
    season_number: int = 1,
    groups: Optional[List[Dict]] = None,
    attach_to_series: bool = False,
    target_series_id: Optional[int] = None,
    attach_to_season: bool = False,
    target_season_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: str = "",
) -> Dict:
    if scan_mode not in {"file", "collection", "batch"}:
        raise ExternalScanError("不支持的扫描模式", "invalid_scan_mode")
    try:
        access_level = validate_access_level(access_level)
    except ValueError as exc:
        raise ExternalScanError(str(exc), "invalid_access_level") from exc
    validated = validate_scan_source(source_path)
    source = validated["source"]
    if source.is_file() and scan_mode == "collection":
        raise ExternalScanError(
            "单个视频不能按视频集扫描",
            "collection_requires_directory",
        )
    if source.is_dir() and scan_mode == "file":
        scan_mode = "batch"
    files = list(_iter_video_files(source))
    if not files:
        raise ExternalScanError(
            "没有发现受支持的视频文件",
            "scan_empty",
        )
    if len(files) > config.external_scan_max_files():
        raise ExternalScanError(
            "视频数量超过单次扫描上限 {} 个，请拆分目录".format(
                config.external_scan_max_files()
            ),
            "scan_too_large",
        )

    collection = None
    resolved_target_series_id = None
    resolved_target_season_id = None
    grouping_mode = "single_season"
    season_group_count = 0
    if scan_mode == "collection":
        collection = _normalise_collection_groups(
            source,
            files,
            groups,
            series_name,
            season_name,
            season_number,
        )
        series_name = collection["series_name"]
        season_name = collection["groups"][0]["season_name"]
        season_number = int(collection["groups"][0]["season_number"])
        grouping_mode = collection["grouping_mode"]
        season_group_count = len(collection["groups"])
        if not series_name or not season_name:
            raise ExternalScanError(
                "视频集名称和季名称不能为空",
                "collection_names_required",
            )
        if attach_to_series:
            if target_series_id is None:
                raise ExternalScanError(
                    "请填写要加入的现有视频集 ID",
                    "target_series_required",
                )
            _validate_target_series_assignment(
                target_series_id,
                (
                    int(group["season_number"])
                    for group in collection["groups"]
                ),
            )
            resolved_target_series_id = int(target_series_id)
        if attach_to_season:
            raise ExternalScanError(
                "文件夹视频集模式请使用“加入到现有视频集”",
                "target_season_only_for_batch_or_file",
            )
    else:
        if attach_to_series:
            raise ExternalScanError(
                "只有文件夹视频集模式可以加入现有视频集",
                "target_series_only_for_collection",
            )
        if attach_to_season:
            if target_season_id is None:
                raise ExternalScanError(
                    "请填写要加入的现有视频季 ID",
                    "target_season_required",
                )
            with database.connect() as connection:
                target_season = _target_season_row(
                    connection,
                    int(target_season_id),
                )
            resolved_target_season_id = int(target_season["id"])
        series_name = ""
        season_name = ""
        season_number = 1

    scan_id = _new_scan_id()
    now = _now()
    review_required = (
        scan_mode == "batch"
        or (
            scan_mode == "file"
            and resolved_target_season_id is not None
        )
    )
    volume = validated["volume"]
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO video_scan_batches (
                scan_id, source_type, source_path, source_volume_id,
                source_relative_path, scan_mode, status, stage,
                review_required, grouping_mode, season_group_count,
                target_series_id, target_season_id, series_name,
                season_name, season_number, access_level,
                total_files, created_by_user_id,
                created_by_username, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'queued',
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                "file" if source.is_file() else "directory",
                str(source),
                int(volume["id"]),
                str(validated["source_relative_path"]),
                scan_mode,
                1 if review_required else 0,
                grouping_mode,
                season_group_count,
                resolved_target_series_id,
                resolved_target_season_id,
                series_name,
                season_name,
                int(season_number),
                int(access_level),
                len(files),
                created_by_user_id,
                created_by_username,
                now,
                now,
            ),
        )
        for path in files:
            item_series = series_name
            item_season = season_name
            item_season_number = season_number
            episode_number = None
            if scan_mode == "collection":
                assignment = collection["assignments"][str(path)]
                item_season = assignment["season_name"]
                item_season_number = assignment["season_number"]
                episode_number = assignment["episode_number"]
            elif resolved_target_season_id is not None:
                episode_number = _episode_number(path)
            try:
                relative = str(path.relative_to(validated["mount_path"]))
            except ValueError:
                relative = path.name
            stat = path.stat()
            connection.execute(
                """
                INSERT INTO video_scan_items (
                    scan_id, source_path, source_relative_path, file_name,
                    extension, size, mtime_ns, inferred_title,
                    series_name, season_name, season_number,
                    episode_number, episode_title, access_level,
                    status, selected, preview_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'pending', 1, 'pending', ?, ?)
                """,
                (
                    scan_id,
                    str(path),
                    relative,
                    path.name,
                    path.suffix.lower(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    path.stem,
                    item_series,
                    item_season,
                    int(item_season_number),
                    episode_number,
                    path.stem,
                    int(access_level),
                    now,
                    now,
                ),
            )
        connection.commit()
    return get_scan(scan_id) or {}


def get_scan(scan_id: str) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_scan_batches WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        if row is None:
            return None
        target = None
        if row["target_series_id"] is not None:
            target = connection.execute(
                """
                SELECT sr.id, sr.name,
                       COALESCE(
                           sr.display_name_override, sr.name
                       ) AS display_name,
                       sr.is_deleted
                FROM video_series sr
                WHERE sr.id = ?
                """,
                (int(row["target_series_id"]),),
            ).fetchone()
        target_season = None
        if row["target_season_id"] is not None:
            target_season = connection.execute(
                """
                SELECT s.id, s.name,
                       COALESCE(
                           s.display_name_override, s.name
                       ) AS display_name,
                       s.season_number, s.is_deleted,
                       sr.id AS series_id,
                       sr.name AS series_name,
                       COALESCE(
                           sr.display_name_override, sr.name
                       ) AS series_display_name,
                       sr.is_deleted AS series_is_deleted
                FROM video_seasons s
                JOIN video_series sr ON sr.id = s.series_id
                WHERE s.id = ?
                """,
                (int(row["target_season_id"]),),
            ).fetchone()
        item_rows = connection.execute(
            """
            SELECT * FROM video_scan_items
            WHERE scan_id = ?
            ORDER BY season_number, episode_number IS NULL,
                     episode_number, source_path COLLATE NOCASE
            """,
            (scan_id,),
        ).fetchall()
    batch = dict(row)
    batch["target_series_name"] = (
        str(target["display_name"] or target["name"])
        if target is not None
        else ""
    )
    batch["target_series_source_name"] = (
        str(target["name"]) if target is not None else ""
    )
    batch["target_series_is_deleted"] = (
        int(target["is_deleted"] or 0) if target is not None else None
    )
    batch["target_season_name"] = (
        str(target_season["display_name"])
        if target_season is not None
        else ""
    )
    batch["target_season_number"] = (
        int(target_season["season_number"])
        if target_season is not None
        else None
    )
    batch["target_season_series_id"] = (
        int(target_season["series_id"])
        if target_season is not None
        else None
    )
    batch["target_season_series_name"] = (
        str(target_season["series_display_name"])
        if target_season is not None
        else ""
    )
    batch["target_season_is_deleted"] = (
        int(target_season["is_deleted"] or 0)
        if target_season is not None
        else None
    )
    batch["status_label"] = SCAN_STATUS_LABELS.get(
        batch["status"],
        batch["status"],
    )
    batch["stage_label"] = SCAN_STAGE_LABELS.get(
        batch["stage"],
        batch["stage"],
    )
    items = []
    for item_row in item_rows:
        item = dict(item_row)
        item["status_label"] = ITEM_STATUS_LABELS.get(
            item["status"],
            item["status"],
        )
        item["thumbnail_url"] = (
            "/api/manage/external-scans/{}/items/{}/thumbnail".format(
                scan_id,
                item["id"],
            )
            if item.get("thumbnail_path")
            else ""
        )
        item["preview_url"] = (
            "/api/manage/external-scans/{}/items/{}/preview".format(
                scan_id,
                item["id"],
            )
            if (
                item.get("preview_path")
                and item.get("preview_status") == "ready"
            )
            else ""
        )
        items.append(item)
    batch["items"] = items
    return batch


def list_scans(limit: int = 20) -> Dict:
    limit = max(1, min(100, int(limit)))
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT b.*,
                   COALESCE(
                       sr.display_name_override, sr.name
                   ) AS target_series_name,
                   sr.is_deleted AS target_series_is_deleted,
                   COALESCE(
                       ts.display_name_override, ts.name
                   ) AS target_season_name,
                   ts.season_number AS target_season_number,
                   tsr.id AS target_season_series_id,
                   COALESCE(
                       tsr.display_name_override, tsr.name
                   ) AS target_season_series_name,
                   ts.is_deleted AS target_season_is_deleted
            FROM video_scan_batches b
            LEFT JOIN video_series sr
              ON sr.id = b.target_series_id
            LEFT JOIN video_seasons ts
              ON ts.id = b.target_season_id
            LEFT JOIN video_series tsr
              ON tsr.id = ts.series_id
            ORDER BY b.created_at DESC, b.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["status_label"] = SCAN_STATUS_LABELS.get(
            item["status"],
            item["status"],
        )
        item["stage_label"] = SCAN_STAGE_LABELS.get(
            item["stage"],
            item["stage"],
        )
        items.append(item)
    return {"items": items, "total": len(items)}


def get_scan_asset(scan_id: str, item_id: int, kind: str) -> Optional[Path]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT thumbnail_path, preview_path FROM video_scan_items
            WHERE scan_id = ? AND id = ?
            """,
            (scan_id, int(item_id)),
        ).fetchone()
    if row is None:
        return None
    value = row["thumbnail_path"] if kind == "thumbnail" else row["preview_path"]
    if not value:
        return None
    return Path(value)


def recover_interrupted_scans() -> int:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_scan_batches
            SET status = 'pending', stage = 'queued', updated_at = ?
            WHERE status IN ('running', 'committing')
            """,
            (_now(),),
        )
        return cursor.rowcount


def claim_scan() -> Optional[Dict]:
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM video_scan_batches
            WHERE status = 'pending'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE video_scan_batches
            SET status = 'running', stage = 'discovering',
                started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (_now(), _now(), int(row["id"])),
        )
        connection.commit()
    result = dict(row)
    result.update({"status": "running", "stage": "discovering"})
    return result


def request_cancel(scan_id: str) -> bool:
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status FROM video_scan_batches WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        if row["status"] in {"pending", "review"}:
            connection.execute(
                """
                UPDATE video_scan_batches
                SET status = 'cancelled', cancel_requested = 1,
                    stage = 'finished', finished_at = ?, updated_at = ?
                WHERE scan_id = ?
                """,
                (_now(), _now(), scan_id),
            )
        elif row["status"] in {"running", "committing"}:
            connection.execute(
                """
                UPDATE video_scan_batches
                SET cancel_requested = 1, updated_at = ?
                WHERE scan_id = ?
                """,
                (_now(), scan_id),
            )
        else:
            connection.rollback()
            return False
        connection.commit()
    return True


def _update_batch(scan_id: str, **fields) -> None:
    allowed = {
        "status",
        "stage",
        "processed_files",
        "ready_files",
        "failed_files",
        "imported_files",
        "progress_percent",
        "error_code",
        "error_detail",
        "started_at",
        "finished_at",
        "cancel_requested",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute(
            "UPDATE video_scan_batches SET {} WHERE scan_id = ?".format(
                assignments
            ),
            (*values.values(), scan_id),
        )


def _update_item(item_id: int, **fields) -> None:
    allowed = {
        "duration",
        "width",
        "height",
        "video_codec",
        "audio_codec",
        "episode_number",
        "status",
        "selected",
        "thumbnail_path",
        "preview_path",
        "preview_status",
        "preview_start",
        "preview_duration",
        "preview_error",
        "existing_video_id",
        "video_id",
        "error_code",
        "error_detail",
        "access_level",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute(
            "UPDATE video_scan_items SET {} WHERE id = ?".format(assignments),
            (*values.values(), int(item_id)),
        )


def _representative_item_ids(items: List[Dict]) -> set:
    grouped = {}
    for item in items:
        if item.get("status") == "failed":
            continue
        key = (
            str(item.get("series_name") or ""),
            int(item.get("season_number") or 1),
        )
        current = grouped.get(key)
        if current is None:
            grouped[key] = item
            continue
        current_episode = current.get("episode_number")
        item_episode = item.get("episode_number")
        if current_episode is None or (
            item_episode is not None and item_episode < current_episode
        ):
            grouped[key] = item
    return {int(item["id"]) for item in grouped.values()}


def _process_items(
    batch: Dict,
    logger: logging.Logger,
) -> List[Dict]:
    scan_id = str(batch["scan_id"])
    items = list((get_scan(scan_id) or {}).get("items") or [])
    representatives = _representative_item_ids(items)
    processed = 0
    ready = 0
    failed = 0
    for item in items:
        current = get_scan(scan_id)
        if current and int(current.get("cancel_requested") or 0):
            break
        if item["status"] == "imported":
            processed += 1
            ready += 1
            continue
        _update_item(item["id"], status="probing")
        try:
            path = Path(item["source_path"])
            if not path.is_file():
                raise ExternalScanError(
                    "外盘视频文件不存在",
                    "source_missing",
                )
            stat = path.stat()
            if (
                stat.st_size != int(item["size"])
                or stat.st_mtime_ns != int(item["mtime_ns"])
            ):
                raise ExternalScanError(
                    "扫描期间视频文件发生变化，请重新扫描",
                    "source_changed",
                )
            info = probe_video(path)
            needs_assets = (
                batch["scan_mode"] != "collection"
                or int(item["id"]) in representatives
            )
            thumbnail = None
            if needs_assets:
                thumbnail = generate_thumbnail(
                    video_path=path,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    duration=info.get("duration"),
                )
            preview = None
            preview_error = None
            if needs_assets:
                try:
                    preview = generate_preview({
                        "id": int(item["id"]),
                        "path": str(path),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "duration": info.get("duration"),
                    }, logger=logger)
                except Exception as exc:
                    preview_error = str(exc)
                    logger.warning(
                        "External scan preview failed scan_id=%s item_id=%s: %s",
                        scan_id,
                        item["id"],
                        exc,
                    )
            _update_item(
                item["id"],
                duration=info.get("duration"),
                width=info.get("width"),
                height=info.get("height"),
                video_codec=info.get("video_codec"),
                audio_codec=info.get("audio_codec"),
                thumbnail_path=str(thumbnail) if thumbnail else None,
                preview_path=str(preview["path"]) if preview else None,
                preview_status="ready" if preview else "failed",
                preview_start=preview.get("start") if preview else None,
                preview_duration=preview.get("duration") if preview else None,
                preview_error=preview_error,
                status="ready",
                error_code=(
                    "" if info or thumbnail else "metadata_unavailable"
                ),
                error_detail=(
                    ""
                    if info or thumbnail
                    else "未能读取媒体信息或生成缩略图"
                ),
            )
            ready += 1
        except Exception as exc:
            failed += 1
            _update_item(
                item["id"],
                status="failed",
                error_code=getattr(exc, "code", "scan_item_failed"),
                error_detail=str(exc),
            )
            logger.warning(
                "External scan item failed scan_id=%s item_id=%s: %s",
                scan_id,
                item["id"],
                exc,
            )
        processed += 1
        percent = processed * 100.0 / max(1, len(items))
        _update_batch(
            scan_id,
            processed_files=processed,
            ready_files=ready,
            failed_files=failed,
            progress_percent=round(percent, 1),
            stage="generating_assets",
        )
    return list((get_scan(scan_id) or {}).get("items") or [])


def _existing_video(
    path: Path,
    volume_id: int,
    relative_path: str,
) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM videos
            WHERE is_deleted = 0
              AND (
                path = ?
                OR (
                    storage_volume_id = ?
                    AND storage_relative_path = ?
                )
              )
            LIMIT 1
            """,
            (str(path), int(volume_id), relative_path),
        ).fetchone()
    return dict(row) if row else None


def _set_external_video_fields(
    video_id: int,
    volume_id: int,
    relative_path: str,
    access_level: int,
) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE videos
            SET storage_volume_id = ?, storage_relative_path = ?,
                access_level = ?, missing = 0, is_deleted = 0,
                deleted_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                int(volume_id),
                relative_path,
                int(access_level),
                _now(),
                int(video_id),
            ),
        )


def _link_external_episode(
    batch: Dict,
    item: Dict,
    video_id: int,
) -> Dict:
    episode_number = int(item.get("episode_number") or 0)
    if not episode_number:
        raise ExternalScanError(
            "加入现有视频季时必须填写有效集数",
            "episode_number_missing",
        )
    target_season = None
    if batch.get("target_season_id"):
        with database.connect() as connection:
            target_season = _target_season_row(
                connection,
                int(batch["target_season_id"]),
            )
        season_id = int(target_season["id"])
    else:
        season_name = str(
            item.get("season_name") or batch["season_name"]
        ).strip()
        season_number = int(
            item.get("season_number") or batch.get("season_number") or 1
        )
        if batch.get("target_series_id"):
            season_info = _upsert_scanned_season_in_series(
                int(batch["target_series_id"]),
                str(batch["scan_id"]),
                season_name,
                season_number,
            )
        else:
            series_name = str(
                item.get("series_name") or batch["series_name"]
            ).strip()
            relative_directory = str(Path(series_name) / season_name)
            season_info = database.upsert_video_season(
                series_name=series_name,
                season_name=season_name,
                season_number=season_number,
                provider="external_scan",
                source_url=_scan_source_url(str(batch["scan_id"])),
                relative_directory=relative_directory,
            )
        season_id = int(season_info["season"]["id"])
    source_url = "external-scan://{}/{}".format(
        batch["storage_key"] if "storage_key" in batch else batch["source_volume_id"],
        quote(str(item["source_relative_path"]), safe="/"),
    )
    now = _now()
    with database.connect() as connection:
        existing = connection.execute(
            """
            SELECT id, season_id, episode_number, origin_scan_id
            FROM video_episodes
            WHERE (season_id = ? AND episode_number = ?)
               OR absolute_path = ?
            LIMIT 1
            """,
            (season_id, episode_number, str(item["source_path"])),
        ).fetchone()
        if existing:
            if target_season is not None and (
                int(existing["season_id"]) != season_id
                or int(existing["episode_number"]) != episode_number
                or str(existing["origin_scan_id"] or "")
                != str(batch["scan_id"])
            ):
                raise ExternalScanError(
                    "目标视频季的第{}集已存在".format(episode_number),
                    "target_episode_conflict",
                )
            episode_id = int(existing["id"])
            connection.execute(
                """
                UPDATE video_episodes
                SET video_id = ?, episode_title = ?, relative_path = ?,
                    absolute_path = ?, status = 'ready',
                    origin_type = 'external_scan', origin_scan_id = ?,
                    storage_volume_id = ?, storage_relative_path = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(video_id),
                    item.get("episode_title") or item["inferred_title"],
                    str(item["source_relative_path"]),
                    str(item["source_path"]),
                    batch["scan_id"],
                    int(batch["source_volume_id"]),
                    str(item["source_relative_path"]),
                    now,
                    episode_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO video_episodes (
                    season_id, episode_number, display_title, episode_title,
                    source_url, normalized_source_url, relative_path,
                    absolute_path, video_id, status, origin_type,
                    origin_scan_id, storage_volume_id,
                    storage_relative_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready',
                          'external_scan', ?, ?, ?, ?, ?)
                """,
                (
                    season_id,
                    episode_number,
                    "第{}集".format(episode_number),
                    item.get("episode_title") or item["inferred_title"],
                    source_url,
                    source_url.casefold(),
                    str(item["source_relative_path"]),
                    str(item["source_path"]),
                    int(video_id),
                    batch["scan_id"],
                    int(batch["source_volume_id"]),
                    str(item["source_relative_path"]),
                    now,
                    now,
                ),
            )
            episode_id = int(cursor.lastrowid)
    return {
        "season_id": season_id,
        "episode_id": episode_id,
        "episode_number": episode_number,
    }


def _commit_item(batch: Dict, item: Dict, access_level: int) -> Dict:
    path = Path(item["source_path"])
    if not path.is_file():
        raise ExternalScanError(
            "外盘视频文件不存在",
            "source_missing",
        )
    existing = _existing_video(
        path,
        int(batch["source_volume_id"]),
        str(item["source_relative_path"]),
    )
    if existing:
        _update_item(
            item["id"],
            status="duplicate",
            existing_video_id=int(existing["id"]),
            video_id=int(existing["id"]),
        )
        return {
            "status": "duplicate",
            "video_id": int(existing["id"]),
        }
    stat = path.stat()
    database.upsert_video(
        path=path,
        title=(
            str(item["inferred_title"])
            if batch["scan_mode"] != "collection"
            else str(item.get("episode_title") or item["inferred_title"])
        ),
        directory=(
            str(
                batch.get("target_series_name")
                or batch["series_name"]
            )
            if batch["scan_mode"] == "collection"
            else Path(batch["source_path"]).parent.name
        ),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    video = database.get_video_by_path(path)
    if video is None:
        raise ExternalScanError("视频入库失败", "video_insert_failed")
    video_id = int(video["id"])
    _set_external_video_fields(
        video_id,
        int(batch["source_volume_id"]),
        str(item["source_relative_path"]),
        access_level,
    )
    episode = None
    if (
        batch["scan_mode"] == "collection"
        or batch.get("target_season_id") is not None
    ):
        episode = _link_external_episode(batch, item, video_id)

    info = {
        "duration": item.get("duration"),
        "width": item.get("width"),
        "height": item.get("height"),
        "video_codec": item.get("video_codec"),
        "audio_codec": item.get("audio_codec"),
    }
    database.update_metadata(
        video_id=video_id,
        info=info,
        thumbnail_path=item.get("thumbnail_path"),
        status="ready" if (item.get("duration") or item.get("thumbnail_path")) else "unavailable",
        error=item.get("error_detail") or None,
    )
    database.set_video_access_level(video_id, access_level)
    if item.get("preview_path") and item.get("preview_status") == "ready":
        database.save_preview_result(
            video_id=video_id,
            status="ready",
            preview_path=Path(item["preview_path"]).name,
            start=item.get("preview_start"),
            duration=item.get("preview_duration"),
            source_size=int(item["size"]),
            source_mtime_ns=int(item["mtime_ns"]),
        )
    else:
        database.save_preview_result(
            video_id=video_id,
            status="not_required",
            error=item.get("preview_error") or None,
            source_size=int(item["size"]),
            source_mtime_ns=int(item["mtime_ns"]),
        )
    return {
        "status": "imported",
        "video_id": video_id,
        "episode": episode,
    }


def _finish_season_assets(batch: Dict, items: List[Dict]) -> None:
    if batch.get("target_season_id"):
        imported = [
            item for item in items
            if item.get("video_id") and item.get("episode_number")
        ]
        if not imported:
            return
        representative = min(
            imported,
            key=lambda item: int(item["episode_number"]),
        )
        season_id = int(batch["target_season_id"])
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_seasons
                SET representative_video_id = COALESCE(
                        representative_video_id, ?
                    ),
                    metadata_video_id = COALESCE(
                        metadata_video_id, ?
                    ),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(representative["video_id"]),
                    int(representative["video_id"]),
                    _now(),
                    season_id,
                ),
            )
        database.update_season_assets_from_video(
            int(representative["video_id"])
        )
        return
    if batch["scan_mode"] != "collection":
        return
    grouped = {}
    for item in items:
        if not item.get("video_id") or not item.get("episode_number"):
            continue
        key = (
            str(item.get("series_name") or batch["series_name"]),
            int(item.get("season_number") or batch["season_number"] or 1),
        )
        current = grouped.get(key)
        if current is None or int(item["episode_number"]) < int(
            current["episode_number"]
        ):
            grouped[key] = item
    for item in grouped.values():
        season_number = int(
            item.get("season_number") or batch["season_number"] or 1
        )
        season_name = str(
            item.get("season_name") or batch["season_name"]
        )
        if batch.get("target_series_id"):
            season = _upsert_scanned_season_in_series(
                int(batch["target_series_id"]),
                str(batch["scan_id"]),
                season_name,
                season_number,
            )
        else:
            season = database.upsert_video_season(
                series_name=str(
                    item.get("series_name") or batch["series_name"]
                ),
                season_name=season_name,
                season_number=season_number,
                provider="external_scan",
                source_url=_scan_source_url(str(batch["scan_id"])),
                relative_directory=str(
                    Path(
                        str(
                            item.get("series_name")
                            or batch["series_name"]
                        )
                    )
                    / season_name
                ),
            )
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_seasons
                SET representative_video_id = ?, metadata_video_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(item["video_id"]),
                    int(item["video_id"]),
                    _now(),
                    int(season["season"]["id"]),
                ),
            )
        database.update_season_assets_from_video(int(item["video_id"]))


def _commit_items(
    batch: Dict,
    items: List[Dict],
    access_levels: Dict[int, int],
    logger: logging.Logger,
) -> Dict:
    selected = {
        int(item["id"]): int(
            access_levels.get(
                int(item["id"]),
                item.get("access_level") or batch["access_level"],
            )
        )
        for item in items
        if item["status"] in {"ready", "selected", "imported"}
    }
    if not selected:
        raise ExternalScanError(
            "请至少选择一个可入库视频",
            "scan_items_required",
        )
    for level in selected.values():
        validate_access_level(level)
    if batch["scan_mode"] == "collection":
        levels = set(selected.values())
        if len(levels) > 1:
            raise ExternalScanError(
                "同一视频集必须使用相同的访问级别",
                "collection_access_level_mismatch",
            )
        if batch.get("target_series_id"):
            _validate_target_series_assignment(
                int(batch["target_series_id"]),
                (
                    int(item.get("season_number") or 1)
                    for item in items
                    if int(item["id"]) in selected
                ),
                scan_id=str(batch["scan_id"]),
            )
    if batch.get("target_season_id"):
        eligible_ids = {
            int(item["id"])
            for item in items
            if item["status"] in {"ready", "selected", "imported"}
        }
        if set(selected) != eligible_ids:
            raise ExternalScanError(
                "加入现有视频季时必须选择全部可入库视频",
                "target_season_items_incomplete",
            )
        failed_count = sum(
            1 for item in items if item["status"] == "failed"
        )
        if failed_count:
            raise ExternalScanError(
                "仍有 {} 个视频解析失败，请先重试或处理失败项".format(
                    failed_count
                ),
                "target_season_items_failed",
            )
        _validate_target_season_episode_numbers(
            int(batch["target_season_id"]),
            (
                int(item.get("episode_number") or 0)
                for item in items
                if int(item["id"]) in selected
            ),
            scan_id=str(batch["scan_id"]),
        )
    _update_batch(
        batch["scan_id"],
        status="committing",
        stage="committing",
        progress_percent=95,
    )
    lock_handle = None
    imported = 0
    duplicates = 0
    failed = 0
    committed_video_ids = []
    try:
        try:
            lock_handle = acquire_library_write_lock()
        except ScanAlreadyRunning as exc:
            raise ExternalScanError(
                "视频库扫描正在进行，请稍后重试",
                "library_scan_running",
            ) from exc
        for item in items:
            if int(item["id"]) not in selected:
                continue
            if item["status"] == "imported":
                imported += 1
                if item.get("video_id"):
                    committed_video_ids.append(int(item["video_id"]))
                continue
            try:
                result = _commit_item(
                    batch,
                    item,
                    selected[int(item["id"])],
                )
                if result["status"] == "duplicate":
                    duplicates += 1
                    committed_video_ids.append(int(result["video_id"]))
                    continue
                imported += 1
                committed_video_ids.append(int(result["video_id"]))
                _update_item(
                    item["id"],
                    status="imported",
                    video_id=result["video_id"],
                )
                item["status"] = "imported"
                item["video_id"] = result["video_id"]
            except Exception as exc:
                failed += 1
                _update_item(
                    item["id"],
                    status="failed",
                    error_code=getattr(exc, "code", "commit_failed"),
                    error_detail=str(exc),
                )
                logger.warning(
                    "External scan commit item failed scan_id=%s item_id=%s: %s",
                    batch["scan_id"],
                    item["id"],
                    exc,
                )
    finally:
        if lock_handle is not None:
            release_library_write_lock(lock_handle)
    final_items = list((get_scan(batch["scan_id"]) or {}).get("items") or [])
    _finish_season_assets(batch, final_items)
    if failed:
        status = "partial"
    elif duplicates and not imported:
        status = "partial"
    else:
        status = "completed"
    _update_batch(
        batch["scan_id"],
        status=status,
        stage="finished",
        imported_files=imported,
        failed_files=failed,
        progress_percent=100,
        finished_at=_now(),
    )
    return {
        "status": status,
        "imported": imported,
        "duplicates": duplicates,
        "failed": failed,
        "video_ids": sorted(set(committed_video_ids)),
    }


def commit_scan(
    scan_id: str,
    item_access_levels: Optional[Dict[int, int]] = None,
    selected_item_ids: Optional[List[int]] = None,
    item_episode_numbers: Optional[Dict[int, Optional[int]]] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict:
    logger = logger or LOGGER
    batch = get_scan(scan_id)
    if batch is None:
        raise ExternalScanError("扫描任务不存在", "scan_not_found")
    if batch["status"] not in {"review", "partial", "completed"}:
        raise ExternalScanError(
            "当前扫描任务还不能确认入库",
            "scan_not_ready",
        )
    items = batch["items"]
    if item_episode_numbers:
        for item in items:
            item_id = int(item["id"])
            if item_id not in item_episode_numbers:
                continue
            raw_number = item_episode_numbers[item_id]
            try:
                number = (
                    int(raw_number)
                    if raw_number is not None and str(raw_number).strip()
                    else None
                )
            except (TypeError, ValueError) as exc:
                raise ExternalScanError(
                    "视频集数无效",
                    "episode_number_invalid",
                ) from exc
            _update_item(item_id, episode_number=number)
        items = (get_scan(scan_id) or {}).get("items", [])
    if selected_item_ids is not None:
        selected = {int(value) for value in selected_item_ids}
        items = [item for item in items if int(item["id"]) in selected]
        for item in items:
            _update_item(item["id"], selected=1)
    result = _commit_items(
        batch,
        items,
        item_access_levels or {},
        logger,
    )
    for key in ("target_series_id", "target_season_id"):
        if batch.get(key) is not None:
            result[key] = batch[key]
    return result


def retry_failed_scan(scan_id: str) -> None:
    batch = get_scan(scan_id)
    if batch is None:
        raise ExternalScanError("扫描任务不存在", "scan_not_found")
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE video_scan_items
            SET status = 'pending', error_code = NULL, error_detail = NULL,
                updated_at = ?
            WHERE scan_id = ? AND status = 'failed'
            """,
            (_now(), scan_id),
        )
    _update_batch(
        scan_id,
        status="pending",
        stage="queued",
        cancel_requested=0,
        error_code=None,
        error_detail=None,
        finished_at=None,
    )


def process_scan(
    scan_id: str,
    logger: Optional[logging.Logger] = None,
) -> Dict:
    logger = logger or LOGGER
    batch = get_scan(scan_id)
    if batch is None:
        raise ExternalScanError("扫描任务不存在", "scan_not_found")
    try:
        _update_batch(
            scan_id,
            status="running",
            stage="probing",
            started_at=batch.get("started_at") or _now(),
        )
        items = _process_items(batch, logger)
        latest = get_scan(scan_id)
        if latest and int(latest.get("cancel_requested") or 0):
            _update_batch(
                scan_id,
                status="cancelled",
                stage="finished",
                finished_at=_now(),
            )
            return {"status": "cancelled"}
        ready_items = [
            item for item in items
            if item["status"] == "ready"
        ]
        if int(batch.get("review_required") or 0):
            status = "review" if ready_items else "failed"
            _update_batch(
                scan_id,
                status=status,
                stage="waiting_review" if ready_items else "finished",
                finished_at=None if ready_items else _now(),
                error_code="" if ready_items else "no_ready_items",
                error_detail=(
                    ""
                    if ready_items
                    else "没有可加入视频库的视频"
                ),
            )
            return get_scan(scan_id) or {}
        access_levels = {
            int(item["id"]): int(item["access_level"])
            for item in ready_items
        }
        commit_result = _commit_items(
            batch,
            ready_items,
            access_levels,
            logger,
        )
        return {
            **(get_scan(scan_id) or {}),
            **commit_result,
        }
    except ExternalScanError as exc:
        _update_batch(
            scan_id,
            status="failed",
            stage="finished",
            error_code=exc.code,
            error_detail=str(exc),
            finished_at=_now(),
        )
        raise
    except Exception as exc:
        _update_batch(
            scan_id,
            status="failed",
            stage="finished",
            error_code="unexpected_error",
            error_detail=str(exc),
            finished_at=_now(),
        )
        logger.exception("External scan failed scan_id=%s", scan_id)
        raise


class ExternalScanWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.runner = JobRunner(
            name="external-video-scan-worker",
            stop_event=self.stop_event,
            claim_job=claim_scan,
            process_job=lambda batch: JobResult.from_status(
                process_scan(
                    batch["scan_id"],
                    logger=self.logger,
                ).get("status", "")
            ),
            poll_seconds=config.external_scan_poll_seconds,
            logger=self.logger,
        )

    def start(self):
        self.runner.start()
        return self

    @property
    def thread(self):
        return self.runner.thread

    def stop(self) -> None:
        self.runner.stop(timeout=5)
