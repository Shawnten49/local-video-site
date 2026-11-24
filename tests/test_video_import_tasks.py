import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site.storage import deletion as delete_service
from local_video_site.storage.deletion import set_delete_password
from local_video_site.media.editing import rename as rename_service
from local_video_site.ingestion import import_tasks as video_import_tasks
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.media.playback.state import get_playback_state


TEST_DELETE_SECRET = "test-delete-secret"


class ExternalVideoImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.local_root = self.root / "download-hub"
        self.local_root.mkdir()
        self.external = self.root / "external"
        self.external.mkdir()
        self.destination = self.root / "download-import"
        self.destination.mkdir()
        self.patches = (
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(config, "DEFAULT_ROOTS", [self.local_root]),
            patch.object(config, "minimum_video_bytes", return_value=0),
            patch.object(config, "migration_min_free_bytes", return_value=0),
            patch.object(
                config,
                "DELETE_PASSWORD_PATH",
                self.data_dir / ".delete-password",
            ),
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

    def _source_file(self, name="external.mp4", size=2048):
        path = self.external / name
        path.write_bytes(b"x" * size)
        return path

    def _run(self, source, delete_source=False, access_level=0):
        result = video_import_tasks.create_import_batch(
            source_path=str(source),
            destination_root=str(self.destination),
            delete_source=delete_source,
            access_level=access_level,
        )
        job = video_import_tasks.claim_import_job()
        self.assertIsNotNone(job)
        thumbnail = config.THUMBNAIL_DIR / "import-thumb.jpg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(b"thumb")
        with (
            patch.object(
                video_import_tasks,
                "probe_video",
                return_value={
                    "duration": 10.0,
                    "width": 1280,
                    "height": 720,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
            ),
            patch.object(
                video_import_tasks,
                "generate_thumbnail",
                return_value=thumbnail,
            ),
        ):
            video_import_tasks.run_import_job(job)
        return result, video_import_tasks.get_import_job(job["id"])

    def test_single_file_is_copied_and_registered_without_categories(self):
        source = self._source_file()

        result, job = self._run(source)
        video = database.get_video(job["video_id"])

        self.assertEqual(result["created"], 1)
        self.assertEqual(job["status"], "success")
        self.assertTrue(source.is_file())
        self.assertEqual(Path(video["path"]), (self.destination / source.name).resolve())
        self.assertIsNone(video["original_url"])
        self.assertEqual(video["preview_status"], "pending")
        self.assertIsNotNone(video["thumbnail_path"])
        with database.connect() as connection:
            category_count = connection.execute(
                "SELECT COUNT(*) FROM video_categories WHERE video_id = ?",
                (video["id"],),
            ).fetchone()[0]
            actor_count = connection.execute(
                "SELECT COUNT(*) FROM video_actors WHERE video_id = ?",
                (video["id"],),
            ).fetchone()[0]
        self.assertEqual((category_count, actor_count), (0, 0))
        self.assertEqual(job["preview_status"], "queued")

    def test_imported_video_receives_selected_access_level(self):
        source = self._source_file("restricted.mp4")

        _, job = self._run(source, access_level=2)
        video = database.get_video(job["video_id"])

        self.assertEqual(job["status"], "success")
        self.assertEqual(job["access_level"], 2)
        self.assertEqual(video["access_level"], 2)

    def test_import_jobs_support_query_status_and_access_filters(self):
        first_source = self._source_file("filter-one.mp4")
        second_source = self._source_file("filter-two.mp4")
        _, first_job = self._run(first_source, access_level=1)
        _, second_job = self._run(second_source, access_level=2)

        by_id = video_import_tasks.list_import_jobs(
            query=str(first_job["id"])
        )
        by_access = video_import_tasks.list_import_jobs(
            access_level=2
        )
        by_status = video_import_tasks.list_import_jobs(
            status="success"
        )
        by_text = video_import_tasks.list_import_jobs(
            query="filter-one"
        )

        self.assertEqual(
            [item["id"] for item in by_id["items"]],
            [first_job["id"]],
        )
        self.assertEqual(
            [item["id"] for item in by_access["items"]],
            [second_job["id"]],
        )
        self.assertEqual(by_status["total"], 2)
        self.assertEqual(
            [item["id"] for item in by_text["items"]],
            [first_job["id"]],
        )

    def test_directory_import_preserves_relative_structure(self):
        source = self.external / "new-library"
        (source / "nested").mkdir(parents=True)
        (source / "one.mp4").write_bytes(b"one")
        (source / "nested" / "two.mkv").write_bytes(b"two")
        (source / "legacy.RMVB").write_bytes(b"real")
        (source / "ignore.txt").write_text("ignore", encoding="utf-8")

        result = video_import_tasks.create_import_batch(
            source_path=str(source),
            destination_root=str(self.destination),
            delete_source=False,
        )
        jobs = video_import_tasks.list_import_jobs(page_size=10)
        destinations = {
            Path(item["destination_path"]).relative_to(
                self.destination.resolve()
            )
            for item in jobs["items"]
        }

        self.assertEqual(result["created"], 3)
        self.assertEqual(
            destinations,
            {
                Path("new-library/one.mp4"),
                Path("new-library/nested/two.mkv"),
                Path("new-library/legacy.RMVB"),
            },
        )

    def test_existing_destination_name_fails_without_deleting_source(self):
        source = self._source_file("conflict.mp4")
        (self.destination / "conflict.mp4").write_bytes(b"existing")

        _, job = self._run(source)

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_code"], "destination_conflict")
        self.assertTrue(source.is_file())
        self.assertEqual(
            (self.destination / "conflict.mp4").read_bytes(),
            b"existing",
        )

    def test_delete_source_happens_after_successful_registration(self):
        source = self._source_file("delete-source.mp4")

        _, job = self._run(source, delete_source=True)

        self.assertEqual(job["status"], "success")
        self.assertFalse(source.exists())
        self.assertIsNotNone(database.get_video(job["video_id"]))

    def test_imported_rmvb_reports_unsupported_without_transcoding(self):
        source = self._source_file("legacy.rmvb")

        _, job = self._run(source)
        state = get_playback_state(job["video_id"])

        self.assertEqual(job["status"], "success")
        self.assertEqual(state["state"], "unsupported_codec")
        self.assertFalse(state["playable"])
        self.assertIn("不会自动转码", state["message"])

    def test_imported_video_can_be_deleted_from_import_directory(self):
        source = self._source_file("deletable-import.mp4")
        _, job = self._run(source)
        imported_path = Path(database.get_video(job["video_id"])["path"])

        result = delete_service.delete_video_authorized(job["video_id"])

        self.assertEqual(result["deleted_files"], 2)
        self.assertFalse(imported_path.exists())
        self.assertEqual(
            database.get_video_admin(job["video_id"])["is_deleted"],
            1,
        )

    def test_renamed_imported_video_remains_deletable(self):
        source = self._source_file("rename-import.mp4")
        _, job = self._run(source)
        video = database.get_video(job["video_id"])

        rename_service.rename_video(
            job["video_id"],
            "Renamed Import",
            expected_path=video["path"],
        )
        renamed = database.get_video(job["video_id"])
        imported = video_import_tasks.get_import_job(job["id"])
        result = delete_service.delete_video_authorized(job["video_id"])

        self.assertEqual(imported["destination_path"], renamed["path"])
        self.assertEqual(result["deleted_files"], 2)
        self.assertFalse(Path(renamed["path"]).exists())

    def test_import_page_and_preview_api_are_available(self):
        source = self._source_file("preview.mp4")
        client = admin_client()

        page = client.get("/manage/migrations/import")
        preview = client.get(
            "/api/manage/import/preview",
            params={
                "source_path": str(source),
                "destination_root": str(self.destination),
            },
        )
        browse = client.get(
            "/api/manage/import/browse",
            params={"path": str(self.external)},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn("导入外部视频", page.text)
        self.assertIn("删除外盘原始文件", page.text)
        self.assertIn('id="external-import-access-level"', page.text)
        self.assertIn("2 · 管理员", page.text)
        self.assertIn(
            'id="external-import-password-fields" hidden',
            page.text,
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["file_count"], 1)
        self.assertEqual(browse.status_code, 200)
        self.assertEqual(browse.json()["files"][0]["name"], "preview.mp4")

    def test_import_without_deleting_source_does_not_require_password(self):
        source = self._source_file("keep-source.mp4")
        response = admin_client().post(
            "/api/manage/import/batches",
            json={
                "source_path": str(source),
                "destination_root": str(self.destination),
                "access_level": 1,
                "delete_source": False,
                "verification_mode": "sha256",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 1)
        job = video_import_tasks.list_import_jobs()["items"][0]
        self.assertEqual(job["access_level"], 1)

    def test_deleting_source_still_requires_password(self):
        source = self._source_file("delete-requires-password.mp4")
        response = admin_client().post(
            "/api/manage/import/batches",
            json={
                "source_path": str(source),
                "destination_root": str(self.destination),
                "delete_source": True,
                "verification_mode": "sha256",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue(source.is_file())


if __name__ == "__main__":
    unittest.main()
