from __future__ import annotations

import getpass
import json
import logging
import sys

from local_video_site import database
from local_video_site.catalog.actors import sync_actor_avatars
from local_video_site.core.network import find_lan_ip
from local_video_site.ingestion.library_scan import repair_missing_thumbnails
from local_video_site.storage.deletion import set_delete_password


def run_actor_avatars(args, logger: logging.Logger) -> int:
    database.init_db()
    report = sync_actor_avatars(
        limit=args.limit,
        refresh=args.refresh,
        retry_failed=args.retry_failed,
        actor_id=args.actor_id,
        workers=args.workers,
        logger=logger,
    )
    write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


def run_repair_relations(args, logger: logging.Logger) -> int:
    database.init_db()
    print(json.dumps(database.repair_orphan_video_dependencies(), ensure_ascii=False))
    return 0


def run_thumbnails(args, logger: logging.Logger) -> int:
    database.init_db()
    repaired = repair_missing_thumbnails(logger=logger)
    print("thumbnails_repaired={}".format(repaired))
    return 0


def run_delete_password(args, logger: logging.Logger) -> int:
    first = getpass.getpass("新删除校验码: ")
    second = getpass.getpass("再次输入: ")
    if first != second:
        print("两次输入的校验码不一致", file=sys.stderr)
        return 1
    set_delete_password(first)
    logger.info(
        "Delete verification changed",
        extra={"operation": "delete_password.changed"},
    )
    print("删除校验码已更新")
    return 0


def run_lan_ip(args, logger: logging.Logger) -> int:
    address = find_lan_ip()
    if not address:
        print("未能找到私有 Wi-Fi IPv4 地址", file=sys.stderr)
        return 1
    print(address)
    return 0


def write_report(path, payload) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
