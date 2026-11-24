from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from local_video_site import database
from local_video_site.settings import SettingError, get_runtime_settings
from ..audit import client_ip
from ..models.settings import (
    DeleteVerificationUpdate,
    SettingReset,
    SettingUpdate,
)
from ..templates import TEMPLATES


router = APIRouter()


def _actor_context(request: Request) -> dict:
    identity = request.state.identity
    return {
        "actor_user_id": identity.user_id,
        "actor_username": identity.username,
        "client_ip": client_ip(request),
        "request_id": request.headers.get("x-request-id", ""),
    }


def _http_error(exc: SettingError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": str(exc),
        },
    )


@router.get("/manage/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    service = get_runtime_settings()
    identity = request.state.identity
    return TEMPLATES.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "identity": identity,
            "active_page": "settings",
            "categories": service.categories(),
            "download_counts": database.download_task_counts(
                owner_user_id=identity.user_id,
                owned_only=False,
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/manage/settings")
def api_settings(
    category: str = "",
    q: str = "",
    include_advanced: bool = True,
):
    try:
        service = get_runtime_settings()
        return {
            "categories": service.categories(),
            "items": service.list_items(
                category=category.strip(),
                query=q.strip(),
                include_advanced=include_advanced,
            ),
        }
    except SettingError as exc:
        raise _http_error(exc) from exc


@router.get("/api/manage/settings/audit")
def api_settings_audit(
    setting_key: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    try:
        return get_runtime_settings().audit(
            setting_key=setting_key.strip(),
            page=page,
            page_size=page_size,
        )
    except SettingError as exc:
        raise _http_error(exc) from exc


@router.patch("/api/manage/settings/{setting_key}")
def api_update_setting(
    request: Request,
    setting_key: str,
    selection: SettingUpdate,
):
    actor = _actor_context(request)
    try:
        return get_runtime_settings().update(
            setting_key,
            selection.value,
            expected_version=selection.expected_version,
            note=selection.note,
            **actor,
        )
    except SettingError as exc:
        raise _http_error(exc) from exc


@router.post("/api/manage/settings/{setting_key}/reset")
def api_reset_setting(
    request: Request,
    setting_key: str,
    selection: SettingReset,
):
    actor = _actor_context(request)
    try:
        return get_runtime_settings().reset(
            setting_key,
            expected_version=selection.expected_version,
            note=selection.note,
            **actor,
        )
    except SettingError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/api/manage/settings/secrets/delete-verification"
)
def api_update_delete_verification(
    request: Request,
    selection: DeleteVerificationUpdate,
):
    actor = _actor_context(request)
    try:
        return get_runtime_settings().change_delete_verification(
            current_password=selection.current_password,
            new_password=selection.new_password,
            confirmation=selection.confirmation,
            note=selection.note,
            **actor,
        )
    except SettingError as exc:
        raise _http_error(exc) from exc
