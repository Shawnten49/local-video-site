from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from local_video_site.identity.auth import AccountAccessMiddleware, LanAccessMiddleware
from local_video_site.core.logging import RequestLoggingMiddleware
from local_video_site.web.security import SecurityHeadersMiddleware
from local_video_site.web.routes.tv import router as tv_api_router
from .lifespan import lifespan
from .routes.accounts import router as accounts_router
from .routes.actors import router as actors_router
from .routes.categories import router as categories_router
from .routes.delete_tasks import router as delete_tasks_router
from .routes.downloads import router as downloads_router
from .routes.external_scans import router as external_scans_router
from .routes.imports import router as imports_router
from .routes.media import router as media_router
from .routes.migrations import router as migrations_router
from .routes.playback import router as playback_router
from .routes.series import router as series_router
from .routes.settings import router as settings_router
from .routes.system import router as system_router
from .routes.transport import router as transport_router
from .routes.videos import router as videos_router


PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class VersionedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.startswith("vendor/movi-player/"):
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
            )
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="本地视频库",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(LanAccessMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(AccountAccessMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    for router in (
        tv_api_router,
        system_router,
        series_router,
        delete_tasks_router,
        accounts_router,
        settings_router,
        downloads_router,
        videos_router,
        categories_router,
        actors_router,
        playback_router,
        media_router,
        migrations_router,
        imports_router,
        external_scans_router,
        transport_router,
    ):
        app.include_router(router)

    app.mount(
        "/static",
        VersionedStaticFiles(directory=str(PACKAGE_ROOT / "static")),
        name="static",
    )
    return app
