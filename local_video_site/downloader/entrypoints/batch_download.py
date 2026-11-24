#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from local_video_site.core import config
from ..sniffer import redact_url


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_URL_FILE = PROJECT_ROOT / "urls.txt"
DEFAULT_OUTPUT = PROJECT_ROOT / "download"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
PYTHON_PATH = PROJECT_ROOT / ".venv" / "bin" / "python"
DOWNLOAD_MODULE = "local_video_site.downloader.entrypoints.download_video"
ENABLE_ENV_MODULE = "local_video_site.downloader.environment.enable_google"
DISABLE_ENV_MODULE = "local_video_site.downloader.environment.disable_google"

_PROGRESS_RE = re.compile(r"\|\s*Progress\s+([0-9]+(?:\.[0-9]+)?)%")
_KEY_LOG_MARKERS = (
    "Final series name:",
    "Selected media type:",
    "Selected highest HLS variant:",
    "Browser HLS download attempt",
    "Clicked play control:",
    "Clicked player container:",
    "Closed popup page:",
    "Download attempt ",
    "Download complete:",
    "Existing output found:",
    "Skipped existing download task",
    "Task page closed",
    "Output size:",
    "Summary:",
    "WARNING",
    "ERROR",
    "Traceback",
)


@dataclass
class UrlEntry:
    line_number: int
    url: str
    metadata_url: Optional[str] = None
    index: int = 0


@dataclass
class UrlList:
    entries: List[UrlEntry]
    invalid: List[Tuple[int, str, str]]
    duplicates: List[Tuple[int, str]]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read URLs from a text file and download them sequentially."
    )
    parser.add_argument(
        "urls_file",
        nargs="?",
        type=Path,
        default=DEFAULT_URL_FILE,
        help="UTF-8 text file containing one URL per line",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--download-attempts", type=int, default=3)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--fragment-retries", type=int, default=5)
    parser.add_argument("--wait-seconds", type=float, default=12.0)
    parser.add_argument("--start-at", type=int, default=1, help="1-based URL position")
    parser.add_argument("--limit", type=int, help="Only process this many URLs")
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop the batch after the first failed URL",
    )
    parser.add_argument(
        "--skip-cdp-check",
        action="store_true",
        help="Do not require port 9222 to be available before processing",
    )
    parser.add_argument(
        "--show-child-output",
        action="store_true",
        help="Relay all child output instead of key output and 10%% progress",
    )
    parser.add_argument("--verbose-child", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated commands without downloading",
    )
    parser.add_argument(
        "--skip-environment-management",
        action="store_true",
        help="Do not open or close the Google/Shadowsocks environment",
    )
    parser.add_argument(
        "--env-verify-seconds",
        type=float,
        default=45.0,
        help="Seconds to wait for Google after enabling the environment",
    )
    return parser


def read_url_list(path: Path) -> UrlList:
    if not path.exists():
        raise FileNotFoundError("URL file not found: {}".format(path))

    entries: List[UrlEntry] = []
    invalid: List[Tuple[int, str, str]] = []
    duplicates: List[Tuple[int, str]] = []
    seen = set()

    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.rstrip("\r\n").strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) > 2:
                invalid.append(
                    (line_number, line, "expected one or two tab-separated URLs")
                )
                continue
            metadata_url = parts[0]
            download_url = parts[1] if len(parts) == 2 and parts[1] else metadata_url
            parsed_metadata = urlparse(metadata_url)
            parsed_download = urlparse(download_url)
            if (
                parsed_metadata.scheme not in ("http", "https")
                or not parsed_metadata.netloc
                or parsed_download.scheme not in ("http", "https")
                or not parsed_download.netloc
            ):
                invalid.append((line_number, line, "not an absolute HTTP(S) URL"))
                continue
            if metadata_url in seen:
                duplicates.append((line_number, line))
                continue
            seen.add(metadata_url)
            entries.append(
                UrlEntry(
                    line_number=line_number,
                    url=download_url,
                    metadata_url=metadata_url,
                )
            )

    return UrlList(entries=entries, invalid=invalid, duplicates=duplicates)


