import argparse
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_video_site.downloader.entrypoints.batch_download import (
    UrlEntry,
    _key_child_line,
    build_child_command,
    build_environment_command,
    format_failed_url_lines,
    random_inter_item_delay,
    read_url_list,
    run,
)


class BatchUrlListTests(unittest.TestCase):
    def test_reads_comments_deduplicates_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "urls.txt"
            path.write_text(
                "\ufeff# comment\n"
                "https://example.com/two\n"
                "\n"
                "https://example.com/one\n"
                "https://example.com/two\n"
                "not-a-url\n",
                encoding="utf-8",
            )
            result = read_url_list(path)

        self.assertEqual(
            [entry.url for entry in result.entries],
            ["https://example.com/two", "https://example.com/one"],
        )
        self.assertEqual(len(result.duplicates), 1)
        self.assertEqual(len(result.invalid), 1)

    def test_missing_file_raises_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "URL file not found"):
            read_url_list(Path("/tmp/does-not-exist-comic3-urls.txt"))

    def test_reads_metadata_and_download_source_tab_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "urls.txt"
            path.write_text(
                "https://site-a.example/video/1\t"
                "https://site-b.example/video/9\n"
                "https://site-a.example/video/2\t\n"
                "https://site-a.example/video/1\t"
                "https://site-b.example/video/10\n",
                encoding="utf-8",
            )
            result = read_url_list(path)

        self.assertEqual(len(result.entries), 2)
        self.assertEqual(
            result.entries[0].metadata_url,
            "https://site-a.example/video/1",
        )
        self.assertEqual(
            result.entries[0].url,
            "https://site-b.example/video/9",
        )
        self.assertEqual(
            result.entries[1].url,
            "https://site-a.example/video/2",
        )
        self.assertEqual(len(result.duplicates), 1)


class BatchCommandTests(unittest.TestCase):
    def test_child_command_reuses_cdp_and_quotes_url_as_an_argument(self):
        args = argparse.Namespace(
            output=Path("/tmp/videos"),
            log_dir=Path("/tmp/logs"),
            cdp_port=9222,
            download_attempts=3,
            retries=4,
            fragment_retries=5,
            wait_seconds=12.0,
            verbose_child=False,
        )
        entry = UrlEntry(line_number=1, url="https://example.com/a?x=1&y=2")
        command = build_child_command(entry, args)

        self.assertIn("--reuse-cdp", command)
        self.assertEqual(command[-1], entry.url)
        self.assertLess(command.index("--reuse-cdp"), len(command) - 1)

    def test_child_command_passes_metadata_source(self):
        args = argparse.Namespace(
            output=Path("/tmp/videos"),
            log_dir=Path("/tmp/logs"),
            cdp_port=9222,
            download_attempts=3,
            retries=4,
            fragment_retries=5,
            wait_seconds=12.0,
            verbose_child=False,
        )
        entry = UrlEntry(
            line_number=1,
            url="https://site-b.example/video/9",
            metadata_url="https://site-a.example/video/1",
        )
        command = build_child_command(entry, args)

        self.assertIn("--metadata-url", command)
        self.assertEqual(
            command[command.index("--metadata-url") + 1],
            entry.metadata_url,
        )
        self.assertEqual(command[-1], entry.url)

    def test_key_log_filter_relays_only_each_ten_percent(self):
        line = "2026-01-01 | INFO | Progress 12.3% | segment 4/30 | 1 MiB"
        show, next_percent = _key_child_line(line, 10, show_all=False)
        self.assertTrue(show)
        self.assertEqual(next_percent, 20)

        show, next_percent = _key_child_line(line, next_percent, show_all=False)
        self.assertFalse(show)
        self.assertEqual(next_percent, 20)

    def test_key_log_filter_relays_completion_and_errors(self):
        show, _ = _key_child_line("Download complete: /tmp/video.mp4", 100, False)
        self.assertTrue(show)
        show, _ = _key_child_line("ERROR | connection reset", 100, False)
        self.assertTrue(show)

    def test_random_inter_item_delay_uses_one_to_ten_seconds(self):
        with patch("local_video_site.downloader.entrypoints.batch_download.random.uniform", return_value=7.256) as mocked:
            delay = random_inter_item_delay()

        mocked.assert_called_once_with(
            1.0,
            10.0,
        )
        self.assertEqual(delay, 7.26)
        self.assertGreaterEqual(delay, 1.0)
        self.assertLessEqual(delay, 10.0)

    def test_failed_url_lines_include_exit_code_and_log(self):
        lines = format_failed_url_lines(
            [
                {
                    "index": 2,
                    "url": "https://example.com/failed?x=1",
                    "exit_code": 1,
                    "raw_log": "/tmp/item-002.log",
                },
                {
                    "index": 4,
                    "url": "https://example.com/another",
                    "exit_code": -1,
                    "raw_log": "/tmp/item-004.log",
                },
            ],
            total=5,
        )

        self.assertEqual(len(lines), 2)
        self.assertIn("[2/5] https://example.com/failed?x=1", lines[0])
        self.assertIn("exit=1", lines[0])
        self.assertIn("/tmp/item-002.log", lines[0])
        self.assertIn("[4/5] https://example.com/another", lines[1])

    def test_environment_commands_use_project_scripts(self):
        args = argparse.Namespace(cdp_port=9222, env_verify_seconds=45)
        enable_command = build_environment_command(True, args)
        disable_command = build_environment_command(False, args)

        self.assertEqual(
            enable_command[1:3],
            ["-m", "local_video_site.downloader.environment.enable_google"],
        )
        self.assertEqual(
            disable_command[1:3],
            ["-m", "local_video_site.downloader.environment.disable_google"],
        )
        self.assertIn("--verify-seconds", enable_command)
        self.assertNotIn("--verify-seconds", disable_command)


