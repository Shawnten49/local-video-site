import json
import logging
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.downloads.tasks import DownloadTaskWorker, RATE_LIMIT_PAUSE_EXIT_CODE, _hls_fields_from_line, _media_fields_from_line, _media_fields_from_summary, _metadata_completion_result, _is_cross_site_task, _prepare_cross_site_metadata, _keep_awake_command, _parse_progress_bytes, _parse_progress_metrics, _refresh_video_metadata_in_subprocess, _resume_media_url, _task_files_for_deletion, _apply_task_access_level, backfill_download_task_media_info, backfill_download_task_transport_info, create_tasks, decorate_download_task_metrics, request_task_deletion
from local_video_site.integrations.metadata import SiteMetadataResult
from local_video_site.media.quality import normalise_quality_label
from local_video_site.downloads.policies.media import get_effective_policy
from local_video_site.downloads.policies.transport import get_policy
from local_video_site.storage.deletion import set_delete_password


TEST_DELETE_SECRET = "test-delete-secret"


class DownloadTaskTests(unittest.TestCase):
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
        set_delete_password(TEST_DELETE_SECRET)

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_create_tasks_normalizes_deduplicates_and_paginates(self):
        report = create_tasks(
            str(self.download_dir),
            [
                "https://example.com/video/1?utm_source=test#part",
                "https://example.com/video/1",
                "not-a-url",
            ],
        )

        self.assertEqual(report["created"], 1)
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(len(report["invalid"]), 1)
        data = database.list_download_tasks(page=1, page_size=1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["status"], "pending")
        self.assertEqual(
            data["items"][0]["normalized_url"],
            "https://example.com/video/1",
        )

    def test_download_only_flag_is_persisted_and_mode_conflicts_are_rejected(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/download-only"],
            index_into_library=False,
        )
        self.assertEqual(report["created"], 1)
        task = database.get_download_task(report["task_ids"][0])
        self.assertEqual(task["index_into_library"], 0)

        conflict = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/download-only"],
            index_into_library=True,
        )
        self.assertEqual(conflict["created"], 0)
        self.assertEqual(len(conflict["invalid"]), 1)
        self.assertIn("入库模式", conflict["invalid"][0]["error"])

    def test_download_page_uses_readable_display_title_with_raw_tooltip(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/long-title"],
        )
        task_id = report["task_ids"][0]
        raw_title = (
            "[無碼破解]DHLD-015 长时间标题 "
            "18AV在線H成人影片-18AV萬部免費影片與您分享"
        )
        display_title = "[無碼破解]DHLD-015 长时间标题"
        video_path = self.download_dir / "long-title.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"video")
        database.upsert_video(
            path=video_path,
            title=raw_title,
            directory=self.download_dir.name,
            size=video_path.stat().st_size,
            mtime_ns=video_path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(video_path)
        database.set_video_display_title(
            video["id"],
            display_title,
        )
        database.update_download_task(
            task_id,
            title=raw_title,
            display_title="过期展示标题",
            video_id=video["id"],
        )

        page = admin_client().get("/downloads")

        self.assertEqual(page.status_code, 200)
        self.assertIn(display_title, page.text)
        self.assertIn(raw_title, page.text)
        self.assertNotIn(
            "data-task-title=\"{}\"".format(raw_title),
            page.text,
        )

    def test_download_only_option_is_available_in_api_and_page(self):
        client = admin_client()
        page = client.get("/downloads")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="download-download-only"', page.text)
        self.assertIn("只下载文件", page.text)

        response = client.post(
            "/api/download-tasks",
            json={
                "mode": "normal",
                "output_dir": str(self.download_dir),
                "quality": "site",
                "access_level": 2,
                "index_into_library": False,
                "urls": ["https://example.com/video/api-download-only"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task_ids"][0]
        task = database.get_download_task(task_id)
        self.assertEqual(task["index_into_library"], 0)
        self.assertEqual(task["access_level"], 0)
        refreshed = client.get("/downloads")
        self.assertIn("download-only-badge", refreshed.text)
        self.assertIn("仅下载", refreshed.text)

    def test_download_only_worker_skips_library_and_metadata_pipeline(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/download-only-worker"],
            index_into_library=False,
        )
        task = database.claim_download_task()
        self.assertIsNotNone(task)
        output = self.download_dir / "download-only.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video-file")
        summary = {
            "input_url": task["url"],
            "status": "success",
            "series_name": "download-only",
            "output_path": str(output),
            "size": output.stat().st_size,
            "media_quality": "1080p",
            "media_width": 1920,
            "media_height": 1080,
            "transport_kind": "direct-range",
        }
        with (
            patch(
                "local_video_site.downloads.worker._run_download_process",
                return_value=(
                    0,
                    self.data_dir / "download-task-logs",
                    "",
                ),
            ),
            patch(
                "local_video_site.downloads.worker._read_task_summary",
                return_value=summary,
            ),
            patch(
                "local_video_site.downloads.worker._prepare_cross_site_metadata",
            ) as prepare_metadata,
            patch(
                "local_video_site.downloads.worker._sync_video_metadata",
            ) as sync_metadata,
        ):
            DownloadTaskWorker(threading.Event())._process(task)

        prepare_metadata.assert_not_called()
        sync_metadata.assert_not_called()
        updated = database.get_download_task(report["task_ids"][0])
        self.assertEqual(updated["status"], "success")
        self.assertEqual(updated["index_into_library"], 0)
        self.assertEqual(updated["video_id"], None)
        self.assertEqual(updated["metadata_status"], "not_required")
        self.assertEqual(updated["file_path"], str(output.resolve()))
        self.assertEqual(updated["media_quality"], "1080p")
        self.assertEqual(database.list_videos()["total"], 0)
        self.assertEqual(
            list((self.data_dir / "thumbnails").glob("*"))
            if (self.data_dir / "thumbnails").exists()
            else [],
            [],
        )
        self.assertEqual(
            list((self.data_dir / "previews").glob("*"))
            if (self.data_dir / "previews").exists()
            else [],
            [],
        )

    def test_inherited_metadata_is_a_successful_terminal_state(self):
        self.assertEqual(
            _metadata_completion_result({
                "status": "inherited",
                "error": None,
            }),
            ("inherited", None),
        )
        self.assertEqual(
            _metadata_completion_result({
                "status": "failed",
                "error": "provider unavailable",
            }),
            ("failed", "provider unavailable"),
        )

    def test_false_inherited_metadata_failure_message_is_repaired(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/metadata-inherited"],
        )
        task_id = report["task_ids"][0]
        database.update_download_task(
            task_id,
            status="success",
            metadata_status="inherited",
            message="success; metadata: failed",
            error_detail=None,
        )

        self.assertEqual(
            database.repair_inherited_metadata_task_messages(),
            1,
        )
        task = database.get_download_task(task_id)
        self.assertEqual(task["status"], "success")
        self.assertEqual(task["metadata_status"], "inherited")
        self.assertEqual(task["message"], "success")
        self.assertIsNone(task["error_detail"])

    def test_youku_account_restriction_fails_without_automatic_retry(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://v.youku.com/v_show/id_example.html"],
        )
        task = database.get_download_task(report["task_ids"][0])
        worker = DownloadTaskWorker(
            threading.Event(),
            logging.getLogger("youku-terminal-test"),
        )
        error = (
            "ERROR: [youku] example: Youku server reported error -6004: "
            "该账号检测到有可疑违规、侵权等行为，已限制播放"
        )

        with patch(
            "local_video_site.downloads.worker.record_task_result"
        ):
            worker._schedule_failure(task, error)

        failed = database.get_download_task(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("优酷账号被限制播放", failed["message"])
        self.assertIn("-6004", failed["message"])
        self.assertIsNone(failed["next_retry_at"])

    def test_download_access_level_is_saved_and_applied_to_video(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/access-level"],
            access_level=1,
        )
        task = database.get_download_task(report["task_ids"][0])
        self.assertEqual(task["access_level"], 1)

        path = self.download_dir / "access-level.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        database.upsert_video(
            path,
            "Access Level Video",
            "download-hub",
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        database.update_download_task(task["id"], video_id=video["id"])
        task = database.get_download_task(task["id"])

        result = _apply_task_access_level(task, video["id"])

        self.assertEqual(result["access_level"], 1)
        self.assertEqual(
            database.get_video(video["id"])["access_level"],
            1,
        )

        page = admin_client().get("/downloads")
        self.assertIn('id="download-access-level"', page.text)

        invalid = admin_client().post(
            "/api/download-tasks",
            json={
                "mode": "normal",
                "output_dir": str(self.download_dir),
                "quality": "site",
                "access_level": 3,
                "urls": ["https://example.com/video/invalid-access"],
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_download_tasks_default_to_fifteen_items_per_page(self):
        report = create_tasks(
            str(self.download_dir),
            [
                "https://example.com/video/page-{}".format(index)
                for index in range(1, 32)
            ],
        )
        self.assertEqual(report["created"], 31)

        first = database.list_download_tasks()
        last = database.list_download_tasks(page=3)
        clamped = database.list_download_tasks(page=999)

        self.assertEqual(first["total"], 31)
        self.assertEqual(first["page_size"], 15)
        self.assertEqual(first["pages"], 3)
        self.assertEqual(first["page"], 1)
        self.assertEqual(len(first["items"]), 15)
        self.assertEqual(last["page"], 3)
        self.assertEqual(len(last["items"]), 1)
        self.assertEqual(clamped["page"], 3)

        client = admin_client()
        api = client.get("/api/download-tasks")
        api_override = client.get(
            "/api/download-tasks",
            params={"page": 1, "page_size": 30},
        )
        page = client.get(
            "/downloads",
            params={"page": 2, "status": "pending"},
        )

        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["page_size"], 15)
        self.assertEqual(len(api.json()["items"]), 15)
        self.assertEqual(api_override.json()["page_size"], 30)
        self.assertEqual(len(api_override.json()["items"]), 30)
        self.assertEqual(page.status_code, 200)
        self.assertIn("当前筛选共 31 个任务", page.text)
        self.assertIn("第 2/3 页", page.text)
        self.assertIn('name="status" value="pending"', page.text)
        self.assertIn("上一页", page.text)
        self.assertIn("下一页", page.text)

    def test_live_download_row_api_returns_only_requested_tasks(self):
        task_ids = create_tasks(
            str(self.download_dir),
            [
                "https://example.com/video/live-one",
                "https://example.com/video/live-two",
            ],
        )["task_ids"]
        database.update_download_task(
            task_ids[0],
            status="running",
            stage="downloading",
            progress_percent=42.5,
            downloaded_bytes=1024,
            total_bytes=2048,
            message="测试进度",
        )
        client = admin_client()

        first = client.get(
            "/api/download-tasks/live",
            params={"ids": str(task_ids[0])},
        )
        etag = first.headers.get("etag")
        cached = client.get(
            "/api/download-tasks/live",
            params={"ids": str(task_ids[0])},
            headers={"If-None-Match": etag},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["rows"]), 1)
        self.assertEqual(first.json()["rows"][0]["id"], task_ids[0])
        self.assertIn(
            'data-task-id="{}"'.format(task_ids[0]),
            first.json()["rows"][0]["html"],
        )
        self.assertIn("42.5%", first.json()["rows"][0]["html"])
        self.assertEqual(cached.status_code, 304)

        database.update_download_task(
            task_ids[0],
            progress_percent=55,
            message="更新后的进度",
        )
        updated = client.get(
            "/api/download-tasks/live",
            params={"ids": str(task_ids[0])},
            headers={"If-None-Match": etag},
        )
        missing = client.get(
            "/api/download-tasks/live",
            params={"ids": "{},999999".format(task_ids[0])},
        )

        self.assertEqual(updated.status_code, 200)
        self.assertIn("55.0%", updated.json()["rows"][0]["html"])
        self.assertTrue(missing.json()["full_refresh_required"])
        self.assertEqual(missing.json()["missing_ids"], [999999])

    def test_live_download_row_marks_filter_mismatch(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/live-filter"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            status="success",
            stage="finished",
        )
        response = admin_client().get(
            "/api/download-tasks/live",
            params={"ids": str(task_id), "status": "running"},
        )

        self.assertEqual(response.status_code, 200)
        row = response.json()["rows"][0]
        self.assertFalse(row["matches_filter"])
        self.assertTrue(response.json()["full_refresh_required"])

    def test_dual_source_tasks_deduplicate_globally_by_metadata_url(self):
        first_dir = self.root / "first-output"
        second_dir = self.root / "second-output"
        first = create_tasks(
            str(first_dir),
            items=[
                {
                    "metadata_url": "https://site-a.example/video/1",
                    "download_url": "https://site-b.example/video/1",
                },
                {
                    "metadata_url": "https://site-a.example/video/1",
                    "download_url": "https://site-b.example/video/2",
                },
                {
                    "metadata_url": "https://site-a.example/video/2",
                    "download_url": "",
                },
            ],
        )

        self.assertEqual(first["created"], 2)
        self.assertEqual(first["duplicates"], 1)
        second = create_tasks(
            str(second_dir),
            items=[
                {
                    "metadata_url": "https://site-a.example/video/1",
                    "download_url": "https://site-b.example/video/3",
                }
            ],
        )
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(
            second["duplicate_items"][0]["existing_task_id"],
            first["task_ids"][0],
        )
        tasks = database.list_download_tasks(page_size=10)["items"]
        task_by_metadata = {
            item["normalized_metadata_url"]: item for item in tasks
        }
        self.assertEqual(
            task_by_metadata["https://site-a.example/video/2"]["url"],
            "https://site-a.example/video/2",
        )

    def test_cross_site_metadata_prefetch_uses_source_a_title(self):
        task = {
            "id": 501,
            "url": "https://site-b.example/video/1",
            "metadata_url": "https://www.xvideos.com/video.abc/example",
            "title": None,
        }
        result = SiteMetadataResult(
            categories=[{"name": "Anal", "slug": "anal", "type": "tag"}],
            actors=[{"name": "Actor A", "slug": "actor-a"}],
            http_status=200,
            source="dom",
            provider="xvideos",
            title="Canonical title from A",
        )
        self.assertTrue(_is_cross_site_task(task))
        with (
            patch(
                "local_video_site.downloads.worker.fetch_site_metadata",
                return_value=result,
            ),
            patch(
                "local_video_site.downloads.worker.database.update_download_task"
            ) as update,
        ):
            prepared = _prepare_cross_site_metadata(
                task,
                logging.getLogger("cross-site-test"),
            )

        self.assertIs(prepared, result)
        self.assertEqual(task["_canonical_title"], "Canonical title from A")
        self.assertTrue(
            any(
                call.kwargs.get("title") == "Canonical title from A"
                for call in update.call_args_list
            )
        )

    def test_cross_site_metadata_accepts_xvideos_red_alias(self):
        task = {
            "id": 502,
            "url": "https://www.tnaflix.com/video/example",
            "metadata_url": (
                "https://www.xvideos.red/video.iohtimf194b/example"
            ),
            "title": None,
        }
        result = SiteMetadataResult(
            categories=[],
            actors=[{"name": "Actor A", "slug": "actor-a"}],
            http_status=200,
            source="dom",
            provider="xvideos",
            title="Canonical title",
        )
        with (
            patch(
                "local_video_site.downloads.worker.fetch_site_metadata",
                return_value=result,
            ) as fetch,
            patch(
                "local_video_site.downloads.worker.database.update_download_task"
            ),
        ):
            prepared = _prepare_cross_site_metadata(
                task,
                logging.getLogger("cross-site-red-test"),
            )

        self.assertIs(prepared, result)
        fetch.assert_called_once()
        self.assertEqual(task["_canonical_title"], "Canonical title")

    def test_cross_site_metadata_failure_does_not_block_download(self):
        task = {
            "id": 503,
            "url": "https://www.tnaflix.com/video/example",
            "metadata_url": (
                "https://www.xvideos.red/video.iohtimf194b/example"
            ),
            "title": None,
        }
        with (
            patch(
                "local_video_site.downloads.worker.fetch_site_metadata",
                side_effect=RuntimeError(
                    "Playwright Sync API inside the asyncio loop"
                ),
            ),
            patch(
                "local_video_site.downloads.worker.database.update_download_task"
            ) as update,
        ):
            prepared = _prepare_cross_site_metadata(
                task,
                logging.getLogger("cross-site-failure-test"),
            )

        self.assertIsNone(prepared)
        self.assertNotIn("_prefetched_metadata", task)
        self.assertTrue(
            any(
                "继续下载视频" in str(call.kwargs.get("message") or "")
                for call in update.call_args_list
            )
        )

    def test_empty_cross_site_metadata_does_not_block_download(self):
        task = {
            "id": 504,
            "url": "https://www.tnaflix.com/video/example",
            "metadata_url": (
                "https://www.xvideos.red/video.iohtimf194b/example"
            ),
            "title": None,
        }
        result = SiteMetadataResult(
            categories=[],
            actors=[],
            http_status=None,
            source="dom",
            provider="xvideos",
            title="",
            error="source unavailable",
        )
        with (
            patch(
                "local_video_site.downloads.worker.fetch_site_metadata",
                return_value=result,
            ),
            patch(
                "local_video_site.downloads.worker.database.update_download_task"
            ),
        ):
            prepared = _prepare_cross_site_metadata(
                task,
                logging.getLogger("cross-site-empty-test"),
            )

        self.assertIsNone(prepared)
        self.assertNotIn("_prefetched_metadata", task)

    def test_metadata_refresh_runs_in_subprocess(self):
        completed = Mock(returncode=0, stdout="{}", stderr="")
        with patch(
            "local_video_site.downloads.worker.subprocess.run",
            return_value=completed,
        ) as run:
            _refresh_video_metadata_in_subprocess(
                83,
                logging.getLogger("metadata-subprocess-test"),
            )

        command = run.call_args.args[0]
        self.assertEqual(
            command[-3:],
            ["--video-id", "83", "--refresh"],
        )

    def test_metadata_refresh_prefers_json_report_error(self):
        completed = Mock(
            returncode=1,
            stdout=json.dumps({
                "failed": 1,
                "errors": ["不是 Bilibili 番剧剧集或季地址"],
            }),
            stderr=(
                "INFO local_video_site command.start | "
                "Command started: metadata-refresh"
            ),
        )
        with (
            patch(
                "local_video_site.downloads.worker.subprocess.run",
                return_value=completed,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "不是 Bilibili 番剧剧集或季地址",
            ),
        ):
            _refresh_video_metadata_in_subprocess(
                770,
                logging.getLogger("metadata-subprocess-error-test"),
            )

    def test_task_claim_recovery_retry_and_cancel(self):
        report = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/one"],
        )
        task_id = report["task_ids"][0]
        claimed = database.claim_download_task()
        self.assertEqual(claimed["id"], task_id)
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["attempts"], 0)
        self.assertEqual(claimed["run_count"], 1)
        self.assertTrue(claimed["run_started_at"])
        self.assertEqual(claimed["speed_bytes_per_sec"], 0)
        self.assertIsNone(claimed["eta_seconds"])

        self.assertEqual(database.recover_download_tasks(), 1)
        recovered = database.get_download_task(task_id)
        self.assertEqual(recovered["status"], "pending")
        self.assertTrue(database.cancel_download_task(task_id))
        self.assertEqual(
            database.get_download_task(task_id)["status"],
            "cancelled",
        )
        self.assertTrue(database.retry_download_task(task_id))
        self.assertEqual(
            database.get_download_task(task_id)["status"],
            "pending",
        )

    def test_pause_running_task_and_resume_without_counting_failure(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/pause"],
        )["task_ids"][0]
        claimed = database.claim_download_task()
        self.assertEqual(claimed["id"], task_id)

        status = database.request_pause_download_task(task_id)
        self.assertEqual(status, "pausing")
        self.assertTrue(database.download_task_pause_requested(task_id))
        database.finish_pause_download_task(
            task_id,
            partial_path=str(self.download_dir / "video.mp4.part"),
            checkpoint_path=str(
                self.download_dir / "video.mp4.resume.json"
            ),
        )
        paused = database.get_download_task(task_id)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["attempts"], 0)
        self.assertEqual(paused["run_count"], 1)
        self.assertTrue(paused["pause_requested"] == 0)

        self.assertTrue(database.resume_download_task(task_id))
        resumed = database.get_download_task(task_id)
        self.assertEqual(resumed["status"], "pending")
        self.assertEqual(resumed["resume_count"], 1)
        self.assertFalse(database.download_task_pause_requested(task_id))

    def test_failed_task_with_partial_file_shows_and_uses_resume(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/failed-partial"],
        )["task_ids"][0]
        partial = self.download_dir / "failed-partial.mp4.part"
        partial.write_bytes(b"x" * 1024)
        database.update_download_task(
            task_id,
            status="failed",
            stage="finished",
            progress_percent=91.1,
            downloaded_bytes=partial.stat().st_size,
            total_bytes=2048,
            attempts=0,
            partial_path=str(partial),
            message="previous failure",
        )

        page = admin_client().get("/downloads")
        self.assertEqual(page.status_code, 200)
        self.assertIn("继续下载", page.text)
        self.assertIn('data-action="resume"', page.text)

        self.assertTrue(database.resume_download_task(task_id))
        resumed = database.get_download_task(task_id)
        self.assertEqual(resumed["status"], "pending")
        self.assertEqual(resumed["progress_percent"], 91.1)
        self.assertEqual(resumed["downloaded_bytes"], 1024)
        self.assertEqual(resumed["partial_path"], str(partial))
        self.assertTrue(partial.exists())

    def test_retry_preserves_progress_when_partial_file_exists(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/retry-partial"],
        )["task_ids"][0]
        partial = self.download_dir / "retry-partial.mp4.part"
        partial.write_bytes(b"x" * 2048)
        database.update_download_task(
            task_id,
            status="failed",
            progress_percent=73.0,
            downloaded_bytes=2048,
            total_bytes=4096,
            partial_path=str(partial),
        )

        self.assertTrue(database.retry_download_task(task_id))
        retried = database.get_download_task(task_id)
        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["progress_percent"], 73.0)
        self.assertEqual(retried["downloaded_bytes"], 2048)
        self.assertEqual(retried["total_bytes"], 4096)
        self.assertEqual(retried["message"], "等待从断点继续")
        self.assertTrue(partial.exists())

    def test_resume_reuses_last_discovered_media_url(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/play/episode"],
        )["task_ids"][0]
        task = database.get_download_task(task_id)
        media_url = "https://cdn.example/video/1080/index.m3u8"
        latest = (
            config.DOWNLOAD_TASK_LOG_DIR
            / "task-{}-20261117-230000".format(task_id)
        )
        older = (
            config.DOWNLOAD_TASK_LOG_DIR
            / "task-{}-20261117-220000".format(task_id)
        )
        latest.mkdir(parents=True)
        older.mkdir(parents=True)
        (latest / "summary.jsonl").write_text(
            json.dumps({
                "input_url": task["url"],
                "status": "failed",
                "media_url": "",
            }) + "\n",
            encoding="utf-8",
        )
        (older / "summary.jsonl").write_text(
            json.dumps({
                "input_url": task["url"],
                "status": "failed",
                "media_kind": "hls",
                "media_url": media_url,
            }) + "\n",
            encoding="utf-8",
        )
        database.update_download_task(
            task_id,
            status="failed",
            partial_path=str(
                self.download_dir / "episode.mp4.part"
            ),
        )
        task = database.get_download_task(task_id)

        self.assertEqual(_resume_media_url(task), media_url)
        self.assertEqual(
            _resume_media_url({**task, "partial_path": None}),
            "",
        )

    def test_worker_schedules_transient_failure_for_retry(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/retry"],
        )["task_ids"][0]
        task = database.claim_download_task()
        worker = DownloadTaskWorker(threading.Event())
        with (
            patch(
                "local_video_site.downloads.worker._run_download_process",
                return_value=(-1, self.download_dir, "network timeout"),
            ),
            patch(
                "local_video_site.downloads.worker._read_task_summary",
                return_value=None,
            ),
        ):
            worker._process(task)

        updated = database.get_download_task(task_id)
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(updated["attempts"], 1)
        self.assertIn("network timeout", updated["error_detail"])
        self.assertTrue(updated["next_retry_at"])

    def test_worker_auto_pauses_rate_limited_download_without_failure(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/rate-limited"],
        )["task_ids"][0]
        task = database.claim_download_task()
        worker = DownloadTaskWorker(threading.Event())
        with patch(
            "local_video_site.downloads.worker._run_download_process",
            return_value=(
                RATE_LIMIT_PAUSE_EXIT_CODE,
                self.download_dir,
                "CDN rate limit repeated",
            ),
        ):
            worker._process(task)

        paused = database.get_download_task(task_id)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["attempts"], 0)
        self.assertIn("CDN 限流", paused["message"])

    def test_progress_parser_reads_downloaded_and_total_sizes(self):
        self.assertEqual(
            _parse_progress_bytes(
                "| 353.7 MiB / 9.1 GiB | 1.2 MiB/s | ETA 10s",
                3.9,
            ),
            (int(353.7 * 1024 ** 2), int(9.1 * 1024 ** 3)),
        )
        downloaded, total = _parse_progress_bytes(
            "| segment 50/1000 | 12.5 MiB | 1.0 MiB/s | ETA 20s",
            5.0,
        )
        self.assertEqual(downloaded, int(12.5 * 1024 ** 2))
        self.assertEqual(total, downloaded * 20)

    def test_progress_parser_reads_speed_and_eta_for_direct_and_hls(self):
        direct = _parse_progress_metrics(
            "| 353.7 MiB / 9.1 GiB | 1.2 MiB/s | ETA 10s",
            3.9,
        )
        hls = _parse_progress_metrics(
            "| segment 50/1000 | 12.5 MiB | 2.0 MiB/s | ETA 20s",
            5.0,
        )

        self.assertEqual(direct[2], int(1.2 * 1024 ** 2))
        self.assertEqual(direct[3], 10)
        self.assertEqual(hls[2], 2 * 1024 ** 2)
        self.assertEqual(hls[3], 20)

    def test_hls_batch_parser_reads_connections_and_segment_progress(self):
        self.assertEqual(
            _hls_fields_from_line(
                "HLS batch 67-72/332 concurrency=4 "
                "bytes=8.7 MiB speed=1.4 MiB/s ETA 265s"
            ),
            {
                "hls_connection_count": 4,
                "hls_completed_segments": 72,
                "hls_total_segments": 332,
            },
        )
        self.assertEqual(_hls_fields_from_line("Progress 20%"), {})

    def test_media_quality_is_stable_and_uses_standard_height(self):
        self.assertEqual(normalise_quality_label("1080p", height=1080), "1080p")
        self.assertEqual(normalise_quality_label(None, height=392), "360p")
        self.assertEqual(normalise_quality_label(None, height=554), "480p")
        self.assertEqual(normalise_quality_label("4K", height=2160), "2160p")
        self.assertEqual(
            normalise_quality_label(None, width=1920, height=804),
            "1080p",
        )
        self.assertEqual(
            normalise_quality_label(None, width=3840, height=1608),
            "2160p",
        )
        self.assertEqual(
            normalise_quality_label("1080P 高清"),
            "1080p",
        )
        self.assertEqual(
            normalise_quality_label("720p", width=1920, height=804),
            "1080p",
        )
        self.assertEqual(
            normalise_quality_label("1080p", width=3840, height=1608),
            "2160p",
        )
        self.assertIsNone(normalise_quality_label("unknown"))

        output_fields = _media_fields_from_line(
            "Media selected: quality=720p width=1280 height=720"
        )
        self.assertEqual(
            output_fields,
            {
                "media_width": 1280,
                "media_height": 720,
                "media_quality": "720p",
            },
        )
        summary_fields = _media_fields_from_summary(
            {
                "media_quality": None,
                "media_width": 1920,
                "media_height": 1080,
            }
        )
        self.assertEqual(summary_fields["media_quality"], "1080p")

    def test_backfill_quality_from_existing_task_summary(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/backfill"],
        )["task_ids"][0]
        log_dir = config.DOWNLOAD_TASK_LOG_DIR / "task-{}-backfill".format(
            task_id
        )
        log_dir.mkdir(parents=True)
        (log_dir / "summary.jsonl").write_text(
            (
                '{"input_url":"https://example.com/video/backfill",'
                '"media_quality":"480p","media_width":854,'
                '"media_height":480}\n'
            ),
            encoding="utf-8",
        )
        database.update_download_task(
            task_id,
            status="failed",
            stage="finished",
            log_path=str(log_dir),
        )

        stats = backfill_download_task_media_info(
            logging.getLogger("download-media-backfill-test")
        )
        task = database.get_download_task(task_id)

        self.assertEqual(stats["from_summaries"], 1)
        self.assertEqual(task["media_quality"], "480p")
        self.assertEqual(task["media_width"], 854)
        self.assertEqual(task["media_height"], 480)
        decorated = decorate_download_task_metrics(task)
        self.assertEqual(decorated["display_media_quality"], "480p")
        self.assertEqual(decorated["display_media_resolution"], "854×480")

    def test_backfill_hls_transport_from_existing_task_log(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://hls-backfill.example/video/1"],
        )["task_ids"][0]
        log_dir = config.DOWNLOAD_TASK_LOG_DIR / "task-{}-hls".format(task_id)
        log_dir.mkdir(parents=True)
        (log_dir / "download-test.log").write_text(
            "Selected media type: hls\n"
            "HLS batch 331-332/332 concurrency=4 "
            "bytes=2.8 MiB speed=1.9 MiB/s ETA 0s\n"
            "Browser HLS segments merged: /tmp/video.mp4\n",
            encoding="utf-8",
        )
        database.update_download_task(
            task_id,
            status="success",
            stage="finished",
            log_path=str(log_dir),
            downloaded_bytes=1024,
        )

        stats = backfill_download_task_transport_info(
            logging.getLogger("download-transport-backfill-test")
        )
        task = database.get_download_task(task_id)
        policy = get_policy("hls-backfill.example")

        self.assertEqual(stats["hls"], 1)
        self.assertEqual(task["transport_kind"], "hls")
        self.assertEqual(task["hls_connection_count"], 4)
        self.assertEqual(task["hls_completed_segments"], 332)
        self.assertEqual(task["hls_total_segments"], 332)
        self.assertEqual(policy["transport_mode"], "hls")

    def test_elapsed_time_is_accumulated_and_frozen_when_task_stops(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/elapsed"],
        )["task_ids"][0]
        database.claim_download_task()
        started_at = (
            datetime.now().astimezone() - timedelta(seconds=10)
        ).isoformat(timespec="seconds")
        database.update_download_task(
            task_id,
            elapsed_seconds=5,
            run_started_at=started_at,
            speed_bytes_per_sec=1024,
            eta_seconds=30,
        )

        database.update_download_task(
            task_id,
            status="pending",
            stage="waiting",
            message="waiting to resume",
        )
        task = database.get_download_task(task_id)

        self.assertGreaterEqual(task["elapsed_seconds"], 15)
        self.assertIsNone(task["run_started_at"])
        self.assertEqual(task["speed_bytes_per_sec"], 0)
        self.assertIsNone(task["eta_seconds"])

    def test_download_metrics_enrichment_and_stale_progress(self):
        now = datetime.now().astimezone()
        running = decorate_download_task_metrics(
            {
                "status": "running",
                "stage": "downloading",
                "elapsed_seconds": 100,
                "run_started_at": (
                    now - timedelta(seconds=30)
                ).isoformat(timespec="seconds"),
                "speed_bytes_per_sec": 2048,
                "eta_seconds": 20,
                "downloaded_bytes": 2048,
                "total_bytes": 8192,
                "last_progress_at": (
                    now - timedelta(seconds=1)
                ).isoformat(timespec="seconds"),
            },
            now=now,
        )
        stale = decorate_download_task_metrics(
            {
                "status": "running",
                "stage": "downloading",
                "speed_bytes_per_sec": 2048,
                "eta_seconds": 20,
                "last_progress_at": (
                    now - timedelta(seconds=30)
                ).isoformat(timespec="seconds"),
            },
            now=now,
        )
        success = decorate_download_task_metrics(
            {
                "status": "success",
                "stage": "finished",
                "elapsed_seconds": 10,
                "downloaded_bytes": 20480,
            },
            now=now,
        )

        self.assertEqual(running["elapsed_live_seconds"], 130)
        self.assertEqual(running["speed_kind"], "current")
        self.assertEqual(running["eta_live_seconds"], 20)
        self.assertTrue(stale["metrics_stale"])
        self.assertEqual(stale["speed_live_bytes"], 0)
        self.assertIsNone(stale["eta_live_seconds"])
        self.assertEqual(success["speed_kind"], "average")
        self.assertEqual(success["speed_live_bytes"], 2048)
        self.assertEqual(success["eta_live_seconds"], 0)

    def test_download_page_and_api_include_live_metrics(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/metrics"],
        )["task_ids"][0]
        database.claim_download_task()
        database.update_download_task(
            task_id,
            stage="downloading",
            progress_percent=50,
            downloaded_bytes=1024,
            total_bytes=2048,
            speed_bytes_per_sec=1024,
            eta_seconds=1,
            last_progress_at=datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        )
        client = admin_client()

        page = client.get("/downloads")
        api = client.get("/api/download-tasks/{}".format(task_id))

        self.assertEqual(page.status_code, 200)
        self.assertIn("data-task-metrics", page.text)
        self.assertIn("data-metric-elapsed", page.text)
        status_cell = page.text.split(
            '<td class="download-status-cell">',
            1,
        )[1].split("</td>", 1)[0]
        self.assertIn("download-status-heading", status_cell)
        self.assertIn("download-status-stage", status_cell)
        self.assertLess(
            status_cell.index("download-status-heading"),
            status_cell.index("download-progress-track"),
        )
        self.assertLess(
            status_cell.index("download-progress-track"),
            status_cell.index("download-live-metrics"),
        )
        self.assertIn("速度", page.text)
        self.assertIn("已用", page.text)
        self.assertIn("剩余", page.text)
        self.assertIn('data-action="delete"', page.text)
        self.assertIn('id="download-delete-dialog"', page.text)
        self.assertIn('id="download-delete-password"', page.text)
        self.assertNotIn('data-action="cancel"', page.text)
        self.assertEqual(api.status_code, 200)
        payload = api.json()
        self.assertEqual(payload["speed_kind"], "current")
        self.assertEqual(payload["speed_live_bytes"], 1024)
        self.assertEqual(payload["eta_live_seconds"], 1)
        self.assertGreaterEqual(payload["elapsed_live_seconds"], 0)

        database.update_download_task(
            task_id,
            status="paused",
            stage="paused",
            message="paused",
        )
        paused_page = client.get("/downloads")
        paused_cell = paused_page.text.split(
            '<td class="download-status-cell">',
            1,
        )[1].split("</td>", 1)[0]
        self.assertEqual(paused_cell.count("已暂停"), 1)
        self.assertNotIn(
            'class="download-status-stage">已暂停',
            paused_cell,
        )

    def test_pending_task_delete_removes_partial_files_and_hides_task(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/delete-partial"],
        )["task_ids"][0]
        partial = self.download_dir / "delete-partial.mp4.part"
        checkpoint = self.download_dir / "delete-partial.mp4.resume.json"
        partial.write_bytes(b"partial-video")
        checkpoint.write_text("{}", encoding="utf-8")
        unrelated = self.download_dir / "keep-me.txt"
        unrelated.write_text("keep", encoding="utf-8")
        database.update_download_task(
            task_id,
            title="delete-partial",
            filename_hint="delete-partial",
            downloaded_bytes=partial.stat().st_size,
            partial_path=str(partial),
            checkpoint_path=str(checkpoint),
        )

        response = admin_client().request(
            "DELETE",
            "/api/download-tasks/{}".format(task_id),
            json={"password": ""},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            database.get_download_task(task_id)["status"],
            "deleting",
        )

        deletion = database.next_pending_download_deletion()
        self.assertEqual(deletion["id"], task_id)
        DownloadTaskWorker(threading.Event())._finalize_deletion(deletion)

        self.assertFalse(partial.exists())
        self.assertFalse(checkpoint.exists())
        self.assertTrue(unrelated.is_file())
        deleted = database.get_download_task(task_id)
        self.assertEqual(deleted["status"], "deleted")
        self.assertTrue(deleted["deleted_at"])
        self.assertEqual(database.list_download_tasks()["total"], 0)

    def test_delete_task_with_video_requires_password_and_removes_video(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/delete-final"],
        )["task_ids"][0]
        video_path = self.download_dir / "delete-final.mp4"
        video_path.write_bytes(b"final-video")
        database.upsert_video(
            path=video_path,
            title="Delete Final",
            directory="download-hub",
            size=video_path.stat().st_size,
            mtime_ns=video_path.stat().st_mtime_ns,
        )
        video_id = database.list_videos()["items"][0]["id"]
        database.update_download_task(
            task_id,
            file_path=str(video_path),
            file_name=video_path.name,
            video_id=video_id,
        )
        client = admin_client()

        rejected = client.request(
            "DELETE",
            "/api/download-tasks/{}".format(task_id),
            json={"password": "wrong"},
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertTrue(video_path.is_file())

        accepted = client.request(
            "DELETE",
            "/api/download-tasks/{}".format(task_id),
            json={"password": TEST_DELETE_SECRET},
        )
        self.assertEqual(accepted.status_code, 202)
        deletion = database.next_pending_download_deletion()
        DownloadTaskWorker(threading.Event())._finalize_deletion(deletion)

        self.assertFalse(video_path.exists())
        self.assertIsNone(database.get_video(video_id))
        self.assertEqual(
            database.get_download_task(task_id)["status"],
            "deleted",
        )

    def test_success_task_cannot_be_deleted_and_running_task_gets_marker(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        video_path = self.download_dir / "success-delete.mp4"
        video_path.write_bytes(b"video")
        database.upsert_video(
            path=video_path,
            title="Success Delete",
            directory="download-hub",
            size=video_path.stat().st_size,
            mtime_ns=video_path.stat().st_mtime_ns,
        )
        video_id = database.list_videos()["items"][0]["id"]
        success_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/success-delete"],
        )["task_ids"][0]
        database.claim_download_task()
        database.update_download_task(
            success_id,
            status="success",
            stage="finished",
            video_id=video_id,
            file_path=str(video_path),
        )
        refused = admin_client().request(
            "DELETE",
            "/api/download-tasks/{}".format(success_id),
            json={"password": TEST_DELETE_SECRET},
        )
        self.assertEqual(refused.status_code, 409)

        running_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/running-delete"],
        )["task_ids"][0]
        database.claim_download_task()
        self.assertEqual(request_task_deletion(running_id), "deleting")
        marker = (
            config.DOWNLOAD_CONTROL_DIR
            / "task-{}.delete".format(running_id)
        )
        self.assertTrue(marker.is_file())
        self.assertTrue(database.download_task_delete_requested(running_id))
        self.assertEqual(
            database.get_download_task(running_id)["status"],
            "deleting",
        )

    def test_success_task_record_can_be_deleted_without_touching_files_or_video(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        video_path = self.download_dir / "record-only.mp4"
        video_path.write_bytes(b"video")
        partial = self.download_dir / "record-only.mp4.part"
        partial.write_bytes(b"partial")
        database.upsert_video(
            path=video_path,
            title="Record Only",
            directory="download-hub",
            size=video_path.stat().st_size,
            mtime_ns=video_path.stat().st_mtime_ns,
        )
        video_id = database.list_videos()["items"][0]["id"]
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/record-only"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            status="success",
            stage="finished",
            video_id=video_id,
            file_path=str(video_path),
            file_name=video_path.name,
            partial_path=str(partial),
        )

        client = admin_client()
        page = client.get("/downloads")
        self.assertIn('data-action="delete-record"', page.text)

        response = client.delete(
            f"/api/download-tasks/{task_id}/record"
        )

        self.assertEqual(response.status_code, 200)
        with database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        self.assertIsNone(row)
        self.assertTrue(video_path.is_file())
        self.assertTrue(partial.is_file())
        self.assertIsNotNone(database.get_video(video_id))

    def test_only_success_tasks_allow_record_only_deletion(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/record-pending"],
        )["task_ids"][0]

        response = admin_client().delete(
            f"/api/download-tasks/{task_id}/record"
        )

        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(database.get_download_task(task_id))

    def test_video_soft_delete_preserves_linked_download_task_history(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        video_path = self.download_dir / "linked-video.mp4"
        video_path.write_bytes(b"video")
        database.upsert_video(
            path=video_path,
            title="Linked Video",
            directory="download-hub",
            size=video_path.stat().st_size,
            mtime_ns=video_path.stat().st_mtime_ns,
        )
        video_id = database.list_videos()["items"][0]["id"]
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/linked"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            status="success",
            stage="finished",
            video_id=video_id,
            file_path=str(video_path),
        )

        with database.connect() as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0],
                0,
            )
        self.assertTrue(database.delete_video_record(video_id))

        task = database.get_download_task(task_id)
        self.assertEqual(task["status"], "success")
        self.assertFalse(task["deleted_at"])
        self.assertEqual(task["video_id"], video_id)
        self.assertEqual(task["file_path"], str(video_path))
        self.assertFalse(task["video_available"])
        self.assertEqual(database.list_download_tasks()["total"], 1)

    def test_orphan_download_tasks_are_visible_then_repaired(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/orphan"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            status="success",
            stage="finished",
            video_id=999999,
            file_path=str(self.download_dir / "missing.mp4"),
        )
        page = admin_client().get("/downloads")
        self.assertIn("源视频已删除", page.text)
        self.assertIn("删除记录", page.text)
        self.assertNotIn('href="/watch/999999"', page.text)

        repaired = database.repair_orphan_video_dependencies()
        self.assertGreaterEqual(repaired["download_tasks"], 1)
        self.assertEqual(
            database.get_download_task(task_id)["status"],
            "deleted",
        )

    def test_orphan_repair_backfills_running_task_elapsed_time(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/elapsed"],
        )["task_ids"][0]
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE download_tasks
                SET status = 'running',
                    started_at = '2020-01-01T00:00:00+08:00',
                    run_started_at = NULL,
                    elapsed_seconds = 0
                WHERE id = ?
                """,
                (task_id,),
            )

        database.repair_orphan_video_dependencies()

        task = database.get_download_task(task_id)
        self.assertGreater(int(task["elapsed_seconds"]), 0)

    def test_orphan_success_task_can_be_deleted_from_download_page(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/orphan-delete"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            status="success",
            stage="finished",
            video_id=999998,
            file_path=str(self.download_dir / "missing-orphan.mp4"),
        )

        response = admin_client().request(
            "DELETE",
            "/api/download-tasks/{}".format(task_id),
            json={"password": TEST_DELETE_SECRET},
        )
        self.assertEqual(response.status_code, 202)
        deletion = database.next_pending_download_deletion()
        self.assertEqual(deletion["id"], task_id)
        DownloadTaskWorker(threading.Event())._finalize_deletion(deletion)

        task = database.get_download_task(task_id)
        self.assertEqual(task["status"], "deleted")
        self.assertTrue(task["deleted_at"])

    def test_task_deletion_rejects_file_outside_download_directory(self):
        outside = self.root / "outside.mp4.part"
        outside.write_bytes(b"outside")

        with self.assertRaises(PermissionError):
            _task_files_for_deletion(
                {
                    "output_dir": str(self.download_dir),
                    "partial_path": str(outside),
                }
            )

    def test_task_deletion_includes_direct_range_checkpoint(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        partial = self.download_dir / "video.mp4.part"
        checkpoint = self.download_dir / "video.mp4.range.json"
        partial.write_bytes(b"partial")
        checkpoint.write_text('{"version":1}', encoding="utf-8")

        files = _task_files_for_deletion(
            {
                "output_dir": str(self.download_dir),
                "filename_hint": "video",
                "partial_path": str(partial),
            }
        )

        self.assertEqual(
            {path.name for path in files},
            {"video.mp4.part", "video.mp4.range.json"},
        )

    def test_keep_awake_command_uses_caffeinate_flags(self):
        with (
            patch(
                "local_video_site.downloads.worker.sys.platform",
                "darwin",
            ),
            patch(
                "local_video_site.downloads.worker.shutil.which",
                return_value="/usr/bin/caffeinate",
            ),
            patch.dict(
                os.environ,
                {
                    "LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE": "1",
                    "LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE_FLAGS": "-ims",
                },
            ),
        ):
            command = _keep_awake_command(1234)

        self.assertEqual(
            command,
            ["/usr/bin/caffeinate", "-ims", "-w", "1234"],
        )

    def test_repeated_stalls_are_requeued_then_failed(self):
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/stalled"],
        )["task_ids"][0]
        database.claim_download_task()
        database.update_download_task(
            task_id,
            progress_percent=87.5,
            downloaded_bytes=900,
            total_bytes=1000,
        )

        first = database.record_download_stall(
            task_id,
            "no progress for 180 seconds",
            partial_path="/tmp/video.part",
        )
        first_task = database.get_download_task(task_id)
        database.claim_download_task()
        second = database.record_download_stall(task_id, "stalled again")
        database.claim_download_task()
        third = database.record_download_stall(task_id, "stalled third time")
        failed = database.get_download_task(task_id)

        self.assertEqual(first, "pending")
        self.assertEqual(first_task["progress_percent"], 87.5)
        self.assertEqual(first_task["stall_count"], 1)
        self.assertEqual(second, "pending")
        self.assertEqual(third, "failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 1)

    def test_download_page_and_api_round_trip(self):
        client = admin_client()
        created = client.post(
            "/api/download-tasks",
            json={
                "output_dir": str(self.download_dir),
                "urls": [
                    "https://example.com/video/page",
                    "https://example.com/video/second",
                ],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["created"], 2)

        api = client.get("/api/download-tasks", params={"q": "example"})
        page = client.get("/downloads")
        home = client.get("/")

        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["total"], 2)
        self.assertIn("新增任务", page.text)
        self.assertIn("下载任务", page.text)
        self.assertIn("文件大小", page.text)
        self.assertIn("总大小", page.text)
        self.assertIn('id="download-summary"', page.text)
        self.assertIn('id="download-table-section"', page.text)
        self.assertIn('id="download-task-dialog"', page.text)
        self.assertIn('id="download-notice"', page.text)
        self.assertNotIn('<select name="output_dir"', page.text)
        self.assertNotIn('href="/downloads"', home.text)
        self.assertIn('href="/manage/videos"', home.text)

    def test_directory_filters_are_removed_with_legacy_redirects(self):
        client = admin_client()
        create_tasks(
            str(self.download_dir),
            ["https://example.com/video/filters"],
        )
        video_path = self.download_dir / "filter-video.mp4"
        video_path.write_bytes(b"video")
        database.upsert_video(
            video_path,
            "Filter video",
            "download-hub",
            video_path.stat().st_size,
            video_path.stat().st_mtime_ns,
        )

        downloads_redirect = client.get(
            "/downloads",
            params={
                "output_dir": str(self.download_dir),
                "status": "pending",
                "q": "filters",
                "page": 1,
            },
            follow_redirects=False,
        )
        videos_redirect = client.get(
            "/manage/videos",
            params={
                "directory": "download-hub",
                "storage": "local",
                "q": "Filter",
                "page": 1,
            },
            follow_redirects=False,
        )
        downloads_page = client.get("/downloads")
        videos_page = client.get("/manage/videos")
        downloads_api = client.get(
            "/api/download-tasks",
            params={"output_dir": str(self.download_dir / "ignored")},
        )
        videos_api = client.get(
            "/api/manage/videos",
            params={"directory": "ignored"},
        )

        self.assertEqual(downloads_redirect.status_code, 302)
        self.assertNotIn(
            "output_dir=",
            downloads_redirect.headers["location"],
        )
        self.assertIn("status=pending", downloads_redirect.headers["location"])
        self.assertEqual(videos_redirect.status_code, 302)
        self.assertNotIn(
            "directory=",
            videos_redirect.headers["location"],
        )
        self.assertIn("storage=local", videos_redirect.headers["location"])
        self.assertNotIn('<select name="output_dir"', downloads_page.text)
        self.assertNotIn('<select name="directory"', videos_page.text)
        self.assertEqual(downloads_api.status_code, 200)
        self.assertEqual(downloads_api.json()["total"], 1)
        self.assertEqual(videos_api.status_code, 200)
        self.assertEqual(videos_api.json()["total"], 1)

    def test_download_page_and_api_show_media_quality(self):
        client = admin_client()
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/quality"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            media_width=1920,
            media_height=1080,
            media_quality="1080p",
        )

        api = client.get("/api/download-tasks/{}".format(task_id))
        page = client.get("/downloads")

        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["media_quality"], "1080p")
        self.assertEqual(api.json()["display_media_quality"], "1080p")
        self.assertEqual(api.json()["display_media_resolution"], "1920×1080")
        self.assertIn("download-quality-badge", page.text)
        self.assertIn(">1080p</span>", page.text)
        self.assertIn('title="1920×1080"', page.text)

    def test_download_page_shows_hls_connections_and_segment_progress(self):
        client = admin_client()
        task_id = create_tasks(
            str(self.download_dir),
            ["https://example.com/video/hls-progress"],
        )["task_ids"][0]
        database.update_download_task(
            task_id,
            status="running",
            stage="downloading",
            transport_kind="hls",
            hls_connection_count=4,
            hls_completed_segments=70,
            hls_total_segments=70,
        )

        page = client.get("/downloads")
        api = client.get("/api/download-tasks/{}".format(task_id))

        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["hls_connection_count"], 4)
        self.assertEqual(api.json()["hls_completed_segments"], 70)
        self.assertEqual(api.json()["hls_total_segments"], 70)
        self.assertIn("HLS · 4 连接", page.text)
        self.assertIn("· 分段 70/70", page.text)

    def test_requested_download_quality_is_saved_and_rendered(self):
        client = admin_client()
        created = client.post(
            "/api/download-tasks",
            json={
                "output_dir": str(self.download_dir),
                "quality": "720p",
                "urls": ["https://example.com/video/requested-quality"],
            },
        )
        self.assertEqual(created.status_code, 200)
        task_id = created.json()["task_ids"][0]
        task = database.get_download_task(task_id)
        page = client.get("/downloads")

        self.assertEqual(task["requested_quality"], "720p")
        self.assertIn('id="download-quality"', page.text)
        self.assertIn('<option value="720p">720p</option>', page.text)
        self.assertIn('<option value="480p">480p</option>', page.text)
        self.assertIn(
            '<option value="2160p">4K 优先（最高 2160p）</option>',
            page.text,
        )
        self.assertIn(">720p</span>", page.text)

    def test_requested_4k_uses_task_level_height_override(self):
        client = admin_client()
        created = client.post(
            "/api/download-tasks",
            json={
                "output_dir": str(self.download_dir),
                "quality": "4k",
                "urls": ["https://example.com/video/requested-4k"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        task = database.get_download_task(created.json()["task_ids"][0])
        snapshot = json.loads(task["media_policy_snapshot_json"])

        self.assertEqual(task["requested_quality"], "2160p")
        self.assertEqual(snapshot["quality_target"], "2160p")
        self.assertEqual(snapshot["max_height"], 2160)
        self.assertEqual(snapshot["quality_fallback"], "nearest_below")
        persistent = get_effective_policy(
            task["transport_site_key"] or "example.com"
        )
        self.assertEqual(persistent["max_height"], 1080)

    def test_pause_and_resume_api(self):
        client = admin_client()
        created = client.post(
            "/api/download-tasks",
            json={
                "output_dir": str(self.download_dir),
                "urls": ["https://example.com/video/api-pause"],
            },
        ).json()
        task_id = created["task_ids"][0]

        paused = client.post(
            "/api/download-tasks/{}/pause".format(task_id)
        )
        resumed = client.post(
            "/api/download-tasks/{}/resume".format(task_id)
        )

        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["status"], "paused")
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(
            database.get_download_task(task_id)["status"],
            "pending",
        )

    def test_dual_source_api_and_page_show_both_urls(self):
        client = admin_client()
        metadata_url = "https://site-a.example/video/abc"
        download_url = "https://site-b.example/video/123"
        created = client.post(
            "/api/download-tasks",
            json={
                "output_dir": str(self.download_dir),
                "items": [
                    {
                        "metadata_url": metadata_url,
                        "download_url": download_url,
                    }
                ],
            },
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["created"], 1)
        task = database.get_download_task(
            created.json()["task_ids"][0]
        )
        self.assertEqual(task["metadata_url"], metadata_url)
        self.assertEqual(task["url"], download_url)
        page = client.get("/downloads")
        self.assertIn(metadata_url, page.text)
        self.assertIn(download_url, page.text)
        self.assertIn("数据来源 A", page.text)
        self.assertIn("下载来源 B", page.text)

    def test_new_task_dialog_has_normal_and_special_tabs(self):
        page = admin_client().get("/downloads")

        self.assertIn('role="tablist"', page.text)
        self.assertIn(">普通下载</button>", page.text)
        self.assertIn(">特殊下载</button>", page.text)
        self.assertIn('id="download-normal-panel"', page.text)
        self.assertIn('id="download-urls"', page.text)
        self.assertIn('id="download-special-panel"', page.text)
        self.assertIn('data-metadata-url', page.text)
        self.assertIn('data-download-url', page.text)
        self.assertIn(
            'data-download-panel="special"\n        hidden',
            page.text,
        )

    def test_create_api_validates_explicit_download_modes(self):
        client = admin_client()
        normal = client.post(
            "/api/download-tasks",
            json={
                "mode": "normal",
                "output_dir": str(self.download_dir),
                "items": [],
            },
        )
        special = client.post(
            "/api/download-tasks",
            json={
                "mode": "special",
                "output_dir": str(self.download_dir),
                "urls": ["https://example.com/video"],
            },
        )
        invalid_quality = client.post(
            "/api/download-tasks",
            json={
                "mode": "normal",
                "output_dir": str(self.download_dir),
                "quality": "999p",
                "urls": ["https://example.com/video"],
            },
        )

        self.assertEqual(normal.status_code, 422)
        self.assertEqual(special.status_code, 422)
        self.assertEqual(invalid_quality.status_code, 422)


if __name__ == "__main__":
    unittest.main()
