#!/usr/bin/env python3
"""Create sibling source-cache and work directories from the pinned source lock."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

WORK_SUBDIRS = (
    "candidates",
    "reference",
    "reports",
    "comparisons",
    "reviews",
    "releases",
    "tmp",
)


def run(
    cmd: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run(("git", *args), cwd=cwd)


def ensure_git() -> None:
    if shutil.which("git") is None:
        raise SystemExit("git is required to set up the source cache")


def checkout_sparse(
    *,
    name: str,
    clone_url: str,
    revision: str,
    sparse_paths: list[str],
    cache_root: Path,
) -> Path:
    dest = cache_root / name
    if dest.exists() and not (dest / ".git").exists():
        raise SystemExit(f"refusing to overwrite non-git path: {dest}")
    if not dest.exists():
        dest.mkdir(parents=True)
        git("init", cwd=dest)
        git("remote", "add", "origin", clone_url, cwd=dest)
    else:
        current_remote = git("remote", "get-url", "origin", cwd=dest).stdout.strip()
        if current_remote != clone_url:
            raise SystemExit(
                f"{dest}: origin mismatch: expected {clone_url}, got {current_remote}"
            )

    # Sparse checkout prevents the CLDR cache from materializing unrelated data.
    git("sparse-checkout", "init", "--no-cone", cwd=dest)
    git("sparse-checkout", "set", *sparse_paths, cwd=dest)
    git("fetch", "--depth", "1", "origin", revision, cwd=dest)
    git("checkout", "--detach", "FETCH_HEAD", cwd=dest)
    head = git("rev-parse", "HEAD", cwd=dest).stdout.strip()
    if head != revision:
        raise SystemExit(f"{name}: expected {revision}, got {head}")
    return dest


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_cache = repo_root.parent / "numeralform-gold-source-cache"
    default_work = repo_root.parent / "numeralform-gold-work"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=default_cache)
    parser.add_argument("--work-root", type=Path, default=default_work)
    parser.add_argument("--skip-sources", action="store_true")
    parser.add_argument("--skip-work-dir", action="store_true")
    args = parser.parse_args(argv)

    cache_root = args.cache_root.expanduser().resolve(strict=False)
    work_root = args.work_root.expanduser().resolve(strict=False)
    lock_path = repo_root / "sources/source-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    if not args.skip_sources:
        ensure_git()
        cache_root.mkdir(parents=True, exist_ok=True)
        for name, source in lock["sources"].items():
            print(f"[{name}] {source['revision'][:12]}")
            checkout = checkout_sparse(
                name=name,
                clone_url=source["clone_url"],
                revision=source["revision"],
                sparse_paths=list(source.get("sparse_paths", [])),
                cache_root=cache_root,
            )
            print(f"  ready: {checkout}")

    if not args.skip_work_dir:
        work_root.mkdir(parents=True, exist_ok=True)
        for subdir in WORK_SUBDIRS:
            (work_root / subdir).mkdir(parents=True, exist_ok=True)
        print(f"work: {work_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
