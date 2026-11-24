import logging
import unittest
from unittest.mock import patch

from local_video_site.downloader.environment.shadowsocks import (
    disable_google_environment,
    enable_google_environment,
    set_shadowsocks_proxy,
)


class ShadowsocksEnvironmentTests(unittest.TestCase):
    def test_enable_does_nothing_when_google_is_available(self):
        with (
            patch("local_video_site.downloader.environment.shadowsocks.google_browser_available", return_value=True),
            patch("local_video_site.downloader.environment.shadowsocks.is_shadowsocks_running") as running,
            patch("local_video_site.downloader.environment.shadowsocks.launch_shadowsocks") as launch,
            patch("local_video_site.downloader.environment.shadowsocks.set_shadowsocks_proxy") as set_proxy,
        ):
            result = enable_google_environment(logging.getLogger("test"))

        self.assertTrue(result)
        running.assert_not_called()
        launch.assert_not_called()
        set_proxy.assert_not_called()

    def test_enable_launches_app_and_toggles_proxy(self):
        with (
            patch(
                "local_video_site.downloader.environment.shadowsocks.google_browser_available",
                side_effect=[False, True],
            ),
            patch("local_video_site.downloader.environment.shadowsocks.is_shadowsocks_running", return_value=False),
            patch("local_video_site.downloader.environment.shadowsocks.launch_shadowsocks") as launch,
            patch("local_video_site.downloader.environment.shadowsocks.set_shadowsocks_proxy") as set_proxy,
        ):
            result = enable_google_environment(logging.getLogger("test"))

        self.assertTrue(result)
        launch.assert_called_once()
        set_proxy.assert_called_once_with(True, unittest.mock.ANY)

    def test_disable_toggles_proxy_then_quits(self):
        with (
            patch("local_video_site.downloader.environment.shadowsocks.is_shadowsocks_running", return_value=True),
            patch("local_video_site.downloader.environment.shadowsocks.google_browser_available", return_value=True),
            patch("local_video_site.downloader.environment.shadowsocks.set_shadowsocks_proxy") as set_proxy,
            patch("local_video_site.downloader.environment.shadowsocks.quit_shadowsocks_via_menu") as quit_app,
        ):
            result = disable_google_environment(logging.getLogger("test"))

        self.assertTrue(result)
        set_proxy.assert_called_once_with(False, unittest.mock.ANY)
        quit_app.assert_called_once()

    def test_disable_quits_without_toggling_when_google_is_unavailable(self):
        with (
            patch("local_video_site.downloader.environment.shadowsocks.is_shadowsocks_running", return_value=True),
            patch("local_video_site.downloader.environment.shadowsocks.google_browser_available", return_value=False),
            patch("local_video_site.downloader.environment.shadowsocks.set_shadowsocks_proxy") as set_proxy,
            patch("local_video_site.downloader.environment.shadowsocks.quit_shadowsocks_via_menu") as quit_app,
        ):
            result = disable_google_environment(logging.getLogger("test"))

        self.assertTrue(result)
        set_proxy.assert_not_called()
        quit_app.assert_called_once()

    def test_disable_does_nothing_when_app_is_not_running(self):
        with (
            patch("local_video_site.downloader.environment.shadowsocks.is_shadowsocks_running", return_value=False),
            patch("local_video_site.downloader.environment.shadowsocks.google_browser_available") as google,
            patch("local_video_site.downloader.environment.shadowsocks.quit_shadowsocks_via_menu") as quit_app,
        ):
            result = disable_google_environment(logging.getLogger("test"))

        self.assertTrue(result)
        google.assert_not_called()
        quit_app.assert_not_called()

    def test_set_proxy_clicks_menu_without_keyboard_shortcut(self):
        with (
            patch(
                "local_video_site.downloader.environment.shadowsocks.shadowsocks_proxy_enabled",
                side_effect=[False, True],
            ),
            patch("local_video_site.downloader.environment.shadowsocks._click_proxy_menu_item") as menu,
            patch("local_video_site.downloader.environment.shadowsocks.time.sleep"),
        ):
            result = set_shadowsocks_proxy(True, logging.getLogger("test"))

        self.assertTrue(result)
        menu.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
