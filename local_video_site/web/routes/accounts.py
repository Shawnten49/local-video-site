from __future__ import annotations

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from local_video_site import database
from local_video_site.core import config
from local_video_site.identity.bootstrap import default_admin_pending_change
from local_video_site.identity.permissions.account import ROLE_ADMIN, ROLE_USER
from local_video_site.identity.accounts import AccountError, SESSION_COOKIE_NAME, authenticate as authenticate_account, change_own_password, create_session, create_user as create_account, normalize_username, reset_user_password
from ..audit import audit_account_action, client_ip
from ..models.accounts import (
    OwnPasswordChange,
    UserCreate,
    UserPasswordReset,
    UserUpdate,
)
from ..templates import TEMPLATES
from ..urls import safe_return_path


LOGGER = logging.getLogger("local_video_site.identity.accounts")
router = APIRouter()


async def _read_form(request: Request) -> dict:
    body = await request.body()
    try:
        values = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=False,
        )
    except UnicodeDecodeError:
        return {}
    return {
        key: value[-1] if value else ""
        for key, value in values.items()
    }


def _set_session_cookie(response, token: str, remember: bool) -> None:
    days = (
        config.remember_session_days()
        if remember
        else config.session_days()
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=days * 86400,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def _is_local_setup_client(request: Request) -> bool:
    return client_ip(request) in {"127.0.0.1", "::1", "testclient", ""}


def _login_page(
    request: Request,
    *,
    error: str = "",
    next_url: str = "/",
    status_code: int = 200,
):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="login.html",
        status_code=status_code,
        context={
            "error": error,
            "next_url": safe_return_path(next_url),
            "setup_available": (
                database.user_count() == 0
                and _is_local_setup_client(request)
            ),
            "default_admin_pending": default_admin_pending_change(),
            "min_password_length": config.min_password_length(),
        },
        headers={"Cache-Control": "no-store"},
    )


def _setup_page(
    request: Request,
    error: str = "",
    status_code: int = 200,
):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="setup.html",
        status_code=status_code,
        context={
            "error": error,
            "min_password_length": config.min_password_length(),
        },
        headers={"Cache-Control": "no-store"},
    )


def _validate_role(role: str) -> str:
    if role not in {ROLE_USER, ROLE_ADMIN}:
        raise HTTPException(status_code=422, detail="账号角色无效")
    return role


def _public_user(user: dict) -> dict:
    return {
        key: user.get(key)
        for key in (
            "id",
            "username",
            "role",
            "is_active",
            "must_change_password",
            "created_at",
            "updated_at",
            "last_login_at",
        )
    }


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if request.state.identity.authenticated:
        return RedirectResponse(
            safe_return_path(next),
            status_code=303,
        )
    return _login_page(request, next_url=next)


@router.post("/login")
async def login_submit(request: Request):
    form = await _read_form(request)
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    remember = str(form.get("remember") or "") in {"1", "on", "true"}
    next_url = safe_return_path(str(form.get("next") or "/"))
    ip = client_ip(request)
    try:
        normalized = normalize_username(username)
    except AccountError:
        normalized = ""
    if (
        normalized
        and database.recent_login_failure_count(
            normalized,
            ip,
            minutes=config.login_failure_window_minutes(),
        ) >= config.login_max_failures()
    ):
        return _login_page(
            request,
            error="登录尝试过多，请稍后再试",
            next_url=next_url,
            status_code=429,
        )
    user = authenticate_account(username, password)
    if user is None:
        if normalized:
            database.record_login_attempt(
                normalized,
                ip,
                False,
                "invalid_credentials",
            )
        database.write_auth_audit(
            action="login.failed",
            result="denied",
            actor_username=username,
            client_ip=ip,
            request_id=request.headers.get("x-request-id", ""),
        )
        return _login_page(
            request,
            error="用户名或密码错误",
            next_url=next_url,
            status_code=401,
        )
    if normalized:
        database.record_login_attempt(normalized, ip, True)
    session_id, token, _ = create_session(
        int(user["id"]),
        remember=remember,
        client_ip=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    LOGGER.info(
        "Account login user_id=%s role=%s session_id=%s",
        user["id"],
        user["role"],
        session_id,
        extra={"operation": "auth.login.success"},
    )
    response = RedirectResponse(
        "/account" if user.get("must_change_password") else next_url,
        status_code=303,
    )
    _set_session_cookie(response, token, remember)
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if database.user_count() > 0:
        raise HTTPException(status_code=404, detail="站点已经完成初始化")
    if not _is_local_setup_client(request):
        raise HTTPException(status_code=403, detail="只能在本机创建首个管理员")
    return _setup_page(request)


@router.post("/setup")
async def setup_submit(request: Request):
    if database.user_count() > 0:
        raise HTTPException(status_code=404, detail="站点已经完成初始化")
    if not _is_local_setup_client(request):
        raise HTTPException(status_code=403, detail="只能在本机创建首个管理员")
    form = await _read_form(request)
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    if password != str(form.get("confirmation") or ""):
        return _setup_page(request, "两次输入的密码不一致", 422)
    try:
        user = create_account(
            username,
            password,
            role=ROLE_ADMIN,
            must_change_password=False,
        )
        database.migrate_legacy_favorites(int(user["id"]))
    except (AccountError, ValueError) as exc:
        return _setup_page(request, str(exc), 422)
    session_id, token, _ = create_session(
        int(user["id"]),
        remember=True,
        client_ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    LOGGER.info(
        "Initial administrator created user_id=%s session_id=%s",
        user["id"],
        session_id,
        extra={"operation": "auth.bootstrap.success"},
    )
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, token, True)
    return response


@router.post("/logout")
def logout(request: Request):
    identity = request.state.identity
    if identity.session_id is not None:
        database.revoke_user_session(identity.session_id)
    audit_account_action(request, "logout", "success")
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "identity": request.state.identity,
            "min_password_length": config.min_password_length(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/account/password")
async def account_password_form(request: Request):
    form = await _read_form(request)
    identity = request.state.identity
    try:
        change_own_password(
            identity.user_id,
            str(form.get("current_password") or ""),
            str(form.get("new_password") or ""),
            keep_session_id=identity.session_id,
        )
    except (AccountError, ValueError) as exc:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="account.html",
            status_code=422,
            context={
                "identity": identity,
                "error": str(exc),
                "min_password_length": config.min_password_length(),
            },
        )
    audit_account_action(request, "user.password_changed", "success")
    return TEMPLATES.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "identity": identity,
            "notice": "密码已更新",
            "min_password_length": config.min_password_length(),
        },
    )


