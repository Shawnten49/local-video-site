from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from local_video_site.core import config
from local_video_site import database
from local_video_site.downloads.policies.media import FORMAT_STRATEGIES, FORMAT_STRATEGY_LABELS, IDENTITY_MODES, KIND_OPTIONS, MERGE_OUTPUT_FORMATS, QUALITY_FALLBACKS, QUALITY_TARGET_LABELS, QUALITY_TARGETS, SOURCE_OPTIONS, get_effective_policy as get_media_policy, preset_options as media_preset_options, reset_policy as reset_media_policy, update_policy as update_media_policy
from local_video_site.downloads.policies.transport import LIFECYCLE_STATES, TRANSPORT_MODES, create_policy as create_transport_policy, get_policy as get_transport_policy, list_policies as list_transport_policies, perform_action as perform_transport_action, site_key_for_url, site_audit as transport_site_audit, site_events as transport_site_events, site_tasks as transport_site_tasks, update_policy as update_transport_policy
from ..models.scans import (
    MediaPolicyProbe,
    MediaPolicyUpdate,
    TransportPolicyAction,
    TransportPolicyCreate,
    TransportPolicyUpdate,
)
from ..formatting import format_bytes
from ..templates import TEMPLATES


router = APIRouter()


@router.get("/downloads/site-policies", response_class=HTMLResponse)
def download_site_policies(
    request: Request,
    q: str = "",
    state: str = "",
    mode: str = "",
):
    policies = list_transport_policies(
        query=q.strip(),
        state=state.strip(),
        mode=mode.strip(),
    )
    for policy in policies:
        policy["media_policy"] = get_media_policy(
            policy["site_key"]
        )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="transport_sites.html",
        context={
            "policies": policies,
            "query": q,
            "state_filter": state,
            "mode_filter": mode,
            "states": sorted(LIFECYCLE_STATES),
            "modes": sorted(TRANSPORT_MODES),
            "format_bytes": format_bytes,
        },
    )


@router.get("/downloads/site-policies/{site_key}", response_class=HTMLResponse)
def download_site_policy_detail(request: Request, site_key: str):
    policy = get_transport_policy(site_key)
    if not policy:
        raise HTTPException(status_code=404, detail="站点策略不存在")
    media_policy = get_media_policy(site_key)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="transport_site_detail.html",
        context={
            "policy": policy,
            "media_policy": media_policy,
            "media_presets": media_preset_options(),
            "media_source_options": SOURCE_OPTIONS,
            "media_identity_modes": IDENTITY_MODES,
            "media_kind_options": KIND_OPTIONS,
            "media_quality_targets": QUALITY_TARGETS,
            "media_quality_target_labels": QUALITY_TARGET_LABELS,
            "media_quality_fallbacks": QUALITY_FALLBACKS,
            "media_format_strategies": FORMAT_STRATEGIES,
            "media_format_strategy_labels": FORMAT_STRATEGY_LABELS,
            "media_merge_formats": MERGE_OUTPUT_FORMATS,
            "tasks": transport_site_tasks(site_key),
            "events": transport_site_events(site_key),
            "audit": transport_site_audit(site_key),
            "states": sorted(LIFECYCLE_STATES),
            "modes": sorted(TRANSPORT_MODES),
            "format_bytes": format_bytes,
        },
    )


@router.get("/api/transport-sites")
def api_transport_sites(q: str = "", state: str = "", mode: str = ""):
    policies = list_transport_policies(q, state, mode)
    for policy in policies:
        policy["media_policy"] = get_media_policy(
            policy["site_key"]
        )
    return {
        "items": policies,
        "states": sorted(LIFECYCLE_STATES),
        "modes": sorted(TRANSPORT_MODES),
    }


