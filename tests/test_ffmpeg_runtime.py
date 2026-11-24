import threading
import time
import unittest
from unittest.mock import patch

from local_video_site.core import config
from local_video_site.media.ffmpeg import FFmpegBusyError, ffmpeg_runtime_status, ffmpeg_slot, reset_ffmpeg_runtime_for_tests


class FFmpegRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(config, "ffmpeg_concurrency", return_value=1)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(reset_ffmpeg_runtime_for_tests)
        reset_ffmpeg_runtime_for_tests()

    def test_interactive_wait_returns_busy_when_slot_is_occupied(self):
        errors = []

        def contend():
            try:
                with ffmpeg_slot("second", wait_seconds=0.05):
                    pass
            except Exception as exc:
                errors.append(exc)

        with ffmpeg_slot("first"):
            thread = threading.Thread(target=contend)
            thread.start()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], FFmpegBusyError)
        with ffmpeg_slot("after-release", wait_seconds=0):
            status = ffmpeg_runtime_status()
            self.assertEqual(status["limit"], 1)
            self.assertEqual(status["active"], 1)

    def test_active_count_returns_to_zero(self):
        with ffmpeg_slot("first"):
            self.assertEqual(ffmpeg_runtime_status()["active"], 1)
        deadline = time.monotonic() + 1
        while (
            ffmpeg_runtime_status()["active"] != 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(ffmpeg_runtime_status()["active"], 0)


if __name__ == "__main__":
    unittest.main()
