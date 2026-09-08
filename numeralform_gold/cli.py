"""Command-line interface for the Numeralform Gold workflow."""

from __future__ import annotations

import argparse
import json
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
from .io import read_jsonl, write_json, write_jsonl
from .packets import (
    adjudication_packet_rows,
    finalize_adjudication,
    merge_adjudication_rows,
    merge_review_rows,
    review_packet_rows,
)
from .review import blind_review_case
from .source_cache import verify_source_cache
from .validate import validate_records
from .work_layout import BatchLayout, WorkLayout
from .workflow import batch_preflight, check_reviews, finalize_batch

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


def _batch_create(args: argparse.Namespace) -> int:
    work = _work(args)
    layout = work.batch(args.batch)
    if layout.metadata.exists():
        raise ValueError(f"batch already exists: {layout.root}")
    candidates = read_jsonl(args.candidates)
    candidates = sorted(candidates, key=lambda row: str(row.get("id", "")))[
        : args.limit
    ]
    layout.init()
    cases = [blind_review_case(candidate, "A") for candidate in candidates]
    source_rows = [
        {"case_id": case["case_id"], "observation": source}
        for case, candidate in zip(cases, candidates)
        for source in candidate.get("source_observations", [])
    ]
    write_jsonl(layout.cases, cases)
    write_jsonl(layout.source_observations, source_rows)
    write_json(
        layout.metadata,
        {
            "batch_id": args.batch,
            "state": "created",
            "candidate_count": len(cases),
            "candidate_snapshot_sha256": _rows_digest(candidates),
        },
    )
    _json_stdout(
        {"batch_id": args.batch, "batch": str(layout.root), "cases": len(cases)}
    )
    return 0


def _rows_digest(rows: Any) -> str:
    import hashlib

    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batch_status(args: argparse.Namespace) -> int:
    layout = _batch(args)
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
    next_role = (
        "review-a"
        if len(a) < len(cases)
        else "review-b"
        if len(b) < len(cases)
        else "adjudicator"
        if len(decisions) < len(cases)
        else "finalize"
    )
    _json_stdout(
        {
            "batch_id": layout.root.name,
            **counts,
            "next_role": next_role,
            "path": str(layout.root),
        }
    )
    return 0


def _review_packet(args: argparse.Namespace) -> int:
    layout = _batch(args)
    slot = args.slot.upper()
    blind = read_jsonl(layout.cases)
    complete_path = layout.review_complete(slot)
    complete = read_jsonl(complete_path) if complete_path.is_file() else []
    rows = review_packet_rows(
        blind,
        complete,
        max_cases=args.max_cases,
        max_bytes=args.max_bytes,
        language=args.language,
    )
    packet_number = len(list(layout.review_packet_dir(slot).glob("*.input.jsonl"))) + 1
    packet_path = layout.review_packet(slot, packet_number)
    write_jsonl(packet_path, rows)
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "slot": slot,
            "cases": len(rows),
            "packet": str(packet_path),
        }
    )
    return 0


def _review_merge(args: argparse.Namespace) -> int:
    layout = _batch(args)
    slot = args.slot.upper()
    blind = read_jsonl(layout.cases)
    existing_path = layout.review_complete(slot)
    existing = read_jsonl(existing_path) if existing_path.is_file() else []
    result = merge_review_rows(
        blind, existing, read_jsonl(args.packet_result), slot=slot, output=existing_path
    )
    write_json(
        layout.review_validation(slot),
        {"slot": slot, "rows": len(result), "complete": len(result)},
    )
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "slot": slot,
            "rows": len(result),
            "complete": str(existing_path),
        }
    )
    return 0


def _review_check(args: argparse.Namespace) -> int:
    layout = _batch(args)
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
    report = check_reviews(cases, a, b)
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
    report = check_reviews(cases, a, b)
    if not report["ready"]:
        raise ValueError("review-check failed: " + "; ".join(report["issues"]))
    source = (
        read_jsonl(layout.source_observations)
        if layout.source_observations.is_file()
        else []
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
    )
    packet_number = (
        len(list(layout.adjudication_dir.joinpath("packets").glob("*.input.jsonl"))) + 1
    )
    packet_path = layout.adjudication_packet(packet_number)
    write_jsonl(packet_path, rows)
    _json_stdout(
        {"batch_id": layout.root.name, "cases": len(rows), "packet": str(packet_path)}
    )
    return 0


