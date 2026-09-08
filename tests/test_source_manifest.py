import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_manifest_and_lock_agree():
    manifest = json.loads((ROOT / "sources/manifest.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "sources/source-lock.json").read_text(encoding="utf-8"))
    manifest_by_name = {source["name"]: source for source in manifest["sources"]}
    assert set(manifest_by_name) == set(lock["sources"]) == {"uninum", "cldr"}
    for name, locked in lock["sources"].items():
        assert manifest_by_name[name]["revision"] == locked["revision"]
        assert manifest_by_name[name]["clone_url"] == locked["clone_url"]


def test_pinned_source_revisions_are_full_git_hashes():
    manifest = json.loads((ROOT / "sources/manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        revision = source["revision"]
        assert len(revision) == 40
        int(revision, 16)
