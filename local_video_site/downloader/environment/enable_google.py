#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, List

from .shadowsocks import AutomationError, enable_google_environment


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enable ShadowsocksX-NG when Google is inaccessible."
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--google-timeout", type=float, default=15.0)
    parser.add_argument("--verify-seconds", type=float, default=30.0)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check Google access; do not launch or toggle Shadowsocks",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logger = logging.getLogger("enable-google-env")

    try:
        if args.check_only:
            from .shadowsocks import google_browser_available

            available = google_browser_available(
                cdp_port=args.cdp_port,
                timeout=args.google_timeout,
            )
            print("Google accessible: {}".format("yes" if available else "no"))
            return 0 if available else 1

        enable_google_environment(
            logger=logger,
            cdp_port=args.cdp_port,
            google_timeout=args.google_timeout,
            verify_seconds=args.verify_seconds,
        )
        return 0
    except AutomationError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
