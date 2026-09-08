"""Numeral-specific blind review contracts and deterministic review gates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

REVIEW_SCHEMA_VERSION = "1.0.0"
REVIEW_PROTOCOL_VERSION = "numeral-review-v1"
ANNOTATION_STATUSES = {"form", "ambiguous", "invalid_request", "capability_blocker"}
REVIEW_COMPLETE_STATUSES = {"A": "review_a_complete", "B": "review_b_complete"}
FORBIDDEN_BLIND_KEYS = {
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
    "review_b",
    "review_a",
    "adjudication",
    "final_oracle",
    "canonical_output",
    "canonical_answer",
    "canonical_record",
    "source_evidence",
    "observed_oracle",
    "candidate_oracle",
}


class ReviewError(ValueError):
    """Raised when a review artifact violates its contract."""


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def case_id_for_record(record: Mapping[str, Any]) -> str:
    """Return a stable case identity derived from the semantic request."""
    identity = {
        "language": record.get("language"),
        "locale": record.get("locale"),
        "input": record.get("input"),
        "mode": record.get("mode"),
        "grammar": record.get("grammar", {}),
    }
    return "nfgcase-" + _digest(identity)


def _family_id(record: Mapping[str, Any]) -> str:
    value = record.get("family_id")
    if isinstance(value, str) and value.strip():
        return value
    grammar = record.get("grammar") or {}
    grammar_part = _digest(grammar) if grammar else "basic"
    return f"{record.get('language', 'und')}-{record.get('mode', 'cardinal')}-{grammar_part}"


def blind_review_case(record: Mapping[str, Any], reviewer_slot: str) -> dict[str, Any]:
    """Project a canonical candidate into an answer-free review case."""
    slot = reviewer_slot.upper()
    if slot not in {"A", "B"}:
        raise ReviewError("reviewer_slot must be A or B")
    required = ("language", "input", "mode")
    missing = [field for field in required if field not in record]
    if missing:
        raise ReviewError("candidate is missing " + ", ".join(missing))
    input_value = record["input"]
    if not isinstance(input_value, dict):
        raise ReviewError("candidate input must be an object")
    case = {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "case_id": case_id_for_record(record),
        "language": record["language"],
        "locale": record.get("locale"),
        "input": dict(input_value),
        "mode": record["mode"],
        "grammar": dict(record.get("grammar") or {}),
        "family_id": _family_id(record),
        "reviewer_slot": slot,
        "annotation": None,
        "review": {"status": "unreviewed"},
    }
    assert_blind_safe(case)
    return case



def neutral_review_case(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a candidate into a slot-independent semantic review case."""
    case = blind_review_case(record, "A")
    case.pop("reviewer_slot", None)
    return case

def review_case(record: Mapping[str, Any], reviewer_slot: str) -> dict[str, Any]:
    """Backward-compatible alias for :func:`blind_review_case`."""
    return blind_review_case(record, reviewer_slot)


def _collect_forbidden(value: Any, path: str = "review") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_BLIND_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_collect_forbidden(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_collect_forbidden(child, f"{path}[{index}]"))
    return found


def assert_blind_safe(value: Any) -> None:
    forbidden = _collect_forbidden(value)
    if forbidden:
        raise ReviewError(
            "blind review contains forbidden field at " + ", ".join(forbidden)
        )


def hidden_field_paths(value: Any) -> list[str]:
    """Return recursively found forbidden paths without raising."""
    return _collect_forbidden(value)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reviewer_id(row: Mapping[str, Any]) -> str | None:
    reviewer = row.get("reviewer")
    values = [
        reviewer.get("reviewer_id") if isinstance(reviewer, Mapping) else None,
        row.get("reviewer_id"),
        (row.get("review") or {}).get("reviewer_id")
        if isinstance(row.get("review"), Mapping)
        else None,
    ]
    return next((value for value in values if _nonempty_string(value)), None)


