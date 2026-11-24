from __future__ import annotations

import argparse
from pathlib import Path


def parse_size(value: str) -> int:
    raw = str(value or "").strip().lower()
    units = {
        "kib": 1024,
        "ki": 1024,
        "kb": 1000,
        "k": 1024,
        "mib": 1024 ** 2,
        "mi": 1024 ** 2,
        "mb": 1000 ** 2,
        "m": 1024 ** 2,
        "gib": 1024 ** 3,
        "gi": 1024 ** 3,
        "gb": 1000 ** 3,
        "g": 1024 ** 3,
        "tib": 1024 ** 4,
        "ti": 1024 ** 4,
        "tb": 1000 ** 4,
        "t": 1024 ** 4,
    }
    for suffix in sorted(units, key=len, reverse=True):
        if raw.endswith(suffix):
            try:
                return int(float(raw[:-len(suffix)]) * units[suffix])
            except ValueError as exc:
                raise argparse.ArgumentTypeError(
                    "无效容量: {}".format(value)
                ) from exc
    try:
        return int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "无效容量: {}".format(value)
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local video library")
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scan video directories")
    scan.add_argument(
        "--roots",
        nargs="+",
        type=Path,
        help="Video directories (defaults to configured roots)",
    )
    scan.add_argument("--metadata", action="store_true", help="Generate metadata and thumbnails")
    scan.add_argument(
        "--wait",
        action="store_true",
        help="Wait for metadata and thumbnail generation",
    )
    scan.add_argument(
        "--status",
        action="store_true",
        help="Print the latest persisted scan status",
    )
    scan.add_argument("--workers", type=int, default=2)
    scan.add_argument("--limit", type=int)

    serve = subparsers.add_parser("serve", help="Start the local website")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--open", action="store_true", help="Open the website in the browser")

    users = subparsers.add_parser("users", help="Manage local video site accounts")
    users.add_argument(
        "action",
        choices=("bootstrap", "create", "reset-password", "list"),
    )
    users.add_argument("--username")
    users.add_argument(
        "--role",
        choices=("user", "admin"),
        default="user",
    )

    episode_titles = subparsers.add_parser(
        "backfill-episode-titles",
        help="Fill missing episode titles from the source site",
    )
    episode_titles.add_argument("--provider")

    classify = subparsers.add_parser("classify", help="Rebuild automatic video categories")
    classify.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild all category mappings (default action)",
    )
    classify.add_argument(
        "--clear",
        action="store_true",
        help="Clear categories and mappings before rebuilding",
    )
    classify.add_argument(
        "--fetch-xhamster",
        action="store_true",
        help="Fetch official categories for xHamster videos",
    )
    classify.add_argument(
        "--fetch-xvideos",
        action="store_true",
        help="Fetch official actors and tags for xVideos videos",
    )
    classify.add_argument(
        "--fetch-all-sites",
        action="store_true",
        help="Fetch supported site metadata for xHamster and xVideos",
    )
    classify.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing successful xHamster category cache entries",
    )
    classify.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed xHamster category fetches",
    )
    classify.add_argument(
        "--limit",
        type=int,
        help="Limit the number of xHamster pages fetched",
    )
    classify.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Fetch concurrency; current implementation uses one Chrome page",
    )
    classify.add_argument(
        "--report",
        type=Path,
        help="Write a JSON category rebuild report",
    )

    actor_avatars = subparsers.add_parser(
        "actor-avatars",
        help="Discover and download local actor avatar images",
    )
    actor_avatars.add_argument("--limit", type=int)
    actor_avatars.add_argument("--actor-id", type=int)
    actor_avatars.add_argument(
        "--refresh",
        action="store_true",
        help="Rediscover and redownload existing avatars",
    )
    actor_avatars.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry actors whose previous avatar download failed",
    )
    actor_avatars.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Accepted for compatibility; CDP downloads run on one page",
    )
    actor_avatars.add_argument(
        "--report",
        type=Path,
        help="Write a JSON avatar download report",
    )

    normalize = subparsers.add_parser(
        "playback-normalize",
        help="Remux incompatible videos in place without re-encoding",
    )
    normalize.add_argument(
        "--all",
        action="store_true",
        help="Process all candidates (default when no video ID is given)",
    )
    normalize.add_argument("--video-id", type=int)
    normalize.add_argument("--limit", type=int)
    normalize.add_argument("--retry-failed", action="store_true")
    normalize.add_argument("--dry-run", action="store_true")
    normalize.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Only one worker is supported because remuxing is I/O-bound",
    )
    normalize.add_argument("--report", type=Path)

    cache = subparsers.add_parser(
        "playback-cache",
        help="Inspect or FIFO-prune playback cache files",
    )
    cache.add_argument("--status", action="store_true")
    cache.add_argument("--prune", action="store_true")
    cache.add_argument("--max-size", type=parse_size)
    cache.add_argument("--max-age-days", type=int)
    cache.add_argument("--dry-run", action="store_true")
    cache.add_argument("--report", type=Path)

    thumbnails = subparsers.add_parser(
        "thumbnails",
        help="Repair missing video thumbnails",
    )
    thumbnails.add_argument(
        "--missing",
        action="store_true",
        help="Generate thumbnails only for videos without one (default action)",
    )

    previews = subparsers.add_parser(
        "previews",
        help="Generate short muted hover-preview clips",
    )
    previews.add_argument("--missing", action="store_true")
    previews.add_argument("--all", action="store_true")
    previews.add_argument("--refresh", action="store_true")
    previews.add_argument("--retry-failed", action="store_true")
    previews.add_argument("--status", action="store_true")
    previews.add_argument("--limit", type=int)
    previews.add_argument("--workers", type=int, default=1)
    previews.add_argument("--report", type=Path)

    subparsers.add_parser(
        "lan-ip",
        help="Print the private Wi-Fi IPv4 address",
    )

    backfill = subparsers.add_parser(
        "backfill-urls",
        help="Backfill original webpage URLs from download summaries",
    )
    backfill.add_argument(
        "--summary",
        type=Path,
        default=Path("logs") / "summary.jsonl",
        help="JSONL download summary path",
    )

    subparsers.add_parser(
        "delete-password",
        help="Change the destructive-action verification code interactively",
    )
    metadata_refresh = subparsers.add_parser(
        "metadata-refresh",
        help="Fetch actors and categories for downloaded source videos",
    )
    metadata_refresh.add_argument(
        "--provider",
        choices=("xhamster", "xvideos"),
    )
    metadata_refresh.add_argument("--video-id", type=int)
    metadata_refresh.add_argument("--url")
    metadata_refresh.add_argument("--limit", type=int)
    metadata_refresh.add_argument(
        "--missing-only",
        action="store_true",
        help="Fetch only videos without a successful metadata cache",
    )
    metadata_refresh.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore successful metadata cache entries",
    )
    metadata_refresh.add_argument("--retry-failed", action="store_true")
    metadata_refresh.add_argument("--report", type=Path)
    subparsers.add_parser(
        "repair-relations",
        help="Remove orphan video relationships using application code",
    )
    return parser
