import sqlite3
import tempfile
import threading
import unittest
import importlib
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.db.migration_lock import (
    MigrationLockTimeout,
    migration_lock,
)
from local_video_site.db.migrations import (
    Migration,
    applied_migrations,
    source_checksum,
)
from local_video_site.db.vocabulary import COLUMNS, VALUE_SETS, is_enum_candidate
from local_video_site.jobs.integrity import latest_integrity_report, persist_integrity_report, run_runtime_integrity_check
from local_video_site.jobs.integrity_tasks import claim_integrity_job, process_integrity_job
from tests.auth_test_utils import admin_client


class VersionedMigrationAndIntegrityTests(unittest.TestCase):
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
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_schema_migrations_are_recorded_once(self):
        first = applied_migrations()
        self.assertEqual(
            [item["version"] for item in first],
            [1, 2, 3, 4, 5, 6, 7],
        )
        self.assertTrue(all(item["checksum"] for item in first))
        self.assertEqual(
            first[0]["checksum"],
            source_checksum(database.baseline_schema_functions()),
        )

        database.init_db()
        second = applied_migrations()
        self.assertEqual(first, second)

    def test_data_dictionary_covers_all_candidate_columns(self):
        registered = {
            (item.table_name, item.column_name)
            for item in COLUMNS
        }
        candidates = set()
        with database.connect() as connection:
            tables = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                  AND name NOT LIKE 'schema_%'
                """
            ).fetchall()
            for table in tables:
                table_name = str(table["name"])
                for column in connection.execute(
                    'PRAGMA table_info("{}")'.format(
                        table_name.replace('"', '""')
                    )
                ):
                    if is_enum_candidate(str(column["name"])):
                        candidates.add(
                            (table_name, str(column["name"]))
                        )

        self.assertEqual(candidates - registered, set())

    def test_data_dictionary_schema_and_defaults_are_consistent(self):
        value_sets = {item.key: item for item in VALUE_SETS}
        self.assertEqual(len(value_sets), len(VALUE_SETS))
        self.assertEqual(
            len({(item.table_name, item.column_name) for item in COLUMNS}),
            len(COLUMNS),
        )

        with database.connect() as connection:
            for column in COLUMNS:
                value_set = value_sets.get(column.set_key)
                self.assertIsNotNone(
                    value_set,
                    "{} has unknown set {}".format(
                        (column.table_name, column.column_name),
                        column.set_key,
                    ),
                )
                information = {
                    item["name"]: item
                    for item in connection.execute(
                        'PRAGMA table_info("{}")'.format(
                            column.table_name.replace('"', '""')
                        )
                    )
                }.get(column.column_name)
                self.assertIsNotNone(
                    information,
                    "{} no longer exists".format(
                        (column.table_name, column.column_name)
                    ),
                )
                self.assertEqual(bool(information["notnull"]), not column.nullable)

                allowed = {term.value for term in value_set.values}
                if column.default_value is not None:
                    actual_default = information["dflt_value"]
                    if actual_default is not None:
                        actual_default = str(actual_default).strip("'")
                    self.assertEqual(actual_default, column.default_value)
                    if allowed:
                        self.assertIn(column.default_value, allowed)

        with database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM schema_column_summary
                WHERE table_name = 'videos'
                  AND column_name = 'missing'
                """
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("0=否", row["allowed_values"])
        self.assertIn("1=是", row["allowed_values"])

    def test_strict_vocabulary_triggers_reject_invalid_values(self):
        with database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    username, normalized_username, password_hash, role,
                    is_active, must_change_password, created_at, updated_at
                ) VALUES (
                    'vocabulary-user', 'vocabulary-user', 'hash', 'user',
                    1, 0, 'now', 'now'
                )
                """
            )
            user_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE id = ?",
                (user_id,),
            )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "schema vocabulary violation: users.role",
            ):
                connection.execute(
                    "UPDATE users SET role = 'owner' WHERE id = ?",
                    (user_id,),
                )

    def test_migration_checksum_covers_real_statements(self):
        first = Migration(3, "same-name", ("CREATE TABLE sample (id);",))
        second = Migration(3, "same-name", ("CREATE TABLE changed (id);",))
        self.assertNotEqual(first.checksum, second.checksum)

    def test_long_title_migration_backfills_display_only(self):
        video_path = self.root / "legacy-18av.mp4"
        video_path.write_bytes(b"video")
        legacy_title = (
            "無碼]HEYZO-1632 最新heyzo.com 1632 美癡女 外觀乾淨 "
            "小野寺梨紗 18AV在線H成人影片-18AV萬部免費影片與您分享,"
            "免費AV在線看,線上a片直播"
        )
        database.upsert_video(
            path=video_path,
            title=legacy_title,
            directory=self.root.name,
            size=video_path.stat().st_size,
            mtime_ns=video_path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(video_path)
        task_id = database.create_download_task(
            batch_id="legacy-title",
            url="https://18av.mm-cg.com/zh/uncensored_content/1/example.html",
            normalized_url="https://18av.mm-cg.com/zh/uncensored_content/1/example.html",
            metadata_url="https://18av.mm-cg.com/zh/uncensored_content/1/example.html",
            normalized_metadata_url="https://18av.mm-cg.com/zh/uncensored_content/1/example.html",
            output_dir=str(self.root),
            max_attempts=3,
        )
        database.update_download_task(
            task_id,
            title=legacy_title,
            video_id=video["id"],
        )

        migration = importlib.import_module(
            "local_video_site.db.migrations.0006_long_title_display"
        ).long_title_display_migration
        with database.connect() as connection:
            migration.apply(connection)

        updated = database.get_video(video["id"])
        task = database.get_download_task(task_id)
        expected = (
            "無碼]HEYZO-1632 最新heyzo.com 1632 美癡女 外觀乾淨 "
            "小野寺梨紗"
        )
        self.assertEqual(updated["title"], expected)
        self.assertEqual(updated["display_title_override"], expected)
        self.assertEqual(updated["path"], str(video_path))
        self.assertEqual(task["display_title"], expected)
        self.assertEqual(task["title"], legacy_title)

    def test_migration_lock_rejects_concurrent_holder(self):
        acquired = threading.Event()
        release = threading.Event()

        def hold_lock():
            with migration_lock():
                acquired.set()
                release.wait(timeout=2)

        thread = threading.Thread(target=hold_lock)
        thread.start()
        self.assertTrue(acquired.wait(timeout=2))
        try:
            with self.assertRaises(MigrationLockTimeout):
                with migration_lock(timeout_seconds=0.1):
                    pass
        finally:
            release.set()
            thread.join(timeout=2)

    def test_runtime_integrity_detects_and_persists_orphans(self):
        clean = run_runtime_integrity_check()
        self.assertTrue(clean["ok"], clean)

        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO video_media_info (video_id, updated_at)
                VALUES (999999, 'now')
                """
            )

        report = run_runtime_integrity_check()
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["issue_count"], 1)
        orphan = next(
            item for item in report["checks"]
            if item["name"] == "orphan_video_media_info"
        )
        self.assertEqual(orphan["count"], 1)

        run_id = persist_integrity_report(report)
        latest = latest_integrity_report()
        self.assertEqual(latest["id"], run_id)
        self.assertEqual(latest["status"], "degraded")
        self.assertEqual(latest["issue_count"], report["issue_count"])
        self.assertEqual(latest["checks"], report["checks"])

    def test_integrity_routes_are_admin_only(self):
        client = admin_client()
        queued = client.post("/api/manage/integrity/run")
        self.assertEqual(queued.status_code, 202, queued.text)
        job = claim_integrity_job()
        self.assertEqual(job["job_id"], queued.json()["job_id"])
        result = process_integrity_job(job)
        self.assertEqual(result.outcome.value, "success")

        status = client.get("/api/manage/integrity")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertTrue(status.json()["latest"]["id"])
        checked = client.get(
            "/api/manage/integrity/jobs/{}".format(job["job_id"])
        )
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(checked.json()["status"], "success")


if __name__ == "__main__":
    unittest.main()
