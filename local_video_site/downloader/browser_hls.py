from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError

from local_video_site.core import config
from local_video_site.integrations.profiles import resolve_media_profile

from .sniffer import redact_url
from .recovery import MediaRefreshRequired, error_indicates_expiry


_ATTRIBUTE_RE = re.compile(r'([A-Z0-9-]+)=("(?:[^"\\]|\\.)*"|[^,]*)')
ABSOLUTE_HLS_MAX_HEIGHT = 2160
RATE_LIMIT_STATUSES = (403, 429, 451, 503)


class HlsRateLimitPause(RuntimeError):
    """Raised when repeated CDN throttling requires pausing the task."""


class HlsPlaylistUnsupported(RuntimeError):
    """Raised when the playlist needs a feature handled by ffmpeg/yt-dlp."""


def _parse_attributes(value: str) -> Dict[str, str]:
    return {
        key: item.strip().strip('"')
        for key, item in _ATTRIBUTE_RE.findall(value)
    }


@dataclass(frozen=True)
class HlsVariant:
    url: str
    bandwidth: int
    width: int
    height: int
    frame_rate: float
    name: str

    @property
    def quality_label(self) -> str:
        if self.name:
            return self.name
        if self.height:
            return "{}p".format(self.height)
        return "unknown"

    @property
    def resolution_label(self) -> str:
        if self.width and self.height:
            return "{}x{}".format(self.width, self.height)
        return "unknown"

    @property
    def sort_key(self) -> Tuple[int, int, int, float]:
        # Prefer picture resolution; bitrate only breaks equal-resolution ties.
        return (self.height, self.width, self.bandwidth, self.frame_rate)


def _format_bytes(value: float) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} TiB".format(size)


def _stable_url(value: str) -> str:
    return resolve_media_profile(value).hls_stable_url(value)


