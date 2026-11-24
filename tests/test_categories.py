import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.catalog.categories import rebuild_category_index, tokenize_title
from local_video_site.ingestion.library_scan import scan_library


class CategoryTokenizerTests(unittest.TestCase):
    def test_tokenizes_latin_titles_and_removes_generic_noise(self):
        tokens = tokenize_title("Sofia Smith & Yessica Bunny DAP | Porn")

        self.assertEqual(
            tokens,
            [
                ("Sofia", "sofia"),
                ("Smith", "smith"),
                ("Yessica", "yessica"),
                ("Bunny", "bunny"),
                ("DAP", "dap"),
            ],
        )

    def test_preserves_model_number(self):
        names = [name for name, _ in tokenize_title(
            "SKYHD-096 Sky Angel Blue Vol.96 HD Porn"
        )]

        self.assertIn("SKYHD-096", names)
        self.assertNotIn("Porn", names)
        self.assertNotIn("Vol", names)
        self.assertNotIn("HD", names)

    def test_tokenizes_chinese_and_removes_streaming_noise(self):
        names = [name for name, _ in tokenize_title(
            "巨乳教师在线观看完整版第3集"
        )]

        self.assertIn("巨乳", names)
        self.assertIn("教师", names)
        for noise in ("在线", "观看", "完整", "完整版", "第3集"):
            self.assertNotIn(noise, names)

    def test_merges_case_insensitively_and_deduplicates_one_title(self):
        tokens = tokenize_title("Anal ANAL")

        self.assertEqual(tokens, [("Anal", "anal")])


class CategoryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video_root = self.root / "videos"
        self.video_root.mkdir()
        self.data_dir = self.root / "data"
        self.config_patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
        )
        for item in self.config_patches:
            item.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for item in reversed(self.config_patches):
            item.stop()
        self.temp_dir.cleanup()

    def _create_videos(self):
        for name in (
            "Anal Shared.mp4",
            "Anal Alpha.mp4",
            "Alpha Beta.mp4",
        ):
            (self.video_root / name).write_bytes(b"video")
        return scan_library([self.video_root], minimum_bytes=0)

    def test_categories_are_ordered_searchable_and_filter_videos(self):
        self._create_videos()

        categories = database.list_categories()
        counts = {item["name"]: item["video_count"] for item in categories["items"]}
        alpha = next(item for item in categories["items"] if item["name"] == "Alpha")

        self.assertEqual(categories["total"], 4)
        self.assertEqual(counts, {
            "Alpha": 2,
            "Anal": 2,
            "Beta": 1,
            "Shared": 1,
        })
        self.assertEqual(
            [item["video_count"] for item in categories["items"]],
            [2, 2, 1, 1],
        )
        self.assertEqual(
            database.list_categories(query="aNaL")["items"][0]["name"],
            "Anal",
        )
        filtered = database.list_videos(category_id=alpha["id"], sort="name")
        self.assertEqual(filtered["total"], 2)
        self.assertEqual(
            [item["title"] for item in filtered["items"]],
            ["Alpha Beta", "Anal Alpha"],
        )
        previous, following = database.get_video_neighbors(
            filtered["items"][0]["id"],
            category_id=alpha["id"],
            sort="name",
        )
        self.assertIsNone(previous)
        self.assertEqual(following["id"], filtered["items"][1]["id"])

    def test_missing_videos_are_excluded_from_categories(self):
        self._create_videos()
        (self.video_root / "Anal Shared.mp4").unlink()

        scan_library([self.video_root], minimum_bytes=0)
        categories = database.list_categories()
        counts = {item["name"]: item["video_count"] for item in categories["items"]}

        self.assertEqual(counts["Anal"], 1)
        self.assertNotIn("Shared", counts)

    def test_rebuild_returns_number_of_distinct_categories(self):
        self._create_videos()

        self.assertEqual(rebuild_category_index(), 4)

    def test_rebuild_preserves_soft_deleted_video_relations(self):
        self._create_videos()
        with database.connect() as connection:
            video_id = connection.execute(
                "SELECT id FROM videos WHERE title = 'Anal Shared'"
            ).fetchone()["id"]
        database.delete_video_record(video_id)

        rebuild_category_index()

        with database.connect() as connection:
            relation_count = connection.execute(
                "SELECT COUNT(*) FROM video_categories WHERE video_id = ?",
                (video_id,),
            ).fetchone()[0]
        names = {
            item["name"] for item in database.list_categories()["items"]
        }
        self.assertGreater(relation_count, 0)
        self.assertNotIn("Shared", names)

    def test_out_of_range_page_clamps_to_last_page(self):
        self._create_videos()

        videos = database.list_videos(page=999, page_size=2, sort="name")
        categories = database.list_categories(page=999, page_size=2)

        self.assertEqual((videos["page"], videos["pages"]), (2, 2))
        self.assertEqual(len(videos["items"]), 1)
        self.assertEqual((categories["page"], categories["pages"]), (2, 2))
        self.assertEqual(len(categories["items"]), 2)

    def test_pagination_controls_render_on_all_listing_pages(self):
        pagination = {
            "items": [],
            "total": 100,
            "page": 2,
            "page_size": 36,
            "pages": 4,
            "directories": [],
        }
        database.init_db()
        client = admin_client()

        with patch.object(database, "list_videos", return_value=pagination):
            home = client.get("/")
        with patch.object(database, "list_categories", return_value=pagination):
            category_list = client.get("/categories")
        with (
            patch.object(
                database,
                "get_category",
                return_value={"id": 7, "name": "Anal", "video_count": 100},
            ),
            patch.object(database, "list_videos", return_value=pagination),
        ):
            category_videos = client.get("/categories/7")

        for response in (home, category_list, category_videos):
            self.assertEqual(response.status_code, 200)
            self.assertIn("首页", response.text)
            self.assertIn("尾页", response.text)
            self.assertIn('class="page-selector"', response.text)
            self.assertIn('<select', response.text)
            self.assertIn('name="page"', response.text)
            self.assertIn("data-auto-submit", response.text)
            self.assertIn("/static/pagination.js", response.text)
            self.assertEqual(response.text.count('value="4"'), 1)
            self.assertNotIn('class="pagination-jump"', response.text)
            self.assertNotIn('class="pagination-area"', response.text)

    def test_pages_and_apis_render_category_data(self):
        self._create_videos()
        categories = database.list_categories()
        alpha = next(item for item in categories["items"] if item["name"] == "Alpha")
        client = admin_client()

        home = client.get("/")
        category_page = client.get("/categories", params={"q": "alp"})
        videos_page = client.get("/categories/{}".format(alpha["id"]))
        category_api = client.get("/api/categories")
        videos_api = client.get(
            "/api/videos",
            params={"category_id": alpha["id"], "sort": "name"},
        )

        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/categories"', home.text)
        self.assertIn("分类", home.text)
        self.assertIn('<option value="size"', home.text)
        self.assertIn("视频大小", home.text)
        self.assertNotIn("LOCAL COLLECTION", home.text)
        self.assertEqual(category_page.status_code, 200)
        self.assertIn("Alpha", category_page.text)
        self.assertNotIn("Anal", category_page.text)
        self.assertEqual(videos_page.status_code, 200)
        self.assertEqual(videos_page.text.count("video-card"), 2)
        self.assertEqual(category_api.status_code, 200)
        self.assertEqual(category_api.json()["total"], 4)
        self.assertEqual(videos_api.status_code, 200)
        self.assertEqual(videos_api.json()["total"], 2)

    def test_video_detail_lists_all_categories_with_collection_links(self):
        self._create_videos()
        video = next(
            item
            for item in database.list_videos(sort="name")["items"]
            if item["title"] == "Anal Shared"
        )
        categories = database.categories_for_video(video["id"])
        response = admin_client().get("/watch/{}".format(video["id"]))

        self.assertEqual(
            [category["name"] for category in categories],
            ["Anal", "Shared"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '视频分类<span class="watch-section-count">2</span>',
            response.text,
        )
        for category in categories:
            self.assertIn(
                'href="/categories/{}"'.format(category["id"]),
                response.text,
            )
            self.assertIn(category["name"], response.text)


if __name__ == "__main__":
    unittest.main()
