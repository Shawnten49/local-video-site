import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.app import app
from local_video_site.core import config
from local_video_site import database
from local_video_site.media import subtitles
from tests.auth_test_utils import admin_client


class SubtitleAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "videos"
        self.subtitle_dir = self.data_dir / "subtitles"
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
            patch.object(config, "COVER_DIR", self.data_dir / "covers"),
            patch.object(config, "PREVIEW_DIR", self.data_dir / "previews"),
            patch.object(config, "SUBTITLE_DIR", self.subtitle_dir),
            patch.object(config, "DEFAULT_ROOTS", [self.video_root]),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _video(self):
        path = self.video_root / "episode.mkv"
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title="Episode",
            directory=self.video_root.name,
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        return database.get_video_by_path(path)

    def test_extracts_text_and_bitmap_subtitles_with_default_track(self):
        video = self._video()
        streams = [
            {
                "index": 2,
                "codec": "hdmv_pgs_subtitle",
                "language": "chi",
                "title": "English PGS",
                "default": False,
            },
            {
                "index": 3,
                "codec": "ass",
                "language": "chi",
                "title": "中英双语 ASS",
                "default": False,
            },
            {
                "index": 4,
                "codec": "subrip",
                "language": "chi",
                "title": "简体中文 SRT",
                "default": False,
            },
        ]

        def fake_extract(ffmpeg, source, stream, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                "WEBVTT\n\n"
                "00:05.170 --> 00:08.290\n字幕一\n\n"
                "01:00:28.620 --> 01:00:29.670\n字幕二\n"
                if destination.suffix == ".vtt"
                else "PGS",
                encoding="utf-8",
            )
            if destination.suffix == ".vtt":
                subtitles.normalize_webvtt_timestamps(destination)

        with (
            patch(
                "local_video_site.media.subtitles.probe_subtitle_streams",
                return_value=streams,
            ),
            patch(
                "local_video_site.media.subtitles.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "local_video_site.media.subtitles._extract_one",
                side_effect=fake_extract,
            ),
        ):
            result = subtitles.extract_subtitle_assets(
                video["id"],
                Path(video["path"]),
            )

        self.assertEqual(len(result["assets"]), 3)
        assets = database.list_video_subtitle_assets(video["id"])
        playback_assets = database.list_video_subtitle_assets(
            video["id"],
            playback_only=True,
        )
        self.assertEqual(len(assets), 3)
        self.assertEqual(len(playback_assets), 2)
        self.assertEqual(playback_assets[0]["title"], "中英双语 ASS")
        self.assertTrue(playback_assets[0]["default_track"])
        vtt_text = Path(playback_assets[0]["storage_path"]).read_text(
            encoding="utf-8"
        )
        self.assertIn("00:00:05.170 --> 00:00:08.290", vtt_text)
        self.assertIn(
            "01:00:28.620 --> 01:00:29.670",
            vtt_text,
        )
        page = admin_client().get("/watch/{}".format(video["id"]))
        languages = re.findall(r'srclang="([^"]+)"', page.text)
        self.assertEqual(len(languages), 2)
        self.assertEqual(len(set(languages)), 2)

    def test_prefers_bilingual_then_chinese_then_other_text_tracks(self):
        streams = [
            {
                "index": 1,
                "codec": "subrip",
                "language": "eng",
                "title": "English",
            },
            {
                "index": 2,
                "codec": "subrip",
                "language": "chi",
                "title": "简体中文",
            },
            {
                "index": 3,
                "codec": "ass",
                "language": "chi",
                "title": "中下英上",
            },
        ]
        self.assertEqual(
            subtitles.preferred_subtitle_stream(streams)["index"],
            3,
        )
        self.assertEqual(
            subtitles.preferred_subtitle_stream(streams[:2])["index"],
            2,
        )
        self.assertEqual(
            subtitles.preferred_subtitle_stream([streams[0]])["index"],
            1,
        )

    def test_subtitle_endpoint_serves_vtt_and_checks_access(self):
        video = self._video()
        vtt = self.subtitle_dir / str(video["id"]) / "stream-001-zh-Hans.vtt"
        vtt.parent.mkdir(parents=True)
        vtt.write_text(
            "WEBVTT\n\n00:00.000 --> 00:01.000\n字幕\n",
            encoding="utf-8",
        )
        asset = database.upsert_subtitle_asset(
            video_id=video["id"],
            source_stream_index=1,
            language="zh-Hans",
            title="简体中文",
            extension="vtt",
            storage_path=vtt,
            source_size=10,
            source_mtime_ns=20,
            default_track=True,
        )

        response = TestClient(app).get("/subtitle/{}".format(asset["id"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/vtt", response.headers["content-type"])
        self.assertIn("WEBVTT", response.text)

        page = admin_client().get("/watch/{}".format(video["id"]))
        self.assertEqual(page.status_code, 200)
        self.assertIn("/subtitle/{}".format(asset["id"]), page.text)
        self.assertIn("简体中文", page.text)
        self.assertIn(
            'srclang="zh-Hans-x-s{}"'.format(asset["id"]),
            page.text,
        )
        self.assertIn('subtitlesize="0.72"', page.text)
        self.assertIn('subtitlebg="0"', page.text)
        self.assertIn('subtitleedge="none"', page.text)


if __name__ == "__main__":
    unittest.main()
