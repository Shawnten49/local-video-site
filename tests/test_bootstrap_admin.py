import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site import database
from local_video_site.app import app
from local_video_site.core import config
from local_video_site.identity.accounts import verify_password
from local_video_site.identity.bootstrap import (
    default_admin_pending_change,
    ensure_bootstrap_admin,
)
from local_video_site.cli.commands.serve import publish_error
from local_video_site.settings import reset_runtime_settings


class BootstrapAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(
                config,
                "DELETE_PASSWORD_PATH",
                self.data_dir / ".delete-password",
            ),
            patch.object(config, "DEFAULT_ROOTS", []),
        )
        for item in self.patches:
            item.start()
        reset_runtime_settings()
        database.init_db()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        reset_runtime_settings()
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_empty_database_gets_default_admin_once(self):
        first = ensure_bootstrap_admin()
        second = ensure_bootstrap_admin()
        user = database.get_user_by_normalized_username("admin")

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "already_exists")
        self.assertEqual(database.user_count(), 1)
        self.assertEqual(user["username"], "admin")
        self.assertEqual(user["role"], "admin")
        self.assertEqual(user["is_active"], 1)
        self.assertEqual(user["must_change_password"], 1)
        self.assertTrue(
            verify_password("123456", user["password_hash"])
        )
        self.assertNotIn("123456", user["password_hash"])
        self.assertTrue(default_admin_pending_change())

    def test_bootstrap_never_overrides_existing_users(self):
        database.create_user(
            username="existing",
            normalized_username="existing",
            password_hash="test-hash",
            role="user",
        )

        result = ensure_bootstrap_admin()

        self.assertEqual(result["status"], "skipped_users_exist")
        self.assertEqual(database.user_count(), 1)
        self.assertIsNone(
            database.get_user_by_normalized_username("admin")
        )

    def test_default_admin_must_change_password_before_management_access(self):
        ensure_bootstrap_admin()
        client = TestClient(app)
        login = client.post(
            "/login",
            data={"username": "admin", "password": "123456"},
            follow_redirects=False,
        )

        self.assertEqual(login.status_code, 303)
        self.assertEqual(login.headers["location"], "/account")
        self.assertEqual(
            client.get(
                "/manage/videos",
                follow_redirects=False,
            ).status_code,
            303,
        )
        self.assertEqual(
            client.get("/api/manage/settings").status_code,
            403,
        )
        self.assertEqual(client.get("/account").status_code, 200)

        changed = client.post(
            "/api/account/password",
            json={
                "current_password": "123456",
                "new_password": "strong-password-456",
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(default_admin_pending_change())
        self.assertEqual(
            client.get("/manage/videos").status_code,
            200,
        )

    def test_lan_mode_rejects_pending_default_admin(self):
        result = ensure_bootstrap_admin()
        user = database.get_user(int(result["user_id"]))
        with patch.object(config, "lan_mode", return_value=True):
            self.assertIn(
                "尚未修改初始密码",
                publish_error(),
            )
            with patch.object(
                config,
                "allow_default_admin_lan",
                return_value=True,
            ):
                self.assertEqual(publish_error(), "")

        database.update_user_password(
            int(user["id"]),
            "pbkdf2_sha256$210000$00$00",
            must_change_password=False,
        )
        self.assertFalse(default_admin_pending_change())


if __name__ == "__main__":
    unittest.main()
