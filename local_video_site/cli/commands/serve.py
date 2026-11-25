from __future__ import annotations

import logging

from local_video_site import database
from local_video_site.core import config
from local_video_site.identity.bootstrap import (
    default_admin_pending_change,
    ensure_bootstrap_admin,
)


def publish_error() -> str:
    if not config.lan_mode():
        return ""
    if database.active_admin_count() > 0:
        if (
            default_admin_pending_change()
            and not config.allow_default_admin_lan()
        ):
            return (
                "局域网模式启动失败：默认管理员 admin 尚未修改初始密码。"
                "请先在本机启动并登录 admin，修改密码后再使用 --lan。"
            )
        return ""
    return (
        "局域网模式启动失败：当前没有有效管理员账号。"
        "请先在本机启动服务并访问 /setup，或运行 "
        "`.venv/bin/python -m local_video_site users bootstrap "
        "--username admin`，完成初始化后再使用 --lan。"
    )


def run(args, logger: logging.Logger) -> int:
    database.init_db()
    bootstrap = ensure_bootstrap_admin()
    if bootstrap["status"] == "created":
        logger.warning(
            "Default administrator created username=%s "
            "must_change_password=true",
            bootstrap["username"],
            extra={"operation": "auth.bootstrap.default_admin"},
        )
    lan_error = publish_error()
    if lan_error:
        print(lan_error)
        logger.error(
            "LAN startup denied: no active administrator",
            extra={"operation": "lan.startup.denied"},
        )
        return 1
    if config.lan_mode():
        logger.info(
            "LAN access policy: guests=level 0; users=levels 0-1 and own "
            "downloads; admins=all levels and management",
            extra={"operation": "lan.policy"},
        )
    import uvicorn

    if getattr(args, "open", False):
        import threading
        import webbrowser

        threading.Timer(
            1.5,
            lambda: webbrowser.open(
                "http://{}:{}".format(args.host, args.port)
            ),
        ).start()
    uvicorn.run(
        "local_video_site.app:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_config=None,
        timeout_graceful_shutdown=10,
    )
    return 0
