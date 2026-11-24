#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from .shadowsocks import AutomationError, disable_google_environment


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disable ShadowsocksX-NG and quit the application."
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--google-timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logger = logging.getLogger("disable-google-env")

    try:
        disable_google_environment(
            logger=logger,
            cdp_port=args.cdp_port,
            google_timeout=args.google_timeout,
        )
        return 0
    except AutomationError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
