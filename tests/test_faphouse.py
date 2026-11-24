import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site import database
from local_video_site.catalog.categories import replace_video_metadata
from local_video_site.core import config
from local_video_site.integrations.metadata import (
    SiteMetadataResult,
    metadata_provider_for_url,
)
from local_video_site.integrations.profiles import (
    resolve_profile_profile,
    resolve_site_profile,
)
from local_video_site.integrations.sites.faphouse import (
    actors_from_payload,
    canonical_faphouse_url,
    categories_from_payload,
)
from tests.auth_test_utils import admin_client


class FaphouseMetadataTests(unittest.TestCase):
    def test_profile_matches_video_and_actor_profiles(self):
        video = (
            "https://faphouse.com/videos/"
            "ryder-brooke-johnson-sex-Df31i6"
        )

        self.assertEqual(metadata_provider_for_url(video), "faphouse")
        profile = resolve_site_profile(video)
        self.assertEqual(profile.key, "faphouse")
        self.assertEqual(profile.metadata_language, "en")
        self.assertEqual(
            resolve_profile_profile(
                "https://faphouse.com/pornstars/rebel-rhyder"
            ).key,
            "faphouse",
        )
        self.assertEqual(
            canonical_faphouse_url(
                "https://zh.faphouse.com/videos/example#fragment"
            ),
            "https://faphouse.com/videos/example",
        )

    def test_related_tags_separate_actors_categories_and_models(self):
        payload = {
            "description": (
                "主演：Rebel Rhyder, FaceFuckingKing, Brooke Johnson。"
                "类别：美国人, 深喉, 粗暴性爱。"
            ),
            "links": [
                {
                    "name": "FaceFuckingKing",
                    "url": "https://zh.faphouse.com/models/facefuckingking",
                },
                {
                    "name": "Rebel Rhyder",
                    "url": (
                        "https://zh.faphouse.com/pornstars/rebel-rhyder"
                    ),
                    "avatar_url": "https://ic.xhcdn.com/rebel.jpg",
                },
                {
                    "name": "Brooke Johnson",
                    "url": (
                        "https://zh.faphouse.com/pornstars/"
                        "brooke-johnson"
                    ),
                    "avatar_url": "https://ic.xhcdn.com/brooke.jpg",
                },
                {
                    "name": "美国人",
                    "url": "https://zh.faphouse.com/c/american/videos",
                },
                {
                    "name": "深喉",
                    "url": "https://zh.faphouse.com/c/deep-throat/videos",
                },
                {
                    "name": "粗暴性爱",
                    "url": "https://zh.faphouse.com/c/rough-sex/videos",
                },
                {
                    "name": "搜索标签",
                    "url": (
                        "https://zh.faphouse.com/search/videos?q=test"
                    ),
                },
            ],
        }

        actors = actors_from_payload(payload)
        categories, source = categories_from_payload(payload)

        self.assertEqual(source, "dom")
        self.assertEqual(
            [item["name"] for item in actors],
            ["Rebel Rhyder", "Brooke Johnson", "FaceFuckingKing"],
        )
        self.assertEqual(actors[0]["slug"], "rebel-rhyder")
        self.assertEqual(
            actors[0]["avatar_url"],
            "https://ic.xhcdn.com/rebel.jpg",
        )
        self.assertEqual(
            [item["name"] for item in categories],
            ["美国人", "深喉", "粗暴性爱"],
        )
        self.assertEqual(categories[1]["slug"], "deep-throat")

    def test_meta_description_is_used_when_dom_links_are_missing(self):
        payload = {
            "links": [],
            "description": (
                "完整版。主演：Rebel Rhyder, FaceFuckingKing, "
                "Brooke Johnson。类别：粗暴肛交, 舔肛, 粗暴深喉口交。"
            ),
        }

        actors = actors_from_payload(payload)
        categories, source = categories_from_payload(payload)

        self.assertEqual(source, "meta")
        self.assertEqual(
            [item["name"] for item in actors],
            ["Rebel Rhyder", "FaceFuckingKing", "Brooke Johnson"],
        )
        self.assertEqual(
            [item["name"] for item in categories],
            ["粗暴肛交", "舔肛", "粗暴深喉口交"],
        )


class FaphouseRefreshApiTests(unittest.TestCase):
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
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_refresh_endpoint_uses_worker_thread_and_saves_metadata(self):
        path = self.video_root / "faphouse.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path,
            "FapHouse video",
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
            original_url=(
                "https://faphouse.com/videos/"
                "ryder-brooke-johnson-sex-Df31i6"
            ),
        )
        video = database.get_video_by_path(path)
        result = SiteMetadataResult(
            categories=[{
                "name": "深喉",
                "slug": "deep-throat",
                "type": "category",
                "url": "https://faphouse.com/c/deep-throat/videos",
            }],
            actors=[{
                "name": "Rebel Rhyder",
                "slug": "rebel-rhyder",
                "url": "https://faphouse.com/pornstars/rebel-rhyder",
            }],
            http_status=200,
            source="dom",
            provider="faphouse",
            language="en",
        )

        with patch(
            "local_video_site.web.routes.media.fetch_site_metadata",
            return_value=result,
        ):
            response = admin_client().post(
                "/api/videos/{}/metadata/refresh".format(video["id"])
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["provider"], "faphouse")
        self.assertEqual(response.json()["actor_count"], 1)
        self.assertEqual(response.json()["category_count"], 1)
        self.assertEqual(
            [item["name"] for item in database.actors_for_video(video["id"])],
            ["Rebel Rhyder"],
        )
        self.assertEqual(
            [
                item["name"]
                for item in database.categories_for_video(video["id"])
            ],
            ["深喉"],
        )
        cache = database.category_fetch_cache_map()[video["id"]]
        self.assertEqual(cache["metadata_language"], "en")

    def test_english_refresh_reuses_slug_instead_of_chinese_category(self):
        path = self.video_root / "locale.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path,
            "Locale video",
            "videos",
            path.stat().st_size,
            path.stat().st_mtime_ns,
            original_url=(
                "https://faphouse.com/videos/"
                "ryder-brooke-johnson-sex-Df31i6"
            ),
        )
        video = database.get_video_by_path(path)
        replace_video_metadata(
            video_id=video["id"],
            categories=[{
                "name": "深喉",
                "slug": "deep-throat",
                "type": "category",
                "url": "https://faphouse.com/c/deep-throat/videos",
            }],
            actors=[],
            provider="faphouse",
            source_url=video["original_url"],
        )
        replace_video_metadata(
            video_id=video["id"],
            categories=[{
                "name": "Deep Throat",
                "slug": "deep-throat",
                "type": "category",
                "url": "https://faphouse.com/c/deep-throat/videos",
            }],
            actors=[],
            provider="faphouse",
            source_url=video["original_url"],
        )

        self.assertEqual(
            [
                item["name"]
                for item in database.categories_for_video(video["id"])
            ],
            ["Deep Throat"],
        )
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM categories
                WHERE source_domain = 'faphouse.com'
                  AND external_slug = 'deep-throat'
                """
            ).fetchall()
        self.assertEqual(
            [row["name"] for row in rows],
            ["Deep Throat"],
        )


if __name__ == "__main__":
    unittest.main()
