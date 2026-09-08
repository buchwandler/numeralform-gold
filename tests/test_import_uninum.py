from pathlib import Path

from numeralform_gold.importers import import_uninum
from numeralform_gold.validate import validate_records

FIXTURE = Path(__file__).parent / "fixtures/uninum"


def test_import_uninum_is_deterministic_and_maps_mvp_locales():
    first = import_uninum(FIXTURE)
    second = import_uninum(FIXTURE)
    assert first == second
    assert len(first) == 8
    assert {row["language"] for row in first} == {"de", "en"}
    assert {row["locale"] for row in first} == {"de-DE", "en-US"}
    forty_two = next(
        row
        for row in first
        if row["language"] == "en" and row["input"]["value"] == "42"
    )
    assert forty_two["oracle"]["canonical"] == "forty two"
    assert forty_two["grammar"] == {"context": "citation"}
    assert forty_two["quality"] == "gold"
    assert forty_two["source_observations"][0]["role"] == "gold_anchor"
    assert validate_records(first)["errors"] == 0


def test_import_uninum_can_filter_language():
    rows = import_uninum(FIXTURE, languages={"de"})
    assert len(rows) == 4
    assert {row["language"] for row in rows} == {"de"}
