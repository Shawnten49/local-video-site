import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.jobs.leases import job_lease_manager


class JobLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
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
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(
                config,
                "ACTOR_AVATAR_DIR",
                self.data_dir / "actor-avatars",
            ),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(
                config,
                "DOWNLOAD_TASK_LOG_DIR",
                self.data_dir / "download-task-logs",
            ),
            patch.object(
                config,
                "DOWNLOAD_CONTROL_DIR",
                self.data_dir / "download-task-controls",
            ),
            patch.object(
                config,
                "DELETE_PASSWORD_PATH",
                self.data_dir / ".delete-password",
            ),
            patch.object(
                config,
                "DELETE_QUARANTINE_DIR",
                self.data_dir / "delete-quarantine",
            ),
            patch.object(
                config,
                "LOG_PATH",
                self.data_dir / "logs" / "server.log",
            ),
            patch.object(config, "job_lease_seconds", return_value=60),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_lease_is_exclusive_renewable_and_releasable(self):
        self.assertTrue(job_lease_manager.acquire("worker-a", "1"))
        self.assertFalse(job_lease_manager.acquire("worker-a", "1"))
        self.assertTrue(job_lease_manager.renew("worker-a", "1"))
        leases = job_lease_manager.active()
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0]["job_id"], "1")
        job_lease_manager.release("worker-a", "1")
        self.assertEqual(job_lease_manager.active(), [])

    def test_expired_lease_can_be_reacquired(self):
        self.assertTrue(job_lease_manager.acquire("worker-b", "2"))
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE worker_leases
                SET expires_at = '2000-01-01T00:00:00+08:00'
                WHERE job_id = '2'
                """
            )
        self.assertTrue(job_lease_manager.acquire("worker-b", "2"))


if __name__ == "__main__":
    unittest.main()
