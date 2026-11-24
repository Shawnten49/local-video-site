from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from local_video_site.core import config
from local_video_site.media.info import probe_video
from local_video_site.media.quality import normalise_quality_label
from local_video_site.catalog.original_urls import register_downloaded_video
from local_video_site.integrations.profiles import resolve_site_profile

from .chrome import (
    DEFAULT_CHROME_PATH,
    ChromeLaunchError,
    launch_chrome,
)
from .browser_hls import BrowserHlsDownloader, HlsRateLimitPause
from .ytdlp import DEFAULT_FORMAT_SPEC, Downloader
from .direct_range import (
    DirectRangeDownloader,
    RangeManifest,
    RangeNotSupported,
)
from .naming import choose_best_title, safe_filename_component, truncate_at_hash
from .page_guard import PopupPageGuard
from .resolution import (
    MediaCatalog,
    MediaPolicy,
    resolve_quality_target,
    format_selector_for_candidate,
    preferred_quality_height as _preferred_height,
    select_media_candidate as _select_media_candidate,
    yt_dlp_format_selector,
)
from .recovery import (
    MediaRefreshRequired,
    extract_media_expiry,
    media_identity,
    partial_size,
)
from .sniffer import (
    MediaCandidate,
    MediaSniffer,
    media_candidate_key,
    redact_url,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "download"
DEFAULT_PROFILE = PROJECT_ROOT / ".chrome-cdp-profile"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".ts", ".webm", ".mkv", ".mov", ".flv")
RATE_LIMIT_PAUSE_EXIT_CODE = 134


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use an isolated Chrome/CDP session to discover and download a web video."
    )
    parser.add_argument("url", help="First-episode page URL")
    parser.add_argument(
        "--metadata-url",
        help="Canonical data source page for title, actors and categories",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--chrome-path", default=DEFAULT_CHROME_PATH)
    parser.add_argument(
        "--reuse-cdp",
        action="store_true",
        help="Reuse an existing CDP endpoint on this port instead of failing",
    )
    parser.add_argument(
        "--close-browser",
        action="store_true",
        help="Close the CDP-connected Chrome after the download",
    )
    parser.add_argument(
        "--keep-browser-open",
        action="store_true",
        help="Leave the Chrome launched by this run open for login or inspection",
    )
    parser.add_argument("--series-name", help="Override the extracted series title")
    parser.add_argument("--media-url", help="Skip media discovery and use this media URL")
    parser.add_argument(
        "--no-library-registration",
        action="store_true",
        help="Download only and do not register the output in the video library",
    )
    parser.add_argument(
        "--format",
        default=DEFAULT_FORMAT_SPEC,
        help=(
            "yt-dlp format selector (default task limit 1080p; "
            "explicit 2160p quality can use 4K)"
        ),
    )
    parser.add_argument(
        "--quality",
        default="site",
        choices=(
            "site",
            "auto",
            "2160p",
            "1080p",
            "720p",
            "480p",
            "360p",
        ),
        help="Preferred downloadable quality",
    )
    parser.add_argument(
        "--media-policy-file",
        type=Path,
        help="Task-level JSON snapshot for site media selection",
    )
    parser.add_argument(
        "--probe-media",
        action="store_true",
        help="Discover and rank media candidates without downloading them",
    )
    parser.add_argument(
        "--allow-below-minimum-quality",
        action="store_true",
        help=(
            "In auto mode, use the best available HLS variant when every "
            "variant is below the normal 360p minimum"
        ),
    )
    parser.add_argument(
        "--transport-mode",
        default="auto",
        choices=("auto", "qualify", "hls", "direct-range", "yt-dlp"),
        help="Site-level transport policy decision",
    )
    parser.add_argument(
        "--tls-backend",
        default="auto",
        choices=("auto", "httpx", "curl_cffi"),
    )
    parser.add_argument(
        "--range-initial-connections",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--range-max-connections",
        type=int,
        default=4,
    )
    parser.add_argument(
        "--proxy",
        help="HTTP/SOCKS proxy for yt-dlp fallback downloads",
    )
    parser.add_argument(
        "--impersonate",
        default="chrome",
        help="yt-dlp browser TLS impersonation target",
    )
    parser.add_argument("--play-selector", help="CSS selector for the page play button")
    parser.add_argument("--no-auto-play", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=12.0)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--extract-retries", type=int, default=2)
    parser.add_argument(
        "--download-attempts",
        type=int,
        default=3,
        help="Breakpoint-resume attempts before one fresh restart",
    )
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--fragment-retries", type=int, default=5)
    parser.add_argument("--retry-backoff", type=float, default=2.0)
    parser.add_argument(
        "--allow-page-fallback",
        action="store_true",
        help="Let yt-dlp extract the page URL when network sniffing finds nothing",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def _detect_download_proxy(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    for port in (1087, 1088):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return "http://127.0.0.1:{}".format(port)
        except OSError:
            continue
    return None


def _build_logger(log_dir: Path, verbose: bool) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = logging.getLogger("local_video_site.downloader")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    file_handler = logging.FileHandler(
        log_dir / "download-{}.log".format(stamp),
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    )
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(console)
    return logger


def _write_summary(log_dir: Path, record: Dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with (log_dir / "summary.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    with (log_dir / "summary.log").open("a", encoding="utf-8") as stream:
        fields = [
            record.get("finished_at", ""),
            record.get("status", ""),
            record.get("series_name", ""),
            "attempt={}".format(record.get("attempt", 0)),
            "size={}".format(_format_bytes(record.get("size", 0))),
            "elapsed={:.1f}s".format(record.get("elapsed_seconds", 0)),
            record.get("output_path", ""),
        ]
        stream.write(" | ".join(str(field) for field in fields))
        if record.get("error"):
            stream.write(" | error={}".format(record["error"]))
        stream.write("\n")


def _format_bytes(value: float) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} TiB".format(size)


def find_existing_output(output_dir: Path, basename: str) -> Optional[Path]:
    """Return an existing completed video with the requested base name."""

    directory = output_dir.expanduser().resolve()
    if not directory.is_dir():
        return None

    for extension in VIDEO_EXTENSIONS:
        candidate = directory / (basename + extension)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _fallback_file_candidates(
    selected: Optional[MediaCandidate],
    candidates: List[MediaCandidate],
) -> List[MediaCandidate]:
    if selected is None or selected.kind != "file":
        return []
    selected_height = selected.quality_height
    selected_key = media_candidate_key(selected.url)
    result = []
    for candidate in candidates:
        if (
            candidate is selected
            or candidate.kind != "file"
            or not candidate.matches_page_video
            or candidate.quality_height < 360
            or media_candidate_key(candidate.url) == selected_key
        ):
            continue
        if selected_height and candidate.quality_height > selected_height:
            continue
        result.append(candidate)
    return sorted(
        result,
        key=lambda candidate: (
            min(candidate.quality_height, 1080),
            candidate.score,
        ),
        reverse=True,
    )


def _direct_file_fallback_candidates(
    candidates: List[MediaCandidate],
    preferred_quality: str,
    policy: Optional[MediaPolicy] = None,
) -> List[MediaCandidate]:
    """Find page-matched direct files when the preferred manifest is unusable."""

    pool = [
        candidate
        for candidate in candidates
        if candidate.kind == "file"
        and candidate.matches_page_video
    ]
    active = policy or MediaPolicy()
    catalog = MediaCatalog(pool, policy=active)
    ordered = catalog.plan(preferred_quality).ordered
    if not ordered:
        return []
    selected = ordered[0]
    remaining = [
        candidate for candidate in ordered[1:]
        if (
            not selected.quality_height
            or candidate.quality_height <= selected.quality_height
        )
    ]
    return [selected] + _fallback_file_candidates(selected, remaining)


def _resume_identity_for_output(
    output_dir: Path,
    basename: str,
) -> Optional[str]:
    directory = output_dir.expanduser().resolve()
    if not directory.is_dir():
        return None
    prefix = basename + "."
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    for path in entries:
        if (
            not path.is_file()
            or not path.name.startswith(prefix)
            or not path.name.endswith(".range.json")
        ):
            continue
        extension = path.name[len(basename):-len(".range.json")]
        part_path = directory / (basename + extension + ".part")
        if not part_path.is_file():
            continue
        manifest = RangeManifest.load(path)
        if manifest is not None:
            return manifest.identity
    return None


def _candidate_identity_set(candidate: MediaCandidate) -> set:
    return {
        media_identity(
            candidate.url,
            candidate.quality_label,
            candidate.width or None,
            candidate.height or None,
        ),
        media_identity(candidate.url, candidate.quality_label),
        media_identity(candidate.url),
    }


def _select_identity_compatible_candidate(
    candidates: List[MediaCandidate],
    previous: MediaCandidate,
) -> Optional[MediaCandidate]:
    previous_identity = media_identity(
        previous.url,
        previous.quality_label,
        previous.width or None,
        previous.height or None,
    )
    for candidate in candidates:
        if previous_identity in _candidate_identity_set(candidate):
            return candidate
    return None


def _order_direct_candidates_for_resume(
    selected: MediaCandidate,
    candidates: List[MediaCandidate],
    output_dir: Path,
    basename: str,
) -> List[MediaCandidate]:
    ordered = [selected] + _fallback_file_candidates(selected, candidates)
    manifest_identity = _resume_identity_for_output(output_dir, basename)
    if not manifest_identity:
        return ordered
    compatible = []
    remaining = []
    for candidate in ordered:
        identities = _candidate_identity_set(candidate)
        if manifest_identity in identities:
            compatible.append(candidate)
        else:
            remaining.append(candidate)
    return compatible + remaining


def is_ad_like_existing_output(
    path: Path,
) -> bool:
    return is_advertisement_size(path.stat().st_size)


def is_advertisement_size(size: int) -> bool:
    return (
        int(size or 0)
        < config.sniffer_minimum_video_bytes()
    )


def _page_titles(page) -> List[str]:
    titles: List[str] = []
    try:
        titles.append(page.title())
    except PlaywrightError:
        pass
    try:
        values = page.evaluate(
            """
            () => [
              document.querySelector('meta[property="og:title"]')?.content,
              document.querySelector('meta[name="twitter:title"]')?.content,
              document.querySelector('h1')?.innerText,
              document.title
            ].filter(Boolean)
            """
        )
        titles.extend(values or [])
    except PlaywrightError:
        pass
    return titles


def _source_page_title(context, url: str, logger: logging.Logger) -> str:
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return choose_best_title(_page_titles(page), source_url=url) or ""
    except PlaywrightError as exc:
        logger.warning(
            "Could not read data source title from %s: %s",
            redact_url(url),
            exc,
        )
        return ""
    finally:
        try:
            page.close(run_before_unload=False)
        except Exception:
            pass


def _select_series_title(
    explicit_title: str,
    source_title: str,
    page_titles: List[str],
    download_url: str,
    metadata_url: str,
) -> str:
    if explicit_title and explicit_title.strip():
        selected = truncate_at_hash(explicit_title)
    else:
        selected = source_title if metadata_url != download_url else ""
        if not selected:
            selected = choose_best_title(
                page_titles,
                source_url=download_url,
            )
    return safe_filename_component(selected) or "video"


def _merge_direct_audio_stream(
    video_result: Dict,
    candidate: MediaCandidate,
    args: argparse.Namespace,
    logger: logging.Logger,
    headers: Dict[str, str],
    cookies: List[Dict],
    proxy: Optional[str],
) -> Dict:
    audio_url = str(candidate.audio_url or "").strip()
    if not audio_url:
        return video_result
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("4K 分离流需要 ffmpeg，但系统中未找到")
    final_path = Path(video_result["path"])
    video_only = final_path.with_name(
        final_path.stem + ".video-only" + final_path.suffix
    )
    audio_basename = final_path.stem + ".audio-source"
    audio_range = DirectRangeDownloader(
        output_dir=args.output,
        logger=logger,
        proxy=proxy,
        refresh_target=None,
        initial_connections=args.range_initial_connections,
        max_connections=args.range_max_connections,
        tls_backend=args.tls_backend,
    )
    audio_result = audio_range.download(
        target=audio_url,
        basename=audio_basename,
        headers=headers,
        cookies=cookies,
        media_quality="audio",
        expires_at=extract_media_expiry(audio_url),
    )
    audio_path = Path(audio_result["path"])
    final_path.replace(video_only)
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video_only),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(final_path),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                "4K 音视频合并失败: {}".format(detail[-2000:])
            )
    except Exception:
        final_path.unlink(missing_ok=True)
        video_only.replace(final_path)
        raise
    video_only.unlink(missing_ok=True)
    audio_path.unlink(missing_ok=True)
    logger.info(
        "Merged Bilibili separate streams: video=%s audio=%s output=%s",
        redact_url(candidate.url),
        redact_url(audio_url),
        final_path,
    )
    info = dict(video_result.get("info") or {})
    info.update({
        "title": info.get("title") or final_path.stem,
        "quality": candidate.quality_label,
        "width": candidate.width or None,
        "height": candidate.height or None,
        "requested_downloads": [{"filepath": str(final_path)}],
    })
    return {
        "path": final_path,
        "info": info,
        "attempt": video_result.get("attempt", 1),
        "size": final_path.stat().st_size,
    }


def _resolve_bilibili_season_page(
    page,
    source_url: str,
    logger: logging.Logger,
) -> str:
    parsed = urlsplit(source_url)
    host = (parsed.hostname or "").lower()
    if not (
        (host == "bilibili.com" or host.endswith(".bilibili.com"))
        and re.match(r"^/bangumi/play/ss\d+", parsed.path)
    ):
        return source_url
    try:
        page.goto(
            source_url,
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(2000)
        episode_url = page.evaluate(
            """
            () => {
              const link = document.querySelector(
                '[class*="PaginatedEpList_root"] '
                + 'a[class*="EpisodeVirtualList_numberItem"]'
                + '[href*="/bangumi/play/ep"]'
              );
              return link ? link.href : '';
            }
            """
        )
    except PlaywrightError as exc:
        logger.warning(
            "Could not resolve Bilibili season page; using original URL: %s",
            exc,
        )
        return source_url
    resolved = str(episode_url or "").strip()
    if not resolved:
        logger.warning(
            "Bilibili season page did not expose a first episode URL"
        )
        try:
            from local_video_site.episodes.catalog import discover_episode_catalog

            catalog = discover_episode_catalog(source_url)
            resolved = (
                catalog.episodes[0].source_url
                if catalog.episodes
                else ""
            )
        except Exception as exc:
            logger.warning(
                "Could not resolve Bilibili season through API: %s",
                exc,
            )
            resolved = ""
    if not resolved:
        return source_url
    logger.info(
        "Resolved Bilibili season to first episode page: %s",
        redact_url(resolved),
    )
    return resolved


def _normalize_transport_stream_if_needed(
    path: Path,
    logger: logging.Logger,
) -> Path:
    if path.suffix.lower() not in {".ts", ".mts", ".m2ts"}:
        return path
    try:
        from local_video_site import database
        from local_video_site.core import config as site_config
        from local_video_site.media.playback.normalize import normalize_video

        if not site_config.normalize_ts_on_download():
            return path
        video = database.get_video_by_path(path)
        if video is None:
            return path
        result = normalize_video(video, logger=logger)
        if result.get("status") == "ready":
            return Path(result["path"])
    except Exception as exc:
        logger.warning(
            "Downloaded TS normalization failed; keeping source and "
            "playback-cache fallback path=%s error=%s",
            path,
            exc,
        )
    return path


def _log_candidates(candidates: List[MediaCandidate], logger: logging.Logger) -> None:
    if not candidates:
        logger.warning("No downloadable media candidates were found")
        return
    logger.info("Media candidates (%s):", len(candidates))
    for index, candidate in enumerate(candidates[:10], start=1):
        logger.info(
            "  %s. score=%s quality=%s resolution=%s kind=%s "
            "evidence=%s url=%s",
            index,
            candidate.score,
            candidate.quality_label,
            "{}x{}".format(candidate.width, candidate.height)
            if candidate.width and candidate.height
            else "unknown",
            candidate.kind,
            candidate.evidence,
            redact_url(candidate.url),
        )


def _discover_media(
    page,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> tuple:
    sniffer = MediaSniffer(page, logger=logger)
    last_error = None
    for attempt in range(1, max(1, args.extract_retries) + 1):
        logger.info(
            "Opening first-episode page (extraction attempt %s/%s): %s",
            attempt,
            args.extract_retries,
            args.url,
        )
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
            logger.info("Media discovery started")
            candidates = sniffer.discover(
                wait_seconds=args.wait_seconds,
                settle_seconds=args.settle_seconds,
                play_selector=(
                    args.play_selector
                    or args.media_policy.play_selector
                ),
                auto_play=not args.no_auto_play,
            )
            _log_candidates(candidates, logger)
            if candidates:
                catalog = MediaCatalog(
                    candidates,
                    policy=args.media_policy,
                )
                plan = catalog.plan(args.quality)
                selected = plan.primary
                args.selection_reason = plan.reason
                if selected:
                    logger.info(
                        "Selected media candidate: quality=%s "
                        "resolution=%s score=%s available=%s "
                        "source=%s reason=%s",
                        selected.quality_label,
                        "{}x{}".format(selected.width, selected.height)
                        if selected.width and selected.height
                        else "unknown",
                        selected.score,
                        ",".join(catalog.available_qualities) or "unknown",
                        selected.source,
                        plan.reason,
                    )
                return selected, candidates, page
        except PlaywrightTimeoutError as exc:
            last_error = exc
            logger.warning("Page extraction attempt %s timed out", attempt)
        except PlaywrightError as exc:
            last_error = exc
            logger.warning("Page extraction attempt %s failed: %s", attempt, exc)

        if attempt < args.extract_retries:
            logger.info("Reloading page to retry media discovery")
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
            except PlaywrightError:
                pass

    if last_error:
        logger.warning("Media discovery finished with error: %s", last_error)
    return None, sniffer.candidates, page


def _refresh_media_candidate(
    context,
    args: argparse.Namespace,
    logger: logging.Logger,
    previous_candidate: MediaCandidate,
) -> tuple:
    refresh_page = context.new_page()
    popup_guard = PopupPageGuard(context, refresh_page, logger)
    try:
        refreshed, candidates, _ = _discover_media(
            refresh_page,
            args,
            logger,
        )
        if refreshed is None:
            raise RuntimeError("媒体地址刷新后未发现新候选")
        old_identity = media_identity(
            previous_candidate.url,
            previous_candidate.quality_label,
            previous_candidate.width,
            previous_candidate.height,
        )
        compatible = _select_identity_compatible_candidate(
            candidates,
            previous_candidate,
        )
        if compatible is not None and compatible is not refreshed:
            refreshed = compatible
            logger.info(
                "Refresh selected resume-compatible candidate "
                "quality=%s resolution=%s",
                refreshed.quality_label,
                "{}x{}".format(refreshed.width, refreshed.height)
                if refreshed.width and refreshed.height
                else "unknown",
            )
        new_identity = media_identity(
            refreshed.url,
            refreshed.quality_label,
            refreshed.width,
            refreshed.height,
        )
        if old_identity != new_identity:
            raise RuntimeError(
                "媒体地址刷新后内容身份不一致；已保留原断点"
            )
        logger.info(
            "Media resume validation: identity_match=true "
            "quality=%s resolution=%s candidates=%s",
            refreshed.quality_label,
            "{}x{}".format(refreshed.width, refreshed.height)
            if refreshed.width and refreshed.height
            else "unknown",
            len(candidates),
        )
        return refreshed, dict(refreshed.headers)
    finally:
        popup_guard.stop()
        try:
            refresh_page.close(run_before_unload=False)
        except Exception:
            pass


def _validate_resume_range(
    page,
    target: str,
    headers: Dict[str, str],
    resume_bytes: int,
    logger: logging.Logger,
) -> None:
    if resume_bytes <= 0:
        return
    try:
        result = page.evaluate(
            """
            async ({url, headers, offset}) => {
              const response = await fetch(url, {
                headers: {...headers, Range: `bytes=${offset}-`}
              });
              return {
                status: response.status,
                contentRange: response.headers.get('content-range') || '',
                etag: response.headers.get('etag') || ''
              };
            }
            """,
            {
                "url": target,
                "headers": headers,
                "offset": int(resume_bytes),
            },
        )
    except Exception as exc:
        logger.warning(
            "Media resume Range validation unavailable; "
            "yt-dlp will validate it: %s",
            exc,
        )
        return
    status = int((result or {}).get("status") or 0)
    logger.info(
        "Media resume Range validation: status=%s content_range=%s",
        status,
        (result or {}).get("contentRange") or "unknown",
    )
    if status in (401, 403, 410):
        raise RuntimeError(
            "刷新后的媒体地址仍然不可用，已保留原断点"
        )
    if (
        status == 200
        and resume_bytes >= config.safe_auto_restart_bytes()
    ):
        raise RuntimeError(
            "刷新后的媒体地址不支持 Range 续传，已保留原断点"
        )


def _prepare_media_referer_page(
    page,
    headers: Dict[str, str],
    logger: logging.Logger,
    media_url: str = "",
) -> None:
    referer = str(headers.get("Referer") or headers.get("referer") or "").strip()
    allowed_domain = ""
    try:
        path = urlsplit(media_url or "").path
    except ValueError:
        path = ""
    marker = "/referer="
    if path and marker in path:
        value = path.split(marker, 1)[1].split("/", 1)[0]
        for domain in value.split(","):
            domain = domain.strip().lower().lstrip(".")
            if domain and domain != "none" and "." in domain:
                allowed_domain = domain
                break
    if allowed_domain:
        try:
            referer_host = (urlsplit(referer).hostname or "").lower()
        except ValueError:
            referer_host = ""
        if not (
            referer_host == allowed_domain
            or referer_host.endswith("." + allowed_domain)
        ):
            scheme = urlsplit(media_url).scheme or "https"
            referer = "{}://{}/".format(scheme, allowed_domain)
    if not referer:
        return
    try:
        current_origin = (
            urlsplit(page.url).scheme,
            urlsplit(page.url).netloc.lower(),
        )
        referer_parts = urlsplit(referer)
        referer_origin = (referer_parts.scheme, referer_parts.netloc.lower())
    except ValueError:
        return
    if (
        not referer_parts.scheme
        or not referer_parts.netloc
        or current_origin == referer_origin
    ):
        return
    # Some signed HLS CDNs validate the player origin through the Referer.
    # A page in the source player origin gives fetch() the accepted Referer.
    target = "{}://{}/".format(referer_parts.scheme, referer_parts.netloc)
    try:
        logger.info("Switching HLS fetch page to media referer origin: %s", target)
        page.goto(target, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightError as exc:
        logger.warning(
            "Could not switch HLS fetch page to %s: %s",
            target,
            exc,
        )


def run(args: argparse.Namespace) -> int:
    args.media_policy = getattr(args, "media_policy", None) or MediaPolicy()
    args.quality = resolve_quality_target(
        args.media_policy,
        getattr(args, "quality", "site"),
    )
    if args.media_policy.allow_below_minimum:
        args.allow_below_minimum_quality = True
    logger = _build_logger(args.log_dir, args.verbose)
    metadata_url = str(args.metadata_url or args.url).strip()
    metadata_profile = resolve_site_profile(
        metadata_url,
        require_metadata=True,
    )
    download_profile = resolve_site_profile(args.url)
    logger.info(
        "Resolved site profiles metadata=%s download=%s "
        "metadata_parser=%s",
        metadata_profile.key,
        download_profile.key,
        metadata_profile.parser_version,
    )
    proxy = _detect_download_proxy(args.proxy)
    if proxy:
        logger.info("Using download proxy for yt-dlp fallback: %s", proxy)
    started = time.monotonic()
    closed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    summary: Dict = {
        "started_at": closed_at,
        "status": "failed",
        "input_url": args.url,
        "metadata_url": metadata_url,
        "download_url": args.url,
        "series_name": args.series_name or "",
        "attempt": 0,
        "size": 0,
        "elapsed_seconds": 0,
        "output_path": "",
        "media_url": redact_url(args.media_url or ""),
        "media_strategy_key": args.media_policy.strategy_key,
        "media_strategy": args.media_policy.as_dict(),
        "requested_quality": args.quality,
        "fallback_trace": [],
    }
    playwright = None
    browser = None
    task_page = None
    popup_guard = None
    launched = False

    try:
        version = launch_chrome(
            port=args.cdp_port,
            profile_dir=args.profile_dir,
            chrome_path=args.chrome_path,
            reuse_existing=args.reuse_cdp,
            logger=logger,
        )
        launched = not args.reuse_cdp
        logger.info("Connected CDP target browser: %s", version.get("Browser", "unknown"))

        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:{}".format(args.cdp_port)
        )
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = browser.new_context()
        task_page = context.new_page()
        page = task_page
        logger.info("Opened dedicated task page")
        popup_guard = PopupPageGuard(context, task_page, logger)

        candidate: Optional[MediaCandidate] = None
        candidates: List[MediaCandidate] = []
        if args.media_url:
            try:
                logger.info("Opening first-episode page: %s", args.url)
                page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
            except PlaywrightError as exc:
                logger.warning(
                    "Could not open the episode page; continuing with --media-url: %s",
                    exc,
                )
            candidate = MediaCandidate(
                url=args.media_url,
                source="command-line",
                score=999,
            )
            logger.info("Using explicit media URL: %s", redact_url(args.media_url))
        else:
            args.url = _resolve_bilibili_season_page(
                page,
                args.url,
                logger,
            )
            candidate, candidates, page = _discover_media(page, args, logger)

        if args.probe_media:
            selected_key = candidate.url if candidate is not None else ""
            summary.update({
                "status": "probed",
                "candidate_count": len(candidates),
                "selected_candidate_url": redact_url(selected_key),
                "selection_reason": getattr(
                    args,
                    "selection_reason",
                    "",
                ),
                "candidates": [
                    {
                        "url": redact_url(item.url),
                        "source": item.source,
                        "kind": item.kind,
                        "quality": item.quality_label,
                        "matches_page_video": item.matches_page_video,
                        "score": item.score,
                        "selected": item.url == selected_key,
                    }
                    for item in candidates
                ],
            })
            logger.info(
                "Media policy probe complete candidates=%s selected=%s",
                len(candidates),
                candidate.quality_label if candidate else "none",
            )
            return 0

        title_candidates = _page_titles(page)
        logger.info("Page title candidates: %s", title_candidates)
        source_title = ""
        if (
            not (args.series_name and args.series_name.strip())
            and metadata_url != args.url
        ):
            source_title = _source_page_title(
                context,
                metadata_url,
                logger,
            )
        series_name = _select_series_title(
            args.series_name or "",
            source_title,
            title_candidates,
            args.url,
            metadata_url,
        )
        summary["series_name"] = series_name
        logger.info("Final series name: %s", series_name)

        existing = find_existing_output(args.output, series_name)
        if existing:
            size = existing.stat().st_size
            if is_ad_like_existing_output(existing):
                logger.warning(
                    "Ignoring %s-byte previous output because it is smaller "
                    "than the 20 MiB advertisement threshold: %s",
                    size,
                    existing,
                )
                existing.unlink()
                existing = None
            if existing:
                media_info = probe_video(existing)
                summary.update(
                    {
                        "status": "skipped",
                        "size": size,
                        "output_path": str(existing),
                        "media_quality": normalise_quality_label(
                            media_info.get("quality"),
                            width=media_info.get("width"),
                            height=media_info.get("height"),
                        ),
                        "media_width": media_info.get("width"),
                        "media_height": media_info.get("height"),
                    }
                )
                logger.info("Existing output found: %s", existing)
                logger.info("Skipped existing download task")
                if not args.no_library_registration:
                    register_downloaded_video(
                        output_path=existing,
                        title=series_name,
                        original_url=metadata_url,
                        download_source_url=args.url,
                        logger=logger,
                    )
                else:
                    logger.info(
                        "Download-only task: skipped library registration"
                    )
                return 0

        if candidate:
            target = candidate.url
            summary["media_url"] = redact_url(target)
            summary["media_kind"] = candidate.kind
            summary["selected_candidate_source"] = candidate.source
            summary["selected_candidate_kind"] = candidate.kind
            summary["selection_reason"] = getattr(
                args,
                "selection_reason",
                "",
            )
            headers = dict(candidate.headers)
            logger.info("Selected media type: %s", candidate.kind)
            if candidate.kind == "file" and candidate.quality_height:
                summary.update(
                    {
                        "media_quality": candidate.quality_label,
                        "media_width": candidate.width or None,
                        "media_height": candidate.height or None,
                        "media_bandwidth": candidate.bandwidth or None,
                    }
                )
                logger.info(
                    "Media selected: quality=%s width=%s height=%s",
                    candidate.quality_label,
                    candidate.width or "unknown",
                    candidate.height or "unknown",
                )
        elif (
            args.allow_page_fallback
            or args.media_policy.allow_page_fallback
        ):
            target = args.url
            headers = {}
            logger.warning(
                "No media request found; falling back to yt-dlp page extraction"
            )
        else:
            raise RuntimeError(
                "No media URL was found. Start playback, pass --play-selector, "
                "--media-url, or enable --allow-page-fallback."
            )

        cookies = context.cookies([args.url])
        headers.setdefault("Referer", args.url)
        headers.setdefault("User-Agent", page.evaluate("navigator.userAgent"))

        current_candidate = candidate
        refresh_count = 0
        media_expires_at = extract_media_expiry(target)
        if media_expires_at is not None:
            logger.info(
                "Media recovery state: expires_at=%s identity=%s",
                media_expires_at.isoformat(timespec="seconds"),
                media_identity(
                    target,
                    getattr(current_candidate, "quality_label", ""),
                    getattr(current_candidate, "width", None),
                    getattr(current_candidate, "height", None),
                ),
            )

        def refresh_target_for_download(reason, previous_expiry=None):
            nonlocal current_candidate, headers, media_expires_at, refresh_count
            if current_candidate is None:
                raise RuntimeError("没有可用于刷新媒体地址的候选")
            refresh_count += 1
            logger.warning(
                "Media URL refresh start reason=%s expires_at=%s "
                "refresh=%s/%s",
                reason,
                previous_expiry.isoformat(timespec="seconds")
                if previous_expiry is not None
                else "unknown",
                refresh_count,
                3,
            )
            popup_guard.pause()
            try:
                refreshed, refreshed_headers = _refresh_media_candidate(
                    context,
                    args,
                    logger,
                    current_candidate,
                )
            finally:
                popup_guard.resume()
            resume_bytes = partial_size(args.output, series_name)
            if current_candidate.kind == "file":
                _validate_resume_range(
                    page,
                    refreshed.url,
                    refreshed_headers,
                    resume_bytes,
                    logger,
                )
            current_candidate = refreshed
            headers = refreshed_headers
            headers.setdefault("Referer", args.url)
            headers.setdefault("User-Agent", page.evaluate("navigator.userAgent"))
            if current_candidate.kind == "hls":
                _prepare_media_referer_page(
                    page,
                    headers,
                    logger,
                    target,
                )
            media_expires_at = extract_media_expiry(refreshed.url)
            summary["media_url"] = redact_url(refreshed.url)
            logger.info(
                "Media URL refresh success expires_at=%s partial_bytes=%s",
                media_expires_at.isoformat(timespec="seconds")
                if media_expires_at is not None
                else "unknown",
                resume_bytes,
            )
            return {
                "target": refreshed.url,
                "headers": headers,
                "expires_at": media_expires_at,
            }

        result = None
        actual_transport = ""
        if candidate and candidate.kind == "hls":
            _prepare_media_referer_page(page, headers, logger, target)
        if (
            candidate
            and candidate.kind == "hls"
            and args.transport_mode != "yt-dlp"
            and args.transport_mode != "direct-range"
        ):
            browser_hls = BrowserHlsDownloader(
                page=page,
                output_dir=args.output,
                logger=logger,
                retries=args.retries,
                retry_backoff=args.retry_backoff,
                max_height=(
                    _preferred_height(args.quality)
                    or args.media_policy.max_height
                ),
                quality_target=args.quality,
                quality_fallback=args.media_policy.quality_fallback,
                allow_below_minimum=args.allow_below_minimum_quality,
                expires_at=media_expires_at,
                refresh_target=refresh_target_for_download,
            )
            try:
                result = browser_hls.download(
                    target,
                    basename=series_name,
                    attempts=args.download_attempts,
                )
                if result is not None:
                    actual_transport = "hls"
            except HlsRateLimitPause:
                raise
            except RuntimeError as exc:
                logger.warning(
                    "Browser HLS path failed; falling back to yt-dlp: %s", exc
                )

        manifest_file_fallback = False
        if (
            result is None
            and candidate
            and candidate.kind in {"hls", "dash"}
        ):
            file_fallbacks = _direct_file_fallback_candidates(
                candidates,
                args.quality,
                args.media_policy,
            )
            if file_fallbacks:
                candidate = file_fallbacks[0]
                target = candidate.url
                headers = dict(candidate.headers)
                headers.setdefault("Referer", args.url)
                headers.setdefault(
                    "User-Agent",
                    page.evaluate("navigator.userAgent"),
                )
                current_candidate = candidate
                media_expires_at = extract_media_expiry(target)
                summary["media_url"] = redact_url(target)
                summary["media_kind"] = candidate.kind
                summary["media_quality"] = candidate.quality_label
                summary["media_width"] = candidate.width or None
                summary["media_height"] = candidate.height or None
                manifest_file_fallback = True
                summary["fallback_trace"].append({
                    "stage": "manifest_to_file",
                    "quality": candidate.quality_label,
                    "source": candidate.source,
                    "kind": candidate.kind,
                })
                logger.warning(
                    "Preferred manifest unavailable; trying direct file "
                    "fallback quality=%s url=%s",
                    candidate.quality_label or "unknown",
                    redact_url(target),
                )
                logger.info(
                    "Media selected: quality=%s width=%s height=%s",
                    candidate.quality_label or "unknown",
                    candidate.width or "unknown",
                    candidate.height or "unknown",
                )

        if (
            result is None
            and candidate
            and candidate.kind == "file"
            and (
                args.transport_mode
                in {"auto", "qualify", "direct-range"}
                or manifest_file_fallback
            )
        ):
            direct_candidates = _order_direct_candidates_for_resume(
                candidate,
                candidates,
                args.output,
                series_name,
            )
            for direct_candidate in direct_candidates:
                if direct_candidate is not candidate:
                    summary["fallback_trace"].append({
                        "stage": "direct_candidate",
                        "quality": direct_candidate.quality_label,
                        "source": direct_candidate.source,
                        "kind": direct_candidate.kind,
                    })
                    target = direct_candidate.url
                    headers = dict(direct_candidate.headers)
                    headers.setdefault("Referer", args.url)
                    headers.setdefault(
                        "User-Agent",
                        page.evaluate("navigator.userAgent"),
                    )
                    current_candidate = direct_candidate
                    media_expires_at = extract_media_expiry(target)
                    summary["media_url"] = redact_url(target)
                    logger.warning(
                        "Trying fallback file candidate quality=%s url=%s",
                        direct_candidate.quality_label or "unknown",
                        redact_url(target),
                    )
                    logger.info(
                        "Media selected: quality=%s width=%s height=%s",
                        direct_candidate.quality_label or "unknown",
                        direct_candidate.width or "unknown",
                        direct_candidate.height or "unknown",
                    )
                try:
                    direct_range = DirectRangeDownloader(
                        output_dir=args.output,
                        logger=logger,
                        proxy=proxy,
                        refresh_target=refresh_target_for_download,
                        initial_connections=args.range_initial_connections,
                        max_connections=args.range_max_connections,
                        tls_backend=args.tls_backend,
                    )
                    if args.transport_mode in {"auto", "qualify"}:
                        try:
                            qualification = direct_range.qualify(
                                target=target,
                                headers=headers,
                                cookies=cookies,
                            )
                            logger.info(
                                "Transport qualification: status=passed "
                                "total=%s tls_backend=%s",
                                qualification["total_bytes"],
                                qualification["tls_backend"],
                            )
                        except (
                            RangeNotSupported,
                            MediaRefreshRequired,
                        ) as exc:
                            logger.warning(
                                "Transport qualification: status=failed "
                                "reason=%s",
                                exc,
                            )
                            raise RangeNotSupported(str(exc)) from exc
                    result = direct_range.download(
                        target=target,
                        basename=series_name,
                        headers=headers,
                        cookies=cookies,
                        media_quality=direct_candidate.quality_label,
                        media_width=direct_candidate.width or None,
                        media_height=direct_candidate.height or None,
                        expires_at=media_expires_at,
                    )
                    if result is not None:
                        if direct_candidate.audio_url:
                            result = _merge_direct_audio_stream(
                                result,
                                direct_candidate,
                                args,
                                logger,
                                headers,
                                cookies,
                                proxy,
                            )
                        candidate = direct_candidate
                        actual_transport = "direct-range"
                        break
                except RangeNotSupported as exc:
                    logger.info(
                        "Direct Range candidate unavailable quality=%s: %s",
                        direct_candidate.quality_label or "unknown",
                        exc,
                    )

        if result is None:
            fallback_target = target
            if candidate and candidate.kind in {"hls", "dash"}:
                logger.info(
                    "Using discovered media URL for yt-dlp fallback after %s failure",
                    candidate.kind,
                )
            summary["fallback_trace"].append({
                "stage": "yt-dlp",
                "quality": args.quality,
                "source": (
                    candidate.source
                    if candidate is not None
                    else "page"
                ),
                "kind": (
                    candidate.kind
                    if candidate is not None
                    else "page"
                ),
            })
            page_format = args.format
            if args.format == DEFAULT_FORMAT_SPEC:
                page_format = yt_dlp_format_selector(
                    args.media_policy,
                    _preferred_height(args.quality),
                )
            downloader = Downloader(
                output_dir=args.output,
                logger=logger,
                format_spec=format_selector_for_candidate(
                    candidate,
                    page_format,
                    (
                        _preferred_height(args.quality)
                        or args.media_policy.max_height
                    ),
                ),
                retries=args.retries,
                fragment_retries=args.fragment_retries,
                retry_backoff=args.retry_backoff,
                impersonate=args.impersonate or None,
                proxy=proxy,
                merge_output_format=(
                    args.media_policy.merge_output_format
                ),
            )
            result = downloader.download(
                target=fallback_target,
                basename=series_name,
                headers=headers,
                cookies=cookies,
                attempts=args.download_attempts,
                expires_at=media_expires_at,
                refresh_target=(
                    refresh_target_for_download
                    if current_candidate is not None
                    and current_candidate.kind == "file"
                    else None
                ),
            )
            if result is not None:
                actual_transport = "yt-dlp"

        info = result["info"]
        if is_advertisement_size(result["size"]):
            rejected_path = result["path"]
            logger.warning(
                "Downloaded media is only %s and is below the 20 MiB "
                "advertisement threshold; deleting it: %s",
                _format_bytes(result["size"]),
                rejected_path,
            )
            rejected_path.unlink(missing_ok=True)
            raise RuntimeError(
                "Downloaded media is smaller than 20 MiB and was treated as an advertisement"
            )
        summary.update(
            {
                "status": "success",
                "attempt": result["attempt"],
                "size": result["size"],
                "output_path": str(result["path"]),
                "media_title": info.get("title", ""),
                "duration_seconds": info.get("duration"),
                "media_quality": normalise_quality_label(
                    (
                        info.get("quality")
                        or info.get("format_note")
                        or summary.get("media_quality")
                    ),
                    width=info.get("width"),
                    height=info.get("height"),
                ),
                "media_width": info.get("width"),
                "media_height": info.get("height"),
                "media_bandwidth": info.get("bandwidth"),
                "transport_kind": actual_transport or None,
            }
        )
        logger.info("Download complete: %s", result["path"])
        logger.info("Output size: %s", _format_bytes(result["size"]))
        if not args.no_library_registration:
            register_downloaded_video(
                output_path=result["path"],
                title=series_name,
                original_url=metadata_url,
                download_source_url=args.url,
                logger=logger,
            )
        else:
            logger.info(
                "Download-only task: skipped library registration"
            )
        normalized_path = Path(result["path"])
        if not args.no_library_registration:
            normalized_path = _normalize_transport_stream_if_needed(
                normalized_path,
                logger,
            )
        if normalized_path != Path(result["path"]):
            result["path"] = normalized_path
            result["size"] = normalized_path.stat().st_size
            summary["output_path"] = str(normalized_path)
            summary["size"] = result["size"]
            logger.info(
                "Downloaded TS normalized to MP4: %s",
                normalized_path,
            )
        return 0
    except HlsRateLimitPause as exc:
        summary["status"] = "paused"
        summary["error"] = str(exc)
        logger.error("HLS rate limit requires pause: %s", exc)
        return RATE_LIMIT_PAUSE_EXIT_CODE
    except (ChromeLaunchError, PlaywrightError, RuntimeError) as exc:
        summary["error"] = str(exc)
        logger.error("Download failed: %s", exc)
        if "drm" in str(exc).lower() or "protected" in str(exc).lower():
            logger.error("The media appears to be DRM-protected and cannot be downloaded.")
        return 1
    except KeyboardInterrupt:
        summary["error"] = "Interrupted by user"
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        summary["error"] = repr(exc)
        logger.exception("Unexpected failure")
        return 1
    finally:
        if popup_guard is not None:
            popup_guard.stop()
            summary["closed_popup_pages"] = popup_guard.closed_count
        if task_page is not None:
            try:
                if not task_page.is_closed():
                    task_page.close(run_before_unload=False)
                    logger.info("Task page closed")
            except Exception:
                logger.debug("Task page close failed", exc_info=True)
        if browser is not None:
            try:
                if args.close_browser or (launched and not args.keep_browser_open):
                    browser.close()
            except Exception:
                logger.debug("Browser close failed", exc_info=True)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                logger.debug("Playwright cleanup failed", exc_info=True)

        summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
        summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _write_summary(args.log_dir, summary)
        logger.info(
            "Summary: status=%s series=%s attempt=%s size=%s elapsed=%.1fs",
            summary["status"],
            summary.get("series_name") or "unknown",
            summary.get("attempt", 0),
            _format_bytes(summary.get("size", 0)),
            summary["elapsed_seconds"],
        )
        logger.info("Run log: %s", args.log_dir.resolve())


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    policy_data = None
    if args.media_policy_file:
        try:
            policy_data = json.loads(
                args.media_policy_file.read_text(encoding="utf-8")
            )
        except (OSError, TypeError, ValueError) as exc:
            parser.error(
                "无法读取媒体选择策略: {}".format(exc)
            )
    args.media_policy = MediaPolicy.from_dict(policy_data)
    return run(args)