def _segment_signature(manifest_url: str, segments: List[Tuple[str, str]]) -> str:
    content = "\n".join(
        [_stable_url(manifest_url)]
        + [_stable_url(url) for _, url in segments]
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_checkpoint(path: Path, payload: Dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_checkpoint(path: Path) -> Optional[Dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class BrowserHlsDownloader:
    """Download CORS-accessible HLS media through the page's Chrome network."""

    def __init__(
        self,
        page,
        output_dir: Path,
        logger: logging.Logger,
        retries: int = 5,
        retry_backoff: float = 1.0,
        max_height: Optional[int] = None,
        quality_target: str = "auto",
        quality_fallback: str = "nearest_below",
        allow_below_minimum: bool = False,
        expires_at: Optional[datetime] = None,
        refresh_target: Optional[Callable] = None,
        refresh_attempts: int = 3,
    ):
        self.page = page
        self.output_dir = output_dir.resolve()
        self.logger = logger
        self.retries = max(1, retries)
        self.retry_backoff = max(0.0, retry_backoff)
        minimum_height = config.sniffer_minimum_video_height()
        requested_max_height = (
            config.sniffer_preferred_max_video_height()
            if max_height is None
            else int(max_height)
        )
        self.max_height = max(
            minimum_height,
            min(int(requested_max_height), ABSOLUTE_HLS_MAX_HEIGHT),
        )
        self.quality_target = str(quality_target or "auto").lower()
        self.quality_fallback = str(
            quality_fallback or "nearest_below"
        ).lower()
        self.allow_below_minimum = bool(allow_below_minimum)
        self.expires_at = expires_at
        self.refresh_target = refresh_target
        self.refresh_attempts = max(0, int(refresh_attempts))
        self.refresh_at = self._refresh_deadline(expires_at)
        self.concurrency = config.hls_initial_concurrency()
        self.clean_batches = 0
        self.rate_limit_events: List[float] = []
        self.last_rate_limit_at: Optional[float] = None

    def download(
        self,
        manifest_url: str,
        basename: str,
        attempts: int = 3,
    ) -> Dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.concurrency = config.hls_initial_concurrency()
        self.clean_batches = 0
        self.rate_limit_events.clear()
        self.last_rate_limit_at = None
        last_error: Optional[Exception] = None
        attempt = 0
        refresh_count = 0
        while attempt < max(1, attempts):
            attempt += 1
            self.logger.info(
                "Browser HLS download attempt %s/%s from %s",
                attempt,
                max(1, attempts),
                redact_url(manifest_url),
            )
            try:
                return self._download_once(manifest_url, basename, attempt)
            except HlsRateLimitPause:
                raise
            except HlsPlaylistUnsupported:
                raise
            except Exception as exc:
                last_error = exc
                if error_indicates_expiry(exc):
                    if (
                        self.refresh_target is None
                        or refresh_count >= self.refresh_attempts
                    ):
                        raise MediaRefreshRequired(
                            "HLS media URL expired and refresh is unavailable"
                        ) from exc
                    refresh_count += 1
                    refreshed = self.refresh_target(
                        str(exc),
                        self.expires_at,
                    )
                    new_target = str(
                        (refreshed or {}).get("target") or ""
                    ).strip()
                    if not new_target or new_target == manifest_url:
                        raise MediaRefreshRequired(
                            "page did not provide a new HLS media URL"
                        ) from exc
                    manifest_url = new_target
                    self.expires_at = (refreshed or {}).get("expires_at")
                    self.refresh_at = self._refresh_deadline(
                        self.expires_at
                    )
                    attempt = max(0, attempt - 1)
                    self.logger.info(
                        "HLS media URL refreshed count=%s/%s target=%s",
                        refresh_count,
                        self.refresh_attempts,
                        redact_url(manifest_url),
                    )
                    continue
                self.logger.warning("Browser HLS attempt %s failed: %s", attempt, exc)
                if attempt < attempts:
                    delay = self.retry_backoff * (2 ** (attempt - 1))
                    self.logger.info("Retrying browser HLS in %.1f seconds", delay)
                    time.sleep(delay)
        raise RuntimeError(
            "Browser HLS download failed after {} attempts: {}".format(
                max(1, attempts),
                last_error,
            )
        )

    @staticmethod
    def _refresh_deadline(
        expires_at: Optional[datetime],
    ) -> Optional[float]:
        if expires_at is None:
            return None
        deadline = expires_at.timestamp() - 300
        return deadline if deadline > time.time() else None

    def _download_once(self, manifest_url: str, basename: str, attempt: int) -> Dict:
        playlist_url, playlist, quality = self._resolve_playlist(manifest_url)
        self.logger.info(
            "Media selected: quality=%s width=%s height=%s",
            quality.get("quality") or "unknown",
            quality.get("width") or "unknown",
            quality.get("height") or "unknown",
        )
        if "#EXT-X-KEY:" in playlist and "METHOD=NONE" not in playlist:
            raise HlsPlaylistUnsupported(
                "Browser HLS path does not decrypt AES-128/SAMPLE-AES playlists"
            )
        if "#EXT-X-BYTERANGE:" in playlist:
            raise HlsPlaylistUnsupported(
                "Browser HLS path does not support byte-range playlists"
            )
        if "#EXT-X-ENDLIST" not in playlist:
            raise RuntimeError("Browser HLS path only supports completed VOD playlists")

        entries = self._parse_media(playlist_url, playlist)
        initialization = [item for item in entries if item[0] == "init"]
        segments = [item for item in entries if item[0] == "segment"]
        if not segments:
            raise RuntimeError("HLS media playlist contains no segments")

        extension = ".mp4" if initialization or segments[0][1].endswith(".m4s") else ".ts"
        final_path = self.output_dir / (basename + extension)
        possible_existing = [self.output_dir / (basename + ".mp4"), final_path]
        existing = next(
            (
                path
                for path in possible_existing
                if path.exists() and path.stat().st_size > 0
            ),
            None,
        )
        if existing:
            self.logger.info("Output already exists: %s", existing)
            return {
                "path": existing,
                "info": {
                    "title": basename,
                    "duration": self._duration(playlist),
                    "quality": quality.get("quality"),
                    "width": quality.get("width"),
                    "height": quality.get("height"),
                    "bandwidth": quality.get("bandwidth"),
                    "requested_downloads": [{"filepath": str(existing)}],
                },
                "attempt": attempt,
                "size": existing.stat().st_size,
            }

        part_path = self.output_dir / (basename + extension + ".part")
        checkpoint_path = self.output_dir / (
            basename + extension + ".resume.json"
        )
        signature = _segment_signature(manifest_url, segments)
        checkpoint = _read_checkpoint(checkpoint_path)
        start_index = 0
        initialization_complete = False
        total_bytes = 0
        mode = "wb"
        checkpoint_signature = (
            str(checkpoint.get("signature") or "")
            if checkpoint
            else ""
        )
        signature_matches = checkpoint_signature == signature
        legacy_checkpoint_matches = bool(
            checkpoint
            and not signature_matches
            and int(checkpoint.get("segment_count") or -1) == len(segments)
            and part_path.is_file()
        )
        if checkpoint and (
            signature_matches or legacy_checkpoint_matches
        ) and part_path.is_file():
            checkpoint_size = int(checkpoint.get("part_size") or 0)
            actual_size = part_path.stat().st_size
            if actual_size >= checkpoint_size >= 0:
                with part_path.open("r+b") as resumed:
                    resumed.truncate(checkpoint_size)
                start_index = min(
                    len(segments),
                    max(0, int(checkpoint.get("next_segment_index") or 0)),
                )
                initialization_complete = bool(
                    checkpoint.get("initialization_complete")
                )
                total_bytes = int(checkpoint.get("total_bytes") or checkpoint_size)
                mode = "ab"
                self.logger.info(
                    "Resuming HLS from segment %s/%s%s",
                    start_index + 1,
                    len(segments),
                    (
                        " (legacy checkpoint signature accepted)"
                        if legacy_checkpoint_matches
                        else ""
                    ),
                )
        if mode == "wb":
            checkpoint_path.unlink(missing_ok=True)
            part_path.unlink(missing_ok=True)
        started = time.monotonic()
        speed_start_bytes = total_bytes
        batches_since_checkpoint = 0
        bytes_since_checkpoint = 0

        def save_checkpoint(output, next_index: int) -> None:
            output.flush()
            os.fsync(output.fileno())
            _write_checkpoint(
                checkpoint_path,
                {
                    "version": 1,
                    "signature": signature,
                    "segment_count": len(segments),
                    "next_segment_index": next_index,
                    "part_size": output.tell(),
                    "total_bytes": total_bytes,
                    "initialization_complete": initialization_complete,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
            )

        output = part_path.open(mode)
        current_index = start_index
        completed = False
        try:
            if not initialization_complete:
                for _, item_url in initialization:
                    data = self._fetch_bytes(item_url)
                    output.write(data)
                    total_bytes += len(data)
                initialization_complete = True
                save_checkpoint(output, current_index)

            while current_index < len(segments):
                if (
                    self.refresh_at is not None
                    and time.time() >= self.refresh_at
                ):
                    raise MediaRefreshRequired(
                        "preemptive HLS media URL expiry"
                    )
                batch_start = current_index
                batch_end = min(
                    len(segments),
                    batch_start + config.hls_batch_size(),
                )
                batch_urls = [
                    url for _, url in segments[batch_start:batch_end]
                ]
                batch_data, clean = self._fetch_batch_with_retries(
                    batch_urls
                )
                batch_bytes = 0
                for data in batch_data:
                    output.write(data)
                    batch_bytes += len(data)
                total_bytes += batch_bytes
                current_index = batch_end
                batches_since_checkpoint += 1
                bytes_since_checkpoint += batch_bytes
                if clean:
                    self._record_clean_batch()

                now = time.monotonic()
                elapsed = max(now - started, 0.001)
                speed = max(0, total_bytes - speed_start_bytes) / elapsed
                remaining_count = len(segments) - current_index
                processed_this_session = max(
                    current_index - start_index,
                    1,
                )
                eta = (
                    elapsed * remaining_count / processed_this_session
                )
                self.logger.info(
                    "HLS batch %s-%s/%s concurrency=%s "
                    "bytes=%s speed=%s/s ETA %.0fs",
                    batch_start + 1,
                    current_index,
                    len(segments),
                    self.concurrency,
                    _format_bytes(batch_bytes),
                    _format_bytes(speed),
                    eta,
                )
                self.logger.info(
                    "Progress %.1f%% | segment %s/%s | %s | %s/s | ETA %.0fs",
                    current_index * 100.0 / len(segments),
                    current_index,
                    len(segments),
                    _format_bytes(total_bytes),
                    _format_bytes(speed),
                    eta,
                )
                if (
                    batches_since_checkpoint
                    >= config.hls_checkpoint_batches()
                    or bytes_since_checkpoint
                    >= config.hls_checkpoint_min_bytes()
                    or current_index == len(segments)
                ):
                    save_checkpoint(output, current_index)
                    batches_since_checkpoint = 0
                    bytes_since_checkpoint = 0
            save_checkpoint(output, current_index)
            completed = True
        finally:
            try:
                if not completed:
                    save_checkpoint(output, current_index)
            finally:
                output.close()

        os.replace(part_path, final_path)
        checkpoint_path.unlink(missing_ok=True)
        final_path = self._remux_ts_to_mp4(final_path, basename)
        size = final_path.stat().st_size
        self.logger.info("Browser HLS segments merged: %s", final_path)
        return {
            "path": final_path,
            "info": {
                "title": basename,
                "duration": self._duration(playlist),
                "quality": quality.get("quality"),
                "width": quality.get("width"),
                "height": quality.get("height"),
                "bandwidth": quality.get("bandwidth"),
                "requested_downloads": [{"filepath": str(final_path)}],
            },
            "attempt": attempt,
            "size": size,
        }

    def _remux_ts_to_mp4(self, ts_path: Path, basename: str) -> Path:
        if ts_path.suffix.lower() != ".ts":
            return ts_path
        avconvert = shutil.which("avconvert")
        if not avconvert:
            return ts_path

        mp4_path = self.output_dir / (basename + ".mp4")
        temporary_mp4 = self.output_dir / (basename + ".remux.mp4")
        try:
            if temporary_mp4.exists():
                temporary_mp4.unlink()
            self.logger.info("Remuxing MPEG-TS to MP4 without re-encoding")
            completed = subprocess.run(
                [
                    avconvert,
                    "--source",
                    str(ts_path),
                    "--preset",
                    "PresetPassthrough",
                    "--output",
                    str(temporary_mp4),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if (
                completed.returncode == 0
                and temporary_mp4.exists()
                and temporary_mp4.stat().st_size > 0
            ):
                os.replace(temporary_mp4, mp4_path)
                ts_path.unlink()
                self.logger.info("MP4 remux complete: %s", mp4_path)
                return mp4_path
            detail = (completed.stderr or completed.stdout or "").strip()
            self.logger.warning("MP4 remux failed; keeping TS%s", ": " + detail if detail else "")
        except (OSError, subprocess.SubprocessError) as exc:
            self.logger.warning("MP4 remux failed; keeping TS: %s", exc)
        finally:
            if temporary_mp4.exists():
                try:
                    temporary_mp4.unlink()
                except OSError:
                    pass
        return ts_path

    def _resolve_playlist(self, url: str) -> Tuple[str, str, Dict]:
        playlist = self._fetch_text(url)
        if "#EXT-X-STREAM-INF:" not in playlist:
            master_urls = self._infer_master_urls(url)
            master_url = None
            master_playlist = ""
            for candidate in master_urls:
                try:
                    candidate_playlist = self._fetch_text(candidate, attempts=1)
                except RuntimeError:
                    continue
                if "#EXT-X-STREAM-INF:" in candidate_playlist:
                    master_url = candidate
                    master_playlist = candidate_playlist
                    break
            if master_url:
                self.logger.info(
                    "Upgraded low-quality HLS playlist via master: %s",
                    redact_url(master_url),
                )
                url = master_url
                playlist = master_playlist
            else:
                inferred_height = self._quality_from_url(url)
                minimum_height = config.sniffer_minimum_video_height()
                if inferred_height and inferred_height < minimum_height:
                    raise RuntimeError(
                        "HLS playlist is below the minimum quality of {}p".format(
                            minimum_height
                        )
                    )
                return url, playlist, {
                    "quality": "{}p".format(inferred_height)
                    if inferred_height
                    else "unknown",
                    "height": inferred_height,
                    "width": None,
                    "bandwidth": None,
                }

        variants: List[HlsVariant] = []
        lines = [line.strip() for line in playlist.splitlines()]
        for index, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue
            attributes = _parse_attributes(line.split(":", 1)[1])
            try:
                bandwidth = int(float(attributes.get("BANDWIDTH", "0") or 0))
            except ValueError:
                bandwidth = 0
            resolution = attributes.get("RESOLUTION", "0x0")
            try:
                width, height = (int(part) for part in resolution.lower().split("x", 1))
            except (TypeError, ValueError):
                width, height = 0, 0
            name = attributes.get("NAME", "").strip()
            if not height:
                height = self._quality_from_name(name)
            try:
                frame_rate = float(attributes.get("FRAME-RATE", "0") or 0)
            except ValueError:
                frame_rate = 0.0
            for candidate in lines[index + 1 :]:
                if candidate and not candidate.startswith("#"):
                    variants.append(
                        HlsVariant(
                            url=urljoin(url, candidate),
                            bandwidth=bandwidth,
                            width=width,
                            height=height,
                            frame_rate=frame_rate,
                            name=name,
                        )
                    )
                    break

        if not variants:
            raise RuntimeError("HLS master playlist contains no playable variants")
        known_heights = [variant.height for variant in variants if variant.height > 0]
        minimum_eligible = [
            variant
            for variant in variants
            if (
                variant.height == 0
                or variant.height
                >= config.sniffer_minimum_video_height()
            )
        ]
        if known_heights and not minimum_eligible:
            if not self.allow_below_minimum:
                raise RuntimeError(
                    "HLS playlist has no variant at or above the minimum quality "
                    "of {}p".format(
                        config.sniffer_minimum_video_height()
                    )
                )
            best = max(variants, key=lambda variant: variant.sort_key)
            self.logger.warning(
                "HLS has no variant at or above %sp; using best available "
                "quality=%s resolution=%s because auto low-quality fallback "
                "is enabled",
                config.sniffer_minimum_video_height(),
                best.quality_label,
                best.resolution_label,
            )
            return best.url, self._fetch_text(best.url), {
                "quality": best.quality_label,
                "width": best.width or None,
                "height": best.height or None,
                "bandwidth": best.bandwidth or None,
                "frame_rate": best.frame_rate or None,
            }
        preferred_variants = [
            variant
            for variant in minimum_eligible
            if 0 < variant.height <= self.max_height
        ]
        if (
            self.quality_fallback == "strict"
            and self.quality_target != "auto"
        ):
            target_height = self._quality_from_name(
                self.quality_target
            )
            exact_variants = [
                variant
                for variant in minimum_eligible
                if variant.height == target_height
                or (
                    self._quality_from_name(variant.name)
                    == target_height
                )
            ]
            if not exact_variants:
                raise RuntimeError(
                    "HLS playlist has no exact {} variant".format(
                        self.quality_target
                    )
                )
            preferred_variants = exact_variants
        selection_pool = preferred_variants or minimum_eligible
        best = max(selection_pool, key=lambda variant: variant.sort_key)
        self.logger.info(
            "Selected HLS variant for target %sp: %s (%s, %s bps): %s",
            self.max_height,
            best.quality_label,
            best.resolution_label,
            best.bandwidth,
            redact_url(best.url),
        )
        return best.url, self._fetch_text(best.url), {
            "quality": best.quality_label,
            "width": best.width or None,
            "height": best.height or None,
            "bandwidth": best.bandwidth or None,
            "frame_rate": best.frame_rate or None,
        }

    @staticmethod
    def _infer_master_urls(url: str) -> List[str]:
        return resolve_media_profile(url).hls_master_candidates(url)

    @staticmethod
    def _quality_from_url(url: str) -> Optional[int]:
        match = re.search(
            r"(?<![0-9])([0-9]{3,4})p(?:\.|/|-|$)",
            urlparse(url).path,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r"(?<![0-9])[0-9]{3,4}x([0-9]{3,4})(?:\.|/|-|$)",
                urlparse(url).path,
            )
        if not match:
            return None
        return int(match.group(1)) if match else None

    @staticmethod
    def _quality_from_name(name: str) -> int:
        match = re.search(r"([0-9]{3,4})p", name or "", flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    def _parse_media(self, base_url: str, playlist: str) -> List[Tuple[str, str]]:
        result: List[Tuple[str, str]] = []
        for line in playlist.splitlines():
            value = line.strip()
            if value.startswith("#EXT-X-MAP:"):
                attributes = _parse_attributes(value.split(":", 1)[1])
                if attributes.get("URI"):
                    result.append(("init", urljoin(base_url, attributes["URI"])))
            elif value and not value.startswith("#"):
                result.append(("segment", urljoin(base_url, value)))
        return result

    def _fetch_text(self, url: str, attempts: Optional[int] = None) -> str:
        timeout_ms = config.hls_manifest_timeout_seconds() * 1000
        return self._with_retry(
            lambda: self.page.evaluate(
                """
                async ({url, timeoutMs}) => {
                  const controller = new AbortController();
                  const timer = setTimeout(
                    () => controller.abort(),
                    timeoutMs
                  );
                  try {
                    const response = await fetch(url, {
                      signal: controller.signal
                    });
                    if (!response.ok)
                      throw new Error(`HTTP ${response.status}`);
                    return await response.text();
                  } catch (error) {
                    if (error?.name === 'AbortError') {
                      throw new Error(
                        `Request timeout after ${timeoutMs}ms`
                      );
                    }
                    throw error;
                  } finally {
                    clearTimeout(timer);
                  }
                }
                """,
                {"url": url, "timeoutMs": timeout_ms},
            ),
            what="manifest",
            attempts=attempts,
        )

    def _fetch_bytes(self, url: str) -> bytes:
        timeout_ms = config.hls_segment_timeout_seconds() * 1000
        encoded = self._with_retry(
            lambda: self.page.evaluate(
                """
                async ({url, timeoutMs}) => {
                  const controller = new AbortController();
                  const timer = setTimeout(
                    () => controller.abort(),
                    timeoutMs
                  );
                  try {
                    const response = await fetch(url, {
                      signal: controller.signal
                    });
                    if (!response.ok)
                      throw new Error(`HTTP ${response.status}`);
                    const bytes = new Uint8Array(
                      await response.arrayBuffer()
                    );
                    let binary = '';
                    const chunkSize = 0x8000;
                    for (
                      let offset = 0;
                      offset < bytes.length;
                      offset += chunkSize
                    ) {
                      binary += String.fromCharCode.apply(
                        null,
                        bytes.subarray(offset, offset + chunkSize)
                      );
                    }
                    return btoa(binary);
                  } catch (error) {
                    if (error?.name === 'AbortError') {
                      throw new Error(
                        `Request timeout after ${timeoutMs}ms`
                      );
                    }
                    throw error;
                  } finally {
                    clearTimeout(timer);
                  }
                }
                """,
                {"url": url, "timeoutMs": timeout_ms},
            ),
            what="segment",
        )
        return base64.b64decode(encoded, validate=True)

    def _fetch_batch_payload(
        self,
        urls: List[str],
        concurrency: int,
    ) -> List[Dict]:
        timeout_ms = config.hls_segment_timeout_seconds() * 1000
        payload = self.page.evaluate(
            """
            async ({
              urls,
              concurrency,
              timeoutMs,
              maxRetries,
              rateStatuses,
              requestIntervalMs
            }) => {
              const sleep = (delay) => new Promise(
                resolve => setTimeout(resolve, delay)
              );
              const results = new Array(urls.length);
              let nextIndex = 0;
              let nextStartAt = 0;

              const toBase64 = (bytes) => {
                let binary = '';
                const chunkSize = 0x8000;
                for (
                  let offset = 0;
                  offset < bytes.length;
                  offset += chunkSize
                ) {
                  binary += String.fromCharCode.apply(
                    null,
                    bytes.subarray(offset, offset + chunkSize)
                  );
                }
                return btoa(binary);
              };

              const fetchOne = async (index, url) => {
                let lastError = '';
                let lastStatus = 0;
                for (
                  let attempt = 0;
                  attempt <= maxRetries;
                  attempt += 1
                ) {
                  const wait = Math.max(0, nextStartAt - Date.now());
                  if (wait) await sleep(wait);
                  nextStartAt = Date.now() + requestIntervalMs;
                  const controller = new AbortController();
                  const timer = setTimeout(
                    () => controller.abort(),
                    timeoutMs
                  );
                  try {
                    const response = await fetch(url, {
                      signal: controller.signal
                    });
                    if (!response.ok) {
                      const status = response.status;
                      const rateLimited = rateStatuses.includes(status);
                      lastStatus = status;
                      lastError = `HTTP ${status}`;
                      if (rateLimited || (status >= 400 && status < 500)) {
                        return {
                          index,
                          ok: false,
                          status,
                          rate_limited: rateLimited,
                          error: lastError
                        };
                      }
                      throw new Error(lastError);
                    }
                    const bytes = new Uint8Array(
                      await response.arrayBuffer()
                    );
                    if (!bytes.length) {
                      throw new Error('Empty segment response');
                    }
                    return {
                      index,
                      ok: true,
                      status: response.status,
                      size: bytes.length,
                      data: toBase64(bytes)
                    };
                  } catch (error) {
                    lastError = (
                      error?.name === 'AbortError'
                      ? `Request timeout after ${timeoutMs}ms`
                      : String(error?.message || error)
                    );
                    if (attempt < maxRetries) {
                      await sleep(500 * (2 ** attempt));
                    }
                  } finally {
                    clearTimeout(timer);
                  }
                }
                return {
                  index,
                  ok: false,
                  status: lastStatus,
                  rate_limited: false,
                  error: lastError || 'Segment fetch failed'
                };
              };

              const worker = async () => {
                while (nextIndex < urls.length) {
                  const index = nextIndex;
                  nextIndex += 1;
                  results[index] = await fetchOne(index, urls[index]);
                }
              };

              const workerCount = Math.max(
                1,
                Math.min(concurrency, urls.length)
              );
              await Promise.all(
                Array.from({length: workerCount}, () => worker())
              );
              return results;
            }
            """,
            {
                "urls": urls,
                "concurrency": max(
                    1,
                    min(config.hls_max_concurrency(), concurrency),
                ),
                "timeoutMs": timeout_ms,
                "maxRetries": config.hls_segment_retries(),
                "rateStatuses": list(RATE_LIMIT_STATUSES),
                "requestIntervalMs": max(
                    1,
                    int(
                        1000
                        / config.hls_max_requests_per_second()
                    ),
                ),
            },
        )
        if not isinstance(payload, list) or len(payload) != len(urls):
            raise RuntimeError("HLS batch fetch returned an invalid result")
        return sorted(payload, key=lambda item: int(item.get("index", -1)))

    def _register_rate_limit_batch(self, failed: List[Dict]) -> None:
        now = time.monotonic()
        self.rate_limit_events = [
            stamp
            for stamp in self.rate_limit_events
            if now - stamp <= config.hls_rate_limit_window_seconds()
        ]
        self.rate_limit_events.append(now)
        self.last_rate_limit_at = now
        self.concurrency = 1
        self.clean_batches = 0
        event_count = len(self.rate_limit_events)
        statuses = sorted(
            {
                int(item.get("status") or 0)
                for item in failed
                if item.get("rate_limited")
            }
        )
        threshold = config.hls_rate_limit_threshold()
        if event_count >= threshold:
            raise HlsRateLimitPause(
                "CDN rate limit repeated {} times within {} seconds: {}".format(
                    event_count,
                    config.hls_rate_limit_window_seconds(),
                    statuses or "connection reset",
                )
            )
        cooldown = min(
            120,
            config.hls_rate_limit_cooldown_seconds()
            * (2 ** max(0, event_count - 1)),
        )
        self.logger.warning(
            "HLS rate-limit detected statuses=%s events=%s/%s "
            "concurrency=1 cooldown=%ss",
            statuses,
            event_count,
            threshold,
            cooldown,
        )
        time.sleep(cooldown)

    def _record_clean_batch(self) -> None:
        self.clean_batches += 1
        maximum_concurrency = config.hls_max_concurrency()
        if self.concurrency >= maximum_concurrency:
            return

        now = time.monotonic()
        recent_rate_limit = bool(
            self.last_rate_limit_at is not None
            and now - self.last_rate_limit_at
            <= config.hls_rate_limit_window_seconds()
        )
        thresholds = {
            1: 2,
            2: 3,
            3: 6 if self.last_rate_limit_at is not None else 3,
        }
        threshold = thresholds.get(self.concurrency, 3)
        if (
            self.concurrency == 3
            and self.last_rate_limit_at is not None
            and recent_rate_limit
        ):
            self.logger.debug(
                "HLS concurrency held at 3 while rate-limit recovery "
                "window remains active"
            )
            return
        if self.clean_batches < threshold:
            return

        previous = self.concurrency
        self.concurrency = min(maximum_concurrency, previous + 1)
        self.clean_batches = 0
        self.logger.info(
            "HLS concurrency increased %s -> %s clean_batches=%s "
            "rate_limit_recovery=%s",
            previous,
            self.concurrency,
            threshold,
            self.last_rate_limit_at is not None,
        )

    def _fetch_batch_with_retries(
        self,
        urls: List[str],
    ) -> Tuple[List[bytes], bool]:
        resolved: List[Optional[bytes]] = [None] * len(urls)
        remaining = list(enumerate(urls))
        batch_attempt = 0
        had_rate_limit = False
        while remaining:
            try:
                payload = self._fetch_batch_payload(
                    [url for _, url in remaining],
                    self.concurrency,
                )
            except (PlaywrightError, RuntimeError) as exc:
                batch_retries = config.hls_batch_retries()
                if batch_attempt >= batch_retries:
                    raise RuntimeError(
                        "HLS batch fetch failed after {} retries: {}".format(
                            batch_retries,
                            exc,
                        )
                    ) from exc
                batch_attempt += 1
                delay = self.retry_backoff * (2 ** (batch_attempt - 1))
                self.logger.warning(
                    "HLS batch retry %s/%s after browser error: %s",
                    batch_attempt,
                    batch_retries,
                    exc,
                )
                time.sleep(delay)
                continue

            rate_failed = [
                item for item in payload
                if item.get("rate_limited")
            ]
            if rate_failed:
                had_rate_limit = True
                self._register_rate_limit_batch(rate_failed)

            remaining_next = []
            for local_index, item in enumerate(payload):
                original_index, url = remaining[local_index]
                if item.get("ok") and item.get("data"):
                    try:
                        resolved[original_index] = base64.b64decode(
                            item["data"],
                            validate=True,
                        )
                    except (KeyError, ValueError):
                        remaining_next.append((original_index, url))
                elif not item.get("rate_limited"):
                    remaining_next.append((original_index, url))
                else:
                    remaining_next.append((original_index, url))
            remaining = remaining_next
            if not remaining:
                break
            if rate_failed:
                continue
            batch_retries = config.hls_batch_retries()
            if batch_attempt >= batch_retries:
                errors = [
                    "{}: {}".format(
                        item.get("status") or "network",
                        item.get("error") or "failed",
                    )
                    for item in payload
                    if not item.get("ok")
                ]
                raise RuntimeError(
                    "HLS segments failed after {} batch retries: {}".format(
                        batch_retries,
                        "; ".join(errors[:3]),
                    )
                )
            batch_attempt += 1
            delay = self.retry_backoff * (2 ** (batch_attempt - 1))
            self.logger.warning(
                "HLS batch retry %s/%s for %s failed segments",
                batch_attempt,
                batch_retries,
                len(remaining),
            )
            time.sleep(delay)

        return (
            [
                value if value is not None else b""
                for value in resolved
            ],
            batch_attempt == 0 and not had_rate_limit,
        )

    def _with_retry(
        self,
        operation,
        what: str,
        attempts: Optional[int] = None,
    ):
        last_error: Optional[Exception] = None
        total_attempts = max(1, attempts if attempts is not None else self.retries)
        for attempt in range(1, total_attempts + 1):
            try:
                return operation()
            except (PlaywrightError, RuntimeError) as exc:
                last_error = exc
                if attempt < total_attempts:
                    delay = self.retry_backoff * (2 ** (attempt - 1))
                    self.logger.warning(
                        "Browser fetch %s failed (%s/%s); retrying in %.1fs",
                        what,
                        attempt,
                        total_attempts,
                        delay,
                    )
                    time.sleep(delay)
        raise RuntimeError("Browser fetch {} failed: {}".format(what, last_error))

    @staticmethod
    def _duration(playlist: str) -> Optional[float]:
        values = re.findall(r"#EXTINF:([0-9.]+)", playlist)
        if not values:
            return None
        return round(sum(float(value) for value in values), 3)
