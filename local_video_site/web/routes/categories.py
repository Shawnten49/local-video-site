from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from local_video_site import database
from local_video_site.identity.permissions.content import max_level_for
from ..formatting import format_bytes, format_duration, format_resolution
from ..models.scans import (
    ManualCategoryCreate,
    VideoManualCategorySelection,
)
from ..templates import TEMPLATES
from ..urls import current_return_path


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")


@router.get("/categories", response_class=HTMLResponse)
def categories(
    request: Request,
    q: str = "",
    page: int = Query(1, ge=1),
):
    data = database.list_categories(
        query=q.strip(),
        page=page,
        page_size=160,
    )
    manual_categories = (
        database.list_manual_categories(query=q.strip())
        if page == 1
        else []
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="categories.html",
        context={
            "data": data,
            "manual_categories": manual_categories,
            "query": q,
            "page": data["page"],
        },
    )


@router.get("/favorites", response_class=HTMLResponse)
def favorites(
    request: Request,
    sort: str = "favorited",
    page: int = Query(1, ge=1),
):
    identity = request.state.identity
    max_access_level = max_level_for(identity)
    data = database.list_videos(
        favorite_only=True,
        favorite_user_id=identity.user_id,
        sort=sort,
        page=page,
        page_size=36,
        max_access_level=max_access_level,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="favorites.html",
        context={
            "data": data,
            "identity": identity,
            "sort": sort,
            "return_to": current_return_path(request),
            "format_duration": format_duration,
            "format_bytes": format_bytes,
            "format_resolution": format_resolution,
        },
    )


@router.get("/categories/{category_id}", response_class=HTMLResponse)
def category_videos(
    request: Request,
    category_id: int,
    sort: str = "recent",
    page: int = Query(1, ge=1),
):
    category = database.get_category(category_id)
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")
    data = database.list_videos(
        category_id=category_id,
        sort=sort,
        page=page,
        page_size=36,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="category_videos.html",
        context={
            "category": category,
            "data": data,
            "sort": sort,
            "is_manual": False,
            "pager_action": "/categories/{}".format(category_id),
            "return_to": current_return_path(request),
            "format_duration": format_duration,
            "format_bytes": format_bytes,
            "format_resolution": format_resolution,
        },
    )


@router.get("/manual-categories/{manual_category_id}", response_class=HTMLResponse)
def manual_category_videos(
    request: Request,
    manual_category_id: int,
    sort: str = "recent",
    page: int = Query(1, ge=1),
):
    category = database.get_manual_category(manual_category_id)
    if not category:
        raise HTTPException(status_code=404, detail="人工标签不存在")
    data = database.list_videos(
        manual_category_id=manual_category_id,
        sort=sort,
        page=page,
        page_size=36,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="category_videos.html",
        context={
            "category": category,
            "data": data,
            "sort": sort,
            "is_manual": True,
            "pager_action": "/manual-categories/{}".format(
                manual_category_id
            ),
            "return_to": current_return_path(request),
            "format_duration": format_duration,
            "format_bytes": format_bytes,
            "format_resolution": format_resolution,
        },
    )


@router.get("/api/manual-categories")
def api_manual_categories(q: str = ""):
    return {"items": database.list_manual_categories(query=q.strip())}


@router.post("/api/manual-categories")
def api_create_manual_category(selection: ManualCategoryCreate):
    try:
        category = database.create_manual_category(selection.name)
    except ValueError as exc:
        status_code = 409 if "已经存在" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    LOGGER.info(
        "Manual category created manual_category_id=%s name=%s",
        category["id"],
        category["name"],
        extra={"operation": "manual_category.create"},
    )
    return category


@router.delete("/api/manual-categories/{manual_category_id}")
def api_delete_manual_category(manual_category_id: int):
    category = database.delete_manual_category(manual_category_id)
    if not category:
        raise HTTPException(status_code=404, detail="人工标签不存在")
    LOGGER.info(
        "Manual category deleted manual_category_id=%s name=%s",
        manual_category_id,
        category["name"],
        extra={"operation": "manual_category.delete"},
    )
    return {"status": "deleted", "id": manual_category_id}


@router.get("/api/videos/{video_id}/manual-categories")
def api_video_manual_categories(video_id: int):
    if not database.get_video(video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    return {
        "items": database.manual_categories_for_video(video_id),
    }


@router.get("/api/videos/{video_id}/manual-category-options")
def api_video_manual_category_options(video_id: int):
    if not database.get_video(video_id):
        raise HTTPException(status_code=404, detail="视频不存在")
    selected = {
        int(item["id"])
        for item in database.manual_categories_for_video(video_id)
    }
    items = database.list_manual_categories()
    for item in items:
        item["selected"] = int(item["id"]) in selected
    return {"items": items}


@router.put("/api/videos/{video_id}/manual-categories")
def api_update_video_manual_categories(
    video_id: int,
    selection: VideoManualCategorySelection,
):
    try:
        items = database.set_video_manual_categories(
            video_id,
            selection.manual_category_ids,
        )
    except ValueError as exc:
        status_code = 404 if "视频不存在" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    LOGGER.info(
        "Video manual categories updated video_id=%s count=%s",
        video_id,
        len(items),
        extra={"operation": "video.manual_categories.update"},
    )
    return {"items": items}


@router.get("/api/categories")
def api_categories(
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(160, ge=1, le=500),
):
    return database.list_categories(
        query=q,
        page=page,
        page_size=page_size,
    )
