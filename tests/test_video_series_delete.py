import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site.storage import deletion_tasks as video_delete_tasks
from local_video_site.storage.deletion import set_delete_password
from local_video_site import database
from local_video_site.episodes.series_management import list_season_videos, list_seasons, list_series
from tests.auth_test_utils import admin_client, user_client


TEST_DELETE_SECRET = "test-delete-secret"


class VideoSeriesDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
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
            patch.object(
                config,
                "DELETE_QUARANTINE_DIR",
                self.data_dir / "delete-quarantine",
            ),
            patch.object(
                config,
                "DELETE_PASSWORD_PATH",
                self.data_dir / ".delete-password",
            ),
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        set_delete_password(TEST_DELETE_SECRET)
        config.ensure_data_directories()
        self.series = database.upsert_video_season(
            series_name="Series One",
            season_name="第一季",
            season_number=1,
            provider="test",
            source_url="https://example.test/series",
            relative_directory="Series One/第一季",
        )

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _episode(self, season, episode_number, with_video=True):
        video_id = None
        absolute = self.video_root / "Series One" / season["name"] / (
            "{:03d}.mp4".format(episode_number)
        )
        if with_video:
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_bytes(b"video-" + str(episode_number).encode())
            stat = absolute.stat()
            database.upsert_video(
                path=absolute,
                title="Episode {}".format(episode_number),
                directory=season["name"],
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
            video = database.get_video_by_path(absolute)
            video_id = int(video["id"])
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO video_episodes (
                    season_id, episode_number, display_title,
                    episode_title, source_url, normalized_source_url,
                    relative_path, absolute_path, video_id, status,
                    origin_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'download', ?, ?)
                """,
                (
                    int(season["id"]),
                    int(episode_number),
                    "第{}集".format(episode_number),
                    "Episode {}".format(episode_number),
                    "test://{}/{}".format(season["id"], episode_number),
                    "test://{}/{}".format(season["id"], episode_number),
                    str(absolute.relative_to(self.video_root)),
                    str(absolute),
                    video_id,
                    "ready" if video_id else "planned",
                    "now",
                    "now",
                ),
            )
        return video_id

    def _two_seasons(self):
        season_one = self.series["season"]
        season_two = database.upsert_video_season(
            series_name="Series One",
            season_name="第二季",
            season_number=2,
            provider="test",
            source_url="https://example.test/series",
            relative_directory="Series One/第二季",
        )["season"]
        videos_one = [self._episode(season_one, 1), self._episode(season_one, 2)]
        videos_two = [self._episode(season_two, 1)]
        return season_one, season_two, videos_one, videos_two

    def _run_all_jobs(self):
        processed = []
        for _ in range(30):
            job = video_delete_tasks.claim_delete_job()
            if job is None:
                break
            processed.append(video_delete_tasks.process_delete_job(job)["id"])
        return processed

    def test_series_and_season_pages_load_videos(self):
        season_one, season_two, videos_one, videos_two = self._two_seasons()
        admin = admin_client()

        series_page = admin.get("/manage/series")
        self.assertEqual(series_page.status_code, 200, series_page.text)
        self.assertIn("Series One", series_page.text)
        self.assertIn("视频集管理", series_page.text)

        season_page = admin.get(
            "/manage/series/{}/seasons/{}".format(
                self.series["series"]["id"],
                season_one["id"],
            )
        )
        self.assertEqual(season_page.status_code, 200, season_page.text)
        self.assertIn("Episode 1", season_page.text)
        self.assertIn("Episode 2", season_page.text)
        second_page = admin.get(
            "/manage/series/{}/seasons/{}".format(
                self.series["series"]["id"],
                season_two["id"],
            )
        ).text
        self.assertIn("Episode 1", second_page)
        self.assertNotIn(
            'href="/watch/{}"'.format(videos_one[0]),
            second_page,
        )

        user = user_client(username="series-page-user")
        self.assertEqual(user.get("/manage/series").status_code, 403)

    def test_series_delete_creates_dependency_chain_and_executes_in_order(self):
        season_one, season_two, videos_one, videos_two = self._two_seasons()
        result = video_delete_tasks.create_delete_batch(
            scope_type="series",
            target_id=int(self.series["series"]["id"]),
            source_action="series_management",
        )
        self.assertEqual(result["video_jobs"], 3)
        self.assertEqual(result["season_jobs"], 2)
        self.assertEqual(result["series_jobs"], 1)

        jobs = video_delete_tasks.list_delete_jobs(page_size=20)["items"]
        by_scope = {
            scope: sorted(
                (item for item in jobs if item["scope_type"] == scope),
                key=lambda item: item["id"],
            )
            for scope in ("video", "season", "series")
        }
        self.assertTrue(all(
            item["status"] == "pending" for item in by_scope["video"]
        ))
        self.assertTrue(all(
            item["status"] == "blocked" for item in by_scope["season"]
        ))
        self.assertEqual(by_scope["series"][0]["status"], "blocked")

        processed = self._run_all_jobs()
        self.assertEqual(len(processed), 6)
        final = video_delete_tasks.list_delete_jobs(page_size=20)["items"]
        self.assertTrue(all(item["status"] == "success" for item in final))
        for video_id in videos_one + videos_two:
            self.assertIsNone(database.get_video(video_id))
        with database.connect() as connection:
            series = connection.execute(
                "SELECT is_deleted FROM video_series WHERE id = ?",
                (int(self.series["series"]["id"]),),
            ).fetchone()
            seasons = connection.execute(
                "SELECT is_deleted FROM video_seasons WHERE id IN (?, ?)",
                (int(season_one["id"]), int(season_two["id"])),
            ).fetchall()
        self.assertEqual(series["is_deleted"], 1)
        self.assertTrue(all(row["is_deleted"] == 1 for row in seasons))
        self.assertEqual(list_series()["total"], 0)

    def test_season_delete_blocks_until_video_tasks_succeed(self):
        season = self.series["season"]
        video_ids = [self._episode(season, 1), self._episode(season, 2)]
        result = video_delete_tasks.create_delete_batch(
            scope_type="season",
            target_id=int(season["id"]),
            source_action="season_management",
        )
        self.assertEqual(result["video_jobs"], 2)
        first = video_delete_tasks.claim_delete_job()
        video_delete_tasks.process_delete_job(first)
        jobs = video_delete_tasks.list_delete_jobs(page_size=10)["items"]
        season_job = next(
            item for item in jobs if item["scope_type"] == "season"
        )
        self.assertEqual(season_job["status"], "blocked")
        self._run_all_jobs()
        self.assertEqual(
            video_delete_tasks.get_delete_job(season_job["id"])["status"],
            "success",
        )
        self.assertTrue(all(
            database.get_video(video_id) is None for video_id in video_ids
        ))

    def test_soft_deleted_series_and_season_are_reactivated_by_upsert(self):
        season = self.series["season"]
        self._episode(season, 1, with_video=False)
        video_delete_tasks.create_delete_batch(
            scope_type="series",
            target_id=int(self.series["series"]["id"]),
            source_action="series_management",
        )
        self._run_all_jobs()
        restored = database.upsert_video_season(
            series_name="Series One",
            season_name="第一季",
            season_number=1,
            provider="test",
            source_url="https://example.test/series",
            relative_directory="Series One/第一季",
        )
        self.assertEqual(restored["series"]["is_deleted"], 0)
        self.assertEqual(restored["season"]["is_deleted"], 0)

    def test_delete_api_requires_code_and_creates_video_task(self):
        video_id = self._episode(self.series["season"], 1)
        admin = admin_client()
        rejected = admin.post(
            "/api/manage/delete-jobs",
            json={
                "scope_type": "video",
                "target_id": video_id,
                "verification_code": "wrong",
            },
        )
        self.assertEqual(rejected.status_code, 401)

        created = admin.post(
            "/api/manage/delete-jobs",
            json={
                "scope_type": "video",
                "target_id": video_id,
                "verification_code": TEST_DELETE_SECRET,
            },
        )
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(created.json()["video_jobs"], 1)
        self.assertEqual(
            video_delete_tasks.list_delete_jobs()["items"][0]["source_action"],
            "video_management",
        )

    def test_season_video_search_uses_exact_video_id(self):
        season = self.series["season"]
        first = self._episode(season, 1)
        self._episode(season, 2)
        result = list_season_videos(
            int(self.series["series"]["id"]),
            int(season["id"]),
            query=str(first),
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["video_id"], first)

    def test_series_and_season_display_names_are_presentation_only(self):
        season = self.series["season"]
        video_id = self._episode(season, 1)
        series_id = int(self.series["series"]["id"])
        season_id = int(season["id"])
        admin = admin_client()

        series_response = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={
                "display_name": "测试视频集",
                "expected_updated_at": self.series["series"]["updated_at"],
            },
        )
        self.assertEqual(series_response.status_code, 200, series_response.text)
        self.assertEqual(
            series_response.json()["previous_display_name"],
            "Series One",
        )
        self.assertFalse(series_response.json()["path_changed"])

        season_response = admin.patch(
            "/api/manage/series/{}/seasons/{}".format(
                series_id,
                season_id,
            ),
            json={
                "display_name": "测试第一季",
                "expected_updated_at": season["updated_at"],
            },
        )
        self.assertEqual(season_response.status_code, 200, season_response.text)
        self.assertFalse(season_response.json()["path_changed"])

        with database.connect() as connection:
            series_row = connection.execute(
                """
                SELECT name, normalized_name, display_name_override
                FROM video_series WHERE id = ?
                """,
                (series_id,),
            ).fetchone()
            season_row = connection.execute(
                """
                SELECT name, relative_directory, display_name_override
                FROM video_seasons WHERE id = ?
                """,
                (season_id,),
            ).fetchone()
        self.assertEqual(series_row["name"], "Series One")
        self.assertEqual(series_row["normalized_name"], "series one")
        self.assertEqual(series_row["display_name_override"], "测试视频集")
        self.assertEqual(season_row["name"], "第一季")
        self.assertEqual(season_row["relative_directory"], "Series One/第一季")
        self.assertEqual(season_row["display_name_override"], "测试第一季")
        self.assertIsNotNone(database.get_video(video_id))

        series_page = admin.get("/manage/series").text
        self.assertIn("测试视频集", series_page)
        self.assertIn("修改数据", series_page)
        self.assertIn("series-edit-dialog", series_page)
        season_page = admin.get(
            "/manage/series/{}".format(series_id)
        ).text
        self.assertIn("测试第一季", season_page)
        episode_page = admin.get(
            "/manage/series/{}/seasons/{}".format(
                series_id,
                season_id,
            )
        ).text
        self.assertIn("测试视频集 · 测试第一季", episode_page)
        self.assertIn("<th>剧集标题</th>", episode_page)
        self.assertIn(
            '<td class="episode-title-cell">Episode 1</td>',
            episode_page,
        )

        by_display_name = list_series(query="视频集")
        self.assertEqual(by_display_name["total"], 1)
        seasons = list_seasons(series_id, query="测试第一季")
        self.assertEqual(seasons["total"], 1)

        reset = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={
                "display_name": "Series One",
                "expected_updated_at": series_response.json()["updated_at"],
            },
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        with database.connect() as connection:
            override = connection.execute(
                "SELECT display_name_override FROM video_series WHERE id = ?",
                (series_id,),
            ).fetchone()[0]
            audits = connection.execute(
                """
                SELECT action, details_json FROM auth_audit_log
                WHERE action IN (
                    'series.display_name.updated',
                    'season.display_name.updated'
                )
                ORDER BY id
                """
            ).fetchall()
        self.assertIsNone(override)
        self.assertEqual(
            [row["action"] for row in audits],
            [
                "series.display_name.updated",
                "season.display_name.updated",
                "series.display_name.updated",
            ],
        )
        audit_details = json.loads(audits[1]["details_json"])
        self.assertEqual(audit_details["old_display_name"], "第一季")
        self.assertEqual(audit_details["new_display_name"], "测试第一季")
        self.assertFalse(audit_details["path_changed"])

    def test_episode_title_update_is_presentation_only_and_searchable(self):
        season = self.series["season"]
        series_id = int(self.series["series"]["id"])
        season_id = int(season["id"])
        video_id = self._episode(season, 1)
        before_items = list_season_videos(series_id, season_id)["items"]
        episode = before_items[0]
        before_video = database.get_video_admin(video_id)
        admin = admin_client()

        response = admin.patch(
            "/api/manage/series/{}/seasons/{}/episodes/{}".format(
                series_id,
                season_id,
                episode["episode_id"],
            ),
            json={
                "episode_title": "百变狸猫 平成狸合戦ぽんぽこ",
                "expected_updated_at": episode["episode_updated_at"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["episode_title"], "百变狸猫 平成狸合戦ぽんぽこ")
        self.assertEqual(payload["old_episode_title"], "Episode 1")
        self.assertEqual(payload["video_id"], video_id)

        with database.connect() as connection:
            episode_row = connection.execute(
                """
                SELECT episode_title, display_title, updated_at
                FROM video_episodes WHERE id = ?
                """,
                (int(episode["episode_id"]),),
            ).fetchone()
            audit = connection.execute(
                """
                SELECT details_json FROM auth_audit_log
                WHERE action = 'episode.title_changed'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual(
            episode_row["episode_title"],
            "百变狸猫 平成狸合戦ぽんぽこ",
        )
        self.assertEqual(episode_row["display_title"], "第1集")
        self.assertEqual(episode_row["updated_at"], payload["updated_at"])
        after_video = database.get_video_admin(video_id)
        for field in ("title", "path", "display_title_override"):
            self.assertEqual(after_video[field], before_video[field])

        search = list_season_videos(
            series_id,
            season_id,
            query="平成狸合戦",
        )
        self.assertEqual(search["total"], 1)
        self.assertEqual(search["items"][0]["episode_id"], episode["episode_id"])

        page = admin.get(
            "/manage/series/{}/seasons/{}".format(series_id, season_id)
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("修改数据", page.text)
        self.assertIn("百变狸猫 平成狸合戦ぽんぽこ", page.text)
        self.assertIn("episode-edit-dialog", page.text)
        self.assertIn("season-video-edit.js", page.text)
        watch = admin.get("/watch/{}".format(video_id))
        self.assertEqual(watch.status_code, 200, watch.text)
        self.assertIn("百变狸猫 平成狸合戦ぽんぽこ", watch.text)

        audit_details = json.loads(audit["details_json"])
        self.assertEqual(audit_details["episode_number"], 1)
        self.assertEqual(
            audit_details["new_episode_title"],
            "百变狸猫 平成狸合戦ぽんぽこ",
        )

    def test_episode_title_update_validates_permissions_and_relationships(self):
        season = self.series["season"]
        series_id = int(self.series["series"]["id"])
        season_id = int(season["id"])
        self._episode(season, 1)
        episode = list_season_videos(series_id, season_id)["items"][0]
        path = "/api/manage/series/{}/seasons/{}/episodes/{}".format(
            series_id,
            season_id,
            episode["episode_id"],
        )
        user = user_client(username="episode-edit-user")
        forbidden = user.patch(path, json={"episode_title": "普通用户修改"})
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        admin = admin_client()
        cases = (
            ("   ", "episode_title_required"),
            ("名" * 201, "episode_title_too_long"),
            ("标题\n换行", "episode_title_invalid"),
            ("第1集", "episode_title_generic"),
        )
        for title, code in cases:
            with self.subTest(code=code):
                response = admin.patch(
                    path,
                    json={"episode_title": title},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], code)

        stale = admin.patch(
            path,
            json={
                "episode_title": "并发修改",
                "expected_updated_at": "2000-01-01T00:00:00+08:00",
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "data_changed")

        other_season = database.upsert_video_season(
            series_name="Series One",
            season_name="第二季",
            season_number=2,
            provider="test",
            source_url="https://example.test/series",
            relative_directory="Series One/第二季",
        )["season"]
        wrong_relationship = admin.patch(
            "/api/manage/series/{}/seasons/{}/episodes/{}".format(
                series_id,
                other_season["id"],
                episode["episode_id"],
            ),
            json={"episode_title": "错误归属"},
        )
        self.assertEqual(
            wrong_relationship.status_code,
            404,
            wrong_relationship.text,
        )
        self.assertEqual(
            wrong_relationship.json()["detail"]["code"],
            "episode_missing",
        )

        video_delete_tasks.create_delete_batch(
            scope_type="series",
            target_id=series_id,
            source_action="series_management",
        )
        deleting = admin.patch(
            path,
            json={"episode_title": "删除中不可修改"},
        )
        self.assertEqual(deleting.status_code, 409, deleting.text)
        self.assertEqual(
            deleting.json()["detail"]["code"],
            "active_delete_task",
        )

    def test_deleted_video_episode_title_can_still_be_updated(self):
        season = self.series["season"]
        series_id = int(self.series["series"]["id"])
        season_id = int(season["id"])
        video_id = self._episode(season, 1)
        self.assertTrue(database.delete_video_record(video_id))
        episode = list_season_videos(
            series_id,
            season_id,
            record_state="deleted",
        )["items"][0]
        admin = admin_client()
        response = admin.patch(
            "/api/manage/series/{}/seasons/{}/episodes/{}".format(
                series_id,
                season_id,
                episode["episode_id"],
            ),
            json={
                "episode_title": "已删除剧集的标题",
                "expected_updated_at": episode["episode_updated_at"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["episode_title"], "已删除剧集的标题")

    def test_display_name_updates_validate_input_duplicates_and_staleness(self):
        series_id = int(self.series["series"]["id"])
        season_id = int(self.series["season"]["id"])
        database.upsert_video_season(
            series_name="Series Two",
            season_name="第二季",
            season_number=2,
            provider="test",
            source_url="https://example.test/series-two",
            relative_directory="Series Two/第二季",
        )
        admin = admin_client()

        empty = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={"display_name": "   "},
        )
        self.assertEqual(empty.status_code, 422, empty.text)
        self.assertEqual(empty.json()["detail"]["code"], "display_name_required")

        too_long = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={"display_name": "名" * 121},
        )
        self.assertEqual(too_long.status_code, 422, too_long.text)
        self.assertEqual(
            too_long.json()["detail"]["code"],
            "display_name_too_long",
        )

        duplicate_series = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={"display_name": "Series Two"},
        )
        self.assertEqual(duplicate_series.status_code, 409, duplicate_series.text)
        self.assertEqual(
            duplicate_series.json()["detail"]["code"],
            "display_name_duplicate",
        )

        database.upsert_video_season(
            series_name="Series One",
            season_name="第三季",
            season_number=3,
            provider="test",
            source_url="https://example.test/series",
            relative_directory="Series One/第三季",
        )
        duplicate_season = admin.patch(
            "/api/manage/series/{}/seasons/{}".format(series_id, season_id),
            json={"display_name": "第三季"},
        )
        self.assertEqual(duplicate_season.status_code, 409, duplicate_season.text)
        self.assertEqual(
            duplicate_season.json()["detail"]["code"],
            "display_name_duplicate",
        )

        stale = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={
                "display_name": "Stale Update",
                "expected_updated_at": "2000-01-01T00:00:00+08:00",
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "data_changed")

        wrong_series = admin.patch(
            "/api/manage/series/{}/seasons/{}".format(series_id + 999, season_id),
            json={"display_name": "错误归属"},
        )
        self.assertEqual(wrong_series.status_code, 404, wrong_series.text)

    def test_display_name_update_requires_admin_and_blocks_active_delete(self):
        series = self.series["series"]
        series_id = int(series["id"])
        user = user_client(username="series-edit-user")
        forbidden = user.patch(
            "/api/manage/series/{}".format(series_id),
            json={"display_name": "不应成功"},
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        video_delete_tasks.create_delete_batch(
            scope_type="series",
            target_id=series_id,
            source_action="series_management",
            expected_updated_at=series["updated_at"],
        )
        admin = admin_client()
        blocked = admin.patch(
            "/api/manage/series/{}".format(series_id),
            json={"display_name": "删除中不可修改"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(
            blocked.json()["detail"]["code"],
            "active_delete_task",
        )
        page = admin.get("/manage/series").text
        self.assertIn("存在活动删除任务，暂不能修改", page)


if __name__ == "__main__":
    unittest.main()
