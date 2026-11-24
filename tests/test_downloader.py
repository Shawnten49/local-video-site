import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.downloader.ytdlp import (
    DownloadProgress,
    Downloader,
    remove_partial_artifacts,
)
from local_video_site.downloader.errors import PermanentDownloadError
from local_video_site.downloader.recovery import (
    MediaRefreshRequired,
)

SAFE_RESTART_BYTES = 256 * 1024 * 1024


class DownloaderRetryTests(unittest.TestCase):
    def test_proxy_and_360p_default_are_passed_to_ytdlp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            completed = output / "series.mp4"
            completed.write_bytes(b"video")
            captured = {}

            class FakeYdl:
                def __init__(self, options):
                    captured.update(options)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return False

                def extract_info(self, target, download):
                    return {
                        "requested_downloads": [
                            {"filepath": str(completed)}
                        ],
                        "title": "Series",
                    }

            downloader = Downloader(
                output_dir=output,
                logger=logging.getLogger("downloader-test"),
                retry_backoff=0,
                proxy="http://127.0.0.1:1087",
                concurrent_fragment_downloads=4,
            )
            with patch(
                "local_video_site.downloader.ytdlp.YoutubeDL",
                FakeYdl,
            ):
                result = downloader._download_once(
                    "https://example.com/watch",
                    "series",
                    {},
                    None,
                    1,
                    fresh=False,
                )

        self.assertEqual(result["path"], completed)
        self.assertEqual(captured["proxy"], "http://127.0.0.1:1087")
        self.assertIn("height>=360", captured["format"])
        self.assertEqual(captured["concurrent_fragment_downloads"], 4)

    def test_merged_separate_streams_report_final_container(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            final = output / "series.mp4"
            audio = output / "series.f30280.m4a"
            video = output / "series.f30032.mp4"
            final.write_bytes(b"merged-video")
            audio.write_bytes(b"audio")
            video.write_bytes(b"video")
            captured = {}

            class FakeYdl:
                def __init__(self, options):
                    captured.update(options)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return False

                def extract_info(self, target, download):
                    return {
                        "requested_downloads": [
                            {"filepath": str(video)},
                            {"filepath": str(audio)},
                        ],
                        "title": "Series",
                    }

            downloader = Downloader(
                output_dir=output,
                logger=logging.getLogger("downloader-merge-test"),
                format_spec="bestvideo*+bestaudio/best",
                merge_output_format="mp4",
            )
            with patch(
                "local_video_site.downloader.ytdlp.YoutubeDL",
                FakeYdl,
            ):
                result = downloader._download_once(
                    "https://example.com/watch",
                    "series",
                    {},
                    None,
                    1,
                    fresh=False,
                )

        self.assertEqual(result["path"], final.resolve())
        self.assertEqual(captured["merge_output_format"], "mp4")

    def test_download_retries_and_succeeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = Downloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("downloader-test"),
                retry_backoff=0,
            )
            expected = {"path": Path(temp_dir) / "series.mp4"}
            with patch.object(
                downloader,
                "_download_once",
                side_effect=[RuntimeError("temporary"), expected],
            ) as mocked:
                result = downloader.download(
                    "https://cdn.example/video.mp4",
                    "series",
                    attempts=2,
                )

        self.assertEqual(result, expected)
        self.assertEqual(mocked.call_count, 2)

    def test_download_fails_after_three_resume_attempts_and_fresh_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = Downloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("downloader-test"),
                retry_backoff=0,
            )
            with patch.object(
                downloader,
                "_download_once",
                side_effect=RuntimeError("still failing"),
            ) as mocked:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "after 3 resume attempts and one fresh restart",
                ):
                    downloader.download(
                        "https://cdn.example/video.mp4",
                        "series",
                        attempts=3,
                    )

        self.assertEqual(mocked.call_count, 4)
        self.assertTrue(mocked.call_args_list[-1].kwargs["fresh"])

    def test_terminal_youku_account_restriction_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = Downloader(
                output_dir=Path(temp_dir),
                logger=logging.getLogger("downloader-terminal-test"),
                retry_backoff=0,
            )
            error = RuntimeError(
                "ERROR: [youku] example: Youku server reported error "
                "-6004: 该账号检测到有可疑违规、侵权等行为，已限制播放"
            )
            with patch.object(
                downloader,
                "_download_once",
                side_effect=error,
            ) as mocked:
                with self.assertRaisesRegex(
                    PermanentDownloadError,
                    "优酷账号被限制播放",
                ):
                    downloader.download(
                        "https://v.youku.com/v_show/id_example.html",
                        "series",
                        attempts=3,
                    )

        self.assertEqual(mocked.call_count, 1)

    def test_corrupt_cache_is_cleared_only_before_fresh_download(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            partial = output / "series.mp4.part"
            fragment = output / "series.mp4.part-Frag19"
            metadata = output / "series.mp4.ytdl"
            completed = output / "series.mp4"
            unrelated = output / "other.mp4.part"
            for path in (partial, fragment, metadata, completed, unrelated):
                path.write_bytes(b"data")

            downloader = Downloader(
                output_dir=output,
                logger=logging.getLogger("downloader-test"),
                retry_backoff=0,
            )
            expected = {"path": completed}
            call_count = {"value": 0}

            def download_once(*args, **kwargs):
                call_count["value"] += 1
                if call_count["value"] <= 3:
                    if call_count["value"] == 3:
                        raise ValueError(
                            "Data must be padded to 16 byte boundary in CBC mode"
                        )
                    raise RuntimeError("resume still failing")
                return expected

            with patch.object(
                downloader,
                "_download_once",
                side_effect=download_once,
            ) as mocked:
                result = downloader.download(
                    "https://cdn.example/video.m3u8",
                    "series",
                    attempts=3,
                )

            self.assertEqual(result, expected)
            self.assertEqual(mocked.call_count, 4)
            self.assertTrue(mocked.call_args_list[-1].kwargs["fresh"])
            self.assertFalse(mocked.call_args_list[-2].kwargs["fresh"])
            self.assertFalse(partial.exists())
            self.assertFalse(fragment.exists())
            self.assertFalse(metadata.exists())
            self.assertTrue(completed.exists())
            self.assertTrue(unrelated.exists())

    def test_partial_cleanup_is_scoped_to_basename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            target = output / "series.mp4.part"
            other = output / "series-extra.mp4.part"
            target.write_bytes(b"partial")
            other.write_bytes(b"other")

            removed = remove_partial_artifacts(output, "series")

            self.assertEqual(removed, 1)
            self.assertFalse(target.exists())
            self.assertTrue(other.exists())

    def test_successful_download_removes_stale_partials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            completed = output / "series.mp4"
            completed.write_bytes(b"video")
            fragment = output / "series.mp4.part-Frag336.part"
            fragment.write_bytes(b"partial")
            downloader = Downloader(
                output_dir=output,
                logger=logging.getLogger("downloader-test"),
                retry_backoff=0,
            )
            expected = {"path": completed}
            with patch.object(downloader, "_download_once", return_value=expected):
                result = downloader.download(
                    "https://cdn.example/video.m3u8",
                    "series",
                    attempts=1,
                )

            self.assertEqual(result, expected)
            self.assertFalse(fragment.exists())
            self.assertTrue(completed.exists())

    def test_expired_url_refreshes_without_counting_normal_resume_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            partial = output / "series.mp4.part"
            partial.write_bytes(b"partial")
            completed = output / "series.mp4"
            completed.write_bytes(b"video")
            downloader = Downloader(
                output_dir=output,
                logger=logging.getLogger("downloader-refresh-test"),
                retry_backoff=0,
            )
            expected = {"path": completed}

            with patch.object(
                downloader,
                "_download_once",
                side_effect=[
                    RuntimeError("HTTP Error 410: Gone"),
                    expected,
                ],
            ) as mocked:
                result = downloader.download(
                    "https://cdn.example/old/series.mp4",
                    "series",
                    attempts=1,
                    refresh_target=lambda reason, expires: {
                        "target": "https://cdn.example/new/series.mp4",
                        "headers": {"Referer": "https://example.com/"},
                        "expires_at": None,
                    },
                )

        self.assertEqual(result, expected)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(
            mocked.call_args_list[0].args[0],
            "https://cdn.example/old/series.mp4",
        )
        self.assertEqual(
            mocked.call_args_list[1].args[0],
            "https://cdn.example/new/series.mp4",
        )
        self.assertFalse(mocked.call_args_list[1].kwargs["fresh"])

    def test_large_partial_is_preserved_after_resume_attempts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            partial = output / "series.mp4.part"
            with partial.open("wb") as handle:
                handle.seek(SAFE_RESTART_BYTES)
                handle.write(b"x")
            downloader = Downloader(
                output_dir=output,
                logger=logging.getLogger("downloader-preserve-test"),
                retry_backoff=0,
            )
            with patch.object(
                downloader,
                "_download_once",
                side_effect=RuntimeError("network timeout"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "preserving .* partial media",
                ):
                    downloader.download(
                        "https://cdn.example/series.mp4",
                        "series",
                        attempts=1,
                    )

            self.assertTrue(partial.exists())
            self.assertGreaterEqual(
                partial.stat().st_size,
                SAFE_RESTART_BYTES,
            )

    def test_progress_hook_requests_preemptive_media_refresh(self):
        progress = DownloadProgress(
            logging.getLogger("downloader-proactive-test"),
            refresh_at=time.time() - 1,
        )
        with self.assertRaises(MediaRefreshRequired):
            progress.hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 1,
                    "total_bytes": 10,
                }
            )


if __name__ == "__main__":
    unittest.main()
