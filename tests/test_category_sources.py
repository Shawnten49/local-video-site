import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.categories import fetch_xhamster_categories, rebuild_category_index
from local_video_site.integrations.sites.xhamster import FetchResult, actors_from_payload, categories_from_payload, deduplicate_categories, is_xhamster_url, parse_meta_categories


class CategorySourceTests(unittest.TestCase):
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
            patch.object(config, "LOG_PATH", self.data_dir / "logs" / "server.log"),
            patch.object(config, "SCAN_STATUS_PATH", self.data_dir / "scan.json"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _add_video(self, name, title, url):
        path = self.video_root / name
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title=title,
            directory="videos",
            size=5,
            mtime_ns=1,
            original_url=url,
        )
        return database.list_videos()["items"][0]["id"]

    def test_schema_contains_category_source_fields(self):
        with database.connect() as connection:
            category_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info('categories')")
            }
            relation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info('video_categories')"
                )
            }
            cache_table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='category_fetch_cache'"
            ).fetchone()
            cache_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info('category_fetch_cache')"
                )
            }
            actor_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info('actors')")
            }

        self.assertIn("external_slug", category_columns)
        self.assertIn("external_type", category_columns)
        self.assertIn("source_domain", category_columns)
        self.assertIn("source_kind", relation_columns)
        self.assertIn("source_url", relation_columns)
        self.assertIn("parser_version", cache_columns)
        self.assertIn("metadata_language", cache_columns)
        self.assertIn("actor_metadata_version", cache_columns)
        self.assertIn("external_id", actor_columns)
        self.assertIn("avatar_source_url", actor_columns)
        self.assertIn("avatar_path", actor_columns)
        self.assertIn("avatar_status", actor_columns)
        self.assertIsNotNone(cache_table)

    def test_official_categories_take_priority_with_explicit_fallback(self):
        official_id = self._add_video(
            "official.mp4",
            "Title words should not appear",
            "https://xhamster.com/videos/example-xhABC",
        )
        fallback_id = self._add_video(
            "fallback.mp4",
            "Fallback Words",
            "https://xhamster.com/videos/fallback-xhDEF",
        )
        other_id = self._add_video(
            "other.mp4",
            "Other Domain Words",
            "https://www.porntrex.com/video/1/example",
        )
        database.save_category_fetch_cache(
            video_id=official_id,
            source_url="https://xhamster.com/videos/example-xhABC",
            status="fetched",
            categories=[
                {
                    "name": "Group Sex",
                    "slug": "group-sex",
                    "url": "https://xhamster.com/categories/group-sex",
                },
                {
                    "name": "English",
                    "slug": "en",
                    "url": "https://xhamster.com/categories/en",
                },
            ],
            http_status=200,
        )

        count = rebuild_category_index(clear=True)
        categories = database.list_categories()["items"]
        names = {item["name"] for item in categories}
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT video_id, source_kind, source_url
                FROM video_categories
                WHERE video_id IN (?, ?, ?)
                """,
                (official_id, fallback_id, other_id),
            ).fetchall()

        self.assertEqual(count, 6)
        self.assertEqual(names, {
            "Group Sex",
            "English",
            "Fallback",
            "Words",
            "Other",
            "Domain",
        })
        source_kinds = {row["video_id"]: row["source_kind"] for row in rows}
        self.assertEqual(source_kinds[official_id], "xhamster")
        self.assertEqual(source_kinds[fallback_id], "xhamster_fallback")
        self.assertEqual(source_kinds[other_id], "title")
        group = next(item for item in categories if item["name"] == "Group Sex")
        self.assertGreater(group["video_count"], 0)

    def test_successful_fetch_is_cached(self):
        video_id = self._add_video(
            "cached.mp4",
            "Cached",
            "https://xhamster.com/videos/cached-xhABC",
        )

        class FakeFetcher:
            calls = []

            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def fetch(self, url):
                self.calls.append(url)
                return FetchResult(
                    [{
                        "name": "Official",
                        "slug": "official",
                        "url": "https://xhamster.com/categories/official",
                    }],
                    200,
                )

            def fetch_many(self, urls, concurrency=1):
                return [self.fetch(url) for url in urls]

        with (
            patch(
                "local_video_site.catalog.categories.XHamsterCategoryFetcher",
                FakeFetcher,
            ),
            patch("local_video_site.catalog.categories.time.sleep"),
        ):
            report = fetch_xhamster_categories(refresh=True)

        cache = database.category_fetch_cache_map()[video_id]
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(cache["status"], "fetched")
        self.assertEqual(cache["categories"][0]["slug"], "official")
        self.assertEqual(
            cache["parser_version"],
            database.CATEGORY_PARSER_VERSION,
        )

    def test_outdated_parser_cache_is_refetched_even_without_refresh(self):
        video_id = self._add_video(
            "outdated.mp4",
            "Outdated Cache",
            "https://xhamster.com/videos/outdated-xhOLD",
        )
        database.save_category_fetch_cache(
            video_id=video_id,
            source_url="https://xhamster.com/videos/outdated-xhOLD",
            status="fetched",
            categories=[{
                "name": "Partial",
                "slug": "partial",
                "url": "https://xhamster.com/categories/partial",
            }],
            actors=[],
            http_status=200,
            parser_version=1,
        )

        class FakeFetcher:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def fetch(self, url):
                return FetchResult([], 200)

            def fetch_many(self, urls, concurrency=1):
                return [
                    FetchResult(
                        [{
                            "name": "Complete",
                            "slug": "complete",
                            "type": "category",
                            "url": (
                                "https://xhamster.com/categories/complete"
                            ),
                        }],
                        200,
                        source="state",
                        actors=[{
                            "name": "Gina Gerson",
                            "slug": "gina-gerson",
                            "url": (
                                "https://xhamster.com/pornstars/gina-gerson"
                            ),
                        }],
                    )
                    for url in urls
                ]

        with (
            patch(
                "local_video_site.catalog.categories.XHamsterCategoryFetcher",
                FakeFetcher,
            ),
            patch("local_video_site.catalog.categories.time.sleep"),
        ):
            report = fetch_xhamster_categories()

        cache = database.category_fetch_cache_map()[video_id]
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(cache["categories"][0]["name"], "Complete")
        self.assertEqual(cache["actors"][0]["name"], "Gina Gerson")
        self.assertEqual(
            cache["parser_version"],
            database.CATEGORY_PARSER_VERSION,
        )

    def test_domain_and_meta_parser(self):
        self.assertTrue(is_xhamster_url(
            "https://xhamster.com/videos/example-xhABC"
        ))
        self.assertTrue(is_xhamster_url(
            "https://www.xhamster.com/videos/example-xhABC"
        ))
        self.assertFalse(is_xhamster_url(
            "https://fakexhamster.com/videos/example"
        ))
        from local_video_site.integrations.sites.xhamster import categories_from_payload
        categories, source = categories_from_payload({
            "links": [{
                "name": "Anal",
                "url": "https://xhamster.com/shemale/categories/anal",
            }],
            "description": "",
        })
        self.assertEqual(source, "dom")
        self.assertEqual(categories[0]["slug"], "anal")
        categories = parse_meta_categories(
            "Description. Category: Foursome, English, Group Sex."
        )
        self.assertEqual(
            [item["name"] for item in categories],
            ["Foursome", "English", "Group Sex"],
        )
        self.assertEqual(
            deduplicate_categories(categories + categories),
            categories,
        )

    def test_state_payload_keeps_categories_tags_hd_and_clean_actors(self):
        payload = {
            "stateTags": [
                {
                    "name": "Anal",
                    "slug": "anal",
                    "url": "/categories/anal",
                    "isCategory": True,
                },
                {
                    "name": "Teen Anal",
                    "slug": "teen-anal",
                    "url": "/tags/teen-anal",
                    "isTag": True,
                },
                {
                    "name": "HD Videos",
                    "slug": "hd",
                    "url": "/hd",
                    "isCategory": True,
                },
                {
                    "name": "Romance with Love",
                    "slug": "romance-with-love",
                    "url": "/channels/romance-with-love",
                    "isChannel": True,
                },
                {
                    "name": "Gina Gerson",
                    "id": 14268,
                    "slug": "gina-gerson",
                    "url": "/pornstars/gina-gerson",
                    "thumbUrl": (
                        "https://ic-tt-nss.xhcdn.com/example/avatar1.webp"
                    ),
                    "isPornstar": True,
                },
            ],
            "links": [{
                "name": "Blowjob",
                "url": "https://xhamster.com/categories/blowjob",
            }],
            "actorLinks": [{
                "name": "G Gina Gerson",
                "url": "https://xhamster.com/pornstars/gina-gerson",
            }],
            "description": "Description. Category: Facial.",
        }

        categories, source = categories_from_payload(payload)
        actors = actors_from_payload(payload)
        by_name = {item["name"]: item for item in categories}

        self.assertEqual(source, "state")
        self.assertEqual(len(categories), 5)
        self.assertEqual(
            set(by_name),
            {"Anal", "Teen Anal", "HD Videos", "Blowjob", "Facial"},
        )
        self.assertEqual(by_name["Anal"]["type"], "category")
        self.assertEqual(by_name["Teen Anal"]["type"], "tag")
        self.assertEqual(by_name["HD Videos"]["type"], "special")
        self.assertEqual(
            by_name["Teen Anal"]["url"],
            "https://xhamster.com/tags/teen-anal",
        )
        self.assertEqual(actors, [{
            "name": "Gina Gerson",
            "slug": "gina-gerson",
            "url": "https://xhamster.com/pornstars/gina-gerson",
            "external_id": 14268,
            "avatar_url": (
                "https://ic-tt-nss.xhcdn.com/example/avatar1.webp"
            ),
        }])

    def test_tag_links_are_extracted_with_tag_type(self):
        categories, _ = categories_from_payload({
            "links": [{
                "name": "Teen Anal",
                "url": "https://xhamster.com/shemale/tags/teen-anal",
            }],
            "description": "",
        })

        self.assertEqual(categories[0]["type"], "tag")
        self.assertEqual(categories[0]["slug"], "teen-anal")
        self.assertEqual(
            categories[0]["url"],
            "https://xhamster.com/tags/teen-anal",
        )


if __name__ == "__main__":
    unittest.main()
