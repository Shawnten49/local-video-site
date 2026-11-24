import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site import database
from local_video_site.core import config
from local_video_site.jobs.readiness import _settings_check
from local_video_site.settings import (
    SettingError,
    get_runtime_settings,
    reset_runtime_settings,
)
from local_video_site.storage.deletion import (
    delete_password_configured,
    ensure_delete_password,
    verify_delete_password,
)


class SettingsServiceTests(unittest.TestCase):
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

    def test_environment_overrides_database_value_and_locks_setting(self):
        settings = get_runtime_settings()
        settings.update(
            "download.poll_seconds",
            9,
            expected_version=0,
            actor_user_id=1,
            actor_username="admin",
            note="test override",
        )
        self.assertEqual(config.download_poll_seconds(), 9.0)

        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_DOWNLOAD_POLL_SECONDS": "12"},
            clear=False,
        ):
            self.assertEqual(config.download_poll_seconds(), 12.0)
            item = settings.get_item("download.poll_seconds")
            self.assertEqual(item["source"], "environment")
            self.assertTrue(item["env_locked"])
            with self.assertRaises(SettingError) as context:
                settings.update(
                    "download.poll_seconds",
                    7,
                    expected_version=1,
                    actor_user_id=1,
                    actor_username="admin",
                    note="blocked",
                )
            self.assertEqual(context.exception.code, "setting_env_locked")

    def test_reset_clears_database_override(self):
        settings = get_runtime_settings()
        settings.update(
            "security.min_password_length",
            14,
            expected_version=0,
            actor_user_id=1,
            actor_username="admin",
            note="test override",
        )
        self.assertEqual(
            settings.get_int("security.min_password_length"),
            14,
        )

        settings.reset(
            "security.min_password_length",
            expected_version=1,
            actor_user_id=1,
            actor_username="admin",
            note="test reset",
        )

        self.assertEqual(
            settings.get_int("security.min_password_length"),
            12,
        )

    def test_delete_verification_is_masked_and_audit_contains_no_secret(self):
        settings = get_runtime_settings()
        changed = settings.change_delete_verification(
            current_password="",
            new_password="test-delete-secret",
            confirmation="test-delete-secret",
            actor_user_id=1,
            actor_username="admin",
            client_ip="127.0.0.1",
            request_id="request-1",
            note="initial setup",
        )

        self.assertTrue(changed["configured"])
        self.assertEqual(changed["display_value"], "已配置")
        self.assertIsNone(changed["value"])
        self.assertNotIn(
            "test-delete-secret",
            config.DELETE_PASSWORD_PATH.read_text(encoding="utf-8"),
        )
        audit = settings.audit(
            "security.delete_verification"
        )["items"][0]
        serialized = str(audit)
        self.assertNotIn("test-delete-secret", serialized)
        self.assertEqual(audit["old_display"], "未配置")
        self.assertEqual(audit["new_display"], "已配置")

    def test_missing_delete_verification_is_not_created_automatically(self):
        ensure_delete_password()

        self.assertFalse(delete_password_configured())
        self.assertFalse(verify_delete_password("anything"))
        self.assertFalse(config.DELETE_PASSWORD_PATH.exists())

    def test_delete_verification_rotation_requires_current_code(self):
        settings = get_runtime_settings()
        settings.change_delete_verification(
            current_password="",
            new_password="initial-delete-secret",
            confirmation="initial-delete-secret",
            actor_user_id=1,
            actor_username="admin",
        )

        with self.assertRaises(SettingError) as context:
            settings.change_delete_verification(
                current_password="wrong-current",
                new_password="rotated-delete-secret",
                confirmation="rotated-delete-secret",
                actor_user_id=1,
                actor_username="admin",
            )
        self.assertEqual(
            context.exception.code,
            "delete_verification_invalid",
        )
        self.assertTrue(
            verify_delete_password("initial-delete-secret")
        )

        settings.change_delete_verification(
            current_password="initial-delete-secret",
            new_password="rotated-delete-secret",
            confirmation="rotated-delete-secret",
            actor_user_id=1,
            actor_username="admin",
        )
        self.assertFalse(
            verify_delete_password("initial-delete-secret")
        )
        self.assertTrue(
            verify_delete_password("rotated-delete-secret")
        )

    def test_startup_only_setting_is_read_only(self):
        settings = get_runtime_settings()
        item = settings.get_item("system.database_path")

        self.assertTrue(item["display_value"].endswith("library.db"))
        self.assertFalse(item["mutable"])
        with self.assertRaises(SettingError) as context:
            settings.update(
                "system.database_path",
                "/tmp/other.db",
                expected_version=0,
                actor_user_id=1,
                actor_username="admin",
                note="blocked",
            )
        self.assertEqual(context.exception.code, "setting_read_only")

    def test_invalid_environment_value_fails_settings_readiness(self):
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_PREVIEW_CRF": "99"},
            clear=False,
        ):
            result = _settings_check()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid")
        self.assertIn("LOCAL_VIDEO_PREVIEW_CRF", result["error"])


if __name__ == "__main__":
    unittest.main()
