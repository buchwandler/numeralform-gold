"""Deterministic bounded packets and atomic review artifact merges."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .io import write_jsonl
from .review import assert_blind_safe, validate_review_rows
from .review_anomaly import build_review_anomaly_report

REVIEW_PACKET_MAX_CASES = 50
REVIEW_PACKET_MAX_BYTES = 64 * 1024
ADJUDICATION_PACKET_MAX_CASES = 25
ADJUDICATION_PACKET_MAX_BYTES = 96 * 1024
DECISIONS = {"accept", "exclude", "unresolved"}

REVIEW_PACKET_KEYS = {
    "review_schema_version",
    "case_id",
    "reviewer_slot",
    "language",
    "locale",
    "input",
    "mode",
    "grammar",
    "family_id",
    "annotation",
    "review",
}
ADJUDICATION_ALLOWED_KEYS = {
    "case_id",
    "adjudicator",
    "decision",
    "final_oracle",
    "rationale",
    "evidence_used",
    "blocker",
}
ADJUDICATOR_ALLOWED_KEYS = {
    "adjudicator_id",
    "kind",
    "provider",
    "model",
    "model_family",
    "protocol_version",
    "independence_group",
}

class PacketError(ValueError):
    """Raised when a packet or merge would violate its contract."""


def serialized_row_bytes(row: Mapping[str, Any]) -> int:
    return len(
        (json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    )


def sha256_file(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def case_ids_sha256(case_ids: Iterable[str]) -> str:
    payload = json.dumps(sorted(case_ids), separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

def packet_group_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the semantic group key used to keep packets homogeneous."""
    source = row
    if isinstance(row.get("case"), Mapping):
        source = row["case"]
    return (
        source.get("language"),
        source.get("locale"),
        source.get("family_id"),
    )


def next_packet_number(packet_dir: str | Path) -> int:
    """Allocate the next numeric packet number without reusing a gap."""
    directory = Path(packet_dir)
    numbers: list[int] = []
    for path in directory.glob("*.input.jsonl"):
        try:
            numbers.append(int(path.name.split(".", 1)[0]))
        except ValueError:
            continue
    return max(numbers, default=0) + 1


def _validate_packet_projection(rows: Mapping[str, Mapping[str, Any]]) -> None:
    for case_id, row in rows.items():
        unknown = set(row) - REVIEW_PACKET_KEYS
        if unknown:
            raise PacketError(
                f"assigned packet contains unknown fields for {case_id}: "
                + ", ".join(sorted(unknown))
            )
        try:
            assert_blind_safe(row)
        except Exception as exc:
            raise PacketError(f"assigned packet is not blind-safe for {case_id}") from exc


def validate_review_packet(
    packet_rows: Iterable[Mapping[str, Any]],
    result_rows: Iterable[Mapping[str, Any]],
    *,
    slot: str,
    authoritative_rows: Iterable[Mapping[str, Any]] | None = None,
    max_cases: int = REVIEW_PACKET_MAX_CASES,
    max_bytes: int = REVIEW_PACKET_MAX_BYTES,
) -> dict[str, Any]:
    packet = _indexed_unique(packet_rows, "assigned packet")
    results = _indexed_unique(result_rows, "packet result")
    if not packet:
        raise PacketError("assigned packet must not be empty")
    _validate_packet_projection(packet)
    if authoritative_rows is not None:
        authoritative = _indexed_unique(authoritative_rows, "authoritative blind")
        if set(packet) - set(authoritative):
            raise PacketError("assigned packet contains unknown case IDs")
        for case_id, row in packet.items():
            expected = {
                key: value
                for key, value in _without_forbidden(authoritative[case_id]).items()
                if key in REVIEW_PACKET_KEYS
            }
            if row != expected:
                raise PacketError(
                    f"assigned packet does not match authoritative blind projection: {case_id}"
                )
    if set(packet) != set(results):
        raise PacketError(
            "packet and result case-ID sets must match: "
            f"missing={sorted(set(packet) - set(results))} "
            f"extra={sorted(set(results) - set(packet))}"
        )
    if len(packet) > max_cases:
        raise PacketError(
            f"assigned packet exceeds case limit ({len(packet)} > {max_cases})"
        )
    packet_bytes = sum(serialized_row_bytes(row) for row in packet.values())
    if packet_bytes > max_bytes:
        raise PacketError(
            f"assigned packet exceeds byte limit ({packet_bytes} > {max_bytes})"
        )
    groups = {packet_group_key(row) for row in packet.values()}
    if len(groups) != 1 or any(value is None for value in next(iter(groups))):
        raise PacketError(f"assigned packet must contain one semantic group: {sorted(groups, key=str)}")
    result_groups = {packet_group_key(row) for row in results.values()}
    if result_groups != groups:
        raise PacketError("packet result group does not match assigned packet")
    anomaly = build_review_anomaly_report(results.values(), slot=slot)
    if anomaly["blocking_signals"]:
        raise PacketError(
            "packet result has blocking anomaly: "
            + anomaly["blocking_signals"][0]["code"]
        )
    group = next(iter(groups))
    return {
        "cases": len(packet),
        "language": group[0],
        "locale": group[1],
        "family_id": group[2],
        "packet_bytes": packet_bytes,
        "anomaly_ready": anomaly["ready"],
        "anomaly": anomaly,
        "case_ids": sorted(packet),
    }


