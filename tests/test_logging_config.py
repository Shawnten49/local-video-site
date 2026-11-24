import logging
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_video_site.core import config
from local_video_site.app import app
from local_video_site.core.logging import ContextFilter, DailyFileHandler, cleanup_old_logs, redact_query_string


class LoggingConfigTests(unittest.TestCase):
    def test_daily_handler_switches_files_and_includes_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            handler = DailyFileHandler(log_dir)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(levelname)s | "
                "req=%(request_id)s | %(operation)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            handler.addFilter(ContextFilter())
            logger = logging.getLogger("logging-test-daily")
            logger.handlers = [handler]
            logger.propagate = False
            logger.setLevel(logging.INFO)
            try:
                first = logging.LogRecord(
                    logger.name,
                    logging.INFO,
                    __file__,
                    1,
                    "first message",
                    (),
                    None,
                )
                first.created = datetime(2026, 11, 12, 10, 0, 0).timestamp()
                first.request_id = "req12345"
                first.operation = "test.first"
                handler.handle(first)

                second = logging.LogRecord(
                    logger.name,
                    logging.INFO,
                    __file__,
                    2,
                    "second message",
                    (),
                    None,
                )
                second.created = datetime(2026, 11, 13, 11, 0, 0).timestamp()
                second.request_id = "req67890"
                second.operation = "test.second"
                handler.handle(second)
            finally:
                logger.handlers = []
                handler.close()

            first_path = log_dir / "server-2026-11-12.log"
            second_path = log_dir / "server-2026-11-13.log"
            self.assertTrue(first_path.is_file())
            self.assertTrue(second_path.is_file())
            self.assertIn("2026-11-12 10:00:00", first_path.read_text())
            self.assertIn("req=req12345", first_path.read_text())
            self.assertIn("test.first", first_path.read_text())
            self.assertIn("2026-11-13 11:00:00", second_path.read_text())

    def test_old_daily_logs_are_cleaned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            old_date = datetime.now().date() - timedelta(days=40)
            recent_date = datetime.now().date() - timedelta(days=2)
            old = log_dir / "server-{}.log".format(old_date.isoformat())
            recent = log_dir / "server-{}.log".format(recent_date.isoformat())
            unrelated = log_dir / "other.log"
            old.write_text("old")
            recent.write_text("recent")
            unrelated.write_text("keep")

            removed = cleanup_old_logs(log_dir, 30)

            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_sensitive_query_values_are_redacted(self):
        value = redact_query_string(
            "q=test&access_token=secret&password=hidden&sort=size"
        )

        self.assertIn("q=test", value)
        self.assertIn("sort=size", value)
        self.assertIn("access_token=%2A%2A%2A", value)
        self.assertIn("password=%2A%2A%2A", value)
        self.assertNotIn("secret", value)
        self.assertNotIn("hidden", value)

    def test_request_id_is_returned(self):
        response = TestClient(app).get(
            "/api/health",
            headers={"X-Request-ID": "request123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "request123")


if __name__ == "__main__":
    unittest.main()
