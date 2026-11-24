import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database


class VideoSchemaSplitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(
                config,
                "DATABASE_PATH",
                self.data_dir / "library.db",
            ),
            patch.object(
                config,
                "THUMBNAIL_DIR",
                self.data_dir / "thumbnails",
            ),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_video_core_and_satellite_tables_are_split(self):
        path = self.video_root / "split.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title="Split Video",
            directory=self.video_root.name,
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            media_origin="local_scan",
        )
        video = database.get_video_by_path(path)

        with database.connect() as connection:
            video_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info('videos')"
                )
            }
            media = connection.execute(
                "SELECT * FROM video_media_info WHERE video_id = ?",
                (video["id"],),
            ).fetchone()
            assets = connection.execute(
                "SELECT * FROM video_assets WHERE video_id = ?",
                (video["id"],),
            ).fetchone()
            catalog = connection.execute(
                "SELECT * FROM video_catalog WHERE id = ?",
                (video["id"],),
            ).fetchone()

        self.assertNotIn("duration", video_columns)
        self.assertNotIn("thumbnail_path", video_columns)
        self.assertIn("media_origin", video_columns)
        self.assertEqual(video["media_origin"], "local_scan")
        self.assertIsNotNone(media)
        self.assertIsNotNone(assets)
        self.assertEqual(catalog["title"], "Split Video")

    def test_metadata_updates_media_and_asset_tables(self):
        path = self.video_root / "metadata.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title="Metadata Video",
            directory=self.video_root.name,
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        thumbnail = self.data_dir / "thumbnails" / "metadata.jpg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(b"thumb")

        database.update_metadata(
            video["id"],
            {
                "duration": 90,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            thumbnail_path=str(thumbnail),
        )
        database.save_preview_result(
            video["id"],
            "ready",
            preview_path="preview.mp4",
            start=2,
            duration=8,
        )
        updated = database.get_video_admin(video["id"])

        self.assertEqual(updated["duration"], 90)
        self.assertEqual(updated["width"], 1920)
        self.assertEqual(updated["thumbnail_path"], str(thumbnail))
        self.assertEqual(updated["preview_status"], "ready")
        self.assertEqual(updated["preview_path"], "preview.mp4")


if __name__ == "__main__":
    unittest.main()
