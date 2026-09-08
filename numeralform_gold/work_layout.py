"""Canonical paths for workflow-owned Numeralform Gold artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

WORK_SUBDIRS = (
    "candidates",
    "reference",
    "campaigns",
    "batches",
    "state",
    "reports",
    "comparisons",
    "reviews",
    "releases",
    "tmp",
)


@dataclass(frozen=True)
class WorkLayout:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def candidates(self) -> Path:
        return self.root / "candidates"

    @property
    def reference(self) -> Path:
        return self.root / "reference"

    @property
    def campaigns(self) -> Path:
        return self.root / "campaigns"

    @property
    def batches_root(self) -> Path:
        return self.root / "batches"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def review_exclusions(self) -> Path:
        return self.state / "review-exclusions.jsonl"

    @property
    def review_exclusions_summary(self) -> Path:
        return self.state / "review-exclusions.summary.json"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def comparisons(self) -> Path:
        return self.root / "comparisons"

    @property
    def lineage(self) -> Path:
        return self.root.parent.parent / "data" / "lineage" / "review-evidence.jsonl"

    def batch(self, batch_id: str) -> BatchLayout:
        return BatchLayout(self.batches_root / batch_id)

    def init(self) -> list[Path]:
        created: list[Path] = []
        self.root.mkdir(parents=True, exist_ok=True)
        for name in WORK_SUBDIRS:
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
        return created


@dataclass(frozen=True)
class BatchLayout:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def metadata(self) -> Path:
        return self.root / "batch.json"

    @property
    def batch_json(self) -> Path:
        return self.metadata

    @property
    def source_dir(self) -> Path:
        return self.root / "source"

    @property
    def source_observations(self) -> Path:
        return self.source_dir / "observations.jsonl"

    @property
    def source_references(self) -> Path:
        return self.source_dir / "references.jsonl"

    @property
    def cases_dir(self) -> Path:
        return self.root / "cases"

    @property
    def cases(self) -> Path:
        return self.cases_dir / "cases.jsonl"

    @property
    def context(self) -> Path:
        return self.cases_dir / "context.jsonl"

    def review_dir(self, slot: str) -> Path:
        if slot.upper() not in {"A", "B"}:
            raise ValueError("review slot must be A or B")
        return self.root / "reviews" / slot.lower()

    def review_blind(self, slot: str) -> Path:
        return self.review_dir(slot) / "blind.jsonl"

    def review_complete(self, slot: str) -> Path:
        return self.review_dir(slot) / "complete.jsonl"

    def review_validation(self, slot: str) -> Path:
        return self.review_dir(slot) / "validation.json"

    def review_packet_dir(self, slot: str) -> Path:
        return self.review_dir(slot) / "packets"

    def review_packet(
        self, slot: str, packet_number: int, result: bool = False
    ) -> Path:
        suffix = "result" if result else "input"
        return self.review_packet_dir(slot) / f"{packet_number:04d}.{suffix}.jsonl"

    @property
    def review_check(self) -> Path:
        return self.root / "reviews" / "check.json"

    @property
    def adjudication_dir(self) -> Path:
        return self.root / "adjudication"

    @property
    def adjudication_decisions(self) -> Path:
        return self.adjudication_dir / "decisions.jsonl"

    @property
    def adjudication_partial(self) -> Path:
        return self.adjudication_dir / "decisions.partial.jsonl"

    def adjudication_packet(self, packet_number: int, result: bool = False) -> Path:
        suffix = "result" if result else "input"
        return self.adjudication_dir / "packets" / f"{packet_number:04d}.{suffix}.jsonl"

    @property
    def integration_dir(self) -> Path:
        return self.root / "integration"

    @property
    def integration_summary(self) -> Path:
        return self.integration_dir / "summary.json"

    @property
    def integration_exclusions(self) -> Path:
        return self.integration_dir / "exclusions.jsonl"

    @property
    def integration_retry(self) -> Path:
        return self.integration_dir / "retry.jsonl"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def handoff(self) -> Path:
        return self.root / "handoff.md"

    def init(self) -> list[Path]:
        paths = [
            self.root,
            self.source_dir,
            self.cases_dir,
            self.review_dir("A"),
            self.review_dir("B"),
            self.adjudication_dir / "packets",
            self.integration_dir,
            self.reports_dir,
        ]
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        return paths


__all__ = ["BatchLayout", "WorkLayout"]
