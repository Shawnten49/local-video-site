import unittest

from local_video_site.integrations.profiles import (
    GENERIC_PROFILE,
    get_site_profile,
    list_site_profiles,
    resolve_media_profile,
    resolve_profile_profile,
    resolve_site_profile,
)
from local_video_site.integrations.profiles.registry import metadata_profile_for_url


class SiteIntegrationTests(unittest.TestCase):
    def test_registered_profiles_expose_capabilities_and_versions(self):
        profiles = {profile.key: profile for profile in list_site_profiles()}

        self.assertEqual(
            set(profiles),
            {"18av", "bilibili", "faphouse", "xvideos", "xhamster"},
        )
        self.assertTrue(profiles["18av"].metadata_supported)
        self.assertTrue(profiles["bilibili"].metadata_supported)
        self.assertTrue(profiles["xvideos"].metadata_supported)
        self.assertTrue(profiles["xhamster"].metadata_supported)
        self.assertGreater(profiles["xvideos"].parser_version, 0)
        self.assertTrue(profiles["xhamster"].capabilities["hls_master_inference"])

    def test_video_metadata_and_generic_resolution_are_separate(self):
        xvideos = resolve_site_profile(
            "https://www.xvideos.com/video.abc/example",
            require_metadata=True,
        )
        generic = resolve_site_profile(
            "https://pornone.com/720p/example/123/",
        )

        self.assertEqual(xvideos.key, "xvideos")
        bilibili = resolve_site_profile(
            "https://www.bilibili.com/bangumi/play/ep468227?from_spmid=1",
            require_metadata=True,
        )
        self.assertEqual(bilibili.key, "bilibili")
        self.assertIs(generic, GENERIC_PROFILE)
        self.assertIsNone(
            metadata_profile_for_url(
                "https://pornone.com/720p/example/123/"
            )
        )

    def test_media_and_profile_urls_use_the_same_registry(self):
        media = resolve_media_profile(
            "https://video-nss.xhcdn.com/signed/media=hls4/test.m3u8"
        )
        profile = resolve_profile_profile(
            "https://www.xvideos.com/models/may-thai"
        )

        self.assertEqual(media.key, "xhamster")
        self.assertIn("media=", media.hls_stable_url(
            "https://video-nss.xhcdn.com/signed/media=hls4/test.m3u8?token=1"
        ))
        self.assertEqual(profile.key, "xvideos")
        self.assertEqual(
            tuple(profile.actor_direct_image_selectors),
            (".profile-pic img[src]",),
        )

    def test_profile_lookup_by_key_keeps_compatibility(self):
        self.assertEqual(get_site_profile("xvideos").source_domain, "xvideos.com")
        self.assertEqual(get_site_profile("xhamster").source_domain, "xhamster.com")
        self.assertEqual(get_site_profile("bilibili").source_domain, "bilibili.com")
        self.assertIs(get_site_profile("unknown"), GENERIC_PROFILE)


if __name__ == "__main__":
    unittest.main()
