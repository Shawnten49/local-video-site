import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.catalog.original_urls import backfill_original_urls, load_download_url_mapping, register_downloaded_video


class OriginalUrlTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.summary_path = self.root / "summary.jsonl"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "LOG_PATH", self.data_dir / "logs" / "server.log"),
            patch.object(config, "SCAN_STATUS_PATH", self.data_dir / "scan.json"),
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_init_db_adds_original_url_to_legacy_table(self):
        self.data_dir.mkdir(parents=True)
        with sqlite3.connect(str(config.DATABASE_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    duration REAL,
                    width INTEGER,
                    height INTEGER,
                    video_codec TEXT,
                    audio_codec TEXT,
                    thumbnail_path TEXT,
                    metadata_status TEXT NOT NULL DEFAULT 'pending',
                    metadata_error TEXT,
                    missing INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

        database.init_db()
        with database.connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info('videos')")
            }

        self.assertIn("original_url", columns)

    def test_backfill_and_upsert_preserve_original_url(self):
        video = self.video_root / "sample.mp4"
        video.write_bytes(b"video")
        database.init_db()
        status = database.upsert_video(
            path=video,
            title="sample",
            directory="videos",
            size=5,
            mtime_ns=1,
        )
        self.assertEqual(status, "added")
        with self.summary_path.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "status": "success",
                "input_url": "https://example.com/watch/1",
                "output_path": str(video),
                "finished_at": "2026-11-13T10:00:00+08:00",
            }) + "\n")

        self.assertEqual(backfill_original_urls(self.summary_path), 1)
        video_id = database.list_videos()["items"][0]["id"]
        self.assertEqual(
            database.get_video(video_id)["original_url"],
            "https://example.com/watch/1",
        )
        detail = admin_client().get("/watch/{}".format(video_id))
        self.assertIn("视频源地址", detail.text)
        self.assertIn(
            'href="https://example.com/watch/1"',
            detail.text,
        )
        self.assertIn('target="_blank"', detail.text)
        self.assertIn('rel="noopener noreferrer"', detail.text)

        database.upsert_video(
            path=video,
            title="sample",
            directory="videos",
            size=5,
            mtime_ns=1,
        )
        self.assertEqual(
            database.get_video(video_id)["original_url"],
            "https://example.com/watch/1",
        )

    def test_register_downloaded_video_writes_source_url(self):
        video = self.video_root / "downloaded.mp4"
        video.write_bytes(b"video")

        registered = register_downloaded_video(
            output_path=video,
            title="Downloaded",
            original_url="https://example.com/watch/2",
            download_source_url="https://download.example/video/9",
        )

        self.assertTrue(registered)
        row = database.list_videos()["items"][0]
        self.assertEqual(row["title"], "Downloaded")
        self.assertEqual(
            row["download_source_url"],
            "https://download.example/video/9",
        )
        self.assertEqual(
            row["original_url"],
            "https://example.com/watch/2",
        )

    def test_latest_successful_summary_record_wins(self):
        video = self.video_root / "sample.mp4"
        records = [
            {
                "status": "success",
                "input_url": "https://example.com/old",
                "output_path": str(video),
                "finished_at": "2026-11-13T09:00:00+08:00",
            },
            {
                "status": "success",
                "input_url": "https://example.com/new",
                "output_path": str(video),
                "finished_at": "2026-11-13T10:00:00+08:00",
            },
        ]
        self.summary_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        mapping = load_download_url_mapping(self.summary_path)

        self.assertEqual(
            mapping[str(video.resolve())],
            "https://example.com/new",
        )


if __name__ == "__main__":
    unittest.main()
