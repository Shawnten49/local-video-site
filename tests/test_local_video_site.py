import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.auth_test_utils import admin_client

from local_video_site.core import config
from local_video_site.ingestion import library_scan as scanner
from local_video_site import database
from local_video_site.app import app
from local_video_site.web.formatting import (
    format_bytes,
    format_duration,
    format_resolution,
)
from local_video_site.media.playback.engine import _playback_command, mp4_is_fragmented, playback_cache_reason, playback_cache_path, playback_preparation_mode, requires_playback_cache, requires_transcode
from local_video_site.ingestion.library_scan import repair_missing_thumbnails, scan_library
from local_video_site.media.assets.thumbnails import generate_thumbnail


class LocalVideoSiteTests(unittest.TestCase):
    def test_health_endpoint_identifies_local_video_site(self):
        response = TestClient(app).get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "ok",
            "service": "local-video-site",
            "mode": "local",
        })

    def test_format_helpers(self):
        self.assertEqual(format_duration(3530.9), "58:51")
        self.assertEqual(format_bytes(1024 * 1024), "1.0 MiB")
        self.assertEqual(format_resolution(1920, 1080), "1920×1080 · 1080p")

    def test_scan_incremental_and_missing_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video = root / "videos" / "sample.mp4"
            video.parent.mkdir()
            video.write_bytes(b"video")

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                first = scan_library([root / "videos"], minimum_bytes=0)
                second = scan_library([root / "videos"], minimum_bytes=0)
                page = database.list_videos()

                self.assertEqual(first.added, 1)
                self.assertEqual(second.added, 0)
                self.assertEqual(second.changed, 0)
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["title"], "sample")

                video.unlink()
                third = scan_library([root / "videos"], minimum_bytes=0)
                self.assertEqual(third.missing, 1)
                self.assertEqual(database.list_videos()["total"], 0)

    def test_search_and_directory_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            first_root = root / "videos-one"
            second_root = root / "videos-two"
            first_root.mkdir()
            second_root.mkdir()
            (first_root / "Alpha film.mp4").write_bytes(b"a")
            (second_root / "Beta film.mp4").write_bytes(b"b")

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                scan_library([first_root, second_root], minimum_bytes=0)
                filtered = database.list_videos(
                    query="alpha",
                    directory="videos-one",
                    sort="name",
                )
                home = TestClient(app).get("/?directory=videos-one")

            self.assertEqual(filtered["total"], 1)
            self.assertEqual(filtered["items"][0]["title"], "Alpha film")
            self.assertEqual(filtered["directories"], ["videos-one", "videos-two"])

            self.assertEqual(home.status_code, 200)
            self.assertNotIn('name="directory"', home.text)
            self.assertIn("Alpha film", home.text)
            self.assertIn("Beta film", home.text)

    def test_scan_skips_application_data_and_playback_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "local_video_site" / "data"
            videos = root / "videos"
            cache = data_dir / "playback-cache"
            videos.mkdir()
            cache.mkdir(parents=True)
            (videos / "new.mp4").write_bytes(b"video")
            (cache / "cached.mp4").write_bytes(b"cache")

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                stats = scan_library([root], minimum_bytes=0)
                page = database.list_videos()

            self.assertEqual(stats.added, 1)
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["items"][0]["title"], "new")

    def test_categories_rebuild_only_after_detected_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video_root = root / "videos"
            video_root.mkdir()
            (video_root / "sample.mp4").write_bytes(b"video")

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
                patch(
                    "local_video_site.ingestion.library_scan.rebuild_category_index"
                ) as rebuild,
            ):
                first = scan_library([video_root], minimum_bytes=0)
                second = scan_library([video_root], minimum_bytes=0)

            self.assertTrue(first.categories_rebuilt)
            self.assertFalse(second.categories_rebuilt)
            self.assertEqual(rebuild.call_count, 1)

    def test_scan_lock_rejects_concurrent_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scanner._SCAN_LOCK.acquire()
            try:
                with self.assertRaises(scanner.ScanAlreadyRunning):
                    scan_library([root], minimum_bytes=0)
            finally:
                scanner._SCAN_LOCK.release()

    def test_scan_configuration_environment_switches(self):
        with patch.dict(
            os.environ,
            {
                "LOCAL_VIDEO_SCAN_ON_STARTUP": "0",
                "LOCAL_VIDEO_SCAN_WAIT": "yes",
            },
            clear=False,
        ):
            self.assertFalse(config.scan_on_startup())
            self.assertTrue(config.scan_wait_for_resources())

    def test_scan_status_runtime_persists_and_counts_resources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video_root = root / "videos"
            video_root.mkdir()
            (video_root / "sample.mp4").write_bytes(b"video")
            status_path = data_dir / "scan-status.json"

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
                patch.object(config, "SCAN_STATUS_PATH", status_path),
            ):
                stats = scan_library([video_root], minimum_bytes=0)
                runtime = scanner.ScanRuntime()
                self.assertTrue(runtime.try_start([video_root]))
                runtime.index_complete(stats)
                runtime.resources_complete()
                persisted = scanner.persisted_scan_status()

            self.assertEqual(persisted["state"], "complete")
            self.assertEqual(persisted["stats"]["added"], 1)
            self.assertEqual(persisted["total"], 1)
            self.assertEqual(persisted["metadata_pending"], 1)

    def test_scan_status_api_reports_database_resources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video_root = root / "videos"
            video_root.mkdir()
            (video_root / "sample.mp4").write_bytes(b"video")

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                scan_library([video_root], minimum_bytes=0)
                response = admin_client().get("/api/scan/status")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 1)
            self.assertEqual(response.json()["metadata_pending"], 1)

    def test_scan_ignores_partial_and_small_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            (root / "small.mp4").write_bytes(b"x")
            (root / "partial.mp4.part").write_bytes(b"x" * 100)

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                stats = scan_library([root], minimum_bytes=10)

            self.assertEqual(stats.discovered, 0)

    def test_videos_can_be_sorted_by_file_size_descending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video_root = root / "videos"
            video_root.mkdir()
            (video_root / "small.mp4").write_bytes(b"a")
            (video_root / "large.mp4").write_bytes(b"a" * 3)
            (video_root / "medium.mp4").write_bytes(b"a" * 2)

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                scan_library([video_root], minimum_bytes=0)
                page = database.list_videos(sort="size")

            self.assertEqual(
                [item["title"] for item in page["items"]],
                ["large", "medium", "small"],
            )
            self.assertEqual(
                [item["size"] for item in page["items"]],
                [3, 2, 1],
            )

    def test_neighbor_navigation_crosses_page_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video_root = root / "videos"
            video_root.mkdir()
            for index in range(40):
                (video_root / "item-{:02d}.mp4".format(index)).write_bytes(
                    b"video"
                )

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
            ):
                scan_library([video_root], minimum_bytes=0)
                first_page = database.list_videos(
                    sort="name",
                    page=1,
                    page_size=36,
                )
                second_page = database.list_videos(
                    sort="name",
                    page=2,
                    page_size=36,
                )
                last_first_page = first_page["items"][-1]
                first_second_page = second_page["items"][0]
                second_second_page = second_page["items"][1]
                previous, following = database.get_video_neighbors(
                    last_first_page["id"],
                    sort="name",
                )

                self.assertEqual(previous["id"], first_page["items"][-2]["id"])
                self.assertEqual(following["id"], first_second_page["id"])

                return_to = "/?sort=name&page=1"
                client = TestClient(app)
                first_detail = client.get(
                    "/watch/{}".format(last_first_page["id"]),
                    params={"return_to": return_to},
                )
                second_detail = client.get(
                    "/watch/{}".format(first_second_page["id"]),
                    params={"return_to": return_to},
                )

                self.assertIn(
                    'href="/watch/{}?return_to='
                    '%2F%3Fsort%3Dname%26page%3D1"'.format(
                        first_second_page["id"]
                    ),
                    first_detail.text,
                )
                self.assertIn(
                    'href="/watch/{}?return_to='
                    '%2F%3Fsort%3Dname%26page%3D1"'.format(
                        last_first_page["id"]
                    ),
                    second_detail.text,
                )
                self.assertIn(
                    'href="/watch/{}?return_to='
                    '%2F%3Fsort%3Dname%26page%3D1"'.format(
                        second_second_page["id"]
                    ),
                    second_detail.text,
                )

                direct_detail = client.get(
                    "/watch/{}".format(first_second_page["id"])
                )
                self.assertIn(
                    'href="/watch/{}'.format(last_first_page["id"]),
                    direct_detail.text,
                )
                self.assertIn(
                    'href="/watch/{}'.format(second_second_page["id"]),
                    direct_detail.text,
                )
                self.assertNotIn("没有上一集", direct_detail.text)
                self.assertNotIn("没有下一集", direct_detail.text)

                root_first = client.get(
                    "/watch/{}".format(last_first_page["id"]),
                    params={"return_to": "/"},
                )
                self.assertIn(
                    'href="/watch/{}?return_to=%2F"'.format(
                        first_second_page["id"]
                    ),
                    root_first.text,
                )
                root_second = client.get(
                    "/watch/{}".format(first_second_page["id"]),
                    params={"return_to": "/"},
                )
                self.assertNotIn("没有上一集", root_second.text)
                self.assertNotIn("没有下一集", root_second.text)
                self.assertIn(
                    'href="/watch/{}?return_to=%2F"'.format(
                        second_second_page["id"]
                    ),
                    root_second.text,
                )

    def test_regular_mp4_plays_directly_without_container_cache(self):
        h264 = {
            "id": 1,
            "path": "/tmp/video.mp4",
            "size": 100,
            "mtime_ns": 1,
            "video_codec": "h264",
        }
        av1 = dict(h264, video_codec="av1")
        vp9_mp4 = dict(h264, video_codec="vp9")
        av1_webm = {
            **h264,
            "path": "/tmp/video.webm",
            "video_codec": "av1",
        }
        hevc = dict(h264, video_codec="hevc")
        unknown_mp4 = dict(h264, video_codec=None)
        h264_unsupported_audio = dict(
            h264,
            audio_codec="cook",
        )

        self.assertFalse(requires_playback_cache(h264))
        self.assertFalse(requires_playback_cache(av1))
        self.assertFalse(requires_playback_cache(vp9_mp4))
        self.assertFalse(requires_playback_cache(av1_webm))
        self.assertFalse(requires_playback_cache(hevc))
        self.assertFalse(requires_playback_cache(unknown_mp4))
        self.assertFalse(requires_playback_cache(h264_unsupported_audio))
        self.assertEqual(playback_cache_path(h264).suffix, ".mp4")

    def test_mpegts_disguised_as_mp4_requires_playback_remux(self):
        video = {
            "id": 1,
            "path": "/tmp/video.mp4",
            "size": 100,
            "mtime_ns": 1,
            "video_codec": "h264",
            "audio_codec": "aac",
            "container_format": "mpegts",
        }
        self.assertTrue(requires_playback_cache(video))
        self.assertEqual(
            playback_cache_reason(video),
            "container_mismatch",
        )

    def test_fragmented_av1_mp4_requires_remux(self):
        def box(box_type, payload=b""):
            return (
                (8 + len(payload)).to_bytes(4, "big")
                + box_type
                + payload
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            standard = root / "standard.mp4"
            standard.write_bytes(
                box(b"ftyp") + box(b"moov") + box(b"mdat", b"video")
            )
            fragmented = root / "fragmented.mp4"
            fragmented.write_bytes(
                box(b"ftyp")
                + box(b"moov")
                + box(b"moof")
                + box(b"mdat", b"video")
            )
            base = {
                "id": 1,
                "size": 100,
                "mtime_ns": 1,
                "video_codec": "av1",
                "path": str(standard),
            }

            self.assertFalse(mp4_is_fragmented(standard))
            self.assertTrue(mp4_is_fragmented(fragmented))
            self.assertFalse(requires_playback_cache(base))
            self.assertTrue(requires_playback_cache(
                {**base, "path": str(fragmented)}
            ))

    def test_hevc_mp4_only_caches_when_container_is_fragmented(self):
        def box(box_type, payload=b""):
            return (
                (8 + len(payload)).to_bytes(4, "big")
                + box_type
                + payload
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            standard = root / "standard-hevc.mp4"
            standard.write_bytes(
                box(b"ftyp") + box(b"moov") + box(b"mdat", b"video")
            )
            fragmented = root / "fragmented-hevc.mp4"
            fragmented.write_bytes(
                box(b"ftyp")
                + box(b"moov")
                + box(b"moof")
                + box(b"mdat", b"video")
            )
            base = {
                "id": 1,
                "size": 100,
                "mtime_ns": 1,
                "video_codec": "hevc",
                "audio_codec": "aac",
                "path": str(standard),
            }

            self.assertFalse(requires_playback_cache(base))
            self.assertTrue(requires_playback_cache(
                {**base, "path": str(fragmented)}
            ))
            self.assertEqual(
                playback_cache_reason(
                    {**base, "path": str(fragmented)}
                ),
                "fragmented_mp4",
            )

    def test_rmvb_is_unsupported_without_media_transcoding(self):
        rmvb = {
            "id": 1,
            "path": "/tmp/legacy.rmvb",
            "size": 100,
            "mtime_ns": 1,
            "video_codec": "rv40",
            "audio_codec": "cook",
        }
        self.assertTrue(requires_playback_cache(rmvb))
        self.assertTrue(requires_transcode(rmvb))
        self.assertEqual(playback_preparation_mode(rmvb), "unsupported")

        with self.assertRaises(ValueError):
            _playback_command(
                "ffmpeg",
                Path(rmvb["path"]),
                Path("/tmp/output.mp4"),
                "transcode",
            )

        remux_command = _playback_command(
            "ffmpeg",
            Path("/tmp/input.mkv"),
            Path("/tmp/output.mp4"),
            "remux",
        )
        self.assertIn("copy", remux_command)
        self.assertNotIn("libx264", remux_command)

    def test_missing_thumbnails_are_repaired_without_reindexing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            video = root / "videos" / "sample.mp4"
            video.parent.mkdir()
            video.write_bytes(b"video")
            thumbnail = data_dir / "thumbnails" / "sample.jpg"

            with (
                patch.object(config, "DATA_DIR", data_dir),
                patch.object(config, "DATABASE_PATH", data_dir / "library.db"),
                patch.object(config, "THUMBNAIL_DIR", data_dir / "thumbnails"),
                patch(
                    "local_video_site.ingestion.library_scan.generate_thumbnail",
                    return_value=thumbnail,
                ),
            ):
                scan_library([root / "videos"], minimum_bytes=0)
                database.init_db()
                video_id = database.list_videos()["items"][0]["id"]
                repaired = repair_missing_thumbnails()

                self.assertEqual(repaired, 1)
                self.assertEqual(
                    database.get_video(video_id)["thumbnail_path"],
                    str(thumbnail),
                )

    def test_thumbnail_generation_skips_black_candidate_frame(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            thumbnail_dir = root / "thumbnails"
            calls = []

            def fake_run(command, **kwargs):
                output = Path(command[-1])
                output.parent.mkdir(parents=True, exist_ok=True)
                calls.append(command)
                luminance = 12.0 if len(calls) == 1 else 140.0
                output.write_bytes(b"jpeg")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="lavfi.signalstats.YAVG={}\n".format(luminance),
                    stderr="",
                )

            with (
                patch.object(config, "THUMBNAIL_DIR", thumbnail_dir),
                patch(
                    "local_video_site.media.assets.thumbnails.available_ffmpeg",
                    return_value="/fake/ffmpeg",
                ),
                patch(
                    "local_video_site.media.assets.thumbnails.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                output = generate_thumbnail(
                    video_path=video,
                    size=5,
                    mtime_ns=1,
                    duration=100,
                )

            self.assertEqual(len(calls), 2)
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), b"jpeg")
            self.assertIn("30.000", calls[1])


if __name__ == "__main__":
    unittest.main()
