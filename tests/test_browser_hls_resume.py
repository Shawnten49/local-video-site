import logging
import tempfile
import unittest
from pathlib import Path

from local_video_site.downloader.browser_hls import BrowserHlsDownloader


class ResumeTestDownloader(BrowserHlsDownloader):
    def __init__(self, output_dir):
        super().__init__(
            page=None,
            output_dir=output_dir,
            logger=logging.getLogger("test.hls.resume"),
            retries=1,
        )
        self.interrupt_once = True
        self.token = "token-a"

    def _resolve_playlist(self, manifest_url):
        playlist = "#EXTM3U\n#EXT-X-ENDLIST\n"
        return manifest_url, playlist, {
            "quality": "480p",
            "width": 854,
            "height": 480,
            "bandwidth": 1000,
        }

    def _parse_media(self, playlist_url, playlist):
        prefix = "https://cdn.example/{}/media=hls4/video".format(
            self.token
        )
        return [
            ("init", prefix + "/init.mp4"),
            ("segment", prefix + "/1.m4s"),
            ("segment", prefix + "/2.m4s"),
            ("segment", prefix + "/3.m4s"),
        ]

    def _duration(self, playlist):
        return 12.0

    def _fetch_bytes(self, url, attempts=5):
        if url.endswith("/init.mp4"):
            return b"INIT"
        if url.endswith("/2.m4s") and self.interrupt_once:
            self.interrupt_once = False
            raise KeyboardInterrupt()
        return {
            "1.m4s": b"A",
            "2.m4s": b"B",
            "3.m4s": b"C",
        }[url.rsplit("/", 1)[-1]]

    def _fetch_batch_with_retries(self, urls):
        return ([self._fetch_bytes(url) for url in urls], True)


class BrowserHlsResumeTests(unittest.TestCase):
    def test_pause_preserves_segment_checkpoint_and_resumes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloader = ResumeTestDownloader(root)
            manifest = "https://cdn.example/master.m3u8?token=a"

            with self.assertRaises(KeyboardInterrupt):
                downloader._download_once(manifest, "video", 1)

            part = root / "video.mp4.part"
            checkpoint = root / "video.mp4.resume.json"
            self.assertTrue(part.is_file())
            self.assertTrue(checkpoint.is_file())

            downloader.token = "token-b"
            result = downloader._download_once(manifest, "video", 2)

            self.assertEqual(result["path"].read_bytes(), b"INITABC")
            self.assertFalse(part.exists())
            self.assertFalse(checkpoint.exists())


if __name__ == "__main__":
    unittest.main()
