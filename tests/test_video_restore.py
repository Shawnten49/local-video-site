import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.episodes.series_management import list_season_videos
from tests.auth_test_utils import admin_client, user_client


class VideoRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.local_root = self.root / "videos"
        self.external_root = self.root / "Volumes"
        self.local_root.mkdir()
        self.external_root.mkdir()
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
            patch.object(
                config,
                "COVER_DIR",
                self.data_dir / "covers",
            ),
            patch.object(
                config,
                "PREVIEW_DIR",
                self.data_dir / "previews",
            ),
            patch.object(config, "DEFAULT_ROOTS", [self.local_root]),
            patch.object(
                config,
                "external_volume_roots",
                return_value=[self.external_root],
            ),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.series = database.upsert_video_season(
            series_name="Restore Series",
            season_name="第一季",
            season_number=1,
            provider="test",
            source_url="https://example.test/restore",
            relative_directory="Restore Series/第一季",
        )

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _deleted_video(self):
        old_path = self.local_root / "Restore Series" / "001.mp4"
        old_path.parent.mkdir(parents=True)
        old_path.write_bytes(b"old-video")
        database.upsert_video(
            old_path,
            "Restore Episode",
            "videos",
            old_path.stat().st_size,
            old_path.stat().st_mtime_ns,
            original_url="https://example.test/episode/1",
        )
        video = database.get_video_by_path(old_path)
        episode_id = database.create_video_episode(
            season_id=int(self.series["season"]["id"]),
            episode_number=1,
            display_title="Restore Episode",
            source_url="https://example.test/episode/1",
            normalized_source_url="https://example.test/episode/1",
            relative_path="Restore Series/第一季/001.mp4",
            absolute_path=str(old_path),
            download_task_id=None,
            episode_title="第一集",
        )
        database.attach_episode_video(episode_id, int(video["id"]), old_path)
        database.set_video_access_level(int(video["id"]), 1)
        self.assertTrue(database.delete_video_record(int(video["id"])))
        return int(video["id"]), episode_id, old_path

    def test_season_page_and_default_listing_include_deleted_records(self):
        video_id, _, _ = self._deleted_video()
        candidate = self.local_root / "restore-choice.mp4"
        candidate.write_bytes(b"candidate")
        admin = admin_client()

        page = admin.get(
            "/manage/series/{}/seasons/{}".format(
                self.series["series"]["id"],
                self.series["season"]["id"],
            )
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("已删除", page.text)
        self.assertIn("重新上线", page.text)
        self.assertIn("data-restore-video=\"{}\"".format(video_id), page.text)
        browse = admin.get(
            "/api/manage/videos/restore/browse",
            params={"scope": "local", "path": str(self.local_root)},
        )
        self.assertEqual(browse.status_code, 200, browse.text)
        self.assertIn("restore-choice.mp4", browse.text)

        data = list_season_videos(
            int(self.series["series"]["id"]),
            int(self.series["season"]["id"]),
        )
        self.assertEqual(data["record_state"], "all")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["is_deleted"], 1)
        self.assertIsNotNone(data["items"][0]["deleted_at"])

    def test_deleted_video_can_be_restored_from_local_file(self):
        video_id, episode_id, old_path = self._deleted_video()
        old_path.unlink()
        restored_path = self.local_root / "Replacement" / "new.mp4"
        restored_path.parent.mkdir()
        restored_path.write_bytes(b"replacement-video")
        admin = admin_client()
        preview = admin.post(
            "/api/manage/videos/{}/restore/preview".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(restored_path),
                "storage_scope": "local",
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        payload = preview.json()
        self.assertIsInstance(payload["mtime_ns"], str)
        self.assertRegex(payload["source_fingerprint"], r"^[0-9a-f]{64}$")

        response = admin.post(
            "/api/manage/videos/{}/restore-online".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(restored_path),
                "storage_scope": "local",
                "source_fingerprint": payload["source_fingerprint"],
                "expected_updated_at": payload["updated_at"]
                if "updated_at" in payload
                else database.get_video_admin(video_id)["updated_at"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        video = database.get_video_admin(video_id)
        self.assertEqual(video["id"], video_id)
        self.assertEqual(video["is_deleted"], 0)
        self.assertIsNone(video["deleted_at"])
        self.assertEqual(video["path"], str(restored_path.resolve()))
        self.assertEqual(video["access_level"], 1)
        with database.connect() as connection:
            episode = connection.execute(
                "SELECT status, video_id FROM video_episodes WHERE id = ?",
                (episode_id,),
            ).fetchone()
            media = connection.execute(
                """
                SELECT metadata_status FROM video_media_info
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()
            assets = connection.execute(
                """
                SELECT thumbnail_path, preview_path, preview_status
                FROM video_assets WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()
            audit = connection.execute(
                """
                SELECT action FROM auth_audit_log
                WHERE action = 'video.restored_online'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual(episode["status"], "available")
        self.assertEqual(episode["video_id"], video_id)
        self.assertEqual(media["metadata_status"], "pending")
        self.assertIsNone(assets["thumbnail_path"])
        self.assertIsNone(assets["preview_path"])
        self.assertEqual(assets["preview_status"], "pending")
        self.assertIsNotNone(audit)
        self.assertTrue(restored_path.is_file())

    def test_restore_rejects_path_bound_to_another_video_and_non_admin(self):
        video_id, episode_id, _ = self._deleted_video()
        occupied = self.local_root / "occupied.mp4"
        occupied.write_bytes(b"occupied")
        database.upsert_video(
            occupied,
            "Occupied",
            "videos",
            occupied.stat().st_size,
            occupied.stat().st_mtime_ns,
        )
        admin = admin_client()
        response = admin.post(
            "/api/manage/videos/{}/restore/preview".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(occupied),
                "storage_scope": "local",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "source_already_bound",
        )

        user = user_client(username="restore-user")
        forbidden = user.post(
            "/api/manage/videos/{}/restore/preview".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(occupied),
                "storage_scope": "local",
            },
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_deleted_video_can_be_restored_from_external_storage(self):
        video_id, episode_id, _ = self._deleted_video()
        disk_root = self.external_root / "D01"
        external_path = disk_root / "anime" / "episode.mp4"
        external_path.parent.mkdir(parents=True)
        external_path.write_bytes(b"external-video")
        with database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO video_storage_volumes (
                    storage_key, label, mount_path, created_at, updated_at
                ) VALUES ('restore-volume', 'D01', ?, 'now', 'now')
                """,
                (str(disk_root),),
            )
            volume_id = int(cursor.lastrowid)
            connection.commit()
        volume = database.get_storage_volume(volume_id)
        registered = {
            "volume": volume,
            "source_relative_path": "anime/episode.mp4",
        }
        admin = admin_client()
        with patch(
            "local_video_site.episodes.restore.register_external_source",
            return_value=registered,
        ):
            preview = admin.post(
                "/api/manage/videos/{}/restore/preview".format(video_id),
                json={
                    "episode_id": episode_id,
                    "source_path": str(external_path),
                    "storage_scope": "external",
                },
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            response = admin.post(
                "/api/manage/videos/{}/restore-online".format(video_id),
                json={
                    "episode_id": episode_id,
                    "source_path": str(external_path),
                    "storage_scope": "external",
                    "source_fingerprint": preview.json()[
                        "source_fingerprint"
                    ],
                    "expected_updated_at": preview.json()["updated_at"],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["storage_kind"], "external")
        video = database.get_video_admin(video_id)
        self.assertEqual(video["storage_volume_id"], volume_id)
        self.assertEqual(video["storage_relative_path"], "anime/episode.mp4")
        self.assertEqual(video["is_deleted"], 0)

    def test_preview_fingerprint_rejects_source_modified_after_preview(self):
        video_id, episode_id, _ = self._deleted_video()
        restored_path = self.local_root / "restore" / "episode.mp4"
        restored_path.parent.mkdir()
        restored_path.write_bytes(b"before")
        admin = admin_client()
        preview = admin.post(
            "/api/manage/videos/{}/restore/preview".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(restored_path),
                "storage_scope": "local",
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        restored_path.write_bytes(b"after-the-file-was-changed")

        response = admin.post(
            "/api/manage/videos/{}/restore-online".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(restored_path),
                "storage_scope": "local",
                "source_fingerprint": preview.json()[
                    "source_fingerprint"
                ],
                "expected_updated_at": preview.json()["updated_at"],
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "source_changed",
        )
        self.assertEqual(database.get_video_admin(video_id)["is_deleted"], 1)

    def test_legacy_mtime_string_remains_supported(self):
        video_id, episode_id, _ = self._deleted_video()
        restored_path = self.local_root / "legacy" / "episode.mp4"
        restored_path.parent.mkdir()
        restored_path.write_bytes(b"legacy-client")
        admin = admin_client()
        preview = admin.post(
            "/api/manage/videos/{}/restore/preview".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(restored_path),
                "storage_scope": "local",
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        response = admin.post(
            "/api/manage/videos/{}/restore-online".format(video_id),
            json={
                "episode_id": episode_id,
                "source_path": str(restored_path),
                "storage_scope": "local",
                "expected_mtime_ns": preview.json()["mtime_ns"],
                "expected_updated_at": preview.json()["updated_at"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
