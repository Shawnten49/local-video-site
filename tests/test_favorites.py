import html
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client, user_client
from local_video_site.app import app
from local_video_site.web.urls import safe_return_path as _safe_return_path
from local_video_site.ingestion.library_scan import scan_library


class FavoriteTests(unittest.TestCase):
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
        for name in ("Favorite One.mp4", "Favorite Two.mp4", "Other.mp4"):
            (self.video_root / name).write_bytes(b"video")
        scan_library([self.video_root], minimum_bytes=0)
        self.videos = {
            item["title"]: item
            for item in database.list_videos(sort="name")["items"]
        }

    def _stop_patches(self):
        for item in reversed(self.config_patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_add_and_remove_are_idempotent(self):
        video_id = self.videos["Favorite One"]["id"]

        self.assertTrue(database.add_favorite(video_id))
        self.assertFalse(database.add_favorite(video_id))
        self.assertTrue(database.is_favorite(video_id))
        self.assertEqual(database.list_videos(favorite_only=True)["total"], 1)
        self.assertEqual(database.favorite_count(), 1)

        self.assertTrue(database.remove_favorite(video_id))
        self.assertFalse(database.remove_favorite(video_id))
        self.assertFalse(database.is_favorite(video_id))
        self.assertEqual(database.list_videos(favorite_only=True)["total"], 0)
        self.assertEqual(database.favorite_count(), 0)

    def test_favorites_are_ordered_paginated_and_keep_missing_records(self):
        first_id = self.videos["Favorite One"]["id"]
        second_id = self.videos["Favorite Two"]["id"]
        database.add_favorite(first_id)
        database.add_favorite(second_id)
        with database.connect() as connection:
            connection.execute(
                "UPDATE favorites SET created_at = ? WHERE video_id = ?",
                ("2026-01-01T10:00:00+08:00", first_id),
            )
            connection.execute(
                "UPDATE favorites SET created_at = ? WHERE video_id = ?",
                ("2026-01-02T10:00:00+08:00", second_id),
            )

        first_page = database.list_videos(
            favorite_only=True,
            sort="favorited",
            page=1,
            page_size=1,
        )
        second_page = database.list_videos(
            favorite_only=True,
            sort="favorited",
            page=2,
            page_size=1,
        )
        self.assertEqual(first_page["pages"], 2)
        self.assertEqual(first_page["items"][0]["title"], "Favorite Two")
        self.assertEqual(second_page["items"][0]["title"], "Favorite One")
        previous, following = database.get_video_neighbors(
            first_page["items"][0]["id"],
            favorite_only=True,
            sort="favorited",
        )
        self.assertIsNone(previous)
        self.assertEqual(following["id"], second_page["items"][0]["id"])

        (self.video_root / "Favorite Two.mp4").unlink()
        scan_library([self.video_root], minimum_bytes=0)

        self.assertTrue(database.is_favorite(second_id))
        self.assertEqual(database.list_videos(favorite_only=True)["total"], 1)
        self.assertEqual(database.favorite_count(), 1)

    def test_detail_actions_and_favorite_page(self):
        video = self.videos["Favorite One"]
        video_id = video["id"]
        client = admin_client()

        home = client.get("/")
        empty_page = client.get("/favorites")
        detail = client.get("/watch/{}".format(video_id))
        added = client.post(
            "/favorites/{}/add".format(video_id),
            follow_redirects=False,
        )
        duplicate = client.post(
            "/favorites/{}/add".format(video_id),
            follow_redirects=False,
        )
        favorite_detail = client.get("/watch/{}".format(video_id))
        favorite_page = client.get("/favorites")

        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/favorites"', home.text)
        self.assertIn("我的收藏", home.text)
        self.assertIn('class="favorite-count">0</span>', home.text)
        self.assertEqual(empty_page.status_code, 200)
        self.assertIn("还没有收藏的视频", empty_page.text)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("添加收藏", detail.text)
        self.assertIn(
            'action="/favorites/{}/add"'.format(video_id),
            detail.text,
        )
        self.assertEqual(added.status_code, 303)
        self.assertEqual(added.headers["location"], "/watch/{}".format(video_id))
        self.assertEqual(duplicate.status_code, 303)
        self.assertIn("取消收藏", favorite_detail.text)
        self.assertIn(
            'action="/favorites/{}/remove"'.format(video_id),
            favorite_detail.text,
        )
        self.assertEqual(favorite_page.status_code, 200)
        self.assertIn(
            "no-store",
            favorite_page.headers["cache-control"],
        )
        self.assertIn("<h1>我的收藏</h1>", favorite_page.text)
        self.assertIn('class="header-brand-group"', favorite_page.text)
        self.assertLess(
            favorite_page.text.index("LOCAL <em>VIDEO</em>"),
            favorite_page.text.index(">返回</a>"),
        )
        self.assertIn(video["title"], favorite_page.text)
        self.assertEqual(favorite_page.text.count("video-card"), 1)
        self.assertIn(
            'class="favorite-count">1</span>',
            client.get("/").text,
        )
        removed = client.post(
            "/favorites/{}/remove".format(video_id),
            follow_redirects=False,
        )
        self.assertEqual(removed.status_code, 303)
        self.assertFalse(database.is_favorite(video_id))
        self.assertIn(
            "添加收藏",
            client.get("/watch/{}".format(video_id)).text,
        )

    def test_favorite_routes_reject_unknown_video(self):
        client = admin_client()

        self.assertEqual(client.post("/favorites/999999/add").status_code, 404)
        self.assertEqual(client.post("/favorites/999999/remove").status_code, 404)

    def test_legacy_get_favorite_links_require_confirmation(self):
        video = self.videos["Favorite One"]
        video_id = video["id"]
        client = admin_client()
        user = database.get_user_by_normalized_username("test-admin")

        add_page = client.get(
            "/favorites/{}/add?return_to=%2F".format(video_id)
        )
        self.assertEqual(add_page.status_code, 200)
        self.assertIn("请确认添加收藏", add_page.text)
        self.assertIn(
            'method="post" action="/favorites/{}/add?return_to=%2F"'.format(
                video_id
            ),
            add_page.text,
        )
        self.assertFalse(
            database.is_user_favorite(user["id"], video_id)
        )
        added = client.post(
            "/favorites/{}/add?return_to=%2F".format(video_id),
            follow_redirects=False,
        )
        self.assertEqual(added.status_code, 303)

        remove_page = client.get(
            "/favorites/{}/remove?return_to=%2Ffavorites".format(video_id)
        )
        self.assertEqual(remove_page.status_code, 200)
        self.assertIn("请确认取消收藏", remove_page.text)
        self.assertIn(
            'method="post" action="/favorites/{}/remove?redirect_to=%2Ffavorites"'.format(
                video_id
            ),
            remove_page.text,
        )
        self.assertIn(
            "no-store",
            remove_page.headers["cache-control"],
        )
        self.assertTrue(
            database.is_user_favorite(user["id"], video_id)
        )

    def test_admin_badge_and_favorite_cards_include_admin_only_video(self):
        first = self.videos["Favorite One"]
        second = self.videos["Favorite Two"]
        database.set_video_access_level(first["id"], 0)
        database.set_video_access_level(second["id"], 2)
        client = admin_client()
        user = database.get_user_by_normalized_username("test-admin")
        database.add_user_favorite(user["id"], first["id"])
        database.add_user_favorite(user["id"], second["id"])

        home = client.get("/")
        favorites = client.get("/favorites")

        self.assertIn('class="favorite-count">2</span>', home.text)
        self.assertIn("2 部已收藏视频", favorites.text)
        self.assertEqual(favorites.text.count("video-card"), 2)
        self.assertIn(
            'action="/favorites/{}/remove?redirect_to=/favorites"'.format(
                first["id"]
            ),
            favorites.text,
        )
        self.assertIn(
            'action="/favorites/{}/remove?redirect_to=/favorites"'.format(
                second["id"]
            ),
            favorites.text,
        )
        removed = client.post(
            "/favorites/{}/remove?redirect_to=%2Ffavorites".format(
                second["id"]
            ),
            follow_redirects=False,
        )
        self.assertEqual(removed.status_code, 303)
        self.assertFalse(
            database.is_user_favorite(user["id"], second["id"])
        )

    def test_remove_favorite_does_not_require_current_view_access(self):
        video = self.videos["Favorite Two"]
        database.set_video_access_level(video["id"], 2)
        client = user_client(username="favorite-owner")
        user = database.get_user_by_normalized_username("favorite-owner")
        database.add_user_favorite(user["id"], video["id"])

        removed = client.post(
            "/favorites/{}/remove?redirect_to=%2Ffavorites".format(video["id"]),
            follow_redirects=False,
        )

        self.assertEqual(removed.status_code, 303)
        self.assertEqual(removed.headers["location"], "/favorites")
        self.assertFalse(
            database.is_user_favorite(user["id"], video["id"])
        )

    def test_remove_favorite_is_scoped_to_current_user(self):
        video = self.videos["Favorite One"]
        owner = user_client(username="favorite-owner")
        owner_user = database.get_user_by_normalized_username("favorite-owner")
        database.add_user_favorite(owner_user["id"], video["id"])

        other = user_client(username="favorite-other")
        removed = other.post(
            "/favorites/{}/remove".format(video["id"]),
            follow_redirects=False,
        )

        self.assertEqual(removed.status_code, 404)
        self.assertTrue(
            database.is_user_favorite(owner_user["id"], video["id"])
        )

    def test_video_detail_preserves_listing_return_path_and_header_order(self):
        video_id = self.videos["Favorite One"]["id"]
        client = admin_client()
        listing = client.get("/?sort=name&page=1")
        listing_html = html.unescape(listing.text)
        card_match = re.search(
            r'href="(/watch/{}\?return_to=[^"]+)"'.format(video_id),
            listing_html,
        )
        self.assertIsNotNone(card_match)

        watch_path = card_match.group(1)
        return_to = parse_qs(urlsplit(watch_path).query)["return_to"][0]
        detail = client.get(watch_path)
        detail_html = html.unescape(detail.text)

        self.assertEqual(return_to, "/?sort=name&page=1")
        self.assertIn(
            'href="/?sort=name&page=1">返回</a>',
            detail_html,
        )
        self.assertLess(
            detail_html.index(">返回</a>"),
            detail_html.index("添加收藏"),
        )

        form_action = re.search(
            r'action="(/favorites/{}/add[^"]*)"'.format(video_id),
            detail_html,
        ).group(1)
        self.assertEqual(
            parse_qs(urlsplit(form_action).query)["return_to"][0],
            return_to,
        )
        added = client.post(form_action, follow_redirects=False)
        self.assertEqual(added.status_code, 303)
        self.assertEqual(
            added.headers["location"],
            "/watch/{}?return_to=%2F%3Fsort%3Dname%26page%3D1".format(video_id),
        )

    def test_return_path_rejects_external_redirects(self):
        self.assertEqual(_safe_return_path("https://example.com"), "/")
        self.assertEqual(_safe_return_path("//example.com/path"), "/")
        self.assertEqual(_safe_return_path("/\\example.com"), "/")
        self.assertEqual(_safe_return_path("/?page=2"), "/?page=2")


if __name__ == "__main__":
    unittest.main()
