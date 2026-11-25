import tempfile
import unittest
from subprocess import CompletedProcess
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site import database
from local_video_site.app import app
from local_video_site.core import config
from local_video_site.media.playback.external import (
    ExternalPlaybackTokenError,
    create_external_playback_token,
    verify_external_playback_token,
)
from tests.auth_test_utils import user_client


class ExternalPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.secret_path = self.data_dir / ".playback-token-secret"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(
                config,
                "PLAYBACK_TOKEN_SECRET_PATH",
                self.secret_path,
            ),
            patch(
                "local_video_site.media.playback.external.external_token_seconds",
                return_value=300,
            ),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.public_id = self._video("Public External.mkv", 0)
        self.user_id = self._video("User External.mkv", 1)
        self.admin_id = self._video("Admin External.mkv", 2)

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _video(self, name, access_level):
        path = self.video_root / name
        path.write_bytes((name * 8).encode("utf-8"))
        database.upsert_video(
            path,
            name,
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        database.set_video_access_level(video["id"], access_level)
        database.update_video_technical_info(
            video["id"],
            {
                "duration": 60,
                "width": 3840,
                "height": 2160,
                "video_codec": "hevc",
                "audio_codec": "truehd",
                "container_format": "matroska",
            },
        )
        return video["id"]

    def test_public_video_exposes_signed_original_url_and_playlist(self):
        client = TestClient(app)
        response = client.get(
            "/api/external-playback/{}".format(self.public_id)
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["title"], "Public External.mkv")
        self.assertEqual(payload["mime_type"], "video/x-matroska")
        self.assertEqual(payload["extension"], ".mkv")
        self.assertIn("/media/{}?token=".format(self.public_id), payload["stream_url"])
        self.assertEqual(
            payload["playlist_url"],
            "/api/external-playback/{}/playlist.m3u".format(self.public_id),
        )
        self.assertTrue(payload["supports_range"])
        self.assertNotIn("path", payload)

        playlist = client.get(payload["playlist_url"])
        self.assertEqual(playlist.status_code, 200)
        self.assertEqual(
            playlist.headers["content-type"],
            "application/vnd.apple.mpegurl",
        )
        self.assertIn("attachment;", playlist.headers["content-disposition"])
        self.assertTrue(playlist.text.startswith("#EXTM3U\n"))
        self.assertIn("Public External.mkv", playlist.text)
        self.assertIn("http://testserver/media/", playlist.text)

    def test_watch_page_exposes_external_playback_controls(self):
        response = TestClient(app).get(
            "/watch/{}".format(self.public_id)
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="open-vlc-playback"', response.text)
        self.assertIn('id="copy-external-playback"', response.text)
        self.assertIn('id="download-external-playlist"', response.text)
        self.assertIn(
            "/api/external-playback/{}".format(self.public_id),
            response.text,
        )
        self.assertIn(
            "/static/player.js?v=20261125-mkv-vlc",
            response.text,
        )

    def test_loopback_open_endpoint_launches_vlc_with_signed_url(self):
        client = TestClient(
            app,
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 50000),
        )
        with patch(
            "local_video_site.web.routes.playback.subprocess.run",
            return_value=CompletedProcess([], 0, "", ""),
        ) as run:
            response = client.post(
                "/api/external-playback/{}/open".format(self.public_id),
                json={},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "opened")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["open", "-a", "VLC"])
        self.assertTrue(
            command[3].startswith(
                "http://127.0.0.1/media/{}?token=".format(self.public_id)
            )
        )

    def test_remote_open_endpoint_does_not_launch_server_vlc(self):
        with patch(
            "local_video_site.web.routes.playback.subprocess.run",
        ) as run:
            response = TestClient(app).post(
                "/api/external-playback/{}/open".format(self.public_id),
                json={},
            )

        self.assertEqual(response.status_code, 403)
        run.assert_not_called()

    def test_logged_in_user_can_open_protected_video_without_cookie(self):
        user = user_client(username="external-user")
        response = user.get(
            "/api/external-playback/{}".format(self.user_id)
        )

        self.assertEqual(response.status_code, 200, response.text)
        stream_path = response.json()["stream_url"]
        anonymous = TestClient(app).get(stream_path)

        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(anonymous.content, b"User External.mkv" * 8)

    def test_guest_and_normal_user_cannot_create_admin_video_link(self):
        guest = TestClient(app)
        user = user_client(username="external-limited")

        self.assertEqual(
            guest.get(
                "/api/external-playback/{}".format(self.user_id)
            ).status_code,
            404,
        )
        self.assertEqual(
            user.get(
                "/api/external-playback/{}".format(self.admin_id)
            ).status_code,
            404,
        )

    def test_invalid_or_expired_token_does_not_unlock_protected_media(self):
        client = TestClient(app)
        invalid = client.get(
            "/media/{}?token=not-a-valid-token".format(self.user_id)
        )
        expired_token = create_external_playback_token(
            self.user_id,
            user_id=1,
            seconds=-1,
        )
        expired = client.get(
            "/media/{}?token={}".format(self.user_id, expired_token)
        )

        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(expired.status_code, 404)

    def test_token_is_bound_to_video_id(self):
        token = create_external_playback_token(
            self.public_id,
            user_id=1,
            seconds=60,
        )

        with self.assertRaises(ExternalPlaybackTokenError):
            verify_external_playback_token(token, self.user_id)


if __name__ == "__main__":
    unittest.main()
