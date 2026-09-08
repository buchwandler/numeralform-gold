"""Deterministic bounded packets and atomic review artifact merges."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .io import write_jsonl
from .review import assert_blind_safe, validate_review_rows

REVIEW_PACKET_MAX_CASES = 50
REVIEW_PACKET_MAX_BYTES = 64 * 1024
ADJUDICATION_PACKET_MAX_CASES = 25
ADJUDICATION_PACKET_MAX_BYTES = 96 * 1024
DECISIONS = {"accept", "exclude", "unresolved"}


class PacketError(ValueError):
    """Raised when a packet or merge would violate its contract."""


def serialized_row_bytes(row: Mapping[str, Any]) -> int:
    return len(
        (json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    )


def select_packet_rows(
    rows: Iterable[Mapping[str, Any]],
    completed_ids: Iterable[str] = (),
    *,
    max_cases: int,
    max_bytes: int,
    identity_field: str = "case_id",
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Select the next stable rows within both packet limits."""
    if max_cases <= 0 or max_bytes <= 0:
        raise PacketError("max_cases and max_bytes must be positive")
    completed = set(completed_ids)
    selected: list[dict[str, Any]] = []
    total_bytes = 0
    ordered = sorted(
        (dict(row) for row in rows), key=lambda row: str(row.get(identity_field, ""))
    )
    selected_language: str | None = language
    if selected_language is None:
        selected_language = next(
            (
                row.get("language")
                for row in ordered
                if isinstance(row.get("language"), str)
            ),
            None,
        )
    for row in ordered:
        identity = row.get(identity_field)
        if not isinstance(identity, str) or not identity:
            raise PacketError(f"packet row is missing {identity_field}")
        if identity in completed:
            continue
        row_language = row.get("language")
        if language is not None and row_language != language:
            continue
        if selected_language is not None and row_language != selected_language:
            continue
        if len(selected) >= max_cases:
            break
        row_bytes = serialized_row_bytes(row)
        if row_bytes > max_bytes:
            raise PacketError(
                f"{identity_field} {identity} exceeds max byte budget ({row_bytes} > {max_bytes})"
            )
        if selected and total_bytes + row_bytes > max_bytes:
            break
        selected.append(row)
        total_bytes += row_bytes
    return selected


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _without_forbidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_forbidden(child)
            for key, child in value.items()
            if key
            not in {
                "source_observations",
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
                "adjudication",
                "final_oracle",
                "canonical_output",
                "canonical_answer",
                "canonical_record",
                "source_evidence",
                "observed_oracle",
                "candidate_oracle",
            }
        }
    if isinstance(value, list):
        return [_without_forbidden(child) for child in value]
    return value


