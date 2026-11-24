from __future__ import annotations

from typing import Dict, Optional

from local_video_site.identity.permissions.account import ROLE_ADMIN, ROLE_GUEST, ROLE_USER, Identity


ACCESS_LEVEL_PUBLIC = 0
ACCESS_LEVEL_USER = 1
ACCESS_LEVEL_ADMIN = 2
ACCESS_LEVELS = (
    ACCESS_LEVEL_PUBLIC,
    ACCESS_LEVEL_USER,
    ACCESS_LEVEL_ADMIN,
)
ACCESS_LEVEL_LABELS = {
    ACCESS_LEVEL_PUBLIC: "所有人",
    ACCESS_LEVEL_USER: "登录用户",
    ACCESS_LEVEL_ADMIN: "管理员",
}
ROLE_MAX_ACCESS_LEVEL = {
    ROLE_GUEST: ACCESS_LEVEL_PUBLIC,
    ROLE_USER: ACCESS_LEVEL_USER,
    ROLE_ADMIN: ACCESS_LEVEL_ADMIN,
}


def validate_access_level(value) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("访问级别必须是 0、1 或 2") from exc
    if level not in ACCESS_LEVELS:
        raise ValueError("访问级别必须是 0、1 或 2")
    return level


def max_level_for(identity: Optional[Identity]) -> int:
    role = identity.role if identity is not None else ROLE_GUEST
    return ROLE_MAX_ACCESS_LEVEL.get(role, ACCESS_LEVEL_PUBLIC)


def can_view(identity: Optional[Identity], video: Dict) -> bool:
    return int(video.get("access_level") or ACCESS_LEVEL_PUBLIC) <= max_level_for(
        identity
    )


def access_level_label(value) -> str:
    try:
        level = int(value)
    except (TypeError, ValueError):
        level = ACCESS_LEVEL_PUBLIC
    return ACCESS_LEVEL_LABELS.get(level, str(level))
