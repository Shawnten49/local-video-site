import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.integrations.sites.bilibili import metadata_from_payload, publication_id_from_url
from local_video_site.catalog.categories import refresh_site_metadata
from local_video_site.integrations.metadata import fetch_site_metadata, metadata_provider_for_url, SiteMetadataResult
from tests.auth_test_utils import admin_client


def _payload():
    return {
        "code": 0,
        "result": {
            "title": "老友记 第一季",
            "styles": ["都市", "经典", "搞笑", "情感"],
            "actors": (
                "詹妮弗·安妮斯顿\n"
                "柯特妮·考克斯\n"
                "丽莎·库卓\n"
                "马特·勒布朗\n"
                "马修·派瑞\n"
                "大卫·休默\n"
                "詹姆斯·迈克尔·泰勒"
            ),
            "staff": "导演：大卫·克拉尼,玛尔塔·考夫曼",
        },
    }


class BilibiliMetadataTests(unittest.TestCase):
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

    def test_payload_parses_styles_and_actors(self):
        result = metadata_from_payload(_payload(), 200, release_year="1994")

        self.assertEqual(
            [item["name"] for item in result.categories],
            ["都市", "经典", "搞笑", "情感", "1994"],
        )
        self.assertEqual(
            [item["name"] for item in result.actors],
            [
                "詹妮弗·安妮斯顿",
                "柯特妮·考克斯",
                "丽莎·库卓",
                "马特·勒布朗",
                "马修·派瑞",
                "大卫·休默",
                "詹姆斯·迈克尔·泰勒",
            ],
        )
        self.assertNotIn("大卫·克拉尼", str(result.actors))
        self.assertEqual(result.http_status, 200)

    def test_profile_resolves_tracking_url(self):
        url = (
            "https://www.bilibili.com/bangumi/play/"
            "ep468227?from_spmid=666.25.episode.0"
        )
        self.assertEqual(metadata_provider_for_url(url), "bilibili")
        profile = fetch_site_metadata(
            "https://www.bilibili.com/video/BV1example"
        )
        self.assertEqual(profile.error, "不支持的元数据网站")
        self.assertEqual(
            publication_id_from_url(
                "https://www.bilibili.com/bangumi/play/ss28274"
            ),
            ("season_id", "28274"),
        )
        self.assertEqual(
            publication_id_from_url(
                "https://www.bilibili.com/bangumi/play/ep819062"
            ),
            ("ep_id", "819062"),
        )

    def test_child_refresh_writes_shared_season_metadata(self):
        first_path = self.video_root / "001.mp4"
        child_path = self.video_root / "002.mp4"
        first_path.write_bytes(b"first")
        child_path.write_bytes(b"child")
        database.upsert_video(
            first_path,
            "老友记 第一季 001",
            "videos",
            first_path.stat().st_size,
            first_path.stat().st_mtime_ns,
            original_url="https://www.bilibili.com/bangumi/play/ep468227",
        )
        database.upsert_video(
            child_path,
            "老友记 第一季 002",
            "videos",
            child_path.stat().st_size,
            child_path.stat().st_mtime_ns,
            original_url="https://www.bilibili.com/bangumi/play/ep468228",
        )
        first = database.get_video_by_path(first_path)
        child = database.get_video_by_path(child_path)
        season_data = database.upsert_video_season(
            "老友记",
            "第一季",
            1,
            "bilibili",
            "https://www.bilibili.com/bangumi/play/ep468227",
            "老友记/第一季",
        )
        season_id = season_data["season"]["id"]
        for video, number in ((first, 1), (child, 2)):
            episode_id = database.create_video_episode(
                season_id,
                number,
                "老友记 第一季 {:03d}".format(number),
                video["original_url"],
                video["original_url"],
                "老友记/第一季/{:03d}.mp4".format(number),
                video["path"],
                None,
            )
            database.attach_episode_video(
                episode_id,
                video["id"],
                Path(video["path"]),
            )
        parsed = metadata_from_payload(_payload(), 200, release_year="1994")
        result = SiteMetadataResult(
            categories=parsed.categories,
            actors=parsed.actors,
            http_status=parsed.http_status,
            source=parsed.source,
            provider="bilibili",
            title=parsed.title,
        )
        client = admin_client()
        with patch(
            "local_video_site.catalog.categories.fetch_site_metadata",
            return_value=result,
        ) as automatic_fetch:
            report = refresh_site_metadata(
                provider="bilibili",
                refresh=True,
            )
        self.assertEqual(report["candidates"], 1)
        self.assertEqual(automatic_fetch.call_count, 1)

        with patch(
            "local_video_site.web.routes.media.fetch_site_metadata",
            return_value=result,
        ):
            response = client.post(
                "/api/videos/{}/metadata/refresh".format(child["id"])
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["video_id"], first["id"])
        self.assertEqual(
            {
                item["name"]
                for item in database.categories_for_video(first["id"])
            },
            {"都市", "经典", "搞笑", "情感", "1994"},
        )
        self.assertEqual(len(database.actors_for_video(first["id"])), 7)
        self.assertEqual(database.actors_for_video(child["id"]), [])


if __name__ == "__main__":
    unittest.main()
