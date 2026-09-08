"""Deterministic review anomaly signals that never decide Gold annotations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any


def _annotation(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("annotation")
    return value if isinstance(value, Mapping) else {}


def _oracle(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _annotation(row).get("oracle")
    return value if isinstance(value, Mapping) else {}


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_review_anomaly_report(
    review_rows: Iterable[Mapping[str, Any]],
    *,
    source_rows: Iterable[Mapping[str, Any]] = (),
    slot: str | None = None,
    substantial_packet_size: int = 10,
) -> dict[str, Any]:
    """Return suspicious bulk-review signals for a completed packet."""
    rows = [dict(row) for row in review_rows]
    sources = [dict(row) for row in source_rows]
    if not rows:
        return {
            "slot": slot,
            "cases": 0,
            "ready": True,
            "fresh_review_required": False,
            "signals": [],
        }
    signals: list[dict[str, Any]] = []
    statuses = [str(_annotation(row).get("status") or "") for row in rows]
    if len(rows) >= substantial_packet_size and len(set(statuses)) == 1:
        signals.append(
            {
                "code": "uniform_annotation_status",
                "case_ids": [row.get("case_id") for row in rows],
                "status": statuses[0],
            }
        )
    rationales = [str(_annotation(row).get("notes") or "") for row in rows]
    if (
        len(rows) >= substantial_packet_size
        and rationales
        and rationales[0]
        and len(set(rationales)) == 1
    ):
        signals.append(
            {
                "code": "uniform_rationale",
                "case_ids": [row.get("case_id") for row in rows],
            }
        )
    oracle_digests = [_digest(_oracle(row)) for row in rows]
    if len(rows) >= substantial_packet_size and len(set(oracle_digests)) == 1:
        signals.append(
            {
                "code": "repeated_oracle_object",
                "case_ids": [row.get("case_id") for row in rows],
            }
        )
    accepted_sets = [tuple(_oracle(row).get("accepted", [])) for row in rows]
    if (
        len(rows) >= substantial_packet_size
        and accepted_sets
        and len(set(accepted_sets)) == 1
    ):
        signals.append(
            {
                "code": "uniform_accepted_set",
                "case_ids": [row.get("case_id") for row in rows],
            }
        )
    values = [
        row.get("input", {}).get("value")
        if isinstance(row.get("input"), Mapping)
        else None
        for row in rows
    ]
    canonicals = [_oracle(row).get("canonical") for row in rows]
    if (
        values
        and all(isinstance(value, str) for value in values)
        and canonicals == values
    ):
        signals.append(
            {
                "code": "canonical_equals_decimal_input",
                "case_ids": [row.get("case_id") for row in rows],
            }
        )
    languages = {
        row.get("language") for row in rows if isinstance(row.get("language"), str)
    }
    if len(languages) > 1:
        signals.append(
            {
                "code": "mixed_language_packet",
                "case_ids": [row.get("case_id") for row in rows],
                "languages": sorted(languages),
            }
        )
    if (
        statuses
        and set(statuses) == {"invalid_request"}
        and any(
            isinstance(row.get("grammar"), Mapping) and row["grammar"] for row in rows
        )
    ):
        signals.append(
            {
                "code": "grammar_rows_all_invalid_request",
                "case_ids": [row.get("case_id") for row in rows],
            }
        )
    source_outputs = {
        row.get("case_id"): row.get("source_form") or row.get("expected")
        for row in sources
        if isinstance(row.get("case_id"), str)
    }
    if source_outputs and all(
        case_id in source_outputs
        and _oracle(row).get("canonical") == source_outputs[case_id]
        for case_id, row in zip((row.get("case_id") for row in rows), rows)
    ):
        signals.append(
            {
                "code": "uniform_hidden_reference_match",
                "case_ids": [row.get("case_id") for row in rows],
            }
        )
    return {
        "slot": slot,
        "cases": len(rows),
        "ready": not signals,
        "fresh_review_required": bool(signals),
        "signals": signals,
    }


__all__ = ["build_review_anomaly_report"]
