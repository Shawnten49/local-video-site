import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site import database
from tests.auth_test_utils import admin_client
from local_video_site.app import app
from local_video_site.catalog.categories import rebuild_actor_index
from local_video_site.integrations.sites.xhamster import actors_from_payload, deduplicate_actors, parse_meta_actors


class ActorTests(unittest.TestCase):
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
            patch.object(config, "LOG_PATH", self.data_dir / "logs" / "server.log"),
            patch.object(config, "SCAN_STATUS_PATH", self.data_dir / "scan.json"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()
        self.video_ids = {}
        for index, (name, title) in enumerate((
            ("one.mp4", "Actor One"),
            ("two.mp4", "Actor Two"),
            ("three.mp4", "Actor Three"),
        ), start=1):
            path = self.video_root / name
            path.write_bytes(b"video")
            url = "https://xhamster.com/videos/test-{}".format(index)
            database.upsert_video(
                path=path,
                title=title,
                directory="videos",
                size=5,
                mtime_ns=index,
                original_url=url,
            )
            with database.connect() as connection:
                row = connection.execute(
                    "SELECT id FROM videos WHERE path = ?",
                    (str(path),),
                ).fetchone()
            self.video_ids[title] = row["id"]
        self._add_actors(
            "Actor One",
            [
                ("Eva Barbie", "eva-barbie"),
                ("Martin Spell", "martin-spell"),
            ],
        )
        self._add_actors(
            "Actor Two",
            [
                ("EVA BARBIE", "eva-barbie"),
                ("Sara Bork", "sara-bork"),
            ],
        )

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def _add_actors(self, title, actors):
        video_id = self.video_ids[title]
        url = "https://xhamster.com/videos/test-{}".format(video_id)
        database.save_category_fetch_cache(
            video_id=video_id,
            source_url=url,
            status="fetched",
            categories=[],
            actors=[
                {
                    "name": name,
                    "slug": slug,
                    "url": "https://xhamster.com/pornstars/" + slug,
                }
                for name, slug in actors
            ],
            http_status=200,
        )

    def test_actor_index_deduplicates_counts_and_filters_videos(self):
        count = rebuild_actor_index(clear=True)
        actors = database.list_actors()
        names = [actor["name"] for actor in actors["items"]]
        eva = next(actor for actor in actors["items"] if actor["name"] == "Eva Barbie")
        eva_videos = database.list_videos(actor_id=eva["id"])

        self.assertEqual(count, 3)
        self.assertEqual(names[0], "Eva Barbie")
        self.assertEqual(eva["video_count"], 2)
        self.assertEqual(database.actor_count(), 3)
        self.assertEqual(eva_videos["total"], 2)
        ordered_eva = database.list_videos(
            actor_id=eva["id"],
            sort="name",
        )["items"]
        previous, following = database.get_video_neighbors(
            ordered_eva[0]["id"],
            actor_id=eva["id"],
            sort="name",
        )
        self.assertIsNone(previous)
        self.assertEqual(following["id"], ordered_eva[1]["id"])
        self.assertEqual(
            database.list_actors(query="mArTiN")["items"][0]["name"],
            "Martin Spell",
        )

    def test_actor_pages_api_and_video_detail(self):
        rebuild_actor_index(clear=True)
        actors = database.list_actors()["items"]
        eva = next(actor for actor in actors if actor["name"] == "Eva Barbie")
        client = admin_client()

        listing = client.get("/actors")
        collection = client.get("/actors/{}".format(eva["id"]))
        api = client.get("/api/actors")
        video_api = client.get(
            "/api/videos",
            params={"actor_id": eva["id"]},
        )
        video_id = self.video_ids["Actor One"]
        detail = client.get("/watch/{}".format(video_id))

        self.assertEqual(listing.status_code, 200)
        self.assertIn("演员清单", listing.text)
        self.assertIn("Eva Barbie", listing.text)
        self.assertEqual(collection.status_code, 200)
        self.assertEqual(collection.text.count("video-card"), 2)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["items"][0]["name"], "Eva Barbie")
        self.assertEqual(video_api.status_code, 200)
        self.assertEqual(video_api.json()["total"], 2)
        self.assertIn("演员", detail.text)
        self.assertIn(
            '演员<span class="watch-section-count">2</span>',
            detail.text,
        )
        self.assertIn("Eva Barbie", detail.text)
        self.assertIn('href="/actors/{}"'.format(eva["id"]), detail.text)
        self.assertIn(
            'href="/actors"',
            client.get("/").text,
        )

    def test_actor_avatar_is_available_to_listing_and_file_route(self):
        rebuild_actor_index(clear=True)
        actor = database.list_actors(query="Eva Barbie")["items"][0]
        config.ACTOR_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        avatar_name = "eva-barbie.png"
        (config.ACTOR_AVATAR_DIR / avatar_name).write_bytes(
            b"\x89PNG\r\n\x1a\navatar"
        )
        database.save_actor_metadata(
            actor_id=actor["id"],
            external_id=12345,
            avatar_source_url=(
                "https://ic-tt-nss.xhcdn.com/example/avatar1.webp"
            ),
        )
        database.save_actor_avatar_result(
            actor_id=actor["id"],
            status="fetched",
            avatar_path=avatar_name,
            attempts=1,
        )
        client = admin_client()

        listing = client.get("/actors")
        detail = client.get("/actors/{}".format(actor["id"]))
        image = client.get("/actor-avatar/{}".format(actor["id"]))

        self.assertEqual(listing.status_code, 200)
        self.assertIn(
            'src="/actor-avatar/{}?v='.format(actor["id"]),
            listing.text,
        )
        self.assertIn("actor-avatar", detail.text)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertIn("immutable", image.headers["cache-control"])

        rebuild_actor_index(clear=True)
        preserved = database.list_actors(query="Eva Barbie")["items"][0]
        self.assertEqual(preserved["avatar_status"], "fetched")
        self.assertTrue(preserved["avatar_available"])
        self.assertEqual(preserved["avatar_path"], avatar_name)

    def test_payload_and_meta_actor_parsing(self):
        payload = {
            "actorLinks": [
                {
                    "name": "Eva Barbie",
                    "url": "https://xhamster.com/pornstars/eva-barbie",
                },
                {
                    "name": "Veronika Havenna",
                    "url": "https://xhamster.com/shemale/pornstars/veronika-havenna",
                },
                {
                    "name": "Pornstars",
                    "url": "https://xhamster.com/pornstars",
                },
            ],
            "description": "",
        }
        actors = actors_from_payload(payload)
        self.assertEqual(
            actors,
            [
                {
                    "name": "Eva Barbie",
                    "slug": "eva-barbie",
                    "url": "https://xhamster.com/pornstars/eva-barbie",
                },
                {
                    "name": "Veronika Havenna",
                    "slug": "veronika-havenna",
                    "url": "https://xhamster.com/pornstars/veronika-havenna",
                },
            ],
        )
        self.assertEqual(
            [item["name"] for item in parse_meta_actors(
                "Description. Starring: Martin Spell, Sara Bork."
            )],
            ["Martin Spell", "Sara Bork"],
        )
        self.assertEqual(
            deduplicate_actors(actors + actors),
            actors,
        )

    def test_actor_slug_is_backfilled_when_first_seen_without_one(self):
        one_id = self.video_ids["Actor One"]
        two_id = self.video_ids["Actor Two"]
        database.save_category_fetch_cache(
            video_id=one_id,
            source_url="https://xhamster.com/videos/test-1",
            status="fetched",
            categories=[],
            actors=[{
                "name": "Korra Del Rio",
                "slug": None,
                "url": None,
            }],
            http_status=200,
        )
        database.save_category_fetch_cache(
            video_id=two_id,
            source_url="https://xhamster.com/videos/test-2",
            status="fetched",
            categories=[],
            actors=[{
                "name": "Korra Del Rio",
                "slug": "korra-del-rio",
                "url": "https://xhamster.com/pornstars/korra-del-rio",
            }],
            http_status=200,
        )

        rebuild_actor_index(clear=True)

        actor = database.list_actors(query="Korra Del Rio")["items"][0]
        with database.connect() as connection:
            row = connection.execute(
                "SELECT external_slug, profile_url FROM actors WHERE id = ?",
                (actor["id"],),
            ).fetchone()
        self.assertEqual(row["external_slug"], "korra-del-rio")
        self.assertEqual(
            row["profile_url"],
            "https://xhamster.com/pornstars/korra-del-rio",
        )


if __name__ == "__main__":
    unittest.main()
