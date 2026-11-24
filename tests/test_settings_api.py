import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site import database
from local_video_site.app import app
from local_video_site.core import config
from local_video_site.settings import reset_runtime_settings
from tests.auth_test_utils import admin_client, user_client


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(
                config,
                "DELETE_PASSWORD_PATH",
                self.data_dir / ".delete-password",
            ),
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

    def test_admin_can_open_settings_page_and_list_values(self):
        client = admin_client()
        page = client.get("/manage/settings")
        listing = client.get("/api/manage/settings?q=download.poll")

        self.assertEqual(page.status_code, 200)
        self.assertIn("参数配置", page.text)
        self.assertIn("settings-category-list", page.text)
        self.assertIn(
            "settings-nav-button is-active",
            page.text,
        )
        self.assertIn('href="/manage/settings"', page.text)
        self.assertLess(
            page.text.index(">账号管理</a>"),
            page.text.index(">参数配置</a>"),
        )
        self.assertNotIn(
            "account-section-tabs",
            page.text,
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            listing.json()["items"][0]["key"],
            "download.poll_seconds",
        )
        self.assertNotIn(
            "test-delete-secret",
            str(listing.json()),
        )

    def test_guest_and_normal_user_cannot_change_settings(self):
        guest = TestClient(app)
        user = user_client()

        self.assertEqual(
            guest.get("/manage/settings", follow_redirects=False).status_code,
            303,
        )
        self.assertEqual(
            user.get("/api/manage/settings").status_code,
            403,
        )

    def test_admin_update_records_audit_and_handles_version_conflict(self):
        client = admin_client()
        updated = client.patch(
            "/api/manage/settings/download.poll_seconds",
            json={
                "value": 7,
                "expected_version": 0,
                "note": "test update",
            },
        )
        conflict = client.patch(
            "/api/manage/settings/download.poll_seconds",
            json={
                "value": 8,
                "expected_version": 0,
                "note": "stale update",
            },
        )
        audit = client.get(
            "/api/manage/settings/audit?setting_key=download.poll_seconds"
        )

        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["value"], 7.0)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            audit.json()["items"][0]["new_display"],
            "7.0 秒",
        )

    def test_delete_verification_endpoint_never_returns_secret(self):
        client = admin_client()
        changed = client.post(
            "/api/manage/settings/secrets/delete-verification",
            json={
                "current_password": "",
                "new_password": "test-delete-secret",
                "confirmation": "test-delete-secret",
                "note": "test setup",
            },
        )

        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["configured"])
        self.assertNotIn("test-delete-secret", changed.text)
        self.assertTrue(
            (self.data_dir / ".delete-password").is_file()
        )


if __name__ == "__main__":
    unittest.main()
