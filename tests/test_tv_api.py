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
            patch.object(
                config,
                "SUBTITLE_DIR",
                self.data_dir / "subtitles",
            ),
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
        self.assertEqual(payload["playback"]["subtitles"], [])
        self.assertNotIn("path", payload)
        self.assertNotIn("original_url", payload)

    def test_public_video_detail_exposes_default_subtitle_tracks(self):
        video_id = self.video_ids["Public TV Video"]
        subtitle_path = (
            self.data_dir
            / "subtitles"
            / str(video_id)
            / "bilingual.vtt"
        )
        subtitle_path.parent.mkdir(parents=True)
        subtitle_path.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
            "快去牵马\nGet back on your horse.\n",
            encoding="utf-8",
        )
        asset = database.upsert_subtitle_asset(
            video_id=video_id,
            source_stream_index=2,
            language="zh-Hans",
            title="中上英下",
            extension="vtt",
            storage_path=subtitle_path,
            source_size=subtitle_path.stat().st_size,
            source_mtime_ns=subtitle_path.stat().st_mtime_ns,
            default_track=True,
        )
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
        tracks = response.json()["playback"]["subtitles"]
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["id"], asset["id"])
        self.assertEqual(tracks[0]["language"], "zh-Hans")
        self.assertEqual(tracks[0]["label"], "中上英下")
        self.assertEqual(tracks[0]["mime_type"], "text/vtt")
        self.assertTrue(tracks[0]["default"])
        self.assertIn(
            "/subtitle/{}?v=".format(asset["id"]),
            tracks[0]["url"],
        )
        subtitle_response = TestClient(app).get(
            tracks[0]["url"].split("?", 1)[0]
        )
        self.assertEqual(subtitle_response.status_code, 200)
        self.assertIn("WEBVTT", subtitle_response.text)

    def test_public_mkv_exposes_original_playback_for_external_player(self):
        path = self.video_root / "External Player.mkv"
        path.write_bytes(b"matroska-video-data")
        database.upsert_video(
            path,
            "External Player",
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        database.update_video_technical_info(
            video["id"],
            {
                "duration": 120,
                "width": 3840,
                "height": 2160,
                "video_codec": "hevc",
                "audio_codec": "truehd",
                "container_format": "matroska",
            },
        )

        response = TestClient(app).get(
            "/api/tv/v1/videos/{}/original-playback".format(video["id"])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertEqual(
            payload["stream_url"],
            "/media/{}".format(video["id"]),
        )
        self.assertEqual(payload["mime_type"], "video/x-matroska")
        self.assertEqual(payload["extension"], ".mkv")
        self.assertEqual(payload["title"], "External Player")
        self.assertEqual(payload["container_format"], "matroska")
        self.assertEqual(payload["video_codec"], "hevc")
        self.assertEqual(payload["audio_codec"], "truehd")
        self.assertEqual(payload["width"], 3840)
        self.assertEqual(payload["height"], 2160)
        self.assertTrue(payload["supports_range"])
        self.assertNotIn("path", payload)

    def test_public_mkv_uses_external_vlc_and_rejects_cache_preparation(self):
        path = self.video_root / "External Only.mkv"
        path.write_bytes(b"matroska-video-data")
        database.upsert_video(
            path,
            "External Only",
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)

        detail = TestClient(app).get(
            "/api/tv/v1/videos/{}".format(video["id"])
        )
        prepared = TestClient(app).post(
            "/api/tv/v1/videos/{}/prepare".format(video["id"])
        )

        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(
            detail.json()["playback"]["playback_mode"],
            "external_vlc",
        )
        self.assertEqual(prepared.status_code, 409, prepared.text)
        self.assertEqual(
            prepared.json()["detail"]["code"],
            "mkv_external_only",
        )

    def test_original_playback_supports_http_range(self):
        path = self.video_root / "Range.mkv"
        path.write_bytes(b"0123456789")
        database.upsert_video(
            path,
            "Range",
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)

        response = TestClient(app).get(
            "/media/{}".format(video["id"]),
            headers={"Range": "bytes=2-5"},
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertEqual(response.headers["content-type"], "video/x-matroska")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertIn("content-range", response.headers)

    def test_protected_video_original_playback_is_hidden(self):
        video_id = self.video_ids["User TV Video"]

        response = TestClient(app).get(
            "/api/tv/v1/videos/{}/original-playback".format(video_id)
        )

        self.assertEqual(response.status_code, 404)

    def test_original_playback_reports_offline_external_storage(self):
        video_id = self.video_ids["Public TV Video"]

        with (
            patch(
                "local_video_site.web.routes.tv.resolve_video_storage",
                return_value={
                    "state": "external_offline",
                    "path": self.video_root / "missing.mp4",
                },
            ),
            patch(
                "local_video_site.web.routes.tv.get_playback_state",
                return_value={
                    "message": "外置硬盘 D02 当前未连接",
                },
            ),
        ):
            response = TestClient(app).get(
                "/api/tv/v1/videos/{}/original-playback".format(video_id)
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "外置硬盘 D02 当前未连接")

    def test_pagination_limits_page_size(self):
        response = TestClient(app).get(
            "/api/tv/v1/videos?page=1&page_size=61"
        )

        self.assertEqual(response.status_code, 422)
