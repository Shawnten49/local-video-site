from __future__ import annotations

import asyncio
import html
import logging
import math
import threading
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.categories import replace_video_metadata
from local_video_site.identity.permissions.content import can_view
from local_video_site.storage.deletion import delete_password_configured, verify_delete_password
from local_video_site.integrations.metadata import fetch_site_metadata, metadata_parser_version, metadata_provider_for_url
from local_video_site.media.editing.rename import VideoRenameError, rename_video
from local_video_site.media.assets.thumbnails import generate_custom_cover, generate_thumbnail
from local_video_site.storage.deletion_tasks import DeleteTaskError, create_delete_batch
from local_video_site.storage.volumes import resolve_video_storage
from ..delete_rate import (
    clear_delete_failures as _clear_delete_failures,
    delete_rate_allowed as _delete_rate_allowed,
    delete_rate_key as _delete_rate_key,
    record_delete_failure as _record_delete_failure,
)
from ..models.playback import (
    CoverSelection,
    DeleteVideoSelection,
    RenameVideoSelection,
)
from ..templates import TEMPLATES
from ..urls import safe_return_path, watch_url


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")
COVER_GENERATION_LOCK = threading.Lock()


def _visible_video_or_404(request: Request, video_id: int) -> dict:
    video = database.get_video(int(video_id))
    if not video or not can_view(request.state.identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    return video


@router.post("/api/videos/{video_id}/metadata/refresh")
async def refresh_video_source_metadata(video_id: int):
    video = database.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    season = database.season_context_for_video(video_id)
    metadata_video_id = video_id
    metadata_video = video
    if season and season.get("metadata_video_id"):
        target_id = int(season["metadata_video_id"])
        target_video = database.get_video(target_id)
        if target_video is not None:
            metadata_video_id = target_id
            metadata_video = target_video
    source_url = str(metadata_video.get("original_url") or "").strip()
    provider = metadata_provider_for_url(source_url)
    if not provider:
        raise HTTPException(
            status_code=422,
            detail="当前视频没有可用的演员和分类来源地址",
        )
    try:
        result = await asyncio.to_thread(
            lambda: fetch_site_metadata(source_url, logger=LOGGER)
        )
    except Exception as exc:
        LOGGER.exception(
            "Video metadata refresh fetch failed video_id=%s provider=%s",
            video_id,
            provider,
            extra={"operation": "video.metadata.refresh.failed"},
        )
        raise HTTPException(
            status_code=502,
            detail="从原始页面获取演员和分类失败",
        ) from exc
    if not result.categories and not result.actors:
        raise HTTPException(
            status_code=502,
            detail=result.error or "原始页面没有返回演员或分类信息",
        )
    try:
        database.save_category_fetch_cache(
            video_id=metadata_video_id,
            source_url=source_url,
            status="fetched",
            categories=result.categories,
            actors=result.actors,
            http_status=result.http_status,
            parser_version=metadata_parser_version(provider),
            actor_metadata_version=database.ACTOR_METADATA_VERSION,
            metadata_language=result.language,
        )
        updated = replace_video_metadata(
            video_id=metadata_video_id,
            categories=result.categories,
            actors=result.actors,
            provider=provider,
            source_url=source_url,
        )
    except Exception as exc:
        LOGGER.exception(
            "Video metadata refresh save failed video_id=%s provider=%s",
            video_id,
            provider,
            extra={"operation": "video.metadata.refresh.failed"},
        )
        raise HTTPException(
            status_code=500,
            detail="演员和分类信息写入失败",
        ) from exc
    LOGGER.info(
        "Video metadata refreshed video_id=%s provider=%s "
        "metadata_video_id=%s categories=%s actors=%s",
        video_id,
        provider,
        metadata_video_id,
        len(updated["categories"]),
        len(updated["actors"]),
        extra={"operation": "video.metadata.refresh.complete"},
    )
    return {
        "status": "success",
        "provider": provider,
        "video_id": metadata_video_id,
        "category_count": len(updated["categories"]),
        "actor_count": len(updated["actors"]),
        "categories": updated["categories"],
        "actors": updated["actors"],
    }


@router.post("/favorites/{video_id}/add")
def add_favorite(request: Request, video_id: int, return_to: str = ""):
    video = _visible_video_or_404(request, video_id)
    database.add_user_favorite(request.state.identity.user_id, video_id)
    LOGGER.info(
        "Favorite added video_id=%s title=%s",
        video_id,
        video["title"],
        extra={"operation": "favorite.add"},
    )
    return RedirectResponse(
        url=watch_url(video_id, return_to),
        status_code=303,
    )


@router.get("/favorites/{video_id}/add")
def legacy_add_favorite(request: Request, video_id: int, return_to: str = ""):
    safe_return = safe_return_path(return_to)
    LOGGER.warning(
        "Legacy GET favorite add intercepted video_id=%s",
        video_id,
        extra={"operation": "favorite.add.legacy_get"},
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="favorite_legacy_action.html",
        context={
            "heading": "请确认添加收藏",
            "message": "当前页面使用了旧版收藏链接，请确认后使用新版操作提交。",
            "submit_label": "确认添加收藏",
            "form_action": "/favorites/{}/add?{}".format(
                int(video_id),
                urlencode({"return_to": safe_return}),
            ),
            "cancel_url": watch_url(video_id, safe_return),
        },
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/favorites/{video_id}/remove")
def remove_favorite(
    request: Request,
    video_id: int,
    return_to: str = "",
    redirect_to: str = "",
):
    user_id = int(request.state.identity.user_id)
    if not database.is_user_favorite(user_id, video_id):
        raise HTTPException(status_code=404, detail="收藏不存在")
    database.remove_user_favorite(user_id, video_id)
    LOGGER.info(
        "Favorite removed video_id=%s user_id=%s",
        video_id,
        user_id,
        extra={"operation": "favorite.remove"},
    )
    target = (
        safe_return_path(redirect_to)
        if redirect_to.strip()
        else watch_url(video_id, return_to)
    )
    return RedirectResponse(url=target, status_code=303)


@router.get("/favorites/{video_id}/remove")
def legacy_remove_favorite(
    request: Request,
    video_id: int,
    return_to: str = "",
):
    safe_return = safe_return_path(return_to)
    LOGGER.warning(
        "Legacy GET favorite remove intercepted video_id=%s",
        video_id,
        extra={"operation": "favorite.remove.legacy_get"},
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="favorite_legacy_action.html",
        context={
            "heading": "请确认取消收藏",
            "message": "当前页面使用了旧版收藏链接，请确认后使用新版操作提交。",
            "submit_label": "确认取消收藏",
            "form_action": "/favorites/{}/remove?{}".format(
                int(video_id),
                urlencode({"redirect_to": safe_return}),
            ),
            "cancel_url": safe_return,
        },
        headers={"Cache-Control": "private, no-store"},
    )


@router.patch("/api/videos/{video_id}/name")
def api_rename_video(video_id: int, selection: RenameVideoSelection):
    if not database.get_video(video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    LOGGER.info(
        "Video rename requested video_id=%s",
        video_id,
        extra={"operation": "video.rename.start"},
    )
    try:
        result = rename_video(
            video_id=video_id,
            requested_title=selection.title,
            expected_path=selection.expected_path,
        )
    except VideoRenameError as exc:
        LOGGER.warning(
            "Video rename rejected video_id=%s code=%s message=%s",
            video_id,
            exc.code,
            exc,
            extra={"operation": "video.rename.rejected"},
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        LOGGER.exception(
            "Video rename failed video_id=%s",
            video_id,
            extra={"operation": "video.rename.failed"},
        )
        raise HTTPException(
            status_code=500,
            detail="修改视频名称失败",
        ) from exc
    result["redirect_url"] = watch_url(video_id, selection.return_to)
    return result


@router.post("/api/videos/{video_id}/cover")
def update_video_cover(video_id: int, selection: CoverSelection):
    video = database.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    timestamp = float(selection.timestamp)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise HTTPException(status_code=422, detail="封面时间点无效")
    duration = video.get("duration")
    if duration is not None and timestamp > float(duration):
        raise HTTPException(status_code=422, detail="封面时间点超过视频时长")
    if duration is not None:
        timestamp = min(timestamp, max(0.0, float(duration) - 0.1))
    resolution = resolve_video_storage(video)
    if resolution["state"] == "external_offline":
        raise HTTPException(
            status_code=409,
            detail="外置存储未连接，请连接移动硬盘后重试",
        )
    path = resolution["path"]
    if not path.is_file():
        database.mark_video_missing(video_id)
        raise HTTPException(status_code=404, detail="视频文件不存在")
    if not COVER_GENERATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="正在生成其他视频封面")
    try:
        LOGGER.info(
            "Cover update started video_id=%s timestamp=%.3f",
            video_id,
            timestamp,
            extra={"operation": "cover.update.start"},
        )
        thumbnail = generate_custom_cover(
            video_path=path,
            size=video["size"],
            mtime_ns=video["mtime_ns"],
            timestamp=timestamp,
        )
        if not thumbnail:
            LOGGER.error(
                "Cover generation failed video_id=%s timestamp=%.3f",
                video_id,
                timestamp,
                extra={"operation": "cover.update.failed"},
            )
            raise HTTPException(status_code=500, detail="封面生成失败")
        database.update_custom_cover(
            video_id,
            str(thumbnail),
            timestamp,
        )
    finally:
        COVER_GENERATION_LOCK.release()
    version = str(thumbnail.stat().st_mtime_ns)
    LOGGER.info(
        "Cover updated video_id=%s timestamp=%.3f path=%s",
        video_id,
        timestamp,
        thumbnail,
        extra={"operation": "cover.update.complete"},
    )
    return {
        "status": "ok",
        "thumbnail_url": "/thumb/{}?v={}".format(video_id, version),
        "timestamp": timestamp,
    }


@router.delete("/api/videos/{video_id}/cover")
def restore_automatic_cover(video_id: int):
    video = database.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    resolution = resolve_video_storage(video)
    if resolution["state"] == "external_offline":
        raise HTTPException(
            status_code=409,
            detail="外置存储未连接，请连接移动硬盘后重试",
        )
    path = resolution["path"]
    if not path.is_file():
        database.mark_video_missing(video_id)
        raise HTTPException(status_code=404, detail="视频文件不存在")
    if not COVER_GENERATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="正在生成其他视频封面")
    try:
        LOGGER.info(
            "Automatic cover restore started video_id=%s",
            video_id,
            extra={"operation": "cover.reset.start"},
        )
        thumbnail = generate_thumbnail(
            video_path=path,
            size=video["size"],
            mtime_ns=video["mtime_ns"],
            duration=video.get("duration"),
            force=True,
        )
        if not thumbnail:
            LOGGER.error(
                "Automatic cover generation failed video_id=%s",
                video_id,
                extra={"operation": "cover.reset.failed"},
            )
            raise HTTPException(status_code=500, detail="自动封面生成失败")
        database.reset_auto_cover(video_id, str(thumbnail))
    finally:
        COVER_GENERATION_LOCK.release()
    version = str(thumbnail.stat().st_mtime_ns)
    LOGGER.info(
        "Automatic cover restored video_id=%s",
        video_id,
        extra={"operation": "cover.reset.complete"},
    )
    return {
        "status": "ok",
        "thumbnail_url": "/thumb/{}?v={}".format(video_id, version),
    }


@router.delete("/api/videos/{video_id}", status_code=202)
def delete_video_api(
    request: Request,
    video_id: int,
    selection: DeleteVideoSelection,
):
    key = _delete_rate_key(request, video_id)
    if not _delete_rate_allowed(key):
        LOGGER.warning(
            "Delete rate limited video_id=%s client=%s",
            video_id,
            key[0],
            extra={"operation": "video.delete.rate_limited"},
        )
        raise HTTPException(
            status_code=429,
            detail="密码尝试次数过多，请稍后再试",
        )
    if not delete_password_configured():
        raise HTTPException(
            status_code=409,
            detail="尚未配置删除校验码，请由管理员在参数配置中设置",
        )
    if not verify_delete_password(selection.password):
        _record_delete_failure(key)
        LOGGER.warning(
            "Delete password rejected video_id=%s client=%s",
            video_id,
            key[0],
            extra={"operation": "video.delete.denied"},
        )
        raise HTTPException(status_code=401, detail="删除校验码错误")
    try:
        LOGGER.info(
            "Video delete started video_id=%s client=%s",
            video_id,
            key[0],
            extra={"operation": "video.delete.start"},
        )
        result = create_delete_batch(
            scope_type="video",
            target_id=video_id,
            source_action="video_management",
            requested_by_user_id=request.state.identity.user_id,
            requested_by_username=request.state.identity.username,
        )
    except DeleteTaskError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except Exception as exc:
        LOGGER.exception(
            "Video deletion failed for %s",
            video_id,
            extra={"operation": "video.delete.failed"},
        )
        raise HTTPException(status_code=500, detail="删除失败") from exc
    _clear_delete_failures(key)
    LOGGER.info(
        "Video delete queued video_id=%s batch=%s client=%s",
        video_id,
        result["batch_id"],
        key[0],
        extra={"operation": "video.delete.queued"},
    )
    result["status"] = (
        "already_deleted"
        if result.get("already_deleted")
        else "queued"
    )
    result["redirect_url"] = "/manage/delete-tasks?q={}".format(
        result["batch_id"]
    )
    return result


@router.get("/thumb/{video_id}")
def thumbnail(request: Request, video_id: int):
    video = database.get_video(video_id)
    if not video or not can_view(request.state.identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    thumbnail_path = video.get("thumbnail_path")
    if thumbnail_path and Path(thumbnail_path).is_file():
        cache_control = (
            "private, no-store"
            if int(video.get("access_level") or 0) > 0
            else "public, max-age=86400"
        )
        return FileResponse(
            thumbnail_path,
            media_type="image/jpeg",
            headers={"Cache-Control": cache_control},
        )

    title = html.escape((video.get("title") or "Video")[:70])
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#202433"/><stop offset="1" stop-color="#0f1118"/>
    </linearGradient></defs>
    <rect width="640" height="360" fill="url(#g)"/>
    <circle cx="320" cy="157" r="45" fill="#ffffff" fill-opacity=".13"/>
    <path d="M306 132 L350 157 L306 182 Z" fill="#ffffff" fill-opacity=".82"/>
    <text x="320" y="248" text-anchor="middle" font-size="20"
      fill="#cbd0dc" font-family="-apple-system,BlinkMacSystemFont,sans-serif">{}</text>
    </svg>""".format(title)
    cache_control = (
        "private, no-store"
        if int(video.get("access_level") or 0) > 0
        else "public, max-age=300"
    )
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": cache_control},
    )


@router.get("/preview/{video_id}")
def video_preview(request: Request, video_id: int):
    video = database.get_video(video_id)
    if (
        not video
        or not can_view(request.state.identity, video)
        or video.get("preview_status") != "ready"
    ):
        raise HTTPException(status_code=404, detail="视频预览不存在")
    preview_path = config.PREVIEW_DIR / Path(
        video.get("preview_path") or ""
    ).name
    if not preview_path.is_file() or preview_path.stat().st_size <= 0:
        raise HTTPException(status_code=404, detail="视频预览文件不存在")
    database.touch_preview_access(video_id)
    cache_control = (
        "private, no-store"
        if int(video.get("access_level") or 0) > 0
        else "public, max-age=604800, immutable"
    )
    return FileResponse(
        preview_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": cache_control,
            "Accept-Ranges": "bytes",
        },
    )
