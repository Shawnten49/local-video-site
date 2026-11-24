from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..sniffer import MediaCandidate

from .quality import select_media_candidate
from .policy import MediaPolicy, MediaSelectionPlan, build_selection_plan


@dataclass
class MediaCatalog:
    candidates: List[MediaCandidate] = field(default_factory=list)
    policy: MediaPolicy = field(default_factory=MediaPolicy)

    @property
    def manifests(self) -> List[MediaCandidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.kind in ("hls", "dash")
        ]

    @property
    def direct_files(self) -> List[MediaCandidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.kind == "file"
        ]

    @property
    def available_qualities(self) -> List[str]:
        values = {
            candidate.quality_label
            for candidate in self.candidates
            if candidate.quality_height > 0
        }
        return sorted(
            values,
            key=lambda value: int(value.rstrip("p") or 0),
            reverse=True,
        )

    def select(self, preferred_quality: str) -> Optional[MediaCandidate]:
        if isinstance(preferred_quality, str):
            return self.plan(preferred_quality).primary
        return select_media_candidate(self.candidates, preferred_quality)

    def plan(self, preferred_quality: str = "site") -> MediaSelectionPlan:
        return build_selection_plan(
            self.candidates,
            preferred_quality,
            self.policy,
        )