def reviewer_model_family(row: Mapping[str, Any]) -> str | None:
    reviewer = row.get("reviewer")
    values = [
        reviewer.get("model_family") if isinstance(reviewer, Mapping) else None,
        row.get("model_family"),
    ]
    return next((value for value in values if _nonempty_string(value)), None)


def _oracle_issues(oracle: Any, label: str) -> list[str]:
    if not isinstance(oracle, Mapping):
        return [f"{label}: annotation.oracle must be an object"]
    canonical = oracle.get("canonical")
    accepted = oracle.get("accepted")
    rejected = oracle.get("rejected")
    issues: list[str] = []
    if not _nonempty_string(canonical):
        issues.append(f"{label}: oracle.canonical must be a non-empty string")
    if (
        not isinstance(accepted, list)
        or not accepted
        or any(not _nonempty_string(value) for value in accepted)
    ):
        issues.append(f"{label}: oracle.accepted must be a non-empty list of strings")
    elif canonical not in accepted:
        issues.append(f"{label}: oracle.canonical must appear in oracle.accepted")
    if not isinstance(rejected, list) or any(
        not _nonempty_string(value) for value in rejected
    ):
        issues.append(f"{label}: oracle.rejected must be a list of strings")
    if isinstance(accepted, list) and len(accepted) != len(set(accepted)):
        issues.append(f"{label}: oracle.accepted must not contain duplicates")
    if isinstance(rejected, list) and len(rejected) != len(set(rejected)):
        issues.append(f"{label}: oracle.rejected must not contain duplicates")
    if isinstance(accepted, list) and isinstance(rejected, list):
        overlap = sorted(set(accepted) & set(rejected))
        if overlap:
            issues.append(
                f"{label}: oracle accepted/rejected overlap: {', '.join(overlap)}"
            )
    return issues


def _blocker_issues(blocker: Any, label: str) -> list[str]:
    if not isinstance(blocker, Mapping):
        return [f"{label}: capability blocker requires blocker object"]
    issues = [
        f"{label}: blocker.{key} is required"
        for key in ("code", "reason")
        if not _nonempty_string(blocker.get(key))
    ]
    if blocker.get("retryable") is not True:
        issues.append(f"{label}: blocker.retryable must be true")
    return issues


def _annotation_issues(row: Mapping[str, Any], label: str) -> list[str]:
    annotation = row.get("annotation")
    if not isinstance(annotation, Mapping):
        return [f"{label}: annotation must be an object"]
    status = annotation.get("status")
    if status not in ANNOTATION_STATUSES:
        return [f"{label}: invalid annotation status {status!r}"]
    issues: list[str] = []
    assessment = annotation.get("grammar_assessment")
    if not isinstance(assessment, Mapping):
        issues.append(f"{label}: annotation.grammar_assessment must be an object")
    if status == "form":
        issues.extend(_oracle_issues(annotation.get("oracle"), label))
    elif status == "capability_blocker":
        issues.extend(_blocker_issues(annotation.get("blocker"), label))
        if "oracle" in annotation:
            issues.extend(_oracle_issues(annotation["oracle"], label))
    elif "oracle" in annotation and annotation["oracle"] is not None:
        issues.extend(_oracle_issues(annotation["oracle"], label))
    return issues


