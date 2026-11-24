import tempfile
import unittest
from pathlib import Path

from local_video_site.downloader.recovery import (
    MEDIA_REFRESH_MARKER,
    MediaRefreshRequired,
    error_indicates_expiry,
    extract_media_expiry,
    media_identity,
    partial_size,
)


class MediaRecoveryTests(unittest.TestCase):
    def test_extracts_signed_url_timestamp(self):
        expires = extract_media_expiry(
            "https://cdn.example/vid2/signed/1794639486/51/1/"
            "video_1920x1080_4000k.mp4?lang=en"
        )

        self.assertIsNotNone(expires)
        self.assertEqual(expires.timestamp(), 1794639486)

    def test_query_deadline_takes_priority_over_media_id_path(self):
        expires = extract_media_expiry(
            "https://upos-sz-mirrorcos.bilivideo.com/upgcxcode/"
            "79/09/1249670979/1249670979_hr1-1-30125.m4s"
            "?e=ig8euxZM2rNcNbdlhoNvNC8BqJIzNbfqXBvEqxTEto8BTrNv"
            "&deadline=1795115701&platform=pc"
        )

        self.assertIsNotNone(expires)
        self.assertEqual(expires.timestamp(), 1795115701)

    def test_media_identity_ignores_signed_prefix_and_query(self):
        old = media_identity(
            "https://s318.example/old/1794639486/1/video_1920x1080_4000k.mp4?x=1",
            "720p",
            1920,
            1080,
        )
        new = media_identity(
            "https://s303.example/new/1794643732/1/video_1920x1080_4000k.mp4?x=2",
            "720p",
            1920,
            1080,
        )
        different = media_identity(
            "https://s303.example/new/1794643732/1/video_720x406_500k.mp4",
            "480p",
            720,
            406,
        )

        self.assertEqual(old, new)
        self.assertNotEqual(old, different)

    def test_expired_media_error_is_classified(self):
        self.assertTrue(
            error_indicates_expiry(
                RuntimeError("unable to download video data: HTTP Error 410: Gone")
            )
        )
        self.assertTrue(
            error_indicates_expiry(
                RuntimeError(
                    "Browser fetch manifest failed: Page.evaluate: "
                    "Error: HTTP 403"
                )
            )
        )
        self.assertTrue(
            error_indicates_expiry(MediaRefreshRequired("preemptive_expiry"))
        )
        self.assertFalse(
            error_indicates_expiry(RuntimeError("connection reset by peer"))
        )
        self.assertIn(MEDIA_REFRESH_MARKER, str(MediaRefreshRequired("test")))

    def test_partial_size_counts_resumable_artifacts_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "series.mp4.part").write_bytes(b"1234")
            (root / "series.mp4.part-Frag1").write_bytes(b"12")
            (root / "series.mp4").write_bytes(b"completed")
            (root / "other.mp4.part").write_bytes(b"ignored")

            self.assertEqual(partial_size(root, "series"), 6)


if __name__ == "__main__":
    unittest.main()
