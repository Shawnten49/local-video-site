from __future__ import annotations

import json
import logging
import sys

from local_video_site import database
from local_video_site.media.assets.previews import generate_missing_previews
from local_video_site.media.playback.engine import (
    playback_cache_stats,
    prune_playback_cache,
)
from local_video_site.media.playback.normalize import normalize_playback_videos


def run_normalize(args, logger: logging.Logger) -> int:
    database.init_db()
    if args.workers != 1:
        print("播放重封装仅支持 --workers 1，已按单线程执行")
    if args.all and args.video_id is not None:
        print("--all 与 --video-id 不能同时使用", file=sys.stderr)
        return 2
    report = normalize_playback_videos(
        video_id=args.video_id,
        limit=args.limit,
        retry_failed=args.retry_failed,
        dry_run=args.dry_run,
        logger=logger,
    )
    write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


def run_cache(args, logger: logging.Logger) -> int:
    database.init_db()
    if args.prune:
        report = prune_playback_cache(
            max_bytes=args.max_size,
            max_age_days=args.max_age_days,
            dry_run=args.dry_run,
            logger=logger,
        )
    else:
        report = playback_cache_stats()
    write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def run_previews(args, logger: logging.Logger) -> int:
    database.init_db()
    if args.status:
        report = database.preview_status_counts()
    else:
        if args.workers != 1:
            print("预览生成仅支持 --workers 1，已按单线程执行")
        if args.all or args.refresh:
            database.reset_previews_pending()
        database.recover_interrupted_previews()
        report = generate_missing_previews(
            limit=args.limit,
            retry_failed=args.retry_failed,
            logger=logger,
        )
    write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not report.get("failed") else 1


def write_report(path, payload) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
