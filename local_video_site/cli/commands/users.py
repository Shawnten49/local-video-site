from __future__ import annotations

import getpass
import logging
import sys

from local_video_site import database
from local_video_site.identity.accounts import (
    AccountError,
    create_user as create_account,
    normalize_username,
    reset_user_password,
)


def run(args, logger: logging.Logger) -> int:
    database.init_db()
    if args.action == "list":
        for user in database.list_users():
            print(
                "{id}\t{username}\t{role}\t{status}\t{last_login}".format(
                    id=user["id"],
                    username=user["username"],
                    role=user["role"],
                    status="active" if user["is_active"] else "disabled",
                    last_login=user.get("last_login_at") or "-",
                )
            )
        return 0
    if not args.username:
        print("--username 不能为空", file=sys.stderr)
        return 2
    if args.action == "bootstrap":
        if database.user_count() > 0:
            print("站点已经存在账号，不能重复初始化", file=sys.stderr)
            return 1
        role = "admin"
    else:
        role = args.role
    if args.action == "reset-password":
        return _reset_password(args.username)
    return _create_account(args.username, role)


def _reset_password(username: str) -> int:
    user = database.get_user_by_normalized_username(normalize_username(username))
    if user is None:
        print("账号不存在", file=sys.stderr)
        return 1
    first = getpass.getpass("新密码: ")
    second = getpass.getpass("再次输入: ")
    if first != second:
        print("两次输入的密码不一致", file=sys.stderr)
        return 1
    try:
        reset_user_password(
            int(user["id"]),
            first,
            must_change_password=True,
        )
    except (AccountError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("密码已重置")
    return 0


def _create_account(username: str, role: str) -> int:
    first = getpass.getpass("密码: ")
    second = getpass.getpass("再次输入: ")
    if first != second:
        print("两次输入的密码不一致", file=sys.stderr)
        return 1
    try:
        user = create_account(
            username,
            first,
            role=role,
            must_change_password=False,
        )
    except (AccountError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if role == "admin" and database.active_admin_count() == 1:
        database.migrate_legacy_favorites(int(user["id"]))
    print("账号已创建: {}".format(user["username"]))
    return 0
