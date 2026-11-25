from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_slot


LOGGER = logging.getLogger(__name__)
TEXT_SUBTITLE_CODECS = {
    "ass",
    "ssa",
    "subrip",
    "srt",
    "webvtt",
    "mov_text",
}
BITMAP_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvb_subtitle",
    "xsub",
}
_VTT_TIMESTAMP_RE = re.compile(
    r"(?m)^((?:\d{2}:)?\d{2}:\d{2}\.\d{3})"
    r"(\s*-->\s*)"
    r"((?:\d{2}:)?\d{2}:\d{2}\.\d{3})"
)
_BILINGUAL_MARKERS = (
    "中英",
    "中上英下",
    "中下英上",
    "简英",
    "繁英",
    "中英双",
    "双语",
)
_CHINESE_MARKERS = (
    "简体",
    "繁体",
    "中文",
    "官方字幕",
)


class SubtitleExtractionError(RuntimeError):
    pass


def _clean_title(title: str) -> str:
    return " ".join(str(title or "").split("￡", 1)[0].split()).strip()


def _language_code(language: str, title: str) -> str:
    language = str(language or "").strip().lower()
    title = str(title or "")
    if language in {"chi", "zho", "zh"}:
        if "繁" in title or "cht" in title.casefold():
            return "zh-Hant"
        return "zh-Hans"
    if language in {"eng", "en"}:
        return "en"
    if language in {"jpn", "ja"}:
        return "ja"
    if language in {"kor", "ko"}:
        return "ko"
    return language or "und"


def probe_subtitle_streams(path: Path) -> List[Dict]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return []
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                (
                    "stream=index,codec_name:"
                    "stream_tags=language,title:"
                    "stream_disposition=default"
                ),
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        return []
    result = []
    for stream in payload.get("streams") or []:
        tags = stream.get("tags") or {}
        result.append({
            "index": int(stream["index"]),
            "codec": str(stream.get("codec_name") or "").lower(),
            "language": str(tags.get("language") or ""),
            "title": _clean_title(tags.get("title") or ""),
            "default": bool(
                (stream.get("disposition") or {}).get("default")
            ),
        })
    return result


def _asset_extension(codec: str) -> str:
    if codec in TEXT_SUBTITLE_CODECS:
        return "vtt"
    if codec in BITMAP_SUBTITLE_CODECS:
        return "sup"
    return ""


def subtitle_priority(stream: Dict) -> int:
    title = str(stream.get("title") or "")
    language = str(stream.get("language") or "").lower()
    if any(marker in title for marker in _BILINGUAL_MARKERS):
        return 0
    if (
        any(marker in title for marker in _CHINESE_MARKERS)
        or language in {"chi", "zho", "zh"}
    ):
        return 1
    return 2


def preferred_subtitle_stream(streams: List[Dict]) -> Optional[Dict]:
    text_streams = [
        stream
        for stream in streams
        if str(stream.get("codec") or "").lower() in TEXT_SUBTITLE_CODECS
    ]
    if not text_streams:
        return None
    return min(
        enumerate(text_streams),
        key=lambda item: (
            subtitle_priority(item[1]),
            item[0],
        ),
    )[1]


def subtitle_stream_options(path: Path) -> Dict:
    streams = probe_subtitle_streams(path)
    preferred = preferred_subtitle_stream(streams)
    preferred_index = (
        int(preferred["index"])
        if preferred is not None
        else None
    )
    options = []
    for ordinal, stream in enumerate(streams, start=1):
        codec = str(stream.get("codec") or "").lower()
        text = codec in TEXT_SUBTITLE_CODECS
        image = codec in BITMAP_SUBTITLE_CODECS
        options.append({
            "ordinal": ordinal,
            "source_index": int(stream["index"]),
            "codec": codec,
            "language": str(stream.get("language") or ""),
            "title": str(stream.get("title") or ""),
            "default": bool(stream.get("default")),
            "text": text,
            "image": image,
            "extractable": text,
            "recommended": text,
            "recommended_default": (
                text and int(stream["index"]) == preferred_index
            ),
        })
    return {
        "streams": options,
        "recommended_stream_indexes": [
            item["source_index"]
            for item in options
            if item["recommended"]
        ],
        "default_stream_index": preferred_index,
    }


def clear_subtitle_assets(
    video_id: int,
    retain_keys: Optional[set] = None,
) -> int:
    retained = retain_keys or set()
    removed = 0
    for existing in database.list_video_subtitle_assets(int(video_id)):
        key = (
            int(existing["source_stream_index"]),
            str(existing["extension"]).lower(),
        )
        if key in retained:
            continue
        path = Path(str(existing.get("storage_path") or ""))
        try:
            path.resolve().relative_to(config.SUBTITLE_DIR.resolve())
        except (OSError, ValueError):
            pass
        else:
            path.unlink(missing_ok=True)
        database.delete_subtitle_asset(int(existing["id"]))
        removed += 1
    return removed


