import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site.storage import deletion as delete_service
from local_video_site.storage import migration_tasks
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.storage.deletion import set_delete_password
from local_video_site.media.playback.state import get_playback_state


TEST_DELETE_SECRET = "test-delete-secret"


class VideoMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.local_root = self.root / "download-hub"
        self.local_root.mkdir()
        self.external_root = self.root / "external-library"
        self.external_root.mkdir()
        self.restore_root = self.root / "restored"
        self.restore_root.mkdir()
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
            patch.object(config, "DEFAULT_ROOTS", [self.local_root]),
            patch.object(config, "DELETE_QUARANTINE_DIR", self.data_dir / "delete-quarantine"),
            patch.object(config, "migration_min_free_bytes", return_value=0),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        set_delete_password(TEST_DELETE_SECRET)

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _create_video(self):
        source = self.local_root / "series" / "episode.mp4"
        source.parent.mkdir()
        source.write_bytes(b"video-content-" * 1024)
        stat = source.stat()
        database.upsert_video(
            path=source,
            title="Episode",
            directory=self.local_root.name,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        video = database.get_video_by_path(source)
        thumb = config.THUMBNAIL_DIR / "episode.jpg"
        preview = config.PREVIEW_DIR / "episode-preview.mp4"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        preview.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"thumbnail")
        preview.write_bytes(b"preview")
        database.update_metadata(
            video["id"],
            {
                "width": 1920,
                "height": 1080,
                "duration": 60,
            },
            thumbnail_path=str(thumb),
        )
        database.save_preview_result(
            video["id"],
            "ready",
            preview_path=preview.name,
            start=1,
            duration=8,
        )
        return database.get_video_admin(video["id"]), thumb, preview

    def _run_next(self):
        task = migration_tasks.claim_migration()
        self.assertIsNotNone(task)
        migration_tasks.process_migration(task)
        return migration_tasks.get_migration(task["id"])

    def test_round_trip_preserves_video_identity_and_local_derivatives(self):
        video, thumbnail, preview = self._create_video()

        outgoing = migration_tasks.create_batch(
            mode="manual",
            direction="to_external",
            destination_root=str(self.external_root),
            video_ids=[video["id"]],
        )
        outgoing_task = self._run_next()
        moved = database.get_video_admin(video["id"])
        external_file = (
            self.external_root / "download-hub" / "series" / "episode.mp4"
        ).resolve()

        self.assertEqual(outgoing["created"], 1)
        self.assertEqual(outgoing_task["status"], "success")
        self.assertEqual(moved["id"], video["id"])
        self.assertEqual(Path(moved["path"]), external_file)
        self.assertIsNotNone(moved["storage_volume_id"])
        self.assertTrue(external_file.is_file())
        self.assertFalse((self.local_root / "series" / "episode.mp4").exists())
        self.assertTrue(thumbnail.is_file())
        self.assertTrue(preview.is_file())

        incoming = migration_tasks.create_batch(
            mode="manual",
            direction="to_local",
            destination_root=str(self.restore_root),
            video_ids=[video["id"]],
        )
        incoming_task = self._run_next()
        restored = database.get_video_admin(video["id"])
        restored_file = (
            self.restore_root / "download-hub" / "series" / "episode.mp4"
        ).resolve()

        self.assertEqual(incoming["created"], 1)
        self.assertEqual(incoming_task["status"], "success")
        self.assertEqual(restored["id"], video["id"])
        self.assertEqual(Path(restored["path"]), restored_file)
        self.assertIsNone(restored["storage_volume_id"])
        self.assertIsNone(restored["storage_relative_path"])
        self.assertTrue(restored_file.is_file())
        self.assertFalse(external_file.exists())
        self.assertEqual(Path(restored["thumbnail_path"]), thumbnail)
        self.assertEqual(restored["preview_path"], preview.name)

        history = migration_tasks.list_migrations(
            query=str(video["id"]),
            page_size=10,
        )
        self.assertEqual(history["total"], 2)
        self.assertEqual(
            {item["direction"] for item in history["items"]},
            {"to_external", "to_local"},
        )

    def test_external_volume_root_does_not_change_between_targets(self):
        target_a = self.external_root / "video" / "d3"
        target_b = self.external_root / "video" / "d1"
        target_a.mkdir(parents=True)
        target_b.mkdir(parents=True)

        with patch.object(
            video_storage,
            "_selected_volume_root",
            return_value=self.external_root,
        ):
            first = video_storage.register_external_target(str(target_a))
            second = video_storage.register_external_target(str(target_b))

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(
            second["mount_path"],
            str(self.external_root.resolve()),
        )
        self.assertEqual(second["root_relative_path"], "")
        self.assertEqual(second["selected_path"], str(target_b.resolve()))

    def test_startup_repair_restores_legacy_video_saved_under_old_target(self):
        video, _, _ = self._create_video()
        target_d3 = self.external_root / "video" / "d3"
        target_d3.mkdir(parents=True)
        with patch.object(
            video_storage,
            "_selected_volume_root",
            return_value=self.external_root,
        ):
            migration_tasks.create_batch(
                mode="manual",
                direction="to_external",
                destination_root=str(target_d3),
                video_ids=[video["id"]],
            )
            self._run_next()

        moved = database.get_video_admin(video["id"])
        actual_path = Path(moved["path"])
        self.assertTrue(actual_path.is_file())
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_storage_volumes
                SET root_relative_path = 'video/d1'
                WHERE id = ?
                """,
                (moved["storage_volume_id"],),
            )
            connection.execute(
                """
                UPDATE videos
                SET path = ?,
                    storage_relative_path = 'download-hub/series/episode.mp4'
                WHERE id = ?
                """,
                (
                    str(
                        self.external_root
                        / "video"
                        / "d1"
                        / "download-hub"
                        / "series"
                        / "episode.mp4"
                    ),
                    video["id"],
                ),
            )
            connection.commit()

        database.init_db()

        repaired = database.get_video_admin(video["id"])
        volume = database.get_storage_volume(
            repaired["storage_volume_id"]
        )
        self.assertEqual(volume["root_relative_path"], "")
        self.assertEqual(Path(repaired["path"]), actual_path)
        self.assertEqual(
            repaired["storage_relative_path"],
            str(actual_path.relative_to(self.external_root.resolve())),
        )
        self.assertEqual(
            video_storage.resolve_video_storage(repaired)["state"],
            "external_online",
        )

    def test_migration_table_has_no_foreign_keys(self):
        with database.connect() as connection:
            keys = connection.execute(
                "PRAGMA foreign_key_list('video_migrations')"
            ).fetchall()
        self.assertEqual(keys, [])

    def test_auto_migration_only_chooses_old_local_videos(self):
        old_video, _, _ = self._create_video()
        newer = self.local_root / "series" / "newer.mp4"
        newer.write_bytes(b"newer-video")
        database.upsert_video(
            path=newer,
            title="Newer",
            directory=self.local_root.name,
            size=newer.stat().st_size,
            mtime_ns=newer.stat().st_mtime_ns,
        )
        newer_video = database.get_video_by_path(newer)
        for video in (old_video, newer_video):
            database.update_metadata(
                video["id"],
                {"duration": 60},
                thumbnail_path=None,
            )
            database.save_preview_result(video["id"], "ready")
        with database.connect() as connection:
            connection.execute(
                "UPDATE videos SET created_at = '2020-01-01T00:00:00+08:00' "
                "WHERE id = ?",
                (old_video["id"],),
            )

        result = migration_tasks.create_batch(
            mode="auto",
            direction="to_external",
            destination_root=str(self.external_root),
            cutoff_days=30,
        )
        self.assertEqual(result["created"], 1)
        task = migration_tasks.claim_migration()
        self.assertEqual(task["video_id"], old_video["id"])

    def test_external_offline_is_not_reported_as_deleted(self):
        video, _, _ = self._create_video()
        migration_tasks.create_batch(
            mode="manual",
            direction="to_external",
            destination_root=str(self.external_root),
            video_ids=[video["id"]],
        )
        self._run_next()
        moved = database.get_video_admin(video["id"])
        volume = database.get_storage_volume(moved["storage_volume_id"])
        marker = Path(volume["mount_path"]) / video_storage.STORAGE_MARKER
        marker.unlink()

        resolution = video_storage.resolve_video_storage(moved)
        self.assertEqual(resolution["state"], "external_offline")
        self.assertIsNotNone(database.get_video_admin(video["id"]))
        state = get_playback_state(video["id"])
        self.assertEqual(state["state"], "external_offline")
        self.assertFalse(state["playable"])
        self.assertEqual(state["storage_label"], "external-library")

        client = admin_client()
        watch = client.get(f"/watch/{video['id']}")
        playback = client.get(f"/media/{video['id']}")
        api_state = client.get(f"/api/playback-state/{video['id']}")

        self.assertEqual(watch.status_code, 200)
        self.assertIn("外置硬盘", watch.text)
        self.assertIn("未连接", watch.text)
        self.assertIn("重新检测", watch.text)
        self.assertIn('data-initial-state="external_offline"', watch.text)
        self.assertNotIn(f'src="/media/{video["id"]}"', watch.text)
        self.assertNotIn("当前浏览器无法直接播放此视频编码", watch.text)
        self.assertEqual(playback.status_code, 409)
        self.assertIn("外置硬盘", playback.json()["detail"])
        self.assertIn("未连接", playback.json()["detail"])
        self.assertEqual(api_state.status_code, 200)
        self.assertEqual(api_state.json()["state"], "external_offline")

        marker.write_text(
            json.dumps(
                {
                    "storage_key": volume["storage_key"],
                    "version": 1,
                }
            ),
            encoding="utf-8",
        )
        restored_state = get_playback_state(video["id"])
        self.assertTrue(restored_state["playable"] or restored_state["state"] == "cache_required")

    def test_management_pages_and_detail_api_include_storage_fields(self):
        video, _, _ = self._create_video()
        client = admin_client()

        home = client.get("/")
        page = client.get("/manage/videos")
        detail = client.get(f"/api/manage/videos/{video['id']}")
        migrations = client.get("/manage/migrations")
        imports_tab = client.get("/manage/migrations?tab=imports")
        new_migration = client.get(
            "/manage/migrations/new?mode=manual&direction=to_external"
        )

        self.assertEqual(home.status_code, 200)
        self.assertIn("视频管理", home.text)
        self.assertEqual(page.status_code, 200)
        self.assertIn("完整路径", page.text)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["video"]["id"], video["id"])
        self.assertIn("storage_state", detail.json()["video"])
        self.assertEqual(migrations.status_code, 200)
        self.assertIn("迁回本地", migrations.text)
        self.assertIn('role="tablist"', migrations.text)
        self.assertIn("文件迁移", migrations.text)
        self.assertIn("外部视频导入", migrations.text)
        self.assertIn(
            'class="download-primary-button"',
            migrations.text,
        )
        self.assertIn(
            'class="download-primary-button" href="/manage/migrations/new?mode=manual&direction=to_external"',
            migrations.text,
        )
        self.assertNotIn('id="panel-imports"', migrations.text)
        self.assertEqual(imports_tab.status_code, 200)
        self.assertIn(
            'class="download-primary-button" href="/manage/migrations/import"',
            imports_tab.text,
        )
        self.assertIn('id="panel-imports"', imports_tab.text)
        self.assertNotIn('id="panel-migrations"', imports_tab.text)
        self.assertEqual(new_migration.status_code, 200)
        self.assertIn("选择文件夹", new_migration.text)
        self.assertIn('id="select-page-videos"', new_migration.text)
        self.assertIn("全选", new_migration.text)
        self.assertIn('id="migration-recent-targets"', new_migration.text)
        self.assertIn('id="recent-target-select"', new_migration.text)
        self.assertIn('id="clear-recent-targets"', new_migration.text)
        self.assertIn(
            "management-form-section migration-submit-form",
            new_migration.text,
        )
        self.assertIn(
            'class="download-secondary-button"',
            new_migration.text,
        )

    def test_video_management_page_does_not_probe_storage_synchronously(self):
        self._create_video()
        client = admin_client()

        with patch(
            "local_video_site.web.routes.migrations.refresh_storage_volumes",
            side_effect=AssertionError("page must not probe storage"),
        ):
            response = client.get("/manage/videos")

        self.assertEqual(response.status_code, 200)
        self.assertIn("刷新外盘状态", response.text)

    def test_manual_candidates_use_cached_storage_without_sync_probe(self):
        video, _, _ = self._create_video()

        with patch.object(
            video_storage,
            "refresh_storage_volumes",
            side_effect=AssertionError("candidate page must not probe storage"),
        ):
            candidates = migration_tasks.list_candidates(
                "to_external",
                page=1,
                page_size=40,
            )

        self.assertEqual(candidates["total"], 1)
        self.assertEqual(candidates["items"][0]["id"], video["id"])
        self.assertEqual(
            candidates["items"][0]["resolved_path"],
            str(video["path"]),
        )

    def test_migration_batch_api_requires_password_and_creates_pending_rows(self):
        video, _, _ = self._create_video()
        client = admin_client()

        wrong = client.post(
            "/api/manage/migrations/batches",
            json={
                "mode": "manual",
                "direction": "to_external",
                "destination_root": str(self.external_root),
                "video_ids": [video["id"]],
                "password": "wrong",
            },
        )
        self.assertEqual(wrong.status_code, 401)

        response = client.post(
            "/api/manage/migrations/batches",
            json={
                "mode": "manual",
                "direction": "to_external",
                "destination_root": str(self.external_root),
                "video_ids": [video["id"]],
                "verification_mode": "sha256",
                "password": TEST_DELETE_SECRET,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 1)
        task = migration_tasks.claim_migration()
        self.assertEqual(task["video_id"], video["id"])

    def test_deleting_external_video_removes_local_derivatives_and_keeps_history(self):
        video, thumbnail, preview = self._create_video()
        migration_tasks.create_batch(
            mode="manual",
            direction="to_external",
            destination_root=str(self.external_root),
            video_ids=[video["id"]],
        )
        task = self._run_next()
        moved = database.get_video_admin(video["id"])
        external_file = Path(moved["path"])

        result = delete_service.delete_video_authorized(video["id"])

        self.assertEqual(result["deleted_files"], 3)
        self.assertFalse(external_file.exists())
        self.assertFalse(thumbnail.exists())
        self.assertFalse(preview.exists())
        self.assertIsNone(database.get_video(video["id"]))
        self.assertEqual(
            database.get_video_admin(video["id"])["is_deleted"],
            1,
        )
        with database.connect() as connection:
            row = connection.execute(
                "SELECT video_id FROM video_migrations WHERE id = ?",
                (task["id"],),
            ).fetchone()
        self.assertEqual(row["video_id"], video["id"])


if __name__ == "__main__":
    unittest.main()