def validate_review_rows(
    rows: Iterable[Mapping[str, Any]], *, slot: str
) -> dict[str, Any]:
    """Validate a completed reviewer artifact without mutating it."""
    expected_slot = slot.upper()
    if expected_slot not in {"A", "B"}:
        raise ReviewError("review slot must be A or B")
    rows_list = [dict(row) for row in rows]
    expected_status = REVIEW_COMPLETE_STATUSES[expected_slot]
    indexed: dict[str, dict[str, Any]] = {}
    reviewer_ids: set[str] = set()
    model_families: set[str] = set()
    issues: list[dict[str, Any]] = []
    blockers = 0
    for index, row in enumerate(rows_list):
        label = str(row.get("case_id") or f"row-{index}")
        row_errors: list[str] = []
        if row.get("review_schema_version") != REVIEW_SCHEMA_VERSION:
            row_errors.append(
                f"{label}: review_schema_version must be {REVIEW_SCHEMA_VERSION}"
            )
        if not _nonempty_string(row.get("case_id")):
            row_errors.append(f"{label}: case_id is required")
        elif row["case_id"] in indexed:
            row_errors.append(f"{label}: duplicate case_id")
        else:
            indexed[row["case_id"]] = row
        if row.get("reviewer_slot") != expected_slot:
            row_errors.append(f"{label}: reviewer_slot must be {expected_slot}")
        if not _nonempty_string(row.get("language")):
            row_errors.append(f"{label}: language is required")
        if not isinstance(row.get("input"), Mapping):
            row_errors.append(f"{label}: input must be an object")
        if not _nonempty_string(row.get("mode")):
            row_errors.append(f"{label}: mode is required")
        if not isinstance(row.get("grammar"), Mapping):
            row_errors.append(f"{label}: grammar must be an object")
        reviewer_id = _reviewer_id(row)
        family = reviewer_model_family(row)
        if reviewer_id:
            reviewer_ids.add(reviewer_id)
        else:
            row_errors.append(f"{label}: reviewer.reviewer_id is required")
        if family:
            model_families.add(family)
        else:
            row_errors.append(f"{label}: reviewer.model_family is required")
        lifecycle = row.get("review")
        if (
            not isinstance(lifecycle, Mapping)
            or lifecycle.get("status") != expected_status
        ):
            row_errors.append(f"{label}: review.status must be {expected_status}")
        row_errors.extend(_annotation_issues(row, label))
        if (
            isinstance(row.get("annotation"), Mapping)
            and row["annotation"].get("status") == "capability_blocker"
        ):
            blockers += 1
        for path in _collect_forbidden(row):
            row_errors.append(f"{label}: forbidden blind field at {path}")
        for error in row_errors:
            issues.append({"case_id": row.get("case_id"), "message": error})
    if len(reviewer_ids) != 1:
        issues.append(
            {
                "case_id": None,
                "message": f"review {expected_slot} must contain one stable reviewer_id",
            }
        )
    if len(model_families) != 1:
        issues.append(
            {
                "case_id": None,
                "message": f"review {expected_slot} must contain one stable model_family",
            }
        )
    return {
        "slot": expected_slot,
        "rows": len(rows_list),
        "reviewer_id": next(iter(reviewer_ids), None),
        "model_family": next(iter(model_families), None),
        "reviewer_ids": sorted(reviewer_ids),
        "model_families": sorted(model_families),
        "capability_blockers": blockers,
        "issues": issues,
        "ready": not issues,
        "_indexed": indexed,
    }