def _adjudication_merge(args: argparse.Namespace) -> int:
    layout = _batch(args)
    partial = (
        read_jsonl(layout.adjudication_partial)
        if layout.adjudication_partial.is_file()
        else []
    )
    merged = merge_adjudication_rows(
        partial, read_jsonl(args.packet_result), output=layout.adjudication_partial
    )
    if args.finalize:
        finalize_adjudication(
            read_jsonl(layout.cases), merged, output=layout.adjudication_decisions
        )
    _json_stdout(
        {
            "batch_id": layout.root.name,
            "decisions": len(merged),
            "finalized": args.finalize,
        }
    )
    return 0


def _batch_preflight(args: argparse.Namespace) -> int:
    report = batch_preflight(_batch(args).root, args.corpus)
    _json_stdout(report)
    return 0 if report["ready_to_finalize"] else 1


def _batch_finalize(args: argparse.Namespace) -> int:
    report = finalize_batch(
        _batch(args).root, args.corpus, write=args.write, lineage_path=args.lineage
    )
    _json_stdout(report)
    return 0


def _agent_bundle(args: argparse.Namespace) -> int:
    layout = _batch(args)
    role = args.role.lower()
    if role in {"review-a", "review-b"}:
        slot = role[-1].upper()
        blind = read_jsonl(layout.cases)
        complete_path = layout.review_complete(slot)
        complete = read_jsonl(complete_path) if complete_path.is_file() else []
        rows = review_packet_rows(
            blind, complete, max_cases=args.max_cases, max_bytes=args.max_bytes
        )
        packet = layout.review_packet(
            slot, len(list(layout.review_packet_dir(slot).glob("*.input.jsonl"))) + 1
        )
        write_jsonl(packet, rows)
        template = REPO_ROOT / "templates" / "reviewer-ab-task.md"
    elif role == "adjudicator":
        cases = read_jsonl(layout.cases)
        a = read_jsonl(layout.review_complete("A"))
        b = read_jsonl(layout.review_complete("B"))
        if not check_reviews(cases, a, b)["ready"]:
            raise ValueError("review-check must be ready before adjudicator bundle")
        source = (
            read_jsonl(layout.source_observations)
            if layout.source_observations.is_file()
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
        )
        packet = layout.adjudication_packet(
            len(list(layout.adjudication_dir.joinpath("packets").glob("*.input.jsonl")))
            + 1
        )
        write_jsonl(packet, rows)
        template = REPO_ROOT / "templates" / "adjudicator-task.md"
    else:
        raise ValueError("role must be review-a, review-b, or adjudicator")
    body = (
        template.read_text(encoding="utf-8")
        + "\n\n## Assigned packet\n\n```jsonl\n"
        + packet.read_text(encoding="utf-8")
        + "```\n"
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
    batch_status = sub.add_parser("batch-status")
    batch_status.add_argument("--batch", required=True)
    batch_status.set_defaults(func=_batch_status)
    packet = sub.add_parser("review-packet")
    packet.add_argument("--batch", required=True)
    packet.add_argument("--slot", choices=("A", "B"), required=True)
    packet.add_argument("--max-cases", type=int, default=50)
    packet.add_argument("--max-bytes", type=int, default=65536)
    packet.add_argument("--language")
    packet.set_defaults(func=_review_packet)
    merge = sub.add_parser("review-merge")
    merge.add_argument("--batch", required=True)
    merge.add_argument("--slot", choices=("A", "B"), required=True)
    merge.add_argument("--packet-result", type=Path, required=True)
    merge.set_defaults(func=_review_merge)
    check = sub.add_parser("review-check")
    check.add_argument("--batch", required=True)
    check.set_defaults(func=_review_check)
    adj_packet = sub.add_parser("adjudication-packet")
    adj_packet.add_argument("--batch", required=True)
    adj_packet.add_argument("--max-cases", type=int, default=25)
    adj_packet.add_argument("--max-bytes", type=int, default=98304)
    adj_packet.set_defaults(func=_adjudication_packet)
    adj_merge = sub.add_parser("adjudication-merge")
    adj_merge.add_argument("--batch", required=True)
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
    bundle.set_defaults(func=_agent_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
