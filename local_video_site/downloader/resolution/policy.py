from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from ..sniffer import MediaCandidate


DEFAULT_SOURCE_ORDER = (
    "player-config",
    "network",
    "video-element",
    "performance",
)
DEFAULT_KIND_ORDER = ("hls", "dash", "file")


def _quality_height(value: str) -> int:
    match = re.search(r"([0-9]{3,4})", str(value or ""))
    return int(match.group(1)) if match else 0


@dataclass(frozen=True)
class MediaPolicy:
    strategy_key: str = "generic"
    source_order: Sequence[str] = DEFAULT_SOURCE_ORDER
    identity_mode: str = "site_default"
    kind_order: Sequence[str] = DEFAULT_KIND_ORDER
    quality_target: str = "auto"
    quality_fallback: str = "nearest_below"
    min_height: int = 360
    max_height: int = 1080
    allow_below_minimum: bool = True
    allow_page_fallback: bool = True
    play_selector: str = ""
    format_strategy: str = "auto"
    prefer_h264: bool = False
    merge_output_format: str = "mp4"

    @classmethod
    def from_dict(cls, value: Optional[Dict]) -> "MediaPolicy":
        data = dict(value or {})
        source_order = tuple(
            str(item)
            for item in data.get("source_order", DEFAULT_SOURCE_ORDER)
            if str(item or "").strip()
        ) or DEFAULT_SOURCE_ORDER
        kind_order = tuple(
            str(item)
            for item in data.get("kind_order", DEFAULT_KIND_ORDER)
            if str(item or "").strip()
        ) or DEFAULT_KIND_ORDER
        return cls(
            strategy_key=str(
                data.get("strategy_key")
                or data.get("preset_key")
                or "generic"
            ),
            source_order=source_order,
            identity_mode=str(
                data.get("identity_mode") or "site_default"
            ),
            kind_order=kind_order,
            quality_target=str(
                data.get("quality_target") or "auto"
            ).lower(),
            quality_fallback=str(
                data.get("quality_fallback") or "nearest_below"
            ).lower(),
            min_height=int(data.get("min_height") or 360),
            max_height=int(data.get("max_height") or 1080),
            allow_below_minimum=bool(
                data.get("allow_below_minimum", True)
            ),
            allow_page_fallback=bool(
                data.get("allow_page_fallback", True)
            ),
            play_selector=str(data.get("play_selector") or ""),
            format_strategy=str(
                data.get("format_strategy") or "auto"
            ).lower(),
            prefer_h264=bool(data.get("prefer_h264", False)),
            merge_output_format=str(
                data.get("merge_output_format") or "mp4"
            ).lower(),
        )

    def as_dict(self) -> Dict:
        return {
            "strategy_key": self.strategy_key,
            "source_order": list(self.source_order),
            "identity_mode": self.identity_mode,
            "kind_order": list(self.kind_order),
            "quality_target": self.quality_target,
            "quality_fallback": self.quality_fallback,
            "min_height": self.min_height,
            "max_height": self.max_height,
            "allow_below_minimum": self.allow_below_minimum,
            "allow_page_fallback": self.allow_page_fallback,
            "play_selector": self.play_selector,
            "format_strategy": self.format_strategy,
            "prefer_h264": self.prefer_h264,
            "merge_output_format": self.merge_output_format,
        }


@dataclass
class MediaSelectionPlan:
    ordered: List[MediaCandidate] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    quality_target: str = "auto"
    strategy_key: str = "generic"

    @property
    def primary(self) -> Optional[MediaCandidate]:
        return self.ordered[0] if self.ordered else None

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def resolve_quality_target(policy: MediaPolicy, requested: str = "site") -> str:
    value = str(requested or "site").strip().lower()
    if value == "4k":
        value = "2160p"
    if value in {"", "site", "auto-default"}:
        return policy.quality_target
    if value in {"auto", "2160p", "1080p", "720p", "480p", "360p"}:
        return value
    return policy.quality_target


def _source_rank(candidate: MediaCandidate, order: Sequence[str]) -> int:
    try:
        return list(order).index(candidate.source)
    except ValueError:
        return len(order)


def _kind_rank(kind: str, order: Sequence[str]) -> int:
    try:
        return list(order).index(kind)
    except ValueError:
        return len(order)


def _score_candidate(
    candidate: MediaCandidate,
    policy: MediaPolicy,
) -> tuple:
    return (
        1 if candidate.matches_page_video else 0,
        -_source_rank(candidate, policy.source_order),
        -_kind_rank(candidate.kind, policy.kind_order),
        candidate.quality_height,
        candidate.score,
        -candidate.order,
    )


def _scope_candidates(
    candidates: Iterable[MediaCandidate],
    policy: MediaPolicy,
) -> List[MediaCandidate]:
    pool = list(candidates)
    if policy.identity_mode == "loose":
        return pool
    matching = [
        candidate for candidate in pool if candidate.matches_page_video
    ]
    if policy.identity_mode == "page_id":
        return matching
    if matching:
        return matching
    manifests = [
        candidate
        for candidate in pool
        if candidate.kind in ("hls", "dash")
    ]
    return manifests or pool


