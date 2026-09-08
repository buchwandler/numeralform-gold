"""Batch review gates and mechanical Numeralform Gold integration."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .io import read_json, read_jsonl, write_json, write_jsonl
from .model import make_record
from .packets import PacketError, finalize_adjudication
from .review import review_preflight
from .review_anomaly import build_review_anomaly_report
from .review_lineage import build_review_evidence, write_review_evidence
from .validate import validate_records
from .work_layout import BatchLayout


def _rows_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        list(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_corpus(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    if target.is_dir():
        rows: list[dict[str, Any]] = []
        for shard in sorted(target.glob("*.jsonl")):
            rows.extend(read_jsonl(shard))
        return rows
    return read_jsonl(target)


def _write_corpus(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    rows_list = [dict(row) for row in rows]
    if target.suffix == ".jsonl":
        write_jsonl(target, rows_list)
        return
    target.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_list:
        grouped[str(row.get("language", "und"))].append(row)
    for language, shard_rows in sorted(grouped.items()):
        write_jsonl(
            target / f"{language}.jsonl",
            sorted(shard_rows, key=lambda row: row.get("id", "")),
        )


def _batch_artifacts(
    batch_root: str | Path,
) -> tuple[BatchLayout, list[dict], list[dict], list[dict], list[dict], list[dict]]:
    layout = BatchLayout(Path(batch_root))
    cases = read_jsonl(layout.cases) if layout.cases.is_file() else []
    source_rows = (
        read_jsonl(layout.source_observations)
        if layout.source_observations.is_file()
        else []
    )
    source_map: dict[str, list[dict]] = defaultdict(list)
    for row in source_rows:
        case_id = row.get("case_id")
        observation = row.get("observation", row)
        if isinstance(case_id, str) and isinstance(observation, dict):
            source_map[case_id].append(observation)
    cases = [
        {
            **case,
            "source_observations": source_map.get(
                case.get("case_id"), case.get("source_observations", [])
            ),
        }
        for case in cases
    ]
    review_a = (
        read_jsonl(layout.review_complete("A"))
        if layout.review_complete("A").is_file()
        else []
    )
    review_b = (
        read_jsonl(layout.review_complete("B"))
        if layout.review_complete("B").is_file()
        else []
    )
    decisions_path = (
        layout.adjudication_decisions
        if layout.adjudication_decisions.is_file()
        else layout.adjudication_partial
    )
    decisions = read_jsonl(decisions_path) if decisions_path.is_file() else []
    metadata = (
        read_json(layout.metadata)
        if layout.metadata.is_file()
        else {"batch_id": layout.root.name}
    )
    return layout, cases, review_a, review_b, decisions, metadata


def check_reviews(
    cases: Iterable[Mapping[str, Any]],
    review_a: Iterable[Mapping[str, Any]],
    review_b: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Run the exact A/B readiness gate, including deterministic anomaly checks."""
    cases_list = [dict(row) for row in cases]
    review_a_list, review_b_list = (
        [dict(row) for row in review_a],
        [dict(row) for row in review_b],
    )
    report = review_preflight(cases_list, review_a_list, review_b_list)
    anomalies = {
        "A": build_review_anomaly_report(review_a_list, slot="A"),
        "B": build_review_anomaly_report(review_b_list, slot="B"),
    }
    issues = list(report["issues"])
    for slot, anomaly in anomalies.items():
        if anomaly["fresh_review_required"]:
            issues.append(f"review {slot}: anomaly requires fresh review")
    report["anomalies"] = anomalies
    report["issues"] = sorted(set(issues))
    report["ready"] = not report["issues"]
    return report