class BatchEnvironmentLifecycleTests(unittest.TestCase):
    def _args(self, url_file, output_dir):
        return argparse.Namespace(
            urls_file=url_file,
            output=output_dir,
            log_dir=output_dir,
            cdp_port=9222,
            download_attempts=3,
            retries=3,
            fragment_retries=5,
            wait_seconds=12.0,
            start_at=1,
            limit=None,
            stop_on_error=False,
            skip_cdp_check=True,
            show_child_output=False,
            verbose_child=False,
            dry_run=False,
            skip_environment_management=False,
            env_verify_seconds=45.0,
        )

    def test_batch_opens_then_closes_google_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            url_file = directory / "urls.txt"
            url_file.write_text("https://example.com/a\n", encoding="utf-8")
            args = self._args(url_file, directory)
            logger = logging.getLogger("batch-env-test")
            summary = {}

            with (
                patch("local_video_site.downloader.entrypoints.batch_download._setup_batch_logger", return_value=(logger, directory / "batch.log")),
                patch("local_video_site.downloader.entrypoints.batch_download._run_environment_script", side_effect=[True, True]) as env,
                patch(
                    "local_video_site.downloader.entrypoints.batch_download._run_one",
                    return_value={
                        "index": 1,
                        "line_number": 1,
                        "url": "https://example.com/a",
                        "status": "success",
                        "exit_code": 0,
                        "elapsed_seconds": 1.0,
                        "raw_log": str(directory / "item.log"),
                    },
                ),
                patch("local_video_site.downloader.entrypoints.batch_download._write_batch_summary", side_effect=lambda _d, _s, value: summary.update(value)),
            ):
                result = run(args)

        self.assertEqual(result, 0)
        self.assertEqual(env.call_count, 2)
        self.assertTrue(env.call_args_list[0].args[0])
        self.assertFalse(env.call_args_list[1].args[0])
        self.assertEqual(
            summary["google_environment"],
            {"managed": True, "started": True, "closed": True},
        )

    def test_dry_run_does_not_manage_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            url_file = directory / "urls.txt"
            url_file.write_text("https://example.com/a\n", encoding="utf-8")
            args = self._args(url_file, directory)
            args.dry_run = True
            logger = logging.getLogger("batch-env-test")

            with (
                patch("local_video_site.downloader.entrypoints.batch_download._setup_batch_logger", return_value=(logger, directory / "batch.log")),
                patch("local_video_site.downloader.entrypoints.batch_download._run_environment_script") as env,
            ):
                result = run(args)

        self.assertEqual(result, 0)
        env.assert_not_called()


if __name__ == "__main__":
    unittest.main()
