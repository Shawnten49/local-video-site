from __future__ import annotations

import json
import logging

from local_video_site import database
from local_video_site.catalog.categories import (
    fetch_xhamster_categories,
    rebuild_actor_index,
    rebuild_category_index,
    refresh_site_metadata,
)
from local_video_site.catalog.original_urls import backfill_original_urls
from local_video_site.episodes.batches import backfill_episode_titles


def run_classify(args, logger: logging.Logger) -> int:
    database.init_db()
    report = {}
    if args.fetch_xhamster:
        if args.workers not in (1, 2):
            print("分类抓取 --workers 仅支持 1 或 2，已限制为 2")
        report = fetch_xhamster_categories(
            limit=args.limit,
            refresh=args.refresh_cache,
            retry_failed=args.retry_failed,
            workers=args.workers,
            logger=logger,
        )
    if args.fetch_xvideos:
        report.setdefault("sites", {})["xvideos"] = refresh_site_metadata(
            provider="xvideos",
            limit=args.limit,
            refresh=args.refresh_cache,
            retry_failed=args.retry_failed,
            logger=logger,
        )
    if args.fetch_all_sites:
        report.setdefault("sites", {})["all"] = refresh_site_metadata(
            limit=args.limit,
            refresh=args.refresh_cache,
            retry_failed=args.retry_failed,
            logger=logger,
        )
    category_count = rebuild_category_index(clear=args.clear)
    actor_count = rebuild_actor_index(clear=args.clear)
    report["categories"] = category_count
    report["actors"] = actor_count
    _write_report(args.report, report)
    if report:
        print(json.dumps(report, ensure_ascii=False))
    print("categories={}".format(category_count))
    print("actors={}".format(actor_count))
    return 0


def run_refresh(args, logger: logging.Logger) -> int:
    database.init_db()
    report = refresh_site_metadata(
        provider=args.provider,
        video_id=args.video_id,
        url=args.url,
        limit=args.limit,
        refresh=args.refresh,
        retry_failed=args.retry_failed,
        logger=logger,
    )
    _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


def run_backfill_urls(args, logger: logging.Logger) -> int:
    updated = backfill_original_urls(
        summary_path=args.summary,
        logger=logger,
    )
    print("original_urls_updated={}".format(updated))
    return 0


def run_backfill_episode_titles(args, logger: logging.Logger) -> int:
    database.init_db()
    report = backfill_episode_titles(provider=args.provider, logger=logger)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not report["failed"] else 1


def _write_report(path, payload) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
