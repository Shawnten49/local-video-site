import unittest

from local_video_site.integrations.metadata import metadata_provider_for_url
from local_video_site.integrations.profiles import (
    resolve_profile_profile,
    resolve_site_profile,
)
from local_video_site.integrations.sites.eighteenav import (
    actors_from_payload,
    categories_from_payload,
)


class EighteenAvMetadataTests(unittest.TestCase):
    def test_profile_matches_video_and_actor_profile(self):
        video = (
            "https://18av.mm-cg.com/zh/uncensored_content/"
            "17616/RED181.html"
        )
        profile = resolve_site_profile(video)

        self.assertEqual(metadata_provider_for_url(video), "18av")
        self.assertEqual(profile.key, "18av")
        self.assertTrue(profile.metadata_supported)
        self.assertEqual(
            resolve_profile_profile(
                "https://18av.mm-cg.com/zh/uncensored_avperformer/"
                "848/actor/1.html"
            ).key,
            "18av",
        )

    def test_actor_links_are_primary_source(self):
        payload = {
            "actorLinks": [
                {
                    "name": "",
                    "href": (
                        "/zh/uncensored_avperformer/848/蓮美戀/1.html"
                    ),
                    "avatar_url": (
                        "https://pics.dmm.co.jp/mono/actjpgs/"
                        "nowprinting.gif"
                    ),
                },
                {
                    "name": " 蓮美戀 ",
                    "href": (
                        "/zh/uncensored_avperformer/848/蓮美戀/1.html"
                    ),
                    "avatar_url": (
                        "https://pics.dmm.co.jp/mono/actjpgs/actor.jpg"
                    ),
                },
                {
                    "name": "中野ありさ",
                    "href": (
                        "/zh/uncensored_avperformer/"
                        "743/中野ありさ/1.html"
                    ),
                },
                {
                    "name": "advertisement",
                    "href": "https://example.com/actor/123",
                },
            ]
        }

        actors = actors_from_payload(payload)

        self.assertEqual(
            [item["name"] for item in actors],
            ["蓮美戀", "中野ありさ"],
        )
        self.assertEqual(actors[0]["external_id"], 848)
        self.assertEqual(actors[0]["slug"], "蓮美戀")
        self.assertTrue(
            actors[0]["url"].endswith(
                "/uncensored_avperformer/848/"
                "%E8%93%AE%E7%BE%8E%E6%88%80/1.html"
            )
        )
        self.assertNotIn("avatar_url", actors[0])

    def test_reducing_mosaic_performer_path_is_supported(self):
        payload = {
            "actorLinks": [
                {
                    "name": "天馬ゆい",
                    "href": (
                        "/zh/reducing-mosaic-censored_avperformer/"
                        "30686/天馬ゆい/1.html"
                    ),
                    "avatar_url": (
                        "https://pics.dmm.co.jp/mono/actjpgs/tenma_yui.jpg"
                    ),
                }
            ]
        }

        actors = actors_from_payload(payload)

        self.assertEqual(len(actors), 1)
        self.assertEqual(actors[0]["name"], "天馬ゆい")
        self.assertEqual(actors[0]["external_id"], 30686)
        self.assertIn(
            "/reducing-mosaic-censored_avperformer/30686/",
            actors[0]["url"],
        )
        self.assertEqual(
            actors[0]["avatar_url"],
            "https://pics.dmm.co.jp/mono/actjpgs/tenma_yui.jpg",
        )

    def test_categories_prefer_dom_links_and_fallback_to_jsonld(self):
        payload = {
            "categoryLinks": [
                {
                    "name": "完全無修正",
                    "href": (
                        "/zh/uncensored_category/"
                        "1/完全無修正/1.html"
                    ),
                },
                {
                    "name": "肛交",
                    "href": (
                        "/zh/uncensored_category/18/肛交/1.html"
                    ),
                },
                {
                    "name": "完全無修正",
                    "href": (
                        "/zh/uncensored_category/"
                        "1/完全無修正/1.html"
                    ),
                },
            ],
            "jsonLdKeywords": "ignored,keywords",
        }

        categories, source = categories_from_payload(payload)

        self.assertEqual(source, "dom")
        self.assertEqual(
            [item["name"] for item in categories],
            ["完全無修正", "肛交"],
        )
        self.assertEqual(categories[0]["slug"], "完全無修正")

        fallback, fallback_source = categories_from_payload({
            "categoryLinks": [],
            "jsonLdKeywords": "完全無修正,美女・美人,,肛交",
        })
        self.assertEqual(fallback_source, "jsonld")
        self.assertEqual(
            [item["name"] for item in fallback],
            ["完全無修正", "美女・美人", "肛交"],
        )


if __name__ == "__main__":
    unittest.main()