@router.post("/api/account/password")
def api_account_password(
    request: Request,
    selection: OwnPasswordChange,
):
    identity = request.state.identity
    change_own_password(
        identity.user_id,
        selection.current_password,
        selection.new_password,
        keep_session_id=identity.session_id,
    )
    audit_account_action(request, "user.password_changed", "success")
    return {"status": "password_changed"}


@router.get("/manage/users", response_class=HTMLResponse)
def manage_users(request: Request):
    identity = request.state.identity
    return TEMPLATES.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "users": database.list_users(),
            "identity": identity,
            "download_counts": database.download_task_counts(
                owner_user_id=identity.user_id,
                owned_only=False,
            ),
            "active_page": "users",
            "min_password_length": config.min_password_length(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/users")
def api_users():
    return {"items": database.list_users()}


@router.post("/api/users")
def api_create_user(request: Request, selection: UserCreate):
    role = _validate_role(selection.role)
    try:
        user = create_account(
            selection.username,
            selection.password,
            role=role,
            created_by_user_id=request.state.identity.user_id,
            must_change_password=selection.must_change_password,
        )
    except (AccountError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_account_action(
        request,
        "user.created",
        "success",
        "user",
        str(user["id"]),
        details={"role": role},
    )
    return {"status": "created", "user": _public_user(user)}


@router.patch("/api/users/{user_id}")
def api_update_user(
    request: Request,
    user_id: int,
    selection: UserUpdate,
):
    normalized = None
    if selection.username is not None:
        try:
            normalized = normalize_username(selection.username)
        except AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if selection.role is not None:
        _validate_role(selection.role)
    try:
        database.update_user_account(
            user_id,
            username=selection.username.strip() if selection.username else None,
            normalized_username=normalized,
            role=selection.role,
            is_active=selection.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_account_action(
        request,
        "user.updated",
        "success",
        "user",
        str(user_id),
        details=selection.model_dump(exclude_none=True),
    )
    updated_user = database.get_user(user_id)
    if updated_user is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {
        "status": "updated",
        "user": _public_user(updated_user),
    }


@router.post("/api/users/{user_id}/reset-password")
def api_reset_user_password(
    request: Request,
    user_id: int,
    selection: UserPasswordReset,
):
    if database.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        reset_user_password(
            user_id,
            selection.password,
            must_change_password=selection.must_change_password,
        )
    except (AccountError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit_account_action(
        request,
        "user.password_reset",
        "success",
        "user",
        str(user_id),
    )
    return {"status": "password_reset"}


def _set_account_active(request: Request, user_id: int, active: bool):
    if database.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        database.update_user_account(user_id, is_active=active)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_account_action(
        request,
        "user.enabled" if active else "user.disabled",
        "success",
        "user",
        str(user_id),
    )
    return {"status": "enabled" if active else "disabled"}


@router.post("/api/users/{user_id}/disable")
def api_disable_user(request: Request, user_id: int):
    return _set_account_active(request, user_id, False)


@router.post("/api/users/{user_id}/enable")
def api_enable_user(request: Request, user_id: int):
    return _set_account_active(request, user_id, True)


@router.post("/manage/users/create")
async def manage_users_create(request: Request):
    form = await _read_form(request)
    try:
        create_account(
            str(form.get("username") or ""),
            str(form.get("password") or ""),
            role=_validate_role(str(form.get("role") or ROLE_USER)),
            created_by_user_id=request.state.identity.user_id,
            must_change_password=True,
        )
    except (AccountError, ValueError, HTTPException):
        pass
    return RedirectResponse("/manage/users", status_code=303)


@router.post("/manage/users/{user_id}/toggle")
def manage_users_toggle(user_id: int):
    user = database.get_user(user_id)
    if user:
        try:
            database.update_user_account(
                user_id,
                is_active=not bool(user["is_active"]),
            )
        except ValueError:
            pass
    return RedirectResponse("/manage/users", status_code=303)


@router.post("/manage/users/{user_id}/role")
async def manage_users_role(request: Request, user_id: int):
    form = await _read_form(request)
    try:
        role = _validate_role(str(form.get("role") or ""))
        database.update_user_account(user_id, role=role)
        audit_account_action(
            request,
            "user.role_changed",
            "success",
            "user",
            str(user_id),
            details={"role": role},
        )
    except (HTTPException, ValueError):
        pass
    return RedirectResponse("/manage/users", status_code=303)


@router.post("/manage/users/{user_id}/reset-password")
async def manage_users_reset_password(request: Request, user_id: int):
    form = await _read_form(request)
    try:
        reset_user_password(
            user_id,
            str(form.get("password") or ""),
            must_change_password=True,
        )
        audit_account_action(
            request,
            "user.password_reset",
            "success",
            "user",
            str(user_id),
        )
    except (AccountError, ValueError):
        pass
    return RedirectResponse("/manage/users", status_code=303)
