"""Durable, sanitized lineage for reviewed Numeralform Gold records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .io import read_jsonl, write_jsonl
from .review import case_id_for_record

FORBIDDEN_KEYS = {
    "source_form",
    "source_expected",
    "upstream_expected",
    "reference_output",
    "generator_output",
    "prediction",
    "current_output",
    "numeralform_output",
    "num2words_output",
    "other_review",
    "review_a",
    "review_b",
    "adjudication",
    "final_oracle",
    "canonical_output",
    "canonical_answer",
    "canonical_record",
}
LINEAGE_FORBIDDEN_KEYS = FORBIDDEN_KEYS - {"review_a", "review_b", "adjudication"}
TRANSIENT_KEYS = {"_source_file", "_source_line"}


def sanitize_review_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_review_artifact(child)
            for key, child in value.items()
            if key not in FORBIDDEN_KEYS and key not in TRANSIENT_KEYS
        }
    if isinstance(value, list):
        return [sanitize_review_artifact(child) for child in value]
    return deepcopy(value)


def artifact_sha256(value: Any) -> str:
    payload = json.dumps(
        sanitize_review_artifact(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _source_ref(source: Mapping[str, Any]) -> dict[str, Any] | None:
    benchmark = source.get("benchmark")
    source_id = source.get("source_id")
    if not isinstance(benchmark, str) or not isinstance(source_id, str):
        return None
    result: dict[str, Any] = {"benchmark": benchmark, "source_id": source_id}
    if isinstance(source.get("source_version"), str):
        result["source_version"] = source["source_version"]
    return result


def _source_refs(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = [
        _source_ref(source)
        for source in record.get("source_observations", [])
        if isinstance(source, Mapping)
    ]
    return sorted(
        (ref for ref in refs if ref is not None),
        key=lambda row: (row["benchmark"], row["source_id"]),
    )


def _row_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: dict(row)
        for row in rows
        if isinstance(row.get("case_id"), str) and row["case_id"]
    }


def build_review_evidence(
    candidates: Iterable[Mapping[str, Any]],
    review_a: Iterable[Mapping[str, Any]],
    review_b: Iterable[Mapping[str, Any]],
    comparisons: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    *,
    records: Iterable[Mapping[str, Any]] = (),
    campaign_id: str | None = None,
    batch_id: str = "",
    integration_revision: str | None = None,
    previous: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build one evidence row per accepted canonical record."""
    candidates_map = _row_map(candidates)
    a_map, b_map, comparison_map = (
        _row_map(review_a),
        _row_map(review_b),
        _row_map(comparisons),
    )
    records_map = {
        row.get("id"): dict(row) for row in records if isinstance(row.get("id"), str)
    }
    previous_rows = list(previous)
    revisions = {
        row.get("record_id"): max(
            [
                prior.get("review_revision", 0)
                for prior in previous_rows
                if prior.get("record_id") == row.get("record_id")
                and isinstance(prior.get("review_revision"), int)
            ],
            default=0,
        )
        for row in previous_rows
    }
    entries: list[dict[str, Any]] = []
    for decision in sorted(
        (dict(row) for row in decisions), key=lambda row: str(row.get("case_id", ""))
    ):
        if decision.get("decision") != "accept":
            continue
        case_id = decision.get("case_id")
        if not isinstance(case_id, str):
            raise TypeError("accepted decision requires case_id")
        candidate = candidates_map.get(case_id, {})
        record = records_map.get(decision.get("record_id"))
        if record is None:
            record = next(
                (
                    value
                    for value in records_map.values()
                    if case_id_for_record(value) == case_id
                ),
                None,
            )
        if record is None:
            raise ValueError(f"accepted decision has no integrated record: {case_id}")
        reviewer_a = a_map.get(case_id)
        reviewer_b = b_map.get(case_id)
        adjudicator = decision.get("adjudicator")
        revision = revisions.get(record["id"], 0) + 1
        entry = {
            "schema_version": "1.0.0",
            "record_id": record["id"],
            "campaign_id": campaign_id,
            "batch_id": batch_id,
            "case_id": case_id,
            "source_refs": _source_refs({**candidate, **record}),
            "candidate_snapshot_sha256": artifact_sha256(candidate),
            "comparison_artifact_sha256": artifact_sha256(comparison_map[case_id])
            if case_id in comparison_map
            else None,
            "review_a": {
                "reviewer_id": reviewer_a.get("reviewer", {}).get("reviewer_id")
                if isinstance(reviewer_a, Mapping)
                and isinstance(reviewer_a.get("reviewer"), Mapping)
                else None,
                "model_family": reviewer_a.get("reviewer", {}).get("model_family")
                if isinstance(reviewer_a, Mapping)
                and isinstance(reviewer_a.get("reviewer"), Mapping)
                else None,
                "artifact_sha256": artifact_sha256(reviewer_a) if reviewer_a else None,
            },
            "review_b": {
                "reviewer_id": reviewer_b.get("reviewer", {}).get("reviewer_id")
                if isinstance(reviewer_b, Mapping)
                and isinstance(reviewer_b.get("reviewer"), Mapping)
                else None,
                "model_family": reviewer_b.get("reviewer", {}).get("model_family")
                if isinstance(reviewer_b, Mapping)
                and isinstance(reviewer_b.get("reviewer"), Mapping)
                else None,
                "artifact_sha256": artifact_sha256(reviewer_b) if reviewer_b else None,
            },
            "adjudication": {
                "adjudicator_id": adjudicator.get("adjudicator_id")
                if isinstance(adjudicator, Mapping)
                else None,
                "model_family": adjudicator.get("model_family")
                if isinstance(adjudicator, Mapping)
                else None,
                "artifact_sha256": artifact_sha256(decision),
            },
            "decision": "accept",
            "integration_revision": integration_revision,
            "protocol_version": "numeral-review-v1",
            "review_revision": revision,
        }
        entries.append(entry)
    return entries


