from numeralform_gold.compare import compare_predictions
from numeralform_gold.model import make_record


def gold():
    return make_record(
        language="en",
        locale="en-US",
        value="42",
        form="forty two",
        accepted=["forty two", "forty-two"],
        grammar={"context": "citation"},
        quality="gold",
        source_observations=[
            {"benchmark": "fixture", "role": "gold_anchor", "source_id": "42"}
        ],
    )


def test_compare_canonical_and_variant():
    row = gold()
    canonical = compare_predictions([row], [{"id": row["id"], "output": "forty two"}])
    assert canonical["canonical_matches"] == 1
    variant = compare_predictions([row], [{"id": row["id"], "output": "forty-two"}])
    assert variant["accepted_matches"] == 1
    assert variant["canonical_matches"] == 0


def test_compare_missing_and_mismatch():
    row = gold()
    missing = compare_predictions([row], [])
    assert missing["missing_predictions"] == 1
    mismatch = compare_predictions([row], [{"id": row["id"], "output": "42"}])
    assert mismatch["mismatches"] == 1
