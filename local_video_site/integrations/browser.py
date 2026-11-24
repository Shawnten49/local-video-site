from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from local_video_site.core import config
from local_video_site.downloader.chrome import cdp_version, launch_chrome


class BrowserSession:
    """Reusable CDP browser lifecycle for metadata and page adapters."""

    def __init__(
        self,
        port: int = 9333,
        timeout_ms: int = 30000,
        logger: Optional[logging.Logger] = None,
        profile_dir: Optional[Path] = None,
    ) -> None:
        self.port = int(port)
        self.timeout_ms = int(timeout_ms)
        self.logger = logger or logging.getLogger(__name__)
        self.profile_dir = profile_dir
        self.playwright = None
        self.browser = None
        self.context = None
        self.pages = []
        self.launched = False

    def __enter__(self):
        was_running = bool(cdp_version(self.port))
        launch_chrome(
            port=self.port,
            profile_dir=(
                self.profile_dir
                or config.PROJECT_ROOT / ".chrome-cdp-category-profile"
            ),
            reuse_existing=True,
            logger=self.logger,
        )
        self.launched = not was_running
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:{}".format(self.port)
        )
        self.context = (
            self.browser.contexts[0]
            if self.browser.contexts
            else self.browser.new_context()
        )
        return self

    def new_page(self, block_images: bool = False):
        if self.context is None:
            raise RuntimeError("BrowserSession must be entered before new_page")
        page = self.context.new_page()
        self.pages.append(page)
        blocked = {"media", "font"}
        if block_images:
            blocked.add("image")
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in blocked
                else route.continue_()
            ),
        )
        return page

    def close(self) -> None:
        for page in reversed(self.pages):
            try:
                page.close()
            except PlaywrightError:
                pass
        self.pages.clear()
        if self.browser is not None and self.launched:
            try:
                self.browser.close()
            except PlaywrightError:
                pass
        if self.playwright is not None:
            self.playwright.stop()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