def build_review_receipt(
    packet_path: str | Path,
    result_path: str | Path,
    *,
    slot: str,
    packet_audit: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "slot": slot.upper(),
        "packet": Path(packet_path).name,
        "packet_sha256": sha256_file(packet_path),
        "result_sha256": sha256_file(result_path),
        "case_ids_sha256": case_ids_sha256(packet_audit["case_ids"]),
        "cases": packet_audit["cases"],
        "language": packet_audit["language"],
        "anomaly_ready": packet_audit["anomaly_ready"],
    }


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
    ordered = sorted(
        (dict(row) for row in rows), key=lambda row: str(row.get(identity_field, ""))
    )
    eligible: list[dict[str, Any]] = []
    for row in ordered:
        identity = row.get(identity_field)
        if not isinstance(identity, str) or not identity:
            raise PacketError(f"packet row is missing {identity_field}")
        if identity in completed:
            continue
        if language is not None and row.get("language") != language:
            continue
        eligible.append(row)
    if not eligible:
        return []
    selected_group = packet_group_key(eligible[0])
    selected: list[dict[str, Any]] = []
    total_bytes = 0
    for row in eligible:
        if packet_group_key(row) != selected_group:
            continue
        if len(selected) >= max_cases:
            break
        identity = row[identity_field]
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
    projected = [
        {
            key: value
            for key, value in _without_forbidden(_public(row)).items()
            if key in REVIEW_PACKET_KEYS
        }
        for row in blind_rows
    ]
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
    packet_rows: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge a result packet while preserving immutable blind fields."""
    slot = slot.upper()
    blind = _indexed_unique(blind_rows, "blind")
    existing = _indexed_unique(existing_rows, "existing review")
    results = _indexed_unique(result_rows, "packet result")
    if packet_rows is None:
        raise PacketError("assigned packet is required")
    validate_review_packet(
        packet_rows, results.values(), slot=slot, authoritative_rows=blind.values()
    )
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


def validate_adjudication_packet(
    packet_rows: Iterable[Mapping[str, Any]],
    result_rows: Iterable[Mapping[str, Any]],
    *,
    max_cases: int = ADJUDICATION_PACKET_MAX_CASES,
    max_bytes: int = ADJUDICATION_PACKET_MAX_BYTES,
) -> dict[str, Any]:
    packet = _indexed_unique(packet_rows, "assigned adjudication packet")
    results = _indexed_unique(result_rows, "adjudication packet result")
    if not packet:
        raise PacketError("assigned adjudication packet must not be empty")
    if set(packet) != set(results):
        raise PacketError(
            "adjudication packet and result case-ID sets must match: "
            f"missing={sorted(set(packet) - set(results))} "
            f"extra={sorted(set(results) - set(packet))}"
        )
    if len(packet) > max_cases:
        raise PacketError(
            f"adjudication packet exceeds case limit ({len(packet)} > {max_cases})"
        )
    packet_bytes = sum(serialized_row_bytes(row) for row in packet.values())
    if packet_bytes > max_bytes:
        raise PacketError(
            f"adjudication packet exceeds byte limit ({packet_bytes} > {max_bytes})"
        )
    for row in results.values():
        _validate_decision(row)
    identities = {
        row.get("adjudicator", {}).get("adjudicator_id")
        for row in results.values()
        if isinstance(row.get("adjudicator"), Mapping)
    }
    if len(identities) != 1 or None in identities:
        raise PacketError("adjudication result must contain one adjudicator identity")
    return {
        "cases": len(packet),
        "packet_bytes": packet_bytes,
        "case_ids": sorted(packet),
        "adjudicator_id": next(iter(identities)),
    }

def _public_list(rows: Any) -> list[Any]:
    if not isinstance(rows, list):
        return []
    return [_public(row) if isinstance(row, Mapping) else row for row in rows]


def _validate_oracle(oracle: Any, case_id: str) -> None:
    if not isinstance(oracle, Mapping):
        raise PacketError(f"{case_id}: accept decision requires final_oracle")
    unknown = set(oracle) - {"canonical", "accepted", "rejected"}
    if unknown:
        raise PacketError(f"{case_id}: final_oracle has unknown fields: {sorted(unknown)}")
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
    if "rejected" in oracle and (
        not isinstance(rejected, list)
        or any(not isinstance(v, str) or not v for v in rejected)
    ):
        raise PacketError(f"{case_id}: final_oracle.rejected must be a list of strings when supplied")
    if isinstance(rejected, list) and set(accepted) & set(rejected):
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
    unknown = set(row) - ADJUDICATION_ALLOWED_KEYS
    if unknown:
        raise PacketError(f"{case_id}: adjudication has unknown fields: {sorted(unknown)}")
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
    unknown_adjudicator = set(adjudicator) - ADJUDICATOR_ALLOWED_KEYS
    if unknown_adjudicator:
        raise PacketError(
            f"{case_id}: adjudicator has unknown fields: {sorted(unknown_adjudicator)}"
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
    packet_rows: Iterable[Mapping[str, Any]],
    *,
    output: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Merge adjudication results atomically with one stable identity."""
    existing = _indexed_unique(existing_rows, "existing adjudication")
    results = _indexed_unique(result_rows, "packet result")
    validate_adjudication_packet(packet_rows, results.values())
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
    "REVIEW_PACKET_KEYS",
    "REVIEW_PACKET_MAX_BYTES",
    "REVIEW_PACKET_MAX_CASES",
    "PacketError",
    "adjudication_packet_rows",
    "build_review_receipt",
    "case_ids_sha256",
    "finalize_adjudication",
    "merge_adjudication_rows",
    "merge_review_rows",
    "next_packet_number",
    "packet_group_key",
    "review_packet_rows",
    "select_packet_rows",
    "serialized_row_bytes",
    "sha256_file",
    "validate_adjudication_packet",
    "validate_review_packet",
]
