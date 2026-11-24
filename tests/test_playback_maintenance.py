import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from local_video_site.app import app
from local_video_site.media.ffmpeg import FFmpegBusyError
from local_video_site.media.playback.engine import ensure_playback_technical_info, prune_playback_cache
from local_video_site.media.playback.normalize import needs_in_place_normalization, normalize_video, normalize_playback_videos


def box(box_type, payload=b""):
    return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


class PlaybackMaintenanceTests(unittest.TestCase):
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
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _add_video(self, name="video.mp4", fragmented=True):
        path = self.video_root / name
        content = box(b"ftyp") + box(b"moov")
        if fragmented:
            content += box(b"moof") + box(b"mdat", b"video")
        else:
            content += box(b"mdat", b"video")
        path.write_bytes(content)
        database.upsert_video(
            path=path,
            title=path.stem,
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        video = database.get_video(database.list_videos()["items"][0]["id"])
        database.update_metadata(
            video_id=video["id"],
            info={
                "duration": 10,
                "width": 640,
                "height": 360,
                "video_codec": "av1",
                "audio_codec": "aac",
            },
            thumbnail_path=None,
        )
        return database.get_video(video["id"])

    def _add_ts_video(self):
        path = self.video_root / "program.ts"
        path.write_bytes(b"transport-stream")
        database.upsert_video(
            path=path,
            title="Program",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        database.update_metadata(
            video_id=video["id"],
            info={
                "duration": 60,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            thumbnail_path=None,
        )
        return database.get_video(video["id"])

    def _register_cache(self, video, filename, size, created_at):
        cache_dir = config.playback_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / filename
        path.write_bytes(b"x" * size)
        database.register_playback_cache(path, video, size)
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE playback_cache_entries
                SET created_at = ?, last_accessed_at = ?
                WHERE cache_path = ?
                """,
                (created_at, created_at, str(path)),
            )
        return path

    def test_fragmented_av1_is_a_normalization_candidate(self):
        fragmented = self._add_video("fragmented.mp4", fragmented=True)
        standard = self._add_video("standard.mp4", fragmented=False)

        self.assertTrue(needs_in_place_normalization(fragmented))
        self.assertFalse(needs_in_place_normalization(standard))
        report = normalize_playback_videos(dry_run=True)
        self.assertEqual(report["candidates"], 1)
        self.assertEqual(report["items"][0]["video_id"], fragmented["id"])

    def test_transport_stream_is_remuxed_replacing_source_and_cache(self):
        video = self._add_ts_video()
        cache = self._register_cache(
            video,
            "ts-cache.mp4",
            10,
            datetime.now().astimezone().isoformat(),
        )
        source_info = {
            "duration": 60,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "audio_codec": "aac",
        }

        def fake_probe(path):
            return source_info

        def fake_run(command, **kwargs):
            destination = Path(command[-1])
            destination.write_bytes(
                box(b"ftyp") + box(b"moov") + box(b"mdat", b"video")
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch(
                "local_video_site.media.playback.normalize.probe_video",
                side_effect=fake_probe,
            ),
            patch(
                "local_video_site.media.playback.normalize.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "local_video_site.media.playback.normalize.subprocess.run",
                side_effect=fake_run,
            ),
            patch.object(
                config,
                "playback_remux_disk_reserve_bytes",
                return_value=0,
            ),
        ):
            result = normalize_video(video)

        target = self.video_root / "program.mp4"
        updated = database.get_video(video["id"])
        self.assertEqual(result["status"], "ready")
        self.assertFalse((self.video_root / "program.ts").exists())
        self.assertTrue(target.is_file())
        self.assertEqual(updated["path"], str(target.resolve()))
        self.assertEqual(updated["extension"], ".mp4")
        self.assertEqual(updated["normalization_status"], "ready")
        self.assertFalse(cache.exists())
        self.assertEqual(database.list_playback_cache_entries(video["id"]), [])

    def test_fifo_prune_removes_oldest_cache_until_under_limit(self):
        video = self._add_video()
        now = datetime.now().astimezone()
        old = (now - timedelta(days=20)).isoformat()
        older = (now - timedelta(days=21)).isoformat()
        newest = (now - timedelta(days=19)).isoformat()
        first = self._register_cache(video, "first.mp4", 10, older)
        second = self._register_cache(video, "second.mp4", 10, old)
        third = self._register_cache(video, "third.mp4", 10, newest)

        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_PLAYBACK_CACHE_PROTECT_SECONDS": "0"},
        ):
            report = prune_playback_cache(
                max_bytes=20,
                max_age_days=30,
            )

        self.assertEqual(report["removed_files"], 1)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(third.exists())

    def test_zero_max_age_disables_time_based_expiry(self):
        video = self._add_video()
        old = (
            datetime.now().astimezone() - timedelta(days=365)
        ).isoformat()
        path = self._register_cache(
            video,
            "long-lived.mp4",
            10,
            old,
        )

        report = prune_playback_cache(
            max_bytes=100,
            max_age_days=0,
        )

        self.assertEqual(report["removed_files"], 0)
        self.assertTrue(path.exists())

    def test_capacity_evicts_oldest_even_when_recently_protected(self):
        video = self._add_video()
        now = datetime.now().astimezone()
        oldest = self._register_cache(
            video,
            "oldest.mp4",
            15,
            (now - timedelta(days=2)).isoformat(),
        )
        newest = self._register_cache(
            video,
            "newest.mp4",
            15,
            now.isoformat(),
        )

        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_PLAYBACK_CACHE_PROTECT_SECONDS": "999999"},
        ):
            report = prune_playback_cache(
                max_bytes=20,
                max_age_days=0,
            )

        self.assertEqual(report["removed_files"], 1)
        self.assertFalse(oldest.exists())
        self.assertTrue(newest.exists())

    def test_source_change_removes_stale_registered_cache(self):
        video = self._add_video()
        path = self._register_cache(
            video,
            "stale.mp4",
            10,
            datetime.now().astimezone().isoformat(),
        )
        with database.connect() as connection:
            connection.execute(
                "UPDATE videos SET size = size + 1 WHERE id = ?",
                (video["id"],),
            )

        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_PLAYBACK_CACHE_PROTECT_SECONDS": "0"},
        ):
            report = prune_playback_cache(max_bytes=100, max_age_days=30)

        self.assertEqual(report["removed_files"], 1)
        self.assertFalse(path.exists())
        self.assertEqual(database.list_playback_cache_entries(), [])

    def test_h264_cache_is_removed_even_without_normalization(self):
        video = self._add_video("h264.mp4", fragmented=False)
        database.update_metadata(
            video_id=video["id"],
            info={
                "duration": 10,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            thumbnail_path=None,
        )
        video = database.get_video(video["id"])
        path = self._register_cache(
            video,
            "no-longer-needed.mp4",
            10,
            datetime.now().astimezone().isoformat(),
        )

        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_PLAYBACK_CACHE_PROTECT_SECONDS": "0"},
        ):
            report = prune_playback_cache(max_bytes=100, max_age_days=30)

        self.assertEqual(report["removed_files"], 1)
        self.assertFalse(path.exists())
        self.assertEqual(database.list_playback_cache_entries(), [])

    def test_playback_probes_metadata_before_cache_decision(self):
        path = self.video_root / "probe-first.mp4"
        path.write_bytes(box(b"ftyp") + box(b"moov"))
        database.upsert_video(
            path=path,
            title="Probe First",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        video = database.get_video_by_path(path)
        self.assertIsNone(video["video_codec"])

        with patch(
            "local_video_site.media.playback.engine.probe_video",
            return_value={
                "duration": 60,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "audio_codec": "aac",
            },
        ):
            updated = ensure_playback_technical_info({
                **video,
                "path": str(path),
            })

        self.assertEqual(updated["video_codec"], "h264")
        self.assertEqual(
            database.get_video(video["id"])["audio_codec"],
            "aac",
        )

    def test_prepare_api_returns_503_when_ffmpeg_slots_are_full(self):
        video = self._add_video("busy.mp4", fragmented=True)
        with patch(
            "local_video_site.web.routes.playback.prepare_playback_cache",
            side_effect=FFmpegBusyError("busy"),
        ):
            response = TestClient(app).post(
                "/api/prepare/{}".format(video["id"])
            )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers["retry-after"], "5")


if __name__ == "__main__":
    unittest.main()