def _full_vtt_timestamp(value: str) -> str:
    return value if value.count(":") == 2 else "00:" + value


def normalize_webvtt_timestamps(path: Path) -> None:
    """Use HH:MM:SS.mmm because Movi's parser rejects shorter VTT timestamps."""

    text = path.read_text(encoding="utf-8")
    normalized = _VTT_TIMESTAMP_RE.sub(
        lambda match: (
            _full_vtt_timestamp(match.group(1))
            + match.group(2)
            + _full_vtt_timestamp(match.group(3))
        ),
        text,
    )
    if normalized != text:
        path.write_text(normalized, encoding="utf-8")


def _extract_one(
    ffmpeg: str,
    source: Path,
    stream: Dict,
    destination: Path,
) -> None:
    codec = str(stream.get("codec") or "").lower()
    extension = _asset_extension(codec)
    if not extension:
        raise SubtitleExtractionError(
            "不支持提取的字幕编码: {}".format(codec or "unknown")
        )
    temporary = destination.with_name(
        ".{}.part{}".format(destination.name, destination.suffix)
    )
    temporary.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:{}".format(int(stream["index"])),
    ]
    if extension == "vtt":
        command.extend(["-c:s", "webvtt", "-f", "webvtt"])
    else:
        command.extend(["-c:s", "copy", "-f", "sup"])
    command.extend(["-y", str(temporary)])
    with ffmpeg_slot("subtitle_extract"):
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise SubtitleExtractionError(
            (completed.stderr or "FFmpeg 字幕提取失败")[-1000:]
        )
    if extension == "vtt":
        normalize_webvtt_timestamps(temporary)
    temporary.replace(destination)


def extract_subtitle_assets(
    video_id: int,
    source_path: Path,
    logger: Optional[logging.Logger] = None,
    selected_stream_indexes: Optional[List[int]] = None,
    default_stream_index: Optional[int] = None,
    replace_existing: bool = False,
) -> Dict:
    logger = logger or LOGGER
    source = Path(source_path)
    if not source.is_file():
        raise SubtitleExtractionError("字幕来源文件不存在")
    streams = probe_subtitle_streams(source)
    if not streams:
        return {"assets": [], "errors": [], "source_streams": 0}
    if selected_stream_indexes is not None:
        selected = {
            int(value)
            for value in selected_stream_indexes
        }
        streams = [
            stream
            for stream in streams
            if int(stream["index"]) in selected
        ]
        if not streams:
            return {"assets": [], "errors": [], "source_streams": 0}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SubtitleExtractionError("ffmpeg is not installed")

    target_dir = config.SUBTITLE_DIR / str(int(video_id))
    target_dir.mkdir(parents=True, exist_ok=True)
    text_streams = [
        stream
        for stream in streams
        if stream.get("codec") in TEXT_SUBTITLE_CODECS
    ]
    preferred = (
        next(
            (
                stream
                for stream in streams
                if default_stream_index is not None
                and int(stream["index"]) == int(default_stream_index)
            ),
            None,
        )
        or preferred_subtitle_stream(streams)
    )
    preferred_index = preferred.get("index") if preferred else None
    source_stat = source.stat()
    assets = []
    errors = []
    for stream in streams:
        extension = _asset_extension(stream["codec"])
        if not extension:
            errors.append({
                "index": stream["index"],
                "codec": stream["codec"],
                "error": "不支持的字幕编码",
            })
            continue
        language = _language_code(
            stream.get("language", ""),
            stream.get("title", ""),
        )
        destination = target_dir / "stream-{:03d}-{}.{}".format(
            int(stream["index"]),
            language,
            extension,
        )
        try:
            _extract_one(ffmpeg, source, stream, destination)
            asset = database.upsert_subtitle_asset(
                video_id=int(video_id),
                source_stream_index=int(stream["index"]),
                language=language,
                title=stream.get("title", ""),
                extension=extension,
                storage_path=destination,
                source_size=source_stat.st_size,
                source_mtime_ns=source_stat.st_mtime_ns,
                default_track=stream["index"] == preferred_index,
            )
            assets.append(asset)
        except (OSError, subprocess.SubprocessError, SubtitleExtractionError) as exc:
            destination.unlink(missing_ok=True)
            errors.append({
                "index": stream["index"],
                "codec": stream["codec"],
                "error": str(exc),
            })
            logger.warning(
                "Subtitle extraction failed video_id=%s stream=%s codec=%s error=%s",
                video_id,
                stream["index"],
                stream["codec"],
                exc,
            )
    if text_streams and not any(
        item.get("extension") == "vtt" for item in assets
    ):
        raise SubtitleExtractionError(
            "文本字幕提取全部失败: {}".format(errors[:3])
        )
    if replace_existing:
        clear_subtitle_assets(
            int(video_id),
            retain_keys={
                (
                    int(item["source_stream_index"]),
                    str(item["extension"]).lower(),
                )
                for item in assets
            },
        )
    return {
        "assets": assets,
        "errors": errors,
        "source_streams": len(streams),
    }
