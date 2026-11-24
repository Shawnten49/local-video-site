import unittest

from local_video_site.downloader.resolution import MediaCatalog, MediaPolicy
from local_video_site.downloader.resolution import yt_dlp_format_selector
from local_video_site.downloader.sniffer import MediaCandidate


class MediaResolutionTests(unittest.TestCase):
    def test_catalog_lists_and_selects_declared_qualities(self):
        high = MediaCandidate(
            url="https://cdn.example/video-1080.mp4",
            source="source-element",
            score=120,
            width=1920,
            height=1080,
            declared_height=720,
            quality_name="720p",
        )
        low = MediaCandidate(
            url="https://cdn.example/video-480.mp4",
            source="source-element",
            score=110,
            width=854,
            height=480,
            declared_height=480,
            quality_name="480p",
        )
        catalog = MediaCatalog([high, low])

        self.assertEqual(catalog.available_qualities, ["720p", "480p"])
        self.assertIs(catalog.select("auto"), high)
        self.assertIs(catalog.select("720p"), high)
        self.assertIs(catalog.select("480p"), low)

    def test_auto_prefers_highest_page_matched_quality(self):
        default_480 = MediaCandidate(
            url="https://cdn.example/video7627760/example-480p.mp4",
            source="network",
            score=300,
            height=480,
            matches_page_video=True,
        )
        actual_720 = MediaCandidate(
            url="https://cdn.example/video7627760/example-720p.mp4",
            source="video-element",
            score=200,
            height=720,
            matches_page_video=True,
        )
        preview_720 = MediaCandidate(
            url="https://preview.example/unrelated.mp4",
            source="video-element",
            score=500,
            height=720,
            matches_page_video=False,
        )
        catalog = MediaCatalog([default_480, preview_720, actual_720])

        self.assertIs(catalog.select("auto"), actual_720)

    def test_auto_caps_at_1080p_and_requires_360p_minimum(self):
        candidates = [
            MediaCandidate(
                url="https://cdn.example/video-2160p.mp4",
                source="video-element",
                height=2160,
                matches_page_video=True,
            ),
            MediaCandidate(
                url="https://cdn.example/video-1080p.mp4",
                source="video-element",
                height=1080,
                matches_page_video=True,
            ),
            MediaCandidate(
                url="https://cdn.example/video-240p.mp4",
                source="video-element",
                height=240,
                matches_page_video=True,
            ),
        ]
        catalog = MediaCatalog(candidates)

        self.assertEqual(
            catalog.select("auto").quality_label,
            "1080p",
        )

    def test_auto_uses_manifest_with_variants_above_policy_cap(self):
        master = MediaCandidate(
            url="https://cdn.example/master.m3u8",
            source="network",
            score=200,
            height=2160,
            matches_page_video=True,
        )
        proxy_480 = MediaCandidate(
            url="https://cdn.example/480/index.m3u8",
            source="network",
            score=180,
            height=480,
            matches_page_video=True,
        )
        catalog = MediaCatalog(
            [proxy_480, master],
            policy=MediaPolicy(
                quality_target="auto",
                max_height=1080,
            ),
        )

        self.assertIs(catalog.select("auto"), master)

    def test_explicit_4k_policy_prioritizes_2160p(self):
        candidates = [
            MediaCandidate(
                url="https://cdn.example/video-2160p.mp4",
                source="video-element",
                height=2160,
                matches_page_video=True,
            ),
            MediaCandidate(
                url="https://cdn.example/video-1080p.mp4",
                source="video-element",
                height=1080,
                matches_page_video=True,
            ),
        ]
        catalog = MediaCatalog(
            candidates,
            policy=MediaPolicy(
                quality_target="2160p",
                max_height=2160,
                quality_fallback="nearest_below",
            ),
        )

        self.assertEqual(
            catalog.select("2160p").quality_label,
            "2160p",
        )

        selector = yt_dlp_format_selector(
            catalog.policy,
            preferred_height=2160,
        )
        self.assertIn("height<=2160", selector)

    def test_separate_stream_selector_prefers_h264_and_audio(self):
        selector = yt_dlp_format_selector(
            MediaPolicy(
                format_strategy="separate_av",
                prefer_h264=True,
                min_height=360,
                max_height=1080,
                allow_below_minimum=False,
            )
        )

        self.assertTrue(selector.startswith("bestvideo*[vcodec^=avc1]"))
        self.assertIn("+bestaudio", selector)
        self.assertIn("height<=1080", selector)
        self.assertIn("height>=360", selector)
        self.assertNotIn("best[height", selector)


if __name__ == "__main__":
    unittest.main()
