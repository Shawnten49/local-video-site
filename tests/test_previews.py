import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.media.assets.previews import _motion_stats, preview_file_path


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        path = self.video_root / "preview-source.mp4"
        path.write_bytes(b"source-video")
        database.upsert_video(
            path=path,
            title="Preview Source",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        self.video_id = database.list_videos()["items"][0]["id"]
        database.update_metadata(
            video_id=self.video_id,
            info={
                "duration": 120,
                "width": 1280,
                "height": 720,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            thumbnail_path=None,
        )

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_preview_claim_ready_state_api_and_card_markup(self):
        video = database.claim_preview_video()
        self.assertEqual(video["id"], self.video_id)
        self.assertEqual(video["preview_status"], "processing")

        preview_path = preview_file_path(video)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"preview-mp4")
        database.save_preview_result(
            video_id=self.video_id,
            status="ready",
            preview_path=preview_path.name,
            start=18.0,
            duration=8.0,
            source_size=video["size"],
            source_mtime_ns=video["mtime_ns"],
        )

        saved = database.get_video(self.video_id)
        self.assertTrue(saved["preview_available"])
        client = admin_client()
        preview = client.get("/preview/{}".format(self.video_id))
        home = client.get("/")

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "video/mp4")
        self.assertIn("max-age=604800", preview.headers["cache-control"])
        self.assertIn(
            'data-preview-url="/preview/{}?v='.format(self.video_id),
            home.text,
        )
        self.assertIn("hover-preview-video", home.text)
        self.assertIn("/static/hover-preview.js", home.text)

    def test_motion_stats_detect_static_preview(self):
        motion, saturation = _motion_stats(
            "lavfi.signalstats.YDIF=0.2\n"
            "lavfi.signalstats.YDIF=1.1\n"
            "lavfi.signalstats.SATAVG=3.5\n"
            "lavfi.signalstats.SATAVG=5.0\n"
        )

        self.assertEqual(motion, 1.1)
        self.assertEqual(saturation, 5.0)

    def test_cover_button_stops_preview_and_thumbnail_stays_visible(self):
        project_root = Path(__file__).resolve().parents[1]
        script = (
            project_root / "local_video_site" / "static" / "hover-preview.js"
        ).read_text(encoding="utf-8")
        styles = (
            project_root / "local_video_site" / "static" / "app.css"
        ).read_text(encoding="utf-8")

        self.assertIn('coverButton.addEventListener("pointerenter"', script)
        self.assertIn('["waiting", "stalled"]', script)
        self.assertIn('video.addEventListener("emptied"', script)
        self.assertIn("requestVideoFrameCallback", script)
        self.assertIn("const releaseTimers = new WeakMap()", script)
        self.assertIn("active?.video !== video", script)
        self.assertNotIn(".thumb.is-previewing img { opacity: 0; }", styles)
        self.assertIn(".card-body:hover .change-cover-button", styles)
        self.assertNotIn(
            ".video-card-shell:hover .change-cover-button",
            styles,
        )
        self.assertNotIn(
            ".card-body:focus-within .change-cover-button",
            styles,
        )


if __name__ == "__main__":
    unittest.main()
