"""Command-line interface for the Numeralform Gold workflow."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .compare import compare_predictions
from .config import (
    ConfigError,
    default_config_path,
    load_config,
    require_runtime_paths,
    resolve_runtime_paths,
)
from .icu_reference import generate_integer_range
from .importers import import_uninum
from .io import read_json, read_jsonl, write_json, write_jsonl
from .packets import (
    adjudication_packet_rows,
    build_review_receipt,
    case_ids_sha256,
    finalize_adjudication,
    merge_adjudication_rows,
    merge_review_rows,
    next_packet_number,
    review_packet_rows,
    sha256_file,
    validate_adjudication_packet,
    validate_review_packet,
)
from .review import blind_review_case, neutral_review_case, validate_review_rows
from .source_cache import verify_source_cache
from .validate import validate_records
from .work_layout import BatchLayout, WorkLayout
from .workflow import (
    aggregate_candidates,
    batch_preflight,
    batch_snapshot,
    check_reviews,
    finalize_batch,
    shard_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = REPO_ROOT / "sources" / "manifest.json"


def _runtime(args: argparse.Namespace):
    config_path = args.config if args.config is not None else default_config_path()
    config = load_config(config_path, explicit=args.config is not None)
    return resolve_runtime_paths(
        config=config, source_cache=args.source_cache, work_root=args.work_root
    )


def _json_stdout(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _split_csv(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    result: set[str] = set()
    for value in values:
        result.update(item.strip() for item in value.split(",") if item.strip())
    return result or None


def _work(args: argparse.Namespace) -> WorkLayout:
    paths = require_runtime_paths(_runtime(args))
    return WorkLayout(paths.work_root)  # type: ignore[arg-type]


def _batch(args: argparse.Namespace) -> BatchLayout:
    work = _work(args)
    value = Path(args.batch)
    if value.is_dir():
        return BatchLayout(value)
    return work.batch(value.name)


def _status(args: argparse.Namespace) -> int:
    paths = _runtime(args)
    payload: dict[str, Any] = {
        "source_cache": str(paths.source_cache) if paths.source_cache else None,
        "source_cache_exists": bool(paths.source_cache and paths.source_cache.is_dir()),
        "work_root": str(paths.work_root) if paths.work_root else None,
        "work_root_exists": bool(paths.work_root and paths.work_root.is_dir()),
    }
    if paths.source_cache:
        payload["source_cache_verification"] = verify_source_cache(
            paths.source_cache, SOURCE_MANIFEST
        )
    _json_stdout(payload)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    work = _work(args)
    batches = (
        sorted(path.name for path in work.batches_root.glob("*") if path.is_dir())
        if work.batches_root.is_dir()
        else []
    )
    _json_stdout(
        {
            "work_root": str(work.root),
            "initialized": work.root.is_dir(),
            "batches": batches,
            "lineage_parent": str(work.lineage.parent),
        }
    )
    return 0


def _work_init(args: argparse.Namespace) -> int:
    work = _work(args)
    _json_stdout(
        {
            "work_root": str(work.root),
            "directories": [str(path) for path in work.init()],
        }
    )
    return 0


def _verify_cache(args: argparse.Namespace) -> int:
    paths = require_runtime_paths(_runtime(args))
    report = verify_source_cache(paths.source_cache, SOURCE_MANIFEST)  # type: ignore[arg-type]
    _json_stdout(report)
    return 0 if report["ok"] else 1


def _import_uninum(args: argparse.Namespace) -> int:
    paths = require_runtime_paths(_runtime(args))
    rows = import_uninum(
        paths.source_cache / "uninum",
        codes=_split_csv(args.codes),
        languages=_split_csv(args.languages),
        quality="quarantine",
    )  # type: ignore[operator]
    report = validate_records(rows)
    if report["errors"]:
        _json_stdout(report)
        return 1
    write_jsonl(args.out, rows)
    _json_stdout(
        {
            "source": "uninum",
            "quality": "quarantine",
            "records": len(rows),
            "languages": sorted({row["language"] for row in rows}),
            "out": str(args.out),
        }
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    report = validate_records(read_jsonl(args.path))
    if args.report:
        write_json(args.report, report)
    _json_stdout(report)
    return 0 if report["errors"] == 0 else 1


def _generate_icu(args: argparse.Namespace) -> int:
    rows = generate_integer_range(args.locale, args.start, args.end)
    report = validate_records(rows)
    if report["errors"]:
        _json_stdout(report)
        return 1
    write_jsonl(args.out, rows)
    _json_stdout(
        {
            "source": "icu_rbnf_runtime",
            "quality": "reference",
            "locale": args.locale,
            "start": args.start,
            "end": args.end,
            "records": len(rows),
            "out": str(args.out),
        }
    )
    return 0


def _compare(args: argparse.Namespace) -> int:
    report = compare_predictions(
        read_jsonl(args.gold),
        read_jsonl(args.predictions),
        normalization=args.normalization,
    )
    if args.report:
        write_json(args.report, report)
    _json_stdout({key: value for key, value in report.items() if key != "details"})
    return 0 if report["mismatches"] == 0 and report["missing_predictions"] == 0 else 1


def _source_census(args: argparse.Namespace) -> int:
    paths = require_runtime_paths(_runtime(args))
    source_root = paths.source_cache / args.source  # type: ignore[operator]
    files = sorted(source_root.glob("**/*.tsv")) if source_root.is_dir() else []
    _json_stdout(
        {
            "source": args.source,
            "root": str(source_root),
            "files": len(files),
            "file_names": [str(path.relative_to(source_root)) for path in files],
        }
    )
    return 0


def _records_path(args: argparse.Namespace) -> Path:
    return args.records if args.records else REPO_ROOT / "data" / "corpus"


def _coverage(args: argparse.Namespace) -> int:
    path = _records_path(args)
    if path.is_dir():
        rows = [
            row for shard in sorted(path.glob("*.jsonl")) for row in read_jsonl(shard)
        ]
    elif path.is_file():
        rows = read_jsonl(path)
    else:
        rows = []
    dimensions = {
        "language": Counter(),
        "locale": Counter(),
        "mode": Counter(),
        "quality": Counter(),
        "input_kind": Counter(),
        "grammar_context": Counter(),
    }
    for row in rows:
        dimensions["language"][row.get("language")] += 1
        dimensions["locale"][row.get("locale") or "null"] += 1
        dimensions["mode"][row.get("mode")] += 1
        dimensions["quality"][row.get("quality")] += 1
        dimensions["input_kind"][
            row.get("input", {}).get("kind")
            if isinstance(row.get("input"), dict)
            else "invalid"
        ] += 1
        dimensions["grammar_context"][
            row.get("grammar", {}).get("context")
            if isinstance(row.get("grammar"), dict)
            else "invalid"
        ] += 1
    _json_stdout(
        {
            "records": len(rows),
            "path": str(path),
            "validation": validate_records(rows),
            "coverage": {
                key: dict(sorted(value.items(), key=lambda item: str(item[0])))
                for key, value in dimensions.items()
            },
        }
    )
    return 0


def _validate_batch_candidates(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        raise ValueError("batch requires at least one candidate")
    report = validate_records(candidates)
    if report["errors"]:
        raise ValueError(
            "invalid candidates: " + json.dumps(report["issues"], ensure_ascii=False)
        )
    ids = [row["id"] for row in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("batch candidates contain duplicate record IDs")
    case_ids = [neutral_review_case(row)["case_id"] for row in candidates]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("batch candidates contain duplicate semantic case IDs")
    if any(not row.get("source_observations") for row in candidates):
        raise ValueError("every candidate must contain source observations")


def _batch_create(args: argparse.Namespace) -> int:
    work = _work(args)
    layout = work.batch(args.batch)
    if layout.metadata.exists():
        raise ValueError(f"batch already exists: {layout.root}")
    raw_candidates = read_jsonl(args.candidates)
    _validate_batch_candidates(raw_candidates)
    aggregated, conflicts = aggregate_candidates(raw_candidates)
    candidates = aggregated[: args.limit]
    if args.limit <= 0:
        raise ValueError("batch limit must be positive")
    _validate_batch_candidates(candidates)
    layout.init()
    cases = [neutral_review_case(candidate) for candidate in candidates]
    review_a = [blind_review_case(candidate, "A") for candidate in candidates]
    review_b = [blind_review_case(candidate, "B") for candidate in candidates]
    source_rows = [
        {"case_id": case["case_id"], "observation": source}
        for case, candidate in zip(cases, candidates)
        for source in candidate.get("source_observations", [])
    ]
    evidence_rows = [
        {
            "case_id": case["case_id"],
            "candidate_id": candidate["id"],
            "candidate_quality": candidate.get("quality"),
            "observed_oracle": candidate.get("oracle"),
            "source_observations": candidate.get("source_observations", []),
            "conflict": any(
                item.get("record_id") == candidate.get("id") for item in conflicts
            ),
        }
        for case, candidate in zip(cases, candidates)
    ]
    write_jsonl(layout.cases, cases)
    write_jsonl(layout.review_blind("A"), review_a)
    write_jsonl(layout.review_blind("B"), review_b)
    write_jsonl(layout.source_candidates, candidates)
    write_jsonl(layout.source_observations, source_rows)
    write_jsonl(layout.source_evidence, evidence_rows)
    write_jsonl(layout.source_conflicts, conflicts)
    write_json(
        layout.metadata,
        {
            "batch_id": args.batch,
            "state": "created",
            "candidate_count": len(cases),
            "candidate_snapshot_sha256": _rows_digest(candidates),
            "aggregated_candidates": len(aggregated),
            "source_conflicts": len(conflicts),
            "snapshot_manifest": batch_snapshot(layout),
        },
    )
    _json_stdout(
        {
            "batch_id": args.batch,
            "batch": str(layout.root),
            "cases": len(cases),
            "source_conflicts": len(conflicts),
        }
    )
    return 0


def _campaign_shard(args: argparse.Namespace) -> int:
    work = _work(args)
    root = work.campaigns / args.campaign
    if root.exists():
        raise ValueError(f"campaign already exists: {root}")
    rows = read_jsonl(args.candidates)
    shards = shard_candidates(rows, max_cases=args.max_cases)
    if not shards:
        raise ValueError("campaign requires at least one candidate")
    root.mkdir(parents=True)
    files: list[str] = []
    for index, shard in enumerate(shards, start=1):
        path = root / f"shard-{index:04d}.jsonl"
        write_jsonl(path, shard["rows"])
        files.append(str(path))
    write_json(
        root / "campaign.json",
        {
            "campaign_id": args.campaign,
            "shards": len(shards),
            "max_cases": args.max_cases,
            "files": files,
            "snapshot_sha256": _rows_digest(shards),
        },
    )
    _json_stdout({"campaign_id": args.campaign, "shards": len(shards), "root": str(root)})
    return 0



def _rows_digest(rows: Any) -> str:
    import hashlib

    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_assignment_manifest(
    layout: BatchLayout,
    *,
    role: str,
    packet_path: Path,
    rows: list[dict[str, Any]],
    expected_identity: dict[str, Any],
    template: Path | None = None,
    packet_number: int,
) -> Path:
    assignment = (
        layout.review_assignment(role[-1].upper(), packet_number)
        if role in {"review-a", "review-b"}
        else layout.adjudication_assignment(packet_number)
    )
    assignment.mkdir(parents=True, exist_ok=True)
    packet_digest = sha256_file(packet_path)
    assignment_id = "nfgassign-" + packet_digest.split(":", 1)[1][:20]
    group = rows[0].get("case", rows[0]) if rows else {}
    if not isinstance(group, dict):
        group = {}
    manifest = {
        "schema_version": "1.0.0",
        "protocol_version": "numeral-review-v2",
        "assignment_id": assignment_id,
        "batch_id": layout.root.name,
        "role": role,
        "slot": role[-1].upper() if role in {"review-a", "review-b"} else "adjudicator",
        "packet": packet_path.name,
        "packet_sha256": packet_digest,
        "case_ids_sha256": case_ids_sha256(row["case_id"] for row in rows),
        "cases": len(rows),
        "group": {
            "language": group.get("language"),
            "locale": group.get("locale"),
            "family_id": group.get("family_id"),
        },
        "expected_reviewer": expected_identity,
        "task_template_sha256": sha256_file(template) if template else None,
    }
    write_json(assignment / "assignment.json", manifest)
    write_jsonl(assignment / "input.jsonl", rows)
    return assignment / "assignment.json"

def _verify_assignment_manifest(
    manifest_path: Path,
    *,
    batch_id: str,
    role: str,
    packet_path: Path,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise ValueError(f"assignment manifest is required: {manifest_path}")
    manifest = read_json(manifest_path)
    expected = {
        "batch_id": batch_id,
        "role": role,
        "packet": packet_path.name,
        "packet_sha256": sha256_file(packet_path),
        "case_ids_sha256": case_ids_sha256(row["case_id"] for row in rows),
        "cases": len(rows),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"assignment manifest mismatch for {key}")
    return manifest


def _verify_result_identity(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    key: str,
    id_key: str,
) -> None:
    expected = manifest.get("expected_reviewer", {})
    if not isinstance(expected, dict):
        raise TypeError("assignment expected identity is invalid")
    for row in rows:
        actual = row.get(key)
        if not isinstance(actual, dict):
            raise TypeError(f"result {key} identity is required")
        for field, value in expected.items():
            if value in (None, "UNBOUND", "YOUR_TRUTHFUL_ID", "YOUR_MODEL_FAMILY"):
                continue
            if actual.get(field) != value:
                raise ValueError(f"result identity mismatch for {field}")
        if id_key not in actual or not isinstance(actual[id_key], str):
            raise ValueError(f"result {key}.{id_key} is required")

def _assert_assignment_independence(
    layout: BatchLayout,
    role: str,
    independence_group: str | None,
) -> None:
    if not independence_group:
        return
    groups: set[str] = set()
    if role in {"review-a", "review-b"}:
        other = "B" if role.endswith("A") else "A"
        path = layout.review_complete(other)
        rows = read_jsonl(path) if path.is_file() else []
        for row in rows:
            reviewer = row.get("reviewer")
            if isinstance(reviewer, dict):
                groups.add(reviewer.get("independence_group") or reviewer.get("model_family"))
    else:
        for slot in ("A", "B"):
            path = layout.review_complete(slot)
            rows = read_jsonl(path) if path.is_file() else []
            for row in rows:
                reviewer = row.get("reviewer")
                if isinstance(reviewer, dict):
                    groups.add(reviewer.get("independence_group") or reviewer.get("model_family"))
    if independence_group in groups:
        raise ValueError("assignment independence_group conflicts with an existing role")

def _assignment_number(assignment: Path) -> int:
    try:
        return int(assignment.name)
    except ValueError as exc:
        raise ValueError(f"assignment directory must be numeric: {assignment}") from exc


def _render_bundle(
    manifest_path: Path,
    packet_path: Path,
    template: Path,
    output: Path,
) -> Path:
    manifest = read_json(manifest_path)
    body = (
        template.read_text(encoding="utf-8")
        + "\n\n## Generated assignment\n\n```json\n"
        + json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n\n## Bounded input\n\n```jsonl\n"
        + packet_path.read_text(encoding="utf-8")
        + "```\n\nReturn only: result.jsonl\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    return output


def _outstanding_assignment(layout: BatchLayout, role: str) -> Path | None:
    root = (
        layout.review_assignments(role[-1].upper())
        if role in {"review-a", "review-b"}
        else layout.adjudication_assignments
    )
    candidates = []
    for path in sorted(root.glob("*/assignment.json")):
        number = _assignment_number(path.parent)
        receipt = (
            layout.review_packet_receipt(role[-1].upper(), number)
            if role in {"review-a", "review-b"}
            else layout.adjudication_packet_receipt(number)
        )
        if not receipt.is_file():
            candidates.append(path)
    return candidates[-1] if candidates else None


def _create_review_assignment(
    layout: BatchLayout,
    slot: str,
    args: argparse.Namespace,
) -> Path | None:
    slot = slot.upper()
    blind = read_jsonl(layout.review_blind(slot))
    complete_path = layout.review_complete(slot)
    complete = read_jsonl(complete_path) if complete_path.is_file() else []
    rows = review_packet_rows(
        blind, complete, max_cases=args.max_cases, max_bytes=args.max_bytes
    )
    if not rows:
        return None
    number = next_packet_number(layout.review_packet_dir(slot))
    packet = layout.review_packet(slot, number)
    _assert_assignment_independence(
        layout, f"review-{slot.lower()}", getattr(args, "independence_group", None)
    )
    write_jsonl(packet, rows)
    manifest = _write_assignment_manifest(
        layout,
        role=f"review-{slot.lower()}",
        packet_path=packet,
        rows=rows,
        expected_identity={
            "reviewer_id": getattr(args, "reviewer_id", None) or "UNBOUND",
            "provider": getattr(args, "provider", None),
            "model": getattr(args, "model", None),
            "independence_group": getattr(args, "independence_group", None),
        },
        template=REPO_ROOT / "templates" / "reviewer-ab-task.md",
        packet_number=number,
    )
    return manifest

def _create_adjudication_assignment(
    layout: BatchLayout,
    args: argparse.Namespace,
) -> Path | None:
    cases = read_jsonl(layout.cases)
    review_a = read_jsonl(layout.review_complete("A"))
    review_b = read_jsonl(layout.review_complete("B"))
    if not check_reviews(cases, review_a, review_b, layout=layout)["ready"]:
        return None
    source = read_jsonl(layout.source_observations) if layout.source_observations.is_file() else []
    evidence = read_jsonl(layout.source_evidence) if layout.source_evidence.is_file() else []
    partial = (
        read_jsonl(layout.adjudication_partial)
        if layout.adjudication_partial.is_file()
        else []
    )
    rows = adjudication_packet_rows(
        cases, review_a, review_b, source, (), partial,
        max_cases=args.max_cases,
        max_bytes=args.max_bytes,
        source_evidence=evidence,
    )
    if not rows:
        return None
    number = next_packet_number(layout.adjudication_dir / "packets")
    packet = layout.adjudication_packet(number)
    _assert_assignment_independence(
        layout, "adjudicator", getattr(args, "independence_group", None)
    )
    write_jsonl(packet, rows)
    return _write_assignment_manifest(
        layout,
        role="adjudicator",
        packet_path=packet,
        rows=rows,
        expected_identity={
            "adjudicator_id": getattr(args, "adjudicator_id", None) or "UNBOUND",
            "provider": getattr(args, "provider", None),
            "model": getattr(args, "model", None),
            "independence_group": getattr(args, "independence_group", None),
        },
        template=REPO_ROOT / "templates" / "adjudicator-task.md",
        packet_number=number,
    )


def _next_task(args: argparse.Namespace) -> int:
    layout = _batch(args)
    available: list[dict[str, Any]] = []
    for role in ("review-a", "review-b"):
        manifest_path = _outstanding_assignment(layout, role)
        if manifest_path is None:
            manifest_path = _create_review_assignment(layout, role[-1], args)
        if manifest_path is not None:
            number = _assignment_number(manifest_path.parent)
            packet = layout.review_packet(role[-1].upper(), number)
            bundle = _render_bundle(
                manifest_path,
                packet,
                REPO_ROOT / "templates" / "reviewer-ab-task.md",
                manifest_path.parent / "bundle.md",
            )
            available.append(
                {
                    "role": role,
                    "assignment": str(manifest_path),
                    "bundle": str(bundle),
                }
            )
    if not available:
        manifest_path = _outstanding_assignment(layout, "adjudicator")
        if manifest_path is None:
            manifest_path = _create_adjudication_assignment(layout, args)
        if manifest_path is not None:
            number = _assignment_number(manifest_path.parent)
            packet = layout.adjudication_packet(number)
            bundle = _render_bundle(
                manifest_path,
                packet,
                REPO_ROOT / "templates" / "adjudicator-task.md",
                manifest_path.parent / "bundle.md",
            )
            available.append(
                {
                    "role": "adjudicator",
                    "assignment": str(manifest_path),
                    "bundle": str(bundle),
                }
            )
    _json_stdout({"available": available, "batch_id": layout.root.name})
    return 0

def _task_import(args: argparse.Namespace) -> int:
    manifest_path = Path(args.assignment).expanduser().resolve()
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise TypeError("assignment manifest must be an object")
    role = manifest.get("role")
    if role not in {"review-a", "review-b", "adjudicator"}:
        raise ValueError("assignment role is invalid")
    batch_root = manifest_path.parents[4]
    layout = BatchLayout(batch_root)
    number = _assignment_number(manifest_path.parent)
    if role in {"review-a", "review-b"}:
        slot = role[-1].upper()
        packet = layout.review_packet(slot, number)
        packet_rows = read_jsonl(packet)
        _verify_assignment_manifest(
            manifest_path,
            batch_id=layout.root.name,
            role=role,
            packet_path=packet,
            rows=packet_rows,
        )
        result_rows = read_jsonl(args.result)
        _verify_result_identity(result_rows, manifest, "reviewer", "reviewer_id")
        assignment_result = manifest_path.parent / "result.jsonl"
        write_jsonl(assignment_result, result_rows)
        packet_audit = validate_review_packet(
            packet_rows,
            result_rows,
            slot=slot,
            authoritative_rows=read_jsonl(layout.review_blind(slot)),
        )
        existing_path = layout.review_complete(slot)
        existing = read_jsonl(existing_path) if existing_path.is_file() else []
        merged = merge_review_rows(
            read_jsonl(layout.review_blind(slot)),
            existing,
            result_rows,
            slot=slot,
            output=existing_path,
            packet_rows=packet_rows,
        )
        receipt = build_review_receipt(
            packet, assignment_result, slot=slot, packet_audit=packet_audit
        )
        receipt["assignment_id"] = manifest["assignment_id"]
        receipt["result"] = str(assignment_result.relative_to(layout.root))
        write_json(layout.review_packet_receipt(slot, number), receipt)
        validation = validate_review_rows(merged, slot=slot)
        validation.pop("_indexed", None)
        write_json(layout.review_validation(slot), validation)
        _json_stdout({"role": role, "cases": len(result_rows), "complete": len(merged)})
        return 0
    packet = layout.adjudication_packet(number)
    packet_rows = read_jsonl(packet)
    _verify_assignment_manifest(
        manifest_path,
        batch_id=layout.root.name,
        role=role,
        packet_path=packet,
        rows=packet_rows,
    )
    result_rows = read_jsonl(args.result)
    _verify_result_identity(result_rows, manifest, "adjudicator", "adjudicator_id")
    packet_audit = validate_adjudication_packet(packet_rows, result_rows)
    partial_path = layout.adjudication_partial
    partial = read_jsonl(partial_path) if partial_path.is_file() else []
    merged = merge_adjudication_rows(
        partial, result_rows, packet_rows=packet_rows, output=partial_path
    )
    write_json(
        layout.adjudication_packet_receipt(number),
        {
            "schema_version": "1.0.0",
            "role": role,
            "assignment_id": manifest["assignment_id"],
            "packet": packet.name,
            "packet_sha256": sha256_file(packet),
            "result_sha256": sha256_file(Path(args.result)),
            "case_ids_sha256": case_ids_sha256(packet_audit["case_ids"]),
            "cases": packet_audit["cases"],
        },
    )
    _json_stdout({"role": role, "cases": len(result_rows), "decisions": len(merged)})
    return 0

def _task_abandon(args: argparse.Namespace) -> int:
    manifest_path = Path(args.assignment).expanduser().resolve()
    manifest = read_json(manifest_path)
    assignment_dir = manifest_path.parent
    layout = BatchLayout(manifest_path.parents[4])
    role = manifest.get("role")
    archive = (
        layout.review_archive(role[-1].upper())
        if role in {"review-a", "review-b"}
        else layout.adjudication_archive
    )
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{assignment_dir.name}-{args.reason}"
    suffix = 1
    while target.exists():
        target = archive / f"{assignment_dir.name}-{args.reason}-{suffix}"
        suffix += 1
    shutil.move(str(assignment_dir), str(target))
    _json_stdout({"abandoned": str(target), "reason": args.reason})
    return 0


def _review_reset(args: argparse.Namespace) -> int:
    layout = _batch(args)
    slot = args.slot.upper()
    if not args.archive:
        raise ValueError("review-reset requires --archive")
    archive = layout.review_archive(slot)
    archive.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while (archive / f"attempt-{attempt:04d}").exists():
        attempt += 1
    target = archive / f"attempt-{attempt:04d}"
    target.mkdir()
    paths = [
        layout.review_complete(slot),
        layout.review_validation(slot),
        layout.review_packet_dir(slot),
        layout.review_assignments(slot),
    ]
    moved: list[str] = []
    for path in paths:
        if path.exists():
            destination = target / path.name
            shutil.move(str(path), str(destination))
            moved.append(path.name)
    layout.review_packet_dir(slot).mkdir(parents=True, exist_ok=True)
    layout.review_assignments(slot).mkdir(parents=True, exist_ok=True)
    metadata = read_json(layout.metadata) if layout.metadata.is_file() else {}
    metadata = dict(metadata)
    resets = list(metadata.get("review_resets", []))
    resets.append({"slot": slot, "reason": args.reason, "attempt": attempt})
    metadata["review_resets"] = resets
    write_json(layout.metadata, metadata)
    _json_stdout({"slot": slot, "attempt": attempt, "archived": moved, "reason": args.reason})
    return 0
def _batch_status(args: argparse.Namespace) -> int:
    layout = _batch(args)
    metadata = read_json(layout.metadata) if layout.metadata.is_file() else {}
    cases = read_jsonl(layout.cases) if layout.cases.is_file() else []
    a = (
        read_jsonl(layout.review_complete("A"))
        if layout.review_complete("A").is_file()
        else []
    )
    b = (
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
    counts = {
        "cases": len(cases),
        "review_a": len(a),
        "review_b": len(b),
        "adjudicated": len(decisions),
        "accept": sum(row.get("decision") == "accept" for row in decisions),
        "exclude": sum(row.get("decision") == "exclude" for row in decisions),
        "unresolved": sum(row.get("decision") == "unresolved" for row in decisions),
    }
    review_report = check_reviews(
        cases, a, b,
        layout=layout if isinstance(metadata.get("snapshot_manifest"), dict) else None,
    )
    review_ready = review_report["ready"]
    review_issues = review_report["issues"]
    if len(a) < len(cases):
        next_role = "review-a"
    elif len(b) < len(cases):
        next_role = "review-b"
    elif not review_ready:
        next_role = "review-remediation"
    elif len(decisions) < len(cases):
        next_role = "adjudicator"
    else:
        next_role = "finalize"
    _json_stdout(
        {
            "batch_id": layout.root.name,
            **counts,
            "next_role": next_role,
            "review_ready": review_ready,
            "review_issues": review_issues,
            "path": str(layout.root),
        }
    )
    return 0


def _review_packet(args: argparse.Namespace) -> int:
    layout = _batch(args)
    slot = args.slot.upper()
    blind = read_jsonl(layout.review_blind(slot))
    complete_path = layout.review_complete(slot)
    complete = read_jsonl(complete_path) if complete_path.is_file() else []
    rows = review_packet_rows(
        blind,
        complete,
        max_cases=args.max_cases,
        max_bytes=args.max_bytes,
        language=args.language,
    )
    if not rows:
        _json_stdout(
            {
                "batch_id": layout.root.name,
                "slot": slot,
                "complete": True,
                "cases": 0,
                "packet": None,
            }
        )
        return 0
    packet_number = next_packet_number(layout.review_packet_dir(slot))
    packet_path = layout.review_packet(slot, packet_number)
    if packet_path.exists():
        raise ValueError(f"refusing to overwrite packet: {packet_path}")
    write_jsonl(packet_path, rows)
    _assert_assignment_independence(
        layout, f"review-{slot.lower()}", getattr(args, "independence_group", None)
    )
    _write_assignment_manifest(
        layout,
        role=f"review-{slot.lower()}",
        packet_path=packet_path,
        rows=rows,
        expected_identity={
            "reviewer_id": getattr(args, "reviewer_id", None) or "UNBOUND",
            "provider": getattr(args, "provider", None),
            "model": getattr(args, "model", None),
            "independence_group": getattr(args, "independence_group", None),
        },
        template=REPO_ROOT / "templates" / "reviewer-ab-task.md",
        packet_number=packet_number,
    )
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "slot": slot,
            "complete": False,
            "cases": len(rows),
            "packet": str(packet_path),
        }
    )
    return 0


def _review_merge(args: argparse.Namespace) -> int:
    layout = _batch(args)
    slot = args.slot.upper()
    blind = read_jsonl(layout.review_blind(slot))
    existing_path = layout.review_complete(slot)
    existing = read_jsonl(existing_path) if existing_path.is_file() else []
    packet_path = Path(args.packet).expanduser().resolve()
    packet_dir = layout.review_packet_dir(slot).resolve()
    try:
        packet_path.relative_to(packet_dir)
    except ValueError as exc:
        raise ValueError(
            "assigned packet must be under the batch review packet directory"
        ) from exc
    packet_name = packet_path.name
    if not packet_name.endswith(".input.jsonl"):
        raise ValueError("assigned packet must be an .input.jsonl file")
    try:
        packet_number = int(packet_name.split(".", 1)[0])
    except ValueError as exc:
        raise ValueError("assigned packet must use a numeric packet name") from exc
    if packet_path != layout.review_packet(slot, packet_number).resolve():
        raise ValueError("assigned packet path does not match its slot")
    packet_rows = read_jsonl(packet_path)
    assignment_manifest = _verify_assignment_manifest(
        layout.review_assignment_manifest(slot, packet_number),
        batch_id=layout.root.name,
        role=f"review-{slot.lower()}",
        packet_path=packet_path,
        rows=packet_rows,
    )
    result_path = Path(args.packet_result).expanduser().resolve()
    result_rows = read_jsonl(result_path)
    _verify_result_identity(result_rows, assignment_manifest, "reviewer", "reviewer_id")
    packet_audit = validate_review_packet(
        packet_rows, result_rows, slot=slot, authoritative_rows=blind
    )
    result = merge_review_rows(
        blind,
        existing,
        result_rows,
        slot=slot,
        output=existing_path,
        packet_rows=packet_rows,
    )
    assignment_result = layout.review_assignment(slot, packet_number) / "result.jsonl"
    write_jsonl(assignment_result, result_rows)
    receipt = build_review_receipt(
        packet_path, assignment_result, slot=slot, packet_audit=packet_audit
    )
    receipt["assignment_id"] = assignment_manifest["assignment_id"]
    receipt["result"] = str(assignment_result.relative_to(layout.root))
    receipt_path = layout.review_packet_receipt(slot, packet_number)
    write_json(receipt_path, receipt)
    validation = validate_review_rows(result, slot=slot)
    validation.pop("_indexed", None)
    write_json(layout.review_validation(slot), validation)
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "slot": slot,
            "rows": len(result),
            "complete": str(existing_path),
            "receipt": str(receipt_path),
            "validation": validation,
        }
    )
    return 0


def _review_check(args: argparse.Namespace) -> int:
    layout = _batch(args)
    metadata = read_json(layout.metadata) if layout.metadata.is_file() else {}
    cases = read_jsonl(layout.cases)
    a = (
        read_jsonl(layout.review_complete("A"))
        if layout.review_complete("A").is_file()
        else []
    )
    b = (
        read_jsonl(layout.review_complete("B"))
        if layout.review_complete("B").is_file()
        else []
    )
    report = check_reviews(
        cases, a, b,
        layout=layout if isinstance(metadata.get("snapshot_manifest"), dict) else None,
    )
    report["ready"] = not report["issues"]
    write_json(layout.review_check, report)
    _json_stdout(report)
    return 0 if report["ready"] else 1


def _adjudication_packet(args: argparse.Namespace) -> int:
    layout = _batch(args)
    cases = read_jsonl(layout.cases)
    a, b = (
        read_jsonl(layout.review_complete("A")),
        read_jsonl(layout.review_complete("B")),
    )
    report = check_reviews(cases, a, b, layout=layout)
    if not report["ready"]:
        raise ValueError("review-check failed: " + "; ".join(report["issues"]))
    source = (
        read_jsonl(layout.source_observations)
        if layout.source_observations.is_file()
        else []
    )
    evidence = (
        read_jsonl(layout.source_evidence) if layout.source_evidence.is_file() else []
    )
    decisions = (
        read_jsonl(layout.adjudication_partial)
        if layout.adjudication_partial.is_file()
        else []
    )
    rows = adjudication_packet_rows(
        cases,
        a,
        b,
        source,
        (),
        decisions,
        max_cases=args.max_cases,
        max_bytes=args.max_bytes,
        source_evidence=evidence,
    )
    if not rows:
        _json_stdout(
            {
                "batch_id": layout.root.name,
                "complete": True,
                "cases": 0,
                "packet": None,
            }
        )
        return 0
    packet_number = next_packet_number(layout.adjudication_dir / "packets")
    packet_path = layout.adjudication_packet(packet_number)
    if packet_path.exists():
        raise ValueError(f"refusing to overwrite packet: {packet_path}")
    write_jsonl(packet_path, rows)
    _assert_assignment_independence(
        layout, "adjudicator", getattr(args, "independence_group", None)
    )
    _write_assignment_manifest(
        layout,
        role="adjudicator",
        packet_path=packet_path,
        rows=rows,
        expected_identity={
            "adjudicator_id": getattr(args, "adjudicator_id", None) or "UNBOUND",
            "provider": getattr(args, "provider", None),
            "model": getattr(args, "model", None),
            "independence_group": getattr(args, "independence_group", None),
        },
        template=REPO_ROOT / "templates" / "adjudicator-task.md",
        packet_number=packet_number,
    )
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "complete": False,
            "cases": len(rows),
            "packet": str(packet_path),
        }
    )
    return 0


def _adjudication_merge(args: argparse.Namespace) -> int:
    layout = _batch(args)
    packet_path = Path(args.packet).expanduser().resolve()
    packet_dir = (layout.adjudication_dir / "packets").resolve()
    try:
        packet_path.relative_to(packet_dir)
    except ValueError as exc:
        raise ValueError(
            "assigned adjudication packet must be under the batch packet directory"
        ) from exc
    if not packet_path.name.endswith(".input.jsonl") or packet_path.parent != packet_dir:
        raise ValueError("assigned adjudication packet must be an input packet")
    packet_rows = read_jsonl(packet_path)
    try:
        packet_number = int(packet_path.name.split(".", 1)[0])
    except ValueError as exc:
        raise ValueError("assigned adjudication packet must use a numeric name") from exc
    if packet_path != layout.adjudication_packet(packet_number).resolve():
        raise ValueError("assigned adjudication packet path does not match its batch")
    assignment_manifest = _verify_assignment_manifest(
        layout.adjudication_assignment_manifest(packet_number),
        batch_id=layout.root.name,
        role="adjudicator",
        packet_path=packet_path,
        rows=packet_rows,
    )
    result_rows = read_jsonl(args.packet_result)
    _verify_result_identity(
        result_rows, assignment_manifest, "adjudicator", "adjudicator_id"
    )
    packet_audit = validate_adjudication_packet(packet_rows, result_rows)
    partial = (
        read_jsonl(layout.adjudication_partial)
        if layout.adjudication_partial.is_file()
        else []
    )
    merged = merge_adjudication_rows(
        partial, result_rows, packet_rows=packet_rows, output=layout.adjudication_partial
    )
    write_json(
        layout.adjudication_packet_receipt(packet_number),
        {
            "schema_version": "1.0.0",
            "role": "adjudicator",
            "assignment_id": assignment_manifest["assignment_id"],
            "packet": packet_path.name,
            "packet_sha256": sha256_file(packet_path),
            "result_sha256": sha256_file(Path(args.packet_result)),
            "case_ids_sha256": case_ids_sha256(packet_audit["case_ids"]),
            "cases": packet_audit["cases"],
        },
    )
    if args.finalize:
        finalize_adjudication(
            read_jsonl(layout.cases), merged, output=layout.adjudication_decisions
        )
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "decisions": len(merged),
            "packet_cases": packet_audit["cases"],
            "finalized": args.finalize,
        }
    )
    return 0


def _batch_preflight(args: argparse.Namespace) -> int:
    report = batch_preflight(_batch(args).root, args.corpus)
    _json_stdout(report)
    return 0 if report["ready_to_finalize"] else 1


def _batch_finalize(args: argparse.Namespace) -> int:
    lineage = args.lineage or REPO_ROOT / "data" / "lineage" / "review-evidence.jsonl"
    report = finalize_batch(
        _batch(args).root, args.corpus, write=args.write, lineage_path=lineage
    )
    _json_stdout(report)
    return 0


def _agent_bundle(args: argparse.Namespace) -> int:
    layout = _batch(args)
    role = args.role.lower()
    if role in {"review-a", "review-b"}:
        slot = role[-1].upper()
        blind = read_jsonl(layout.review_blind(slot))
        complete_path = layout.review_complete(slot)
        complete = read_jsonl(complete_path) if complete_path.is_file() else []
        rows = review_packet_rows(
            blind, complete, max_cases=args.max_cases, max_bytes=args.max_bytes
        )
        if not rows:
            raise ValueError(f"no assignment available: slot {slot} is complete")
        packet = layout.review_packet(
            slot, next_packet_number(layout.review_packet_dir(slot))
        )
        if packet.exists():
            raise ValueError(f"refusing to overwrite packet: {packet}")
        write_jsonl(packet, rows)
        template = REPO_ROOT / "templates" / "reviewer-ab-task.md"
    elif role == "adjudicator":
        cases = read_jsonl(layout.cases)
        a = read_jsonl(layout.review_complete("A"))
        b = read_jsonl(layout.review_complete("B"))
        if not check_reviews(cases, a, b, layout=layout)["ready"]:
            raise ValueError("review-check must be ready before adjudicator bundle")
        source = (
            read_jsonl(layout.source_observations)
            if layout.source_observations.is_file()
            else []
        )
        evidence = (
            read_jsonl(layout.source_evidence)
            if layout.source_evidence.is_file()
            else []
        )
        partial = (
            read_jsonl(layout.adjudication_partial)
            if layout.adjudication_partial.is_file()
            else []
        )
        rows = adjudication_packet_rows(
            cases,
            a,
            b,
            source,
            (),
            partial,
            max_cases=args.max_cases,
            max_bytes=args.max_bytes,
            source_evidence=evidence,
        )
        if not rows:
            raise ValueError("no assignment available: adjudication is complete")
        packet = layout.adjudication_packet(
            next_packet_number(layout.adjudication_dir / "packets")
        )
        if packet.exists():
            raise ValueError(f"refusing to overwrite packet: {packet}")
        write_jsonl(packet, rows)
        template = REPO_ROOT / "templates" / "adjudicator-task.md"
    else:
        raise ValueError("role must be review-a, review-b, or adjudicator")
    template_text = template.read_text(encoding="utf-8")
    identity = args.reviewer_id or args.adjudicator_id or "YOUR_TRUTHFUL_ID"
    model_family = args.model_family or "YOUR_MODEL_FAMILY"
    _assert_assignment_independence(
        layout,
        role,
        getattr(args, "independence_group", None),
    )
    manifest_path = _write_assignment_manifest(
        layout,
        role=role,
        packet_path=packet,
        rows=rows,
        expected_identity={
            ("reviewer_id" if role.startswith("review-") else "adjudicator_id"): identity,
            "model_family": model_family,
            "provider": getattr(args, "provider", None),
            "model": getattr(args, "model", None),
            "independence_group": getattr(args, "independence_group", None),
        },
        template=template,
        packet_number=int(packet.stem.split(".", 1)[0]),
    )
    template_text = (
        template_text.replace(
            "<A_OR_B>", role[-1].upper() if role.startswith("review-") else role
        )
        .replace("<BATCH_ID>", layout.root.name)
        .replace("<REVIEWER_ID>", identity)
        .replace("<ADJUDICATOR_ID>", identity)
    )
    body = (
        template_text
        + "\n\n## Generated assignment\n\n```json\n"
        + json.dumps(read_json(manifest_path), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n\n## Bounded input\n\n```jsonl\n"
        + packet.read_text(encoding="utf-8")
        + "```\n\nReturn only: result.jsonl\n"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(body, encoding="utf-8")
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "role": role,
            "cases": len(rows),
            "packet": str(packet),
            "bundle": str(args.out),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="numeralform-gold")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--source-cache", type=Path, default=None)
    parser.add_argument("--work-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func, help_text in (
        ("status", _status, "show runtime paths"),
        ("doctor", _doctor, "show workflow health"),
        ("work-status", _doctor, "show workflow status"),
        ("work-init", _work_init, "create work layout"),
        ("source-cache-verify", _verify_cache, "verify pinned source checkouts"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.set_defaults(func=func)
    next_task = sub.add_parser("next-task", help="create or resume the next isolated assignment")
    next_task.add_argument("--batch", required=True)
    next_task.add_argument("--max-cases", type=int, default=50)
    next_task.add_argument("--max-bytes", type=int, default=65536)
    next_task.add_argument("--reviewer-id")
    next_task.add_argument("--adjudicator-id")
    next_task.add_argument("--provider")
    next_task.add_argument("--model")
    next_task.add_argument("--independence-group")
    next_task.set_defaults(func=_next_task)
    task_import = sub.add_parser("task-import", help="import a bounded assignment result")
    task_import.add_argument("--assignment", type=Path, required=True)
    task_import.add_argument("--result", type=Path, required=True)
    task_import.set_defaults(func=_task_import)
    abandon = sub.add_parser("task-abandon", help="archive an outstanding assignment")
    abandon.add_argument("--assignment", type=Path, required=True)
    abandon.add_argument("--reason", required=True)
    abandon.set_defaults(func=_task_abandon)
    reset = sub.add_parser("review-reset", help="archive a contaminated review attempt")
    reset.add_argument("--batch", required=True)
    reset.add_argument("--slot", choices=("A", "B"), required=True)
    reset.add_argument("--reason", required=True)
    reset.add_argument("--archive", action="store_true")
    reset.set_defaults(func=_review_reset)
    uninum = sub.add_parser("import-uninum", help="import UniNum quarantine candidates")
    uninum.add_argument("--out", type=Path, required=True)
    uninum.add_argument("--codes", action="append")
    uninum.add_argument("--languages", action="append")
    uninum.set_defaults(func=_import_uninum)
    validate = sub.add_parser("validate")
    validate.add_argument("path", type=Path)
    validate.add_argument("--report", type=Path)
    validate.set_defaults(func=_validate)
    icu = sub.add_parser("generate-icu-range")
    icu.add_argument("--locale", required=True)
    icu.add_argument("--start", type=int, default=0)
    icu.add_argument("--end", type=int, default=9999)
    icu.add_argument("--out", type=Path, required=True)
    icu.set_defaults(func=_generate_icu)
    compare = sub.add_parser("compare")
    compare.add_argument("--gold", type=Path, required=True)
    compare.add_argument("--predictions", type=Path, required=True)
    compare.add_argument(
        "--normalization", choices=("exact", "spacefold"), default="exact"
    )
    compare.add_argument("--report", type=Path)
    compare.set_defaults(func=_compare)
    census = sub.add_parser("source-census")
    census.add_argument("--source", default="uninum")
    census.set_defaults(func=_source_census)
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--records", type=Path)
    coverage.set_defaults(func=_coverage)
    create = sub.add_parser("batch-create")
    create.add_argument("--batch", required=True)
    create.add_argument("--candidates", type=Path, required=True)
    create.add_argument("--limit", type=int, default=500)
    create.set_defaults(func=_batch_create)
    campaign = sub.add_parser("campaign-shard", help="split candidates into homogeneous review shards")
    campaign.add_argument("--campaign", required=True)
    campaign.add_argument("--candidates", type=Path, required=True)
    campaign.add_argument("--max-cases", type=int, default=100)
    campaign.set_defaults(func=_campaign_shard)
    batch_status = sub.add_parser("batch-status")
    batch_status.add_argument("--batch", required=True)
    batch_status.set_defaults(func=_batch_status)
    packet = sub.add_parser("review-packet")
    packet.add_argument("--batch", required=True)
    packet.add_argument("--slot", choices=("A", "B"), required=True)
    packet.add_argument("--max-cases", type=int, default=50)
    packet.add_argument("--max-bytes", type=int, default=65536)
    packet.add_argument("--language")
    packet.add_argument("--reviewer-id")
    packet.add_argument("--provider")
    packet.add_argument("--model")
    packet.add_argument("--independence-group")
    packet.set_defaults(func=_review_packet)
    merge = sub.add_parser("review-merge")
    merge.add_argument("--batch", required=True)
    merge.add_argument("--slot", choices=("A", "B"), required=True)
    merge.add_argument("--packet-result", type=Path, required=True)
    merge.add_argument("--packet", type=Path, required=True)
    merge.set_defaults(func=_review_merge)
    check = sub.add_parser("review-check")
    check.add_argument("--batch", required=True)
    check.set_defaults(func=_review_check)
    adj_packet = sub.add_parser("adjudication-packet")
    adj_packet.add_argument("--batch", required=True)
    adj_packet.add_argument("--max-cases", type=int, default=25)
    adj_packet.add_argument("--max-bytes", type=int, default=98304)
    adj_packet.add_argument("--adjudicator-id")
    adj_packet.add_argument("--provider")
    adj_packet.add_argument("--model")
    adj_packet.add_argument("--independence-group")
    adj_packet.set_defaults(func=_adjudication_packet)
    adj_merge = sub.add_parser("adjudication-merge")
    adj_merge.add_argument("--batch", required=True)
    adj_merge.add_argument("--packet", type=Path, required=True)
    adj_merge.add_argument("--packet-result", type=Path, required=True)
    adj_merge.add_argument("--finalize", action="store_true")
    adj_merge.set_defaults(func=_adjudication_merge)
    preflight = sub.add_parser("batch-preflight")
    preflight.add_argument("--batch", required=True)
    preflight.add_argument("--corpus", type=Path, required=True)
    preflight.set_defaults(func=_batch_preflight)
    finalize = sub.add_parser("batch-finalize")
    finalize.add_argument("--batch", required=True)
    finalize.add_argument("--corpus", type=Path, required=True)
    finalize.add_argument("--lineage", type=Path)
    finalize.add_argument("--write", action="store_true")
    finalize.set_defaults(func=_batch_finalize)
    bundle = sub.add_parser("agent-bundle")
    bundle.add_argument("--batch", required=True)
    bundle.add_argument(
        "--role", choices=("review-a", "review-b", "adjudicator"), required=True
    )
    bundle.add_argument("--out", type=Path, required=True)
    bundle.add_argument("--max-cases", type=int, default=50)
    bundle.add_argument("--max-bytes", type=int, default=65536)
    bundle.add_argument("--reviewer-id")
    bundle.add_argument("--adjudicator-id")
    bundle.add_argument("--model-family")
    bundle.add_argument("--provider")
    bundle.add_argument("--model")
    bundle.add_argument("--independence-group")
    bundle.set_defaults(func=_agent_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, TypeError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
