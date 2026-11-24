import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site.storage import deletion_tasks as video_delete_tasks
from local_video_site import database
from local_video_site.ingestion.external_scan import ExternalScanError, _parse_episode_file, commit_scan, create_scan, get_scan, list_scans, preview_scan_source, process_scan
from local_video_site.storage.volumes import resolve_video_storage
from tests.auth_test_utils import admin_client, user_client


class ExternalVideoScanTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.local_root = self.root / "local-videos"
        self.local_root.mkdir()
        self.volumes_root = self.root / "Volumes"
        self.mount_root = self.volumes_root / "ExternalDisk"
        self.mount_root.mkdir(parents=True)
        self.source = self.mount_root / "老友记" / "第一季"
        self.source.mkdir(parents=True)
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
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(config, "DEFAULT_ROOTS", [self.local_root]),
            patch.object(config, "minimum_video_bytes", return_value=0),
            patch.dict(
                os.environ,
                {"LOCAL_VIDEO_VOLUME_ROOTS": str(self.volumes_root)},
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

    def _video(self, name: str) -> Path:
        path = self.source / name
        path.write_bytes((name.encode("utf-8") * 2048)[:8192])
        return path

    def _video_at(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((path.name.encode("utf-8") * 2048)[:8192])
        return path

    def _target_season(self, episode_numbers=()):
        created = database.upsert_video_season(
            series_name="Frieren",
            season_name="第二季",
            season_number=2,
            provider="bilibili",
            source_url="https://example.test/frieren-season-2",
            relative_directory="Frieren/第二季",
        )
        season_id = int(created["season"]["id"])
        for episode_number in episode_numbers:
            database.create_video_episode(
                season_id=season_id,
                episode_number=int(episode_number),
                display_title="第{}集".format(episode_number),
                source_url="https://example.test/frieren/{}".format(
                    episode_number
                ),
                normalized_source_url=(
                    "https://example.test/frieren/{}".format(
                        episode_number
                    )
                ),
                relative_path="Frieren/第二季/{:02d}.mp4".format(
                    episode_number
                ),
                absolute_path=str(
                    self.mount_root
                    / "Frieren"
                    / "第二季"
                    / "{:02d}.mp4".format(episode_number)
                ),
                download_task_id=None,
                episode_title="第{}集".format(episode_number),
            )
        return created["series"], created["season"]

    def _resource_patches(self):
        def thumbnail(video_path, size, mtime_ns, duration, **_kwargs):
            config.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
            path = config.THUMBNAIL_DIR / (Path(video_path).stem + ".jpg")
            path.write_bytes(b"thumbnail")
            return path

        def preview(video, **_kwargs):
            config.PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            path = config.PREVIEW_DIR / "{}.mp4".format(video["id"])
            path.write_bytes(b"preview")
            return {"path": path, "start": 1.0, "duration": 8.0}

        info = {
            "duration": 120.0,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "audio_codec": "aac",
        }
        return (
            patch(
                "local_video_site.ingestion.external_scan.probe_video",
                return_value=info,
            ),
            patch(
                "local_video_site.ingestion.external_scan.generate_thumbnail",
                side_effect=thumbnail,
            ),
            patch(
                "local_video_site.ingestion.external_scan.generate_preview",
                side_effect=preview,
            ),
        )

    def _process(self, scan_id: str):
        patches = self._resource_patches()
        for item in patches:
            item.start()
        try:
            return process_scan(scan_id)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_single_file_scans_in_place_and_keeps_external_storage(self):
        path = (self.mount_root / "standalone.mp4").resolve()
        path.write_bytes(b"video")
        scan = create_scan(
            str(path),
            scan_mode="file",
            access_level=1,
            created_by_username="admin",
        )
        result = self._process(scan["scan_id"])

        self.assertEqual(result["status"], "completed")
        video = database.get_video_by_path(path)
        self.assertIsNotNone(video)
        self.assertEqual(video["access_level"], 1)
        self.assertIsNotNone(video["storage_volume_id"])
        self.assertEqual(resolve_video_storage(video)["state"], "external_online")
        self.assertTrue(path.is_file())

    def test_batch_review_does_not_insert_until_selected_commit(self):
        self._video("clip-a.mp4")
        self._video("clip-b.mp4")
        scan = create_scan(
            str(self.source),
            scan_mode="batch",
            access_level=0,
            created_by_username="admin",
        )
        reviewed = self._process(scan["scan_id"])
        self.assertEqual(reviewed["status"], "review")
        self.assertEqual(database.list_videos()["total"], 0)

        selected = reviewed["items"][0]
        committed = commit_scan(
            scan["scan_id"],
            item_access_levels={int(selected["id"]): 2},
            selected_item_ids=[int(selected["id"])],
        )

        self.assertEqual(committed["imported"], 1)
        self.assertEqual(database.list_videos()["total"], 1)
        video = database.get_video_by_path(Path(selected["source_path"]))
        self.assertEqual(video["access_level"], 2)
        self.assertEqual(
            database.list_videos()["items"][0]["preview_status"],
            "ready",
        )

    def test_collection_builds_series_and_uniform_access_level(self):
        self._video("老友记 第一季 001.mp4")
        self._video("老友记 第一季 002.mp4")
        scan = create_scan(
            str(self.source),
            scan_mode="collection",
            access_level=1,
            series_name="老友记",
            season_name="第一季",
            season_number=1,
            created_by_username="admin",
        )
        result = self._process(scan["scan_id"])

        self.assertEqual(result["status"], "completed")
        with database.connect() as connection:
            series = connection.execute(
                "SELECT * FROM video_series WHERE name = '老友记'"
            ).fetchone()
            season = connection.execute(
                "SELECT * FROM video_seasons WHERE series_id = ?",
                (series["id"],),
            ).fetchone()
            episodes = connection.execute(
                """
                SELECT * FROM video_episodes
                WHERE season_id = ? ORDER BY episode_number
                """,
                (season["id"],),
            ).fetchall()
            videos = connection.execute(
                """
                SELECT * FROM videos
                WHERE storage_volume_id IS NOT NULL
                ORDER BY id
                """
            ).fetchall()
        self.assertEqual([row["episode_number"] for row in episodes], [1, 2])
        self.assertTrue(all(row["access_level"] == 1 for row in videos))
        self.assertEqual(season["metadata_video_id"], episodes[0]["video_id"])
        self.assertGreater(len(episodes[0]["source_url"]), 0)

    def test_offline_external_volume_is_reported_as_offline(self):
        path = (self.mount_root / "offline.mp4").resolve()
        path.write_bytes(b"video")
        scan = create_scan(str(path), scan_mode="file")
        self._process(scan["scan_id"])
        video = database.get_video_by_path(path)

        moved = self.volumes_root / "EjectedDisk"
        self.mount_root.rename(moved)
        try:
            self.assertEqual(
                resolve_video_storage(video)["state"],
                "external_offline",
            )
        finally:
            moved.rename(self.mount_root)

    def test_external_scan_pages_and_api_are_admin_only(self):
        path = self.mount_root / "permission.mp4"
        path.write_bytes(b"video")
        admin = admin_client()
        user = user_client(username="scan-user")

        page = admin.get("/manage/videos/external-scan")
        self.assertEqual(page.status_code, 200)
        self.assertIn("加入到现有视频集", page.text)
        self.assertIn("目标视频集 ID", page.text)
        self.assertIn("v=20261120-target-season", page.text)
        self.assertIn("加入到现有视频季", page.text)
        self.assertIn("目标视频季 ID", page.text)
        self.assertEqual(
            user.get("/manage/videos/external-scan").status_code,
            403,
        )
        created = admin.post(
            "/api/manage/external-scans",
            json={
                "source_path": str(path),
                "scan_mode": "file",
                "access_level": 2,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        scan = created.json()
        self.assertEqual(scan["access_level"], 2)
        self.assertEqual(
            admin.get(
                "/manage/videos/external-scan/{}".format(scan["scan_id"])
            ).status_code,
            200,
        )
        self.assertEqual(
            user.get("/api/manage/external-scans").status_code,
            403,
        )

    def test_collection_rejects_duplicate_episode_numbers(self):
        self._video("老友记 第一季 001.mp4")
        self._video("老友记 第一季 第1集.mp4")
        with self.assertRaises(ExternalScanError) as raised:
            create_scan(
                str(self.source),
                scan_mode="collection",
                series_name="老友记",
                season_name="第一季",
            )
        self.assertEqual(raised.exception.code, "duplicate_episode_number")
        self.assertEqual(list_scans()["total"], 0)

    def test_single_season_discovery_uses_editable_name_and_fixed_number(self):
        self._video("01.mp4")
        self._video("02.mp4")
        result = preview_scan_source(
            str(self.source),
            scan_mode="collection",
        )

        self.assertEqual(result["grouping_mode"], "single_season")
        self.assertEqual(result["suggested_series_name"], self.source.name)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["season_name"], "全季")
        self.assertEqual(result["groups"][0]["season_number"], 1)
        self.assertFalse(result["groups"][0]["number_editable"])
        invalid = json.loads(json.dumps(result["groups"]))
        invalid[0]["season_number"] = 2
        with self.assertRaises(ExternalScanError) as raised:
            create_scan(
                str(self.source),
                scan_mode="collection",
                series_name="Series",
                groups=invalid,
            )
        self.assertEqual(
            raised.exception.code,
            "single_season_number_fixed",
        )

    def test_filename_season_episode_formats_are_parsed(self):
        cases = {
            "The.Big.Bang.Theory.S02E01.1080p.Blu-Ray.AC3.x265.10bit-Yumi.mkv": (
                2,
                1,
            ),
            "老友记S01E01.mkv": (1, 1),
            "老友记 S01.E02.mkv": (1, 2),
            "老友记SE01.01.mkv": (1, 1),
            "老友记SE02_08.mkv": (2, 8),
            "老友记 Season 3 Episode 004.mkv": (3, 4),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                parsed = _parse_episode_file(Path(name))
                self.assertEqual(
                    (parsed.season_number, parsed.episode_number),
                    expected,
                )
                self.assertTrue(parsed.detection_mode)

    def test_leading_chinese_index_is_parsed_as_episode_number(self):
        cases = {
            "10、百变狸猫 平成狸合戦ぽんぽこ (1994) .mp4": 10,
            "01、标题.mp4": 1,
            " 10 、标题.mp4": 10,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                parsed = _parse_episode_file(Path(name))
                self.assertIsNone(parsed.season_number)
                self.assertEqual(parsed.episode_number, expected)
                self.assertEqual(parsed.detection_mode, "前导序号、")
                self.assertIn("、", parsed.matched_text)

    def test_leading_index_year_is_not_treated_as_episode(self):
        parsed = _parse_episode_file(
            Path("2024、年度合集 平成狸合戦ぽんぽこ.mp4")
        )
        self.assertIsNone(parsed.episode_number)

    def test_leading_index_items_are_sorted_numerically(self):
        for name in (
            "10、第十集.mp4",
            "2、第二集.mp4",
            "11、第十一集.mp4",
            "1、第一集.mp4",
        ):
            self._video(name)

        discovery = preview_scan_source(
            str(self.source),
            scan_mode="collection",
        )

        self.assertEqual(discovery["grouping_mode"], "single_season")
        self.assertEqual(
            [
                item["episode_number"]
                for item in discovery["groups"][0]["items"]
            ],
            [1, 2, 10, 11],
        )

    def test_direct_files_are_grouped_by_season_from_filename(self):
        for name in (
            "老友记S02E01.mkv",
            "老友记S02E02.mkv",
            "老友记S01E01.mkv",
            "老友记SE01.02.mkv",
        ):
            self._video(name)

        discovery = preview_scan_source(
            str(self.source),
            scan_mode="collection",
        )

        self.assertEqual(discovery["grouping_mode"], "multi_season")
        self.assertEqual(
            [
                (group["season_name"], group["season_number"])
                for group in discovery["groups"]
            ],
            [("第1季", 1), ("第2季", 2)],
        )
        self.assertEqual(
            [item["episode_number"] for item in discovery["groups"][0]["items"]],
            [1, 2],
        )
        self.assertEqual(
            [item["episode_number"] for item in discovery["groups"][1]["items"]],
            [1, 2],
        )

    def test_multi_season_discovery_can_be_manually_corrected(self):
        series_root = self.mount_root / "Series"
        self._video_at(series_root / "第三季" / "01.mp4")
        self._video_at(series_root / "Season04" / "01.mp4")
        discovery = preview_scan_source(
            str(series_root),
            scan_mode="collection",
        )

        self.assertEqual(discovery["grouping_mode"], "multi_season")
        self.assertEqual(
            [group["season_name"] for group in discovery["groups"]],
            ["Season04", "第三季"],
        )
        groups = discovery["groups"]
        groups[0]["season_name"] = "第四季（修正）"
        groups[0]["season_number"] = 4
        groups[1]["season_name"] = "第三季（修正）"
        groups[1]["season_number"] = 3
        groups[1]["items"][0]["episode_number"] = 21

        scan = create_scan(
            str(series_root),
            scan_mode="collection",
            access_level=1,
            series_name="Series（修正）",
            groups=groups,
        )
        items = scan["items"]
        self.assertEqual(scan["grouping_mode"], "multi_season")
        self.assertEqual(scan["season_group_count"], 2)
        self.assertEqual(
            {(item["season_name"], item["season_number"]) for item in items},
            {("第四季（修正）", 4), ("第三季（修正）", 3)},
        )
        corrected = next(
            item
            for item in items
            if item["season_name"] == "第三季（修正）"
        )
        self.assertEqual(corrected["episode_number"], 21)
        admin = admin_client()
        detail = admin.get(
            "/manage/videos/external-scan/{}".format(scan["scan_id"])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn("第四季（修正）", detail.text)
        self.assertIn("第三季（修正）", detail.text)

    def test_collection_requires_every_discovered_file_in_groups(self):
        self._video("01.mp4")
        self._video("02.mp4")
        with self.assertRaises(ExternalScanError) as raised:
            create_scan(
                str(self.source),
                scan_mode="collection",
                series_name="Series",
                groups=[{
                    "season_name": "全季",
                    "season_number": 1,
                    "items": [{
                        "source_path": str(self.source / "01.mp4"),
                        "episode_number": 1,
                    }],
                }],
            )
        self.assertEqual(raised.exception.code, "scan_items_unassigned")

    def test_collection_can_attach_new_seasons_to_existing_series(self):
        target = database.upsert_video_season(
            series_name="Frieren",
            season_name="第一季",
            season_number=1,
            provider="bilibili",
            source_url="https://example.test/frieren",
            relative_directory="Frieren/第一季",
        )
        target_series_id = int(target["series"]["id"])
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_series
                SET display_name_override = '葬送的芙莉莲'
                WHERE id = ?
                """,
                (target_series_id,),
            )
        series_root = self.mount_root / "FrierenExternal"
        self._video_at(series_root / "Season02" / "S02E01.mkv")
        self._video_at(series_root / "Season02" / "S02E02.mkv")
        self._video_at(series_root / "Season03" / "S03E01.mkv")
        discovery = preview_scan_source(
            str(series_root),
            scan_mode="collection",
        )

        admin = admin_client()
        created = admin.post(
            "/api/manage/external-scans",
            json={
                "source_path": str(series_root),
                "scan_mode": "collection",
                "access_level": 1,
                "series_name": "外盘来源名称",
                "attach_to_series": True,
                "target_series_id": target_series_id,
                "groups": discovery["groups"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        scan = created.json()
        self.assertEqual(scan["target_series_id"], target_series_id)
        self.assertEqual(scan["target_series_name"], "葬送的芙莉莲")

        result = self._process(scan["scan_id"])
        self.assertEqual(result["status"], "completed")

        with database.connect() as connection:
            series_rows = connection.execute(
                "SELECT * FROM video_series ORDER BY id"
            ).fetchall()
            season_rows = connection.execute(
                """
                SELECT * FROM video_seasons
                WHERE series_id = ? ORDER BY season_number
                """,
                (target_series_id,),
            ).fetchall()
            episode_counts = connection.execute(
                """
                SELECT s.season_number, COUNT(e.id) AS count
                FROM video_seasons s
                LEFT JOIN video_episodes e ON e.season_id = s.id
                WHERE s.series_id = ?
                GROUP BY s.id ORDER BY s.season_number
                """,
                (target_series_id,),
            ).fetchall()
            audit = connection.execute(
                """
                SELECT details_json FROM auth_audit_log
                WHERE action = 'external_scan.created'
                  AND target_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (scan["scan_id"],),
            ).fetchone()
        self.assertEqual(len(series_rows), 1)
        self.assertEqual(series_rows[0]["name"], "Frieren")
        self.assertEqual(series_rows[0]["provider"], "bilibili")
        self.assertEqual(series_rows[0]["display_name_override"], "葬送的芙莉莲")
        self.assertEqual(
            [int(row["season_number"]) for row in season_rows],
            [1, 2, 3],
        )
        self.assertEqual(
            [int(row["count"]) for row in episode_counts],
            [0, 2, 1],
        )
        self.assertTrue(
            all(
                str(row["source_url"]).startswith("external-scan://")
                for row in season_rows[1:]
            )
        )
        self.assertEqual(
            json.loads(audit["details_json"])["target_series_id"],
            target_series_id,
        )

        detail = admin.get(
            "/manage/videos/external-scan/{}".format(scan["scan_id"])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(
            "加入视频集：",
            detail.text,
        )
        self.assertIn("#{}".format(target_series_id), detail.text)
        history = admin.get("/manage/videos/external-scan")
        self.assertIn("→ #{}".format(target_series_id), history.text)

    def test_existing_series_assignment_rejects_season_conflicts(self):
        target = database.upsert_video_season(
            series_name="Frieren",
            season_name="第二季",
            season_number=2,
            provider="external_scan",
            source_url="https://example.test/frieren-season-2",
            relative_directory="Frieren/第二季",
        )
        target_series_id = int(target["series"]["id"])
        self._video("S02E01.mkv")
        discovery = preview_scan_source(
            str(self.source),
            scan_mode="collection",
        )

        with self.assertRaises(ExternalScanError) as raised:
            create_scan(
                str(self.source),
                scan_mode="collection",
                series_name="外盘来源",
                groups=discovery["groups"],
                attach_to_series=True,
                target_series_id=target_series_id,
            )
        self.assertEqual(
            raised.exception.code,
            "target_season_conflict",
        )
        with database.connect() as connection:
            scan_count = connection.execute(
                "SELECT COUNT(*) FROM video_scan_batches"
            ).fetchone()[0]
        self.assertEqual(scan_count, 0)

    def test_series_assignment_preview_checks_admin_and_delete_state(self):
        target = database.upsert_video_season(
            series_name="Assignment",
            season_name="第一季",
            season_number=1,
            provider="test",
            source_url="https://example.test/assignment",
            relative_directory="Assignment/第一季",
        )
        target_series_id = int(target["series"]["id"])
        admin = admin_client()
        user = user_client(username="assignment-preview-user")

        preview = admin.get(
            "/api/manage/series/{}/assignment-preview".format(
                target_series_id
            )
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["display_name"], "Assignment")
        self.assertEqual(preview.json()["seasons"][0]["season_number"], 1)
        self.assertEqual(
            user.get(
                "/api/manage/series/{}/assignment-preview".format(
                    target_series_id
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            admin.get(
                "/api/manage/series/999999/assignment-preview"
            ).status_code,
            404,
        )

        video_delete_tasks.create_delete_batch(
            scope_type="series",
            target_id=target_series_id,
            source_action="series_management",
        )
        blocked = admin.get(
            "/api/manage/series/{}/assignment-preview".format(
                target_series_id
            )
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(
            blocked.json()["detail"]["code"],
            "target_series_deleting",
        )

    def test_series_assignment_is_revalidated_before_commit(self):
        target = database.upsert_video_season(
            series_name="Revalidate",
            season_name="第一季",
            season_number=1,
            provider="test",
            source_url="https://example.test/revalidate",
            relative_directory="Revalidate/第一季",
        )
        target_series_id = int(target["series"]["id"])
        series_root = self.mount_root / "Revalidate" / "Season02"
        self._video_at(series_root / "S02E01.mkv")
        discovery = preview_scan_source(
            str(series_root),
            scan_mode="collection",
        )
        scan = create_scan(
            str(series_root),
            scan_mode="collection",
            series_name="Revalidate External",
            groups=discovery["groups"],
            attach_to_series=True,
            target_series_id=target_series_id,
        )
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_series
                SET is_deleted = 1, deleted_at = 'now'
                WHERE id = ?
                """,
                (target_series_id,),
            )

        with self.assertRaises(ExternalScanError) as raised:
            self._process(scan["scan_id"])
        self.assertEqual(
            raised.exception.code,
            "target_series_deleted",
        )
        with database.connect() as connection:
            season_count = connection.execute(
                """
                SELECT COUNT(*) FROM video_seasons
                WHERE series_id = ? AND season_number = 2
                """,
                (target_series_id,),
            ).fetchone()[0]
            video_count = connection.execute(
                "SELECT COUNT(*) FROM videos"
            ).fetchone()[0]
        self.assertEqual(season_count, 0)
        self.assertEqual(video_count, 0)
        failed = get_scan(scan["scan_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "target_series_deleted")

    def test_batch_scan_can_attach_videos_to_existing_season(self):
        series, season = self._target_season([1])
        season_id = int(season["id"])
        self._video("S02E11.mkv")
        self._video("S02E12.mkv")
        scan = create_scan(
            str(self.source),
            scan_mode="batch",
            access_level=1,
            attach_to_season=True,
            target_season_id=season_id,
            created_by_username="admin",
        )
        self.assertEqual(scan["target_season_id"], season_id)
        self.assertEqual(
            [item["episode_number"] for item in scan["items"]],
            [11, 12],
        )

        reviewed = self._process(scan["scan_id"])
        self.assertEqual(reviewed["status"], "review")
        page = admin_client().get(
            "/manage/videos/external-scan/{}".format(scan["scan_id"])
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn("加入视频季：", page.text)
        self.assertEqual(
            page.text.count('class="external-scan-episode-input"'),
            2,
        )

        item_levels = {
            int(item["id"]): 1 for item in reviewed["items"]
        }
        committed = commit_scan(
            scan["scan_id"],
            item_access_levels=item_levels,
            selected_item_ids=list(item_levels),
        )
        self.assertEqual(committed["status"], "completed")
        self.assertEqual(committed["target_season_id"], season_id)

        with database.connect() as connection:
            episodes = connection.execute(
                """
                SELECT episode_number, video_id, origin_scan_id
                FROM video_episodes
                WHERE season_id = ?
                ORDER BY episode_number
                """,
                (season_id,),
            ).fetchall()
            series_count = connection.execute(
                "SELECT COUNT(*) FROM video_series"
            ).fetchone()[0]
            stored_season = connection.execute(
                "SELECT * FROM video_seasons WHERE id = ?",
                (season_id,),
            ).fetchone()
        self.assertEqual(
            [int(row["episode_number"]) for row in episodes],
            [1, 11, 12],
        )
        self.assertTrue(all(
            row["video_id"] is not None
            for row in episodes[1:]
        ))
        self.assertEqual(series_count, 1)
        self.assertEqual(stored_season["name"], "第二季")
        self.assertEqual(stored_season["series_id"], series["id"])
        self.assertEqual(
            int(stored_season["representative_video_id"]),
            int(episodes[1]["video_id"]),
        )

    def test_single_file_attached_to_season_uses_review_episode_number(self):
        _, season = self._target_season()
        season_id = int(season["id"])
        path = self._video("unclassified.mkv")
        scan = create_scan(
            str(path),
            scan_mode="file",
            access_level=0,
            attach_to_season=True,
            target_season_id=season_id,
        )
        self.assertEqual(scan["review_required"], 1)
        self.assertIsNone(scan["items"][0]["episode_number"])

        reviewed = self._process(scan["scan_id"])
        self.assertEqual(reviewed["status"], "review")
        item = reviewed["items"][0]
        committed = commit_scan(
            scan["scan_id"],
            item_access_levels={int(item["id"]): 0},
            selected_item_ids=[int(item["id"])],
            item_episode_numbers={int(item["id"]): 21},
        )
        self.assertEqual(committed["status"], "completed")
        with database.connect() as connection:
            episode = connection.execute(
                """
                SELECT episode_number, video_id
                FROM video_episodes
                WHERE season_id = ?
                """,
                (season_id,),
            ).fetchone()
        self.assertEqual(episode["episode_number"], 21)
        self.assertIsNotNone(episode["video_id"])

    def test_target_season_rejects_existing_episode_number(self):
        _, season = self._target_season([11])
        season_id = int(season["id"])
        self._video("S02E11.mkv")
        scan = create_scan(
            str(self.source),
            scan_mode="batch",
            attach_to_season=True,
            target_season_id=season_id,
        )
        reviewed = self._process(scan["scan_id"])
        item = reviewed["items"][0]

        with self.assertRaises(ExternalScanError) as raised:
            commit_scan(
                scan["scan_id"],
                item_access_levels={int(item["id"]): 0},
                selected_item_ids=[int(item["id"])],
            )
        self.assertEqual(
            raised.exception.code,
            "target_episode_conflict",
        )
        with database.connect() as connection:
            video_count = connection.execute(
                "SELECT COUNT(*) FROM videos"
            ).fetchone()[0]
        self.assertEqual(video_count, 0)

    def test_season_assignment_preview_is_admin_only(self):
        _, season = self._target_season([1, 2])
        season_id = int(season["id"])
        admin = admin_client()
        preview = admin.get(
            "/api/manage/seasons/{}/assignment-preview".format(season_id)
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["season"]["id"], season_id)
        self.assertEqual(preview.json()["series"]["name"], "Frieren")
        self.assertEqual(
            preview.json()["used_episode_numbers"],
            [1, 2],
        )
        self.assertEqual(
            user_client(username="season-target-user").get(
                "/api/manage/seasons/{}/assignment-preview".format(
                    season_id
                )
            ).status_code,
            403,
        )

    def test_init_db_migrates_legacy_scan_batches_before_creating_index(self):
        legacy_path = self.root / "legacy-scan.db"
        with sqlite3.connect(str(legacy_path)) as connection:
            connection.execute(
                """
                CREATE TABLE video_scan_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_volume_id INTEGER,
                    source_relative_path TEXT NOT NULL DEFAULT '',
                    scan_mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    stage TEXT NOT NULL DEFAULT 'queued',
                    review_required INTEGER NOT NULL DEFAULT 0,
                    grouping_mode TEXT NOT NULL DEFAULT 'single_season',
                    season_group_count INTEGER NOT NULL DEFAULT 0,
                    series_name TEXT NOT NULL DEFAULT '',
                    season_name TEXT NOT NULL DEFAULT '',
                    season_number INTEGER NOT NULL DEFAULT 1,
                    access_level INTEGER NOT NULL DEFAULT 0,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    ready_files INTEGER NOT NULL DEFAULT 0,
                    failed_files INTEGER NOT NULL DEFAULT 0,
                    imported_files INTEGER NOT NULL DEFAULT 0,
                    progress_percent REAL NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_detail TEXT,
                    created_by_user_id INTEGER,
                    created_by_username TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

        with patch.object(config, "DATABASE_PATH", legacy_path):
            database.init_db()
            with database.connect() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info('video_scan_batches')"
                    )
                }
                indexes = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA index_list('video_scan_batches')"
                    )
                }
        self.assertIn("target_series_id", columns)
        self.assertIn("target_season_id", columns)
        self.assertIn(
            "idx_video_scan_batches_target_series",
            indexes,
        )
        self.assertIn(
            "idx_video_scan_batches_target_season",
            indexes,
        )


if __name__ == "__main__":
    unittest.main()