def _oracle(row: Mapping[str, Any]) -> Mapping[str, Any]:
    annotation = row.get("annotation")
    value = annotation.get("oracle") if isinstance(annotation, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def compare_reviews(
    review_a: Iterable[Mapping[str, Any]], review_b: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Compare two validated reviewer artifacts by case ID."""
    report_a = validate_review_rows(review_a, slot="A")
    report_b = validate_review_rows(review_b, slot="B")
    if report_a["issues"] or report_b["issues"]:
        raise ReviewError("cannot compare invalid review artifacts")
    ids_a = set(report_a["_indexed"])
    ids_b = set(report_b["_indexed"])
    if ids_a != ids_b:
        raise ReviewError(
            f"review case sets differ: only_a={sorted(ids_a - ids_b)} only_b={sorted(ids_b - ids_a)}"
        )
    comparisons: list[dict[str, Any]] = []
    for case_id in sorted(ids_a):
        left = report_a["_indexed"][case_id]
        right = report_b["_indexed"][case_id]
        for field in ("language", "locale", "input", "mode", "grammar", "family_id"):
            if left.get(field) != right.get(field):
                raise ReviewError(f"{case_id}: reviewer inputs disagree for {field}")
        status_a = (left.get("annotation") or {}).get("status")
        status_b = (right.get("annotation") or {}).get("status")
        if status_a == "capability_blocker" or status_b == "capability_blocker":
            classification = "one_or_both_blocked"
        elif status_a != status_b:
            classification = "request_validity_disagreement"
        elif _oracle(left).get("canonical") != _oracle(right).get("canonical"):
            classification = "canonical_disagreement"
        elif set(_oracle(left).get("accepted", [])) != set(
            _oracle(right).get("accepted", [])
        ) or set(_oracle(left).get("rejected", [])) != set(
            _oracle(right).get("rejected", [])
        ):
            classification = "variant_set_disagreement"
        else:
            classification = "exact_agreement"
        comparisons.append(
            {
                "case_id": case_id,
                "classification": classification,
                "reviewer_a": report_a["reviewer_id"],
                "reviewer_b": report_b["reviewer_id"],
            }
        )
    return comparisons


def review_preflight(
    cases: Iterable[Mapping[str, Any]],
    review_a: Iterable[Mapping[str, Any]],
    review_b: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the deterministic gate required before adjudication."""
    cases_list = [dict(row) for row in cases]
    case_map = {
        row.get("case_id"): row
        for row in cases_list
        if _nonempty_string(row.get("case_id"))
    }
    report_a = validate_review_rows(review_a, slot="A")
    report_b = validate_review_rows(review_b, slot="B")
    issues = [issue["message"] for issue in report_a["issues"] + report_b["issues"]]
    ids_a = set(report_a["_indexed"])
    ids_b = set(report_b["_indexed"])
    expected = set(case_map)
    if ids_a != expected:
        issues.append(
            f"review A does not cover exactly the batch: missing={sorted(expected - ids_a)} extra={sorted(ids_a - expected)}"
        )
    if ids_b != expected:
        issues.append(
            f"review B does not cover exactly the batch: missing={sorted(expected - ids_b)} extra={sorted(ids_b - expected)}"
        )
    if ids_a != ids_b:
        issues.append(
            f"review case sets differ: only_a={sorted(ids_a - ids_b)} only_b={sorted(ids_b - ids_a)}"
        )
    if (
        report_a["reviewer_id"] == report_b["reviewer_id"]
        and report_a["reviewer_id"] is not None
    ):
        issues.append("reviewer A and reviewer B must have distinct reviewer_id values")
    if (
        report_a["model_family"] == report_b["model_family"]
        and report_a["model_family"] is not None
    ):
        issues.append(
            "reviewer A and reviewer B must have distinct model_family values"
        )
    if report_a["capability_blockers"] or report_b["capability_blockers"]:
        issues.append("capability blockers prevent review readiness")
    comparisons: list[dict[str, Any]] = []
    if not report_a["issues"] and not report_b["issues"] and ids_a == ids_b:
        comparisons = compare_reviews(review_a, review_b)
    return {
        "ready": not issues,
        "cases": len(case_map),
        "review_a": {
            key: value for key, value in report_a.items() if key != "_indexed"
        },
        "review_b": {
            key: value for key, value in report_b.items() if key != "_indexed"
        },
        "comparisons": comparisons,
        "issues": sorted(set(issues)),
    }


__all__ = [
    "ANNOTATION_STATUSES",
    "FORBIDDEN_BLIND_KEYS",
    "REVIEW_PROTOCOL_VERSION",
    "REVIEW_SCHEMA_VERSION",
    "ReviewError",
    "assert_blind_safe",
    "blind_review_case",
    "case_id_for_record",
    "compare_reviews",
    "hidden_field_paths",
    "neutral_review_case",
    "review_case",
    "review_preflight",
    "reviewer_model_family",
    "validate_review_rows",
]
