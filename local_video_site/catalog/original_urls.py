from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.categories import rebuild_actor_index, rebuild_category_index


LOGGER = logging.getLogger(__name__)
DEFAULT_SUMMARY_PATH = config.PROJECT_ROOT / "logs" / "summary.jsonl"


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_download_url_mapping(summary_path: Path) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    latest: Dict[str, str] = {}
    if not summary_path.is_file():
        return mapping

    with summary_path.open("r", encoding="utf-8") as stream:
        for raw_line in stream:
            try:
                record = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if record.get("status") not in {"success", "skipped"}:
                continue
            input_url = str(record.get("input_url") or "").strip()
            output_path = str(record.get("output_path") or "").strip()
            if not output_path or not _valid_http_url(input_url):
                continue
            normalized_path = str(Path(output_path).expanduser().resolve())
            timestamp = str(record.get("finished_at") or "")
            if normalized_path in latest and timestamp < latest[normalized_path]:
                continue
            mapping[normalized_path] = input_url
            latest[normalized_path] = timestamp
    return mapping


def backfill_original_urls(
    summary_path: Optional[Path] = None,
    logger: Optional[logging.Logger] = None,
) -> int:
    logger = logger or LOGGER
    path = (summary_path or DEFAULT_SUMMARY_PATH).expanduser()
    mapping = load_download_url_mapping(path)
    database.init_db()
    updated = database.update_original_urls(mapping)
    logger.info(
        "Original URL backfill: mapped=%s updated=%s summary=%s",
        len(mapping),
        updated,
        path,
    )
    return updated


def find_video_root(path: Path) -> Optional[Path]:
    resolved = path.expanduser().resolve()
    for root in config.configured_roots():
        if resolved == root or root in resolved.parents:
            return root
    return None


def register_downloaded_video(
    output_path: Path,
    title: str,
    original_url: str,
    download_source_url: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> bool:
    logger = logger or LOGGER
    if not _valid_http_url(original_url):
        logger.warning("Not registering download with invalid source URL")
        return False
    path = output_path.expanduser().resolve()
    root = find_video_root(path)
    if root is None:
        logger.warning(
            "Downloaded file is outside configured roots and was not indexed: %s",
            path,
        )
        return False
    try:
        stat = path.stat()
        database.init_db()
        status = database.upsert_video(
            path=path,
            title=title,
            directory=root.name,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            original_url=original_url,
            download_source_url=download_source_url or original_url,
        )
        video = database.get_video_by_path(path)
        if video:
            try:
                from local_video_site.media.playback.normalize import normalize_video

                result = normalize_video(video, logger=logger)
                if result["status"] == "ready":
                    logger.info(
                        "Normalized downloaded video for direct playback: %s",
                        path,
                    )
            except Exception:
                logger.exception(
                    "Downloaded video registration succeeded, but playback "
                    "normalization failed: %s",
                    path,
                )
        if status in {"added", "changed"}:
            rebuild_category_index()
            rebuild_actor_index()
    except Exception:
        logger.exception("Could not register downloaded video: %s", path)
        return False
    logger.info(
        "Registered downloaded video: status=%s path=%s "
        "metadata_source=%s download_source=%s",
        status,
        path,
        original_url,
        download_source_url or original_url,
    )
    return True
