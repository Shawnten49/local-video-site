from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from local_video_site import database


SOURCE_OPTIONS = (
    "player-config",
    "network",
    "video-element",
    "performance",
)
IDENTITY_MODES = ("site_default", "page_id", "loose")
KIND_OPTIONS = ("hls", "dash", "file")
QUALITY_TARGETS = (
    "auto",
    "source_first",
    "2160p",
    "1080p",
    "720p",
    "480p",
    "360p",
)
QUALITY_FALLBACKS = ("nearest_below", "best_effort", "strict")
FORMAT_STRATEGIES = ("auto", "combined", "separate_av")
MERGE_OUTPUT_FORMATS = ("mp4", "mkv")

IDENTITY_LABELS = {
    "site_default": "站点默认（优先页面视频）",
    "page_id": "必须匹配页面视频身份",
    "loose": "宽松（仅用于特殊站点）",
}
QUALITY_FALLBACK_LABELS = {
    "nearest_below": "精确档位 → 最近的较低档",
    "best_effort": "精确档位 → 最接近的可用档位",
    "strict": "仅使用精确档位",
}
QUALITY_TARGET_LABELS = {
    "auto": "最高可用清晰度（默认上限 1080p）",
    "source_first": "优先正确媒体来源",
    "2160p": "4K 优先（最高 2160p）",
    "1080p": "1080p",
    "720p": "720p",
    "480p": "480p",
    "360p": "360p",
}
FORMAT_STRATEGY_LABELS = {
    "auto": "自动识别",
    "combined": "单文件音视频",
    "separate_av": "分离音视频并合并",
}

_GENERIC_POLICY = {
    "preset_key": "generic",
    "source_order": list(SOURCE_OPTIONS),
    "identity_mode": "site_default",
    "kind_order": ["hls", "dash", "file"],
    "quality_target": "auto",
    "quality_fallback": "nearest_below",
    "min_height": 360,
    "max_height": 1080,
    "allow_below_minimum": True,
    "allow_page_fallback": True,
    "play_selector": "",
    "format_strategy": "auto",
    "prefer_h264": False,
    "merge_output_format": "mp4",
}

