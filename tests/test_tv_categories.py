import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site import database
from local_video_site.app import app
from local_video_site.core import config
from local_video_site.identity.permissions.account import ROLE_ADMIN
from tests.auth_test_utils import account_client


class TvCategoryTests(unittest.TestCase):
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
            patch.object(
                config,
                "COVER_PREVIEW_DIR",
                self.data_dir / "cover-previews",
            ),
            patch.object(
                config,
                "SERIES_COVER_DIR",
                self.data_dir / "series-covers",
            ),
            patch.object(
                config,
                "SEASON_COVER_DIR",
                self.data_dir / "season-covers",
            ),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.admin = account_client(ROLE_ADMIN, "series-admin")
        self.library = database.list_manual_categories()
        self.by_key = {
            str(item["system_key"]): item
            for item in self.library
            if item.get("system_key")
        }

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _video(self, name):
        path = self.video_root / name
        path.write_bytes(name.encode("utf-8"))
        database.upsert_video(
            path=path,
            title=name,
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        database.update_video_technical_info(
            video["id"],
            {"duration": 120},
        )
        return database.get_video(video["id"])

    def _series(self, series_name, season_names):
        entry_ids = []
        for index, season_name in enumerate(season_names, start=1):
            created = database.upsert_video_season(
                series_name=series_name,
                season_name=season_name,
                season_number=index,
                provider="test",
                source_url="test://{}".format(series_name),
                relative_directory=series_name,
            )
            video = self._video(
                "{}-{}.mp4".format(series_name, season_name)
            )
            episode_id = database.create_video_episode(
                season_id=int(created["season"]["id"]),
                episode_number=1,
                display_title="第1集",
                source_url="test://{}/{}/1".format(
                    series_name,
                    season_name,
                ),
                normalized_source_url="test://{}/{}/1".format(
                    series_name,
                    season_name,
                ),
                relative_path=str(video["path"]),
                absolute_path=str(video["path"]),
                download_task_id=None,
                episode_title="第1集",
            )
            database.attach_episode_video(
                episode_id,
                int(video["id"]),
                Path(str(video["path"])),
            )
            if index == 1:
                entry_ids.append(int(video["id"]))
        return int(created["series"]["id"]), entry_ids[0]

    def test_tv_home_only_uses_explicit_series_categories(self):
        television_id, _ = self._series("冰与火之歌", ["第一季", "第二季"])
        other_id, _ = self._series("未知作品", ["合集"])
        self._series("未分类作品", ["合集"])
        database.set_series_manual_categories(
            television_id,
            [int(self.by_key["tv"]["id"])],
        )
        database.set_series_manual_categories(
            other_id,
            [int(self.by_key["other"]["id"])],
        )

        home = TestClient(app).get("/api/tv/v1/home")
        self.assertEqual(home.status_code, 200, home.text)
        categories = {
            item["key"]: item for item in home.json()["categories"]
        }
        self.assertEqual(categories["tv"]["series_count"], 1)
        self.assertEqual(categories["other"]["series_count"], 1)
        self.assertEqual(categories["movie"]["series_count"], 0)

        television = TestClient(app).get(
            "/api/tv/v1/series",
            params={"category_key": "tv"},
        )
        self.assertEqual(television.status_code, 200, television.text)
        self.assertEqual(
            [item["title"] for item in television.json()["items"]],
            ["冰与火之歌"],
        )
        series = television.json()["items"][0]
        self.assertIsNone(series["single_season_entry_video_id"])
        self.assertEqual(series["available_season_count"], 2)

        other = TestClient(app).get(
            "/api/tv/v1/series",
            params={"category_key": "other"},
        )
        self.assertEqual(
            [item["title"] for item in other.json()["items"]],
            ["未知作品"],
        )

    def test_single_season_series_skips_season_selection(self):
        series_id, video_id = self._series("绿色星球", ["第一季"])
        database.set_series_manual_categories(
            series_id,
            [int(self.by_key["documentary"]["id"])],
        )
        response = TestClient(app).get(
            "/api/tv/v1/series",
            params={"category_key": "documentary"},
        )
        item = response.json()["items"][0]
        self.assertEqual(item["single_season_entry_video_id"], video_id)
        detail = TestClient(app).get(
            "/api/tv/v1/series/{}".format(series_id)
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(len(detail.json()["seasons"]), 1)

    def test_admin_series_categories_and_cover(self):
        series_id, video_id = self._series("测试视频集", ["第一季", "第二季"])
        page = self.admin.get("/manage/series")
        self.assertIn("分类管理", page.text)
        self.assertIn("视频封面", page.text)
        self.assertIn('id="series-category-dialog"', page.text)
        self.assertIn('id="series-cover-dialog"', page.text)
        options = self.admin.get(
            "/api/manage/series/{}/manual-category-options".format(series_id)
        )
        self.assertEqual(options.status_code, 200, options.text)
        self.assertTrue(
            any(item["is_system"] for item in options.json()["items"])
        )
        updated = self.admin.put(
            "/api/manage/series/{}/manual-categories".format(series_id),
            json={
                "manual_category_ids": [int(self.by_key["tv"]["id"])],
                "expected_updated_at": options.json()["updated_at"],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["items"][0]["system_key"], "tv")

        generated = config.SERIES_COVER_DIR / "series.jpg"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"series-cover")
        with patch(
            "local_video_site.web.routes.series.generate_series_cover_from_video",
            return_value=generated,
        ):
            cover = self.admin.put(
                "/api/manage/series/{}/cover".format(series_id),
                json={
                    "source": "video_frame",
                    "video_id": video_id,
                    "timestamp": 20,
                },
            )
        self.assertEqual(cover.status_code, 200, cover.text)
        image = TestClient(app).get(
            "/api/tv/v1/series/{}/cover".format(series_id)
        )
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, b"series-cover")

    def test_admin_can_customize_and_restore_season_cover(self):
        series_id, video_id = self._series("季封面测试", ["第一季"])
        with database.connect() as connection:
            season_id = int(connection.execute(
                "SELECT id FROM video_seasons WHERE series_id = ?",
                (series_id,),
            ).fetchone()["id"])
        page = self.admin.get("/manage/series/{}".format(series_id))
        self.assertIn("视频封面", page.text)
        self.assertIn('id="season-cover-dialog"', page.text)
        editor = self.admin.get(
            "/api/manage/series/{}/seasons/{}/cover-editor".format(
                series_id,
                season_id,
            )
        )
        self.assertEqual(editor.status_code, 200, editor.text)
        self.assertEqual(editor.json()["episodes"][0]["video_id"], video_id)

        generated = config.SEASON_COVER_DIR / "season.jpg"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b"season-cover")
        with patch(
            "local_video_site.web.routes.series.generate_season_cover_from_video",
            return_value=generated,
        ):
            saved = self.admin.put(
                "/api/manage/series/{}/seasons/{}/cover".format(
                    series_id,
                    season_id,
                ),
                json={
                    "source": "video_frame",
                    "video_id": video_id,
                    "timestamp": 30,
                },
            )
        self.assertEqual(saved.status_code, 200, saved.text)
        cover = TestClient(app).get(
            "/api/tv/v1/seasons/{}/cover".format(season_id)
        )
        self.assertEqual(cover.status_code, 200)
        self.assertEqual(cover.content, b"season-cover")

        restored = self.admin.delete(
            "/api/manage/series/{}/seasons/{}/cover".format(
                series_id,
                season_id,
            )
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        with database.connect() as connection:
            row = connection.execute(
                "SELECT cover_source FROM video_seasons WHERE id = ?",
                (season_id,),
            ).fetchone()
        self.assertEqual(row["cover_source"], "inherited")

    def test_representative_video_cover_updates_inherited_season_cover(self):
        series_id, video_id = self._series("同步封面测试", ["第一季"])
        with database.connect() as connection:
            season_id = int(connection.execute(
                "SELECT id FROM video_seasons WHERE series_id = ?",
                (series_id,),
            ).fetchone()["id"])
        cover = config.COVER_DIR / "updated-video.jpg"
        cover.parent.mkdir(parents=True, exist_ok=True)
        cover.write_bytes(b"updated-video-cover")
        database.update_custom_cover(video_id, str(cover), 50)
        with database.connect() as connection:
            row = connection.execute(
                """
                SELECT cover_path, cover_source, cover_updated_at
                FROM video_seasons WHERE id = ?
                """,
                (season_id,),
            ).fetchone()
        self.assertEqual(Path(row["cover_path"]), cover)
        self.assertEqual(row["cover_source"], "inherited")
        self.assertTrue(row["cover_updated_at"])
