import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.media.editing.rename import VideoRenameError, normalize_video_title, rename_video


class RenameVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
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

    def _create_video(self, name="Old Name.mp4"):
        path = self.video_root / name
        path.write_bytes(b"video-content")
        database.upsert_video(
            path=path,
            title=path.stem,
            directory=self.video_root.name,
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        return database.get_video_by_path(path)

    def test_rename_updates_file_video_and_download_task(self):
        video = self._create_video()
        task_id = database.create_download_task(
            batch_id="rename-test",
            url="https://example.com/video",
            normalized_url="https://example.com/video",
            metadata_url="https://example.com/video",
            normalized_metadata_url="https://example.com/video",
            output_dir=str(self.video_root),
            max_attempts=3,
        )
        database.update_download_task(
            task_id,
            video_id=video["id"],
            file_path=video["path"],
            file_name=Path(video["path"]).name,
        )

        result = rename_video(
            video["id"],
            "New Name",
            expected_path=video["path"],
        )
        updated = database.get_video(video["id"])
        task = database.get_download_task(task_id)

        self.assertEqual(result["title"], "Old Name")
        self.assertFalse(result["title_changed"])
        self.assertTrue((self.video_root / "New Name.mp4").is_file())
        self.assertFalse((self.video_root / "Old Name.mp4").exists())
        self.assertEqual(updated["id"], video["id"])
        self.assertEqual(updated["title"], "Old Name")
        self.assertEqual(updated["path"], str(self.video_root / "New Name.mp4"))
        self.assertEqual(task["file_name"], "New Name.mp4")
        self.assertEqual(task["file_path"], updated["path"])
        self.assertIsNone(task["title"])

    def test_duplicate_filename_is_rejected_without_changes(self):
        video = self._create_video()
        (self.video_root / "Existing.mp4").write_bytes(b"other")

        with self.assertRaises(VideoRenameError) as raised:
            rename_video(video["id"], "Existing")

        self.assertEqual(raised.exception.status_code, 409)
        unchanged = database.get_video(video["id"])
        self.assertEqual(unchanged["path"], video["path"])
        self.assertTrue(Path(video["path"]).is_file())
        self.assertTrue((self.video_root / "Existing.mp4").is_file())

    def test_missing_file_record_still_blocks_database_path_collision(self):
        video = self._create_video()
        target = self.video_root / "Reserved.mp4"
        target.write_bytes(b"reserved")
        database.upsert_video(
            path=target,
            title=target.stem,
            directory=self.video_root.name,
            size=target.stat().st_size,
            mtime_ns=target.stat().st_mtime_ns,
        )
        target.unlink()

        with self.assertRaises(VideoRenameError) as raised:
            rename_video(video["id"], "Reserved")

        self.assertEqual(raised.exception.code, "duplicate_database_path")
        self.assertTrue(Path(video["path"]).is_file())

    def test_case_only_rename_keeps_video_id(self):
        video = self._create_video("Case Name.mp4")
        result = rename_video(video["id"], "case name")

        self.assertEqual(result["title"], "Case Name")
        self.assertIn("case name.mp4", {path.name for path in self.video_root.iterdir()})
        self.assertEqual(database.get_video(video["id"])["title"], "Case Name")

    def test_invalid_names_and_stale_paths_are_rejected(self):
        video = self._create_video()
        for value in ("", "../escape", ".hidden", "bad/name"):
            with self.subTest(value=value), self.assertRaises(VideoRenameError):
                normalize_video_title(value, ".mp4")

        with self.assertRaises(VideoRenameError) as raised:
            rename_video(
                video["id"],
                "New Name",
                expected_path=str(self.video_root / "old-path.mp4"),
            )
        self.assertEqual(raised.exception.code, "stale_video_state")

    def test_title_normalization_matches_filename_rules(self):
        self.assertEqual(
            normalize_video_title("  New   Name #ignored.mp4 ", ".mp4"),
            "New Name",
        )

    def test_watch_page_contains_rename_dialog_and_script(self):
        video = self._create_video()
        client = admin_client()
        page = client.get("/watch/{}".format(video["id"]))

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="edit-video-name"', page.text)
        self.assertIn('id="rename-video-dialog"', page.text)
        self.assertIn('id="toggle-video-title"', page.text)
        self.assertIn("/static/video-title.js", page.text)
        self.assertIn("/static/rename-video.js", page.text)

    def test_rename_api_returns_updated_names_and_duplicate_conflict(self):
        video = self._create_video()
        client = admin_client()
        response = client.patch(
            "/api/videos/{}/name".format(video["id"]),
            json={
                "title": "API Renamed",
                "expected_path": video["path"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Old Name")
        self.assertFalse(response.json()["title_changed"])

        (self.video_root / "Conflict.mp4").write_bytes(b"existing")
        duplicate = client.patch(
            "/api/videos/{}/name".format(video["id"]),
            json={
                "title": "Conflict",
                "expected_path": str(self.video_root / "API Renamed.mp4"),
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("同名文件", duplicate.json()["detail"])

    def test_display_title_changes_without_touching_file(self):
        video = self._create_video()
        client = admin_client()
        response = client.patch(
            "/api/manage/videos/{}/display-title".format(video["id"]),
            json={
                "display_title": "新的展示 / 标题：第一集",
                "expected_updated_at": video["updated_at"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = database.get_video(video["id"])
        self.assertEqual(updated["title"], "新的展示 / 标题:第一集")
        self.assertEqual(updated["path"], video["path"])
        self.assertTrue((self.video_root / "Old Name.mp4").is_file())
        self.assertEqual(
            response.json()["display_title_override"],
            "新的展示 / 标题:第一集",
        )
        self.assertEqual(
            database.list_videos(query="新的展示")["items"][0]["title"],
            "新的展示 / 标题:第一集",
        )
        self.assertEqual(
            database.list_videos(query="Old Name")["items"][0]["id"],
            video["id"],
        )
        watch = client.get("/watch/{}".format(video["id"]))
        self.assertIn("新的展示 / 标题:第一集", watch.text)
        tv = client.get("/api/tv/v1/videos?page_size=20")
        self.assertIn(
            "新的展示 / 标题:第一集",
            [item["title"] for item in tv.json()["items"]],
        )

        restored = client.patch(
            "/api/manage/videos/{}/display-title".format(video["id"]),
            json={
                "display_title": None,
                "expected_updated_at": updated["updated_at"],
            },
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(
            database.get_video(video["id"])["title"],
            "Old Name",
        )

    def test_file_name_endpoint_preserves_custom_display_title(self):
        video = self._create_video()
        client = admin_client()
        title_response = client.patch(
            "/api/manage/videos/{}/display-title".format(video["id"]),
            json={
                "display_title": "人工展示标题",
                "expected_updated_at": video["updated_at"],
            },
        )
        self.assertEqual(title_response.status_code, 200, title_response.text)
        current = database.get_video(video["id"])
        response = client.patch(
            "/api/manage/videos/{}/file-name".format(video["id"]),
            json={
                "file_stem": "Renamed File",
                "expected_path": current["path"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = database.get_video(video["id"])
        self.assertEqual(updated["title"], "人工展示标题")
        self.assertEqual(updated["path"], str(self.video_root / "Renamed File.mp4"))
        self.assertEqual(response.json()["title"], "人工展示标题")
        self.assertFalse(response.json()["title_changed"])

    def test_management_page_contains_edit_controls(self):
        video = self._create_video()
        page = admin_client().get("/manage/videos")

        self.assertEqual(page.status_code, 200)
        self.assertIn('data-edit-video="{}"'.format(video["id"]), page.text)
        self.assertIn('id="edit-video-dialog"', page.text)
        self.assertIn('id="edit-display-title-form"', page.text)
        self.assertIn('id="edit-file-name-form"', page.text)
        self.assertIn('id="edit-access-level-form"', page.text)
        self.assertIn('id="edit-access-level-select"', page.text)
        self.assertIn("原始网页地址", page.text)

    def test_edit_access_level_updates_video_permissions(self):
        video = self._create_video()
        response = admin_client().patch(
            "/api/manage/videos/{}/access-level".format(video["id"]),
            json={"access_level": 2},
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = database.get_video_admin(video["id"])
        self.assertEqual(updated["access_level"], 2)
        self.assertEqual(response.json()["access_level_label"], "管理员")

    def test_listing_neighbors_use_effective_display_title(self):
        first = self._create_video("A.mp4")
        middle = self._create_video("B.mp4")
        self._create_video("C.mp4")
        database.set_video_display_title(middle["id"], "B Updated")

        previous, following = database.get_video_neighbors(
            first["id"],
            sort="name",
        )

        self.assertIsNone(previous)
        self.assertEqual(following["id"], middle["id"])
        self.assertEqual(following["title"], "B Updated")
        page = admin_client().get(
            "/watch/{}".format(first["id"]),
            params={"return_to": "/?sort=name"},
        )
        self.assertIn("B Updated", page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")

    def test_season_navigation_and_tv_use_effective_display_title(self):
        videos = [
            self._create_video("Episode 1.mp4"),
            self._create_video("Episode 2.mp4"),
            self._create_video("Episode 3.mp4"),
        ]
        season_data = database.upsert_video_season(
            series_name="系列",
            season_name="第一季",
            season_number=1,
            provider="test",
            source_url="https://example.com/series",
            relative_directory="系列/第一季",
        )
        season_id = int(season_data["season"]["id"])
        for index, video in enumerate(videos, start=1):
            path = Path(video["path"])
            episode_id = database.create_video_episode(
                season_id=season_id,
                episode_number=index,
                display_title="Episode {}".format(index),
                source_url="https://example.com/episode/{}".format(index),
                normalized_source_url=(
                    "https://example.com/episode/{}".format(index)
                ),
                relative_path="{}.mp4".format(index),
                absolute_path=str(path),
                download_task_id=None,
                episode_title="Episode {}".format(index),
            )
            database.attach_episode_video(
                episode_id,
                int(video["id"]),
                path,
            )
        database.set_video_display_title(
            videos[1]["id"],
            "第二集新的展示标题",
        )

        client = admin_client()
        first_page = client.get("/watch/{}".format(videos[0]["id"]))
        third_page = client.get("/watch/{}".format(videos[2]["id"]))
        tv_detail = client.get(
            "/api/tv/v1/videos/{}".format(videos[0]["id"])
        )

        self.assertIn("第二集新的展示标题", first_page.text)
        self.assertIn("第二集新的展示标题", third_page.text)
        episode_titles = [
            episode["title"]
            for episode in tv_detail.json()["series"]["episodes"]
        ]
        self.assertIn("第二集新的展示标题", episode_titles)


if __name__ == "__main__":
    unittest.main()
