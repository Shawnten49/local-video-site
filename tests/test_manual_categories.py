import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.catalog.categories import rebuild_actor_index, rebuild_category_index


class ManualCategoryTests(unittest.TestCase):
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

    def _add_video(self, name, title):
        path = self.video_root / name
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title=title,
            directory="videos",
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            original_url="https://example.com/{}".format(name),
        )
        return database.get_video_by_path(path)

    def test_manual_category_create_duplicate_and_api_delete(self):
        client = admin_client()
        created = client.post(
            "/api/manual-categories",
            json={"name": "  剧情  "},
        )
        duplicate = client.post(
            "/api/manual-categories",
            json={"name": "剧情"},
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["name"], "剧情")
        self.assertEqual(duplicate.status_code, 409)
        categories = database.list_manual_categories()
        self.assertEqual(categories[0]["video_count"], 0)

        deleted = client.delete(
            "/api/manual-categories/{}".format(created.json()["id"])
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(database.list_manual_categories(), [])

    def test_soft_deleting_video_preserves_manual_category_relation(self):
        video = self._add_video("one.mp4", "One")
        category = database.create_manual_category("收藏")
        database.set_video_manual_categories(video["id"], [category["id"]])

        self.assertTrue(database.delete_video_record(video["id"]))

        self.assertIsNotNone(database.get_manual_category(category["id"]))
        self.assertEqual(
            database.list_manual_categories()[0]["video_count"],
            0,
        )
        with database.connect() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM video_manual_categories
                WHERE manual_category_id = ?
                """,
                (category["id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_video_manual_categories_do_not_replace_automatic_categories(self):
        video = self._add_video("two.mp4", "Alpha Beta")
        rebuild_category_index(clear=False)
        manual = database.create_manual_category("人工精选")
        database.set_video_manual_categories(video["id"], [manual["id"]])
        rebuild_category_index(clear=False)
        rebuild_actor_index(clear=False)

        automatic = database.categories_for_video(video["id"])
        assigned = database.manual_categories_for_video(video["id"])
        self.assertGreaterEqual(len(automatic), 1)
        self.assertEqual([item["name"] for item in assigned], ["人工精选"])
        self.assertNotIn(
            "人工精选",
            {item["name"] for item in automatic},
        )

    def test_orphan_repair_removes_relations_but_preserves_manual_category(self):
        video = self._add_video("orphan.mp4", "Orphan")
        category = database.create_manual_category("永久保留")
        database.set_video_manual_categories(video["id"], [category["id"]])
        with database.connect() as connection:
            connection.execute("DELETE FROM videos WHERE id = ?", (video["id"],))

        report = database.repair_orphan_video_dependencies()

        self.assertGreaterEqual(report["relations"], 1)
        self.assertIsNotNone(database.get_manual_category(category["id"]))
        self.assertEqual(
            database.list_manual_categories()[0]["video_count"],
            0,
        )

    def test_invalid_manual_category_selection_rolls_back(self):
        video = self._add_video("three.mp4", "Three")
        category = database.create_manual_category("保留")
        database.set_video_manual_categories(video["id"], [category["id"]])

        with self.assertRaisesRegex(ValueError, "不存在"):
            database.set_video_manual_categories(video["id"], [999999])

        assigned = database.manual_categories_for_video(video["id"])
        self.assertEqual([item["id"] for item in assigned], [category["id"]])

    def test_manual_category_delete_is_only_explicit_tag_removal_path(self):
        video = self._add_video("explicit.mp4", "Explicit")
        category = database.create_manual_category("显式删除")
        database.set_video_manual_categories(video["id"], [category["id"]])

        deleted = database.delete_manual_category(category["id"])

        self.assertEqual(deleted["name"], "显式删除")
        self.assertIsNone(database.get_manual_category(category["id"]))
        self.assertTrue(database.get_video(video["id"]))
        with database.connect() as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM video_manual_categories
                WHERE manual_category_id = ?
                """,
                (category["id"],),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_categories_page_and_watch_editor_render_manual_controls(self):
        video = self._add_video("four.mp4", "Four")
        category = database.create_manual_category("人工")
        database.set_video_manual_categories(video["id"], [category["id"]])
        rebuild_category_index(clear=False)
        client = admin_client()

        categories_page = client.get("/categories")
        watch_page = client.get("/watch/{}".format(video["id"]))
        manual_page = client.get(
            "/manual-categories/{}".format(category["id"])
        )

        self.assertIn("人工标签", categories_page.text)
        self.assertIn(
            'id="add-manual-category" type="button">新增</button>',
            categories_page.text,
        )
        self.assertIn(
            'id="toggle-manual-category-delete"',
            categories_page.text,
        )
        self.assertIn(
            'id="manual-category-delete-dialog"',
            categories_page.text,
        )
        self.assertIn(
            'id="confirm-manual-category-delete"',
            categories_page.text,
        )
        self.assertNotIn("人工 · 人工", watch_page.text)
        self.assertIn(">人工</a>", watch_page.text)
        self.assertIn('id="edit-manual-categories"', watch_page.text)
        self.assertIn("自动分类（只读）", watch_page.text)
        self.assertEqual(manual_page.status_code, 200)
        self.assertIn("MANUAL CATEGORY", manual_page.text)

        project_root = Path(__file__).resolve().parents[1]
        styles = (
            project_root / "local_video_site" / "static" / "app.css"
        ).read_text(encoding="utf-8")
        script = (
            project_root
            / "local_video_site"
            / "static"
            / "manual-categories.js"
        ).read_text(encoding="utf-8")
        self.assertIn(
            ".manual-category-chip > button {\n  display: none;",
            styles,
        )
        self.assertIn(".is-delete-mode", styles)
        self.assertIn("classList.toggle(\"is-delete-mode\"", script)
        self.assertIn("deleteDialog.showModal()", script)
        self.assertNotIn("window.confirm(", script)
        self.assertNotIn("window.alert(", script)

    def test_manual_section_is_not_rendered_on_later_pages(self):
        page_two = {
            "items": [],
            "total": 200,
            "page": 2,
            "page_size": 160,
            "pages": 2,
            "directories": [],
        }
        with (
            patch(
                "local_video_site.web.routes.categories.database.list_categories",
                return_value=page_two,
            ),
            patch(
                "local_video_site.web.routes.categories.database.list_manual_categories",
                return_value=[{"id": 1, "name": "人工", "video_count": 0}],
            ),
        ):
            response = admin_client().get("/categories?page=2")

        self.assertNotIn('class="manual-category-section"', response.text)
        self.assertNotIn('id="manual-category-dialog"', response.text)


if __name__ == "__main__":
    unittest.main()
