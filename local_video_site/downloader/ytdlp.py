from __future__ import annotations

import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from local_video_site.core import config

from .sniffer import MediaCandidate, redact_url
from .errors import (
    PermanentDownloadError,
    permanent_download_error_message,
)
from .recovery import (
    MediaRefreshRequired,
    error_indicates_expiry,
    partial_size,
)


DEFAULT_FORMAT_SPEC = "best[height>=360][height<=1080]/best[height>=360]"


class _YtDlpLogger:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            self.logger.debug(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)

    def error(self, message: str) -> None:
        if message.startswith("Deprecated Feature:"):
            self.logger.debug(message)
        else:
            self.logger.error(message)


class DownloadProgress:
    def __init__(
        self,
        logger: logging.Logger,
        interval: float = 2.0,
        refresh_at: Optional[float] = None,
    ):
        self.logger = logger
        self.interval = interval
        self.refresh_at = refresh_at
        self._last_log_at = 0.0
        self._last_percent = -1.0
        self.filename: Optional[str] = None
        self.info: Dict = {}

    def hook(self, data: Dict) -> None:
        status = data.get("status")
        if status == "finished":
            self.filename = data.get("filename") or self.filename
            self.logger.info("Download stream finished: %s", self.filename or "unknown")
            return
        if status != "downloading":
            return
        if self.refresh_at is not None and time.time() >= self.refresh_at:
            raise MediaRefreshRequired("preemptive_expiry")

        now = time.monotonic()
        downloaded = data.get("downloaded_bytes") or 0
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        percent = (downloaded * 100.0 / total) if total else -1.0
        should_log = now - self._last_log_at >= self.interval
        if percent >= 0 and self._last_percent >= 0:
            should_log = should_log or percent - self._last_percent >= 2.0
        if not should_log:
            return

        self._last_log_at = now
        self._last_percent = percent
        speed = data.get("speed") or 0
        eta = data.get("eta")
        if total:
            self.logger.info(
                "Progress %.1f%% | %s / %s | %s/s | ETA %ss",
                percent,
                _format_bytes(downloaded),
                _format_bytes(total),
                _format_bytes(speed),
                int(eta) if eta is not None else "?",
            )
        else:
            self.logger.info(
                "Progress %s downloaded | %s/s",
                _format_bytes(downloaded),
                _format_bytes(speed),
            )


def _format_bytes(value: float) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} TiB".format(size)


def remove_partial_artifacts(output_dir: Path, basename: str) -> int:
    """Remove only resumable artifacts for one download, never the final file."""

    removed = 0
    prefix = basename + "."
    for path in output_dir.iterdir():
        if not path.is_file() or not path.name.startswith(prefix):
            continue
        if ".part" not in path.name and not path.name.endswith(".ytdl"):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


