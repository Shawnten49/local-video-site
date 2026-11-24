from __future__ import annotations

from typing import Dict, Optional

from local_video_site.identity.permissions.account import Identity


def is_task_owner(identity: Identity, task: Dict) -> bool:
    owner_id = task.get("created_by_user_id")
    return bool(
        identity.authenticated
        and owner_id is not None
        and int(owner_id) == int(identity.user_id)
    )


def can_view_task(identity: Identity, task: Dict) -> bool:
    return identity.is_admin or is_task_owner(identity, task)


def can_control_task(identity: Identity, task: Dict) -> bool:
    return can_view_task(identity, task)


def can_delete_task_record(identity: Identity, task: Dict) -> bool:
    return can_view_task(identity, task) and task.get("status") == "success"


def can_delete_task_files(identity: Identity, task: Dict) -> bool:
    return identity.is_admin


def can_view_batch(identity: Identity, batch: Dict) -> bool:
    owner_id = batch.get("created_by_user_id")
    return bool(
        identity.is_admin
        or (
            identity.authenticated
            and owner_id is not None
            and int(owner_id) == int(identity.user_id)
        )
    )


def allowed_download_access_levels(identity: Identity):
    if identity.is_admin:
        return (0, 1, 2)
    return (0, 1)
