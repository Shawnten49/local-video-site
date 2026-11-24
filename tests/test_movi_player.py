import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.account import ROLE_ADMIN, ROLE_GUEST, ROLE_USER, Identity
from local_video_site.media.playback.state import get_playback_state
from local_video_site.media.playback.engine import playback_cache_path
from tests.auth_test_utils import admin_client
from tests.auth_test_utils import user_client


class MoviPlayerTests(unittest.TestCase):
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
            patch.object(
                config,
                "COVER_DIR",
                self.data_dir / "covers",
            ),
            patch.object(
                config,
                "PREVIEW_DIR",
                self.data_dir / "previews",
            ),
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

    def _video(
        self,
        name="episode.mkv",
        audio_codec="ac3",
        video_codec="hevc",
        container_format=None,
    ):
        path = self.video_root / name
        path.write_bytes(b"media")
        stat = path.stat()
        database.upsert_video(
            path=path,
            title=path.stem,
            directory="videos",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        database.update_metadata(
            video["id"],
            {
                "duration": 60,
                "width": 1920,
                "height": 1080,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "container_format": container_format,
            },
            thumbnail_path=None,
        )
        return database.get_video_admin(video["id"])

    def test_admin_mode_routes_mkv_and_ac3_to_movi(self):
        video = self._video()
        admin = Identity(
            user_id=1,
            username="admin",
            role=ROLE_ADMIN,
            session_id=1,
        )
        user = Identity(
            user_id=2,
            username="user",
            role=ROLE_USER,
            session_id=2,
        )
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_MOVI_PLAYER": "admin"},
        ):
            admin_state = get_playback_state(video["id"], identity=admin)
            user_state = get_playback_state(video["id"], identity=user)

        self.assertEqual(admin_state["engine"], "movi")
        self.assertEqual(admin_state["engine_reason"], "matroska_container")
        self.assertFalse(admin_state["playable"])
        self.assertEqual(admin_state["state"], "cache_required")
        self.assertEqual(user_state["engine"], "native")
        self.assertFalse(user_state["playable"])

    def test_ac3_mp4_uses_movi_before_native_direct_play(self):
        video = self._video("episode.mp4", audio_codec="ac3")
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_MOVI_PLAYER": "auto"},
        ):
            state = get_playback_state(video["id"], identity=Identity(
                role=ROLE_GUEST,
            ))
        self.assertEqual(state["engine"], "movi")
        self.assertEqual(state["engine_reason"], "unsupported_audio_codec")
        self.assertTrue(state["playable"])

    def test_mpegts_disguised_as_mp4_requires_container_cache(self):
        video = self._video(
            "episode.mp4",
            video_codec="h264",
            audio_codec="aac",
            container_format="mpegts",
        )
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_MOVI_PLAYER": "off"},
        ):
            state = get_playback_state(
                video["id"],
                identity=Identity(role=ROLE_ADMIN),
            )
        self.assertFalse(state["playable"])
        self.assertEqual(state["state"], "cache_required")
        self.assertEqual(state["reason_code"], "container_mismatch")
        self.assertIn("实际容器", state["message"])

    def test_mkv_cache_ready_uses_movi_on_cached_mp4(self):
        video = self._video()
        cache_path = playback_cache_path({
            **video,
            "path": str(Path(video["path"]).resolve()),
        })
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"cache")
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_MOVI_PLAYER": "auto"},
        ):
            state = get_playback_state(
                video["id"],
                identity=Identity(role=ROLE_GUEST),
            )
        self.assertEqual(state["state"], "cache_ready")
        self.assertEqual(state["engine"], "movi")
        self.assertEqual(
            state["playback_url"],
            "/playback/{}".format(video["id"]),
        )

    def test_off_mode_keeps_existing_playback_state(self):
        video = self._video()
        admin = Identity(
            user_id=1,
            username="admin",
            role=ROLE_ADMIN,
            session_id=1,
        )
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_MOVI_PLAYER": "off"},
        ):
            state = get_playback_state(video["id"], identity=admin)
        self.assertEqual(state["engine"], "native")
        self.assertEqual(state["state"], "cache_required")

    def test_default_mode_is_auto_for_guests(self):
        video = self._video()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOCAL_VIDEO_MOVI_PLAYER", None)
            state = get_playback_state(
                video["id"],
                identity=Identity(role=ROLE_GUEST),
            )
        self.assertEqual(state["engine"], "movi")
        self.assertEqual(state["state"], "cache_required")

    def test_watch_page_exposes_movi_configuration_without_native_src(self):
        video = self._video()
        client = admin_client()
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_MOVI_PLAYER": "admin"},
        ):
            page = client.get("/watch/{}".format(video["id"]))

        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('data-playback-engine="movi"', page.text)
        self.assertIn(
            'data-movi-script="/static/vendor/movi-player/0.4.1/element.slim.js"',
            page.text,
        )
        self.assertIn(
            'data-movi-wasm="/static/vendor/movi-player/0.4.1/movi.wasm"',
            page.text,
        )
        self.assertIn('data-playback-url=""', page.text)
        video_tag = re.search(
            r'<video id="player".*?</video>',
            page.text,
            re.DOTALL,
        )
        self.assertIsNotNone(video_tag)
        self.assertNotRegex(video_tag.group(0), r'\ssrc="')

    def test_prepare_mkv_cache_endpoint_calls_remux_pipeline(self):
        video = self._video()
        client = admin_client()
        with patch(
            "local_video_site.web.routes.playback.prepare_playback_cache",
            return_value=Path("/tmp/cache.mp4"),
        ) as prepare:
            response = client.post(
                "/api/prepare/{}".format(video["id"])
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["playback_url"],
            "/playback/{}".format(video["id"]),
        )
        prepare.assert_called_once()

    def test_vendor_assets_are_served_with_long_cache_and_wasm_type(self):
        client = admin_client()
        script = client.head(
            "/static/vendor/movi-player/0.4.1/element.slim.js"
        )
        wasm = client.head(
            "/static/vendor/movi-player/0.4.1/movi.wasm"
        )

        self.assertEqual(script.status_code, 200)
        self.assertIn("immutable", script.headers["cache-control"])
        self.assertEqual(wasm.status_code, 200)
        self.assertEqual(wasm.headers["content-type"], "application/wasm")

    def test_compare_page_is_admin_only_and_loads_both_players(self):
        video = self._video()
        admin = admin_client()
        user = user_client(username="movi-test-user")

        page = admin.get(
            "/manage/player-test/{}".format(video["id"])
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('id="native-player"', page.text)
        self.assertIn('id="movi-test-player"', page.text)
        self.assertIn("element.slim.js", page.text)
        self.assertEqual(
            user.get(
                "/manage/player-test/{}".format(video["id"])
            ).status_code,
            403,
        )


if __name__ == "__main__":
    unittest.main()
