import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.editing.trim import VideoTrimError, claim_trim_job, create_trim_job, delete_trim_backup, process_trim_job, restore_trim_backup
from tests.auth_test_utils import admin_client


class VideoTrimTests(unittest.TestCase):
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
            patch.object(
                config,
                "playback_remux_disk_reserve_bytes",
                return_value=0,
            ),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.source = self.video_root / "movie.mp4"
        self.source.write_bytes(b"source-video")
        database.upsert_video(
            self.source,
            "Movie",
            "videos",
            self.source.stat().st_size,
            self.source.stat().st_mtime_ns,
        )
        self.video = database.get_video_by_path(self.source)
        database.update_metadata(
            self.video["id"],
            {
                "duration": 120,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            thumbnail_path=None,
        )
        self.video = database.get_video(self.video["id"])

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _options(self):
        return {
            "video_id": self.video["id"],
            "path": str(self.source.resolve()),
            "duration": 120.0,
            "requested_start_seconds": 20.0,
            "previous_keyframe": 19.183,
            "next_keyframe": 21.263,
            "recommended_mode": "after_keyframe",
            "estimated_duration": {
                "after_keyframe": 98.737,
                "before_keyframe": 100.817,
                "precise": 100.0,
            },
            "video_codec": "h264",
            "audio_codec": "aac",
            "width": 1920,
            "height": 1080,
        }

    def _create_job(self):
        with patch(
            "local_video_site.media.editing.trim.probe_trim_options",
            return_value=self._options(),
        ):
            return create_trim_job(
                self.video["id"],
                20,
                "after_keyframe",
                expected_path=str(self.source.resolve()),
                expected_mtime_ns=self.source.stat().st_mtime_ns,
            )

    def _run_successful_trim(self):
        job = self._create_job()
        claimed = claim_trim_job()
        source_info = {
            "duration": 120,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "audio_codec": "aac",
        }
        output_info = {
            **source_info,
            "duration": 98.737,
            "width": 1280,
            "height": 694,
        }

        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(b"trimmed-video")
            return subprocess.CompletedProcess(command, 0, "", "")

        def fake_probe(path):
            return (
                source_info
                if Path(path).resolve() == self.source.resolve()
                else output_info
            )

        with (
            patch(
                "local_video_site.media.editing.trim.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "local_video_site.media.editing.trim.subprocess.run",
                side_effect=fake_run,
            ),
            patch(
                "local_video_site.media.editing.trim.probe_video",
                side_effect=fake_probe,
            ),
            patch(
                "local_video_site.media.editing.trim._frame_dimensions_at",
                return_value={"width": 1280, "height": 694},
            ),
        ):
            return process_trim_job(claimed), source_info

    def test_trim_job_uses_next_keyframe_and_rejects_duplicate(self):
        job = self._create_job()

        self.assertEqual(job["status"], "pending")
        self.assertAlmostEqual(job["actual_cut_seconds"], 21.263)
        with self.assertRaises(VideoTrimError) as raised:
            self._create_job()
        self.assertEqual(raised.exception.code, "trim_busy")

    def test_fast_trim_replaces_source_and_updates_duration(self):
        job = self._create_job()
        claimed = claim_trim_job()
        source_info = {
            "duration": 120,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "audio_codec": "aac",
        }
        output_info = {
            **source_info,
            "duration": 98.737,
            "width": 1280,
            "height": 694,
        }

        def fake_run(command, **kwargs):
            output = Path(command[-1])
            output.write_bytes(b"trimmed-video")
            return subprocess.CompletedProcess(command, 0, "", "")

        def fake_probe(path):
            return (
                source_info
                if Path(path).resolve() == self.source.resolve()
                else output_info
            )

        with (
            patch(
                "local_video_site.media.editing.trim.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "local_video_site.media.editing.trim.subprocess.run",
                side_effect=fake_run,
            ),
            patch(
                "local_video_site.media.editing.trim.probe_video",
                side_effect=fake_probe,
            ),
            patch(
                "local_video_site.media.editing.trim._frame_dimensions_at",
                return_value={"width": 1280, "height": 694},
            ),
        ):
            result = process_trim_job(claimed)

        self.assertEqual(result["status"], "success")
        self.assertEqual(self.source.read_bytes(), b"trimmed-video")
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(result["backup_status"], "available")
        self.assertEqual(backup.read_bytes(), b"source-video")
        self.assertTrue(result["backup_sha256"])
        updated = database.get_video(self.video["id"])
        self.assertEqual(updated["duration"], 98.737)
        self.assertEqual(updated["preview_status"], "pending")

    def test_backup_can_restore_original_video(self):
        result, source_info = self._run_successful_trim()
        backup = Path(result["backup_path"])

        with patch(
            "local_video_site.media.editing.trim.probe_video",
            return_value=source_info,
        ):
            restored = restore_trim_backup(result["job_id"])

        self.assertEqual(self.source.read_bytes(), b"source-video")
        self.assertTrue(backup.is_file())
        self.assertEqual(restored["restore_count"], 1)
        self.assertEqual(restored["backup_status"], "available")
        self.assertEqual(database.get_video(self.video["id"])["duration"], 120)
        page = admin_client().get("/manage/videos/trims")
        self.assertIn('data-restore-trim-backup="{}"'.format(
            result["job_id"]
        ), page.text)
        self.assertIn('data-delete-trim-backup="{}"'.format(
            result["job_id"]
        ), page.text)

    def test_backup_can_be_deleted_without_deleting_trimmed_video(self):
        result, _ = self._run_successful_trim()
        backup = Path(result["backup_path"])

        deleted = delete_trim_backup(result["job_id"])

        self.assertFalse(backup.exists())
        self.assertEqual(deleted["backup_status"], "deleted")
        self.assertEqual(self.source.read_bytes(), b"trimmed-video")

    def test_failed_verification_preserves_original_source(self):
        job = self._create_job()
        claimed = claim_trim_job()
        source_info = {
            "duration": 120,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "audio_codec": "aac",
        }
        bad_output = {**source_info, "duration": 80}

        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(b"bad-trim")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch(
                "local_video_site.media.editing.trim.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "local_video_site.media.editing.trim.subprocess.run",
                side_effect=fake_run,
            ),
            patch(
                "local_video_site.media.editing.trim.probe_video",
                side_effect=lambda path: (
                    source_info
                    if Path(path).resolve() == self.source.resolve()
                    else bad_output
                ),
            ),
            self.assertRaises(VideoTrimError),
        ):
            process_trim_job(claimed)

        self.assertEqual(self.source.read_bytes(), b"source-video")
        with database.connect() as connection:
            failed = connection.execute(
                "SELECT status FROM video_trim_jobs WHERE job_id = ?",
                (job["job_id"],),
            ).fetchone()
        self.assertEqual(failed["status"], "failed")

    def test_management_page_contains_trim_controls(self):
        page = admin_client().get("/manage/videos")

        self.assertEqual(page.status_code, 200)
        self.assertIn('href="/manage/videos/trims"', page.text)
        self.assertIn(
            'data-trim-video="{}"'.format(self.video["id"]),
            page.text,
        )
        self.assertIn('id="trim-video-dialog"', page.text)
        self.assertIn('id="trim-start-input"', page.text)
        self.assertIn("去掉广告优先", page.text)

    def test_trim_jobs_page_supports_search_status_and_pagination(self):
        for index in range(35):
            now = "2026-11-19T01:{:02d}:00+08:00".format(index)
            with database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO video_trim_jobs (
                        job_id, video_id, status, stage, progress_percent,
                        requested_start_seconds, actual_cut_seconds, cut_mode,
                        source_path, source_size, source_mtime_ns,
                        source_duration, error_detail, created_at, updated_at
                    ) VALUES (?, ?, 'failed', 'finished', 70, 20, 21.263,
                              'after_keyframe', ?, 100, 1, 120,
                              '再次验证失败', ?, ?)
                    """,
                    (
                        "history-{}".format(index),
                        self.video["id"],
                        str(self.source),
                        now,
                        now,
                    ),
                )
                connection.commit()

        page = admin_client().get("/manage/videos/trims")
        filtered = admin_client().get(
            "/manage/videos/trims",
            params={"q": str(self.video["id"]), "status": "failed"},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn("视频裁剪", page.text)
        self.assertIn("history-", page.text)
        self.assertIn("/ 2 页", page.text)
        self.assertEqual(filtered.status_code, 200)
        self.assertIn("再次验证失败", filtered.text)


if __name__ == "__main__":
    unittest.main()