@router.post("/api/transport-sites")
def api_create_transport_site(selection: TransportPolicyCreate):
    try:
        policy = create_transport_policy(selection.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return policy


@router.patch("/api/transport-sites/{site_key}")
def api_update_transport_site(
    request: Request,
    site_key: str,
    selection: TransportPolicyUpdate,
):
    try:
        policy = update_transport_policy(
            site_key,
            selection.model_dump(exclude_none=True),
            client_address=request.client.host if request.client else "",
            request_id=str(getattr(request.state, "request_id", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return policy


@router.post("/api/transport-sites/{site_key}/actions")
def api_transport_site_action(
    request: Request,
    site_key: str,
    selection: TransportPolicyAction,
):
    try:
        policy = perform_transport_action(
            site_key,
            selection.action,
            note=selection.note,
            client_address=request.client.host if request.client else "",
            request_id=str(getattr(request.state, "request_id", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return policy


@router.get("/api/transport-sites/{site_key}")
def api_transport_site(site_key: str):
    policy = get_transport_policy(site_key)
    if not policy:
        raise HTTPException(status_code=404, detail="站点策略不存在")
    policy["media_policy"] = get_media_policy(site_key)
    return policy


@router.get("/api/transport-sites/{site_key}/media-policy")
def api_transport_site_media_policy(site_key: str):
    if not get_transport_policy(site_key):
        raise HTTPException(status_code=404, detail="站点策略不存在")
    return {
        "policy": get_media_policy(site_key),
        "presets": media_preset_options(),
        "options": {
            "source_order": list(SOURCE_OPTIONS),
            "identity_modes": list(IDENTITY_MODES),
            "kind_order": list(KIND_OPTIONS),
            "quality_targets": list(QUALITY_TARGETS),
            "quality_fallbacks": list(QUALITY_FALLBACKS),
            "format_strategies": list(FORMAT_STRATEGIES),
            "merge_output_formats": list(MERGE_OUTPUT_FORMATS),
        },
    }


@router.patch("/api/transport-sites/{site_key}/media-policy")
def api_update_transport_site_media_policy(
    request: Request,
    site_key: str,
    selection: MediaPolicyUpdate,
):
    if not get_transport_policy(site_key):
        raise HTTPException(status_code=404, detail="站点策略不存在")
    try:
        return update_media_policy(
            site_key,
            selection.model_dump(exclude_none=True),
            client_address=request.client.host if request.client else "",
            request_id=str(getattr(request.state, "request_id", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/transport-sites/{site_key}/media-policy/probe")
def api_probe_transport_site_media_policy(
    site_key: str,
    selection: MediaPolicyProbe,
):
    if not get_transport_policy(site_key):
        raise HTTPException(status_code=404, detail="站点策略不存在")
    url = str(selection.url or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="URL 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="仅支持 HTTP/HTTPS URL")
    try:
        resolved_site_key = site_key_for_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resolved_site_key != site_key:
        raise HTTPException(
            status_code=422,
            detail="试运行 URL 不属于当前站点",
        )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        cdp_port = probe_socket.getsockname()[1]
    with tempfile.TemporaryDirectory(
        prefix="comic3-media-probe-"
    ) as temp_value:
        temp_dir = Path(temp_value)
        policy_path = temp_dir / "media-policy.json"
        policy_path.write_text(
            json.dumps(
                get_media_policy(site_key),
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        log_dir = temp_dir / "log"
        output_dir = temp_dir / "output"
        command = [
            sys.executable,
            "-m",
            "local_video_site.downloader.entrypoints.download_video",
            "--probe-media",
            "--media-policy-file",
            str(policy_path),
            "--quality",
            "site",
            "--output",
            str(output_dir),
            "--log-dir",
            str(log_dir),
            "--cdp-port",
            str(cdp_port),
            "--profile-dir",
            str(temp_dir / "profile"),
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(config.PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504,
                detail="媒体策略探测超时",
            ) from exc
        summary_path = log_dir / "summary.jsonl"
        records = []
        if summary_path.is_file():
            for line in summary_path.read_text(
                encoding="utf-8"
            ).splitlines():
                try:
                    records.append(json.loads(line))
                except (TypeError, ValueError):
                    continue
        if not records:
            detail = (
                (completed.stderr or completed.stdout or "").strip()
                or "未生成媒体探测结果"
            )
            raise HTTPException(
                status_code=502,
                detail=detail[-500:],
            )
        result = records[-1]
        if completed.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=str(
                    result.get("error")
                    or "媒体策略探测失败"
                )[:500],
            )
        return {
            "site_key": site_key,
            "policy": get_media_policy(site_key),
            "result": result,
        }


@router.post("/api/transport-sites/{site_key}/media-policy/reset")
def api_reset_transport_site_media_policy(
    request: Request,
    site_key: str,
):
    if not get_transport_policy(site_key):
        raise HTTPException(status_code=404, detail="站点策略不存在")
    return reset_media_policy(
        site_key,
        client_address=request.client.host if request.client else "",
        request_id=str(getattr(request.state, "request_id", "")),
    )


@router.get("/api/transport-sites/{site_key}/tasks")
def api_transport_site_tasks(site_key: str):
    return {"items": transport_site_tasks(site_key)}


@router.get("/api/transport-sites/{site_key}/events")
def api_transport_site_events(site_key: str):
    return {"items": transport_site_events(site_key)}


@router.get("/api/transport-sites/{site_key}/audit")
def api_transport_site_audit(site_key: str):
    return {"items": transport_site_audit(site_key)}
