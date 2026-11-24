from __future__ import annotations

from typing import Optional

from fastapi import Request

from local_video_site import database


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def audit_account_action(
    request: Request,
    action: str,
    result: str,
    target_type: str = "",
    target_id: Optional[str] = None,
    actor_username: str = "",
    details: Optional[dict] = None,
) -> None:
    identity = getattr(request.state, "identity", None)
    database.write_auth_audit(
        action=action,
        result=result,
        actor_user_id=identity.user_id if identity else None,
        actor_username=(
            actor_username
            or (identity.username if identity else "")
        ),
        target_type=target_type,
        target_id=target_id,
        request_id=request.headers.get("x-request-id", ""),
        client_ip=client_ip(request),
        details=details,
    )
