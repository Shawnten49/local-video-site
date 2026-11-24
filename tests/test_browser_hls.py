import base64
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.downloader.browser_hls import (
    BrowserHlsDownloader,
    HlsPlaylistUnsupported,
    HlsRateLimitPause,
)


class _FakeHlsDownloader(BrowserHlsDownloader):
    def __init__(self, *args, **kwargs):
        super().__init__(page=None, *args, **kwargs)
        self.texts = {}
        self.bodies = {}

    def _fetch_text(self, url, attempts=None):
        if url not in self.texts:
            raise RuntimeError("manifest not found")
        return self.texts[url]

    def _fetch_bytes(self, url):
        if url not in self.bodies:
            raise RuntimeError("segment not found")
        return self.bodies[url]

    def _fetch_batch_with_retries(self, urls):
        return ([self._fetch_bytes(url) for url in urls], True)


class BrowserHlsTests(unittest.TestCase):
    def test_concurrency_adapts_and_repeated_rate_limits_pause(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = BrowserHlsDownloader(
                page=None,
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-rate-test"),
            )
            self.assertEqual(downloader.concurrency, 2)
            downloader._record_clean_batch()
            downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 2)
            downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 3)
            downloader._record_clean_batch()
            downloader._record_clean_batch()
            downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 4)
            downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 4)

            failed = [{"rate_limited": True, "status": 429}]
            with patch("local_video_site.downloader.browser_hls.time.sleep"):
                downloader._register_rate_limit_batch(failed)
                self.assertEqual(downloader.concurrency, 1)
                downloader._register_rate_limit_batch(failed)
                with self.assertRaises(HlsRateLimitPause):
                    downloader._register_rate_limit_batch(failed)

            downloader.rate_limit_events = [
                time.monotonic() - 301,
                time.monotonic() - 300,
            ]
            with patch("local_video_site.downloader.browser_hls.time.sleep"):
                downloader._register_rate_limit_batch(failed)
            self.assertEqual(len(downloader.rate_limit_events), 1)

    def test_rate_limit_recovery_is_conservative_and_capped_at_four(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = BrowserHlsDownloader(
                page=None,
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-concurrency-recovery"),
            )
            now = time.monotonic()
            downloader.last_rate_limit_at = now
            downloader.concurrency = 1
            downloader.clean_batches = 0

            downloader._record_clean_batch()
            downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 2)

            for _ in range(3):
                downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 3)

            for _ in range(6):
                downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 3)

            downloader.last_rate_limit_at = (
                time.monotonic() - 301
            )
            downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 4)

            for _ in range(20):
                downloader._record_clean_batch()
            self.assertEqual(downloader.concurrency, 4)

    def test_browser_batch_concurrency_is_clamped_to_four(self):
        captured = {}

        class FakePage:
            def evaluate(self, script, payload):
                captured.update(payload)
                return [{"index": 0, "ok": True, "data": ""}]

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = BrowserHlsDownloader(
                page=FakePage(),
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-concurrency-cap"),
            )
            downloader._fetch_batch_payload(
                ["https://cdn.example/segment.ts"],
                concurrency=10,
            )

        self.assertEqual(captured["concurrency"], 4)

    def test_rate_limit_pause_is_not_retried_as_generic_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = BrowserHlsDownloader(
                page=None,
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-rate-propagation"),
                retry_backoff=0,
            )
            with patch.object(
                downloader,
                "_download_once",
                side_effect=HlsRateLimitPause("rate limited"),
            ) as mocked:
                with self.assertRaises(HlsRateLimitPause):
                    downloader.download(
                        "https://cdn.example/master.m3u8",
                        "video",
                        attempts=3,
                    )

        self.assertEqual(mocked.call_count, 1)

    def test_expired_hls_url_is_refreshed_without_new_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            refreshed_targets = []

            def refresh_target(reason, expires_at):
                refreshed_targets.append(reason)
                return {
                    "target": "https://cdn.example/new/master.m3u8",
                    "expires_at": None,
                }

            downloader = BrowserHlsDownloader(
                page=None,
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-refresh-test"),
                retry_backoff=0,
                refresh_target=refresh_target,
            )
            expected = {"path": Path(temp_dir) / "video.mp4"}
            with patch.object(
                downloader,
                "_download_once",
                side_effect=[
                    RuntimeError("HTTP Error 410: Gone"),
                    expected,
                ],
            ) as mocked:
                result = downloader.download(
                    "https://cdn.example/old/master.m3u8",
                    "video",
                    attempts=1,
                )

        self.assertEqual(result, expected)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(
            mocked.call_args_list[1].args[0],
            "https://cdn.example/new/master.m3u8",
        )
        self.assertEqual(len(refreshed_targets), 1)

    def test_batch_retry_only_requests_failed_segments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = BrowserHlsDownloader(
                page=None,
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-batch-test"),
                retry_backoff=0,
            )
            calls = []

            def fetch_batch(urls, concurrency):
                calls.append((list(urls), concurrency))
                if len(calls) == 1:
                    return [
                        {
                            "index": 0,
                            "ok": True,
                            "data": base64.b64encode(b"A").decode("ascii"),
                        },
                        {
                            "index": 1,
                            "ok": False,
                            "status": 500,
                            "error": "HTTP 500",
                        },
                    ]
                return [
                    {
                        "index": 0,
                        "ok": True,
                        "data": base64.b64encode(b"B").decode("ascii"),
                    }
                ]

            downloader._fetch_batch_payload = fetch_batch
            data, clean = downloader._fetch_batch_with_retries(
                ["https://cdn.example/1.ts", "https://cdn.example/2.ts"]
            )

        self.assertEqual(data, [b"A", b"B"])
        self.assertFalse(clean)
        self.assertEqual(calls[0][1], 2)
        self.assertEqual(calls[1][0], ["https://cdn.example/2.ts"])

    def test_segment_fetch_uses_abort_timeout(self):
        class FakePage:
            def __init__(self):
                self.script = ""
                self.argument = None

            def evaluate(self, script, argument):
                self.script = script
                self.argument = argument
                return base64.b64encode(b"segment-data").decode("ascii")

        with tempfile.TemporaryDirectory() as temp_dir:
            page = FakePage()
            downloader = BrowserHlsDownloader(
                page=page,
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-timeout-test"),
                retries=1,
            )
            content = downloader._fetch_bytes(
                "https://cdn.example/segment.m4s"
            )

        self.assertEqual(content, b"segment-data")
        self.assertIn("AbortController", page.script)
        self.assertIn("controller.abort()", page.script)
        self.assertEqual(page.argument["timeoutMs"], 45_000)

    def test_downloads_highest_variant_and_merges_segments(self):
        master_url = "https://cdn.example/master.m3u8"
        high_url = "https://cdn.example/high.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=100,RESOLUTION=320x180\nlow.m3u8\n'
                    '#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720\nhigh.m3u8\n'
                ),
                high_url: (
                    "#EXTM3U\n"
                    "#EXT-X-TARGETDURATION:10\n"
                    "#EXTINF:10.0,\nsegment-1.ts\n"
                    "#EXTINF:10.0,\nsegment-2.ts\n"
                    "#EXT-X-ENDLIST\n"
                ),
            }
            downloader.bodies = {
                "https://cdn.example/segment-1.ts": b"first-",
                "https://cdn.example/segment-2.ts": b"second",
            }
            with patch("local_video_site.downloader.browser_hls.shutil.which", return_value=None):
                result = downloader._download_once(master_url, "series", 1)

            self.assertEqual(result["path"].read_bytes(), b"first-second")
            self.assertEqual(result["info"]["duration"], 20.0)
            self.assertEqual(result["info"]["quality"], "720p")
            self.assertEqual(result["info"]["height"], 720)
            self.assertEqual(result["attempt"], 1)

    def test_checkpoint_is_saved_per_two_batches(self):
        playlist_url = "https://cdn.example/checkpoint.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-checkpoint-test"),
                retries=1,
            )
            lines = ["#EXTM3U"]
            for index in range(12):
                lines.extend(
                    [
                        "#EXTINF:5.0,",
                        "segment-{}.ts".format(index),
                    ]
                )
                downloader.bodies[
                    "https://cdn.example/segment-{}.ts".format(index)
                ] = b"x"
            lines.append("#EXT-X-ENDLIST")
            downloader.texts[playlist_url] = "\n".join(lines)
            checkpoints = []

            with (
                patch(
                    "local_video_site.downloader.browser_hls._write_checkpoint",
                    side_effect=lambda path, payload: checkpoints.append(
                        payload
                    ),
                ),
                patch(
                    "local_video_site.downloader.browser_hls.shutil.which",
                    return_value=None,
                ),
            ):
                downloader._download_once(playlist_url, "checkpoint", 1)

        self.assertEqual(len(checkpoints), 3)
        self.assertEqual(
            [item["next_segment_index"] for item in checkpoints],
            [0, 12, 12],
        )

    def test_batch_download_logs_worker_compatible_progress(self):
        playlist_url = "https://cdn.example/progress.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = logging.getLogger("hls-progress-format-test")
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logger,
                retries=1,
            )
            downloader.texts[playlist_url] = (
                "#EXTM3U\n"
                "#EXTINF:5,\nsegment.ts\n"
                "#EXT-X-ENDLIST\n"
            )
            downloader.bodies["https://cdn.example/segment.ts"] = b"data"
            with (
                self.assertLogs(logger, level="INFO") as captured,
                patch(
                    "local_video_site.downloader.browser_hls.shutil.which",
                    return_value=None,
                ),
            ):
                downloader._download_once(playlist_url, "progress", 1)

        self.assertTrue(
            any(
                "Progress 100.0% | segment 1/1" in line
                for line in captured.output
            )
        )

    def test_rejects_encrypted_playlist(self):
        playlist_url = "https://cdn.example/index.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
            )
            downloader.texts = {
                playlist_url: (
                    "#EXTM3U\n"
                    "#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n"
                    "#EXTINF:10,\nsegment.ts\n"
                    "#EXT-X-ENDLIST\n"
                )
            }
            with self.assertRaisesRegex(RuntimeError, "does not decrypt"):
                downloader._download_once(playlist_url, "series", 1)

    def test_unsupported_playlist_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-unsupported-test"),
                retries=3,
            )
            with patch.object(
                downloader,
                "_download_once",
                side_effect=HlsPlaylistUnsupported(
                    "AES playlist needs yt-dlp"
                ),
            ) as mocked:
                with self.assertRaises(HlsPlaylistUnsupported):
                    downloader.download(
                        "https://cdn.example/index.m3u8",
                        basename="series",
                        attempts=3,
                    )

        self.assertEqual(mocked.call_count, 1)

    def test_low_variant_upgrades_through_master_and_chooses_480p(self):
        low_url = "https://cdn.example/path/hls-250p.m3u8"
        master_url = "https://cdn.example/path/hls.m3u8"
        high_url = "https://cdn.example/path/hls-480p.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                low_url: (
                    "#EXTM3U\n"
                    "#EXTINF:10,\nsegment-250.ts\n"
                    "#EXT-X-ENDLIST\n"
                ),
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=155648,RESOLUTION=384x288,NAME="250p"\n'
                    "hls-250p.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=763904,RESOLUTION=738x554,NAME="480p"\n'
                    "hls-480p.m3u8\n"
                ),
                high_url: (
                    "#EXTM3U\n"
                    "#EXTINF:10,\nsegment-480.ts\n"
                    "#EXT-X-ENDLIST\n"
                ),
            }
            downloader.bodies = {
                "https://cdn.example/path/segment-480.ts": b"high",
            }
            with patch("local_video_site.downloader.browser_hls.shutil.which", return_value=None):
                result = downloader._download_once(low_url, "series", 1)

            self.assertEqual(result["info"]["quality"], "480p")
            self.assertEqual(result["info"]["width"], 738)
            self.assertEqual(result["info"]["height"], 554)
            self.assertEqual(result["path"].read_bytes(), b"high")

    def test_resolution_beats_higher_bitrate_lower_resolution(self):
        master_url = "https://cdn.example/master.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=9000000,RESOLUTION=854x480,NAME="480p"\n'
                    "480.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,NAME="720p"\n'
                    "720.m3u8\n"
                ),
                "https://cdn.example/720.m3u8": (
                    "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                ),
            }
            resolved_url, _, quality = downloader._resolve_playlist(master_url)

            self.assertEqual(resolved_url, "https://cdn.example/720.m3u8")
            self.assertEqual(quality["quality"], "720p")
            self.assertEqual(quality["height"], 720)

    def test_selects_1080p_over_720p_480p_and_250p(self):
        master_url = "https://cdn.example/master-1080.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=155648,RESOLUTION=384x288,NAME="250p"\n'
                    "250.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,NAME="480p"\n'
                    "480.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,NAME="720p"\n'
                    "720.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,NAME="1080p"\n'
                    "1080.m3u8\n"
                ),
                "https://cdn.example/1080.m3u8": (
                    "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                ),
            }
            resolved_url, _, quality = downloader._resolve_playlist(master_url)

            self.assertEqual(resolved_url, "https://cdn.example/1080.m3u8")
            self.assertEqual(quality["quality"], "1080p")
            self.assertEqual(quality["width"], 1920)
            self.assertEqual(quality["height"], 1080)

    def test_respects_requested_720p_or_480p_target(self):
        master_url = "https://cdn.example/master-target.m3u8"
        master = (
            "#EXTM3U\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,NAME="480p"\n'
            "480.m3u8\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,NAME="720p"\n'
            "720.m3u8\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,NAME="1080p"\n'
            "1080.m3u8\n"
        )
        for target, expected in ((720, "720.m3u8"), (480, "480.m3u8")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp_dir:
                downloader = _FakeHlsDownloader(
                    output_dir=Path(temp_dir),
                    logger=logging.getLogger("hls-target-test"),
                    retries=1,
                    max_height=target,
                )
                downloader.texts = {
                    master_url: master,
                    "https://cdn.example/480.m3u8": (
                        "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                    ),
                    "https://cdn.example/720.m3u8": (
                        "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                    ),
                }
                resolved_url, _, quality = downloader._resolve_playlist(master_url)

                self.assertEqual(
                    resolved_url,
                    "https://cdn.example/{}".format(expected),
                )
                self.assertEqual(quality["height"], target)

    def test_strict_quality_rejects_missing_exact_variant(self):
        master_url = "https://cdn.example/master-strict.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-strict-test"),
                retries=1,
                max_height=720,
                quality_target="720p",
                quality_fallback="strict",
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480,NAME="480p"\n'
                    "480.m3u8\n"
                ),
            }

            with self.assertRaisesRegex(RuntimeError, "no exact 720p"):
                downloader._resolve_playlist(master_url)

    def test_xhamster_child_playlist_upgrades_to_720p_master(self):
        base = "https://video-nss.xhcdn.com/signed/media=hls4/videos/019/831/724/"
        low_url = base + "144p.av1.mp4.m3u8"
        master_url = base + "_TPL_.av1.mp4.m3u8"
        high_url = base + "720p.av1.mp4.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                low_url: "#EXTM3U\n#EXTINF:4,\nsegment.m4s\n",
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=127221,RESOLUTION=256x144\n'
                    "144p.av1.mp4.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=855783,RESOLUTION=1280x720\n'
                    "720p.av1.mp4.m3u8\n"
                ),
                high_url: "#EXTM3U\n#EXTINF:4,\nsegment.m4s\n",
            }
            resolved_url, _, quality = downloader._resolve_playlist(low_url)

            self.assertEqual(resolved_url, high_url)
            self.assertEqual(quality["quality"], "720p")
            self.assertEqual(quality["height"], 720)

    def test_prefers_1080p_when_2160p_is_available(self):
        master_url = "https://cdn.example/master-cap.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=12000000,RESOLUTION=3840x2160,NAME="2160p"\n'
                    "2160.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=7000000,RESOLUTION=2560x1440,NAME="1440p"\n'
                    "1440.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,NAME="1080p"\n'
                    "1080.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,NAME="720p"\n'
                    "720.m3u8\n"
                ),
                "https://cdn.example/1080.m3u8": (
                    "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                ),
            }
            resolved_url, _, quality = downloader._resolve_playlist(master_url)

            self.assertEqual(resolved_url, "https://cdn.example/1080.m3u8")
            self.assertEqual(quality["quality"], "1080p")
            self.assertEqual(quality["height"], 1080)

    def test_explicit_4k_selects_2160p_hls_variant(self):
        master_url = "https://cdn.example/master-4k.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
                max_height=2160,
                quality_target="2160p",
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=12000000,RESOLUTION=3840x2160,NAME="2160p"\n'
                    "2160.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,NAME="1080p"\n'
                    "1080.m3u8\n"
                ),
                "https://cdn.example/2160.m3u8": (
                    "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n"
                ),
            }
            resolved_url, _, quality = downloader._resolve_playlist(master_url)

            self.assertEqual(
                resolved_url,
                "https://cdn.example/2160.m3u8",
            )
            self.assertEqual(quality["quality"], "2160p")
            self.assertEqual(quality["height"], 2160)

    def test_selects_360p_when_all_variants_are_below_480p(self):
        master_url = "https://cdn.example/master-low.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=155648,RESOLUTION=444x250,NAME="250p"\n'
                    "250.m3u8\n"
                    '#EXT-X-STREAM-INF:BANDWIDTH=423936,RESOLUTION=646x364,NAME="360p"\n'
                    "360.m3u8\n"
                ),
                "https://cdn.example/360.m3u8": (
                    "#EXTM3U\n#EXTINF:4,\nsegment-360.ts\n#EXT-X-ENDLIST\n"
                ),
            }
            resolved_url, _, quality = downloader._resolve_playlist(master_url)

            self.assertEqual(resolved_url, "https://cdn.example/360.m3u8")
            self.assertEqual(quality["quality"], "360p")
            self.assertEqual(quality["height"], 364)

    def test_rejects_standalone_playlist_below_360p(self):
        low_url = "https://cdn.example/hls-250p.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-test"),
                retries=1,
            )
            downloader.texts = {
                low_url: "#EXTM3U\n#EXTINF:4,\nsegment.ts\n#EXT-X-ENDLIST\n"
            }
            with self.assertRaisesRegex(RuntimeError, "below the minimum quality"):
                downloader._resolve_playlist(low_url)

    def test_auto_mode_can_use_best_available_below_360p(self):
        master_url = "https://cdn.example/master-272p.m3u8"
        media_url = "https://cdn.example/video-480x272.m3u8"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = _FakeHlsDownloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("hls-low-fallback-test"),
                retries=1,
                allow_below_minimum=True,
            )
            downloader.texts = {
                master_url: (
                    "#EXTM3U\n"
                    "#EXT-X-STREAM-INF:BANDWIDTH=625602,"
                    "RESOLUTION=480x272\n"
                    "video-480x272.m3u8\n"
                ),
                media_url: (
                    "#EXTM3U\n#EXTINF:4,\nsegment.ts\n"
                    "#EXT-X-ENDLIST\n"
                ),
            }

            resolved_url, _, quality = downloader._resolve_playlist(master_url)

            self.assertEqual(resolved_url, media_url)
            self.assertEqual(quality["quality"], "272p")
            self.assertEqual(quality["width"], 480)
            self.assertEqual(quality["height"], 272)


if __name__ == "__main__":
    unittest.main()
