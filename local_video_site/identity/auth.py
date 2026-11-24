from __future__ import annotations

import logging
import ipaddress
from http.cookies import SimpleCookie
from urllib.parse import quote, urlsplit

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from local_video_site.identity import accounts
from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.account import GUEST_IDENTITY, ROLE_ADMIN, ROLE_USER, Identity, required_role
from local_video_site.core.network import is_lan_address


LOGGER = logging.getLogger("local_video_site.identity.auth")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_PASSWORD_CHANGE_PATHS = {
    "/account",
    "/account/password",
    "/api/account/password",
    "/logout",
    "/api/health",
    "/api/ready",
    "/favicon.ico",
}


def _session_token(scope: Scope) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(Headers(scope=scope).get("cookie", ""))
    except Exception:
        return ""
    morsel = cookie.get(accounts.SESSION_COOKIE_NAME)
    return morsel.value if morsel is not None else ""


def _normalized_host(
    value: str,
    default_port: int | None,
) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(value if "://" in value else "//" + value)
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError:
        return None
    if not hostname:
        return None
    if hostname == "localhost":
        hostname = "loopback"
    else:
        try:
            if ipaddress.ip_address(hostname).is_loopback:
                hostname = "loopback"
        except ValueError:
            pass
    if port is None:
        port = _DEFAULT_PORTS.get(parsed.scheme.casefold(), default_port)
    if port is None:
        return None
    return hostname, int(port)


def _same_origin(scope: Scope) -> bool:
    headers = Headers(scope=scope)
    # Sec-Fetch-Site is set by the browser and cannot be forged by a
    # cross-site form. It is more reliable than string-comparing Host with
    # Origin when localhost aliases or a local reverse proxy are in use.
    fetch_site = headers.get("sec-fetch-site", "").strip().casefold()
    if fetch_site == "same-origin":
        return True
    if fetch_site == "cross-site":
        return False
    origin = headers.get("origin", "")
    referer = headers.get("referer", "")
    value = origin or referer
    if not value:
        # SameSite=Lax still protects cookie-bearing browser writes. Native
        # clients and tests may omit Origin/Referer.
        return True
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in _DEFAULT_PORTS:
        return False
    expected_scheme = str(scope.get("scheme") or "http").casefold()
    if parsed.scheme.casefold() != expected_scheme:
        return False
    origin_host = _normalized_host(value, None)
    host_header = headers.get("host", "")
    server = scope.get("server") or ("", None)
    request_host = _normalized_host(
        host_header,
        int(server[1]) if len(server) > 1 and server[1] else None,
    )
    return bool(origin_host and request_host and origin_host == request_host)


async def _deny(
    scope: Scope,
    receive: Receive,
    send: Send,
    status_code: int,
    message: str,
    login_url: str = "",
) -> None:
    path = str(scope.get("path") or "/")
    wants_html = not path.startswith("/api/")
    if status_code == 401 and wants_html and login_url:
        response = RedirectResponse(
            login_url,
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
    elif wants_html:
        body = (
            "<!doctype html><html lang=\"zh-CN\"><head>"
            "<meta charset=\"utf-8\"><meta name=\"viewport\" "
            "content=\"width=device-width,initial-scale=1\">"
            "<title>没有权限</title></head><body>"
            "<main style=\"max-width:680px;margin:120px auto;padding:24px;"
            "font-family:-apple-system,BlinkMacSystemFont,sans-serif\">"
            "<h1>没有权限</h1><p>{}</p><p><a href=\"/\">返回首页</a></p>"
            "</main></body></html>"
        ).format(message)
        response = HTMLResponse(
            body,
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )
    else:
        response = JSONResponse(
            {"detail": message},
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )
    await response(scope, receive, send)


class LanAccessMiddleware:
    """Reject non-private clients when LAN mode is enabled."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not config.lan_mode():
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or ("", 0)
        if not is_lan_address(client[0]):
            LOGGER.warning(
                "Network access denied client=%s path=%s",
                client[0],
                scope.get("path", ""),
                extra={"operation": "network.denied"},
            )
            response = HTMLResponse(
                "访问被拒绝：本地视频站仅允许内网访问。",
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class AccountAccessMiddleware:
    """Resolve account identity and enforce route-level role requirements."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "/")
        method = str(scope.get("method") or "GET").upper()
        if path.startswith("/static/"):
            scope.setdefault("state", {})["identity"] = GUEST_IDENTITY
            await self.app(scope, receive, send)
            return
        token = _session_token(scope)
        identity = GUEST_IDENTITY
        if token:
            try:
                session = database.get_session_identity(
                    accounts.hash_session_token(token)
                )
            except Exception:
                LOGGER.exception("Could not resolve account session")
                session = None
            if session:
                identity = Identity(
                    user_id=int(session["user_id"]),
                    username=str(session["username"]),
                    role=str(session["role"]),
                    session_id=int(session["session_id"]),
                    must_change_password=bool(
                        session.get("must_change_password")
                    ),
                )
                database.touch_user_session(int(session["session_id"]))
        scope.setdefault("state", {})["identity"] = identity

        if (
            identity.authenticated
            and identity.must_change_password
            and method != "OPTIONS"
            and path not in _PASSWORD_CHANGE_PATHS
            and not path.startswith("/static/")
        ):
            if path.startswith("/api/"):
                response = JSONResponse(
                    {
                        "detail": (
                            "首次登录必须先修改初始密码"
                        )
                    },
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
            else:
                response = RedirectResponse(
                    "/account",
                    status_code=303,
                    headers={"Cache-Control": "no-store"},
                )
            await response(scope, receive, send)
            return

        required = required_role(method, path)
        if required == ROLE_USER and not identity.authenticated:
            safe_next = path
            if scope.get("query_string"):
                safe_next += "?" + scope["query_string"].decode("utf-8")
            await _deny(
                scope,
                receive,
                send,
                401,
                "需要登录后才能执行该操作",
                "/login?next={}".format(
                    quote(safe_next, safe="/?=&")
                ),
            )
            return
        if (
            required == ROLE_ADMIN
            and identity.role != ROLE_ADMIN
        ):
            status_code = 403 if identity.authenticated else 401
            login_url = "/login?next={}".format(
                quote(path, safe="/?=&")
            )
            await _deny(
                scope,
                receive,
                send,
                status_code,
                (
                    "当前账号没有管理员权限"
                    if identity.authenticated
                    else "需要管理员登录后才能访问"
                ),
                login_url,
            )
            return
        if (
            method in _UNSAFE_METHODS
            and identity.authenticated
            and path not in {"/login", "/setup", "/logout"}
            and not _same_origin(scope)
        ):
            headers = Headers(scope=scope)
            LOGGER.warning(
                "Request source rejected method=%s path=%s host=%s "
                "origin=%s referer=%s sec_fetch_site=%s",
                method,
                path,
                headers.get("host", ""),
                headers.get("origin", ""),
                headers.get("referer", ""),
                headers.get("sec-fetch-site", ""),
                extra={"operation": "request.source.rejected"},
            )
            await _deny(
                scope,
                receive,
                send,
                403,
                "请求来源校验失败",
            )
            return
        await self.app(scope, receive, send)
