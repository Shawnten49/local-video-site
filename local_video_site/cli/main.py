from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from local_video_site.core.logging import configure_logging
from .commands import maintenance, metadata, playback, scan, serve, users
from .parser import build_parser


def _handlers() -> Dict[str, Callable]:
    return {
        "scan": scan.run,
        "classify": metadata.run_classify,
        "metadata-refresh": metadata.run_refresh,
        "backfill-urls": metadata.run_backfill_urls,
        "backfill-episode-titles": metadata.run_backfill_episode_titles,
        "repair-relations": maintenance.run_repair_relations,
        "actor-avatars": maintenance.run_actor_avatars,
        "thumbnails": maintenance.run_thumbnails,
        "delete-password": maintenance.run_delete_password,
        "lan-ip": maintenance.run_lan_ip,
        "playback-normalize": playback.run_normalize,
        "playback-cache": playback.run_cache,
        "previews": playback.run_previews,
        "users": users.run,
        "serve": serve.run,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["serve"])
    command = args.command
    configure_logging()
    logger = logging.getLogger("local_video_site")
    logger.info(
        "Command started: %s",
        command,
        extra={"operation": "command.start"},
    )
    handler = _handlers().get(command)
    if handler is None:
        print("不支持的命令: {}".format(command))
        return 2
    result = handler(args, logger)
    logger.info(
        "Command stopped: %s",
        command,
        extra={"operation": "command.stop"},
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
