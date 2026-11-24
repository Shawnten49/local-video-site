#!/usr/bin/env python3
"""Small local page used to exercise the real Chrome/CDP integration path."""

from __future__ import annotations

import argparse
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta property="og:title" content="测试星河 第1集 - 示例视频">
  <title>测试星河 第1集 - 示例视频</title>
</head>
<body>
  <h1>测试星河</h1>
  <video src="/media/series-video.mp4" controls autoplay muted></video>
</body>
</html>
""".encode("utf-8")

MEDIA_SIZE = 2 * 1024 * 1024


class FixtureHandler(BaseHTTPRequestHandler):
    chunk_delay = 0.0

    def do_GET(self) -> None:
        if self.path.startswith("/media/series-video.mp4"):
            self._serve_media(send_body=True)
            return
        if self.path in ("/", "/episode-1"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
            return
        self.send_error(404)

    def do_HEAD(self) -> None:
        if self.path.startswith("/media/series-video.mp4"):
            self._serve_media(send_body=False)
            return
        self.send_error(404)

    def _serve_media(self, send_body: bool) -> None:
        start = 0
        end = MEDIA_SIZE - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), end)

        length = end - start + 1
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if range_header:
            self.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, MEDIA_SIZE))
        self.end_headers()
        if send_body:
            chunk = b"\x00" * min(65536, length)
            remaining = length
            while remaining:
                part = chunk[: min(len(chunk), remaining)]
                self.wfile.write(part)
                remaining -= len(part)
                if self.chunk_delay:
                    time.sleep(self.chunk_delay)

    def log_message(self, format_string: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--chunk-delay",
        type=float,
        default=0.0,
        help="Optional delay between 64 KiB media chunks for progress-log testing",
    )
    args = parser.parse_args()
    FixtureHandler.chunk_delay = args.chunk_delay
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FixtureHandler)
    print("fixture server: http://127.0.0.1:{}/episode-1".format(args.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
