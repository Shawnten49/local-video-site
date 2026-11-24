from __future__ import annotations

import contextvars
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from local_video_site.core import config


REQUEST_ID = contextvars.ContextVar("request_id", default="-")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
_DAILY_LOG_RE = re.compile(r"^server-(\d{4}-\d{2}-\d{2})\.log$")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth",
    "cookie",
    "key",
    "password",
    "sign",
    "signature",
    "token",
}


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", REQUEST_ID.get())
        record.operation = getattr(record, "operation", "-")
        return True


class DailyFileHandler(logging.Handler):
    def __init__(self, log_dir: Path, prefix: str = "server") -> None:
        super().__init__()
        self.log_dir = log_dir
        self.prefix = prefix
        self.current_date = None
        self.stream = None

    def _ensure_stream(self, record: logging.LogRecord) -> None:
        file_date = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d")
        if self.stream is not None and self.current_date == file_date:
            return
        if self.stream is not None:
            self.stream.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / "{}-{}.log".format(self.prefix, file_date)
        self.stream = path.open("a", encoding="utf-8", buffering=1)
        self.current_date = file_date

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_stream(record)
            self.stream.write(self.format(record) + "\n")
            self.stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        super().close()


def cleanup_old_logs(log_dir: Path, retention_days: int) -> int:
    if retention_days <= 0 or not log_dir.is_dir():
        return 0
    cutoff = (datetime.now().date() - timedelta(days=retention_days))
    removed = 0
    for path in log_dir.glob("server-*.log*"):
        match = _DAILY_LOG_RE.match(path.name)
        if not match:
            continue
        try:
            log_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            if log_date < cutoff:
                path.unlink()
                removed += 1
        except (OSError, ValueError):
            continue
    return removed


def configure_logging() -> DailyFileHandler:
    level_name = config.log_level()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(name)s | "
        "req=%(request_id)s | %(operation)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    context_filter = ContextFilter()

    config.ensure_data_directories()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(context_filter)

    daily_file = DailyFileHandler(config.LOG_DIR)
    daily_file.setFormatter(formatter)
    daily_file.addFilter(context_filter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(daily_file)

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(level)
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.disabled = True

    cleanup_old_logs(config.LOG_DIR, config.log_retention_days())
    return daily_file


def current_log_path(now: datetime = None) -> Path:
    value = now or datetime.now()
    return config.LOG_DIR / "server-{}.log".format(value.strftime("%Y-%m-%d"))


def redact_query_string(query: str) -> str:
    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        pairs.append((
            key,
            "***" if key.casefold() in _SENSITIVE_QUERY_KEYS else value,
        ))
    return urlencode(pairs)


class RequestLoggingMiddleware:
    def __init__(self, app) -> None:
        self.app = app
        self.logger = logging.getLogger("local_video_site.http")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        supplied = headers.get("x-request-id", "")
        request_id = (
            supplied
            if _REQUEST_ID_RE.fullmatch(supplied)
            else uuid.uuid4().hex[:12]
        )
        token = REQUEST_ID.set(request_id)
        started = time.monotonic()
        status_code = 500

        async def send_with_request_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw_headers = list(message.get("headers", []))
                raw_headers.append(
                    (b"x-request-id", request_id.encode("ascii"))
                )
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            self.logger.exception(
                "Unhandled request error",
                extra={"operation": "http.error"},
            )
            raise
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            path = scope.get("path", "")
            query = redact_query_string(scope.get("query_string", b"").decode("utf-8"))
            target = path + ("?" + query if query else "")
            client = scope.get("client") or ("", 0)
            level = logging.DEBUG if (
                path.startswith("/media/")
                or path.startswith("/playback/")
                or path.startswith("/thumb/")
                or path.startswith("/static/")
            ) else (
                logging.WARNING if status_code >= 400 else logging.INFO
            )
            self.logger.log(
                level,
                "%s %s status=%s duration=%.1fms client=%s",
                scope.get("method", ""),
                target,
                status_code,
                elapsed_ms,
                client[0],
                extra={"operation": "http.request"},
            )
            REQUEST_ID.reset(token)
