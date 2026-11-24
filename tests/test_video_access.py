import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from local_video_site.app import app
from tests.auth_test_utils import admin_client, user_client


class VideoAccessTests(unittest.TestCase):
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
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.video_ids = {}
        for title, level in (
            ("Public Video", 0),
            ("User Video", 1),
            ("Admin Video", 2),
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
            self.video_ids[title] = video["id"]
            database.set_video_access_level(video["id"], level)

    def _create_video(self, title, level):
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
        return video["id"]

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_home_filters_videos_by_role(self):
        guest = TestClient(app).get("/")
        user = user_client().get("/")
        admin = admin_client().get("/")

        self.assertIn("Public Video", guest.text)
        self.assertNotIn("User Video", guest.text)
        self.assertNotIn("Admin Video", guest.text)
        self.assertNotIn("专属视频", guest.text)
        self.assertIn("Public Video", user.text)
        self.assertIn("User Video", user.text)
        self.assertNotIn("Admin Video", user.text)
        self.assertNotIn("专属视频", user.text)
        self.assertIn("Public Video", admin.text)
        self.assertIn("User Video", admin.text)
        self.assertNotIn("Admin Video", admin.text)
        self.assertIn("专属视频", admin.text)
        self.assertIn('href="/admin-videos"', admin.text)

    def test_admin_only_video_collection_reuses_home_listing(self):
        admin = admin_client()
        page = admin.get("/admin-videos")

        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("<title>专属视频 · 本地视频库</title>", page.text)
        self.assertIn("<h1>专属视频</h1>", page.text)
        self.assertIn("Admin Video", page.text)
        self.assertNotIn("Public Video", page.text)
        self.assertNotIn("User Video", page.text)
        self.assertIn('method="get" action="/admin-videos"', page.text)
        self.assertIn('href="/watch/{}?return_to=/admin-videos"'.format(
            self.video_ids["Admin Video"]
        ), page.text)

        searched = admin.get("/admin-videos", params={"q": "Admin"})
        self.assertIn("Admin Video", searched.text)
        missing = admin.get("/admin-videos", params={"q": "Public"})
        self.assertNotIn("Public Video", missing.text)
        self.assertIn("暂无专属视频", missing.text)

        user = user_client()
        guest = TestClient(app)
        self.assertEqual(user.get("/admin-videos").status_code, 403)
        self.assertEqual(
            guest.get(
                "/admin-videos",
                follow_redirects=False,
            ).status_code,
            303,
        )

    def test_no_admin_collection_entry_when_level_two_is_empty(self):
        admin = admin_client()
        database.set_video_access_level(
            self.video_ids["Admin Video"],
            1,
        )

        home = admin.get("/")
        self.assertIn("Admin Video", home.text)
        self.assertNotIn('href="/admin-videos"', home.text)

    def test_library_cards_show_size_and_duration_without_directory(self):
        public_id = self.video_ids["Public Video"]
        admin_id = self.video_ids["Admin Video"]
        database.update_metadata(
            public_id,
            {"duration": 125.0},
            None,
            status="ready",
        )
        database.update_metadata(
            admin_id,
            {"duration": 225.0},
            None,
            status="ready",
        )

        home = admin_client().get("/")
        public_card = re.search(
            r"<article[^>]*>.*?Public Video.*?</article>",
            home.text,
            re.S,
        )
        self.assertIsNotNone(public_card)
        self.assertIn("· 02:05", public_card.group(0))
        self.assertNotIn("videos ·", public_card.group(0))

        exclusive = admin_client().get("/admin-videos")
        exclusive_card = re.search(
            r"<article[^>]*>.*?Admin Video.*?</article>",
            exclusive.text,
            re.S,
        )
        self.assertIsNotNone(exclusive_card)
        self.assertIn("· 03:45", exclusive_card.group(0))
        self.assertNotIn("videos ·", exclusive_card.group(0))

    def test_admin_collection_card_is_last_on_the_last_home_page(self):
        for index in range(35):
            self._create_video("Public Extra {:02d}".format(index), 0)
        admin = admin_client()

        first = admin.get("/", params={"page": 1})
        last = admin.get("/", params={"page": 2})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(last.status_code, 200)
        self.assertNotIn(
            'class="video-card admin-only-virtual-card"',
            first.text,
        )
        self.assertIn(
            'class="video-card admin-only-virtual-card"',
            last.text,
        )

        grid = re.search(
            r'<section class="video-grid">(.*?)</section>',
            last.text,
            re.S,
        )
        self.assertIsNotNone(grid)
        self.assertIn(
            "admin-only-virtual-card",
            grid.group(1),
        )
        self.assertTrue(
            grid.group(1).rstrip().endswith("</article>")
        )
        self.assertIn("专属视频", grid.group(1))
        self.assertIn("查看内容 →", grid.group(1))
        self.assertNotIn("ACCESS LEVEL 2", grid.group(1))
        self.assertNotIn("ADMIN ONLY", grid.group(1))

    def test_admin_only_listing_uses_only_level_two_neighbors(self):
        self.video_ids["Second Admin Video"] = self._create_video(
            "Second Admin Video",
            2,
        )
        admin = admin_client()
        first_id = self.video_ids["Admin Video"]
        second_id = self.video_ids["Second Admin Video"]

        first_page = admin.get(
            "/watch/{}".format(first_id),
            params={"return_to": "/admin-videos"},
        )
        second_page = admin.get(
            "/watch/{}".format(second_id),
            params={"return_to": "/admin-videos"},
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertTrue(
            "/watch/{}".format(second_id) in first_page.text
            or "/watch/{}".format(first_id) in second_page.text
        )
        self.assertNotIn(
            'href="/watch/{}?return_to=/admin-videos"'.format(
                self.video_ids["Public Video"]
            ),
            first_page.text + second_page.text,
        )

    def test_direct_video_and_media_routes_enforce_level(self):
        user_video = self.video_ids["User Video"]
        admin_video = self.video_ids["Admin Video"]
        guest = TestClient(app)
        user = user_client()

        self.assertEqual(guest.get("/watch/{}".format(user_video)).status_code, 404)
        self.assertEqual(
            guest.get("/api/playback-state/{}".format(user_video)).status_code,
            404,
        )
        self.assertEqual(
            guest.get("/media/{}".format(user_video)).status_code,
            404,
        )
        self.assertEqual(
            guest.post("/api/prepare/{}".format(user_video)).status_code,
            404,
        )
        self.assertEqual(
            user.get("/watch/{}".format(user_video)).status_code,
            200,
        )
        self.assertEqual(
            user.get("/watch/{}".format(admin_video)).status_code,
            404,
        )
        self.assertEqual(
            user.get("/media/{}".format(admin_video)).status_code,
            404,
        )

    def test_thumbnail_and_preview_do_not_leak_restricted_content(self):
        user_video = self.video_ids["User Video"]
        guest_thumb = TestClient(app).get("/thumb/{}".format(user_video))
        user_thumb = user_client().get("/thumb/{}".format(user_video))

        self.assertEqual(guest_thumb.status_code, 404)
        self.assertEqual(user_thumb.status_code, 200)
        self.assertEqual(
            user_thumb.headers.get("cache-control"),
            "private, no-store",
        )

    def test_favorite_requires_visible_video(self):
        user_video = self.video_ids["User Video"]
        admin_video = self.video_ids["Admin Video"]
        client = user_client()

        visible = client.post(
            "/favorites/{}/add".format(user_video),
            follow_redirects=False,
        )
        hidden = client.post(
            "/favorites/{}/add".format(admin_video),
            follow_redirects=False,
        )

        self.assertEqual(visible.status_code, 303)
        self.assertEqual(hidden.status_code, 404)

    def test_access_level_update_is_admin_only_and_syncs_season(self):
        first_path = self.video_root / "first.mp4"
        second_path = self.video_root / "second.mp4"
        first_path.write_bytes(b"first")
        second_path.write_bytes(b"second")
        database.upsert_video(
            first_path,
            "Season 001",
            "videos",
            first_path.stat().st_size,
            first_path.stat().st_mtime_ns,
        )
        database.upsert_video(
            second_path,
            "Season 002",
            "videos",
            second_path.stat().st_size,
            second_path.stat().st_mtime_ns,
        )
        first = database.get_video_by_path(first_path)
        second = database.get_video_by_path(second_path)
        season = database.upsert_video_season(
            "Series",
            "第一季",
            1,
            "bilibili",
            "https://www.bilibili.com/bangumi/play/ep1",
            "Series/第一季",
        )["season"]
        for video, number in ((first, 1), (second, 2)):
            episode_id = database.create_video_episode(
                season["id"],
                number,
                "Series 第一季 {:03d}".format(number),
                "https://www.bilibili.com/bangumi/play/ep{}".format(number),
                "https://www.bilibili.com/bangumi/play/ep{}".format(number),
                "Series/第一季/{:03d}.mp4".format(number),
                video["path"],
                None,
            )
            database.attach_episode_video(
                episode_id,
                video["id"],
                Path(video["path"]),
            )

        user = user_client()
        forbidden = user.patch(
            "/api/manage/videos/{}/access-level".format(first["id"]),
            json={"access_level": 1},
        )
        self.assertEqual(forbidden.status_code, 403)

        admin = admin_client()
        response = admin.patch(
            "/api/manage/videos/{}/access-level".format(first["id"]),
            json={"access_level": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["affected_count"], 2)
        self.assertEqual(
            database.get_video(first["id"])["access_level"],
            1,
        )
        self.assertEqual(
            database.get_video(second["id"])["access_level"],
            1,
        )
        accessible_season = user.get("/watch/{}".format(first["id"]))
        self.assertEqual(accessible_season.status_code, 200)
        self.assertNotIn("当前不可用", accessible_season.text)
        self.assertEqual(TestClient(app).get("/watch/{}".format(first["id"])).status_code, 404)

    def test_scan_does_not_reset_manual_access_level(self):
        path = self.video_root / "Public Video.mp4"
        before = database.get_video(self.video_ids["Public Video"])
        database.set_video_access_level(before["id"], 2)
        path.write_bytes(b"changed")

        database.upsert_video(
            path,
            "Public Video",
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )

        self.assertEqual(
            database.get_video(before["id"])["access_level"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
