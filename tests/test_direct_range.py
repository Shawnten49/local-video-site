import json
import logging
import re
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import httpx

from local_video_site.core import config
from local_video_site.downloader import direct_range
from local_video_site.downloader.direct_range import (
    _ProgressTracker,
    _should_increase_concurrency,
    _should_use_gateway_slices,
    DirectRangeDownloader,
    RangeNotSupported,
)
from local_video_site.downloader.recovery import media_identity


class MockRangeClient(DirectRangeDownloader):
    def __init__(self, *args, body: bytes, **kwargs):
        super().__init__(*args, **kwargs)
        self.body = body
        self.requests = []

    def _client(self, headers):
        def handler(request):
            range_value = request.headers.get("range", "")
            self.requests.append(range_value)
            match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", range_value)
            if not match:
                return httpx.Response(400)
            start, end = (int(value) for value in match.groups())
            payload = self.body[start:end + 1]
            return httpx.Response(
                206,
                headers={
                    "Content-Range": "bytes {}-{}/{}".format(
                        start,
                        end,
                        len(self.body),
                    ),
                    "ETag": '"fixture-etag"',
                },
                content=payload,
            )

        return httpx.Client(
            headers=headers,
            transport=httpx.MockTransport(handler),
        )


class DirectRangeTests(unittest.TestCase):
    def setUp(self):
        self.body = bytes(range(256)) * (3 * 4096 + 17)

    def test_downloads_with_parallel_ranges_and_finalizes_file(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            downloader = MockRangeClient(
                output_dir=output,
                logger=logging.getLogger("direct-range-test"),
                body=self.body,
                chunk_size=1024 * 1024,
                initial_connections=2,
                max_connections=3,
            )
            result = downloader.download(
                "https://cdn.example/video.mp4",
                "video",
                headers={"Referer": "https://example.com/"},
                cookies=[],
                media_quality="720p",
                media_width=1280,
                media_height=720,
            )

            self.assertEqual(result["path"].read_bytes(), self.body)
            self.assertFalse((output / "video.mp4.part").exists())
            self.assertFalse((output / "video.mp4.range.json").exists())
            self.assertGreater(len(downloader.requests), 2)

    def test_streaming_bytes_emit_progress_before_chunk_completion(self):
        logger = logging.getLogger("direct-range-stream-progress")
        with patch.object(direct_range.time, "monotonic", return_value=10.0):
            tracker = _ProgressTracker(logger, total=1000, initial=0)

            with self.assertLogs(logger, level="INFO") as captured:
                tracker.add(1)

        self.assertTrue(
            any("Progress 0.1%" in line for line in captured.output)
        )

    def test_recent_rate_limit_keeps_single_connection(self):
        self.assertFalse(
            _should_increase_concurrency(
                concurrency=1,
                clean_batches=100,
                recovering=True,
                recent_rate_limit=True,
            )
        )
        self.assertTrue(
            _should_increase_concurrency(
                concurrency=1,
                clean_batches=6,
                recovering=True,
                recent_rate_limit=False,
            )
        )

    def test_progressive_early_eof_switches_to_small_slices(self):
        self.assertTrue(
            _should_use_gateway_slices(
                gateway_error=False,
                made_progress=True,
                retries_without_progress=0,
                total_retries=8,
            )
        )
        self.assertFalse(
            _should_use_gateway_slices(
                gateway_error=False,
                made_progress=True,
                retries_without_progress=0,
                total_retries=7,
            )
        )
        self.assertTrue(
            _should_use_gateway_slices(
                gateway_error=True,
                made_progress=False,
                retries_without_progress=1,
                total_retries=1,
            )
        )

    def test_existing_partial_prefix_is_reused(self):
        prefix = 700_000
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            (output / "video.mp4.part").write_bytes(self.body[:prefix])
            downloader = MockRangeClient(
                output_dir=output,
                logger=logging.getLogger("direct-range-resume-test"),
                body=self.body,
                chunk_size=512 * 1024,
                initial_connections=2,
                max_connections=2,
            )
            result = downloader.download(
                "https://cdn.example/video.mp4",
                "video",
                headers={},
                cookies=[],
            )

            self.assertEqual(result["path"].read_bytes(), self.body)
            self.assertIn(
                "bytes={}-{}".format(prefix - 1, prefix - 1),
                downloader.requests,
            )
            self.assertFalse(
                any(value.startswith("bytes=0-") for value in downloader.requests)
            )

    def test_rotated_media_identity_resumes_when_etag_and_size_match(self):
        prefix = 700_000
        old_target = "https://cdn.example/old-signature/video.mp4"
        new_target = "https://cdn.example/new-signature/video.mp4"
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            (output / "video.mp4.part").write_bytes(self.body[:prefix])
            (output / "video.mp4.range.json").write_text(
                json.dumps({
                    "version": 1,
                    "total_bytes": len(self.body),
                    "media_identity": media_identity(old_target),
                    "etag": '"fixture-etag"',
                    "chunk_size": 512 * 1024,
                    "complete_ranges": [[0, prefix - 1]],
                }),
                encoding="utf-8",
            )
            downloader = MockRangeClient(
                output_dir=output,
                logger=logging.getLogger("direct-range-identity-test"),
                body=self.body,
                chunk_size=512 * 1024,
                initial_connections=1,
                max_connections=1,
            )

            result = downloader.download(
                new_target,
                "video",
                headers={},
                cookies=[],
            )

            self.assertEqual(result["path"].read_bytes(), self.body)
            self.assertFalse(
                any(value.startswith("bytes=0-") for value in downloader.requests)
            )

    def test_stale_partial_larger_than_remote_is_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            (output / "video.mp4.part").write_bytes(
                self.body + b"stale-data"
            )
            (output / "video.mp4.range.json").write_text(
                json.dumps({
                    "version": 1,
                    "total_bytes": len(self.body) + 10,
                    "media_identity": media_identity(
                        "https://cdn.example/old-video.mp4"
                    ),
                    "etag": '"old-etag"',
                    "chunk_size": 512 * 1024,
                    "complete_ranges": [[0, len(self.body) + 9]],
                }),
                encoding="utf-8",
            )
            downloader = MockRangeClient(
                output_dir=output,
                logger=logging.getLogger("direct-range-stale-test"),
                body=self.body,
                chunk_size=512 * 1024,
                initial_connections=1,
                max_connections=1,
            )

            result = downloader.download(
                "https://cdn.example/video.mp4",
                "video",
                headers={},
                cookies=[],
            )

            self.assertEqual(result["path"].read_bytes(), self.body)
            self.assertTrue(
                any(
                    value.startswith("bytes=0-")
                    for value in downloader.requests
                )
            )

    def test_eof_sized_sparse_part_resumes_only_missing_range(self):
        target = "https://cdn.example/video.mp4"
        chunk_size = 1024
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            total = len(self.body)
            missing_start = chunk_size
            missing_end = min(total, missing_start + chunk_size) - 1
            complete_ranges = [
                [0, missing_start - 1],
                [missing_end + 1, total - 1],
            ]
            partial = bytearray(b"\x00" * total)
            for start, end in complete_ranges:
                partial[start:end + 1] = self.body[start:end + 1]
            (output / "video.mp4.part").write_bytes(partial)
            (output / "video.mp4.range.json").write_text(
                json.dumps({
                    "version": 1,
                    "total_bytes": total,
                    "media_identity": media_identity(
                        target,
                        "720p",
                        None,
                        720,
                    ),
                    "etag": '"fixture-etag"',
                    "chunk_size": chunk_size,
                    "complete_ranges": complete_ranges,
                }),
                encoding="utf-8",
            )
            downloader = MockRangeClient(
                output_dir=output,
                logger=logging.getLogger("direct-range-eof-resume-test"),
                body=self.body,
                chunk_size=chunk_size,
                initial_connections=1,
                max_connections=1,
            )

            result = downloader.download(
                target,
                "video",
                headers={},
                cookies=[],
                media_quality="720p",
                media_height=720,
            )

            self.assertEqual(result["path"].read_bytes(), self.body)
            self.assertIn(
                "bytes={}-{}".format(total - 1, total - 1),
                downloader.requests,
            )
            self.assertNotIn(
                "bytes={}-{}".format(total, total),
                downloader.requests,
            )

    def test_matching_etag_allows_small_manifest_total_correction(self):
        prefix = 700_000
        target = "https://cdn.example/video.mp4"
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            (output / "video.mp4.part").write_bytes(self.body[:prefix])
            (output / "video.mp4.range.json").write_text(
                json.dumps({
                    "version": 1,
                    "total_bytes": len(self.body) - 1024,
                    "media_identity": media_identity(
                        target,
                        "720p",
                    ),
                    "etag": '"fixture-etag"',
                    "chunk_size": 512 * 1024,
                    "complete_ranges": [[0, prefix - 1]],
                }),
                encoding="utf-8",
            )
            downloader = MockRangeClient(
                output_dir=output,
                logger=logging.getLogger("direct-range-manifest-total-test"),
                body=self.body,
                chunk_size=512 * 1024,
                initial_connections=1,
                max_connections=1,
            )
            result = downloader.download(
                target,
                "video",
                headers={},
                cookies=[],
                media_quality="720p",
                media_height=720,
            )

            self.assertEqual(result["path"].read_bytes(), self.body)

    def test_truncated_range_response_resumes_from_received_offset(self):
        body = bytes(range(256)) * (8 * 1024 + 17)
        truncated = {"done": False}

        class TruncatingDownloader(DirectRangeDownloader):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.requests = []

            def _client(self, headers):
                def handler(request):
                    range_value = request.headers.get("range", "")
                    self.requests.append(range_value)
                    match = re.fullmatch(
                        r"bytes=([0-9]+)-([0-9]+)",
                        range_value,
                    )
                    if not match:
                        return httpx.Response(400)
                    start, end = (int(value) for value in match.groups())
                    payload = body[start:end + 1]
                    if (
                        start == 0
                        and end > start
                        and not truncated["done"]
                    ):
                        truncated["done"] = True
                        payload = payload[:170_000]
                    return httpx.Response(
                        206,
                        headers={
                            "Content-Range": "bytes {}-{}/{}".format(
                                start,
                                end,
                                len(body),
                            ),
                            "ETag": '"truncated-fixture"',
                        },
                        content=payload,
                    )

                return httpx.Client(
                    headers=headers,
                    transport=httpx.MockTransport(handler),
                )

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            downloader = TruncatingDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("direct-range-truncated-test"),
                chunk_size=1024 * 1024,
                initial_connections=1,
                max_connections=1,
            )
            result = downloader.download(
                "https://cdn.example/video.mp4",
                "video",
                headers={},
                cookies=[],
            )
            self.assertEqual(result["path"].read_bytes(), body)
            self.assertIn(
                "bytes=170000-{}".format(1024 * 1024 - 1),
                downloader.requests,
            )

    def test_transient_502_retries_beyond_default_no_progress_limit(self):
        body = bytes(range(256)) * (8 * 1024 + 17)
        gateway_failures = {"count": 0}

        class GatewayRetryDownloader(DirectRangeDownloader):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.requests = []

            def _client(self, headers):
                def handler(request):
                    range_value = request.headers.get("range", "")
                    self.requests.append(range_value)
                    match = re.fullmatch(
                        r"bytes=([0-9]+)-([0-9]+)",
                        range_value,
                    )
                    if not match:
                        return httpx.Response(400)
                    start, end = (int(value) for value in match.groups())
                    if (
                        start == 0
                        and end > start
                        and gateway_failures["count"] < 3
                    ):
                        gateway_failures["count"] += 1
                        return httpx.Response(502)
                    return httpx.Response(
                        206,
                        headers={
                            "Content-Range": "bytes {}-{}/{}".format(
                                start,
                                end,
                                len(body),
                            ),
                            "ETag": '"gateway-fixture"',
                        },
                        content=body[start:end + 1],
                    )

                return httpx.Client(
                    headers=headers,
                    transport=httpx.MockTransport(handler),
                )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(config, "range_min_file_bytes", return_value=0),
            patch.object(direct_range.time, "sleep", return_value=None),
        ):
            downloader = GatewayRetryDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("direct-range-502-test"),
                chunk_size=1024 * 1024,
                initial_connections=1,
                max_connections=1,
            )
            result = downloader.download(
                "https://cdn.example/video.mp4",
                "video",
                headers={},
                cookies=[],
            )
            self.assertEqual(result["path"].read_bytes(), body)

        self.assertEqual(gateway_failures["count"], 3)

    def test_persistent_full_range_502_recovers_with_smaller_slices(self):
        body = bytes(range(256)) * (24 * 1024 + 17)
        sliced_requests = {"count": 0}

        class GatewaySliceDownloader(DirectRangeDownloader):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.requests = []

            def _client(self, headers):
                def handler(request):
                    range_value = request.headers.get("range", "")
                    self.requests.append(range_value)
                    match = re.fullmatch(
                        r"bytes=([0-9]+)-([0-9]+)",
                        range_value,
                    )
                    if not match:
                        return httpx.Response(400)
                    start, end = (int(value) for value in match.groups())
                    if end - start + 1 > 3 * 1024 * 1024:
                        return httpx.Response(502)
                    sliced_requests["count"] += 1
                    return httpx.Response(
                        206,
                        headers={
                            "Content-Range": "bytes {}-{}/{}".format(
                                start,
                                end,
                                len(body),
                            ),
                            "ETag": '"gateway-slice-fixture"',
                        },
                        content=body[start:end + 1],
                    )

                return httpx.Client(
                    headers=headers,
                    transport=httpx.MockTransport(handler),
                )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(config, "range_min_file_bytes", return_value=0),
            patch.object(
                config,
                "range_gateway_slice_bytes",
                return_value=1024 * 1024,
            ),
            patch.object(direct_range.time, "sleep", return_value=None),
        ):
            downloader = GatewaySliceDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("direct-range-slice-test"),
                chunk_size=8 * 1024 * 1024,
                initial_connections=1,
                max_connections=1,
            )
            result = downloader.download(
                "https://cdn.example/video.mp4",
                "video",
                headers={},
                cookies=[],
            )

            self.assertEqual(result["path"].read_bytes(), body)
            self.assertGreater(sliced_requests["count"], 1)

    def test_non_range_server_raises_fallback_signal(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            downloader = MockRangeClient(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("direct-range-fallback-test"),
                body=self.body,
            )

            def no_range(request):
                return httpx.Response(200, content=self.body)

            def client(_headers):
                return httpx.Client(
                    transport=httpx.MockTransport(no_range)
                )

            downloader._client = client
            with self.assertRaises(RangeNotSupported):
                downloader.download(
                    "https://cdn.example/video.mp4",
                    "video",
                    headers={},
                    cookies=[],
                )

    def test_tls_probe_failure_switches_to_curl_cffi_backend(self):
        class FailingClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, target):
                raise httpx.ConnectError(
                    "TLSV1_ALERT_PROTOCOL_VERSION tls protocol version"
                )

        class WorkingClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, target):
                return SimpleNamespace(
                    status_code=206,
                    headers={
                        "content-range": "bytes 0-0/1234",
                        "etag": '"etag"',
                        "last-modified": "",
                    },
                )

        class FallbackDownloader(DirectRangeDownloader):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.clients = [FailingClient(), WorkingClient()]

            def _client(self, headers):
                return self.clients.pop(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = FallbackDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("direct-range-tls-fallback"),
            )
            probe = downloader._probe(
                "https://cdn.example/video.mp4",
                0,
                {},
            )

        self.assertTrue(downloader.prefer_curl_cffi)
        self.assertEqual(probe.total_bytes, 1234)

    def test_expired_range_url_is_refreshed_and_manifest_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            config,
            "range_min_file_bytes",
            return_value=0,
        ):
            output = Path(temp_dir)
            downloader = DirectRangeDownloader(
                output_dir=output,
                logger=logging.getLogger("direct-range-refresh-test"),
                chunk_size=512 * 1024,
                refresh_target=lambda reason, expires: {
                    "target": "https://new.example/video.mp4",
                    "headers": {"Referer": "https://example.com/"},
                    "expires_at": None,
                },
            )
            state = {"old_requests": 0}

            def handler(request):
                if request.url.host == "old.example":
                    state["old_requests"] += 1
                    if state["old_requests"] > 1:
                        return httpx.Response(410)
                range_value = request.headers.get("range", "")
                match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", range_value)
                if not match:
                    return httpx.Response(400)
                start, end = (int(value) for value in match.groups())
                return httpx.Response(
                    206,
                    headers={
                        "Content-Range": "bytes {}-{}/{}".format(
                            start,
                            end,
                            len(self.body),
                        ),
                        "ETag": '"same-media"',
                    },
                    content=self.body[start:end + 1],
                )

            downloader._client = lambda headers: httpx.Client(
                headers=headers,
                transport=httpx.MockTransport(handler),
            )
            result = downloader.download(
                "https://old.example/video.mp4",
                "video",
                headers={},
                cookies=[],
            )

            self.assertEqual(result["path"].read_bytes(), self.body)
            self.assertFalse((output / "video.mp4.range.json").exists())


if __name__ == "__main__":
    unittest.main()
