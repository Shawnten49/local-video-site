from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional


DEFAULT_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class ChromeLaunchError(RuntimeError):
    pass


def cdp_version(port: int, timeout: float = 1.0) -> Optional[dict]:
    endpoint = "http://127.0.0.1:{}/json/version".format(port)
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def wait_for_cdp(port: int, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        version = cdp_version(port)
        if version:
            return version
        time.sleep(0.25)
    raise ChromeLaunchError(
        "Chrome CDP did not become available on port {} within {:.0f}s{}".format(
            port,
            timeout,
            ": {}".format(last_error) if last_error else "",
        )
    )


def launch_chrome(
    port: int,
    profile_dir: Path,
    chrome_path: str = DEFAULT_CHROME_PATH,
    wait_seconds: float = 30.0,
    reuse_existing: bool = False,
    logger: Optional[logging.Logger] = None,
) -> dict:
    logger = logger or logging.getLogger(__name__)
    existing = cdp_version(port)
    if existing:
        if reuse_existing:
            logger.info("Reusing existing CDP endpoint on port %s", port)
            return existing
        raise ChromeLaunchError(
            "Port {} already has a CDP endpoint. Use --reuse-cdp only if it is "
            "the Chrome instance you intend to control.".format(port)
        )

    if sys.platform == "darwin":
        if not Path(chrome_path).is_file():
            raise ChromeLaunchError("Chrome executable not found: {}".format(chrome_path))
    elif not Path(chrome_path).is_file():
        raise ChromeLaunchError("Chrome executable not found: {}".format(chrome_path))

    profile_dir = profile_dir.expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    chrome_args: List[str] = [
        "--remote-debugging-port={}".format(port),
        "--user-data-dir={}".format(profile_dir),
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]

    logger.info(
        "Launching isolated Chrome (port=%s, profile=%s)",
        port,
        profile_dir,
    )
    if sys.platform == "darwin":
        command = ["open", "-na", "Google Chrome", "--args"] + chrome_args
    else:
        command = [chrome_path] + chrome_args

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChromeLaunchError("Could not launch Chrome: {}".format(exc)) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ChromeLaunchError(
            "Chrome launcher exited with code {}{}".format(
                completed.returncode,
                ": {}".format(detail) if detail else "",
            )
        )
    return wait_for_cdp(port, timeout=wait_seconds)
