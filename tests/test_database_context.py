import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database


class DatabaseContextTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.patches = (
            patch.object(config, "DATA_DIR", self.root / "data"),
            patch.object(
                config,
                "DATABASE_PATH",
                self.root / "data" / "library.db",
            ),
            patch.object(
                config,
                "THUMBNAIL_DIR",
                self.root / "data" / "thumbnails",
            ),
            patch.object(
                config,
                "COVER_DIR",
                self.root / "data" / "covers",
            ),
            patch.object(
                config,
                "ACTOR_AVATAR_DIR",
                self.root / "data" / "actor-avatars",
            ),
            patch.object(
                config,
                "PREVIEW_DIR",
                self.root / "data" / "previews",
            ),
            patch.object(
                config,
                "DOWNLOAD_TASK_LOG_DIR",
                self.root / "data" / "download-task-logs",
            ),
            patch.object(
                config,
                "DOWNLOAD_CONTROL_DIR",
                self.root / "data" / "download-task-controls",
            ),
            patch.object(
                config,
                "DELETE_PASSWORD_PATH",
                self.root / "data" / ".delete-password",
            ),
            patch.object(
                config,
                "DELETE_QUARANTINE_DIR",
                self.root / "data" / "delete-quarantine",
            ),
            patch.object(
                config,
                "LOG_PATH",
                self.root / "data" / "logs" / "server.log",
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

    def test_connection_is_closed_after_context_exit(self):
        with database.connect() as connection:
            connection.execute("SELECT 1").fetchone()

        with self.assertRaises(Exception):
            connection.execute("SELECT 1")

    def test_success_is_committed(self):
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    username, normalized_username, password_hash,
                    role, created_at, updated_at
                ) VALUES ('commit-user', 'commit-user', 'hash',
                          'user', 'now', 'now')
                """
            )

        with database.connect() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE normalized_username = 'commit-user'
                """
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_exception_is_rolled_back(self):
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        username, normalized_username, password_hash,
                        role, created_at, updated_at
                    ) VALUES ('rollback-user', 'rollback-user', 'hash',
                              'user', 'now', 'now')
                    """
                )
                raise RuntimeError("rollback")

        with database.connect() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE normalized_username = 'rollback-user'
                """
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
