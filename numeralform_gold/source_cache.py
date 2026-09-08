"""Verification helpers for external pinned source checkouts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _git_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise TypeError(f"invalid source manifest: {path}")
    return payload


def verify_source_cache(cache_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    items: list[dict[str, Any]] = []
    ok = True
    for source in manifest["sources"]:
        checkout = cache_root / source["name"]
        missing = [
            rel
            for rel in source.get("expected_paths", [])
            if not (checkout / rel).exists()
        ]
        head = _git_head(checkout)
        revision_ok = head == source.get("revision") if head else False
        exists = checkout.is_dir()
        source_ok = exists and not missing and revision_ok
        ok = ok and source_ok
        items.append(
            {
                "name": source["name"],
                "path": str(checkout),
                "exists": exists,
                "head": head,
                "expected_revision": source.get("revision"),
                "revision_ok": revision_ok,
                "missing_paths": missing,
                "ok": source_ok,
            }
        )
    return {"cache_root": str(cache_root), "ok": ok, "sources": items}
