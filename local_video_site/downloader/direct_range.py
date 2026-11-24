from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from curl_cffi import requests as curl_requests

from local_video_site.core import config
from .browser_hls import HlsRateLimitPause
from .recovery import MediaRefreshRequired, media_identity
from .sniffer import redact_url


RATE_LIMIT_STATUSES = (403, 429, 451, 503)
CONTENT_RANGE_RE = re.compile(r"bytes\s+([0-9]+)-([0-9]+)/([0-9]+)")


class RangeNotSupported(RuntimeError):
    pass


class RangeRequestError(RuntimeError):
    pass


class RangeRateLimit(RuntimeError):
    def __init__(self, status: int):
        self.status = int(status)
        super().__init__("range request rate limited with HTTP {}".format(status))


def _should_increase_concurrency(
    concurrency: int,
    clean_batches: int,
    recovering: bool,
    recent_rate_limit: bool,
) -> bool:
    thresholds = {
        1: 4,
        2: 6 if recovering else 3,
        3: 12 if recovering else 6,
    }
    threshold = thresholds.get(concurrency)
    return bool(
        threshold
        and clean_batches >= threshold
        and not recent_rate_limit
    )


def _should_use_gateway_slices(
    gateway_error: bool,
    made_progress: bool,
    retries_without_progress: int,
    total_retries: int,
) -> bool:
    return bool(
        (gateway_error and retries_without_progress >= 1)
        or (made_progress and total_retries >= 8)
    )


class _CurlResponseAdapter:
    def __init__(self, response):
        self.response = response
        self.status_code = int(response.status_code)
        self.headers = response.headers

    def iter_bytes(self, chunk_size: int):
        return self.response.iter_content(chunk_size=chunk_size)


class _CurlRangeClient:
    def __init__(self, headers: Dict[str, str], proxy: Optional[str]):
        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}
        self.session = curl_requests.Session(
            headers=headers,
            proxies=proxies,
            impersonate="chrome",
            timeout=30,
            trust_env=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def get(self, url: str):
        response = self.session.get(url, allow_redirects=True)
        return _CurlResponseAdapter(response)

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ):
        if str(method).upper() != "GET":
            raise RangeRequestError(
                "curl_cffi Range transport only supports GET"
            )
        response = self.session.get(
            url,
            headers=headers,
            stream=True,
            allow_redirects=True,
        )
        try:
            yield _CurlResponseAdapter(response)
        finally:
            response.close()

    def close(self):
        self.session.close()


@dataclass(frozen=True)
class RangeChunk:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass
class RangeProbe:
    total_bytes: int
    etag: str
    last_modified: str