def review_packet_rows(
    blind_rows: Iterable[Mapping[str, Any]],
    completed_rows: Iterable[Mapping[str, Any]] = (),
    *,
    max_cases: int = REVIEW_PACKET_MAX_CASES,
    max_bytes: int = REVIEW_PACKET_MAX_BYTES,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Project blind-review rows and select the next uncompleted packet."""
    projected = [_without_forbidden(_public(row)) for row in blind_rows]
    assert_blind_safe(projected)
    completed_ids = [
        row.get("case_id")
        for row in completed_rows
        if isinstance(row.get("case_id"), str)
    ]
    return select_packet_rows(
        projected,
        completed_ids,
        max_cases=max_cases,
        max_bytes=max_bytes,
        language=language,
    )


def _indexed_unique(
    rows: Iterable[Mapping[str, Any]], label: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = row.get("case_id")
        if not isinstance(identity, str) or not identity:
            raise PacketError(f"{label} row is missing case_id")
        if identity in indexed:
            raise PacketError(f"duplicate {label} case_id: {identity}")
        indexed[identity] = _public(row)
    return indexed


def _atomic_write(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        write_jsonl(temporary, rows)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def merge_review_rows(
    blind_rows: Iterable[Mapping[str, Any]],
    existing_rows: Iterable[Mapping[str, Any]],
    result_rows: Iterable[Mapping[str, Any]],
    *,
    slot: str,
    output: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Merge a result packet while preserving immutable blind fields."""
    slot = slot.upper()
    blind = _indexed_unique(blind_rows, "blind")
    existing = _indexed_unique(existing_rows, "existing review")
    results = _indexed_unique(result_rows, "packet result")
    for case_id, row in results.items():
        if case_id not in blind:
            raise PacketError(f"review result has unknown case_id: {case_id}")
        expected = blind[case_id]
        for field in (
            "review_schema_version",
            "reviewer_slot",
            "language",
            "locale",
            "input",
            "mode",
            "grammar",
            "family_id",
        ):
            if row.get(field) != expected.get(field):
                raise PacketError(
                    f"review result changes blind field {field} for {case_id}"
                )
        previous = existing.get(case_id)
        if previous is not None and previous != row:
            raise PacketError(f"conflicting duplicate review result: {case_id}")
    merged = dict(existing)
    merged.update(results)
    report = validate_review_rows(merged.values(), slot=slot)
    if report["issues"]:
        raise PacketError("invalid merged review: " + report["issues"][0]["message"])
    output_rows = [merged[key] for key in sorted(merged)]
    if output is not None:
        _atomic_write(output, output_rows)
    return output_rows


def _source_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if isinstance(case_id, str):
            observation = row.get("observation", row)
            if isinstance(observation, Mapping):
                result.setdefault(case_id, []).append(_public(observation))
    return result



def _evidence_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if isinstance(case_id, str):
            result.setdefault(case_id, []).append(_public(row))
    return result

def adjudication_packet_rows(
    cases: Iterable[Mapping[str, Any]],
    review_a: Iterable[Mapping[str, Any]],
    review_b: Iterable[Mapping[str, Any]],
    source_observations: Iterable[Mapping[str, Any]] = (),
    reference_observations: Iterable[Mapping[str, Any]] = (),
    completed_decisions: Iterable[Mapping[str, Any]] = (),
    *,
    max_cases: int = ADJUDICATION_PACKET_MAX_CASES,
    max_bytes: int = ADJUDICATION_PACKET_MAX_BYTES,
    source_evidence: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Project aligned reviews and selected evidence for adjudication."""
    case_map = _indexed_unique(cases, "case")
    a_map = _indexed_unique(review_a, "review A")
    b_map = _indexed_unique(review_b, "review B")
    if set(case_map) != set(a_map) or set(case_map) != set(b_map):
        raise PacketError("cases, review A, and review B case-ID sets must match")
    source_map = _source_map(source_observations)
    reference_map = _source_map(reference_observations)
    evidence_map = _evidence_map(source_evidence)
    projected: list[dict[str, Any]] = []
    for case_id in sorted(case_map):
        case = case_map[case_id]
        sources = source_map.get(case_id, case.get("source_observations", []))
        references = reference_map.get(case_id, case.get("reference_observations", []))
        row = {
            "case_id": case_id,
            "case": _without_forbidden(case),
            "review_a": a_map[case_id],
            "review_b": b_map[case_id],
            "source_observations": _public_list(sources),
            "reference_observations": _public_list(references),
            "source_evidence": _public_list(evidence_map.get(case_id, [])),
        }
        projected.append(row)
    completed_ids = [
        row.get("case_id")
        for row in completed_decisions
        if isinstance(row.get("case_id"), str)
    ]
    return select_packet_rows(
        projected, completed_ids, max_cases=max_cases, max_bytes=max_bytes
    )


def _public_list(rows: Any) -> list[Any]:
    if not isinstance(rows, list):
        return []
    return [_public(row) if isinstance(row, Mapping) else row for row in rows]


def _validate_oracle(oracle: Any, case_id: str) -> None:
    if not isinstance(oracle, Mapping):
        raise PacketError(f"{case_id}: accept decision requires final_oracle")
    canonical = oracle.get("canonical")
    accepted = oracle.get("accepted")
    rejected = oracle.get("rejected")
    if not isinstance(canonical, str) or not canonical:
        raise PacketError(f"{case_id}: final_oracle.canonical is required")
    if (
        not isinstance(accepted, list)
        or not accepted
        or canonical not in accepted
        or any(not isinstance(v, str) or not v for v in accepted)
    ):
        raise PacketError(f"{case_id}: final_oracle.accepted must contain canonical")
    if not isinstance(rejected, list) or any(
        not isinstance(v, str) or not v for v in rejected
    ):
        raise PacketError(f"{case_id}: final_oracle.rejected must be a list of strings")
    if set(accepted) & set(rejected):
        raise PacketError(f"{case_id}: final_oracle accepted/rejected overlap")


def _validate_blocker(blocker: Any, case_id: str) -> None:
    if not isinstance(blocker, Mapping):
        raise PacketError(f"{case_id}: unresolved decision requires structured blocker")
    for key in ("code", "class", "reason", "attempted_resolution"):
        if not isinstance(blocker.get(key), str) or not blocker[key].strip():
            raise PacketError(f"{case_id}: unresolved blocker.{key} is required")
    if blocker.get("retryable") is not True:
        raise PacketError(f"{case_id}: unresolved blocker must be retryable")


def _validate_decision(row: Mapping[str, Any]) -> None:
    case_id = row.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise PacketError("adjudication row is missing case_id")
    adjudicator = row.get("adjudicator")
    if (
        not isinstance(adjudicator, Mapping)
        or not isinstance(adjudicator.get("adjudicator_id"), str)
        or not adjudicator["adjudicator_id"].strip()
        or not isinstance(adjudicator.get("model_family"), str)
        or not adjudicator["model_family"].strip()
    ):
        raise PacketError(
            f"{case_id}: adjudicator identity and model_family are required"
        )
    decision = row.get("decision")
    if decision not in DECISIONS:
        raise PacketError(f"{case_id}: invalid adjudication decision")
    if not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
        raise PacketError(f"{case_id}: rationale is required")
    if not isinstance(row.get("evidence_used"), list) or any(
        not isinstance(v, str) or not v for v in row["evidence_used"]
    ):
        raise PacketError(f"{case_id}: evidence_used must be a list of strings")
    if decision == "accept":
        _validate_oracle(row.get("final_oracle"), case_id)
        if "blocker" in row:
            raise PacketError(f"{case_id}: accept decision cannot contain blocker")
    elif decision == "unresolved":
        _validate_blocker(row.get("blocker"), case_id)
    elif "blocker" in row and not isinstance(row["blocker"], Mapping):
        raise PacketError(f"{case_id}: blocker must be an object")


def merge_adjudication_rows(
    existing_rows: Iterable[Mapping[str, Any]],
    result_rows: Iterable[Mapping[str, Any]],
    *,
    output: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Merge adjudication results atomically with one stable identity."""
    existing = _indexed_unique(existing_rows, "existing adjudication")
    results = _indexed_unique(result_rows, "packet result")
    merged = dict(existing)
    identities = {
        (row.get("adjudicator") or {}).get("adjudicator_id")
        for row in existing.values()
        if isinstance(row.get("adjudicator"), Mapping)
    }
    for case_id, row in results.items():
        _validate_decision(row)
        identities.add(row["adjudicator"]["adjudicator_id"])
        if case_id in merged and merged[case_id] != row:
            raise PacketError(f"conflicting duplicate adjudication result: {case_id}")
        merged[case_id] = row
    if len(identities) > 1:
        raise PacketError("adjudication must use one stable adjudicator identity")
    output_rows = [merged[key] for key in sorted(merged)]
    for row in output_rows:
        _validate_decision(row)
    if output is not None:
        _atomic_write(output, output_rows)
    return output_rows


def finalize_adjudication(
    cases: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    *,
    output: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Require exact case-ID decision coverage and valid decision shapes."""
    case_ids = {row.get("case_id") for row in cases}
    indexed = _indexed_unique(decisions, "adjudication")
    if set(indexed) != case_ids:
        raise PacketError(
            f"adjudication case-ID set mismatch: missing={sorted(case_ids - set(indexed))} extra={sorted(set(indexed) - case_ids)}"
        )
    for row in indexed.values():
        _validate_decision(row)
    output_rows = [indexed[key] for key in sorted(indexed)]
    if output is not None:
        _atomic_write(output, output_rows)
    return output_rows


__all__ = [
    "ADJUDICATION_PACKET_MAX_BYTES",
    "ADJUDICATION_PACKET_MAX_CASES",
    "DECISIONS",
    "REVIEW_PACKET_MAX_BYTES",
    "REVIEW_PACKET_MAX_CASES",
    "PacketError",
    "adjudication_packet_rows",
    "finalize_adjudication",
    "merge_adjudication_rows",
    "merge_review_rows",
    "review_packet_rows",
    "select_packet_rows",
    "serialized_row_bytes",
]
