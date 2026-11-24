from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from local_video_site import database
from ..formatting import format_bytes, format_duration, format_resolution
from ..templates import TEMPLATES
from ..urls import current_return_path


router = APIRouter()


@router.get("/actors", response_class=HTMLResponse)
def actors(
    request: Request,
    q: str = "",
    page: int = Query(1, ge=1),
):
    data = database.list_actors(
        query=q.strip(),
        page=page,
        page_size=120,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="actors.html",
        context={
            "data": data,
            "query": q,
        },
    )


@router.get("/actors/{actor_id}", response_class=HTMLResponse)
def actor_videos(
    request: Request,
    actor_id: int,
    sort: str = "recent",
    page: int = Query(1, ge=1),
):
    actor = database.get_actor(actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    data = database.list_videos(
        actor_id=actor_id,
        sort=sort,
        page=page,
        page_size=36,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="actor_videos.html",
        context={
            "actor": actor,
            "data": data,
            "sort": sort,
            "return_to": current_return_path(request),
            "format_duration": format_duration,
            "format_bytes": format_bytes,
            "format_resolution": format_resolution,
        },
    )


@router.get("/actor-avatar/{actor_id}")
def actor_avatar(actor_id: int):
    avatar = database.get_actor_avatar_file(actor_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="演员头像不存在")
    return FileResponse(
        avatar["path"],
        headers={
            "Cache-Control": "public, max-age=604800, immutable",
        },
    )


@router.get("/api/actors")
def api_actors(
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(120, ge=1, le=500),
):
    return database.list_actors(
        query=q,
        page=page,
        page_size=page_size,
    )
