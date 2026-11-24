import unittest
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import patch

from local_video_site.downloader.sniffer import (
    classify_media,
    extract_player_config_sources,
    MediaCandidate,
    MediaSniffer,
    media_candidate_key,
    media_size_from_headers,
    redact_url,
    sanitize_request_headers,
    urls_share_page_identity,
    video_id_from_page,
)
from local_video_site.downloader.resolution import MediaCatalog


MIN_VIDEO_BYTES = 20 * 1024 * 1024


class _BilibiliScanPage:
    def __init__(self, url):
        self.url = url
        self.context = SimpleNamespace(
            cookies=lambda urls: [],
        )

    def on(self, *args, **kwargs):
        return None

    def evaluate(self, script):
        if "navigator.userAgent" in script:
            return "test-browser"
        return []


class _ExistingManifestSniffer(MediaSniffer):
    def __init__(self):
        page = SimpleNamespace(
            url="https://missav.ws/example",
            on=lambda *args, **kwargs: None,
        )
        super().__init__(page)
        self.activation_count = 0

    def scan_page(self):
        return None

    def activate_video(self, play_selector=None):
        self.activation_count += 1


class SnifferTests(unittest.TestCase):
    def test_hls_manifest_is_detected(self):
        candidate = classify_media("https://cdn.example/master.m3u8")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, "hls")
        self.assertGreater(candidate.score, 90)

    def test_fragments_are_ignored(self):
        self.assertIsNone(classify_media("https://cdn.example/part-001.m4s"))

    def test_alternate_cdn_id_matches_page_title_slug(self):
        page_url = (
            "https://www.tnaflix.com/jp/hd-videos/"
            "Bonnie-Rose-Linda-Rey-Bambi-Bella/video5283210"
        )
        media_url = (
            "https://fck-cl08.tnaflix.com/dev8/0/032/919/"
            "0032919065/"
            "Bonnie_Rose_Linda_Rey_Bambi_Bella-720p.mp4"
        )
        candidate = classify_media(
            media_url,
            source="video-element",
            page_url=page_url,
        )

        self.assertTrue(
            urls_share_page_identity(page_url, media_url)
        )
        self.assertTrue(candidate.matches_page_video)

    def test_bilibili_4k_super_resolution_stream_is_direct_file(self):
        candidate = classify_media(
            "https://upos-sz-mirrorcoso1.bilivideo.com/"
            "upgcxcode/96/10/25746081096/"
            "25746081096_sr2-1-100168.m4s",
            source="performance",
            page_url="https://www.bilibili.com/bangumi/play/ep248180",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, "file")
        self.assertEqual(candidate.quality_height, 2160)
        self.assertEqual(candidate.quality_label, "2160p")
        self.assertTrue(candidate.matches_page_video)

    def test_widescreen_resolution_uses_width_for_quality_ranking(self):
        candidate = classify_media(
            "https://cdn.example/movie_1920x804.mp4",
            source="network",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.quality_height, 1080)
        self.assertEqual(candidate.quality_label, "1080p")

    def test_xhamster_campaign_proxy_stream_is_ignored(self):
        self.assertIsNone(classify_media(
            "https://cdnproxy.dirtyporxy.com/cmp_example/"
            "media=hlsA/multi=854x480:850x480/"
            "videos/1/c/advert-noaudio/_TPL_.mp4",
            content_type="application/vnd.apple.mpegurl",
            resource_type="xhr",
        ))

    def test_manifest_multi_parameter_exposes_source_resolutions(self):
        candidate = classify_media(
            "https://video-nss.xhcdn.com/token/"
            "media=hls4/multi=256x144:144p:,426x240:240p:,"
            "854x480:480p:,1280x720:720p:,1920x1080:1080p:,"
            "3840x2160:2160p:/026/413/988/_TPL_.av1.mp4.m3u8",
            content_type="application/vnd.apple.mpegurl",
            resource_type="other",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, "hls")
        self.assertEqual(candidate.width, 3840)
        self.assertEqual(candidate.height, 2160)
        self.assertEqual(candidate.quality_height, 2160)
        self.assertEqual(candidate.quality_label, "2160p")

    def test_page_url_is_not_accepted_as_video_element_media(self):
        page_url = (
            "https://18av.mm-cg.com/zh/reducing-mosaic_content/"
            "176178/MISM-364.html"
        )
        self.assertIsNone(
            classify_media(
                page_url,
                resource_type="media",
                source="video-element",
                page_url=page_url,
            )
        )
        self.assertIsNone(
            classify_media(
                page_url,
                content_type="text/html; charset=utf-8",
                resource_type="media",
                source="video-element",
            )
        )

        hls = MediaCandidate(
            url=(
                "https://cdn.example/hash/playlist.m3u8"
            ),
            source="network",
            content_type="application/vnd.apple.mpegurl",
            resource_type="xhr",
        )
        catalog = MediaCatalog([hls])
        self.assertIs(catalog.select("auto"), hls)

    def test_bilibili_audio_m4s_is_not_selected_as_video(self):
        self.assertIsNone(classify_media(
            "https://upos-sz-estgoss.bilivideo.com/"
            "upgcxcode/96/10/25746081096/"
            "25746081096-1-100010.m4s",
            source="performance",
            page_url="https://www.bilibili.com/bangumi/play/ep248180",
        ))

    def test_bilibili_playview_skips_widevine_streams_and_keeps_clear_1080p(self):
        page_url = "https://www.bilibili.com/bangumi/play/ep248180"
        page = _BilibiliScanPage(page_url)
        sniffer = MediaSniffer(page)
        sniffer._record(classify_media(
            "https://upos-sz-mirrorcoso1.bilivideo.com/"
            "upgcxcode/96/10/25746081096/"
            "25746081096_sr2-1-100168.m4s",
            source="performance",
            page_url=page_url,
        ))
        clear_audio = (
            "https://upos-sz-estgoss.bilivideo.com/"
            "upgcxcode/96/10/25746081096/"
            "25746081096-1-100009.m4s"
        )
        protected_audio = (
            "https://upos-sz-estgoss.bilivideo.com/"
            "upgcxcode/96/10/25746081096/"
            "25746081096-1-100010.m4s"
        )
        payload = {
            "dash": {
                "video": [
                    {
                        "id": 120,
                        "baseUrl": "https://cdn.example/video-4k.m4s",
                        "width": 3840,
                        "height": 1608,
                        "bandwidth": 11_000_000,
                        "widevine_pssh": "protected",
                    },
                    {
                        "id": 80,
                        "baseUrl": "https://cdn.example/video-1080.m4s",
                        "width": 1920,
                        "height": 804,
                        "bandwidth": 2_600_000,
                    },
                    {
                        "id": 64,
                        "baseUrl": "https://cdn.example/video-720.m4s",
                        "width": 1280,
                        "height": 536,
                        "bandwidth": 780_000,
                    },
                ],
                "audio": [
                    {
                        "id": 100010,
                        "baseUrl": protected_audio,
                        "bandwidth": 168_159,
                        "widevine_pssh": "protected",
                    },
                    {
                        "id": 100009,
                        "baseUrl": clear_audio,
                        "bandwidth": 92_608,
                    },
                ],
            },
        }

        with patch(
            "local_video_site.integrations.sites.bilibili.fetch_bilibili_playview",
            return_value=payload,
        ):
            sniffer.scan_page()

        self.assertEqual(
            [candidate.quality_label for candidate in sniffer.candidates],
            ["1080p", "720p"],
        )
        self.assertFalse(any(
            "_sr2-" in candidate.url
            for candidate in sniffer.candidates
        ))
        self.assertTrue(all(
            candidate.audio_url == clear_audio
            for candidate in sniffer.candidates
        ))

    def test_bilibili_playview_keeps_4k_when_video_and_audio_are_clear(self):
        page_url = "https://www.bilibili.com/bangumi/play/ep248180"
        page = _BilibiliScanPage(page_url)
        sniffer = MediaSniffer(page)
        payload = {
            "dash": {
                "video": [{
                    "id": 120,
                    "baseUrl": "https://cdn.example/video-4k.m4s",
                    "width": 3840,
                    "height": 1608,
                    "bandwidth": 11_000_000,
                }],
                "audio": [{
                    "id": 100010,
                    "baseUrl": "https://cdn.example/audio.m4s",
                    "bandwidth": 168_159,
                }],
            },
        }

        with patch(
            "local_video_site.integrations.sites.bilibili.fetch_bilibili_playview",
            return_value=payload,
        ):
            sniffer.scan_page()

        self.assertEqual(sniffer.candidates[0].quality_label, "2160p")
        self.assertEqual(
            sniffer.candidates[0].audio_url,
            "https://cdn.example/audio.m4s",
        )

    def test_bilibili_playview_all_protected_uses_page_fallback(self):
        page_url = "https://www.bilibili.com/bangumi/play/ep248180"
        page = _BilibiliScanPage(page_url)
        sniffer = MediaSniffer(page)
        sniffer._record(classify_media(
            "https://upos-sz-mirrorcoso1.bilivideo.com/"
            "upgcxcode/96/10/25746081096/"
            "25746081096_sr2-1-100168.m4s",
            source="performance",
            page_url=page_url,
        ))
        payload = {
            "dash": {
                "video": [{
                    "id": 120,
                    "baseUrl": "https://cdn.example/video-4k.m4s",
                    "width": 3840,
                    "height": 1608,
                    "widevine_pssh": "protected",
                }],
                "audio": [{
                    "id": 100010,
                    "baseUrl": "https://cdn.example/audio.m4s",
                    "widevine_pssh": "protected",
                }],
            },
        }

        with patch(
            "local_video_site.integrations.sites.bilibili.fetch_bilibili_playview",
            return_value=payload,
        ) as fetch:
            sniffer.scan_page()
            sniffer.scan_page()

        self.assertEqual(sniffer.candidates, [])
        self.assertEqual(fetch.call_count, 1)

    def test_video_content_type_is_detected(self):
        candidate = classify_media(
            "https://cdn.example/play?id=42",
            content_type="video/mp4",
            resource_type="media",
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, "file")

    def test_master_playlist_outranks_quality_variants(self):
        master = classify_media("https://cdn.example/0/hls.m3u8")
        ultra = classify_media("https://cdn.example/0/hls-1080p.m3u8")
        uhd = classify_media("https://cdn.example/0/hls-2160p.m3u8")
        high = classify_media("https://cdn.example/0/hls-720p.m3u8")
        minimum = classify_media("https://cdn.example/0/hls-480p.m3u8")
        low = classify_media("https://cdn.example/0/hls-250p.m3u8")
        self.assertGreater(master.score, high.score)
        self.assertGreater(ultra.score, uhd.score)
        self.assertGreater(uhd.score, high.score)
        self.assertGreater(high.score, minimum.score)
        self.assertIsNone(low)

    def test_direct_sources_keep_player_quality_label_and_rank_resolution(self):
        high = classify_media(
            "https://cdn.example/video_1920x1080_4000k.mp4",
            declared_height=720,
            quality_name="720p",
        )
        low = classify_media(
            "https://cdn.example/video_720x406_500k.mp4",
            declared_height=480,
            quality_name="480p",
        )

        self.assertEqual(high.quality_label, "720p")
        self.assertEqual(high.width, 1920)
        self.assertEqual(high.height, 1080)
        self.assertEqual(high.bandwidth, 4_000_000)
        self.assertEqual(low.quality_label, "480p")
        self.assertGreater(high.score, low.score)

    def test_direct_mp4_suffix_quality_outranks_default_network_stream(self):
        page = (
            "https://www.tnaflix.com/anal-porn/example/video7627760"
        )
        high = classify_media(
            "https://sl109.example/video7627760/"
            "example-720p.mp4?rs=269k",
            resource_type="media",
            source="video-element",
            page_url=page,
        )
        default = classify_media(
            "https://sl109.example/video7627760/"
            "example-480p.mp4?rs=118k",
            resource_type="media",
            source="network",
            page_url=page,
        )

        self.assertIsNotNone(high)
        self.assertIsNotNone(default)
        self.assertTrue(high.matches_page_video)
        self.assertEqual(high.quality_label, "720p")
        self.assertEqual(default.quality_label, "480p")
        self.assertGreater(high.score, default.score)

    def test_player_config_extracts_alt_quality_for_current_video(self):
        page = "https://www.porntrex.com/video/2553113/example"
        script = """
        var flashvars = {
          video_url: '/get_file/6/token/2553000/2553113/2553113.mp4/',
          video_url_text: '480p',
          video_alt_url: '/get_file/6/token/2553000/2553113/2553113_720p.mp4/',
          video_alt_url_text: '720p HD',
          related_url: '/get_file/6/token/999/999999/999999_1080p.mp4/',
          related_url_text: '1080p'
        };
        """

        sources = extract_player_config_sources(script, page)

        self.assertEqual(
            [
                (
                    item["label"],
                    urlparse(item["url"]).path.rstrip("/").rsplit("/", 1)[-1],
                )
                for item in sources
            ],
            [
                ("480p", "2553113.mp4"),
                ("720p HD", "2553113_720p.mp4"),
            ],
        )
        high = classify_media(
            sources[1]["url"],
            resource_type="media",
            source="player-config",
            page_url=page,
            quality_name=sources[1]["label"],
        )
        network = classify_media(
            "https://pcdn.example/2553113/2553113.mp4",
            resource_type="media",
            source="network",
            page_url=page,
        )
        self.assertIsNotNone(high)
        self.assertIsNotNone(network)
        self.assertEqual(high.quality_label, "720p")
        self.assertGreater(high.score, network.score)

    def test_player_config_extracts_playlist_sources_and_720p(self):
        page = "https://noodlemagazine.com/watch/-127700459_456252621"
        script = r"""
        window.playlist = {
          "sources": [
            {
              "file": "https://cdn2.example/videos/-107603347/456240214/vid_720p.mp4?secure=token",
              "label": "720",
              "type": "mp4"
            },
            {
              "file": "https://cdn2.example/videos/-107603347/456240214/vid_480p.mp4?secure=token",
              "label": "480",
              "type": "mp4",
              "default": true
            }
          ]
        };
        """

        sources = extract_player_config_sources(script, page)

        self.assertEqual([item["label"] for item in sources], ["720", "480"])
        self.assertEqual([item["res"] for item in sources], [720, 480])
        high = classify_media(
            sources[0]["url"],
            resource_type="media",
            source="player-config",
            page_url=page,
            declared_height=sources[0]["res"],
            quality_name=sources[0]["label"],
        )
        network_default = classify_media(
            sources[1]["url"],
            resource_type="media",
            source="network",
            page_url=page,
        )
        self.assertIsNotNone(high)
        self.assertIsNotNone(network_default)
        self.assertEqual(high.quality_label, "720p")
        self.assertGreater(high.score, network_default.score)

    def test_same_media_prefers_network_url_with_required_query(self):
        page_url = "https://www.porntrex.com/video/2289712/example"
        page = SimpleNamespace(url=page_url, on=lambda *args: None)
        sniffer = MediaSniffer(page)
        network = classify_media(
            "https://www.porntrex.com/get_file/16/token/2289000/"
            "2289712/2289712_720p.mp4/?rnd=1794679026756",
            resource_type="media",
            source="network",
            page_url=page_url,
        )
        player = classify_media(
            "https://www.porntrex.com/get_file/16/token/2289000/"
            "2289712/2289712_720p.mp4/",
            resource_type="media",
            source="player-config",
            page_url=page_url,
            quality_name="720p HD",
        )

        self.assertIsNotNone(network)
        self.assertIsNotNone(player)
        sniffer._record(network)
        sniffer._record(player)
        merged = sniffer.candidates[0]

        self.assertEqual(len(sniffer.candidates), 1)
        self.assertEqual(merged.quality_label, "720p")
        self.assertIn("?rnd=1794679026756", merged.url)
        self.assertEqual(merged.score, player.score)

    def test_content_type_hls_master_with_known_quality_beats_unknown_url(self):
        known = classify_media(
            "https://cdn.example/multi=1280x720/video/_TPL_.mp4",
            content_type="application/vnd.apple.mpegurl",
            resource_type="xhr",
            source="network",
            quality_name="720p",
        )
        unknown = classify_media(
            "https://cdn.example/video_hq.mp4.m3u8",
            content_type="application/vnd.apple.mpegurl",
            resource_type="xhr",
            source="network",
        )

        self.assertIsNotNone(known)
        self.assertIsNotNone(unknown)
        self.assertEqual(known.kind, "hls")
        self.assertEqual(known.quality_label, "720p")
        self.assertGreater(known.score, unknown.score)

    def test_media_below_360p_is_rejected(self):
        self.assertIsNone(classify_media("https://cdn.example/hls-250p.m3u8"))
        self.assertIsNotNone(classify_media("https://cdn.example/hls-360p.m3u8"))

    def test_url_secrets_are_redacted(self):
        value = redact_url(
            "https://cdn.example/video.m3u8?token=secret&path=public&expires=123"
        )
        self.assertNotIn("secret", value)
        self.assertIn("path=public", value)
        self.assertIn("expires=%3Credacted%3E", value)

    def test_download_headers_drop_browser_range_controls(self):
        headers = sanitize_request_headers(
            {
                "Accept": "*/*",
                "Range": "bytes=0-30000",
                "If-Range": "etag",
                "Referer": "https://example.com/",
            }
        )

        self.assertNotIn("Range", headers)
        self.assertNotIn("If-Range", headers)
        self.assertEqual(headers["Accept"], "*/*")
        self.assertEqual(headers["Referer"], "https://example.com/")

    def test_advertisement_creative_url_is_ignored(self):
        self.assertIsNone(
            classify_media("https://static.gsrv.dev/data/creatives/53395/1098309.mp4")
        )
        self.assertIsNone(
            classify_media("https://jpeg.live.mmcdn.com/roomad/alis_molli-15.mp4")
        )
        self.assertIsNone(
            classify_media(
                "https://xvacdn77.wakuhyfeserucu.com/ad/video.m3u8"
            )
        )

    def test_media_matching_page_video_id_is_preferred(self):
        page_url = "https://www.porntrex.com/video/3359491/vol-96-ren-azumi"
        actual = classify_media(
            "https://cdn.example/3359000/3359491/3359491.mp4",
            page_url=page_url,
            content_length=200 * 1024 * 1024,
        )
        unrelated = classify_media(
            "https://cdn.example/preview/1098309.mp4",
            page_url=page_url,
        )
        self.assertIsNotNone(actual)
        self.assertIsNotNone(unrelated)
        self.assertTrue(actual.matches_page_video)
        self.assertFalse(unrelated.matches_page_video)
        self.assertGreater(actual.score, unrelated.score)

    def test_video_id_is_extracted_from_supported_page_paths(self):
        self.assertEqual(
            video_id_from_page(
                "https://www.porntrex.com/video/3359491/vol-96-ren-azumi"
            ),
            "3359491",
        )
        self.assertEqual(
            video_id_from_page("https://www.se0201hu.vip:9527/detail/309246"),
            "309246",
        )
        self.assertEqual(
            video_id_from_page(
                "https://www.tnaflix.com/anal-porn/example/video7627760"
            ),
            "7627760",
        )

    def test_split_numeric_page_identity_matches_main_cdn_url(self):
        page_url = (
            "https://ukdevilz.com/watch/-125307451_456239856"
        )
        main_url = (
            "https://cdn.pvvstream.pro/videos/-125307451/456239856/"
            "vid_360p.mp4?secure=example"
        )
        advertisement_url = (
            "https://video.sacdnssedge.com/video/"
            "ol_226ba45c2822cd4fd8aab68126431cdd.mp4"
        )

        self.assertTrue(
            urls_share_page_identity(page_url, main_url)
        )
        self.assertFalse(
            urls_share_page_identity(page_url, advertisement_url)
        )

        main = classify_media(
            url=main_url,
            resource_type="media",
            source="player-config",
            page_url=page_url,
            declared_height=360,
        )
        advertisement = classify_media(
            url=advertisement_url,
            resource_type="media",
            source="video-element",
            page_url=page_url,
            declared_height=480,
        )
        self.assertTrue(main.matches_page_video)
        self.assertFalse(advertisement.matches_page_video)

        catalog = MediaCatalog([advertisement, main])
        self.assertIs(catalog.select("auto"), main)

    def test_existing_hls_skips_blocking_player_activation(self):
        sniffer = _ExistingManifestSniffer()
        candidate = MediaCandidate(
            url="https://surrit.com/example/playlist.m3u8",
            source="network",
            content_type="application/vnd.apple.mpegurl",
            resource_type="media",
        )
        sniffer._candidates[
            media_candidate_key(candidate.url)
        ] = candidate

        candidates = sniffer.discover(
            wait_seconds=0,
            settle_seconds=0,
            auto_play=True,
        )

        self.assertEqual(sniffer.activation_count, 0)
        self.assertEqual(candidates, [candidate])

    def test_media_below_twenty_mib_is_rejected(self):
        self.assertIsNone(
            classify_media(
                "https://cdn.example/video-6mb.mp4",
                content_length=6 * 1024 * 1024,
            )
        )
        self.assertIsNotNone(
            classify_media(
                "https://cdn.example/video-20mb.mp4",
                content_length=MIN_VIDEO_BYTES,
            )
        )

    def test_total_size_is_read_from_content_range(self):
        size = media_size_from_headers(
            {"content-range": "bytes 0-1023/987654321"}
        )
        self.assertEqual(size, 987654321)


if __name__ == "__main__":
    unittest.main()