def build_child_command(
    entry: UrlEntry,
    args: argparse.Namespace,
) -> List[str]:
    command = [
        str(PYTHON_PATH),
        "-m",
        DOWNLOAD_MODULE,
        "--output",
        str(args.output),
        "--log-dir",
        str(args.log_dir),
        "--cdp-port",
        str(args.cdp_port),
        "--download-attempts",
        str(args.download_attempts),
        "--retries",
        str(args.retries),
        "--fragment-retries",
        str(args.fragment_retries),
        "--wait-seconds",
        str(args.wait_seconds),
    ]
    if args.verbose_child:
        command.append("--verbose")
    if entry.metadata_url and entry.metadata_url != entry.url:
        command.extend(["--metadata-url", entry.metadata_url])
    command.extend(["--reuse-cdp", entry.url])
    return command


def build_environment_command(
    enable: bool,
    args: argparse.Namespace,
) -> List[str]:
    command = [
        str(PYTHON_PATH),
        "-m",
        ENABLE_ENV_MODULE if enable else DISABLE_ENV_MODULE,
        "--cdp-port",
        str(args.cdp_port),
        "--google-timeout",
        "15",
    ]
    if enable:
        command.extend(["--verify-seconds", str(args.env_verify_seconds)])
    return command


def _setup_batch_logger(log_dir: Path, stamp: str) -> Tuple[logging.Logger, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "batch-{}.log".format(stamp)
    logger = logging.getLogger("batch_download")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger, log_path


def _check_cdp(port: int) -> str:
    endpoint = "http://127.0.0.1:{}/json/version".format(port)
    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("Browser") or "unknown"
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(
            "CDP endpoint is unavailable on port {}. Start the isolated Chrome "
            "or pass --skip-cdp-check. Detail: {}".format(port, exc)
        ) from exc


def _run_environment_script(
    enable: bool,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> bool:
    action = "enable" if enable else "disable"
    command = build_environment_command(enable, args)
    logger.info("Starting Google environment script: %s", action)
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("Google environment script failed to run: %s", exc)
        return False

    output = (completed.stdout or "").strip()
    if output:
        for line in output.splitlines():
            logger.info("[google-env] %s", line)

    if completed.returncode == 0:
        logger.info("Google environment script completed: %s", action)
        return True
    logger.error(
        "Google environment script failed: %s exit=%s",
        action,
        completed.returncode,
    )
    return False


def _key_child_line(
    line: str,
    next_progress: int,
    show_all: bool,
) -> Tuple[bool, int]:
    if show_all:
        return True, next_progress

    progress_match = _PROGRESS_RE.search(line)
    if progress_match:
        percent = float(progress_match.group(1))
        if percent >= next_progress or percent >= 100:
            return True, min(100, int(percent // 10) * 10 + 10)
        return False, next_progress

    if any(marker in line for marker in _KEY_LOG_MARKERS):
        return True, next_progress
    return False, next_progress


def random_inter_item_delay() -> float:
    """Return a delay strictly within the configured ten-second window."""

    minimum, maximum = config.batch_inter_item_delays()
    return round(
        random.uniform(minimum, maximum),
        2,
    )


def _run_one(
    entry: UrlEntry,
    total: int,
    command: Sequence[str],
    raw_log_path: Path,
    logger: logging.Logger,
    show_all: bool,
) -> Dict:
    started = time.monotonic()
    logger.info(
        "[%s/%s] START %s",
        entry.index,
        total,
        redact_url(entry.url),
    )
    next_progress = 10
    process: Optional[subprocess.Popen] = None

    try:
        with raw_log_path.open("w", encoding="utf-8") as raw_log:
            process = subprocess.Popen(
                list(command),
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                raw_log.write(line)
                raw_log.flush()
                value = line.rstrip()
                should_show, next_progress = _key_child_line(
                    value,
                    next_progress,
                    show_all,
                )
                if should_show and value:
                    logger.info("[%s/%s] %s", entry.index, total, value)
            return_code = process.wait()
    except KeyboardInterrupt:
        logger.warning("Batch interrupted while processing %s", entry.index)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    except OSError as exc:
        return_code = -1
        logger.error("[%s/%s] Could not start child process: %s", entry.index, total, exc)

    elapsed = time.monotonic() - started
    status = "success" if return_code == 0 else "failed"
    log_method = logger.info if return_code == 0 else logger.error
    log_method(
        "[%s/%s] %s exit=%s elapsed=%.1fs",
        entry.index,
        total,
        status.upper(),
        return_code,
        elapsed,
    )
    return {
        "index": entry.index,
        "line_number": entry.line_number,
        "url": entry.url,
        "status": status,
        "exit_code": return_code,
        "elapsed_seconds": round(elapsed, 2),
        "raw_log": str(raw_log_path),
    }


def _write_batch_summary(
    log_dir: Path,
    stamp: str,
    summary: Dict,
) -> None:
    json_path = log_dir / "batch-summary-{}.json".format(stamp)
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (log_dir / "batch-summary.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")


def format_failed_url_lines(failed_items: List[Dict], total: int) -> List[str]:
    lines = []
    for item in failed_items:
        lines.append(
            "[{}/{}] {} | exit={} | log={}".format(
                item.get("index", "?"),
                total,
                item.get("url", ""),
                item.get("exit_code", "?"),
                item.get("raw_log", ""),
            )
        )
    return lines


def run(args: argparse.Namespace) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = args.log_dir.resolve()
    logger, batch_log_path = _setup_batch_logger(log_dir, stamp)
    started_time = datetime.now().astimezone()
    started = time.monotonic()
    logger.info("Reading URL file: %s", args.urls_file.resolve())

    if not PYTHON_PATH.is_file():
        logger.error(
            "Batch Python executable is missing: python=%s",
            PYTHON_PATH,
        )
        return 2

    try:
        url_list = read_url_list(args.urls_file)
    except (OSError, UnicodeError) as exc:
        logger.error("Could not read URL file: %s", exc)
        return 2

    for line_number, value, reason in url_list.invalid:
        logger.warning("Ignored invalid URL at line %s: %s (%s)", line_number, value, reason)
    for line_number, value in url_list.duplicates:
        logger.info("Skipped duplicate URL at line %s: %s", line_number, redact_url(value))

    start_index = max(1, args.start_at)
    selected = url_list.entries[start_index - 1 :]
    if args.limit is not None:
        selected = selected[: max(0, args.limit)]
    for index, entry in enumerate(selected, start=1):
        entry.index = index

    if not selected:
        logger.error("No valid URLs remain after filtering")
        return 2

    logger.info(
        "Batch plan: %s URL(s), output=%s, cdp_port=%s",
        len(selected),
        args.output.resolve(),
        args.cdp_port,
    )

    environment_managed = not args.skip_environment_management and not args.dry_run
    environment_started = False
    environment_closed = not environment_managed
    if environment_managed:
        environment_started = _run_environment_script(True, args, logger)
        if not environment_started:
            environment_closed = _run_environment_script(False, args, logger)
            logger.error("Aborting batch because the Google environment is unavailable")
            return 2

    if not args.skip_cdp_check and not args.dry_run:
        try:
            browser = _check_cdp(args.cdp_port)
            logger.info("CDP ready: %s", browser)
        except RuntimeError as exc:
            logger.error("%s", exc)
            if environment_managed:
                environment_closed = _run_environment_script(False, args, logger)
            return 2

    results: List[Dict] = []
    if args.dry_run:
        for entry in selected:
            command = build_child_command(entry, args)
            logger.info("[%s/%s] DRY-RUN %s", entry.index, len(selected), " ".join(command))
        return 0

    interrupted = False
    for position, entry in enumerate(selected, start=1):
        raw_log = log_dir / "batch-{}-item-{:03d}.log".format(stamp, entry.index)
        command = build_child_command(entry, args)
        try:
            result = _run_one(
                entry=entry,
                total=len(selected),
                command=command,
                raw_log_path=raw_log,
                logger=logger,
                show_all=args.show_child_output,
            )
        except KeyboardInterrupt:
            interrupted = True
            break

        results.append(result)
        if result["status"] == "failed" and args.stop_on_error:
            logger.warning("Stopping batch after failure (--stop-on-error)")
            break
        if position < len(selected):
            delay = random_inter_item_delay()
            logger.info(
                "Waiting %.2fs before next download (random delay <= %.0fs)",
                delay,
                config.batch_inter_item_delays()[1],
            )
            try:
                time.sleep(delay)
            except KeyboardInterrupt:
                interrupted = True
                logger.warning("Batch interrupted during inter-item delay")
                break

    success = sum(item["status"] == "success" for item in results)
    failed = sum(item["status"] == "failed" for item in results)
    not_run = len(selected) - len(results)
    successful_items = [item for item in results if item["status"] == "success"]
    failed_items = [item for item in results if item["status"] == "failed"]
    processed_indexes = {item["index"] for item in results}
    not_run_items = [
        {
            "index": entry.index,
            "line_number": entry.line_number,
            "url": entry.url,
            "status": "not_run",
        }
        for entry in selected
        if entry.index not in processed_indexes
    ]
    if environment_managed:
        environment_closed = _run_environment_script(False, args, logger)

    elapsed = time.monotonic() - started
    summary = {
        "batch_id": stamp,
        "started_at": started_time.isoformat(timespec="seconds"),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "interrupted" if interrupted else ("success" if failed == 0 else "failed"),
        "url_file": str(args.urls_file.resolve()),
        "total": len(selected),
        "success": success,
        "failed": failed,
        "not_run": not_run,
        "invalid": len(url_list.invalid),
        "duplicates": len(url_list.duplicates),
        "elapsed_seconds": round(elapsed, 2),
        "batch_log": str(batch_log_path),
        "google_environment": {
            "managed": environment_managed,
            "started": environment_started,
            "closed": environment_closed,
        },
        "successful_urls": [item["url"] for item in successful_items],
        "failed_urls": [item["url"] for item in failed_items],
        "not_run_urls": [item["url"] for item in not_run_items],
        "successful_items": successful_items,
        "failed_items": failed_items,
        "not_run_items": not_run_items,
        "invalid_items": [
            {
                "line_number": line_number,
                "url": value,
                "reason": reason,
            }
            for line_number, value, reason in url_list.invalid
        ],
        "items": results,
    }
    _write_batch_summary(log_dir, stamp, summary)
    logger.info(
        "Batch summary: total=%s success=%s failed=%s not_run=%s invalid=%s "
        "duplicates=%s elapsed=%.1fs",
        summary["total"],
        success,
        failed,
        not_run,
        summary["invalid"],
        summary["duplicates"],
        elapsed,
    )
    logger.info(
        "Batch counts: success=%s, failed=%s, not_run=%s, invalid=%s, duplicates=%s",
        success,
        failed,
        not_run,
        summary["invalid"],
        summary["duplicates"],
    )
    if failed_items:
        logger.error("Failed download URLs (%s):", len(failed_items))
        for line in format_failed_url_lines(failed_items, len(selected)):
            logger.error("%s", line)
    else:
        logger.info("Failed download URLs: none")
    logger.info("Batch log: %s", batch_log_path)
    logger.info("Batch summary JSON: %s", log_dir / "batch-summary-{}.json".format(stamp))

    if interrupted:
        return 130
    if environment_managed and not environment_closed:
        return 1
    return 0 if failed == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
