"""Compare system predictions with accepted Gold/reference forms."""

from __future__ import annotations

import unicodedata
from typing import Any


def normalize(text: str, mode: str) -> str:
    value = unicodedata.normalize("NFC", text)
    if mode == "exact":
        return value
    if mode == "spacefold":
        return " ".join(value.split())
    raise ValueError(f"unknown normalization mode: {mode}")


def compare_predictions(
    gold_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    *,
    normalization: str = "exact",
) -> dict[str, Any]:
    predictions: dict[str, str] = {}
    duplicate_prediction_ids: list[str] = []
    for row in prediction_rows:
        record_id = row.get("id")
        output = row.get("output")
        if not isinstance(record_id, str) or not isinstance(output, str):
            raise TypeError("prediction rows require string id and output")
        if record_id in predictions:
            duplicate_prediction_ids.append(record_id)
        predictions[record_id] = output
    if duplicate_prediction_ids:
        raise ValueError(
            "duplicate prediction id(s): "
            + ", ".join(sorted(set(duplicate_prediction_ids)))
        )

    details: list[dict[str, Any]] = []
    exact = accepted = missing = mismatched = 0
    gold_ids = {row["id"] for row in gold_rows}
    for row in gold_rows:
        record_id = row["id"]
        prediction = predictions.get(record_id)
        if prediction is None:
            missing += 1
            status = "missing"
        else:
            canonical = row["oracle"]["canonical"]
            accepted_forms = row["oracle"]["accepted"]
            norm_prediction = normalize(prediction, normalization)
            norm_canonical = normalize(canonical, normalization)
            norm_accepted = {
                normalize(value, normalization) for value in accepted_forms
            }
            if norm_prediction == norm_canonical:
                exact += 1
                accepted += 1
                status = "canonical"
            elif norm_prediction in norm_accepted:
                accepted += 1
                status = "accepted_variant"
            else:
                mismatched += 1
                status = "mismatch"
        details.append(
            {
                "id": record_id,
                "language": row["language"],
                "locale": row.get("locale"),
                "input": row["input"],
                "prediction": prediction,
                "canonical": row["oracle"]["canonical"],
                "accepted": row["oracle"]["accepted"],
                "status": status,
            }
        )
    extra = sorted(set(predictions) - gold_ids)
    total = len(gold_rows)
    evaluated = total - missing
    return {
        "normalization": normalization,
        "records": total,
        "evaluated": evaluated,
        "canonical_matches": exact,
        "accepted_matches": accepted,
        "mismatches": mismatched,
        "missing_predictions": missing,
        "extra_prediction_ids": extra,
        "accuracy": accepted / total if total else 0.0,
        "evaluated_accuracy": accepted / evaluated if evaluated else 0.0,
        "details": details,
    }