def validate_review_evidence(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, int]] = set()
    for index, entry in enumerate(entries):
        label = f"evidence[{index}]"
        record_id = entry.get("record_id")
        revision = entry.get("review_revision")
        if not isinstance(record_id, str) or not record_id:
            errors.append(f"{label}: record_id is required")
        if not isinstance(revision, int) or revision < 1:
            errors.append(f"{label}: review_revision must be a positive integer")
        if isinstance(record_id, str) and isinstance(revision, int):
            key = (record_id, revision)
            if key in seen:
                errors.append(
                    f"{label}: duplicate record/revision {record_id}/{revision}"
                )
            seen.add(key)
        if entry.get("schema_version") != "1.0.0":
            errors.append(f"{label}: schema_version must be 1.0.0")
        if entry.get("decision") != "accept":
            errors.append(f"{label}: lineage decisions must be accept")
        if entry.get("protocol_version") != "numeral-review-v1":
            errors.append(f"{label}: invalid protocol_version")
        if any(_contains_key(entry, key) for key in LINEAGE_FORBIDDEN_KEYS):
            errors.append(f"{label}: forbidden review field leaked into lineage")
        for section in ("review_a", "review_b", "adjudication"):
            value = entry.get(section)
            if not isinstance(value, Mapping) or not isinstance(
                value.get("artifact_sha256"), str
            ):
                errors.append(f"{label}: {section}.artifact_sha256 is required")
    return errors


def _contains_key(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return needle in value or any(
            _contains_key(child, needle) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, needle) for child in value)
    return False


def write_review_evidence(
    path: str | Path, entries: Iterable[Mapping[str, Any]]
) -> None:
    """Atomically write evidence while rejecting conflicting revisions."""
    target = Path(path)
    existing = read_jsonl(target) if target.is_file() else []
    combined = {
        (row.get("record_id"), row.get("review_revision")): dict(row)
        for row in existing
    }
    for row in entries:
        key = (row.get("record_id"), row.get("review_revision"))
        prior = combined.get(key)
        if prior is not None and artifact_sha256(prior) != artifact_sha256(row):
            raise ValueError(
                f"conflicting review evidence for {key[0]} revision {key[1]}"
            )
        combined[key] = dict(row)
    output = sorted(
        combined.values(),
        key=lambda row: (row.get("record_id", ""), row.get("review_revision", 0)),
    )
    errors = validate_review_evidence(output)
    if errors:
        raise ValueError("invalid review evidence: " + "; ".join(errors))
    write_jsonl(target, output)


__all__ = [
    "FORBIDDEN_KEYS",
    "artifact_sha256",
    "build_review_evidence",
    "sanitize_review_artifact",
    "validate_review_evidence",
    "write_review_evidence",
]