BUILTIN_MEDIA_PRESETS: Dict[str, Dict] = {
    "generic": _GENERIC_POLICY,
    "xvideos": {
        **_GENERIC_POLICY,
        "preset_key": "xvideos",
    },
    "xhamster": {
        **_GENERIC_POLICY,
        "preset_key": "xhamster",
        "source_order": [
            "player-config",
            "network",
            "video-element",
            "performance",
        ],
    },
    "porntrex": {
        **_GENERIC_POLICY,
        "preset_key": "porntrex",
        "source_order": [
            "player-config",
            "network",
            "video-element",
            "performance",
        ],
        "kind_order": ["file", "hls", "dash"],
    },
    "pornone": {
        **_GENERIC_POLICY,
        "preset_key": "pornone",
        "source_order": [
            "network",
            "video-element",
            "player-config",
            "performance",
        ],
        "kind_order": ["file", "hls", "dash"],
    },
    "tnaflix": {
        **_GENERIC_POLICY,
        "preset_key": "tnaflix",
        "source_order": [
            "player-config",
            "network",
            "video-element",
            "performance",
        ],
        "kind_order": ["file", "hls", "dash"],
    },
    "sxyprn": {
        **_GENERIC_POLICY,
        "preset_key": "sxyprn",
        "source_order": [
            "network",
            "video-element",
            "player-config",
            "performance",
        ],
        "identity_mode": "loose",
        "kind_order": ["file", "hls", "dash"],
        "quality_target": "source_first",
    },
    "bilibili": {
        **_GENERIC_POLICY,
        "preset_key": "bilibili",
        "source_order": [
            "player-config",
            "network",
            "video-element",
            "performance",
        ],
        "identity_mode": "loose",
        "kind_order": ["dash", "hls", "file"],
        "format_strategy": "separate_av",
        "prefer_h264": True,
        "merge_output_format": "mp4",
    },
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_preset_key(site_key: str) -> str:
    key = str(site_key or "").strip().lower()
    if key in BUILTIN_MEDIA_PRESETS:
        return key
    domain_key = key.split(".", 1)[0]
    if domain_key in BUILTIN_MEDIA_PRESETS:
        return domain_key
    return "generic"


def get_preset(preset_key: str) -> Dict:
    key = str(preset_key or "generic").strip()
    if key not in BUILTIN_MEDIA_PRESETS:
        raise ValueError("媒体选择预设无效")
    return deepcopy(BUILTIN_MEDIA_PRESETS[key])


def preset_options() -> List[Dict]:
    labels = {
        "generic": "通用兜底",
        "xvideos": "xVideos",
        "xhamster": "xHamster",
        "porntrex": "Porntrex",
        "pornone": "PornOne",
        "tnaflix": "TNAflix",
        "sxyprn": "SexyPorn / sxyprn",
        "bilibili": "Bilibili",
    }
    return [
        {
            "key": key,
            "label": labels.get(key, key),
            "defaults": get_preset(key),
        }
        for key in BUILTIN_MEDIA_PRESETS
    ]


def _clean_order(value: Iterable, allowed: Iterable[str], label: str) -> List[str]:
    raw = list(value or [])
    result = []
    for item in raw:
        key = str(item or "").strip().lower()
        if key and key not in result:
            result.append(key)
    invalid = [item for item in result if item not in set(allowed)]
    if invalid:
        raise ValueError("{}包含无效项: {}".format(label, ", ".join(invalid)))
    if not result:
        raise ValueError("{}不能为空".format(label))
    return result


def _validate_policy(data: Dict) -> Dict:
    preset_key = str(data.get("preset_key") or "generic").strip()
    preset = get_preset(preset_key)
    result = {
        **preset,
        "preset_key": preset_key,
        "source_order": _clean_order(
            data.get("source_order") or preset["source_order"],
            SOURCE_OPTIONS,
            "媒体来源顺序",
        ),
        "identity_mode": str(
            data.get("identity_mode") or preset["identity_mode"]
        ).strip(),
        "kind_order": _clean_order(
            data.get("kind_order") or preset["kind_order"],
            KIND_OPTIONS,
            "媒体类型顺序",
        ),
        "quality_target": str(
            data.get("quality_target") or preset["quality_target"]
        ).strip().lower(),
        "quality_fallback": str(
            data.get("quality_fallback") or preset["quality_fallback"]
        ).strip().lower(),
        "min_height": int(data.get("min_height") or preset["min_height"]),
        "max_height": int(data.get("max_height") or preset["max_height"]),
        "allow_below_minimum": bool(
            data.get(
                "allow_below_minimum",
                preset["allow_below_minimum"],
            )
        ),
        "allow_page_fallback": bool(
            data.get(
                "allow_page_fallback",
                preset["allow_page_fallback"],
            )
        ),
        "play_selector": str(
            data.get("play_selector") or preset["play_selector"]
        ).strip(),
        "format_strategy": str(
            data.get("format_strategy") or preset["format_strategy"]
        ).strip().lower(),
        "prefer_h264": bool(
            data.get("prefer_h264", preset["prefer_h264"])
        ),
        "merge_output_format": str(
            data.get(
                "merge_output_format",
                preset["merge_output_format"],
            )
        ).strip().lower(),
    }
    if result["identity_mode"] not in IDENTITY_MODES:
        raise ValueError("视频身份校验模式无效")
    if result["quality_target"] not in QUALITY_TARGETS:
        raise ValueError("清晰度目标无效")
    if result["quality_fallback"] not in QUALITY_FALLBACKS:
        raise ValueError("清晰度回退方式无效")
    if result["format_strategy"] not in FORMAT_STRATEGIES:
        raise ValueError("下载格式策略无效")
    if result["merge_output_format"] not in MERGE_OUTPUT_FORMATS:
        raise ValueError("合并容器格式无效")
    if not 240 <= result["min_height"] <= result["max_height"] <= 2160:
        raise ValueError("清晰度范围必须满足 240p–2160p 且最小值不大于最大值")
    if len(result["play_selector"]) > 500:
        raise ValueError("播放按钮选择器过长")
    return result


def _policy_row(site_key: str) -> Optional[Dict]:
    try:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM site_media_policies WHERE site_key = ?",
                (site_key,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table: site_media_policies" not in str(exc):
            raise
        database.ensure_media_policy_schema()
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM site_media_policies WHERE site_key = ?",
                (site_key,),
            ).fetchone()
    return dict(row) if row else None


def _row_to_policy(row: Dict) -> Dict:
    result = {
        "preset_key": row["preset_key"],
        "source_order": json.loads(row["source_order_json"] or "[]"),
        "identity_mode": row["identity_mode"],
        "kind_order": json.loads(row["kind_order_json"] or "[]"),
        "quality_target": row["quality_target"],
        "quality_fallback": row["quality_fallback"],
        "min_height": int(row["min_height"]),
        "max_height": int(row["max_height"]),
        "allow_below_minimum": bool(row["allow_below_minimum"]),
        "allow_page_fallback": bool(row["allow_page_fallback"]),
        "play_selector": row["play_selector"] or "",
        "format_strategy": row["format_strategy"],
        "prefer_h264": bool(row["prefer_h264"]),
        "merge_output_format": row["merge_output_format"],
    }
    return _validate_policy(result)


def get_effective_policy(site_key: str) -> Dict:
    row = _policy_row(site_key)
    default_preset = _default_preset_key(site_key)
    if row and row.get("manual_override"):
        result = _row_to_policy(row)
        result.update(
            {
                "site_key": site_key,
                "version": int(row["strategy_version"]),
                "source_kind": "user",
                "source_label": "用户自定义",
                "manual_override": True,
                "updated_at": row["updated_at"],
            }
        )
        return result

    result = get_preset(default_preset)
    result.update(
        {
            "site_key": site_key,
            "version": int(row["strategy_version"]) if row else 1,
            "source_kind": "builtin",
            "source_label": (
                "站点内置"
                if default_preset != "generic"
                else "通用兜底"
            ),
            "manual_override": False,
            "updated_at": row["updated_at"] if row else "",
        }
    )
    return result


def update_policy(
    site_key: str,
    data: Dict,
    client_address: str = "",
    request_id: str = "",
) -> Dict:
    current = get_effective_policy(site_key)
    expected_version = data.get("expected_version")
    if (
        expected_version is not None
        and int(expected_version) != int(current["version"])
    ):
        raise ValueError("媒体选择策略已被其他页面修改，请刷新后重试")

    requested_preset = str(
        data.get("preset_key") or current["preset_key"]
    )
    merged = (
        get_preset(requested_preset)
        if requested_preset != current["preset_key"]
        else dict(current)
    )
    for key in (
        "preset_key",
        "source_order",
        "identity_mode",
        "kind_order",
        "quality_target",
        "quality_fallback",
        "min_height",
        "max_height",
        "allow_below_minimum",
        "allow_page_fallback",
        "play_selector",
        "format_strategy",
        "prefer_h264",
        "merge_output_format",
    ):
        if key in data:
            merged[key] = data[key]
    policy = _validate_policy(merged)
    now = _now()
    row = _policy_row(site_key)
    version = int(row["strategy_version"]) + 1 if row else 2
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO site_media_policies (
                site_key, preset_key, source_order_json, identity_mode,
                kind_order_json, quality_target, quality_fallback,
                min_height, max_height, allow_below_minimum,
                allow_page_fallback, play_selector,
                format_strategy, prefer_h264, merge_output_format,
                manual_override, strategy_version, status_note,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(site_key) DO UPDATE SET
                preset_key = excluded.preset_key,
                source_order_json = excluded.source_order_json,
                identity_mode = excluded.identity_mode,
                kind_order_json = excluded.kind_order_json,
                quality_target = excluded.quality_target,
                quality_fallback = excluded.quality_fallback,
                min_height = excluded.min_height,
                max_height = excluded.max_height,
                allow_below_minimum = excluded.allow_below_minimum,
                allow_page_fallback = excluded.allow_page_fallback,
                play_selector = excluded.play_selector,
                format_strategy = excluded.format_strategy,
                prefer_h264 = excluded.prefer_h264,
                merge_output_format = excluded.merge_output_format,
                manual_override = 1,
                strategy_version = excluded.strategy_version,
                status_note = excluded.status_note,
                updated_at = excluded.updated_at
            """,
            (
                site_key,
                policy["preset_key"],
                json.dumps(policy["source_order"]),
                policy["identity_mode"],
                json.dumps(policy["kind_order"]),
                policy["quality_target"],
                policy["quality_fallback"],
                policy["min_height"],
                policy["max_height"],
                1 if policy["allow_below_minimum"] else 0,
                1 if policy["allow_page_fallback"] else 0,
                policy["play_selector"],
                policy["format_strategy"],
                1 if policy["prefer_h264"] else 0,
                policy["merge_output_format"],
                version,
                str(data.get("note") or "").strip(),
                now,
                now,
            ),
        )
        connection.commit()
    updated = get_effective_policy(site_key)
    from local_video_site.downloads.policies.transport import record_audit

    record_audit(
        site_key,
        "media_policy.update",
        current,
        updated,
        str(data.get("note") or "").strip(),
        client_address,
        request_id,
    )
    return updated


def reset_policy(
    site_key: str,
    client_address: str = "",
    request_id: str = "",
) -> Dict:
    current = get_effective_policy(site_key)
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM site_media_policies WHERE site_key = ?",
            (site_key,),
        )
        connection.commit()
    updated = get_effective_policy(site_key)
    from local_video_site.downloads.policies.transport import record_audit

    record_audit(
        site_key,
        "media_policy.reset",
        current,
        updated,
        "恢复站点默认媒体选择策略",
        client_address,
        request_id,
    )
    return updated


def policy_for_task(
    site_key: str,
    requested_quality: str = "auto",
) -> Dict:
    policy = get_effective_policy(site_key)
    requested = str(requested_quality or "site").strip().lower()
    policy["requested_quality"] = requested
    policy["task_quality_override"] = requested not in {"", "site", "auto-default"}
    if not policy["task_quality_override"]:
        policy["quality_target"] = policy["quality_target"]
    else:
        policy["quality_target"] = requested
        if requested == "2160p":
            policy["max_height"] = 2160
            policy["quality_fallback"] = "nearest_below"
    return policy
