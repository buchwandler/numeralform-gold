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


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        candidate.get("language"),
        candidate.get("locale"),
        json.dumps(candidate.get("input"), sort_keys=True),
        candidate.get("mode"),
        json.dumps(candidate.get("grammar", {}), sort_keys=True),
    )


def aggregate_candidates(
    candidates: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge independent observations that share one semantic request."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for candidate in candidates:
        key = _candidate_key(candidate)
        current = grouped.get(key)
        if current is None:
            grouped[key] = deepcopy(dict(candidate))
            continue
        observations = current.setdefault("source_observations", [])
        for observation in candidate.get("source_observations", []):
            if observation not in observations:
                observations.append(deepcopy(observation))
        if current.get("oracle") != candidate.get("oracle"):
            conflicts.append(
                {
                    "semantic_key": key,
                    "record_id": current.get("id"),
                    "candidates": [current.get("oracle"), candidate.get("oracle")],
                }
            )
    return sorted(grouped.values(), key=lambda row: str(row.get("id", ""))), conflicts


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


def _review_anomaly_summary(
    review_rows: Iterable[Mapping[str, Any]],
    *,
    slot: str,
) -> dict[str, Any]:
    rows = [dict(row) for row in review_rows]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        language = row.get("language")
        key = language if isinstance(language, str) and language else "<missing>"
        grouped[key].append(row)

    per_language: dict[str, dict[str, Any]] = {}
    signals: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for language in sorted(grouped):
        anomaly = build_review_anomaly_report(grouped[language], slot=slot)
        per_language[language] = anomaly
        signals.extend(
            {"language": language, **signal} for signal in anomaly["signals"]
        )
        blocking.extend(
            {"language": language, **signal} for signal in anomaly["blocking_signals"]
        )

    if len(grouped) > 1:
        signals.append(
            {
                "code": "mixed_language_review_artifact",
                "languages": sorted(grouped),
            }
        )

    return {
        "slot": slot,
        "cases": len(rows),
        "ready": not blocking,
        "fresh_review_required": bool(blocking),
        "signals": signals,
        "blocking_signals": blocking,
        "per_language": per_language,
    }


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
        "A": _review_anomaly_summary(review_a_list, slot="A"),
        "B": _review_anomaly_summary(review_b_list, slot="B"),
    }
    issues = list(report["issues"])
    for slot, anomaly in anomalies.items():
        if anomaly["blocking_signals"]:
            issues.append(f"review {slot}: anomaly requires fresh review")
    report["anomalies"] = anomalies
    report["issues"] = sorted(set(issues))
    report["ready"] = not report["issues"]
    return report


def _source_observation_key(source: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(source.get("benchmark", "")),
        str(source.get("source_id", "")),
        str(source.get("source_version", "")),
    )


def _merged_source_observations(
    current: Iterable[Mapping[str, Any]],
    prior: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source in [*prior, *current]:
        if isinstance(source, Mapping):
            key = _source_observation_key(source)
            merged.setdefault(key, deepcopy(dict(source)))
    return [merged[key] for key in sorted(merged)]


def build_accepted_record(
    case: Mapping[str, Any],
    decision: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a v1 canonical record from one accepted adjudication."""
    oracle = decision.get("final_oracle")
    if not isinstance(oracle, Mapping):
        raise TypeError(f"{case.get('case_id')}: accept decision requires final_oracle")
    source_observations = _merged_source_observations(
        case.get("source_observations", []),
        existing.get("source_observations", []) if existing else [],
    )
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


def _adjudicator_issues(
    decisions: Iterable[Mapping[str, Any]],
    review: Mapping[str, Any],
) -> list[str]:
    reviewer_ids = {
        review.get("review_a", {}).get("reviewer_id"),
        review.get("review_b", {}).get("reviewer_id"),
    } - {None}
    reviewer_families = {
        review.get("review_a", {}).get("model_family"),
        review.get("review_b", {}).get("model_family"),
    } - {None}
    issues: list[str] = []
    adjudicator_ids: set[str] = set()
    adjudicator_families: set[str] = set()
    for row in decisions:
        identity = row.get("adjudicator")
        if isinstance(identity, Mapping):
            if isinstance(identity.get("adjudicator_id"), str):
                adjudicator_ids.add(identity["adjudicator_id"])
            if isinstance(identity.get("model_family"), str):
                adjudicator_families.add(identity["model_family"])
    if adjudicator_ids & reviewer_ids:
        issues.append("adjudicator_id must differ from reviewer IDs")
    if adjudicator_families & reviewer_families:
        issues.append("adjudicator model_family must differ from reviewer families")
    return issues


def _decision_validation(
    cases: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    try:
        finalize_adjudication(cases, decisions)
    except PacketError as exc:
        return False, [str(exc)]
    return True, []


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
    invalid_accepts = validate_accepted_decisions(cases, decisions, existing)
    decisions_valid, adjudication_issues = _decision_validation(cases, decisions)
    adjudication_issues.extend(_adjudicator_issues(decisions, review))
    accepted = sum(row.get("decision") == "accept" for row in decisions)
    excluded = sum(row.get("decision") == "exclude" for row in decisions)
    unresolved = sum(row.get("decision") == "unresolved" for row in decisions)
    ready = bool(
        review["ready"]
        and coverage_ok
        and not invalid_accepts
        and decisions_valid
        and not adjudication_issues,
    )
    return {
        "batch_id": metadata.get("batch_id", layout.root.name),
        "cases": len(cases),
        "reviews_ready": review["ready"],
        "adjudication_complete": coverage_ok,
        "accept": accepted,
        "exclude": excluded,
        "unresolved": unresolved,
        "invalid_accepts": invalid_accepts,
        "adjudication_issues": adjudication_issues,
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
    adjudication_issues = _adjudicator_issues(decisions, review)
    if adjudication_issues:
        raise ValueError(
            "adjudication independence failed: " + "; ".join(adjudication_issues)
        )
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
        target_lineage = Path(lineage_path) if lineage_path else layout.lineage
        previous = read_jsonl(target_lineage) if target_lineage.is_file() else []
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
            previous=previous,
        )
        conflicts = (
            read_jsonl(layout.source_conflicts)
            if layout.source_conflicts.is_file()
            else []
        )
        write_jsonl(layout.integration_exclusions, excluded)
        write_jsonl(layout.integration_retry, retry)
        write_jsonl(layout.integration_dir / "conflicts.jsonl", conflicts)
        write_review_evidence(target_lineage, entries)
        write_json(
            layout.integration_summary,
            {
                **result,
                "state": "integrated",
                "decision_sha256": _rows_digest(decisions),
                "case_sha256": _rows_digest(cases),
                "source_conflicts": len(conflicts),
            },
        )
        result["lineage"] = str(target_lineage)
        result["source_conflicts"] = conflicts
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
    "aggregate_candidates",
    "batch_preflight",
    "build_accepted_record",
    "check_reviews",
    "finalize_batch",
    "integrate_batch",
    "validate_accepted_decisions",
]
