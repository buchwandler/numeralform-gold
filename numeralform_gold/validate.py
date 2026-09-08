"""Strict MVP validation without a runtime JSON Schema dependency."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .model import record_id

ALLOWED_QUALITIES = {"gold", "reference", "quarantine"}
ALLOWED_MODES = {"cardinal", "ordinal", "ordinal_num", "year", "currency"}
ALLOWED_KINDS = {"integer", "decimal", "fraction"}


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "id",
        "language",
        "locale",
        "input",
        "mode",
        "grammar",
        "oracle",
        "quality",
        "source_observations",
    }
    missing = sorted(required - set(record))
    if missing:
        return [f"missing required field(s): {', '.join(missing)}"]
    if record.get("schema_version") != "1.0.0":
        errors.append("schema_version must be 1.0.0")
    if not isinstance(record.get("language"), str) or len(record["language"]) < 2:
        errors.append("language must be a non-empty language code")
    if record.get("locale") is not None and not isinstance(record.get("locale"), str):
        errors.append("locale must be string or null")
    input_value = record.get("input")
    if not isinstance(input_value, dict):
        errors.append("input must be an object")
    else:
        if input_value.get("kind") not in ALLOWED_KINDS:
            errors.append(f"input.kind must be one of {sorted(ALLOWED_KINDS)}")
        if not isinstance(input_value.get("value"), str) or not input_value.get(
            "value"
        ):
            errors.append("input.value must be a non-empty string")
    if record.get("mode") not in ALLOWED_MODES:
        errors.append(f"mode must be one of {sorted(ALLOWED_MODES)}")
    if not isinstance(record.get("grammar"), dict):
        errors.append("grammar must be an object")
    if record.get("quality") not in ALLOWED_QUALITIES:
        errors.append(f"quality must be one of {sorted(ALLOWED_QUALITIES)}")
    oracle = record.get("oracle")
    if not isinstance(oracle, dict):
        errors.append("oracle must be an object")
    else:
        canonical = oracle.get("canonical")
        accepted = oracle.get("accepted")
        rejected = oracle.get("rejected")
        if not isinstance(canonical, str) or not canonical:
            errors.append("oracle.canonical must be a non-empty string")
        if (
            not isinstance(accepted, list)
            or not accepted
            or not all(isinstance(item, str) and item for item in accepted)
        ):
            errors.append("oracle.accepted must be a non-empty list of strings")
        else:
            if len(accepted) != len(set(accepted)):
                errors.append("oracle.accepted must not contain duplicates")
            if isinstance(canonical, str) and canonical not in accepted:
                errors.append("oracle.canonical must appear in oracle.accepted")
        if not isinstance(rejected, list) or not all(
            isinstance(item, str) and item for item in rejected
        ):
            errors.append("oracle.rejected must be a list of strings")
        elif len(rejected) != len(set(rejected)):
            errors.append("oracle.rejected must not contain duplicates")
        if isinstance(accepted, list) and isinstance(rejected, list):
            overlap = sorted(set(accepted) & set(rejected))
            if overlap:
                errors.append("accepted/rejected overlap: " + ", ".join(overlap))
    sources = record.get("source_observations")
    if not isinstance(sources, list) or not sources:
        errors.append("source_observations must be a non-empty list")
    elif any(not isinstance(source, dict) for source in sources):
        errors.append("every source observation must be an object")
    else:
        for index, source in enumerate(sources):
            for key in ("benchmark", "role", "source_id"):
                if not isinstance(source.get(key), str) or not source.get(key):
                    errors.append(f"source_observations[{index}].{key} is required")
    if (
        isinstance(record.get("language"), str)
        and isinstance(record.get("input"), dict)
        and isinstance(record.get("grammar"), dict)
        and record.get("mode") in ALLOWED_MODES
    ):
        expected_id = record_id(
            language=record["language"],
            locale=record.get("locale"),
            input_value=record["input"],
            mode=record["mode"],
            grammar=record["grammar"],
        )
        if record.get("id") != expected_id:
            errors.append(f"id mismatch: expected {expected_id}")
    return errors


def validate_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    count = 0
    for count, record in enumerate(records, 1):
        row_errors = validate_record(record)
        record_key = record.get("id", f"row-{count}")
        if isinstance(record_key, str) and record_key in seen_ids:
            row_errors.append("duplicate record id")
        if isinstance(record_key, str):
            seen_ids.add(record_key)
        if row_errors:
            issues.append({"row": count, "id": record_key, "errors": row_errors})
    return {"records": count, "errors": len(issues), "issues": issues}
