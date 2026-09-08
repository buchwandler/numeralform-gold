"""Command-line interface for the Numeralform Gold MVP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
from .source_cache import verify_source_cache
from .validate import validate_records
from .work_layout import WorkLayout

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = REPO_ROOT / "sources" / "manifest.json"


def _runtime(args: argparse.Namespace):
    config_path = args.config if args.config is not None else default_config_path()
    config = load_config(config_path, explicit=args.config is not None)
    return resolve_runtime_paths(
        config=config,
        source_cache=args.source_cache,
        work_root=args.work_root,
    )


def _json_stdout(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _status(args: argparse.Namespace) -> int:
    paths = _runtime(args)
    payload = {
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


def _work_init(args: argparse.Namespace) -> int:
    paths = require_runtime_paths(_runtime(args))
    created = WorkLayout(paths.work_root).init()  # type: ignore[arg-type]
    _json_stdout(
        {
            "work_root": str(paths.work_root),
            "directories": [str(path) for path in created],
        }
    )
    return 0


def _verify_cache(args: argparse.Namespace) -> int:
    paths = require_runtime_paths(_runtime(args))
    report = verify_source_cache(
        paths.source_cache,
        SOURCE_MANIFEST,  # type: ignore[arg-type]
    )
    _json_stdout(report)
    return 0 if report["ok"] else 1


def _split_csv(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    result: set[str] = set()
    for value in values:
        result.update(item.strip() for item in value.split(",") if item.strip())
    return result or None


def _import_uninum(args: argparse.Namespace) -> int:
    paths = require_runtime_paths(_runtime(args))
    root = paths.source_cache / "uninum"  # type: ignore[operator]
    rows = import_uninum(
        root,
        codes=_split_csv(args.codes),
        languages=_split_csv(args.languages),
    )
    report = validate_records(rows)
    if report["errors"]:
        _json_stdout(report)
        return 1
    write_jsonl(args.out, rows)
    _json_stdout(
        {
            "source": "uninum",
            "records": len(rows),
            "languages": sorted({row["language"] for row in rows}),
            "locales": sorted({row["locale"] for row in rows if row["locale"]}),
            "out": str(args.out),
        }
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.path)
    report = validate_records(rows)
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
    summary = {key: value for key, value in report.items() if key != "details"}
    _json_stdout(summary)
    return 0 if report["mismatches"] == 0 and report["missing_predictions"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="numeralform-gold")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--source-cache", type=Path, default=None)
    parser.add_argument("--work-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser(
        "status", help="show resolved runtime paths and source status"
    )
    status.set_defaults(func=_status)

    work = sub.add_parser("work-init", help="create the disposable work-root layout")
    work.set_defaults(func=_work_init)

    verify = sub.add_parser(
        "source-cache-verify", help="verify pinned source checkouts"
    )
    verify.set_defaults(func=_verify_cache)

    uninum = sub.add_parser("import-uninum", help="import UniNum citation-form anchors")
    uninum.add_argument("--out", type=Path, required=True)
    uninum.add_argument(
        "--codes", action="append", help="UniNum code(s), comma-separated"
    )
    uninum.add_argument(
        "--languages", action="append", help="language code(s), comma-separated"
    )
    uninum.set_defaults(func=_import_uninum)

    validate = sub.add_parser("validate", help="validate a Numeralform Gold JSONL file")
    validate.add_argument("path", type=Path)
    validate.add_argument("--report", type=Path)
    validate.set_defaults(func=_validate)

    icu = sub.add_parser(
        "generate-icu-range",
        help="generate reference rows with the optional icu-rbnf runtime",
    )
    icu.add_argument("--locale", required=True)
    icu.add_argument("--start", type=int, default=0)
    icu.add_argument("--end", type=int, default=9999)
    icu.add_argument("--out", type=Path, required=True)
    icu.set_defaults(func=_generate_icu)

    compare = sub.add_parser(
        "compare", help="compare predictions against accepted forms"
    )
    compare.add_argument("--gold", type=Path, required=True)
    compare.add_argument("--predictions", type=Path, required=True)
    compare.add_argument(
        "--normalization", choices=("exact", "spacefold"), default="exact"
    )
    compare.add_argument("--report", type=Path)
    compare.set_defaults(func=_compare)
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
