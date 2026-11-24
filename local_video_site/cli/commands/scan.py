from __future__ import annotations

import json
import logging

from local_video_site import database
from local_video_site.core import config
from local_video_site.ingestion.library_scan import (
    SCAN_RUNTIME,
    ScanAlreadyRunning,
    persisted_scan_status,
    process_pending_metadata,
    repair_missing_thumbnails,
    scan_library,
)


def run(args, logger: logging.Logger) -> int:
    database.init_db()
    if args.status:
        print(
            json.dumps(
                persisted_scan_status(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    roots = args.roots or config.configured_roots()
    if not SCAN_RUNTIME.try_start(roots):
        print("scan already running")
        return 1
    try:
        stats = scan_library(roots=args.roots)
    except ScanAlreadyRunning as exc:
        SCAN_RUNTIME.fail(str(exc))
        print("scan already running")
        return 1
    except Exception as exc:
        SCAN_RUNTIME.fail(str(exc))
        raise
    SCAN_RUNTIME.index_complete(stats)
    print(
        "discovered={} added={} changed={} missing={} categories_rebuilt={}".format(
            stats.discovered,
            stats.added,
            stats.changed,
            stats.missing,
            stats.categories_rebuilt,
        )
    )
    if args.metadata or args.wait:
        processed = process_pending_metadata(
            logger=logger,
            max_workers=args.workers,
            limit=args.limit,
        )
        print("metadata_processed={}".format(processed))
        if args.wait:
            repaired = repair_missing_thumbnails(logger=logger)
            print("thumbnails_repaired={}".format(repaired))
    SCAN_RUNTIME.resources_complete()
    return 0
