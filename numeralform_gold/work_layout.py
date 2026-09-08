"""Minimal disposable work-root layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

WORK_SUBDIRS = (
    "candidates",
    "reference",
    "reports",
    "comparisons",
    "reviews",
    "releases",
    "tmp",
)


@dataclass(frozen=True)
class WorkLayout:
    root: Path

    @property
    def candidates(self) -> Path:
        return self.root / "candidates"

    @property
    def reference(self) -> Path:
        return self.root / "reference"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def comparisons(self) -> Path:
        return self.root / "comparisons"

    def init(self) -> list[Path]:
        created: list[Path] = []
        self.root.mkdir(parents=True, exist_ok=True)
        for name in WORK_SUBDIRS:
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
        return created
