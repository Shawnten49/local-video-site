from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


ROLE_GUEST = "guest"
ROLE_USER = "user"
ROLE_ADMIN = "admin"

ROLE_LABELS = {
    ROLE_GUEST: "游客",
    ROLE_USER: "一般用户",
    ROLE_ADMIN: "管理员",
}


@dataclass(frozen=True)
class Identity:
    user_id: Optional[int] = None
    username: str = ""
    role: str = ROLE_GUEST
    session_id: Optional[int] = None
    must_change_password: bool = False

    @property
    def authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def can_favorite(self) -> bool:
        return self.role in {ROLE_USER, ROLE_ADMIN}

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)


GUEST_IDENTITY = Identity()


_PUBLIC_EXACT_PATHS = {
    "/",
    "/login",
    "/logout",
    "/setup",
    "/favicon.ico",
    "/api/health",
    "/api/ready",
}
_PUBLIC_PREFIXES = (
    "/static/",
    "/watch/",
    "/api/tv/v1/",
    "/api/external-playback/",
    "/api/playback-state/",
    "/api/playback/resolve/",
    "/api/prepare/",
    "/media/",
    "/playback/",
    "/subtitle/",
    "/thumb/",
    "/preview/",
)
_USER_EXACT_PATHS = {
    "/favorites",
    "/account",
    "/account/password",
    "/api/account/password",
    "/downloads",
    "/downloads/batch",
}
_USER_PREFIXES = (
    "/favorites/",
    "/api/favorites",
    "/api/download-tasks",
    "/api/episode-batches",
)


def required_role(method: str, path: str) -> Optional[str]:
    """Return the minimum role required for an HTTP route."""

    method = str(method or "").upper()
    path = str(path or "/")
    if path in _PUBLIC_EXACT_PATHS:
        return None
    if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
        return None
    if method in {"GET", "HEAD", "OPTIONS"}:
        if path in _USER_EXACT_PATHS or any(
            path.startswith(prefix) for prefix in _USER_PREFIXES
        ):
            return ROLE_USER
        # All remaining read APIs and management pages are administrative.
        if path.startswith("/api/") or path not in {"/robots.txt"}:
            return ROLE_ADMIN
        return None
    if path in _USER_EXACT_PATHS or any(
        path.startswith(prefix) for prefix in _USER_PREFIXES
    ):
        return ROLE_USER
    # Unknown unsafe routes remain administrator-only; FastAPI will return
    # 404 to an authorized request without exposing them to guests.
    return ROLE_ADMIN