def _select_for_quality(
    candidates: List[MediaCandidate],
    target: str,
    policy: MediaPolicy,
) -> List[MediaCandidate]:
    if not candidates:
        return []
    target_height = _quality_height(target)
    if target == "source_first":
        return sorted(
            candidates,
            key=lambda item: _score_candidate(item, policy),
            reverse=True,
        )
    known = [
        candidate
        for candidate in candidates
        if candidate.quality_height > 0
    ]
    unknown = [
        candidate
        for candidate in candidates
        if candidate.quality_height <= 0
    ]
    ordered_unknown = sorted(
        unknown,
        key=lambda item: _score_candidate(item, policy),
        reverse=True,
    )
    if not known:
        return ordered_unknown

    preferred = [
        candidate
        for candidate in known
        if policy.min_height <= candidate.quality_height <= policy.max_height
    ]
    manifests_above_max = [
        candidate
        for candidate in known
        if (
            candidate.kind in {"hls", "dash"}
            and candidate.quality_height > policy.max_height
        )
    ]
    if target == "auto":
        eligible = preferred + manifests_above_max or (
            known if policy.allow_below_minimum else []
        )
        if not eligible:
            return ordered_unknown
        highest = max(
            (candidate.quality_height for candidate in eligible),
            default=0,
        )
        selected = sorted(
            [
                candidate
                for candidate in candidates
                if candidate.quality_height == highest
            ],
            key=lambda item: _score_candidate(item, policy),
            reverse=True,
        )
        return selected + sorted(
            [
                candidate
                for candidate in candidates
                if candidate not in selected
                and (
                    policy.allow_below_minimum
                    or candidate.quality_height <= 0
                    or policy.min_height
                    <= candidate.quality_height
                    <= policy.max_height
                )
            ],
            key=lambda item: _score_candidate(item, policy),
            reverse=True,
        )

    exact = [
        candidate
        for candidate in known
        if candidate.quality_height == target_height
    ]
    lower = [
        candidate
        for candidate in known
        if candidate.quality_height < target_height
        and candidate.quality_height >= policy.min_height
    ]
    higher = [
        candidate
        for candidate in known
        if candidate.quality_height > target_height
        and candidate.quality_height <= policy.max_height
    ]
    if policy.quality_fallback == "strict":
        return sorted(
            exact,
            key=lambda item: _score_candidate(item, policy),
            reverse=True,
        )
    selected: List[MediaCandidate] = []
    pools = [exact]
    if policy.quality_fallback == "best_effort" and (lower or higher):
        nearest_distance = min(
            abs(candidate.quality_height - target_height)
            for candidate in lower + higher
        )
        nearest = [
            candidate
            for candidate in lower + higher
            if abs(candidate.quality_height - target_height)
            == nearest_distance
        ]
        pools.append(nearest)
    elif lower:
        nearest_lower = max(
            candidate.quality_height for candidate in lower
        )
        pools.append(
            [
                candidate
                for candidate in lower
                if candidate.quality_height == nearest_lower
            ]
        )
    for pool in pools:
        for candidate in sorted(
            pool,
            key=lambda item: _score_candidate(item, policy),
            reverse=True,
        ):
            if not any(item is candidate for item in selected):
                selected.append(candidate)
    remainder = sorted(
        [
            candidate
            for candidate in candidates
            if candidate not in selected
            and (
                candidate.quality_height <= 0
                or policy.min_height
                <= candidate.quality_height
                <= policy.max_height
            )
        ],
        key=lambda item: _score_candidate(item, policy),
        reverse=True,
    )
    return selected + remainder


def build_selection_plan(
    candidates: Iterable[MediaCandidate],
    requested_quality: str = "site",
    policy: Optional[MediaPolicy] = None,
) -> MediaSelectionPlan:
    active = policy or MediaPolicy()
    scoped = _scope_candidates(candidates, active)
    target = resolve_quality_target(active, requested_quality)
    reasons = [
        "strategy={}".format(active.strategy_key),
        "identity={} candidates={}".format(
            active.identity_mode,
            len(scoped),
        ),
        "quality_target={}".format(target),
    ]
    if not scoped:
        return MediaSelectionPlan(
            reasons=reasons + ["no identity-compatible candidate"],
            quality_target=target,
            strategy_key=active.strategy_key,
        )

    result: List[MediaCandidate] = []
    seen = set()
    actual_kinds = {candidate.kind for candidate in scoped}
    kind_order = list(active.kind_order) + sorted(
        actual_kinds.difference(active.kind_order)
    )
    for kind in kind_order:
        bucket = [
            candidate for candidate in scoped if candidate.kind == kind
        ]
        if not bucket:
            continue
        ordered_bucket = _select_for_quality(bucket, target, active)
        for candidate in ordered_bucket:
            key = id(candidate)
            if key not in seen:
                seen.add(key)
                result.append(candidate)
        if ordered_bucket:
            reasons.append(
                "kind={} primary_quality={}".format(
                    kind,
                    ordered_bucket[0].quality_label,
                )
            )
    return MediaSelectionPlan(
        ordered=result,
        reasons=reasons,
        quality_target=target,
        strategy_key=active.strategy_key,
    )
