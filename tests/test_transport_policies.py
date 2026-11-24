import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.downloads.tasks import create_tasks
from local_video_site.downloads.policies.transport import create_policy, ensure_site_for_url, get_policy, perform_action, record_task_result, update_policy, record_qualification, resolve_transport_for_task


class TransportPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.download_dir = self.root / "downloads"
        self.download_dir.mkdir()
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "DEFAULT_ROOTS", [self.download_dir]),
            patch.object(config, "scan_on_startup", return_value=False),
            patch.object(config, "download_worker_enabled", return_value=False),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_builtin_sites_are_seeded(self):
        policies = {
            key: get_policy(key)
            for key in ("pornone", "porntrex", "xvideos", "xhamster")
        }

        self.assertEqual(policies["pornone"]["transport_mode"], "direct-range")
        self.assertEqual(policies["pornone"]["lifecycle_state"], "VERIFIED")
        self.assertEqual(policies["porntrex"]["tls_backend"], "curl_cffi")
        self.assertEqual(policies["xvideos"]["transport_mode"], "hls")
        self.assertEqual(policies["xhamster"]["transport_mode"], "hls")

    def test_unknown_site_is_created_and_uses_qualification(self):
        policy = ensure_site_for_url(
            "https://www.new-site.example/video/1"
        )
        resolved = resolve_transport_for_task({
            "id": 1,
            "url": "https://www.new-site.example/video/1",
            "transport_site_key": policy["site_key"],
        })

        self.assertEqual(policy["lifecycle_state"], "UNKNOWN")
        self.assertEqual(resolved["child_mode"], "qualify")

    def test_qualification_and_manual_promotion(self):
        ensure_site_for_url("https://unknown.example/video/1")
        record_qualification("unknown.example", 1, True)
        qualified = get_policy("unknown.example")
        self.assertEqual(qualified["lifecycle_state"], "TASK_QUALIFIED")

        promoted = perform_action(
            "unknown.example",
            "promote_verified",
            note="manual verification",
        )
        self.assertEqual(promoted["lifecycle_state"], "VERIFIED")
        resolved = resolve_transport_for_task({
            "id": 2,
            "url": "https://unknown.example/video/2",
            "transport_site_key": "unknown.example",
        })
        self.assertEqual(resolved["child_mode"], "direct-range")

    def test_force_ytdlp_overrides_verified_site(self):
        perform_action("pornone", "force_ytdlp", note="maintenance")
        resolved = resolve_transport_for_task({
            "id": 1,
            "url": "https://pornone.com/video/1",
            "transport_site_key": "pornone",
        })
        self.assertEqual(resolved["child_mode"], "yt-dlp")

    def test_successful_hls_download_teaches_auto_site_mode(self):
        policy = ensure_site_for_url("https://hls-auto.example/video/1")
        record_task_result(
            {
                "id": 1,
                "transport_site_key": policy["site_key"],
                "transport_kind": "hls",
                "downloaded_bytes": 1024,
            },
            True,
        )

        learned = get_policy("hls-auto.example")
        self.assertEqual(learned["transport_mode"], "hls")
        self.assertEqual(
            learned["policy_version"],
            policy["policy_version"] + 1,
        )
        resolved = resolve_transport_for_task({
            "id": 2,
            "url": "https://hls-auto.example/video/2",
            "transport_site_key": "hls-auto.example",
        })
        self.assertEqual(resolved["child_mode"], "hls")

    def test_download_task_automatically_records_site(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://auto-added.example/video/1"],
        )
        task = database.get_download_task(report["task_ids"][0])

        self.assertEqual(task["transport_site_key"], "auto-added.example")
        self.assertEqual(
            get_policy("auto-added.example")["lifecycle_state"],
            "UNKNOWN",
        )

    def test_site_policy_pages_and_api(self):
        client = admin_client()
        page = client.get("/downloads/site-policies")
        api = client.get("/api/transport-sites")

        self.assertEqual(page.status_code, 200)
        self.assertIn("PornOne", page.text)
        self.assertIn("Porntrex", page.text)
        self.assertIn("站点策略", page.text)
        self.assertIn("data-edit-transport-site", page.text)
        self.assertIn("transport-site-edit-dialog", page.text)
        self.assertIn("状态修改说明", page.text)
        self.assertIn("show-transport-guide", page.text)
        self.assertIn("TASK_QUALIFIED", page.text)
        self.assertIn("curl_cffi", page.text)
        self.assertIn("视频选择策略", page.text)
        self.assertIn("媒体来源顺序", page.text)
        self.assertIn("视频身份校验", page.text)
        self.assertIn("优先最近的较低档", page.text)
        self.assertIn("任务策略快照", page.text)
        self.assertIn("试运行当前策略", page.text)
        self.assertEqual(api.status_code, 200)
        keys = {item["site_key"] for item in api.json()["items"]}
        self.assertIn("pornone", keys)
        self.assertIn("porntrex", keys)

    def test_create_update_and_action_api(self):
        client = admin_client()
        created = client.post(
            "/api/transport-sites",
            json={
                "site_key": "manual.example",
                "display_name": "Manual",
                "domains": ["manual.example"],
                "transport_mode": "auto",
                "lifecycle_state": "UNKNOWN",
                "initial_connections": 2,
                "max_connections": 4,
                "tls_backend": "auto",
            },
        )
        self.assertEqual(created.status_code, 200)
        updated = client.patch(
            "/api/transport-sites/manual.example",
            json={"status_note": "test note"},
        )
        self.assertEqual(updated.status_code, 200)
        promoted = client.post(
            "/api/transport-sites/manual.example/actions",
            json={
                "action": "promote_verified",
                "note": "manual approval",
            },
        )
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.json()["lifecycle_state"], "VERIFIED")

    def test_half_open_breaker_closes_after_success(self):
        ensure_site_for_url("https://breaker.example/video/1")
        perform_action(
            "breaker.example",
            "promote_verified",
            note="approve",
        )
        rechecked = perform_action("breaker.example", "recheck")
        self.assertEqual(rechecked["breaker_state"], "HALF_OPEN")

        record_task_result({
            "id": 1,
            "transport_site_key": "breaker.example",
            "transport_kind": "direct-range",
            "downloaded_bytes": 1024,
        }, True)
        self.assertEqual(get_policy("breaker.example")["breaker_state"], "CLOSED")

    def test_edit_policy_fields_state_side_effects_and_audit(self):
        ensure_site_for_url("https://edit.example/video/1")
        original = get_policy("edit.example")
        updated = update_policy(
            "edit.example",
            {
                "display_name": "Edited Site",
                "domains": ["edit.example", "www.edit.example"],
                "transport_mode": "direct-range",
                "lifecycle_state": "VERIFIED",
                "breaker_state": "OPEN",
                "cooldown_minutes": 60,
                "initial_connections": 2,
                "max_connections": 3,
                "tls_backend": "curl_cffi",
                "note": "manual approval",
                "expected_policy_version": original["policy_version"],
                "expected_updated_at": original["updated_at"],
            },
        )

        self.assertEqual(updated["display_name"], "Edited Site")
        self.assertEqual(updated["lifecycle_state"], "VERIFIED")
        self.assertEqual(updated["breaker_state"], "OPEN")
        self.assertEqual(updated["max_connections"], 3)
        self.assertEqual(updated["tls_backend"], "curl_cffi")
        self.assertEqual(updated["policy_version"], original["policy_version"] + 1)
        self.assertTrue(updated["disabled_until"])
        self.assertEqual(updated["promoted_by"], "manual")

        closed = update_policy(
            "edit.example",
            {"breaker_state": "CLOSED", "max_connections": 4},
        )
        self.assertEqual(closed["breaker_state"], "CLOSED")
        self.assertIsNone(closed["disabled_until"])

    def test_verified_state_change_requires_note_and_version_conflict_fails(self):
        ensure_site_for_url("https://validation.example/video/1")
        policy = get_policy("validation.example")
        with self.assertRaisesRegex(ValueError, "备注"):
            update_policy(
                "validation.example",
                {"lifecycle_state": "VERIFIED"},
            )
        with self.assertRaisesRegex(ValueError, "其他页面"):
            update_policy(
                "validation.example",
                {
                    "max_connections": 3,
                    "expected_policy_version": policy["policy_version"] - 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
