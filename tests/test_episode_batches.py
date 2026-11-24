import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client, user_client
from local_video_site.app import app
from local_video_site.web.routes.playback import _resolved_episode_title
from local_video_site.episodes.batches import EpisodeBatchError, backfill_episode_titles, confirm_batch, confirm_episode_discovery, discover_batch, normalize_season_name
from local_video_site.episodes.catalog import EpisodeCandidate, EpisodeCatalog, EpisodeTab, _browser_rows_to_episodes, _catalog_from_bilibili_payload, discover_episode_catalog, discover_episode_tabs
from local_video_site.episodes.naming import episode_display_title, season_number_from_label
from local_video_site.ingestion.library_scan import backfill_episode_technical_metadata


class EpisodeBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.download_dir = self.root / "download-hub"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
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
            patch.object(config, "DEFAULT_ROOTS", [self.download_dir]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _catalog(self):
        return EpisodeCatalog(
            provider="bilibili",
            detected_series_name="老友记",
            detected_season_name="第一季",
            source_url="https://www.bilibili.com/bangumi/play/ep468227",
            episodes=[
                EpisodeCandidate(
                    1,
                    "莫妮卡的新室友",
                    "https://www.bilibili.com/bangumi/play/ep468227",
                ),
                EpisodeCandidate(
                    2,
                    "超声波检查",
                    "https://www.bilibili.com/bangumi/play/ep468251",
                ),
                EpisodeCandidate(
                    3,
                    "第三集标题",
                    "https://www.bilibili.com/bangumi/play/ep468290",
                ),
            ],
        )

    def test_planned_episode_without_season_metadata_attaches_new_video(self):
        season = database.upsert_video_season(
            series_name="地球脉动",
            season_name="地球脉动 第三季",
            season_number=3,
            provider="bilibili",
            source_url="https://www.bilibili.com/bangumi/play/ss46178",
            relative_directory="地球脉动/地球脉动 第三季",
        )
        season_id = int(season["season"]["id"])
        path = (
            self.download_dir
            / "地球脉动"
            / "地球脉动 第三季"
            / "015.mp4"
        )
        path.parent.mkdir(parents=True)
        path.write_bytes(b"video")
        database.create_video_episode(
            season_id=season_id,
            episode_number=15,
            display_title="地球脉动 地球脉动 第三季 015",
            source_url="https://www.bilibili.com/bangumi/play/ep790813",
            normalized_source_url=(
                "https://www.bilibili.com/bangumi/play/ep790813"
            ),
            relative_path="地球脉动/地球脉动 第三季/015.mp4",
            absolute_path=str(path),
            download_task_id=None,
            episode_title="第十五集",
        )

        status = database.upsert_video(
            path,
            "015",
            "download",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )

        self.assertIn(status, {"added", "changed"})
        video = database.get_video_by_path(path)
        context = database.season_context_for_video(video["id"])
        self.assertEqual(context["representative_video_id"], video["id"])
        self.assertEqual(context["metadata_video_id"], video["id"])
        self.assertEqual(context["episodes"][0]["video_id"], video["id"])

    def _continuous_tabs(self, include_failed=False):
        first = EpisodeCatalog(
            provider="bilibili",
            detected_series_name="老友记",
            detected_season_name="第一季",
            source_url="https://www.bilibili.com/bangumi/play/ss1001",
            episodes=[
                EpisodeCandidate(
                    1,
                    "第一季第一集",
                    "https://www.bilibili.com/bangumi/play/ep1001",
                    source_tab_item_title="中配版1",
                ),
                EpisodeCandidate(
                    2,
                    "第一季第二集",
                    "https://www.bilibili.com/bangumi/play/ep1002",
                    source_tab_item_title="中配版2",
                ),
            ],
        )
        second = EpisodeCatalog(
            provider="bilibili",
            detected_series_name="老友记",
            detected_season_name="第二季",
            source_url="https://www.bilibili.com/bangumi/play/ss1002",
            episodes=[
                EpisodeCandidate(
                    1,
                    "第二季第一集",
                    "https://www.bilibili.com/bangumi/play/ep2001",
                    source_tab_item_title="中配版1",
                ),
                EpisodeCandidate(
                    2,
                    "第二季第二集",
                    "https://www.bilibili.com/bangumi/play/ep2002",
                    source_tab_item_title="中配版2",
                ),
            ],
        )
        tabs = [
            EpisodeTab(
                tab_sequence=1,
                tab_key="ss1001",
                tab_title="第一季",
                season_name="第一季",
                season_number=1,
                source_url=first.source_url,
                catalog=first,
            ),
            EpisodeTab(
                tab_sequence=2,
                tab_key="ss1002",
                tab_title="第二季",
                season_name="第二季",
                season_number=2,
                source_url=second.source_url,
                catalog=second,
            ),
        ]
        if include_failed:
            tabs.append(EpisodeTab(
                tab_sequence=3,
                tab_key="ss1003",
                tab_title="第三季",
                season_name="第三季",
                season_number=3,
                source_url="https://www.bilibili.com/bangumi/play/ss1003",
                warning="接口暂时不可用",
            ))
        return tabs

    def _discover(self, access_level=0):
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            return discover_batch(
                series_name="老友记",
                season_name="第一季",
                season_number=1,
                first_episode_url=(
                    "https://www.bilibili.com/bangumi/play/ep468227"
                ),
                output_root=str(self.download_dir),
                quality="site",
                access_level=access_level,
            )

    def test_discovery_builds_safe_episode_preview_and_paths(self):
        batch = self._discover()

        self.assertEqual(batch["status"], "preview")
        self.assertEqual(batch["item_count"], 3)
        self.assertEqual(
            [item["relative_path"] for item in batch["items"]],
            [
                "老友记/第一季/001.mp4",
                "老友记/第一季/002.mp4",
                "老友记/第一季/003.mp4",
            ],
        )
        self.assertTrue(all(item["selected"] for item in batch["items"]))
        self.assertEqual(
            batch["items"][0]["episode_title"],
            "莫妮卡的新室友",
        )
        self.assertNotIn("ep468252", str(batch))

    def test_confirm_persists_only_selected_episode_items(self):
        batch = self._discover()
        selected_ids = [
            batch["items"][0]["id"],
            batch["items"][2]["id"],
        ]

        confirm_batch(
            batch["batch_id"],
            selected_ids,
            batch["revision"],
        )
        confirmed = database.get_episode_download_batch(batch["batch_id"])

        self.assertEqual(
            [item["selected"] for item in confirmed["items"]],
            [1, 0, 1],
        )
        database.reconcile_episode_batch_library()
        with database.connect() as connection:
            episodes = connection.execute(
                """
                SELECT episode_number
                FROM video_episodes
                ORDER BY episode_number
                """
            ).fetchall()
        self.assertEqual(
            [int(item["episode_number"]) for item in episodes],
            [1, 3],
        )

    def test_historical_unselected_planning_episodes_are_removed(self):
        batch = self._discover()
        confirm_batch(
            batch["batch_id"],
            [
                batch["items"][0]["id"],
                batch["items"][2]["id"],
            ],
            batch["revision"],
        )
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE episode_download_items
                SET selected = 1
                WHERE batch_id = ?
                """,
                (batch["batch_id"],),
            )
        database.reconcile_episode_batch_library()

        repair = database.repair_confirmed_episode_batch_selections()
        confirmed = database.get_episode_download_batch(batch["batch_id"])
        self.assertEqual(repair["episodes_removed"], 1)
        self.assertEqual(
            [item["selected"] for item in confirmed["items"]],
            [1, 0, 1],
        )
        with database.connect() as connection:
            episodes = connection.execute(
                """
                SELECT episode_number, status
                FROM video_episodes
                ORDER BY episode_number
                """
            ).fetchall()
        self.assertEqual(
            [
                (int(item["episode_number"]), item["status"])
                for item in episodes
            ],
            [(1, "planned"), (3, "planned")],
        )

    def test_reconcile_skips_episode_url_owned_by_another_season(self):
        batch = self._discover()
        confirm_batch(
            batch["batch_id"],
            [batch["items"][0]["id"]],
            batch["revision"],
        )
        database.reconcile_episode_batch_library()
        with database.connect() as connection:
            original_owner = connection.execute(
                """
                SELECT season_id
                FROM video_episodes
                WHERE normalized_source_url = ?
                """,
                (batch["items"][0]["normalized_source_url"],),
            ).fetchone()["season_id"]

        database.upsert_video_season(
            series_name="冲突节目",
            season_name="第一季",
            season_number=1,
            provider="bilibili",
            source_url=batch["items"][0]["source_url"],
            relative_directory="冲突节目/第一季",
        )
        conflicting_batch_id = "episode-conflict-test"
        database.create_episode_download_batch(
            batch_id=conflicting_batch_id,
            series_name="冲突节目",
            season_name="第一季",
            season_number=1,
            first_episode_url=batch["items"][0]["source_url"],
            provider="bilibili",
            output_root=str(self.download_dir),
            quality="auto",
            items=[{
                "episode_number": 1,
                "source_url": batch["items"][0]["source_url"],
                "normalized_source_url": batch["items"][0][
                    "normalized_source_url"
                ],
                "display_title": "冲突节目 第一季 001",
                "episode_title": "重复条目",
                "file_name": "001.mp4",
                "relative_path": "冲突节目/第一季/001.mp4",
                "absolute_path": str(
                    self.download_dir / "冲突节目/第一季/001.mp4"
                ),
                "selected": True,
                "validation_status": "ready",
            }],
        )
        database.update_episode_download_batch(
            conflicting_batch_id,
            status="confirmed",
        )

        result = database.reconcile_episode_batch_library()

        self.assertEqual(result["source_conflicts"], 1)
        with database.connect() as connection:
            owners = connection.execute(
                """
                SELECT season_id
                FROM video_episodes
                WHERE normalized_source_url = ?
                """,
                (batch["items"][0]["normalized_source_url"],),
            ).fetchall()
        self.assertEqual(len(owners), 1)
        self.assertEqual(
            int(owners[0]["season_id"]),
            int(original_owner),
        )

    def test_episode_batch_4k_quality_is_inherited_by_tasks(self):
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            batch = discover_batch(
                "老友记",
                "第一季",
                1,
                "https://www.bilibili.com/bangumi/play/ep468227",
                str(self.download_dir),
                quality="2160p",
            )
        report = confirm_batch(
            batch["batch_id"],
            [batch["items"][0]["id"]],
            batch["revision"],
        )
        task = database.get_download_task(report["task_ids"][0])
        snapshot = json.loads(task["media_policy_snapshot_json"])

        self.assertEqual(batch["quality"], "2160p")
        self.assertEqual(task["requested_quality"], "2160p")
        self.assertEqual(snapshot["quality_target"], "2160p")
        self.assertEqual(snapshot["max_height"], 2160)

    def test_confirm_creates_shared_batch_tasks_with_episode_metadata(self):
        batch = self._discover(access_level=1)
        report = confirm_batch(
            batch_id=batch["batch_id"],
            selected_item_ids=[item["id"] for item in batch["items"]],
            revision=batch["revision"],
        )

        self.assertEqual(report["selected"], 3)
        self.assertEqual(report["created"], 3)
        self.assertEqual(batch["access_level"], 1)
        tasks = database.list_download_tasks(page_size=10)["items"]
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(item["access_level"] == 1 for item in tasks))
        self.assertEqual({item["batch_id"] for item in tasks}, {batch["batch_id"]})
        self.assertEqual(
            {item["output_dir"] for item in tasks},
            {str((self.download_dir / "老友记" / "第一季").resolve())},
        )
        first = next(item for item in tasks if item["episode_id"] is not None
                     and item["output_stem"] == "001")
        second = next(item for item in tasks if item["output_stem"] == "002")
        self.assertEqual(first["display_title"], "老友记 第一季 001")
        self.assertGreater(first["priority"], second["priority"])

        repeated = confirm_batch(
            batch_id=batch["batch_id"],
            selected_item_ids=[],
            revision=batch["revision"],
        )
        self.assertEqual(repeated["task_ids"], report["task_ids"])
        self.assertEqual(
            len(database.list_download_tasks(page_size=10)["items"]),
            3,
        )

    def test_first_episode_is_metadata_source_and_children_are_inherited(self):
        batch = self._discover()
        confirm_batch(
            batch["batch_id"],
            [item["id"] for item in batch["items"]],
            batch["revision"],
        )
        season_dir = self.download_dir / "老友记" / "第一季"
        first_path = season_dir / "001.mp4"
        second_path = season_dir / "002.mp4"
        season_dir.mkdir(parents=True, exist_ok=True)
        first_path.write_bytes(b"first")
        second_path.write_bytes(b"second")

        database.upsert_video(
            first_path,
            "001",
            "download-hub",
            first_path.stat().st_size,
            first_path.stat().st_mtime_ns,
        )
        database.upsert_video(
            second_path,
            "002",
            "download-hub",
            second_path.stat().st_size,
            second_path.stat().st_mtime_ns,
        )
        first = database.get_video_by_path(first_path)
        second = database.get_video_by_path(second_path)

        thumbnail_path = self.data_dir / "thumbnails" / "episode-002.jpg"
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        thumbnail_path.write_bytes(b"jpeg")
        with (
            patch(
                "local_video_site.ingestion.library_scan.probe_video",
                return_value={
                    "duration": 123.5,
                    "width": 1920,
                    "height": 1080,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
            ),
            patch(
                "local_video_site.ingestion.library_scan.generate_thumbnail",
                return_value=thumbnail_path,
            ),
        ):
            self.assertEqual(backfill_episode_technical_metadata(), 1)
        second = database.get_video_by_path(second_path)

        self.assertEqual(first["metadata_status"], "pending")
        self.assertEqual(second["metadata_status"], "inherited")
        self.assertEqual(second["preview_status"], "not_required")
        self.assertEqual(second["duration"], 123.5)
        self.assertEqual(second["width"], 1920)
        self.assertEqual(second["height"], 1080)
        self.assertEqual(second["video_codec"], "h264")
        self.assertEqual(second["audio_codec"], "aac")
        self.assertEqual(second["thumbnail_path"], str(thumbnail_path))
        context = database.season_context_for_video(first["id"])
        self.assertEqual(len(context["episodes"]), 3)
        self.assertEqual(context["metadata_video_id"], first["id"])
        self.assertEqual(
            context["episodes"][0]["episode_title"],
            "莫妮卡的新室友",
        )
        cards = database.list_library_cards(page_size=10)
        season_cards = [
            item for item in cards["items"] if item["card_kind"] == "season"
        ]
        self.assertEqual(len(season_cards), 1)
        self.assertEqual(season_cards[0]["id"], first["id"])
        self.assertEqual(season_cards[0]["episode_count"], 3)

        page = admin_client().get("/watch/{}".format(first["id"]))
        self.assertEqual(page.status_code, 200)
        self.assertIn("season-episode-grid", page.text)
        self.assertIn("老友记 · 第一季", page.text)
        self.assertIn("第 1 集", page.text)
        self.assertIn("第 2 集 ·", page.text)
        self.assertNotIn("第 4 集", page.text)
        self.assertIn("<span>莫妮卡的新室友</span>", page.text)
        self.assertIn("<strong>02</strong>", page.text)
        self.assertIn("<span>超声波检查</span>", page.text)
        second_page = admin_client().get(
            "/watch/{}".format(second["id"])
        )
        self.assertEqual(second_page.status_code, 200)
        self.assertIn(
            'poster="/thumb/{}"'.format(second["id"]),
            second_page.text,
        )

    def test_batch_page_and_api_are_available(self):
        client = admin_client()
        page = client.get("/downloads/batch")
        downloads = client.get("/downloads")
        self.assertEqual(page.status_code, 200)
        self.assertIn("解析并预览", page.text)
        self.assertIn("<th>Tab 内名称</th>", page.text)
        self.assertIn("<th>原集数</th>", page.text)
        self.assertIn('id="episode-access-level"', page.text)
        self.assertIn('href="/downloads/batch"', downloads.text)
        self.assertIn(
            '<option value="2160p">4K 优先（最高 2160p）</option>',
            page.text,
        )
        self.assertIn('id="episode-continuous-parse"', page.text)
        self.assertIn(
            'id="episode-aggregate-single-season"',
            page.text,
        )
        self.assertIn("汇总成单季", page.text)
        self.assertIn('id="episode-preserve-original-name"', page.text)
        self.assertIn("保留原始名称", page.text)
        self.assertIn('id="episode-allow-source-mismatch"', page.text)
        self.assertIn("允许来源与目标视频集不同", page.text)

        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            response = client.post(
                "/api/episode-batches/discover",
                json={
                    "series_name": "老友记",
                    "season_name": "第一季",
                    "season_number": 1,
                    "first_episode_url": (
                        "https://www.bilibili.com/bangumi/play/ep468227"
                    ),
                    "output_root": str(self.download_dir),
                    "quality": "site",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item_count"], 3)

    def test_batch_rejects_unconfirmed_source_series_mismatch(self):
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            with self.assertRaises(EpisodeBatchError) as raised:
                discover_batch(
                    "绿色星球",
                    "第二季",
                    2,
                    "https://www.bilibili.com/bangumi/play/ep701353",
                    str(self.download_dir),
                )
        self.assertEqual(raised.exception.code, "source_series_mismatch")

        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            preview = discover_batch(
                "绿色星球",
                "第二季",
                2,
                "https://www.bilibili.com/bangumi/play/ep701353",
                str(self.download_dir),
                allow_source_series_mismatch=True,
            )
        self.assertEqual(preview["series_name"], "绿色星球")

        user = user_client(username="batch-source-user")
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            forbidden = user.post(
                "/api/episode-batches/discover",
                json={
                    "series_name": "绿色星球",
                    "season_name": "第二季",
                    "season_number": 2,
                    "first_episode_url": (
                        "https://www.bilibili.com/bangumi/play/ep701353"
                    ),
                    "output_root": str(self.download_dir),
                    "allow_source_series_mismatch": True,
                },
            )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

    def test_existing_task_in_another_season_is_not_reusable(self):
        first_url = "https://www.bilibili.com/bangumi/play/ep468227"
        other_dir = self.download_dir / "其他视频集" / "第一季"
        other_dir.mkdir(parents=True)
        task_id = database.create_download_task(
            batch_id="other-batch",
            url=first_url,
            normalized_url=first_url,
            metadata_url=first_url,
            normalized_metadata_url=first_url,
            output_dir=str(other_dir),
            max_attempts=3,
        )

        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            preview = discover_batch(
                "老友记",
                "第一季",
                1,
                first_url,
                str(self.download_dir),
            )

        first_item = next(
            item
            for item in preview["items"]
            if item["source_url"] == first_url
        )
        self.assertEqual(
            first_item["validation_status"],
            "existing_task_elsewhere",
        )
        self.assertIn("#{}".format(task_id), first_item["validation_message"])
        self.assertFalse(first_item["selected"])

    def test_continuous_discovery_groups_tabs_into_separate_seasons(self):
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=self._continuous_tabs(),
        ):
            discovery = discover_batch(
                "老友记",
                "第一季",
                1,
                "https://www.bilibili.com/bangumi/play/ss1001",
                str(self.download_dir),
                continuous_parse=True,
            )

        self.assertEqual(discovery["mode"], "continuous_grouped")
        self.assertEqual(discovery["tab_count"], 2)
        self.assertEqual(discovery["item_count"], 4)
        self.assertTrue(discovery["complete"])
        self.assertEqual(len({
            group["batch_id"] for group in discovery["groups"]
        }), 2)
        self.assertEqual(
            [item["relative_path"] for item in discovery["items"]],
            [
                "老友记/第一季/001.mp4",
                "老友记/第一季/002.mp4",
                "老友记/第二季/001.mp4",
                "老友记/第二季/002.mp4",
            ],
        )

        report = confirm_episode_discovery(
            discovery["discovery_id"],
            [item["id"] for item in discovery["items"]],
            discovery["revision"],
        )
        self.assertEqual(report["created"], 4)
        tasks = database.list_download_tasks(page_size=10)["items"]
        self.assertEqual(len(tasks), 4)
        self.assertEqual(
            {task["batch_id"] for task in tasks},
            {discovery["discovery_id"]},
        )
        self.assertEqual(
            {task["output_dir"] for task in tasks},
            {
                str((self.download_dir / "老友记" / "第一季").resolve()),
                str((self.download_dir / "老友记" / "第二季").resolve()),
            },
        )

    def test_continuous_discovery_can_flatten_all_tabs_into_one_season(self):
        tabs = self._continuous_tabs()
        duplicate = EpisodeCandidate(
            3,
            "重复视频",
            "https://www.bilibili.com/bangumi/play/ep2002",
        )
        duplicate_catalog = EpisodeCatalog(
            provider="bilibili",
            detected_series_name="老友记",
            detected_season_name="第二季",
            source_url="https://www.bilibili.com/bangumi/play/ss1002",
            episodes=[duplicate],
        )
        tabs.append(EpisodeTab(
            tab_sequence=3,
            tab_key="ss1003",
            tab_title="特别篇",
            season_name="特别篇",
            season_number=3,
            source_url="https://www.bilibili.com/bangumi/play/ss1003",
            catalog=duplicate_catalog,
        ))
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=tabs,
        ):
            discovery = discover_batch(
                "老友记",
                "合集",
                1,
                "https://www.bilibili.com/bangumi/play/ss1001",
                str(self.download_dir),
                continuous_parse=True,
                aggregate_single_season=True,
            )

        self.assertEqual(discovery["mode"], "continuous_aggregate")
        self.assertEqual(discovery["target_season_name"], "合集")
        self.assertEqual(discovery["item_count"], 4)
        self.assertIn("重复视频已去重", discovery["warning"])
        self.assertEqual(
            [item["episode_number"] for item in discovery["items"]],
            [1, 2, 3, 4],
        )
        self.assertEqual(
            [item["source_episode_number"] for item in discovery["items"]],
            [1, 2, 1, 2],
        )
        self.assertEqual(
            [item["source_season_name"] for item in discovery["items"]],
            ["第一季", "第一季", "第二季", "第二季"],
        )
        self.assertEqual(
            [item["relative_path"] for item in discovery["items"]],
            [
                "老友记/合集/001.mp4",
                "老友记/合集/002.mp4",
                "老友记/合集/003.mp4",
                "老友记/合集/004.mp4",
            ],
        )
        self.assertEqual(len({
            group["batch_id"] for group in discovery["groups"]
        }), 1)

        report = confirm_episode_discovery(
            discovery["discovery_id"],
            [item["id"] for item in discovery["items"]],
            discovery["revision"],
        )
        self.assertEqual(report["created"], 4)
        tasks = database.list_download_tasks(page_size=10)["items"]
        self.assertEqual(
            {task["output_dir"] for task in tasks},
            {str((self.download_dir / "老友记" / "合集").resolve())},
        )
        with database.connect() as connection:
            season = connection.execute(
                """
                SELECT s.name, COUNT(e.id) AS episode_count
                FROM video_seasons s
                JOIN video_episodes e ON e.season_id = s.id
                GROUP BY s.id
                """
            ).fetchone()
        self.assertEqual(
            dict(season),
            {"name": "合集", "episode_count": 4},
        )

    def test_aggregate_requires_continuous_discovery(self):
        with self.assertRaisesRegex(ValueError, "连续解析"):
            discover_batch(
                "老友记",
                "合集",
                1,
                "https://www.bilibili.com/bangumi/play/ss1001",
                str(self.download_dir),
                aggregate_single_season=True,
            )

    def test_aggregate_can_preserve_meaningful_original_names(self):
        tabs = [
            EpisodeTab(
                1,
                "ss2001",
                "哈利·波特与魔法石",
                "哈利·波特与魔法石",
                1,
                "https://www.bilibili.com/bangumi/play/ss2001",
                EpisodeCatalog(
                    provider="bilibili",
                    detected_series_name="哈利·波特",
                    detected_season_name="哈利·波特与魔法石",
                    source_url=(
                        "https://www.bilibili.com/bangumi/play/ss2001"
                    ),
                    episodes=[
                        EpisodeCandidate(
                            1,
                            "正片",
                            "https://www.bilibili.com/bangumi/play/ep2001",
                        ),
                    ],
                ),
            ),
            EpisodeTab(
                2,
                "ss2002",
                "哈利·波特与密室",
                "哈利·波特与密室",
                2,
                "https://www.bilibili.com/bangumi/play/ss2002",
                EpisodeCatalog(
                    provider="bilibili",
                    detected_series_name="哈利·波特",
                    detected_season_name="哈利·波特与密室",
                    source_url=(
                        "https://www.bilibili.com/bangumi/play/ss2002"
                    ),
                    episodes=[
                        EpisodeCandidate(
                            1,
                            "正片",
                            "https://www.bilibili.com/bangumi/play/ep2002",
                        ),
                    ],
                ),
            ),
        ]
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=tabs,
        ):
            discovery = discover_batch(
                "哈利·波特",
                "合集",
                1,
                "https://www.bilibili.com/bangumi/play/ss2001",
                str(self.download_dir),
                continuous_parse=True,
                aggregate_single_season=True,
                preserve_original_name=True,
            )

        self.assertEqual(discovery["file_name_mode"], "original")
        self.assertEqual(discovery["preserve_original_name"], 1)
        self.assertEqual(
            [item["file_name"] for item in discovery["items"]],
            [
                "哈利·波特与魔法石.mp4",
                "哈利·波特与密室.mp4",
            ],
        )
        self.assertEqual(
            [item["episode_number"] for item in discovery["items"]],
            [1, 2],
        )
        self.assertTrue(all(
            item["file_name_mode"] == "original"
            for item in discovery["items"]
        ))

        confirm_episode_discovery(
            discovery["discovery_id"],
            [item["id"] for item in discovery["items"]],
            discovery["revision"],
        )
        tasks = database.list_download_tasks(page_size=10)["items"]
        self.assertEqual(
            {task["output_stem"] for task in tasks},
            {"哈利·波特与魔法石", "哈利·波特与密室"},
        )

    def test_original_name_falls_back_and_resolves_duplicates(self):
        generic_tab = EpisodeTab(
            1,
            "ss3001",
            "第一季",
            "第一季",
            1,
            "https://www.bilibili.com/bangumi/play/ss3001",
            EpisodeCatalog(
                provider="bilibili",
                detected_series_name="测试剧",
                detected_season_name="第一季",
                source_url=(
                    "https://www.bilibili.com/bangumi/play/ss3001"
                ),
                episodes=[
                    EpisodeCandidate(
                        1,
                        "正片",
                        "https://www.bilibili.com/bangumi/play/ep3001",
                    ),
                    EpisodeCandidate(
                        2,
                        "第2集",
                        "https://www.bilibili.com/bangumi/play/ep3002",
                    ),
                ],
            ),
        )

        def single_tab(sequence, ep_id):
            return EpisodeTab(
                sequence,
                "ss{}".format(ep_id),
                "特别篇",
                "特别篇",
                sequence,
                "https://www.bilibili.com/bangumi/play/ss{}".format(ep_id),
                EpisodeCatalog(
                    provider="bilibili",
                    detected_series_name="测试剧",
                    detected_season_name="特别篇",
                    source_url=(
                        "https://www.bilibili.com/bangumi/play/ss{}"
                        .format(ep_id)
                    ),
                    episodes=[
                        EpisodeCandidate(
                            1,
                            "正片",
                            "https://www.bilibili.com/bangumi/play/ep{}"
                            .format(ep_id),
                        ),
                    ],
                ),
            )

        tabs = [
            generic_tab,
            single_tab(2, 3101),
            single_tab(3, 3102),
            EpisodeTab(
                4,
                "ss3103",
                "神奇动物：格林德沃之罪",
                "神奇动物：格林德沃之罪",
                4,
                "https://www.bilibili.com/bangumi/play/ss3103",
                EpisodeCatalog(
                    provider="bilibili",
                    detected_series_name="测试剧",
                    detected_season_name="神奇动物：格林德沃之罪",
                    source_url=(
                        "https://www.bilibili.com/bangumi/play/ss3103"
                    ),
                    episodes=[
                        EpisodeCandidate(
                            1,
                            "原版",
                            "https://www.bilibili.com/bangumi/play/ep3103",
                        ),
                        EpisodeCandidate(
                            2,
                            "中文版",
                            "https://www.bilibili.com/bangumi/play/ep3104",
                        ),
                    ],
                ),
            ),
        ]
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=tabs,
        ):
            discovery = discover_batch(
                "测试剧",
                "合集",
                1,
                "https://www.bilibili.com/bangumi/play/ss3001",
                str(self.download_dir),
                continuous_parse=True,
                aggregate_single_season=True,
                preserve_original_name=True,
            )

        self.assertEqual(
            [item["file_name"] for item in discovery["items"]],
            [
                "第一季 - 第001集.mp4",
                "第一季 - 第002集.mp4",
                "特别篇.mp4",
                "特别篇 (2).mp4",
                "神奇动物 格林德沃之罪 - 原版.mp4",
                "神奇动物 格林德沃之罪 - 中文版.mp4",
            ],
        )
        self.assertEqual(
            [item["episode_number"] for item in discovery["items"]],
            [1, 2, 3, 4, 5, 6],
        )

    def test_original_name_removes_unsafe_filename_characters(self):
        tab = EpisodeTab(
            1,
            "ss4001",
            "A/B:C?",
            "A/B:C?",
            1,
            "https://www.bilibili.com/bangumi/play/ss4001",
            EpisodeCatalog(
                provider="bilibili",
                detected_series_name="测试剧",
                detected_season_name="A/B:C?",
                source_url=(
                    "https://www.bilibili.com/bangumi/play/ss4001"
                ),
                episodes=[
                    EpisodeCandidate(
                        1,
                        "正片",
                        "https://www.bilibili.com/bangumi/play/ep4001",
                    ),
                ],
            ),
        )
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=[tab],
        ):
            discovery = discover_batch(
                "测试剧",
                "合集",
                1,
                "https://www.bilibili.com/bangumi/play/ss4001",
                str(self.download_dir),
                continuous_parse=True,
                aggregate_single_season=True,
                preserve_original_name=True,
            )

        self.assertEqual(
            discovery["items"][0]["file_name"],
            "A／B C.mp4",
        )
        self.assertNotIn("/", discovery["items"][0]["file_name"])

    def test_original_name_requires_aggregate_mode(self):
        response = admin_client().post(
            "/api/episode-batches/discover",
            json={
                "series_name": "老友记",
                "season_name": "合集",
                "season_number": 1,
                "first_episode_url": (
                    "https://www.bilibili.com/bangumi/play/ss1001"
                ),
                "output_root": str(self.download_dir),
                "preserve_original_name": True,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("汇总成单季", response.json()["detail"])

    def test_aggregate_marks_videos_already_in_another_season(self):
        tabs = self._continuous_tabs()
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=tabs,
        ):
            first = discover_batch(
                "老友记",
                "合集一",
                1,
                "https://www.bilibili.com/bangumi/play/ss1001",
                str(self.download_dir),
                continuous_parse=True,
                aggregate_single_season=True,
            )
            confirm_episode_discovery(
                first["discovery_id"],
                [item["id"] for item in first["items"]],
                first["revision"],
            )
            second = discover_batch(
                "老友记",
                "合集二",
                2,
                "https://www.bilibili.com/bangumi/play/ss1001",
                str(self.download_dir),
                continuous_parse=True,
                aggregate_single_season=True,
            )

        self.assertTrue(second["items"])
        self.assertTrue(all(
            item["validation_status"] == "existing_episode"
            for item in second["items"]
        ))
        self.assertTrue(all(not item["selected"] for item in second["items"]))

    def test_discovery_tables_use_application_level_relationships(self):
        with database.connect() as connection:
            discovery_fks = connection.execute(
                "PRAGMA foreign_key_list('episode_download_discoveries')"
            ).fetchall()
            tab_fks = connection.execute(
                "PRAGMA foreign_key_list("
                "'episode_download_discovery_tabs')"
            ).fetchall()
            item_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info('episode_download_items')"
                )
            }
            batch_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info('episode_download_batches')"
                )
            }
            discovery_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info('episode_download_discoveries')"
                )
            }
        self.assertEqual(discovery_fks, [])
        self.assertEqual(tab_fks, [])
        self.assertTrue({
            "source_tab_key",
            "source_tab_title",
            "source_tab_item_title",
            "source_season_name",
            "source_episode_number",
        }.issubset(item_columns))
        self.assertIn("file_name_mode", batch_columns)
        self.assertTrue({
            "preserve_original_name",
            "file_name_mode",
        }.issubset(discovery_columns))

    def test_continuous_discovery_reports_partial_tab_failure(self):
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=self._continuous_tabs(include_failed=True),
        ):
            discovery = discover_batch(
                "老友记",
                "第一季",
                1,
                "https://www.bilibili.com/bangumi/play/ss1001",
                str(self.download_dir),
                continuous_parse=True,
            )

        self.assertFalse(discovery["complete"])
        self.assertEqual(discovery["item_count"], 4)
        self.assertEqual(discovery["groups"][-1]["status"], "failed")
        self.assertIn("接口暂时不可用", discovery["warning"])

    def test_continuous_discovery_api_returns_aggregate_preview(self):
        client = admin_client()
        with patch(
            "local_video_site.episodes.batches.discover_episode_tabs",
            return_value=self._continuous_tabs(),
        ):
            response = client.post(
                "/api/episode-batches/discover",
                json={
                    "series_name": "老友记",
                    "season_name": "合集",
                    "season_number": 1,
                    "first_episode_url": (
                        "https://www.bilibili.com/bangumi/play/ss1001"
                    ),
                    "output_root": str(self.download_dir),
                    "quality": "site",
                    "continuous_parse": True,
                    "aggregate_single_season": True,
                    "preserve_original_name": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["mode"], "continuous_aggregate")
        self.assertEqual(payload["file_name_mode"], "original")
        self.assertEqual(payload["item_count"], 4)
        self.assertEqual(
            [item["source_tab_title"] for item in payload["items"]],
            ["第一季", "第一季", "第二季", "第二季"],
        )
        self.assertEqual(
            [item["source_tab_item_title"] for item in payload["items"]],
            ["中配版1", "中配版2", "中配版1", "中配版2"],
        )
        self.assertEqual(
            [group["tab_title"] for group in payload["groups"]],
            ["第一季", "第二季"],
        )
        self.assertEqual(
            payload["items"][-1]["relative_path"],
            "老友记/合集/第二季第二集.mp4",
        )
        confirmed = client.post(
            f"/api/episode-discoveries/{payload['discovery_id']}/confirm",
            json={
                "revision": payload["revision"],
                "selected_item_ids": [
                    item["id"] for item in payload["items"]
                ],
            },
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["created"], 4)
        self.assertEqual(
            confirmed.json()["status"],
            "confirmed",
        )

    def test_aggregate_api_rejects_mode_without_continuous_parse(self):
        response = admin_client().post(
            "/api/episode-batches/discover",
            json={
                "series_name": "老友记",
                "season_name": "合集",
                "season_number": 1,
                "first_episode_url": (
                    "https://www.bilibili.com/bangumi/play/ss1001"
                ),
                "output_root": str(self.download_dir),
                "aggregate_single_season": True,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("连续解析", response.json()["detail"])

    def test_missing_episode_titles_can_be_backfilled(self):
        batch = self._discover()
        confirm_batch(
            batch["batch_id"],
            [item["id"] for item in batch["items"]],
            batch["revision"],
        )
        database.reconcile_episode_batch_library()
        with database.connect() as connection:
            connection.execute(
                "UPDATE video_episodes SET episode_title = NULL"
            )
            connection.execute(
                "UPDATE episode_download_items SET episode_title = NULL"
            )

        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            report = backfill_episode_titles(provider="bilibili")

        self.assertEqual(report["updated"], 3)
        with database.connect() as connection:
            context = {
                "episodes": [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT episode_number, episode_title
                        FROM video_episodes
                        ORDER BY episode_number
                        """
                    ).fetchall()
                ]
            }
        self.assertEqual(
            context["episodes"][0]["episode_title"],
            "莫妮卡的新室友",
        )

    def test_generic_episode_title_is_repaired_from_real_file_name(self):
        batch = self._discover()
        confirm_batch(
            batch["batch_id"],
            [item["id"] for item in batch["items"]],
            batch["revision"],
        )
        database.reconcile_episode_batch_library()
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_episodes
                SET episode_title = '正片',
                    relative_path =
                        '老友记/第一季/莫妮卡的新室友.mp4'
                WHERE episode_number = 1
                """
            )

        self.assertEqual(
            database.repair_generic_episode_titles_from_paths(),
            1,
        )
        with database.connect() as connection:
            title = connection.execute(
                """
                SELECT episode_title
                FROM video_episodes
                WHERE episode_number = 1
                """
            ).fetchone()["episode_title"]
        self.assertEqual(title, "莫妮卡的新室友")

    def test_bilibili_payload_uses_returned_episode_urls(self):
        catalog = _catalog_from_bilibili_payload(
            {
                "result": {
                    "title": "老友记",
                    "season_title": "第一季",
                    "episodes": [
                        {"ep_id": 468227, "title": "1", "long_title": "第一集"},
                        {"ep_id": 468251, "title": "2", "long_title": "第二集"},
                        {"ep_id": 468290, "title": "3", "long_title": "第三集"},
                    ],
                }
            },
            "https://www.bilibili.com/bangumi/play/ep468227",
        )

        self.assertEqual(
            [item.source_url.rsplit("/", 1)[-1] for item in catalog.episodes],
            ["ep468227", "ep468251", "ep468290"],
        )
        self.assertEqual(
            catalog.selection_mode,
            "bilibili_legacy_all",
        )

    def test_bilibili_payload_keeps_only_main_tv_episodes(self):
        catalog = _catalog_from_bilibili_payload(
            {
                "code": 0,
                "result": {
                    "title": "葬送的芙莉莲",
                    "season_title": "葬送的芙莉莲",
                    "episodes": [
                        {
                            "ep_id": 779775,
                            "title": "1",
                            "long_title": "冒险的结束",
                            "section_type": 0,
                        },
                        {
                            "ep_id": 791080,
                            "title": "8",
                            "long_title": "葬送的芙莉莲",
                            "section_type": 1,
                            "badge": "预告",
                        },
                        {
                            "ep_id": 779776,
                            "title": "2",
                            "long_title": "也不一定要是魔法…",
                            "section_type": 0,
                        },
                    ],
                    "section": [
                        {
                            "title": "预告",
                            "episodes": [{"ep_id": 791080}],
                        }
                    ],
                },
            },
            "https://www.bilibili.com/bangumi/play/ss46089",
        )

        self.assertEqual(
            [item.source_url.rsplit("/", 1)[-1] for item in catalog.episodes],
            ["ep779775", "ep779776"],
        )
        self.assertEqual(catalog.selection_mode, "bilibili_section_type")
        self.assertEqual(catalog.accepted_count, 2)
        self.assertEqual(catalog.excluded_count, 1)
        self.assertIn("排除 1 个", catalog.warning)

    def test_bilibili_tab_item_title_preserves_dub_variant_name(self):
        catalog = _catalog_from_bilibili_payload(
            {
                "code": 0,
                "result": {
                    "title": "地球脉动 第二季",
                    "season_title": "地球脉动 第二季",
                    "season_id": 43768,
                    "episodes": [
                        {
                            "ep_id": 773586,
                            "title": "中配版1",
                            "long_title": "岛屿",
                            "section_type": 0,
                        },
                        {
                            "ep_id": 773589,
                            "title": "中配版4",
                            "long_title": "沙漠",
                            "section_type": 0,
                        },
                    ],
                },
            },
            "https://www.bilibili.com/bangumi/play/ss43768",
        )

        self.assertEqual(
            [
                item.source_tab_item_title
                for item in catalog.episodes
            ],
            ["中配版1", "中配版4"],
        )
        self.assertEqual(
            [item.source_tab_title for item in catalog.episodes],
            ["地球脉动 第二季", "地球脉动 第二季"],
        )

    def test_browser_fallback_ignores_pv_rows_in_tv_list(self):
        episodes = _browser_rows_to_episodes([
            {
                "href": "https://www.bilibili.com/bangumi/play/ep1",
                "text": "1",
                "className": "EpisodeVirtualList_numberItem__hash",
            },
            {
                "href": "https://www.bilibili.com/bangumi/play/ep2",
                "text": "2",
                "className": "EpisodeVirtualList_numberItem__hash",
            },
            {
                "href": "https://www.bilibili.com/bangumi/play/epPV",
                "text": "PV",
                "className": "EpisodeVirtualList_listItem__hash",
            },
        ])

        self.assertEqual([item.episode_number for item in episodes], [1, 2])
        self.assertTrue(all(
            "/bangumi/play/ep" in item.source_url
            for item in episodes
        ))

    def test_bilibili_movie_episode_uses_season_title_as_film_name(self):
        payload = {
            "code": 0,
            "result": {
                "title": "哈利·波特与魔法石",
                "season_title": "哈利·波特与魔法石",
                "episodes": [{
                    "ep_id": 280975,
                    "title": "正片",
                    "long_title": "",
                }],
            },
        }

        catalog = _catalog_from_bilibili_payload(
            payload,
            "https://www.bilibili.com/bangumi/play/ss28281",
        )

        self.assertEqual(catalog.episodes[0].title, "哈利·波特与魔法石")

    def test_bilibili_variant_episode_keeps_film_and_version(self):
        payload = {
            "code": 0,
            "result": {
                "title": "神奇动物：格林德沃之罪",
                "season_title": "神奇动物：格林德沃之罪",
                "episodes": [
                    {
                        "ep_id": 262628,
                        "title": "原版",
                        "long_title": "",
                    },
                    {
                        "ep_id": 262630,
                        "title": "中文版",
                        "long_title": "",
                    },
                ],
            },
        }

        catalog = _catalog_from_bilibili_payload(
            payload,
            "https://www.bilibili.com/bangumi/play/ss25005",
        )

        self.assertEqual(
            [item.title for item in catalog.episodes],
            [
                "神奇动物：格林德沃之罪 · 原版",
                "神奇动物：格林德沃之罪 · 中文版",
            ],
        )

    def test_generic_episode_title_falls_back_to_real_file_stem(self):
        episode = {
            "episode_title": "正片",
            "relative_path": (
                "哈利波特/全季/哈利·波特与魔法石.mp4"
            ),
            "video_path": "",
        }

        self.assertEqual(
            _resolved_episode_title(episode),
            "哈利·波特与魔法石",
        )
        self.assertEqual(
            episode_display_title(
                "原版",
                relative_path=(
                    "哈利波特/全季/"
                    "神奇动物 格林德沃之罪 - 原版.mp4"
                ),
            ),
            "神奇动物 格林德沃之罪 - 原版",
        )

    def test_bilibili_season_url_uses_season_id_api(self):
        payload = {
            "code": 0,
            "result": {
                "season_id": 46089,
                "title": "葬送的芙莉莲",
                "season_title": "葬送的芙莉莲",
                "episodes": [
                    {
                        "ep_id": 779775,
                        "title": "1",
                        "long_title": "冒险的结束",
                    },
                    {
                        "ep_id": 779776,
                        "title": "2",
                        "long_title": "也不一定要是魔法…",
                    },
                ],
            },
        }
        with patch(
            "local_video_site.episodes.catalog._fetch_bilibili_season",
            return_value=payload,
        ) as fetch:
            catalog = discover_episode_catalog(
                "https://www.bilibili.com/bangumi/play/ss46089"
                "?from_spmid=666.25.series.0"
            )

        fetch.assert_called_once_with("46089", "season_id")
        self.assertEqual(catalog.provider, "bilibili")
        self.assertEqual(catalog.detected_series_name, "葬送的芙莉莲")
        self.assertEqual(len(catalog.episodes), 2)
        self.assertEqual(
            catalog.episodes[0].source_url,
            "https://www.bilibili.com/bangumi/play/ep779775",
        )

    def test_discover_episode_tabs_follows_returned_series_seasons(self):
        first_payload = {
            "code": 0,
            "result": {
                "season_id": 1001,
                "title": "老友记",
                "season_title": "第一季",
                "episodes": [
                    {
                        "ep_id": 1101,
                        "title": "1",
                        "long_title": "第一季第一集",
                    },
                ],
                "seasons": [
                    {
                        "season_id": 1001,
                        "season_title": "第一季",
                    },
                    {
                        "season_id": 1002,
                        "season_title": "第二季",
                    },
                ],
            },
        }
        second_payload = {
            "code": 0,
            "result": {
                "season_id": 1002,
                "title": "老友记",
                "season_title": "第二季",
                "episodes": [
                    {
                        "ep_id": 2101,
                        "title": "1",
                        "long_title": "第二季第一集",
                    },
                ],
            },
        }

        def fetch(identifier, parameter="ep_id"):
            if identifier == "1001":
                return first_payload
            self.assertEqual(identifier, "1002")
            self.assertEqual(parameter, "season_id")
            return second_payload

        with patch(
            "local_video_site.episodes.catalog._fetch_bilibili_season",
            side_effect=fetch,
        ):
            tabs = discover_episode_tabs(
                "https://www.bilibili.com/bangumi/play/ss1001"
            )

        self.assertEqual(
            [tab.tab_title for tab in tabs],
            ["第一季", "第二季"],
        )
        self.assertEqual(
            [
                tab.catalog.episodes[0].source_url.rsplit("/", 1)[-1]
                for tab in tabs
            ],
            ["ep1101", "ep2101"],
        )

    def test_season_label_prevents_second_season_overwriting_first(self):
        self.assertEqual(
            normalize_season_name("第三集", 3),
            ("第三季", 3),
        )
        self.assertEqual(
            normalize_season_name("Episode 4", 1),
            ("第4季", 4),
        )
        self.assertEqual(season_number_from_label("第二季", 1), 2)
        self.assertEqual(season_number_from_label("第12季", 1), 12)
        self.assertEqual(season_number_from_label("第十二季", 1), 12)

        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=self._catalog(),
        ):
            repaired = discover_batch(
                "老友记",
                "第三集",
                1,
                "https://www.bilibili.com/bangumi/play/ep468227",
                str(self.download_dir),
            )
        self.assertEqual(repaired["season_name"], "第三季")
        self.assertEqual(repaired["season_number"], 3)
        self.assertEqual(
            repaired["items"][0]["relative_path"],
            "老友记/第三季/001.mp4",
        )

        first_catalog = self._catalog()
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=first_catalog,
        ):
            first = discover_batch(
                "老友记",
                "第一季",
                1,
                "https://www.bilibili.com/bangumi/play/ep468227",
                str(self.download_dir),
            )
        first_report = confirm_batch(
            first["batch_id"],
            [item["id"] for item in first["items"]],
            first["revision"],
        )
        self.assertEqual(first_report["created"], 3)

        second_catalog = EpisodeCatalog(
            provider="bilibili",
            detected_series_name="老友记",
            detected_season_name="第二季",
            source_url="https://www.bilibili.com/bangumi/play/ep568227",
            episodes=[
                EpisodeCandidate(
                    1,
                    "第一集",
                    "https://www.bilibili.com/bangumi/play/ep568227",
                ),
                EpisodeCandidate(
                    2,
                    "第二集",
                    "https://www.bilibili.com/bangumi/play/ep568251",
                ),
            ],
        )
        with patch(
            "local_video_site.episodes.batches.discover_episode_catalog",
            return_value=second_catalog,
        ):
            second = discover_batch(
                "老友记",
                "第二季",
                1,
                "https://www.bilibili.com/bangumi/play/ep568227",
                str(self.download_dir),
            )
        self.assertEqual(second["season_number"], 2)
        confirm_batch(
            second["batch_id"],
            [item["id"] for item in second["items"]],
            second["revision"],
        )

        with database.connect() as connection:
            seasons = connection.execute(
                """
                SELECT name, season_number, relative_directory
                FROM video_seasons
                ORDER BY season_number
                """
            ).fetchall()
        self.assertEqual(
            [dict(row) for row in seasons],
            [
                {
                    "name": "第一季",
                    "season_number": 1,
                    "relative_directory": "老友记/第一季",
                },
                {
                    "name": "第二季",
                    "season_number": 2,
                    "relative_directory": "老友记/第二季",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
