import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site.storage import migration_tasks
from local_video_site import database
from local_video_site.web.routes.downloads import (
    _task_matches_filters as _download_task_matches_filters,
)
from local_video_site.core.search import MAX_SQLITE_INTEGER, parse_search_query


class SearchQueryParserTests(unittest.TestCase):
    def test_pure_digits_are_an_exact_integer_id(self):
        for value in ("123", " 123 ", "000123"):
            with self.subTest(value=value):
                parsed = parse_search_query(value)
                self.assertTrue(parsed.is_id)
                self.assertEqual(parsed.id_value, 123)

    def test_text_containing_digits_is_not_an_id(self):
        for value in ("123abc", "abc123", "id:123", "-123", "+123"):
            with self.subTest(value=value):
                parsed = parse_search_query(value)
                self.assertFalse(parsed.is_id)
                self.assertIsNone(parsed.id_value)

    def test_out_of_range_numeric_input_stays_an_id_query(self):
        parsed = parse_search_query(str(MAX_SQLITE_INTEGER + 1))
        self.assertTrue(parsed.is_id)
        self.assertIsNone(parsed.id_value)

    def test_empty_input_is_not_a_query(self):
        parsed = parse_search_query("   ")
        self.assertEqual(parsed.raw, "")
        self.assertFalse(parsed.is_id)
        self.assertIsNone(parsed.id_value)


class SearchQueryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.video_root = self.root / "download-hub"
        self.external_root = self.root / "external"
        self.video_root.mkdir()
        self.external_root.mkdir()
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
            directory=self.video_root.name,
            size=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
        )
        return database.get_video_by_path(path)

    def test_admin_video_numeric_query_only_matches_exact_id(self):
        first = self._add_video("alpha.mp4", "Alpha")
        second = self._add_video("episode-1.mp4", "Episode 1")

        exact = database.admin_video_rows(query=str(first["id"]))
        padded = database.admin_video_rows(query="000{}".format(first["id"]))
        text = database.admin_video_rows(query="Episode")
        missing = database.admin_video_rows(query="999999")

        self.assertEqual([item["id"] for item in exact], [first["id"]])
        self.assertEqual([item["id"] for item in padded], [first["id"]])
        self.assertEqual([item["id"] for item in text], [second["id"]])
        self.assertEqual(missing, [])

    def test_admin_video_page_filters_before_pagination(self):
        videos = [
            self._add_video(
                "page-{}.mp4".format(index),
                "Page Video {}".format(index),
            )
            for index in range(5)
        ]
        database.set_video_access_level(videos[-1]["id"], 2)

        first_page = database.admin_video_page(page=1, page_size=2)
        level_two = database.admin_video_page(
            access_level=2,
            page=1,
            page_size=2,
        )
        deleted = database.admin_video_page(
            record_state="deleted",
            page=1,
            page_size=2,
        )

        self.assertEqual(first_page["total"], 5)
        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(first_page["pages"], 3)
        self.assertEqual(level_two["total"], 1)
        self.assertEqual(level_two["items"][0]["id"], videos[-1]["id"])
        self.assertEqual(deleted["total"], 0)

    def test_download_numeric_query_only_matches_exact_task_id(self):
        first_id = database.create_download_task(
            batch_id="first",
            url="https://example.com/alpha",
            normalized_url="https://example.com/alpha",
            metadata_url="https://example.com/alpha",
            normalized_metadata_url="https://example.com/alpha",
            output_dir=str(self.video_root),
            max_attempts=3,
        )
        second_id = database.create_download_task(
            batch_id="second",
            url="https://example.com/episode-1",
            normalized_url="https://example.com/episode-1",
            metadata_url="https://example.com/episode-1",
            normalized_metadata_url="https://example.com/episode-1",
            output_dir=str(self.video_root),
            max_attempts=3,
        )
        database.update_download_task(
            second_id,
            title="Episode {}".format(first_id),
        )

        exact = database.list_download_tasks(query=str(first_id))
        text = database.list_download_tasks(query="Episode")
        second = database.get_download_task(second_id)

        self.assertEqual(
            [item["id"] for item in exact["items"]],
            [first_id],
        )
        self.assertEqual(
            [item["id"] for item in text["items"]],
            [second_id],
        )
        self.assertFalse(
            _download_task_matches_filters(
                second,
                str(first_id),
                "",
            )
        )

    def test_migration_numeric_query_only_matches_migration_or_video_id(self):
        first = self._add_video("alpha.mp4", "Alpha")
        second = self._add_video("episode-1.mp4", "Episode 1")
        for video in (first, second):
            database.update_metadata(
                video["id"],
                {
                    "duration": 60,
                    "width": 1280,
                    "height": 720,
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
                thumbnail_path=None,
            )
            database.save_preview_result(video["id"], "ready")
        migration_tasks.create_batch(
            mode="manual",
            direction="to_external",
            destination_root=str(self.external_root),
            video_ids=[first["id"], second["id"]],
        )

        matches = migration_tasks.list_migrations(
            query=str(second["id"]),
            page_size=10,
        )
        text_matches = migration_tasks.list_migrations(
            query="Episode",
            page_size=10,
        )

        self.assertEqual(matches["total"], 1)
        self.assertEqual(matches["items"][0]["video_id"], second["id"])
        self.assertEqual(text_matches["total"], 1)
        self.assertEqual(text_matches["items"][0]["video_id"], second["id"])


if __name__ == "__main__":
    unittest.main()
