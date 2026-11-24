from __future__ import annotations

import logging
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..chrome import (
    ChromeLaunchError,
    DEFAULT_CHROME_PATH,
    cdp_version,
    wait_for_cdp,
)


APP_NAME = "ShadowsocksX-NG"
PROCESS_NAME = "ShadowsocksX-NG"
GOOGLE_CHECK_URL = "https://www.google.com/robots.txt"
PREFERENCE_DOMAIN = "com.qiuyuzhou.ShadowsocksX-NG"


class AutomationError(RuntimeError):
    pass


def is_shadowsocks_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", PROCESS_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def wait_for_shadowsocks_running(timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_shadowsocks_running():
            return True
        time.sleep(0.25)
    return False


def wait_for_shadowsocks_stopped(timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_shadowsocks_running():
            return True
        time.sleep(0.25)
    return not is_shadowsocks_running()


def launch_shadowsocks(logger: logging.Logger) -> None:
    logger.info("Launching %s", APP_NAME)
    try:
        subprocess.run(["open", "-a", APP_NAME], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AutomationError("Could not launch {}: {}".format(APP_NAME, exc)) from exc

    if not wait_for_shadowsocks_running(timeout=10):
        raise AutomationError("{} did not start within 10 seconds".format(APP_NAME))
    time.sleep(1.5)


def _run_osascript(script: str) -> None:
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AutomationError("Could not execute osascript: {}".format(exc)) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise AutomationError(
            "macOS automation failed. Grant Accessibility and Automation permission "
            "to the app running this script.{}".format(
                ": {}".format(detail) if detail else ""
            )
        )


def shadowsocks_proxy_enabled() -> Optional[bool]:
    try:
        completed = subprocess.run(
            ["defaults", "read", PREFERENCE_DOMAIN, "ShadowsocksOn"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


def _click_proxy_menu_item(enable: bool) -> None:
    labels = (
        ("打开 Shadowsocks", "Turn Shadowsocks On")
        if enable
        else ("关闭 Shadowsocks", "Turn Shadowsocks Off")
    )
    script = """
tell application "System Events"
    tell process "ShadowsocksX-NG"
        perform action "AXPress" of menu bar item 1 of menu bar 1
        delay 0.5
        set clickedItem to false
        repeat with itemRef in menu items of menu 1 of menu bar item 1 of menu bar 1
            try
                set itemName to name of itemRef as text
            on error
                set itemName to ""
            end try
            if enabled of itemRef and (itemName is "{enable_label}" or itemName is "{english_label}") then
                click itemRef
                set clickedItem to true
                exit repeat
            end if
        end repeat
        if not clickedItem then
            key code 53
            error "Proxy menu item not found: {enable_label}"
        end if
    end tell
end tell
""".format(enable_label=labels[0], english_label=labels[1])
    _run_osascript(script)


def set_shadowsocks_proxy(
    enable: bool,
    logger: logging.Logger,
    wait_seconds: float = 1.0,
) -> bool:
    current = shadowsocks_proxy_enabled()
    if current is enable:
        logger.info(
            "Shadowsocks proxy is already %s",
            "enabled" if enable else "disabled",
        )
        return True

    logger.info(
        "Clicking the Shadowsocks menu item to %s the proxy",
        "enable" if enable else "disable",
    )
    _click_proxy_menu_item(enable)
    time.sleep(max(0.2, wait_seconds))
    changed = shadowsocks_proxy_enabled()
    if changed is enable:
        logger.info(
            "Shadowsocks proxy state changed to %s",
            "enabled" if enable else "disabled",
        )
        return True

    raise AutomationError(
        "Could not {} the Shadowsocks proxy; ShadowsocksOn={}".format(
            "enable" if enable else "disable",
            changed,
        )
    )


def quit_shadowsocks_via_menu(logger: logging.Logger) -> None:
    logger.info("Clicking the Shadowsocks menu item to quit %s", APP_NAME)
    _run_osascript(
        """
tell application "System Events"
    tell process "ShadowsocksX-NG"
        perform action "AXPress" of menu bar item 1 of menu bar 1
        delay 0.5
        set clickedItem to false
        repeat with itemRef in menu items of menu 1 of menu bar item 1 of menu bar 1
            try
                set itemName to name of itemRef as text
            on error
                set itemName to ""
            end try
            if enabled of itemRef and (itemName is "退出" or itemName is "Quit" or itemName is "Quit ShadowsocksX-NG") then
                click itemRef
                set clickedItem to true
                exit repeat
            end if
        end repeat
        if not clickedItem then
            key code 53
            error "Quit menu item not found"
        end if
    end tell
end tell
"""
    )

    if wait_for_shadowsocks_stopped(timeout=8):
        return
    raise AutomationError(
        "{} is still running after clicking its quit menu item".format(APP_NAME)
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def google_browser_available(
    cdp_port: int = 9222,
    timeout: float = 15.0,
    chrome_path: str = DEFAULT_CHROME_PATH,
) -> bool:
    """Check Google through an existing or temporary Chrome browser."""

    existing_cdp = cdp_version(cdp_port, timeout=1)
    port = cdp_port if existing_cdp else _free_port()
    process: Optional[subprocess.Popen] = None
    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    playwright = None
    browser = None
    page = None
    try:
        if not existing_cdp:
            if not Path(chrome_path).is_file():
                raise AutomationError(
                    "Chrome executable not found: {}".format(chrome_path)
                )
            temp_dir = tempfile.TemporaryDirectory(prefix="google-env-chrome-")
            process = subprocess.Popen(
                [
                    chrome_path,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--remote-debugging-port={}".format(port),
                    "--user-data-dir={}".format(temp_dir.name),
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            wait_for_cdp(port, timeout=20)

        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:{}".format(port)
        )
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        response = page.goto(
            GOOGLE_CHECK_URL,
            wait_until="domcontentloaded",
            timeout=timeout * 1000,
        )
        return bool(response and response.ok)
    except (AutomationError, ChromeLaunchError, OSError, PlaywrightError):
        return False
    finally:
        if page is not None:
            try:
                if not page.is_closed():
                    page.close(run_before_unload=False)
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        if browser is not None and not existing_cdp:
            try:
                browser.close()
            except Exception:
                pass
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
        if temp_dir is not None:
            temp_dir.cleanup()


def enable_google_environment(
    logger: logging.Logger,
    cdp_port: int = 9222,
    google_timeout: float = 15.0,
    verify_seconds: float = 30.0,
) -> bool:
    if google_browser_available(cdp_port=cdp_port, timeout=google_timeout):
        logger.info("Google is already accessible; no proxy change is needed")
        return True

    if not is_shadowsocks_running():
        launch_shadowsocks(logger)
    else:
        logger.info("%s is already running", APP_NAME)

    set_shadowsocks_proxy(True, logger)

    deadline = time.monotonic() + max(1.0, verify_seconds)
    while time.monotonic() < deadline:
        if google_browser_available(cdp_port=cdp_port, timeout=google_timeout):
            logger.info("Google is accessible after enabling Shadowsocks")
            return True
        time.sleep(2)

    raise AutomationError(
        "Shadowsocks was toggled, but Google is still inaccessible after "
        "{:.0f} seconds".format(verify_seconds)
    )


def disable_google_environment(
    logger: logging.Logger,
    cdp_port: int = 9222,
    google_timeout: float = 15.0,
) -> bool:
    if not is_shadowsocks_running():
        logger.info("%s is not running; nothing to close", APP_NAME)
        return True

    logger.info("%s is running", APP_NAME)
    if google_browser_available(cdp_port=cdp_port, timeout=google_timeout):
        logger.info(
            "Google is accessible; switching off the proxy environment"
        )
        set_shadowsocks_proxy(False, logger)
    else:
        logger.info("Google is not accessible; skipping the proxy toggle")

    quit_shadowsocks_via_menu(logger)
    logger.info("%s has been closed", APP_NAME)
    return True
