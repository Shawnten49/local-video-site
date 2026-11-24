from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from fastapi import Request


def safe_return_path(value: str) -> str:
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or value.startswith("/\\")
        or "\\" in value
    ):
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


def current_return_path(request: Request) -> str:
    path = request.url.path
    if request.url.query:
        return "{}?{}".format(path, request.url.query)
    return path


def path_without_query_params(
    request: Request,
    *names: str,
) -> str:
    excluded = {str(name) for name in names}
    query = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in excluded
    ]
    path = request.url.path
    if query:
        return "{}?{}".format(path, urlencode(query, doseq=True))
    return path


def watch_url(video_id: int, return_to: str) -> str:
    path = "/watch/{}".format(int(video_id))
    if not str(return_to or "").strip():
        return path
    return "{}?{}".format(
        path,
        urlencode({"return_to": safe_return_path(return_to)}),
    )
