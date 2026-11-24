import unittest

from local_video_site.downloader.naming import (
    choose_best_title,
    clean_series_title,
    safe_filename_component,
    truncate_readable_filename,
    truncate_at_hash,
)


class NamingTests(unittest.TestCase):
    def test_chinese_episode_and_site_are_removed(self):
        title = "《星河旅人》第08集 - 腾讯视频"
        self.assertEqual(
            clean_series_title(title, "https://v.qq.com/x/cover/example.html"),
            "星河旅人",
        )

    def test_english_markers_are_removed(self):
        title = "Deep Space S02E03 1080P | ExampleSite"
        self.assertEqual(clean_series_title(title), "Deep Space")

    def test_generic_streaming_noise_is_removed(self):
        title = "城市故事 第2话 在线观看 高清完整版_哔哩哔哩"
        self.assertEqual(
            clean_series_title(title, "https://www.bilibili.com/video/example"),
            "城市故事",
        )

    def test_missing_title_uses_fallback(self):
        self.assertEqual(clean_series_title("", fallback="video"), "video")

    def test_choose_best_non_fallback_title(self):
        result = choose_best_title(["", "Short", "A Longer Series 第1集"])
        self.assertEqual(result, "A Longer Series")

    def test_path_separator_is_replaced(self):
        title = "A/B 第1集"
        self.assertEqual(clean_series_title(title), "A／B")

    def test_explicit_title_cannot_create_nested_paths(self):
        self.assertEqual(
            safe_filename_component(
                "Gape farting sluts (ATM/ATOGM) RS25"
            ),
            "Gape farting sluts (ATM／ATOGM) RS25",
        )
        traversal = safe_filename_component("../outside")
        self.assertNotIn("/", traversal)
        self.assertNotIn("\\", traversal)

    def test_unknown_streaming_site_suffix_is_removed(self):
        title = "无名系列 - 大米影视"
        self.assertEqual(clean_series_title(title), "无名系列")

    def test_xvideos_site_suffix_is_removed(self):
        title = "Ragazze Italiane Best Anal - XVIDEOS.COM"
        self.assertEqual(
            clean_series_title(title, "https://www.xvideos.com/video.example/title"),
            "Ragazze Italiane Best Anal",
        )

    def test_hash_and_all_following_text_are_removed(self):
        self.assertEqual(
            clean_series_title("Video title#标签1#标签2"),
            "Video title",
        )

    def test_explicit_name_hash_is_removed(self):
        self.assertEqual(truncate_at_hash("Custom name#ignored"), "Custom name")
        self.assertEqual(truncate_at_hash("#only-tags"), "")

    def test_xhamster_site_suffix_is_removed(self):
        self.assertEqual(
            clean_series_title(
                "Little red riding hood NT | xHamster",
                "https://xhamster.com/videos/example",
            ),
            "Little red riding hood NT",
        )

    def test_porntrex_site_suffix_is_removed(self):
        self.assertEqual(
            clean_series_title(
                "Sofia Smith & Yessica Bunny DAP | Porntrex.com best ...",
                "https://www.porntrex.com/video/3149547/example",
            ),
            "Sofia Smith & Yessica Bunny DAP",
        )

    def test_18av_marketing_suffix_is_removed(self):
        title = (
            "無碼]HEYZO-1632 最新heyzo.com 1632 美癡女 外觀乾淨 "
            "小野寺梨紗 18AV在線H成人影片-18AV萬部免費影片與您分享,"
            "免費AV在線看,線上a片直播"
        )
        self.assertEqual(
            clean_series_title(
                title,
                "https://18av.mm-cg.com/zh/uncensored_content/1/example.html",
            ),
            "無碼]HEYZO-1632 最新heyzo.com 1632 美癡女 外觀乾淨 小野寺梨紗",
        )

    def test_balanced_content_prefix_is_preserved(self):
        title = (
            "[無碼破解]DHLD-015 一天穿了一整天的濕答答黑絲襪 "
            "18AV在線H成人影片-18AV萬部免費影片與您分享,免費A"
        )
        self.assertEqual(
            clean_series_title(title),
            "[無碼破解]DHLD-015 一天穿了一整天的濕答答黑絲襪",
        )

    def test_long_filename_uses_readable_boundary_and_byte_cap(self):
        title = " ".join(["Long descriptive title"] * 12)
        shortened = truncate_readable_filename(title)

        self.assertLessEqual(len(shortened.encode("utf-8")), 180)
        self.assertTrue(shortened.endswith("…"))
        self.assertFalse(shortened.endswith("descript…"))


if __name__ == "__main__":
    unittest.main()
