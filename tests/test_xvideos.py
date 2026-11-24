import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.actors import validate_avatar_url, validate_profile_url
from local_video_site.catalog.categories import rebuild_actor_index, rebuild_category_index, refresh_site_metadata
from local_video_site.integrations.metadata import SiteMetadataResult, metadata_provider_for_url
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.integrations.sites.xvideos import XVIDEOS_PARSER_VERSION, actors_from_payload, categories_from_payload, is_xvideos_url


class XvideosMetadataTests(unittest.TestCase):
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

    def test_video_url_detection_and_provider_selection(self):
        url = (
            "https://www.xvideos.com/video.kktkiub19e0/"
            "only_extreme_double_anal"
        )
        self.assertTrue(is_xvideos_url(url))
        self.assertFalse(is_xvideos_url("https://www.xvideos.com/"))
        self.assertEqual(metadata_provider_for_url(url), "xvideos")
        self.assertEqual(
            metadata_provider_for_url(
                "https://www.xvideos.red/video.iohtimf194b/example"
            ),
            "xvideos",
        )
        self.assertEqual(
            metadata_provider_for_url(
                "https://xhamster.com/videos/example-xh123"
            ),
            "xhamster",
        )
        self.assertTrue(
            validate_profile_url(
                "https://www.xvideos.com/pornstars/may-thai"
            )
        )
        self.assertTrue(
            validate_profile_url(
                "https://www.xvideos.com/models/lana-fever"
            )
        )
        self.assertTrue(
            validate_avatar_url(
                "https://profile-cdn77.xvideos-cdn.com/a/0/pp_big.jpg"
            )
        )
        self.assertFalse(
            validate_avatar_url(
                "https://profile-cdn77.xvideos-cdn.com.evil.test/a.jpg"
            )
        )

    def test_payload_parses_actor_without_follower_count_and_all_tags(self):
        tags = [
            "anal-porn",
            "hardcore-porn",
            "asian-porn",
            "interracial-porn",
            "blowjob-porn",
            "rough-porn",
            "dp-porn",
            "brunette-porn",
            "gangbang-porn",
            "legal-porno",
            "bbc-porn",
            "may-thay",
        ]
        payload = {
            "actorLinks": [
                {
                    "name": "May Thai",
                    "href": "/pornstars/may-thai",
                    "external_id": "50119997",
                },
                {
                    "name": "Lana Fever",
                    "href": "/models/lana-fever",
                    "external_id": "11557979",
                },
                {
                    "name": "Susanne Brend",
                    "href": "/models/susanne-brend",
                    "external_id": "6959289",
                },
                {
                    "name": "May Thai",
                    "href": "/pornstars/may-thai",
                    "external_id": "50119997",
                },
            ],
            "tagLinks": [
                {"name": name, "href": "/tags/" + name}
                for name in tags
            ],
            "videoTags": tags,
            "metaKeywords": (
                "xvideos,xvideos.com,x videos,porn,video,videos,"
                + ",".join(tags)
            ),
        }

        categories, source = categories_from_payload(payload)
        actors = actors_from_payload(payload)

        self.assertEqual(source, "initials")
        self.assertEqual(len(categories), 12)
        self.assertEqual(
            {item["name"] for item in categories},
            set(tags),
        )
        self.assertEqual(len(actors), 3)
        self.assertEqual(actors[0]["name"], "May Thai")
        self.assertEqual(actors[0]["external_id"], 50119997)
        self.assertEqual(actors[0]["slug"], "may-thai")
        self.assertEqual(
            actors[1]["url"],
            "https://www.xvideos.com/models/lana-fever",
        )
        self.assertEqual(actors[2]["external_id"], 6959289)

    def test_xvideos_cache_rebuilds_official_categories_and_actors(self):
        path = self.video_root / "xvideos.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title="Fallback title",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            original_url=(
                "https://www.xvideos.com/video.kktkiub19e0/example"
            ),
        )
        video = database.list_videos()["items"][0]
        database.save_category_fetch_cache(
            video_id=video["id"],
            source_url=video["original_url"],
            status="fetched",
            parser_version=XVIDEOS_PARSER_VERSION,
            categories=[
                {
                    "name": "anal-porn",
                    "slug": "anal-porn",
                    "type": "tag",
                    "url": "https://www.xvideos.com/tags/anal-porn",
                }
            ],
            actors=[
                {
                    "name": "May Thai",
                    "slug": "may-thai",
                    "url": "https://www.xvideos.com/pornstars/may-thai",
                    "external_id": 50119997,
                    "avatar_url": "https://profile-cdn77.xvideos-cdn.com/a.jpg",
                }
            ],
        )

        rebuild_category_index(clear=False)
        rebuild_actor_index(clear=False)
        categories = database.categories_for_video(video["id"])
        actors = database.actors_for_video(video["id"])

        self.assertEqual([item["name"] for item in categories], ["anal-porn"])
        self.assertEqual(categories[0]["source_kind"], "xvideos_tag")
        self.assertEqual([item["name"] for item in actors], ["May Thai"])
        self.assertEqual(actors[0]["source_domain"], "xvideos.com")
        self.assertEqual(actors[0]["external_slug"], "may-thai")

    def test_missing_xvideos_metadata_refresh_saves_cache_and_relations(self):
        path = self.video_root / "xvideos-refresh.mp4"
        path.write_bytes(b"video")
        source_url = "https://www.xvideos.com/video.refresh123/example"
        database.upsert_video(
            path=path,
            title="Refresh Example",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            original_url=source_url,
        )
        video = database.list_videos()["items"][0]
        result = SiteMetadataResult(
            categories=[
                {
                    "name": "anal-porn",
                    "slug": "anal-porn",
                    "type": "tag",
                    "url": "https://www.xvideos.com/tags/anal-porn",
                }
            ],
            actors=[
                {
                    "name": "May Thai",
                    "slug": "may-thai",
                    "url": "https://www.xvideos.com/pornstars/may-thai",
                    "external_id": 50119997,
                }
            ],
            http_status=200,
            source="initials",
            provider="xvideos",
        )

        with patch(
            "local_video_site.catalog.categories.fetch_site_metadata",
            return_value=result,
        ):
            report = refresh_site_metadata(provider="xvideos")

        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(report["xvideos"], 1)
        self.assertEqual(
            [item["name"] for item in database.categories_for_video(video["id"])],
            ["anal-porn"],
        )
        self.assertEqual(
            [item["name"] for item in database.actors_for_video(video["id"])],
            ["May Thai"],
        )

    def test_manual_refresh_replaces_metadata_only_after_success(self):
        path = self.video_root / "xvideos-api-refresh.mp4"
        path.write_bytes(b"video")
        source_url = "https://www.xvideos.com/video.apirefresh123/example"
        database.upsert_video(
            path=path,
            title="API Refresh Example",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            original_url=source_url,
        )
        video = database.list_videos()["items"][0]
        database.save_category_fetch_cache(
            video_id=video["id"],
            source_url=source_url,
            status="fetched",
            parser_version=XVIDEOS_PARSER_VERSION,
            categories=[
                {
                    "name": "old-category",
                    "slug": "old-category",
                    "type": "tag",
                    "url": "https://www.xvideos.com/tags/old-category",
                }
            ],
            actors=[
                {
                    "name": "Old Actor",
                    "slug": "old-actor",
                    "url": "https://www.xvideos.com/pornstars/old-actor",
                }
            ],
        )
        rebuild_category_index(clear=False)
        rebuild_actor_index(clear=False)
        refreshed = SiteMetadataResult(
            categories=[
                {
                    "name": "new-category",
                    "slug": "new-category",
                    "type": "tag",
                    "url": "https://www.xvideos.com/tags/new-category",
                }
            ],
            actors=[
                {
                    "name": "New Actor",
                    "slug": "new-actor",
                    "url": "https://www.xvideos.com/pornstars/new-actor",
                }
            ],
            http_status=200,
            source="dom",
            provider="xvideos",
        )
        client = admin_client()

        with patch(
            "local_video_site.web.routes.media.fetch_site_metadata",
            return_value=refreshed,
        ):
            response = client.post(
                "/api/videos/{}/metadata/refresh".format(video["id"])
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["actor_count"], 1)
        self.assertEqual(payload["category_count"], 1)
        self.assertEqual(
            [item["name"] for item in database.actors_for_video(video["id"])],
            ["New Actor"],
        )
        self.assertEqual(
            [
                item["name"]
                for item in database.categories_for_video(video["id"])
            ],
            ["new-category"],
        )

        failed = SiteMetadataResult(
            categories=[],
            actors=[],
            http_status=403,
            source="dom",
            error="blocked",
            provider="xvideos",
        )
        with patch(
            "local_video_site.web.routes.media.fetch_site_metadata",
            return_value=failed,
        ):
            failed_response = client.post(
                "/api/videos/{}/metadata/refresh".format(video["id"])
            )

        self.assertEqual(failed_response.status_code, 502)
        self.assertEqual(
            [item["name"] for item in database.actors_for_video(video["id"])],
            ["New Actor"],
        )
        self.assertEqual(
            [
                item["name"]
                for item in database.categories_for_video(video["id"])
            ],
            ["new-category"],
        )

    def test_watch_page_contains_refresh_metadata_control(self):
        path = self.video_root / "watch-refresh.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title="Watch Refresh",
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            original_url="https://www.xvideos.com/video.watchrefresh/example",
        )
        video = database.list_videos()["items"][0]

        response = admin_client().get("/watch/{}".format(video["id"]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="refresh-video-metadata"', response.text)
        self.assertIn("重新获取演员和分类信息", response.text)
        self.assertIn("/static/refresh-video-metadata.js", response.text)
        self.assertIn('class="watch-source-actions"', response.text)
        self.assertIn(
            'id="metadata-refresh-confirm-dialog"',
            response.text,
        )
        self.assertIn(
            'id="confirm-metadata-refresh"',
            response.text,
        )
        self.assertLess(
            response.text.index('class="watch-source-actions"'),
            response.text.index('id="refresh-video-metadata"'),
        )
        self.assertNotIn("<strong>演员与分类信息</strong>", response.text)
        script = (
            Path(__file__).resolve().parents[1]
            / "local_video_site"
            / "static"
            / "refresh-video-metadata.js"
        ).read_text(encoding="utf-8")
        self.assertIn("confirmDialog.showModal()", script)
        self.assertNotIn("window.confirm(", script)


if __name__ == "__main__":
    unittest.main()
