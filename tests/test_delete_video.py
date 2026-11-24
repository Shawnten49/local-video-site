import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.web.delete_rate import DELETE_ATTEMPTS
from local_video_site.storage.deletion import delete_video, set_delete_password, verify_delete_password
from local_video_site.media.playback.engine import playback_cache_path
from local_video_site.storage import deletion_tasks as video_delete_tasks


TEST_DELETE_SECRET = "test-delete-secret"


class DeleteVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.thumbnail_dir = self.data_dir / "thumbnails"
        self.cover_dir = self.data_dir / "covers"
        self.quarantine_dir = self.data_dir / "delete-quarantine"
        self.password_path = self.data_dir / ".delete-password"
        self.video = self.video_root / "delete-me.mp4"
        self.video.write_bytes(b"video-data")
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.thumbnail_dir),
            patch.object(
                config,
                "PREVIEW_DIR",
                self.data_dir / "previews",
            ),
            patch.object(config, "COVER_DIR", self.cover_dir),
            patch.object(config, "DELETE_PASSWORD_PATH", self.password_path),
            patch.object(
                config,
                "DELETE_QUARANTINE_DIR",
                self.quarantine_dir,
            ),
            patch.object(config, "LOG_PATH", self.data_dir / "logs" / "server.log"),
            patch.object(config, "SCAN_STATUS_PATH", self.data_dir / "scan.json"),
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
        )
        for item in self.patches:
            item.start()
        DELETE_ATTEMPTS.clear()
        database.init_db()
        set_delete_password(TEST_DELETE_SECRET)
        database.upsert_video(
            path=self.video,
            title="Delete Me",
            directory="videos",
            size=self.video.stat().st_size,
            mtime_ns=self.video.stat().st_mtime_ns,
        )
        self.video_id = database.list_videos()["items"][0]["id"]
        database.update_video_technical_info(
            self.video_id,
            {
                "duration": 60,
                "video_codec": "av1",
            },
        )
        self.cover = self.cover_dir / "custom-cover.jpg"
        self.cover.parent.mkdir(parents=True, exist_ok=True)
        self.cover.write_bytes(b"cover")
        database.update_custom_cover(self.video_id, str(self.cover), 10)
        video = database.get_video(self.video_id)
        self.playback = playback_cache_path(video)
        self.playback.parent.mkdir(parents=True, exist_ok=True)
        self.playback.write_bytes(b"cache")
        database.register_playback_cache(
            self.playback,
            video,
            self.playback.stat().st_size,
        )
        config.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        self.preview = config.PREVIEW_DIR / "delete-preview.mp4"
        self.preview.write_bytes(b"preview")
        database.save_preview_result(
            video_id=self.video_id,
            status="ready",
            preview_path=self.preview.name,
            start=5,
            duration=8,
            source_size=video["size"],
            source_mtime_ns=video["mtime_ns"],
        )
        database.add_favorite(self.video_id)
        with database.connect() as connection:
            category_id = connection.execute(
                """
                INSERT INTO categories (
                    name, normalized_name, created_at, updated_at
                ) VALUES ('Test', 'test', 'now', 'now')
                """
            ).lastrowid
            connection.execute(
                "INSERT INTO video_categories (video_id, category_id) VALUES (?, ?)",
                (self.video_id, category_id),
            )
            actor_id = connection.execute(
                """
                INSERT INTO actors (
                    name, normalized_name, created_at, updated_at
                ) VALUES ('Tester', 'tester', 'now', 'now')
                """
            ).lastrowid
            connection.execute(
                "INSERT INTO video_actors (video_id, actor_id, created_at) VALUES (?, ?, 'now')",
                (self.video_id, actor_id),
            )
        database.save_category_fetch_cache(
            self.video_id,
            "https://xhamster.com/videos/delete-me",
            "failed",
            error="test",
        )

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_configured_password_is_hashed_and_wrong_password_preserves_video(self):
        self.assertTrue(verify_delete_password(TEST_DELETE_SECRET))
        self.assertFalse(verify_delete_password("wrong"))
        self.assertEqual(self.password_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(
            TEST_DELETE_SECRET,
            self.password_path.read_text(encoding="utf-8"),
        )
        with self.assertRaises(PermissionError):
            delete_video(self.video_id, "wrong")
        self.assertTrue(self.video.is_file())
        self.assertIsNotNone(database.get_video(self.video_id))

    def test_correct_password_soft_deletes_record_and_preserves_relations(self):
        result = delete_video(self.video_id, TEST_DELETE_SECRET)

        self.assertEqual(result["deleted_files"], 4)
        self.assertFalse(self.video.exists())
        self.assertFalse(self.cover.exists())
        self.assertFalse(self.playback.exists())
        self.assertFalse(self.preview.exists())
        self.assertIsNone(database.get_video(self.video_id))
        deleted = database.get_video_admin(self.video_id)
        self.assertIsNotNone(deleted)
        self.assertEqual(deleted["is_deleted"], 1)
        self.assertTrue(database.is_favorite(self.video_id))
        self.assertEqual(database.favorite_count(), 0)
        self.assertEqual(database.list_playback_cache_entries(), [])
        self.assertIn(
            self.video_id,
            database.category_fetch_cache_map(),
        )
        with database.connect() as connection:
            for table in (
                "video_categories",
                "video_actors",
                "favorites",
                "category_fetch_cache",
            ):
                count = connection.execute(
                    "SELECT COUNT(*) FROM {} WHERE video_id = ?".format(table),
                    (self.video_id,),
                ).fetchone()[0]
                self.assertEqual(count, 1, table)

        repeated = delete_video(self.video_id, TEST_DELETE_SECRET)
        self.assertTrue(repeated["already_deleted"])
        self.assertEqual(repeated["deleted_files"], 0)

    def test_delete_api_requires_password_and_queues_task(self):
        client = admin_client()
        wrong = client.request(
            "DELETE",
            "/api/videos/{}".format(self.video_id),
            json={
                "password": "wrong",
                "return_to": "/?sort=size&page=2",
            },
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertTrue(self.video.is_file())

        deleted = client.request(
            "DELETE",
            "/api/videos/{}".format(self.video_id),
            json={
                "password": TEST_DELETE_SECRET,
                "return_to": "/?sort=size&page=2",
            },
        )
        self.assertEqual(deleted.status_code, 202)
        self.assertEqual(deleted.json()["status"], "queued")
        self.assertEqual(
            deleted.json()["redirect_url"],
            "/manage/delete-tasks?q={}".format(
                deleted.json()["batch_id"]
            ),
        )
        self.assertTrue(self.video.is_file())

        job = video_delete_tasks.claim_delete_job()
        video_delete_tasks.process_delete_job(job)
        self.assertFalse(self.video.exists())
        self.assertIsNone(database.get_video(self.video_id))

    def test_soft_deleted_video_is_hidden_but_visible_in_admin_filter(self):
        client = admin_client()
        delete_video(self.video_id, TEST_DELETE_SECRET)

        self.assertEqual(database.list_videos()["total"], 0)
        self.assertEqual(database.library_stats()["total"], 0)
        active = client.get("/manage/videos?record_state=active")
        deleted = client.get("/manage/videos?record_state=deleted")
        detail = client.get(f"/api/manage/videos/{self.video_id}")
        media = client.get(f"/media/{self.video_id}")
        watch = client.get(f"/watch/{self.video_id}")
        playback_state = client.get(
            f"/api/playback-state/{self.video_id}"
        )

        self.assertNotIn("Delete Me", active.text)
        self.assertIn("Delete Me", deleted.text)
        self.assertIn("已删除", deleted.text)
        self.assertEqual(detail.json()["video"]["is_deleted"], 1)
        self.assertEqual(media.status_code, 410)
        self.assertEqual(watch.status_code, 404)
        self.assertEqual(playback_state.status_code, 404)

    def test_reappearing_file_restores_existing_soft_deleted_record(self):
        database.delete_video_record(self.video_id)
        self.assertIsNone(database.get_video(self.video_id))

        self.video.write_bytes(b"video-data")
        status = database.upsert_video(
            path=self.video,
            title="Delete Me",
            directory="videos",
            size=self.video.stat().st_size,
            mtime_ns=self.video.stat().st_mtime_ns,
        )
        restored = database.get_video(self.video_id)

        self.assertEqual(status, "changed")
        self.assertIsNotNone(restored)
        self.assertEqual(restored["is_deleted"], 0)

    def test_video_management_defaults_to_all_records(self):
        client = admin_client()
        delete_video(self.video_id, TEST_DELETE_SECRET)

        default_page = client.get("/manage/videos")
        active_page = client.get("/manage/videos?record_state=active")
        all_page = client.get("/manage/videos?record_state=all")
        invalid_page = client.get("/manage/videos?record_state=unknown")

        self.assertIn(
            '<option value="all" selected>全部记录</option>',
            default_page.text,
        )
        self.assertIn(
            '<option value="active" selected>有效记录</option>',
            active_page.text,
        )
        self.assertIn(
            '<option value="all" selected>全部记录</option>',
            all_page.text,
        )
        self.assertIn(
            '<option value="all" selected>全部记录</option>',
            invalid_page.text,
        )
        self.assertEqual(
            default_page.headers["cache-control"],
            "no-store",
        )

    def test_changed_password_replaces_default(self):
        set_delete_password("new-secret")

        self.assertFalse(verify_delete_password(TEST_DELETE_SECRET))
        self.assertTrue(verify_delete_password("new-secret"))

    def test_play_page_contains_two_step_delete_controls(self):
        response = admin_client().get("/watch/{}".format(self.video_id))

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="delete-video-button"', response.text)
        self.assertIn("删除这个视频", response.text)
        self.assertIn('id="delete-password"', response.text)
        self.assertIn("/static/delete-video.js", response.text)

    def test_video_outside_configured_roots_is_not_deleted(self):
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"outside")
        database.upsert_video(
            path=outside,
            title="Outside",
            directory="outside",
            size=7,
            mtime_ns=1,
        )
        with database.connect() as connection:
            outside_id = connection.execute(
                "SELECT id FROM videos WHERE path = ?",
                (str(outside),),
            ).fetchone()["id"]

        with self.assertRaises(PermissionError):
            delete_video(outside_id, TEST_DELETE_SECRET)
        self.assertTrue(outside.is_file())
        self.assertIsNotNone(database.get_video(outside_id))


if __name__ == "__main__":
    unittest.main()
