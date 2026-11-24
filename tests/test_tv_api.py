import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from local_video_site.app import app


class TvApiTests(unittest.TestCase):
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
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.video_ids = {}
        for title, level in (
            ("Public TV Video", 0),
            ("User TV Video", 1),
            ("Admin TV Video", 2),
        ):
            path = self.video_root / "{}.mp4".format(title)
            path.write_bytes(title.encode("utf-8"))
            database.upsert_video(
                path,
                title,
                "videos",
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            video = database.get_video_by_path(path)
            database.set_video_access_level(video["id"], level)
            self.video_ids[title] = video["id"]

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_ping_does_not_require_login(self):
        response = TestClient(app).get("/api/tv/v1/ping")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api_version"], 1)

    def test_video_list_only_returns_guest_visible_items(self):
        response = TestClient(app).get("/api/tv/v1/videos")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["id"], self.video_ids["Public TV Video"])
        self.assertEqual(payload["items"][0]["title"], "Public TV Video")
        self.assertNotIn("path", payload["items"][0])
        self.assertNotIn("access_level", payload["items"][0])

    def test_protected_video_detail_and_playback_are_hidden(self):
        client = TestClient(app)
        for title in ("User TV Video", "Admin TV Video"):
            video_id = self.video_ids[title]
            self.assertEqual(
                client.get("/api/tv/v1/videos/{}".format(video_id)).status_code,
                404,
            )
            self.assertEqual(
                client.get(
                    "/api/tv/v1/videos/{}/playback".format(video_id)
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    "/api/tv/v1/videos/{}/prepare".format(video_id)
                ).status_code,
                404,
            )

    def test_public_video_detail_contains_safe_playback_data(self):
        video_id = self.video_ids["Public TV Video"]
        playback = {
            "state": "direct_ready",
            "playable": True,
            "message": "",
            "retryable": False,
            "playback_url": "/media/{}".format(video_id),
        }

        with patch(
            "local_video_site.web.routes.tv.get_playback_state",
            return_value=playback,
        ):
            response = TestClient(app).get(
                "/api/tv/v1/videos/{}".format(video_id)
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["playback"]["stream_url"], "/media/{}".format(video_id))
        self.assertTrue(payload["playback"]["supports_range"])
        self.assertNotIn("path", payload)
        self.assertNotIn("original_url", payload)

    def test_pagination_limits_page_size(self):
        response = TestClient(app).get(
            "/api/tv/v1/videos?page=1&page_size=61"
        )

        self.assertEqual(response.status_code, 422)