class RangeManifest:
    def __init__(
        self,
        path: Path,
        total_bytes: int,
        identity: str,
        etag: str = "",
        chunk_size: Optional[int] = None,
        complete_ranges: Optional[List[List[int]]] = None,
    ):
        self.path = path
        self.total_bytes = int(total_bytes)
        self.identity = identity
        self.etag = etag
        self.chunk_size = int(
            config.range_chunk_size_bytes()
            if chunk_size is None
            else chunk_size
        )
        self.complete_ranges = (
            [list(item) for item in complete_ranges]
            if complete_ranges
            else []
        )

    @classmethod
    def load(cls, path: Path) -> Optional["RangeManifest"]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        try:
            total_bytes = int(payload["total_bytes"])
            identity = str(payload["media_identity"])
        except (KeyError, TypeError, ValueError):
            return None
        ranges = []
        for item in payload.get("complete_ranges") or []:
            if not isinstance(item, list) or len(item) != 2:
                continue
            try:
                start, end = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if 0 <= start <= end < total_bytes:
                ranges.append([start, end])
        return cls(
            path=path,
            total_bytes=total_bytes,
            identity=identity,
            etag=str(payload.get("etag") or ""),
            chunk_size=int(
                payload.get("chunk_size")
                or config.range_chunk_size_bytes()
            ),
            complete_ranges=ranges,
        )

    def save(self) -> None:
        self._merge_ranges()
        payload = {
            "version": 1,
            "total_bytes": self.total_bytes,
            "media_identity": self.identity,
            "etag": self.etag,
            "chunk_size": self.chunk_size,
            "complete_ranges": self.complete_ranges,
            "updated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def add_completed(self, chunk: RangeChunk) -> None:
        self.complete_ranges.append([chunk.start, chunk.end])
        self._merge_ranges()

    def _merge_ranges(self) -> None:
        merged: List[List[int]] = []
        for start, end in sorted(self.complete_ranges):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        self.complete_ranges = merged

    @property
    def completed_bytes(self) -> int:
        return sum(end - start + 1 for start, end in self.complete_ranges)

    @property
    def is_complete(self) -> bool:
        return (
            len(self.complete_ranges) == 1
            and self.complete_ranges[0][0] == 0
            and self.complete_ranges[0][1] == self.total_bytes - 1
        )

    def remaining_chunks(self) -> List[RangeChunk]:
        result = []
        position = (
            self.complete_ranges[0][1] + 1
            if self.complete_ranges and self.complete_ranges[0][0] == 0
            else 0
        )
        while position < self.total_bytes:
            end = min(position + self.chunk_size, self.total_bytes) - 1
            if not self._covered(position, end):
                result.append(RangeChunk(position, end))
            position = end + 1
        return result

    def _covered(self, start: int, end: int) -> bool:
        return any(
            range_start <= start and range_end >= end
            for range_start, range_end in self.complete_ranges
        )


class _ProgressTracker:
    def __init__(self, logger: logging.Logger, total: int, initial: int):
        self.logger = logger
        self.total = max(1, int(total))
        self.downloaded = int(initial)
        self.started = time.monotonic()
        self.last_logged_at = 0.0
        self.speed_started_at = self.started
        self.speed_start_bytes = self.downloaded
        self.connections = 1
        self.lock = threading.Lock()

    def add(self, count: int) -> None:
        with self.lock:
            self.downloaded = min(
                self.total,
                self.downloaded + max(0, int(count)),
            )
        self.maybe_log(self.connections)

    def set_connections(self, count: int) -> None:
        self.connections = max(1, int(count))

    def max_downloaded(self) -> int:
        with self.lock:
            return self.downloaded

    def maybe_log(self, connections: int, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and now - self.last_logged_at
            < config.range_progress_interval_seconds()
        ):
            return
        self.last_logged_at = now
        downloaded = self.max_downloaded()
        elapsed = max(now - self.speed_started_at, 0.001)
        speed = max(0, downloaded - self.speed_start_bytes) / elapsed
        remaining = max(0, self.total - downloaded)
        eta = int(remaining / speed) if speed > 0 else 0
        self.logger.info(
            "Progress %.1f%% | %s / %s | %s/s | ETA %ss",
            downloaded * 100.0 / self.total,
            _format_bytes(downloaded),
            _format_bytes(self.total),
            _format_bytes(speed),
            eta,
        )
        self.logger.info(
            "Direct range batch active=%s downloaded=%s remaining=%s "
            "speed=%s/s ETA=%ss",
            connections,
            _format_bytes(downloaded),
            _format_bytes(remaining),
            _format_bytes(speed),
            eta,
        )


def _format_bytes(value: float) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} TiB".format(size)


def _cookie_header(cookies: List[Dict]) -> str:
    return "; ".join(
        "{}={}".format(cookie.get("name"), cookie.get("value"))
        for cookie in cookies or []
        if cookie.get("name")
    )


def _output_extension(target: str) -> str:
    suffix = Path(urlparse(target).path.rstrip("/")).suffix.lower()
    if suffix in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}:
        return suffix
    return ".mp4"