class Downloader:
    def __init__(
        self,
        output_dir: Path,
        logger: logging.Logger,
        format_spec: str = DEFAULT_FORMAT_SPEC,
        retries: int = 3,
        fragment_retries: int = 5,
        retry_backoff: float = 2.0,
        impersonate: Optional[str] = "chrome",
        proxy: Optional[str] = None,
        merge_output_format: Optional[str] = None,
        concurrent_fragment_downloads: Optional[int] = None,
    ):
        self.output_dir = output_dir.resolve()
        self.logger = logger
        self.format_spec = format_spec
        self.retries = retries
        self.fragment_retries = fragment_retries
        self.retry_backoff = retry_backoff
        self.impersonate = (
            ImpersonateTarget.from_str(impersonate)
            if isinstance(impersonate, str) and impersonate
            else impersonate
        )
        self.proxy = str(proxy or "").strip() or None
        self.merge_output_format = (
            str(merge_output_format or "").strip().lower() or None
        )
        self.concurrent_fragment_downloads = (
            config.ytdlp_concurrent_fragments()
            if concurrent_fragment_downloads is None
            else max(1, min(8, int(concurrent_fragment_downloads)))
        )

    def download(
        self,
        target: str,
        basename: str,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[List[Dict]] = None,
        attempts: int = 3,
        expires_at: Optional[datetime] = None,
        refresh_target: Optional[Callable] = None,
        refresh_attempts: int = 3,
    ) -> Dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        last_error: Optional[Exception] = None
        cookie_file = self._write_cookie_file(cookies or [])
        resume_attempts = max(1, attempts)
        refresh_limit = max(0, int(refresh_attempts))
        refresh_count = 0
        attempt = 0

        try:
            while attempt < resume_attempts:
                attempt += 1
                self.logger.info(
                    "Resume download attempt %s/%s from %s",
                    attempt,
                    resume_attempts,
                    redact_url(target),
                )
                try:
                    result = self._download_once(
                        target,
                        basename,
                        headers or {},
                        cookie_file,
                        attempt,
                        fresh=False,
                        expires_at=(
                            expires_at if refresh_target is not None else None
                        ),
                    )
                    removed = remove_partial_artifacts(self.output_dir, basename)
                    if removed:
                        self.logger.info(
                            "Removed %s stale partial file(s) after successful download",
                            removed,
                        )
                    return result
                except (DownloadError, RuntimeError) as exc:
                    last_error = exc
                    permanent_message = permanent_download_error_message(exc)
                    if permanent_message:
                        raise PermanentDownloadError(
                            permanent_message
                        ) from exc
                    self.logger.warning("Download attempt %s failed: %s", attempt, exc)
                except Exception as exc:
                    last_error = exc
                    self.logger.exception(
                        "Unexpected download failure on attempt %s", attempt
                    )

                if error_indicates_expiry(last_error):
                    if refresh_target is None or refresh_count >= refresh_limit:
                        raise MediaRefreshRequired(
                            "media URL expired and refresh is unavailable"
                        ) from last_error
                    refresh_count += 1
                    self.logger.warning(
                        "Media URL refresh required attempt=%s/%s reason=%s",
                        refresh_count,
                        refresh_limit,
                        last_error,
                    )
                    refreshed = refresh_target(
                        str(last_error),
                        expires_at,
                    )
                    new_target = str((refreshed or {}).get("target") or "").strip()
                    if not new_target or new_target == target:
                        raise MediaRefreshRequired(
                            "page did not provide a new media URL"
                        ) from last_error
                    target = new_target
                    headers = dict((refreshed or {}).get("headers") or headers or {})
                    expires_at = (refreshed or {}).get("expires_at")
                    attempt = max(0, attempt - 1)
                    self.logger.info(
                        "Media URL refreshed count=%s/%s new_target=%s",
                        refresh_count,
                        refresh_limit,
                        redact_url(target),
                    )
                    continue

                if attempt < resume_attempts:
                    delay = self.retry_backoff * (2 ** (attempt - 1))
                    self.logger.info("Retrying in %.1f seconds", delay)
                    time.sleep(delay)

            retained = partial_size(self.output_dir, basename)
            if retained >= config.safe_auto_restart_bytes():
                raise RuntimeError(
                    "Resume attempts exhausted; preserving {} bytes of "
                    "partial media instead of starting over".format(retained)
                ) from last_error
            removed = remove_partial_artifacts(self.output_dir, basename)
            self.logger.warning(
                "Resume attempts exhausted; removed %s residual file(s) "
                "and starting one fresh download",
                removed,
            )
            try:
                result = self._download_once(
                    target,
                    basename,
                    headers or {},
                    cookie_file,
                    resume_attempts + 1,
                    fresh=True,
                )
                removed = remove_partial_artifacts(self.output_dir, basename)
                if removed:
                    self.logger.info(
                        "Removed %s stale partial file(s) after fresh download",
                        removed,
                    )
                return result
            except (DownloadError, RuntimeError) as exc:
                last_error = exc
                permanent_message = permanent_download_error_message(exc)
                if permanent_message:
                    raise PermanentDownloadError(
                        permanent_message
                    ) from exc
                self.logger.error("Fresh download failed: %s", exc)
            except Exception as exc:
                last_error = exc
                self.logger.exception("Unexpected fresh download failure")

            remove_partial_artifacts(self.output_dir, basename)
            raise RuntimeError(
                "Download failed after {} resume attempts and one fresh restart: {}".format(
                    resume_attempts,
                    last_error,
                )
            )
        finally:
            if cookie_file is not None:
                try:
                    cookie_file.unlink()
                except OSError:
                    self.logger.debug("Could not remove temporary cookie file")

    def _write_cookie_file(self, cookies: List[Dict]) -> Optional[Path]:
        if not cookies:
            return None
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="comic3-cookies-",
            suffix=".txt",
            delete=False,
        )
        path = Path(handle.name)
        try:
            os.chmod(path, 0o600)
            handle.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                domain = str(cookie.get("domain") or "")
                if not domain:
                    continue
                includes_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
                secure = "TRUE" if cookie.get("secure") else "FALSE"
                expires = int(cookie.get("expires") or 0)
                if expires <= 0:
                    expires = 2147483647
                handle.write(
                    "\t".join(
                        [
                            domain,
                            includes_subdomains,
                            str(cookie.get("path") or "/"),
                            secure,
                            str(expires),
                            str(cookie.get("name") or ""),
                            str(cookie.get("value") or ""),
                        ]
                    )
                    + "\n"
                )
        finally:
            handle.close()
        return path

    def _download_once(
        self,
        target: str,
        basename: str,
        headers: Dict[str, str],
        cookie_file: Optional[Path],
        attempt: int,
        fresh: bool,
        expires_at: Optional[datetime] = None,
    ) -> Dict:
        refresh_at = None
        if expires_at is not None:
            candidate_refresh_at = expires_at.timestamp() - 300
            if candidate_refresh_at > time.time():
                refresh_at = candidate_refresh_at
        progress = DownloadProgress(
            self.logger,
            refresh_at=refresh_at,
        )

        def on_info(info: Dict) -> None:
            progress.info = info

        options = {
            "paths": {"home": str(self.output_dir)},
            "outtmpl": {"default": basename + ".%(ext)s"},
            "format": self.format_spec,
            "noplaylist": True,
            "continuedl": not fresh,
            "overwrites": False,
            "retries": self.retries,
            "fragment_retries": self.fragment_retries,
            "concurrent_fragment_downloads": (
                self.concurrent_fragment_downloads
            ),
            "extractor_retries": self.retries,
            "file_access_retries": self.retries,
            "http_headers": headers,
            "cookiefile": str(cookie_file) if cookie_file else None,
            "progress_hooks": [progress.hook],
            "post_hooks": [],
            "quiet": True,
            "no_warnings": False,
            "logger": _YtDlpLogger(self.logger),
            "noprogress": True,
            "impersonate": self.impersonate,
        }
        if self.proxy:
            options["proxy"] = self.proxy
        if self.merge_output_format:
            options["merge_output_format"] = self.merge_output_format
        self.logger.debug("yt-dlp options: %s", {
            key: value
            for key, value in options.items()
            if key not in ("http_headers", "progress_hooks", "logger")
        })

        with YoutubeDL(options) as ydl:
            result = ydl.extract_info(target, download=True)

        expected_path = None
        if self.merge_output_format:
            expected_path = self.output_dir / (
                basename + "." + self.merge_output_format
            )
        if expected_path is not None and expected_path.is_file():
            path = expected_path
        else:
            requested = result.get("requested_downloads") or []
            candidates = [
                result.get("filepath"),
                result.get("_filename"),
                progress.filename,
            ]
            candidates.extend(
                item.get("filepath")
                for item in requested
                if isinstance(item, dict)
            )
            existing = []
            for value in candidates:
                if not value:
                    continue
                candidate_path = Path(value)
                if not candidate_path.is_absolute():
                    candidate_path = self.output_dir / candidate_path
                if candidate_path.is_file():
                    existing.append(candidate_path)
            if not existing:
                path = None
            else:
                video_extensions = {
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                    ".flv",
                    ".ts",
                }
                video_paths = [
                    item
                    for item in existing
                    if item.suffix.lower() in video_extensions
                ]
                path = max(
                    video_paths or existing,
                    key=lambda item: item.stat().st_size,
                )

        if path is None:
            raise RuntimeError("yt-dlp did not report a downloaded file path")

        return {
            "path": path,
            "info": result,
            "attempt": attempt,
            "size": path.stat().st_size,
        }
