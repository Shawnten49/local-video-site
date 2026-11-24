import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.episodes.catalog import EpisodeCandidate, EpisodeCatalog
from tests.auth_test_utils import admin_client, user_client


class DownloadPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.download_dir = self.root / "download-hub"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "DEFAULT_ROOTS", [self.download_dir]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _create_task(self, client, suffix="one", **overrides):
        payload = {
            "mode": "normal",
            "output_dir": str(self.download_dir.resolve()),
            "quality": "site",
            "access_level": 0,
            "urls": ["https://example.com/video/{}".format(suffix)],
        }
        payload.update(overrides)
        return client.post("/api/download-tasks", json=payload)

    def test_user_can_open_downloads_but_not_admin_download_pages(self):
        client = user_client(username="alice")
        home = client.get("/")
        downloads = client.get("/downloads")
        batch = client.get("/downloads/batch")
        policies = client.get("/downloads/site-policies")

        self.assertNotIn('href="/downloads"', home.text)
        self.assertEqual(downloads.status_code, 200)
        self.assertIn(
            "download-nav-button is-active",
            downloads.text,
        )
        self.assertNotIn('href="/manage/videos"', downloads.text)
        self.assertNotIn('href="/manage/users"', downloads.text)
        self.assertIn('readonly', downloads.text)
        self.assertNotIn(
            '<option value="2"',
            downloads.text,
        )
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(policies.status_code, 403)

    def test_tasks_are_isolated_by_owner(self):
        alice = user_client(username="alice")
        bob = user_client(username="bob")
        admin = admin_client()
        created = self._create_task(alice, "owner-isolation")
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["task_ids"][0]
        task = database.get_download_task(task_id)
        self.assertEqual(task["created_by_username"], "alice")

        alice_page = alice.get("/downloads")
        bob_page = bob.get("/downloads")
        admin_page = admin.get("/downloads")
        self.assertIn("owner-isolation", alice_page.text)
        self.assertNotIn("owner-isolation", bob_page.text)
        self.assertIn("owner-isolation", admin_page.text)
        self.assertEqual(
            bob.get("/api/download-tasks/{}".format(task_id)).status_code,
            404,
        )
        self.assertEqual(
            bob.post(
                "/api/download-tasks/{}/pause".format(task_id)
            ).status_code,
            404,
        )

    def test_user_download_limits_and_admin_override(self):
        user = user_client(username="alice")
        admin = admin_client()

        custom_dir = self.root / "other"
        custom_dir.mkdir()
        forbidden_dir = self._create_task(
            user,
            "custom-dir",
            output_dir=str(custom_dir),
        )
        forbidden_level = self._create_task(
            user,
            "admin-level",
            access_level=2,
        )
        admin_task = self._create_task(
            admin,
            "admin-custom",
            output_dir=str(custom_dir),
            access_level=2,
        )

        self.assertEqual(forbidden_dir.status_code, 403)
        self.assertEqual(forbidden_level.status_code, 403)
        self.assertEqual(admin_task.status_code, 200)

    def test_user_can_pause_own_task_but_cannot_delete_files(self):
        user = user_client(username="alice")
        created = self._create_task(user, "pause-own")
        task_id = created.json()["task_ids"][0]

        paused = user.post(
            "/api/download-tasks/{}/pause".format(task_id)
        )
        deleted = user.request(
            "DELETE",
            "/api/download-tasks/{}".format(task_id),
            json={"password": ""},
        )

        self.assertEqual(paused.status_code, 200)
        self.assertEqual(deleted.status_code, 403)
        self.assertIn(
            database.get_download_task(task_id)["status"],
            {"pausing", "paused"},
        )

    def test_batch_owner_and_access_level_are_inherited(self):
        user = user_client(username="alice")
        catalog = EpisodeCatalog(
            provider="bilibili",
            detected_series_name="Series",
            detected_season_name="第一季",
            source_url="https://www.bilibili.com/bangumi/play/ep100",
            episodes=[
                EpisodeCandidate(
                    1,
                    "Title 1",
                    "https://www.bilibili.com/bangumi/play/ep100",
                ),
                EpisodeCandidate(
                    2,
                    "Title 2",
                    "https://www.bilibili.com/bangumi/play/ep101",
                ),
            ],
        )
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=catalog,
        ):
            discovered = user.post(
                "/api/episode-batches/discover",
                json={
                    "series_name": "Series",
                    "season_name": "第一季",
                    "season_number": 1,
                    "first_episode_url": catalog.source_url,
                    "output_root": str(self.download_dir.resolve()),
                    "quality": "site",
                    "access_level": 1,
                },
            )
        self.assertEqual(discovered.status_code, 200, discovered.text)
        batch = discovered.json()
        self.assertEqual(batch["created_by_username"], "alice")
        self.assertEqual(batch["access_level"], 1)

        confirmed = user.post(
            "/api/episode-batches/{}/confirm".format(batch["batch_id"]),
            json={
                "revision": batch["revision"],
                "selected_item_ids": [
                    item["id"] for item in batch["items"]
                ],
            },
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        tasks = database.get_download_tasks_by_ids(
            confirmed.json()["task_ids"]
        )
        self.assertTrue(all(task["created_by_username"] == "alice" for task in tasks))
        self.assertTrue(all(task["access_level"] == 1 for task in tasks))

        other = user_client(username="bob")
        self.assertEqual(
            other.get(
                "/api/episode-batches/{}".format(batch["batch_id"])
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