class DirectRangeDownloader:
    def __init__(
        self,
        output_dir: Path,
        logger: logging.Logger,
        proxy: Optional[str] = None,
        chunk_size: Optional[int] = None,
        initial_connections: Optional[int] = None,
        max_connections: Optional[int] = None,
        refresh_target=None,
        rate_limit_cooldown: Optional[int] = None,
        tls_backend: str = "auto",
    ):
        self.output_dir = output_dir.resolve()
        self.logger = logger
        self.proxy = str(proxy or "").strip() or None
        self.chunk_size = max(
            1024 * 1024,
            int(
                config.range_chunk_size_bytes()
                if chunk_size is None
                else chunk_size
            ),
        )
        configured_initial = config.range_initial_connections()
        requested_initial = (
            configured_initial
            if initial_connections is None
            else int(initial_connections)
        )
        self.initial_connections = max(
            1,
            min(configured_initial, requested_initial),
        )
        configured_maximum = config.range_max_connections()
        requested_maximum = (
            configured_maximum
            if max_connections is None
            else int(max_connections)
        )
        self.max_connections = max(
            self.initial_connections,
            min(configured_maximum, requested_maximum),
        )
        self.refresh_target = refresh_target
        self.rate_limit_cooldown = max(
            0,
            int(
                config.range_rate_limit_cooldown_seconds()
                if rate_limit_cooldown is None
                else rate_limit_cooldown
            ),
        )
        self.tls_backend = str(tls_backend or "auto")
        if self.tls_backend not in {"auto", "httpx", "curl_cffi"}:
            self.tls_backend = "auto"
        self.prefer_curl_cffi = self.tls_backend == "curl_cffi"

    def qualify(
        self,
        target: str,
        headers: Dict[str, str],
        cookies: List[Dict],
    ) -> Dict:
        request_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in {"range", "if-range", "host", "content-length"}
        }
        cookie_value = _cookie_header(cookies)
        if cookie_value:
            request_headers.setdefault("Cookie", cookie_value)
        first = self._probe(target, 0, request_headers)
        if first.total_bytes < config.range_min_file_bytes():
            raise RangeNotSupported("file is below the direct Range threshold")
        offsets = (
            first.total_bytes // 2,
            max(0, first.total_bytes - 1),
        )
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="range-qualify",
        ) as executor:
            probes = list(
                executor.map(
                    lambda offset: self._probe(
                        target,
                        offset,
                        request_headers,
                    ),
                    offsets,
                )
            )
        for probe in probes:
            if probe.total_bytes != first.total_bytes:
                raise RangeNotSupported(
                    "Range qualification total size changed"
                )
            if (
                first.etag
                and probe.etag
                and first.etag != probe.etag
            ):
                raise RangeNotSupported(
                    "Range qualification ETag changed"
                )
        return {
            "status": "passed",
            "total_bytes": first.total_bytes,
            "etag": first.etag,
            "tls_backend": (
                "curl_cffi" if self.prefer_curl_cffi else "httpx"
            ),
        }

    def download(
        self,
        target: str,
        basename: str,
        headers: Dict[str, str],
        cookies: List[Dict],
        media_quality: str = "",
        media_width: Optional[int] = None,
        media_height: Optional[int] = None,
        expires_at: Optional[datetime] = None,
    ) -> Dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        extension = _output_extension(target)
        final_path = self.output_dir / (basename + extension)
        part_path = final_path.with_suffix(final_path.suffix + ".part")
        manifest_path = final_path.with_suffix(
            final_path.suffix + ".range.json"
        )
        identity = media_identity(
            target,
            media_quality,
            media_width,
            media_height,
        )
        request_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in {"range", "if-range", "host", "content-length"}
        }
        cookie_value = _cookie_header(cookies)
        if cookie_value:
            request_headers.setdefault("Cookie", cookie_value)

        prefix_size = part_path.stat().st_size if part_path.is_file() else 0

        def discard_range_state(reason: str) -> int:
            part_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            self.logger.warning(
                "Discarded incompatible Range checkpoint: %s",
                reason,
            )
            return 0

        try:
            probe = self._probe_existing_prefix(
                target,
                prefix_size,
                request_headers,
            )
        except RangeRequestError as exc:
            if "partial file exceeds remote media size" not in str(exc):
                raise
            prefix_size = discard_range_state(str(exc))
            probe = self._probe(target, 0, request_headers)
        if probe.total_bytes < config.range_min_file_bytes():
            raise RangeNotSupported("file is below the direct Range threshold")

        manifest = RangeManifest.load(manifest_path)
        if manifest is not None and prefix_size <= 0:
            prefix_size = discard_range_state(
                "manifest exists without a partial file"
            )
            manifest = None
        if manifest is not None:
            compatible_identities = {
                identity,
                media_identity(target, media_quality),
                media_identity(target),
            }
            if manifest.identity not in compatible_identities:
                same_remote_media = bool(
                    prefix_size > 0
                    and manifest.total_bytes == probe.total_bytes
                    and manifest.etag
                    and probe.etag
                    and manifest.etag == probe.etag
                )
                if not same_remote_media:
                    prefix_size = discard_range_state(
                        "media identity changed manifest={} current={}".format(
                            manifest.identity,
                            identity,
                        )
                    )
                    manifest = None
            if manifest is not None and manifest.identity != identity:
                self.logger.warning(
                    "Range manifest identity upgraded from legacy metadata"
                )
                manifest.identity = identity
            if manifest is not None:
                total_difference = abs(
                    manifest.total_bytes - probe.total_bytes
                )
                if total_difference:
                    etag_compatible = (
                        not manifest.etag
                        or not probe.etag
                        or manifest.etag == probe.etag
                    )
                    if (
                        total_difference
                        <= config.range_manifest_total_tolerance_bytes()
                        and etag_compatible
                    ):
                        self.logger.warning(
                            "Range manifest total corrected by %s bytes "
                            "manifest_etag=%s probe_etag=%s",
                            probe.total_bytes - manifest.total_bytes,
                            manifest.etag or "missing",
                            probe.etag or "missing",
                        )
                        manifest.total_bytes = probe.total_bytes
                        manifest.save()
                    else:
                        prefix_size = discard_range_state(
                            "media total changed manifest={} "
                            "current={} difference={}".format(
                                manifest.total_bytes,
                                probe.total_bytes,
                                total_difference,
                            )
                        )
                        manifest = None
            if manifest is not None:
                manifest.etag = manifest.etag or probe.etag
                manifest.chunk_size = self.chunk_size
        if manifest is None:
            complete_ranges = (
                [[0, min(prefix_size, probe.total_bytes) - 1]]
                if prefix_size > 0
                else []
            )
            manifest = RangeManifest(
                path=manifest_path,
                total_bytes=probe.total_bytes,
                identity=identity,
                etag=probe.etag,
                chunk_size=self.chunk_size,
                complete_ranges=complete_ranges,
            )
            manifest.save()
        with part_path.open("ab") as output:
            output.truncate(prefix_size)

        total_chunks = max(
            1,
            (probe.total_bytes + self.chunk_size - 1) // self.chunk_size,
        )
        pending = manifest.remaining_chunks()
        completed_chunks = total_chunks - len(pending)
        self.logger.info(
            "Direct range initialized connections=%s chunk=%s "
            "total=%s prefix=%s completed_chunks=%s total_chunks=%s",
            self.initial_connections,
            _format_bytes(self.chunk_size),
            _format_bytes(probe.total_bytes),
            _format_bytes(manifest.completed_bytes),
            completed_chunks,
            total_chunks,
        )

        if pending:
            self._download_chunks(
                target=target,
                headers=request_headers,
                part_path=part_path,
                manifest=manifest,
                pending=pending,
                expires_at=expires_at,
                quality=media_quality,
                width=media_width,
                height=media_height,
            )

        if not manifest.is_complete:
            raise RangeRequestError("direct Range download is incomplete")
        if part_path.stat().st_size != manifest.total_bytes:
            with part_path.open("r+b") as output:
                output.truncate(manifest.total_bytes)
        with part_path.open("rb") as output:
            os.fsync(output.fileno())
        os.replace(part_path, final_path)
        manifest_path.unlink(missing_ok=True)
        self.logger.info("Direct range download complete: %s", final_path)
        return {
            "path": final_path,
            "info": {
                "title": basename,
                "quality": media_quality,
                "width": media_width,
                "height": media_height,
                "requested_downloads": [{"filepath": str(final_path)}],
            },
            "attempt": 1,
            "size": final_path.stat().st_size,
        }

    def _probe_existing_prefix(
        self,
        target: str,
        prefix_size: int,
        headers: Dict[str, str],
    ) -> RangeProbe:
        if prefix_size <= 0:
            return self._probe(target, 0, headers)
        # A zero-length probe at EOF is valid HTTP but commonly rejected with
        # 416 because the requested start equals the file length. Probing the
        # last existing byte still returns the full Content-Range total and
        # lets sparse manifests continue only their missing chunks.
        try:
            return self._probe(target, prefix_size - 1, headers)
        except RangeNotSupported as exc:
            if "HTTP 416" not in str(exc):
                raise
            first_byte = self._probe(target, 0, headers)
            if prefix_size > first_byte.total_bytes:
                raise RangeRequestError(
                    "partial file exceeds remote media size: "
                    "partial={} remote={}".format(
                        prefix_size,
                        first_byte.total_bytes,
                    )
                ) from exc
            return first_byte

    def _probe(
        self,
        target: str,
        offset: int,
        headers: Dict[str, str],
    ) -> RangeProbe:
        request_headers = dict(headers)
        request_headers["Range"] = "bytes={}-{}".format(offset, offset)
        try:
            with self._client(request_headers) as client:
                response = client.get(target)
        except httpx.HTTPError as exc:
            if (
                not self._is_tls_failure(exc)
                or self.tls_backend == "httpx"
            ):
                raise RangeNotSupported(
                    "Range probe failed: {}".format(exc)
                ) from exc
            self.prefer_curl_cffi = True
            self.logger.warning(
                "Direct range switched to curl_cffi Chrome TLS "
                "after probe failure: %s",
                exc,
            )
            try:
                with self._client(request_headers) as client:
                    response = client.get(target)
            except Exception as curl_exc:
                raise RangeNotSupported(
                    "Range probe failed with both HTTP transports: {}".format(
                        curl_exc
                    )
                ) from curl_exc
        if response.status_code in (401, 403, 410):
            raise MediaRefreshRequired(
                "Range probe returned HTTP {}".format(response.status_code)
            )
        if response.status_code == 200:
            raise RangeNotSupported("server ignored Range probe")
        if response.status_code != 206:
            raise RangeNotSupported(
                "Range probe returned HTTP {}".format(response.status_code)
            )
        match = CONTENT_RANGE_RE.search(
            response.headers.get("content-range", "")
        )
        if not match:
            raise RangeNotSupported("Range probe omitted Content-Range")
        start, _, total = (int(value) for value in match.groups())
        if start != offset:
            raise RangeNotSupported("Range probe returned a different offset")
        return RangeProbe(
            total_bytes=total,
            etag=response.headers.get("etag", ""),
            last_modified=response.headers.get("last-modified", ""),
        )

    @staticmethod
    def _is_tls_failure(error) -> bool:
        text = str(error or "").lower()
        return any(
            marker in text
            for marker in (
                "tls",
                "ssl",
                "protocol version",
                "handshake",
            )
        )

    def _client(self, headers: Dict[str, str]):
        if self.prefer_curl_cffi:
            return _CurlRangeClient(headers, self.proxy)
        return httpx.Client(
            headers=headers,
            proxy=self.proxy,
            timeout=httpx.Timeout(30.0, read=30.0),
            follow_redirects=True,
            trust_env=False,
            http1=True,
            http2=False,
            limits=httpx.Limits(
                max_connections=1,
                max_keepalive_connections=1,
            ),
        )

    def _download_chunks(
        self,
        target: str,
        headers: Dict[str, str],
        part_path: Path,
        manifest: RangeManifest,
        pending: List[RangeChunk],
        expires_at: Optional[datetime],
        quality: str,
        width: Optional[int],
        height: Optional[int],
    ) -> None:
        total_chunks = max(
            1,
            (manifest.total_bytes + self.chunk_size - 1) // self.chunk_size,
        )
        concurrency = self.initial_connections
        clean_batches = 0
        rate_limit_events: List[float] = []
        last_rate_limit_at = None
        refresh_count = 0
        tracker = _ProgressTracker(
            self.logger,
            manifest.total_bytes,
            manifest.completed_bytes,
        )
        tracker.set_connections(concurrency)
        thread_state = threading.local()
        clients: List[object] = []
        clients_lock = threading.Lock()

        def worker(chunk: RangeChunk):
            client = getattr(thread_state, "client", None)
            if client is None:
                client = self._client(headers)
                thread_state.client = client
                with clients_lock:
                    clients.append(client)
            return self._download_chunk(
                client=client,
                target=target,
                chunk=chunk,
                headers=headers,
                part_path=part_path,
                tracker=tracker,
            )

        pending_iter = iter(pending)
        in_flight = {}
        completed_chunks = total_chunks - len(pending)
        try:
            with ThreadPoolExecutor(
                max_workers=self.max_connections,
                thread_name_prefix="direct-range",
            ) as executor:
                while True:
                    now = time.time()
                    if (
                        expires_at is not None
                        and now >= expires_at.timestamp() - 300
                        and self.refresh_target is not None
                        and refresh_count < 3
                    ):
                        refresh_count += 1
                        target, headers, expires_at = self._refresh(
                            target,
                            headers,
                            expires_at,
                            quality,
                            width,
                            height,
                            manifest,
                            refresh_count,
                        )
                        for client in clients:
                            client.headers.update(headers)
                        last_rate_limit_at = None

                    while len(in_flight) < concurrency:
                        try:
                            chunk = next(pending_iter)
                        except StopIteration:
                            break
                        future = executor.submit(worker, chunk)
                        in_flight[future] = chunk

                    if not in_flight:
                        break
                    done, _ = wait(
                        tuple(in_flight),
                        return_when=FIRST_COMPLETED,
                    )
                    retry_chunks = []
                    refreshed_this_batch = False
                    rate_limited_this_batch = False
                    for future in done:
                        chunk = in_flight.pop(future)
                        try:
                            future.result()
                        except MediaRefreshRequired as exc:
                            if (
                                self.refresh_target is None
                                or refresh_count >= 3
                            ):
                                raise
                            if not refreshed_this_batch:
                                refresh_count += 1
                                target, headers, expires_at = self._refresh(
                                    target,
                                    headers,
                                    expires_at,
                                    quality,
                                    width,
                                    height,
                                    manifest,
                                    refresh_count,
                                )
                                refreshed_this_batch = True
                            retry_chunks.append(chunk)
                            clean_batches = 0
                            continue
                        except RangeRateLimit as exc:
                            if rate_limited_this_batch:
                                retry_chunks.append(chunk)
                                continue
                            rate_limited_this_batch = True
                            now_limit = time.monotonic()
                            rate_limit_events = [
                                stamp
                                for stamp in rate_limit_events
                                if (
                                    now_limit - stamp
                                    <= config.range_rate_limit_window_seconds()
                                )
                            ]
                            rate_limit_events.append(now_limit)
                            last_rate_limit_at = now_limit
                            concurrency = 1
                            tracker.set_connections(concurrency)
                            clean_batches = 0
                            retry_chunks.append(chunk)
                            if (
                                len(rate_limit_events)
                                >= config.range_rate_limit_threshold()
                            ):
                                raise HlsRateLimitPause(
                                    "direct Range CDN rate limit repeated"
                                ) from exc
                            self.logger.warning(
                                "Direct range rate limited status=%s "
                                "connections=1 cooldown=%ss",
                                exc.status,
                                self.rate_limit_cooldown,
                            )
                            if self.rate_limit_cooldown:
                                time.sleep(self.rate_limit_cooldown)
                            continue

                        manifest.add_completed(chunk)
                        manifest.save()
                        completed_chunks += 1
                        clean_batches += 1
                        recovering = last_rate_limit_at is not None
                        recent_rate_limit = bool(
                            recovering
                            and time.monotonic() - last_rate_limit_at
                            <= config.range_rate_limit_window_seconds()
                        )
                        if _should_increase_concurrency(
                            concurrency,
                            clean_batches,
                            recovering,
                            recent_rate_limit,
                        ):
                            previous = concurrency
                            concurrency = min(
                                self.max_connections,
                                concurrency + 1,
                            )
                            tracker.set_connections(concurrency)
                            clean_batches = 0
                            self.logger.info(
                                "Direct range concurrency increased %s -> %s",
                                previous,
                                concurrency,
                            )
                        tracker.maybe_log(concurrency)
                        self.logger.info(
                            "Direct range chunk complete completed=%s/%s "
                            "connections=%s",
                            completed_chunks,
                            total_chunks,
                            concurrency,
                        )

                    if retry_chunks:
                        pending_iter = iter(
                            retry_chunks + list(pending_iter)
                        )
                tracker.maybe_log(concurrency, force=True)
        finally:
            for client in clients:
                try:
                    client.close()
                except Exception:
                    pass

    def _download_chunk(
        self,
        client: httpx.Client,
        target: str,
        chunk: RangeChunk,
        headers: Dict[str, str],
        part_path: Path,
        tracker: _ProgressTracker,
    ) -> None:
        position = chunk.start
        retries_without_progress = 0
        total_retries = 0
        use_gateway_slices = False
        while position <= chunk.end:
            request_position = position
            request_end = (
                min(
                    position
                    + config.range_gateway_slice_bytes()
                    - 1,
                    chunk.end,
                )
                if use_gateway_slices
                else chunk.end
            )
            request_headers = dict(headers)
            request_headers["Range"] = "bytes={}-{}".format(
                position,
                request_end,
            )
            try:
                with client.stream(
                    "GET",
                    target,
                    headers=request_headers,
                ) as response:
                    if response.status_code in (401, 403, 410):
                        raise MediaRefreshRequired(
                            "Range returned HTTP {}".format(
                                response.status_code
                            )
                        )
                    if response.status_code in RATE_LIMIT_STATUSES:
                        raise RangeRateLimit(response.status_code)
                    if response.status_code == 404:
                        raise MediaRefreshRequired(
                            "Range returned HTTP 404"
                        )
                    if response.status_code != 206:
                        raise RangeRequestError(
                            "expected HTTP 206, received {}".format(
                                response.status_code
                            )
                        )
                    match = CONTENT_RANGE_RE.search(
                        response.headers.get("content-range", "")
                    )
                    if not match:
                        raise RangeRequestError(
                            "Range response omitted Content-Range"
                        )
                    start, end, _ = (
                        int(value) for value in match.groups()
                    )
                    if start != position or end != request_end:
                        raise RangeRequestError(
                            "unexpected Content-Range: {} != {}"
                            .format(
                                response.headers.get("content-range"),
                                "bytes {}-{}".format(position, request_end),
                            )
                        )
                    with part_path.open("r+b") as output:
                        for data in response.iter_bytes(1024 * 1024):
                            remaining = request_end - position + 1
                            if remaining <= 0:
                                break
                            payload = data[:remaining]
                            output.seek(position)
                            output.write(payload)
                            position += len(payload)
                            tracker.add(len(payload))
                        output.flush()
                        if position == request_end + 1:
                            os.fsync(output.fileno())
                    if position == request_end + 1:
                        if request_end == chunk.end:
                            return
                        total_retries = 0
                        retries_without_progress = 0
                        continue
                    raise RangeRequestError("Range response ended early")
            except (MediaRefreshRequired, RangeRateLimit):
                raise
            except (
                httpx.HTTPError,
                curl_requests.RequestsError,
                OSError,
                RangeRequestError,
            ) as exc:
                made_progress = position > request_position
                retries_without_progress = (
                    0 if made_progress else retries_without_progress + 1
                )
                total_retries += 1
                error_text = str(exc).lower()
                gateway_error = (
                    "received 502" in error_text
                    or "received 504" in error_text
                )
                if _should_use_gateway_slices(
                    gateway_error=gateway_error,
                    made_progress=made_progress,
                    retries_without_progress=retries_without_progress,
                    total_retries=total_retries,
                ):
                    use_gateway_slices = True
                no_progress_limit = (
                    config.range_transient_gateway_retries()
                    if gateway_error
                    else config.range_chunk_retries()
                )
                if (
                    retries_without_progress > no_progress_limit
                    or total_retries
                    > config.range_max_total_retries()
                ):
                    raise RangeRequestError(
                        "Range chunk failed after retries: {}".format(exc)
                    ) from exc
                delay = (
                    0.25
                    if made_progress
                    else 2 ** min(retries_without_progress, 4)
                )
                self.logger.warning(
                    "Direct range chunk retry %s/%s offset=%s "
                    "no_progress=%s progress=%s error=%s",
                    total_retries,
                    config.range_max_total_retries(),
                    position,
                    retries_without_progress,
                    position - request_position,
                    exc,
                )
                time.sleep(delay)

    def _refresh(
        self,
        target: str,
        headers: Dict[str, str],
        expires_at: Optional[datetime],
        quality: str,
        width: Optional[int],
        height: Optional[int],
        manifest: RangeManifest,
        refresh_count: int,
    ) -> Tuple[str, Dict[str, str], Optional[datetime]]:
        refreshed = self.refresh_target(
            "direct-range-media-url-expired",
            expires_at,
        )
        new_target = str((refreshed or {}).get("target") or "").strip()
        if not new_target:
            raise MediaRefreshRequired(
                "direct Range media URL refresh returned no target"
            )
        new_identity = media_identity(
            new_target,
            quality,
            width,
            height,
        )
        if new_identity != manifest.identity:
            raise RangeRequestError(
                "refreshed direct Range media identity changed"
            )
        new_headers = dict(
            (refreshed or {}).get("headers") or headers
        )
        probe_offset = min(
            manifest.completed_bytes,
            max(0, manifest.total_bytes - 1),
        )
        probe = self._probe(
            new_target,
            probe_offset,
            new_headers,
        )
        if probe.total_bytes != manifest.total_bytes:
            raise RangeRequestError(
                "refreshed direct Range media size changed"
            )
        if (
            manifest.etag
            and probe.etag
            and manifest.etag != probe.etag
        ):
            raise RangeRequestError(
                "refreshed direct Range ETag changed"
            )
        self.logger.info(
            "Direct range media URL refreshed count=%s target=%s",
            refresh_count,
            redact_url(new_target),
        )
        return (
            new_target,
            new_headers,
            (refreshed or {}).get("expires_at"),
        )
