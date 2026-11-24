import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video = self.root / "videos" / "sample.mp4"
        self.video.parent.mkdir()
        self.video.write_bytes(b"video")
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "LOG_PATH", self.data_dir / "logs" / "server.log"),
            patch.object(config, "SCAN_STATUS_PATH", self.data_dir / "scan.json"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        database.upsert_video(
            path=self.video,
            title="Cover Test",
            directory="videos",
            size=5,
            mtime_ns=1,
        )
        video = database.get_video_by_path(self.video)
        database.update_video_technical_info(
            video["id"],
            {"duration": 120},
        )
        self.video_id = database.list_videos()["items"][0]["id"]

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_custom_cover_and_restore_auto(self):
        custom = config.COVER_DIR / "custom.jpg"
        automatic = config.THUMBNAIL_DIR / "automatic.jpg"
        custom.parent.mkdir(parents=True, exist_ok=True)
        automatic.parent.mkdir(parents=True, exist_ok=True)
        custom.write_bytes(b"custom")
        automatic.write_bytes(b"auto")
        client = admin_client()

        with patch(
            "local_video_site.web.routes.media.generate_custom_cover",
            return_value=custom,
        ):
            saved = client.post(
                "/api/videos/{}/cover".format(self.video_id),
                json={"timestamp": 42.5},
            )

        self.assertEqual(saved.status_code, 200)
        self.assertIn("?v=", saved.json()["thumbnail_url"])
        row = database.get_video(self.video_id)
        self.assertEqual(row["cover_source"], "custom")
        self.assertEqual(row["cover_timestamp"], 42.5)
        self.assertEqual(row["thumbnail_path"], str(custom))

        with patch(
            "local_video_site.web.routes.media.generate_thumbnail",
            return_value=automatic,
        ):
            restored = client.delete(
                "/api/videos/{}/cover".format(self.video_id)
            )

        self.assertEqual(restored.status_code, 200)
        row = database.get_video(self.video_id)
        self.assertEqual(row["cover_source"], "auto")
        self.assertIsNone(row["cover_timestamp"])
        self.assertEqual(row["thumbnail_path"], str(automatic))

    def test_invalid_cover_timestamp_is_rejected(self):
        client = admin_client()

        self.assertEqual(
            client.post(
                "/api/videos/{}/cover".format(self.video_id),
                json={"timestamp": -1},
            ).status_code,
            422,
        )
        self.assertEqual(
            client.post(
                "/api/videos/{}/cover".format(self.video_id),
                json={"timestamp": 121},
            ).status_code,
            422,
        )

    def test_upsert_preserves_custom_cover_until_file_changes(self):
        database.update_custom_cover(
            self.video_id,
            str(config.COVER_DIR / "custom.jpg"),
            30,
        )
        database.upsert_video(
            path=self.video,
            title="Cover Test",
            directory="videos",
            size=5,
            mtime_ns=1,
        )
        row = database.get_video(self.video_id)
        self.assertEqual(row["cover_source"], "custom")
        self.assertEqual(row["cover_timestamp"], 30)

        database.upsert_video(
            path=self.video,
            title="Cover Test",
            directory="videos",
            size=6,
            mtime_ns=2,
        )
        row = database.get_video(self.video_id)
        self.assertEqual(row["cover_source"], "auto")
        self.assertIsNone(row["cover_timestamp"])

    def test_homepage_contains_cover_controls(self):
        response = admin_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="change-cover-button"', response.text)
        self.assertIn('class="card-meta-row"', response.text)
        self.assertLess(
            response.text.index('class="card-meta-row"'),
            response.text.index('class="change-cover-button"'),
        )
        self.assertIn('id="cover-dialog"', response.text)
        self.assertIn("/static/cover.js", response.text)
        self.assertIn("使用当前画面", response.text)
        self.assertIn("恢复自动封面", response.text)


if __name__ == "__main__":
    unittest.main()
