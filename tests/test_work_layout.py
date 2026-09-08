from pathlib import Path

from numeralform_gold.work_layout import WORK_SUBDIRS, WorkLayout


def test_work_layout_creates_stage_owned_directories(tmp_path: Path):
    root = tmp_path / "numeralform-gold-work"
    paths = WorkLayout(root).init()
    assert {path.name for path in paths} == set(WORK_SUBDIRS)
    assert all(path.is_dir() for path in paths)
    assert WorkLayout(root).candidates == root / "candidates"
    assert WorkLayout(root).reference == root / "reference"
