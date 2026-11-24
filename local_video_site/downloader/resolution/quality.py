from __future__ import annotations

import re
from typing import List, Optional

from ..ytdlp import DEFAULT_FORMAT_SPEC
from ..sniffer import MediaCandidate

from .policy import MediaPolicy


def preferred_quality_height(value: str) -> Optional[int]:
    raw = str(value or "").strip().lower()
    if raw == "4k":
        return 2160
    match = re.search(r"([0-9]{3,4})", raw)
    if not match:
        return None
    height = int(match.group(1))
    return height if height >= 360 else None


def select_media_candidate(
    candidates: List[MediaCandidate],
    preferred_quality: str,
) -> Optional[MediaCandidate]:
    if not candidates:
        return None
    preferred_height = preferred_quality_height(preferred_quality)
    if not preferred_height:
        manifests = [
            candidate
            for candidate in candidates
            if candidate.kind in ("hls", "dash")
        ]
        if manifests:
            return manifests[0]
        matched_known = [
            candidate
            for candidate in candidates
            if candidate.kind == "file"
            and candidate.matches_page_video
            and 360 <= candidate.quality_height <= 1080
        ]
        if matched_known:
            return max(
                matched_known,
                key=lambda item: (
                    item.quality_height,
                    item.score,
                    item.order,
                ),
            )
        return candidates[0]

    manifests = [
        candidate
        for candidate in candidates
        if candidate.kind in ("hls", "dash")
    ]
    if manifests:
        return manifests[0]

    files = [
        candidate
        for candidate in candidates
        if candidate.kind == "file"
    ]
    matched = [candidate for candidate in files if candidate.matches_page_video]
    pool = matched or files or candidates
    known = [candidate for candidate in pool if candidate.quality_height > 0]
    if not known:
        return pool[0]
    exact = [
        candidate
        for candidate in known
        if candidate.quality_height == preferred_height
    ]
    if exact:
        return max(exact, key=lambda item: (item.score, item.order))
    lower = [
        candidate
        for candidate in known
        if candidate.quality_height < preferred_height
    ]
    if lower:
        highest_lower = max(
            candidate.quality_height for candidate in lower
        )
        return max(
            (
                candidate
                for candidate in lower
                if candidate.quality_height == highest_lower
            ),
            key=lambda item: (item.score, item.order),
        )
    lowest_higher = min(
        candidate.quality_height for candidate in known
    )
    return max(
        (
            candidate
            for candidate in known
            if candidate.quality_height == lowest_higher
        ),
        key=lambda item: (item.score, item.order),
    )


def format_selector_for_candidate(
    candidate: Optional[MediaCandidate],
    configured_format: str,
    preferred_height: Optional[int] = None,
) -> str:
    if (
        candidate
        and candidate.kind == "file"
        and configured_format == DEFAULT_FORMAT_SPEC
    ):
        return "best"
    if configured_format == DEFAULT_FORMAT_SPEC and preferred_height:
        return (
            "best[height<={0}][height>=360]/"
            "best[height<={0}]/best[height>=360]"
        ).format(int(preferred_height))
    return configured_format


def yt_dlp_format_selector(
    policy: MediaPolicy,
    preferred_height: Optional[int] = None,
) -> str:
    max_height = min(
        int(policy.max_height),
        int(preferred_height or policy.max_height),
    )
    min_height = int(policy.min_height)
    bounded = "[height<={}][height>={}]".format(
        max_height,
        min_height,
    )
    combined = "best{}".format(bounded)
    video = "bestvideo*{}".format(bounded)
    separated = "{}+bestaudio".format(video)
    # Keep the codec-specific selector explicit; the parsed format list uses
    # AV1 by default on Bilibili even when an H.264 stream is available.
    codec_separated = (
        "bestvideo*[vcodec^=avc1][height<={}]"
        "[height>={}]+bestaudio".format(max_height, min_height)
    )

    if policy.format_strategy == "combined":
        return "{}/best{}".format(combined, bounded)

    if policy.format_strategy == "separate_av":
        selectors = []
        if policy.prefer_h264:
            selectors.append(codec_separated)
        selectors.extend([separated, "best"])
        if policy.allow_below_minimum:
            lower_bound = "[height<={}]".format(max_height)
            if policy.prefer_h264:
                selectors.append(
                    "bestvideo*[vcodec^=avc1]{}"
                    "+bestaudio".format(lower_bound)
                )
            selectors.append(
                "bestvideo*{}+bestaudio".format(lower_bound)
            )
        return "/".join(dict.fromkeys(selectors))

    # Auto mode keeps the existing combined-stream behavior while also
    # supporting sites that expose only separate audio and video streams.
    return "/".join(
        dict.fromkeys(
            [
                combined,
                separated,
                "best",
            ]
        )
    )
