import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from local_video_site.app import app


class AccountAccessTests(unittest.TestCase):
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
        video = self.video_root / "Account Video.mp4"
        video.write_bytes(b"video")
        database.upsert_video(
            video,
            "Account Video",
            "videos",
            video.stat().st_size,
            video.stat().st_mtime_ns,
        )
        self.video_id = database.list_videos()["items"][0]["id"]

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _admin_client(self):
        client = TestClient(app)
        response = client.post(
            "/setup",
            data={
                "username": "admin",
                "password": "strong-password-123",
                "confirmation": "strong-password-123",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        return client

    def _create_user(self, admin, username):
        response = admin.post(
            "/api/users",
            json={
                "username": username,
                "password": "user-password-123",
                "role": "user",
                "must_change_password": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user"]

    def _login(self, username, password):
        client = TestClient(app)
        response = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303, response.text)
        return client

    def test_guest_can_browse_but_cannot_operate(self):
        client = TestClient(app)
        home = client.get("/")
        watch = client.get("/watch/{}".format(self.video_id))
        favorites = client.get("/favorites", follow_redirects=False)
        downloads = client.get("/downloads", follow_redirects=False)
        scan = client.get("/api/scan/status")

        self.assertEqual(home.status_code, 200)
        self.assertNotIn('href="/categories"', home.text)
        self.assertNotIn('href="/actors"', home.text)
        self.assertNotIn('href="/favorites"', home.text)
        self.assertNotIn('href="/downloads"', home.text)
        self.assertNotIn('href="/manage/videos"', home.text)
        self.assertIn('href="/login?next=%2F"', home.text)
        self.assertEqual(watch.status_code, 200)
        self.assertNotIn("video-actors-title", watch.text)
        self.assertNotIn("video-categories-title", watch.text)
        self.assertNotIn("watch-source-actions", watch.text)
        self.assertNotIn("添加收藏", watch.text)
        self.assertEqual(favorites.status_code, 303)
        self.assertIn("/login?next=", favorites.headers["location"])
        self.assertEqual(downloads.status_code, 303)
        self.assertEqual(scan.status_code, 401)

    def test_setup_creates_admin_and_management_navigation(self):
        admin = self._admin_client()
        home = admin.get("/")
        manage = admin.get("/manage/videos")
        downloads = admin.get("/downloads")
        users = admin.get("/manage/users")
        api = admin.get("/api/users")

        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/categories"', home.text)
        self.assertIn('href="/actors"', home.text)
        self.assertIn('href="/manage/videos"', home.text)
        self.assertNotIn('href="/downloads"', home.text)
        self.assertNotIn('href="/manage/users"', home.text)
        self.assertEqual(manage.status_code, 200)
        self.assertIn('href="/manage/videos"', manage.text)
        self.assertIn('href="/downloads"', manage.text)
        self.assertIn('href="/manage/users"', manage.text)
        self.assertIn('aria-current="page"', manage.text)
        self.assertIn(
            "manage-video-nav-button is-active",
            manage.text,
        )
        self.assertEqual(manage.text.count("admin-identity-button"), 1)
        self.assertIn(">\n      admin\n    </a>", manage.text)
        self.assertIn('action="/logout"', manage.text)
        self.assertEqual(downloads.status_code, 200)
        self.assertIn(
            "download-nav-button is-active",
            downloads.text,
        )
        self.assertEqual(users.status_code, 200)
        self.assertIn(
            "account-management-nav-button is-active",
            users.text,
        )
        self.assertIn("账号管理", users.text)
        self.assertIn('href="/manage/settings"', users.text)
        self.assertNotIn("account-section-tabs", users.text)
        self.assertIn("account-admin-layout", users.text)
        self.assertIn("account-create-panel", users.text)
        self.assertIn("account-user-card", users.text)
        self.assertIn("account-control-grid", users.text)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["items"][0]["role"], "admin")

    def test_user_can_favorite_and_admin_pages_are_forbidden(self):
        admin = self._admin_client()
        user = self._create_user(admin, "alice")
        client = self._login("alice", "user-password-123")
        home = client.get("/")
        added = client.post(
            "/favorites/{}/add".format(self.video_id),
            follow_redirects=False,
        )
        favorites = client.get("/favorites")
        watch = client.get("/watch/{}".format(self.video_id))
        administration = client.get("/manage/users")

        self.assertIn('href="/favorites"', home.text)
        self.assertNotIn('href="/categories"', home.text)
        self.assertNotIn('href="/downloads"', home.text)
        self.assertEqual(added.status_code, 303)
        self.assertEqual(favorites.status_code, 200)
        self.assertIn("Account Video", favorites.text)
        self.assertIn("取消收藏", watch.text)
        self.assertNotIn("视频源地址", watch.text)
        self.assertEqual(administration.status_code, 403)
        self.assertTrue(database.is_user_favorite(user["id"], self.video_id))
        self.assertFalse(database.is_favorite(self.video_id))

    def test_favorites_are_isolated_between_users(self):
        admin = self._admin_client()
        first = self._create_user(admin, "first")
        second = self._create_user(admin, "second")
        first_client = self._login("first", "user-password-123")
        second_client = self._login("second", "user-password-123")

        first_client.post("/favorites/{}/add".format(self.video_id))

        self.assertEqual(
            first_client.get("/favorites").text.count("video-card"),
            1,
        )
        self.assertIn("还没有收藏", second_client.get("/favorites").text)
        self.assertTrue(database.is_user_favorite(first["id"], self.video_id))
        self.assertFalse(database.is_user_favorite(second["id"], self.video_id))

    def test_last_admin_cannot_be_disabled(self):
        admin = self._admin_client()
        users = admin.get("/api/users").json()["items"]
        admin_id = users[0]["id"]
        response = admin.patch(
            "/api/users/{}".format(admin_id),
            json={"is_active": False},
        )
        self.assertEqual(response.status_code, 409)

    def test_password_change_revokes_other_sessions(self):
        admin = self._admin_client()
        self._create_user(admin, "session-user")
        first = self._login("session-user", "user-password-123")
        second = self._login("session-user", "user-password-123")

        changed = first.post(
            "/api/account/password",
            json={
                "current_password": "user-password-123",
                "new_password": "changed-password-456",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(first.get("/account").status_code, 200)
        self.assertEqual(
            second.get("/account", follow_redirects=False).status_code,
            303,
        )

    def test_cross_origin_admin_write_is_rejected(self):
        admin = self._admin_client()
        response = admin.post(
            "/api/users",
            headers={"Origin": "https://example.com"},
            json={
                "username": "blocked",
                "password": "blocked-password-123",
                "role": "user",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_fetch_header_accepts_local_alias(self):
        admin = self._admin_client()
        response = admin.post(
            "/favorites/{}/add".format(self.video_id),
            headers={
                "Host": "127.0.0.1:8766",
                "Origin": "http://localhost:8766",
                "Sec-Fetch-Site": "same-origin",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def test_loopback_aliases_match_when_fetch_header_is_unavailable(self):
        admin = self._admin_client()
        response = admin.post(
            "/favorites/{}/add".format(self.video_id),
            headers={
                "Host": "127.0.0.1:8766",
                "Origin": "http://localhost:8766",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def test_same_site_different_port_is_rejected(self):
        admin = self._admin_client()
        response = admin.post(
            "/favorites/{}/add".format(self.video_id),
            headers={
                "Host": "127.0.0.1:8766",
                "Origin": "http://127.0.0.1:9000",
                "Sec-Fetch-Site": "same-site",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