def build_accepted_record(
    case: Mapping[str, Any],
    decision: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a v1 canonical record from one accepted adjudication."""
    oracle = decision.get("final_oracle")
    if not isinstance(oracle, Mapping):
        raise TypeError(f"{case.get('case_id')}: accept decision requires final_oracle")
    source_observations = deepcopy(case.get("source_observations", []))
    if not isinstance(source_observations, list) or not source_observations:
        raise ValueError(
            f"{case.get('case_id')}: accepted record requires source observations"
        )
    record = make_record(
        language=str(case["language"]),
        locale=case.get("locale"),
        value=case["input"]["value"],
        form=str(oracle["canonical"]),
        mode=str(case["mode"]),
        grammar=dict(case.get("grammar") or {}),
        quality="gold",
        source_observations=source_observations,
        kind=str(case["input"].get("kind", "integer")),
        accepted=list(oracle.get("accepted", [])),
        rejected=list(oracle.get("rejected", [])),
    )
    if existing is not None and existing.get("id") != record["id"]:
        raise ValueError(f"semantic identity collision for {case.get('case_id')}")
    return record


def validate_accepted_decisions(
    cases: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    existing_records: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    case_map = {row.get("case_id"): row for row in cases}
    existing_map = {
        (
            row.get("language"),
            row.get("locale"),
            json.dumps(row.get("input"), sort_keys=True),
            row.get("mode"),
            json.dumps(row.get("grammar", {}), sort_keys=True),
        ): row
        for row in existing_records
    }
    diagnostics: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.get("decision") != "accept":
            continue
        case_id = decision.get("case_id")
        case = case_map.get(case_id)
        if case is None:
            diagnostics.append(
                {"case_id": case_id, "errors": ["case is missing from batch"]}
            )
            continue
        try:
            key = (
                case.get("language"),
                case.get("locale"),
                json.dumps(case.get("input"), sort_keys=True),
                case.get("mode"),
                json.dumps(case.get("grammar", {}), sort_keys=True),
            )
            record = build_accepted_record(case, decision, existing_map.get(key))
            errors = validate_records([record])
            if errors["errors"]:
                diagnostics.append({"case_id": case_id, "errors": errors["issues"]})
        except (KeyError, TypeError, ValueError) as exc:
            diagnostics.append({"case_id": case_id, "errors": [str(exc)]})
    return diagnostics


def batch_preflight(batch_root: str | Path, corpus_path: str | Path) -> dict[str, Any]:
    """Validate a batch without mutating its decisions or canonical corpus."""
    layout, cases, review_a, review_b, decisions, metadata = _batch_artifacts(
        batch_root
    )
    existing = _read_corpus(corpus_path)
    review = check_reviews(cases, review_a, review_b)
    case_ids = {case.get("case_id") for case in cases}
    decision_ids = [decision.get("case_id") for decision in decisions]
    coverage_ok = (
        len(decision_ids) == len(set(decision_ids)) and set(decision_ids) == case_ids
    )
    invalid = validate_accepted_decisions(cases, decisions, existing)
    accepted = sum(row.get("decision") == "accept" for row in decisions)
    excluded = sum(row.get("decision") == "exclude" for row in decisions)
    unresolved = sum(row.get("decision") == "unresolved" for row in decisions)
    ready = bool(review["ready"] and coverage_ok and not invalid)
    return {
        "batch_id": metadata.get("batch_id", layout.root.name),
        "cases": len(cases),
        "reviews_ready": review["ready"],
        "adjudication_complete": coverage_ok,
        "accept": accepted,
        "exclude": excluded,
        "unresolved": unresolved,
        "invalid_accepts": invalid,
        "ready_to_finalize": ready,
        "review_issues": review["issues"],
    }


def integrate_batch(
    batch_root: str | Path,
    corpus_path: str | Path,
    *,
    write: bool = False,
    lineage_path: str | Path | None = None,
) -> dict[str, Any]:
    """Integrate accepted adjudications, optionally writing canonical artifacts."""
    layout, cases, review_a, review_b, decisions, metadata = _batch_artifacts(
        batch_root
    )
    review = check_reviews(cases, review_a, review_b)
    if not review["ready"]:
        raise ValueError("review-check failed: " + "; ".join(review["issues"]))
    try:
        decisions = finalize_adjudication(cases, decisions)
    except PacketError as exc:
        raise ValueError(str(exc)) from exc
    existing = _read_corpus(corpus_path)
    existing_map = {
        (
            row.get("language"),
            row.get("locale"),
            json.dumps(row.get("input"), sort_keys=True),
            row.get("mode"),
            json.dumps(row.get("grammar", {}), sort_keys=True),
        ): row
        for row in existing
    }
    final_records: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    retry: list[dict[str, Any]] = []
    case_map = {case["case_id"]: case for case in cases}
    for decision in decisions:
        case = case_map[decision["case_id"]]
        if decision["decision"] == "accept":
            key = (
                case.get("language"),
                case.get("locale"),
                json.dumps(case.get("input"), sort_keys=True),
                case.get("mode"),
                json.dumps(case.get("grammar", {}), sort_keys=True),
            )
            final_records.append(
                build_accepted_record(case, decision, existing_map.get(key))
            )
        elif decision["decision"] == "exclude":
            excluded.append(
                {
                    "case_id": case["case_id"],
                    "reason": decision.get("rationale", "excluded"),
                }
            )
        else:
            retry.append(
                {
                    "case_id": case["case_id"],
                    "reason": decision.get("rationale", "unresolved"),
                    "blocker": decision["blocker"],
                }
            )
    combined = {row["id"]: row for row in existing}
    combined.update({row["id"]: row for row in final_records})
    corpus_report = validate_records(combined.values())
    if corpus_report["errors"]:
        raise ValueError(
            "integrated corpus is invalid: "
            + json.dumps(corpus_report["issues"], ensure_ascii=False)
        )
    result = {
        "ready": True,
        "batch_id": metadata.get("batch_id", layout.root.name),
        "records": len(final_records),
        "records_added": sum(
            record["id"] not in {row["id"] for row in existing}
            for record in final_records
        ),
        "excluded": excluded,
        "retry": retry,
        "review": review,
        "corpus_validation": "passed",
        "skipped_integration": False,
    }
    if write:
        _write_corpus(corpus_path, combined.values())
        write_jsonl(layout.integration_exclusions, excluded)
        write_jsonl(layout.integration_retry, retry)
        entries = build_review_evidence(
            cases,
            review_a,
            review_b,
            review.get("comparisons", []),
            decisions,
            records=final_records,
            campaign_id=metadata.get("campaign_id"),
            batch_id=str(metadata.get("batch_id", layout.root.name)),
            integration_revision=_rows_digest(final_records),
        )
        target_lineage = (
            Path(lineage_path)
            if lineage_path
            else Path("data/lineage/review-evidence.jsonl")
        )
        write_review_evidence(target_lineage, entries)
        write_json(
            layout.integration_summary,
            {
                **result,
                "state": "integrated",
                "decision_sha256": _rows_digest(decisions),
                "case_sha256": _rows_digest(cases),
            },
        )
        result["lineage"] = str(target_lineage)
    return result


def finalize_batch(
    batch_root: str | Path,
    corpus_path: str | Path,
    *,
    write: bool = False,
    lineage_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run batch integration and mark a written batch finalized."""
    result = integrate_batch(
        batch_root, corpus_path, write=write, lineage_path=lineage_path
    )
    if write:
        layout, _cases, _a, _b, _decisions, metadata = _batch_artifacts(batch_root)
        metadata = dict(metadata)
        metadata["state"] = "finalized"
        metadata["finalization"] = {
            "accepted": result["records"],
            "excluded": len(result["excluded"]),
            "retry_deferred": len(result["retry"]),
        }
        write_json(layout.metadata, metadata)
        result["state"] = "finalized"
    else:
        result["state"] = "ready_to_finalize"
    return result


__all__ = [
    "batch_preflight",
    "build_accepted_record",
    "check_reviews",
    "finalize_batch",
    "integrate_batch",
    "validate_accepted_decisions",
]
