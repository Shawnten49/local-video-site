import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.actors import ActorAvatarWorker, AvatarDownloadError, avatar_url_expires_at, avatar_url_is_expired, detect_image_extension, sync_actor_avatars, validate_avatar_url
from local_video_site.catalog.categories import rebuild_actor_index


AVATAR_URL = "https://ic-tt-nss.xhcdn.com/example/avatar1.webp"
XV_AVATAR_URL = (
    "https://profile-cdn77.xvideos-cdn.com/example/pp_big.jpg"
    "?secure=token,2000000000"
)
PROFILE_URL = "https://xhamster.com/pornstars/eva-barbie"
PNG_BYTES = b"\x89PNG\r\n\x1a\nlocal-avatar"


class FakeDownloader:
    discovered_url = AVATAR_URL
    fail_download = False
    download_calls = 0

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def discover_profile_avatar(self, profile_url):
        return {"thumbUrl": self.discovered_url}

    def download(self, source_url):
        type(self).download_calls += 1
        if self.fail_download:
            raise AvatarDownloadError("test download failure")
        return PNG_BYTES, "image/png"


class ActorAvatarTests(unittest.TestCase):
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
            patch.object(
                config,
                "ACTOR_AVATAR_DIR",
                self.data_dir / "actor-avatars",
            ),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        path = self.video_root / "avatar.mp4"
        path.write_bytes(b"video")
        database.upsert_video(
            path=path,
            title="Avatar Test",
            directory="videos",
            size=5,
            mtime_ns=1,
            original_url="https://xhamster.com/videos/avatar-xhABC",
        )
        self.video_id = database.list_videos()["items"][0]["id"]
        FakeDownloader.discovered_url = AVATAR_URL
        FakeDownloader.fail_download = False
        FakeDownloader.download_calls = 0

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _cache_actor(self, avatar_url=None):
        actor = {
            "name": "Eva Barbie",
            "slug": "eva-barbie",
            "url": PROFILE_URL,
            "external_id": 12345,
        }
        if avatar_url:
            actor["avatar_url"] = avatar_url
        database.save_category_fetch_cache(
            video_id=self.video_id,
            source_url="https://xhamster.com/videos/avatar-xhABC",
            status="fetched",
            categories=[{
                "name": "Test",
                "slug": "test",
                "type": "category",
                "url": "https://xhamster.com/categories/test",
            }],
            actors=[actor],
            http_status=200,
        )
        rebuild_actor_index(clear=True)
        return database.list_actors(query="Eva Barbie")["items"][0]

    def test_downloads_avatar_atomically_and_skips_existing_file(self):
        actor = self._cache_actor(AVATAR_URL)

        with patch("local_video_site.catalog.actors.time.sleep"):
            report = sync_actor_avatars(
                downloader_factory=FakeDownloader,
            )
            cached_report = sync_actor_avatars(
                downloader_factory=FakeDownloader,
            )

        updated = database.get_actor(actor["id"])
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(cached_report["skipped"], 1)
        self.assertEqual(cached_report["candidates"], 0)
        self.assertEqual(updated["avatar_status"], "fetched")
        self.assertTrue(updated["avatar_available"])
        self.assertEqual(updated["external_id"], 12345)
        avatar_file = (
            config.ACTOR_AVATAR_DIR / Path(updated["avatar_path"]).name
        )
        self.assertEqual(avatar_file.read_bytes(), PNG_BYTES)

    def test_discovers_avatar_from_profile_when_video_state_has_none(self):
        actor = self._cache_actor()

        with patch("local_video_site.catalog.actors.time.sleep"):
            report = sync_actor_avatars(
                downloader_factory=FakeDownloader,
            )

        updated = database.get_actor(actor["id"])
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(updated["avatar_source_url"], AVATAR_URL)
        self.assertEqual(updated["avatar_status"], "fetched")

    def test_failed_download_is_recorded_after_three_attempts(self):
        actor = self._cache_actor(AVATAR_URL)
        FakeDownloader.fail_download = True

        with patch("local_video_site.catalog.actors.time.sleep"):
            report = sync_actor_avatars(
                downloader_factory=FakeDownloader,
            )

        updated = database.get_actor(actor["id"])
        self.assertEqual(report["failed"], 1)
        self.assertEqual(updated["avatar_status"], "failed")
        self.assertIn("test download failure", updated["avatar_error"])
        self.assertGreaterEqual(updated["avatar_attempts"], 3)

    def test_failed_avatar_can_retry_after_cooldown(self):
        actor = self._cache_actor(AVATAR_URL)
        FakeDownloader.fail_download = True
        with patch("local_video_site.catalog.actors.time.sleep"):
            sync_actor_avatars(
                downloader_factory=FakeDownloader,
            )

        FakeDownloader.fail_download = False
        with patch("local_video_site.catalog.actors.time.sleep"):
            report = sync_actor_avatars(
                failed_retry_after_seconds=0,
                downloader_factory=FakeDownloader,
            )

        updated = database.get_actor(actor["id"])
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(updated["avatar_status"], "fetched")

    def test_xhamster_actor_falls_back_to_matching_xvideos_avatar(self):
        actor = self._cache_actor()

        class CrossSiteDownloader(FakeDownloader):
            def discover_profile_avatar(self, profile_url):
                return None

            def discover_xvideos_avatar(self, actor_name, source_url):
                if actor_name != "Eva Barbie":
                    return None
                return {"thumbUrl": XV_AVATAR_URL}

        with patch("local_video_site.catalog.actors.time.sleep"):
            report = sync_actor_avatars(
                downloader_factory=CrossSiteDownloader,
            )

        updated = database.get_actor(actor["id"])
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(updated["avatar_source_url"], XV_AVATAR_URL)
        self.assertEqual(updated["avatar_status"], "fetched")

    def test_actor_without_any_source_avatar_is_marked_unavailable(self):
        actor = self._cache_actor()

        class NoAvatarDownloader(FakeDownloader):
            def discover_profile_avatar(self, profile_url):
                return None

            def discover_xvideos_avatar(self, actor_name, source_url):
                return None

        with patch("local_video_site.catalog.actors.time.sleep"):
            first = sync_actor_avatars(
                downloader_factory=NoAvatarDownloader,
            )
            second = sync_actor_avatars(
                failed_retry_after_seconds=0,
                downloader_factory=NoAvatarDownloader,
            )

        updated = database.get_actor(actor["id"])
        self.assertEqual(first["candidates"], 1)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(updated["avatar_status"], "unavailable")
        self.assertEqual(second["candidates"], 0)

    def test_validates_cdn_urls_and_image_signatures(self):
        self.assertTrue(validate_avatar_url(AVATAR_URL))
        self.assertFalse(validate_avatar_url(
            "http://ic-tt-nss.xhcdn.com/example.webp"
        ))
        self.assertFalse(validate_avatar_url(
            "https://xhcdn.com.example.com/avatar.webp"
        ))
        self.assertEqual(detect_image_extension(PNG_BYTES), ".png")
        self.assertEqual(
            detect_image_extension(b"not-an-image", "image/webp"),
            ".webp",
        )
        expired = (
            "https://ic-tt-nss.xhcdn.com/example/avatar.jpg"
            "?secure=token,1000000000"
        )
        self.assertIsNotNone(avatar_url_expires_at(expired))
        self.assertTrue(avatar_url_is_expired(expired))

    def test_background_worker_processes_configured_batch(self):
        stop_event = threading.Event()
        worker = ActorAvatarWorker(
            stop_event=stop_event,
            batch_size=7,
            interval_seconds=15,
            failed_retry_after_seconds=300,
        )
        report = {
            "candidates": 7,
            "succeeded": 5,
            "failed": 1,
            "skipped": 1,
        }

        with patch(
            "local_video_site.catalog.actors.sync_actor_avatars",
            return_value=report,
        ) as sync:
            self.assertIs(worker.run_once(), report)

        sync.assert_called_once_with(
            limit=7,
            failed_retry_after_seconds=300,
            workers=1,
            logger=worker.logger,
        )


if __name__ == "__main__":
    unittest.main()
