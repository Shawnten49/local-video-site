import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_video_site.downloader.cli import (
    _detect_download_proxy,
    _direct_file_fallback_candidates,
    _fallback_file_candidates,
    _order_direct_candidates_for_resume,
    _prepare_media_referer_page,
    _resolve_bilibili_season_page,
    _select_identity_compatible_candidate,
    _select_media_candidate,
    _select_series_title,
    find_existing_output,
    format_selector_for_candidate,
    is_ad_like_existing_output,
    is_advertisement_size,
)
from local_video_site.downloader.direct_range import RangeManifest
from local_video_site.downloader.ytdlp import DEFAULT_FORMAT_SPEC
from local_video_site.downloader.recovery import media_identity
from local_video_site.downloader.sniffer import MediaCandidate


SUSPICIOUS_EXISTING_MEDIA_BYTES = 20 * 1024 * 1024


class ExistingOutputTests(unittest.TestCase):
    def test_bilibili_season_resolves_to_first_episode_page(self):
        page = MagicMock()
        page.evaluate.return_value = (
            "https://www.bilibili.com/bangumi/play/ep248180"
        )

        resolved = _resolve_bilibili_season_page(
            page,
            "https://www.bilibili.com/bangumi/play/ss25568",
            logging.getLogger("bilibili-season-test"),
        )

        self.assertEqual(
            resolved,
            "https://www.bilibili.com/bangumi/play/ep248180",
        )
        page.goto.assert_called_once()

    def test_non_bilibili_url_is_not_rewritten(self):
        page = MagicMock()
        url = "https://example.com/video/1"

        self.assertEqual(
            _resolve_bilibili_season_page(
                page,
                url,
                logging.getLogger("non-bilibili-test"),
            ),
            url,
        )
        page.goto.assert_not_called()

    def test_cross_site_title_falls_back_to_download_page(self):
        title = _select_series_title(
            "",
            "",
            [
                "Bonnie Rose Linda Rey Bambi Bella - Tnaflix.com",
                "Bonnie Rose Linda Rey Bambi Bella",
            ],
            "https://www.tnaflix.com/jp/hd-videos/example/video5283210",
            "https://www.xvideos.red/video.example/source-title",
        )

        self.assertIn("Bonnie Rose Linda Rey Bambi Bella", title)
        self.assertNotEqual(title, "video")

    def test_explicit_title_wins_over_download_page_title(self):
        title = _select_series_title(
            "Canonical source title",
            "",
            ["Download page title - Tnaflix.com"],
            "https://www.tnaflix.com/video/example",
            "https://www.xvideos.red/video.example/source-title",
        )

        self.assertEqual(title, "Canonical source title")

    def test_refresh_candidate_matches_previous_480p_identity(self):
        previous_480 = MediaCandidate(
            url="https://cdn.example/video/1129043.mp4/",
            source="player-config",
            declared_height=480,
            matches_page_video=True,
        )
        refreshed_720 = MediaCandidate(
            url="https://cdn.example/video/1129043_720p.mp4/",
            source="player-config",
            height=720,
            matches_page_video=True,
        )
        refreshed_480 = MediaCandidate(
            url="https://cdn.example/video/1129043.mp4/",
            source="player-config",
            declared_height=480,
            matches_page_video=True,
        )

        self.assertIs(
            _select_identity_compatible_candidate(
                [refreshed_720, refreshed_480],
                previous_480,
            ),
            refreshed_480,
        )

    def test_existing_range_manifest_selects_matching_quality_first(self):
        target_480 = "https://cdn.example/video/1129043.mp4/"
        target_720 = "https://cdn.example/video/1129043_720p.mp4/"
        selected_720 = MediaCandidate(
            url=target_720,
            source="player-config",
            score=278,
            height=720,
            matches_page_video=True,
        )
        fallback_480 = MediaCandidate(
            url=target_480,
            source="player-config",
            score=266,
            declared_height=480,
            matches_page_video=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            basename = "series"
            (output / (basename + ".mp4.part")).write_bytes(b"partial")
            RangeManifest(
                path=output / (basename + ".mp4.range.json"),
                total_bytes=100,
                identity=media_identity(target_480, "480p"),
                chunk_size=16,
                complete_ranges=[[0, 15]],
            ).save()

            ordered = _order_direct_candidates_for_resume(
                selected_720,
                [selected_720, fallback_480],
                output,
                basename,
            )

        self.assertEqual(ordered[0], fallback_480)
        self.assertIn(selected_720, ordered)

    def test_manifest_failure_uses_highest_page_matched_direct_file(self):
        manifest = MediaCandidate(
            url="https://cdn.example/video/master.m3u8",
            source="video-element",
            score=140,
            matches_page_video=True,
        )
        direct_480 = MediaCandidate(
            url="https://cdn.example/video/main-480p.mp4",
            source="video-element",
            score=106,
            height=480,
            matches_page_video=True,
        )
        direct_360 = MediaCandidate(
            url="https://cdn.example/video/main-360p.mp4",
            source="video-element",
            score=102,
            height=360,
            matches_page_video=True,
        )
        unrelated_1080 = MediaCandidate(
            url="https://cdn.example/preview-1080p.mp4",
            source="video-element",
            score=500,
            height=1080,
            matches_page_video=False,
        )

        self.assertEqual(
            _direct_file_fallback_candidates(
                [
                    manifest,
                    unrelated_1080,
                    direct_360,
                    direct_480,
                ],
                "auto",
            ),
            [direct_480, direct_360],
        )

    def test_failed_direct_candidate_has_lower_quality_fallbacks(self):
        selected = MediaCandidate(
            url="https://cdn.example/video/2289712_720p.mp4",
            source="player-config",
            score=280,
            height=720,
            matches_page_video=True,
        )
        fallback = MediaCandidate(
            url="https://cdn.example/video/2289712.mp4",
            source="player-config",
            score=260,
            height=480,
            matches_page_video=True,
        )
        preview = MediaCandidate(
            url="https://preview.example/unrelated-1080p.mp4",
            source="video-element",
            score=500,
            height=1080,
            matches_page_video=False,
        )

        self.assertEqual(
            _fallback_file_candidates(
                selected,
                [selected, preview, fallback],
            ),
            [fallback],
        )

    def test_download_proxy_detection_prefers_explicit_and_local_http(self):
        self.assertEqual(
            _detect_download_proxy("http://127.0.0.1:9999"),
            "http://127.0.0.1:9999",
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "local_video_site.downloader.cli.socket.create_connection",
                side_effect=[OSError(), MagicMock()],
            ),
        ):
            self.assertEqual(
                _detect_download_proxy(),
                "http://127.0.0.1:1088",
            )

    def test_finds_completed_mp4(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            expected = output / "series.mp4"
            expected.write_bytes(b"video")
            self.assertEqual(find_existing_output(output, "series"), expected.resolve())

    def test_finds_fallback_ts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            expected = output / "series.ts"
            expected.write_bytes(b"video")
            self.assertEqual(find_existing_output(output, "series"), expected.resolve())

    def test_ignores_partial_empty_and_other_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / "series.mp4.part").write_bytes(b"partial")
            (output / "series.mp4").touch()
            (output / "other.mp4").write_bytes(b"other")
            self.assertIsNone(find_existing_output(output, "series"))

    def test_prefers_mp4_over_ts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / "series.ts").write_bytes(b"ts")
            expected = output / "series.mp4"
            expected.write_bytes(b"mp4")
            self.assertEqual(find_existing_output(output, "series"), expected.resolve())

    def test_missing_output_directory_is_ignored(self):
        self.assertIsNone(
            find_existing_output(Path("/tmp/no-such-comic3-output"), "series")
        )

    def test_file_smaller_than_twenty_mib_is_treated_as_ad(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            ad_file = output / "series.mp4"
            ad_file.write_bytes(b"x" * (SUSPICIOUS_EXISTING_MEDIA_BYTES - 1))
            candidate = MediaCandidate(
                url="https://cdn.example/3359491/3359491.mp4",
                source="network",
                matches_page_video=True,
            )
            unrelated = MediaCandidate(
                url="https://cdn.example/preview.mp4",
                source="network",
                matches_page_video=False,
            )

            self.assertTrue(is_ad_like_existing_output(ad_file))
            self.assertTrue(is_advertisement_size(SUSPICIOUS_EXISTING_MEDIA_BYTES - 1))
            self.assertFalse(is_advertisement_size(SUSPICIOUS_EXISTING_MEDIA_BYTES))

    def test_direct_file_uses_best_when_default_filter_lacks_height(self):
        direct = MediaCandidate(
            url="https://cdn.example/video.mp4",
            source="network",
        )
        hls = MediaCandidate(
            url="https://cdn.example/video.m3u8",
            source="network",
        )

        self.assertEqual(
            format_selector_for_candidate(direct, DEFAULT_FORMAT_SPEC),
            "best",
        )
        self.assertEqual(
            format_selector_for_candidate(hls, DEFAULT_FORMAT_SPEC),
            DEFAULT_FORMAT_SPEC,
        )
        self.assertEqual(
            format_selector_for_candidate(direct, "custom"),
            "custom",
        )

    def test_requested_quality_selects_matching_declared_direct_source(self):
        high = MediaCandidate(
            url="https://cdn.example/video_1920x1080_4000k.mp4",
            source="network",
            score=110,
            width=1920,
            height=1080,
            declared_height=720,
        )
        low = MediaCandidate(
            url="https://cdn.example/video_720x406_500k.mp4",
            source="network",
            score=100,
            width=720,
            height=406,
            declared_height=480,
        )

        self.assertIs(
            _select_media_candidate([high, low], "720p"),
            high,
        )
        self.assertIs(
            _select_media_candidate([high, low], "480p"),
            low,
        )
        self.assertIs(
            _select_media_candidate([high, low], "auto"),
            high,
        )

    def test_hls_fetch_page_switches_to_media_referer_origin(self):
        class FakePage:
            url = "https://pornzog.com/video/1"

            def __init__(self):
                self.goto_calls = []

            def goto(self, *args, **kwargs):
                self.goto_calls.append((args, kwargs))

        page = FakePage()
        _prepare_media_referer_page(
            page,
            {"Referer": "https://pornzog.com/video/1"},
            logging.getLogger("referer-test"),
            (
                "https://hclips.ahcdn.com/key=abc/media=hlsA/"
                "referer=none,.privatehomeclips.com,.gstatic.com/"
                "video.m3u8"
            ),
        )

        self.assertEqual(
            page.goto_calls[0][0][0],
            "https://privatehomeclips.com/",
        )


if __name__ == "__main__":
    unittest.main()
