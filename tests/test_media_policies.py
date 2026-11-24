import json
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
from local_video_site.downloads.policies.media import get_effective_policy, policy_for_task, reset_policy, update_policy
from local_video_site.downloader.resolution import MediaCatalog, MediaPolicy
from local_video_site.downloader.sniffer import MediaCandidate


class MediaPolicyTests(unittest.TestCase):
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

    def test_builtin_and_user_policy_are_resolved_with_fallback(self):
        builtin = get_effective_policy("xhamster")
        generic = get_effective_policy("unknown.example")
        sxyprn = get_effective_policy("sxyprn.com")
        bilibili = get_effective_policy("bilibili.com")
        self.assertEqual(builtin["preset_key"], "xhamster")
        self.assertEqual(builtin["source_kind"], "builtin")
        self.assertEqual(generic["preset_key"], "generic")
        self.assertEqual(sxyprn["preset_key"], "sxyprn")
        self.assertEqual(sxyprn["quality_target"], "source_first")
        self.assertEqual(bilibili["preset_key"], "bilibili")
        self.assertEqual(bilibili["format_strategy"], "separate_av")
        self.assertTrue(bilibili["prefer_h264"])
        self.assertEqual(bilibili["merge_output_format"], "mp4")

        updated = update_policy(
            "xhamster",
            {
                "preset_key": "xhamster",
                "quality_target": "720p",
                "source_order": ["network", "player-config"],
                "kind_order": ["hls", "file"],
                "identity_mode": "site_default",
                "quality_fallback": "best_effort",
                "min_height": 360,
                "max_height": 1080,
                "allow_below_minimum": False,
                "allow_page_fallback": True,
                "play_selector": ".play",
                "format_strategy": "separate_av",
                "prefer_h264": True,
                "merge_output_format": "mkv",
                "expected_version": builtin["version"],
                "note": "test override",
            },
        )

        self.assertEqual(updated["quality_target"], "720p")
        self.assertEqual(updated["source_order"], ["network", "player-config"])
        self.assertEqual(updated["source_kind"], "user")
        self.assertEqual(updated["version"], builtin["version"] + 1)
        self.assertEqual(updated["format_strategy"], "separate_av")
        self.assertTrue(updated["prefer_h264"])
        self.assertEqual(updated["merge_output_format"], "mkv")

        reset = reset_policy("xhamster")
        self.assertEqual(reset["source_kind"], "builtin")
        self.assertEqual(reset["quality_target"], "auto")

    def test_4k_task_policy_overrides_only_snapshot_limit(self):
        before = get_effective_policy("generic.example")
        snapshot = policy_for_task("generic.example", "2160p")
        after = get_effective_policy("generic.example")

        self.assertEqual(snapshot["quality_target"], "2160p")
        self.assertEqual(snapshot["max_height"], 2160)
        self.assertEqual(snapshot["quality_fallback"], "nearest_below")
        self.assertEqual(before["max_height"], 1080)
        self.assertEqual(after["max_height"], 1080)

    def test_media_policy_read_repairs_older_database_schema(self):
        with database.connect() as connection:
            connection.execute("DROP TABLE site_media_policies")
            connection.commit()

        policy = get_effective_policy("xhamster")
        with database.connect() as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'site_media_policies'"
            ).fetchone()

        self.assertEqual(policy["preset_key"], "xhamster")
        self.assertIsNotNone(table)

    def test_media_policy_api_and_detail_page(self):
        client = admin_client()
        detail = client.get("/downloads/site-policies/xhamster")
        policy_api = client.get(
            "/api/transport-sites/xhamster/media-policy"
        )

        self.assertEqual(detail.status_code, 200)
        self.assertIn("视频选择策略", detail.text)
        self.assertIn('id="media-policy-form"', detail.text)
        self.assertIn("恢复站点默认", detail.text)
        self.assertIn('id="probe-media-policy"', detail.text)
        self.assertIn("优先正确媒体来源", detail.text)
        self.assertIn("分离音视频并合并", detail.text)
        self.assertEqual(policy_api.status_code, 200)
        current = policy_api.json()["policy"]

        updated = client.patch(
            "/api/transport-sites/xhamster/media-policy",
            json={
                "preset_key": "xhamster",
                "quality_target": "480p",
                "source_order": [
                    "network",
                    "player-config",
                    "video-element",
                ],
                "kind_order": ["hls", "file"],
                "identity_mode": "site_default",
                "quality_fallback": "nearest_below",
                "min_height": 360,
                "max_height": 1080,
                "allow_below_minimum": False,
                "allow_page_fallback": True,
                "play_selector": "",
                "format_strategy": "combined",
                "prefer_h264": False,
                "merge_output_format": "mp4",
                "expected_version": current["version"],
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["quality_target"], "480p")
        self.assertEqual(
            updated.json()["format_strategy"],
            "combined",
        )

        reset = client.post(
            "/api/transport-sites/xhamster/media-policy/reset"
        )
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json()["source_kind"], "builtin")

        mismatched_probe = client.post(
            "/api/transport-sites/xhamster/media-policy/probe",
            json={"url": "https://example.com/video/1"},
        )
        self.assertEqual(mismatched_probe.status_code, 422)

    def test_task_uses_site_policy_or_explicit_quality_override(self):
        current = get_effective_policy("xhamster")
        update_policy(
            "xhamster",
            {
                "preset_key": "xhamster",
                "quality_target": "720p",
                "expected_version": current["version"],
            },
        )
        site_task = create_tasks(
            str(self.download_dir),
            ["https://www.xhamster.com/videos/example-123"],
        )["task_ids"][0]
        explicit_task = create_tasks(
            str(self.download_dir),
            ["https://www.xhamster.com/videos/explicit-456"],
            preferred_quality="480p",
        )["task_ids"][0]

        site = database.get_download_task(site_task)
        explicit = database.get_download_task(explicit_task)
        snapshot = json.loads(site["media_policy_snapshot_json"])
        explicit_snapshot = json.loads(
            explicit["media_policy_snapshot_json"]
        )

        self.assertEqual(site["requested_quality"], "site")
        self.assertEqual(site["media_strategy_key"], "xhamster")
        self.assertEqual(snapshot["quality_target"], "720p")
        self.assertEqual(explicit["requested_quality"], "480p")
        self.assertEqual(explicit_snapshot["quality_target"], "480p")

    def test_selection_plan_filters_unrelated_media_and_honors_target(self):
        unrelated = MediaCandidate(
            url="https://ads.example/preview-1080p.mp4",
            source="video-element",
            score=999,
            height=1080,
            matches_page_video=False,
        )
        matching_720 = MediaCandidate(
            url="https://cdn.example/video-720p.mp4",
            source="network",
            score=120,
            height=720,
            matches_page_video=True,
        )
        matching_480 = MediaCandidate(
            url="https://cdn.example/video-480p.mp4",
            source="player-config",
            score=200,
            height=480,
            matches_page_video=True,
        )
        catalog = MediaCatalog(
            [unrelated, matching_720, matching_480],
            policy=MediaPolicy(quality_target="720p"),
        )

        plan = catalog.plan("site")

        self.assertIs(plan.primary, matching_720)
        self.assertNotIn(unrelated, plan.ordered)
        self.assertEqual(plan.quality_target, "720p")

    def test_source_first_preset_prefers_network_over_quality_preview(self):
        preview = MediaCandidate(
            url="https://preview.example/library/720p.mp4",
            source="video-element",
            score=118,
            height=720,
            matches_page_video=False,
        )
        main_media = MediaCandidate(
            url="https://sxyprn.com/cdn8/video.vid",
            source="network",
            score=35,
            matches_page_video=False,
        )
        policy = get_effective_policy("sxyprn.com")
        catalog = MediaCatalog(
            [preview, main_media],
            policy=MediaPolicy.from_dict(policy),
        )

        plan = catalog.plan("site")

        self.assertIs(plan.primary, main_media)
        self.assertEqual(plan.quality_target, "source_first")


if __name__ == "__main__":
    unittest.main()
