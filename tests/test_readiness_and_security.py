import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from local_video_site.app import app
from local_video_site.jobs.readiness import readiness_snapshot


class ReadinessAndSecurityTests(unittest.TestCase):
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
            patch.dict(
                os.environ,
                {
                    "LOCAL_VIDEO_DOWNLOAD_WORKER": "0",
                    "LOCAL_VIDEO_PREVIEW_ENABLED": "0",
                    "LOCAL_VIDEO_ACTOR_AVATAR_SYNC": "0",
                    "LOCAL_VIDEO_MIGRATION_WORKER": "0",
                    "LOCAL_VIDEO_EXTERNAL_SCAN_WORKER": "0",
                    "LOCAL_VIDEO_TRIM_WORKER": "0",
                    "LOCAL_VIDEO_FORMAT_WORKER": "0",
                    "LOCAL_VIDEO_DELETE_WORKER": "0",
                    "LOCAL_VIDEO_INTEGRITY_WORKER": "0",
                    "LOCAL_VIDEO_SCAN_ON_STARTUP": "0",
                    "LOCAL_VIDEO_CATEGORY_FETCH_ON_STARTUP": "0",
                },
                clear=False,
            ),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _state(self, worker_alive=True):
        thread = SimpleNamespace(is_alive=lambda: worker_alive)
        return SimpleNamespace(
            lifespan_ready=True,
            storage_volume_worker=SimpleNamespace(thread=thread),
        )

    def test_readiness_checks_database_storage_and_workers(self):
        snapshot = readiness_snapshot(self._state())
        self.assertTrue(snapshot["ready"], snapshot)
        self.assertEqual(snapshot["checks"]["database"]["status"], "ready")
        self.assertEqual(snapshot["checks"]["storage"]["status"], "ready")
        self.assertEqual(
            snapshot["checks"]["workers"]["workers"]["storage_volume"]["status"],
            "running",
        )

        stopped = readiness_snapshot(self._state(worker_alive=False))
        self.assertFalse(stopped["ready"])
        self.assertEqual(stopped["status"], "not_ready")

    def test_readiness_endpoint_is_not_ready_before_lifespan(self):
        client = TestClient(app)
        response = client.get("/api/ready")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.headers["retry-after"], "5")

    def test_readiness_endpoint_is_ready_after_lifespan_start(self):
        with TestClient(app) as client:
            response = client.get("/api/ready")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ready"], payload)
        self.assertEqual(payload["checks"]["database"]["status"], "ready")
        self.assertEqual(payload["checks"]["storage"]["status"], "ready")
        self.assertEqual(
            payload["checks"]["workers"]["workers"]["storage_volume"]["status"],
            "running",
        )

    def test_security_headers_are_applied_to_success_and_denial(self):
        client = TestClient(app)
        health = client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(
            health.headers["content-security-policy"].split(";")[0],
            "default-src 'self'",
        )
        self.assertEqual(health.headers["x-content-type-options"], "nosniff")
        self.assertEqual(health.headers["x-frame-options"], "DENY")
        self.assertEqual(health.headers["referrer-policy"], "no-referrer")
        self.assertIn("camera=()", health.headers["permissions-policy"])

        denied = client.get("/api/users")
        self.assertIn(denied.status_code, {401, 403})
        self.assertEqual(denied.headers["x-content-type-options"], "nosniff")
        self.assertEqual(denied.headers["x-frame-options"], "DENY")


if __name__ == "__main__":
    unittest.main()
