from __future__ import annotations

import logging
from typing import Optional

from .sniffer import redact_url


class PopupPageGuard:
    """Close top-level pages created after a download task starts."""

    def __init__(self, context, task_page, logger: logging.Logger):
        self.context = context
        self.task_page = task_page
        self.logger = logger
        self.closed_count = 0
        self._stopped = False
        self._paused = False
        self.context.on("page", self._on_page)

    def _on_page(self, page) -> None:
        if self._stopped or self._paused or page is self.task_page:
            return
        try:
            page_url = page.url
        except Exception:
            page_url = ""
        try:
            if not page.is_closed():
                page.close(run_before_unload=False)
            self.closed_count += 1
            display_url = redact_url(page_url) if page_url.startswith("http") else (
                page_url or "<blank>"
            )
            self.logger.info("Closed popup page: %s", display_url)
        except Exception:
            self.logger.debug("Could not close popup page", exc_info=True)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if not self._stopped:
            self._paused = False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._paused = False
        try:
            self.context.remove_listener("page", self._on_page)
        except Exception:
            self.logger.debug("Could not remove popup page listener", exc_info=True)
