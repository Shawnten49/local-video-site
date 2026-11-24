import logging
import unittest

from local_video_site.downloader.page_guard import PopupPageGuard


class _FakeContext:
    def __init__(self):
        self.listeners = []

    def on(self, event, callback):
        self.listeners.append((event, callback))

    def remove_listener(self, event, callback):
        self.listeners.remove((event, callback))

    def emit_page(self, page):
        for event, callback in list(self.listeners):
            if event == "page":
                callback(page)


class _FakePage:
    def __init__(self, url):
        self.url = url
        self.closed = False
        self.close_calls = 0

    def is_closed(self):
        return self.closed

    def close(self, run_before_unload=False):
        self.closed = True
        self.close_calls += 1


class PopupPageGuardTests(unittest.TestCase):
    def test_closes_only_pages_created_after_task_page(self):
        context = _FakeContext()
        task_page = _FakePage("https://example.com/video")
        popup = _FakePage("https://ads.example/popup?token=secret")
        guard = PopupPageGuard(
            context,
            task_page,
            logging.getLogger("popup-test"),
        )

        context.emit_page(task_page)
        context.emit_page(popup)

        self.assertFalse(task_page.closed)
        self.assertTrue(popup.closed)
        self.assertEqual(popup.close_calls, 1)
        self.assertEqual(guard.closed_count, 1)

    def test_stop_removes_listener(self):
        context = _FakeContext()
        guard = PopupPageGuard(
            context,
            _FakePage("https://example.com/video"),
            logging.getLogger("popup-test"),
        )
        guard.stop()
        popup = _FakePage("https://ads.example/popup")
        context.emit_page(popup)

        self.assertFalse(popup.closed)
        self.assertEqual(context.listeners, [])

    def test_pause_allows_refresh_page_and_resume_closes_ads(self):
        context = _FakeContext()
        task_page = _FakePage("https://example.com/video")
        refresh_page = _FakePage("about:blank")
        popup = _FakePage("https://ads.example/popup")
        guard = PopupPageGuard(
            context,
            task_page,
            logging.getLogger("popup-pause-test"),
        )

        guard.pause()
        context.emit_page(refresh_page)
        self.assertFalse(refresh_page.closed)

        guard.resume()
        context.emit_page(popup)
        self.assertFalse(refresh_page.closed)
        self.assertTrue(popup.closed)


if __name__ == "__main__":
    unittest.main()
