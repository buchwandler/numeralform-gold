from numeralform_gold.model import make_record
from numeralform_gold.validate import validate_record, validate_records


def record():
    return make_record(
        language="en",
        locale="en-US",
        value="42",
        form="forty two",
        grammar={"context": "citation"},
        quality="gold",
        source_observations=[
            {"benchmark": "fixture", "role": "gold_anchor", "source_id": "42"}
        ],
    )


def test_valid_record_passes():
    assert validate_record(record()) == []


def test_canonical_must_be_accepted():
    row = record()
    row["oracle"]["accepted"] = ["other"]
    errors = validate_record(row)
    assert any("canonical" in error and "accepted" in error for error in errors)


def test_semantic_id_change_is_detected():
    row = record()
    row["grammar"]["case"] = "genitive"
    errors = validate_record(row)
    assert any("id mismatch" in error for error in errors)


def test_duplicate_ids_are_detected():
    row = record()
    report = validate_records([row, row])
    assert report["errors"] == 1
    assert report["issues"][0]["row"] == 2
