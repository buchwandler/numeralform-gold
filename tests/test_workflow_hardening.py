import json
from pathlib import Path

import pytest

from numeralform_gold.cli import main
from numeralform_gold.io import write_jsonl
from numeralform_gold.model import make_record
from numeralform_gold.packets import (
    PacketError,
    select_packet_rows,
    validate_review_packet,
)
from numeralform_gold.review import blind_review_case, validate_review_rows
from numeralform_gold.workflow import shard_candidates


def candidate(value: str = "42", locale: str = "en-US"):
    return make_record(
        language="en",
        locale=locale,
        value=value,
        form="forty-two",
        mode="cardinal",
        grammar={"context": "citation"},
        quality="quarantine",
        source_observations=[
            {"benchmark": "fixture", "source_id": value, "role": "gold_anchor"}
        ],
    )


def completed(case, slot="A"):
    row = dict(case)
    row.update(
        {
            "reviewer_slot": slot,
            "reviewer": {
                "reviewer_id": f"review-{slot.lower()}",
                "kind": "llm",
                "model_family": f"family-{slot.lower()}",
                "protocol_version": "numeral-review-v1",
            },
            "annotation": {
                "status": "form",
                "oracle": {"canonical": "forty-two", "accepted": ["forty-two"]},
                "grammar_assessment": {
                    "request_is_well_formed": True,
                    "features_supported": True,
                },
            },
            "review": {"status": f"review_{slot.lower()}_complete"},
        }
    )
    return row


def test_packet_selection_advances_after_completed_first_group():
    rows = [
        {"case_id": "a", "language": "en"},
        {"case_id": "b", "language": "de"},
        {"case_id": "c", "language": "en"},
    ]
    selected = select_packet_rows(
        rows, completed_ids={"a", "c"}, max_cases=2, max_bytes=1000
    )
    assert [row["case_id"] for row in selected] == ["b"]


def test_packet_rejects_unknown_and_tampered_projection():
    case = blind_review_case(candidate(), "A")
    result = completed(case)
    with pytest.raises(PacketError, match="unknown fields"):
        validate_review_packet(
            [{**case, "hint": "forty-two"}], [result], slot="A"
        )
    with pytest.raises(PacketError, match="authoritative blind projection"):
        validate_review_packet(
            [{**case, "input": {"kind": "integer", "value": "43"}}],
            [result],
            slot="A",
            authoritative_rows=[case],
        )


def test_runtime_review_contract_requires_full_reviewer_metadata():
    case = blind_review_case(candidate(), "A")
    row = completed(case)
    del row["reviewer"]["kind"]
    report = validate_review_rows([row], slot="A")
    assert not report["ready"]
    assert any("reviewer.kind" in issue["message"] for issue in report["issues"])


def test_next_task_is_idempotent_and_bundle_is_sanitized(tmp_path: Path, capsys):
    candidates = tmp_path / "candidates.jsonl"
    write_jsonl(candidates, [candidate()])
    work = tmp_path / "work"
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-create",
                "--batch",
                "idempotent",
                "--candidates",
                str(candidates),
            ]
        )
        == 0
    )
    capsys.readouterr()
    command = ["--work-root", str(work), "next-task", "--batch", "idempotent"]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second
    bundle = Path(first["available"][0]["bundle"]).read_text()
    assert "TASK_NOT_BOUND" in bundle
    assert "source_observations" not in bundle
    assert "review-merge" not in bundle


def test_campaign_shards_are_homogeneous():
    rows = [candidate(str(i), locale="en-US" if i < 2 else "en-GB") for i in range(4)]
    shards = shard_candidates(rows, max_cases=1)
    assert len(shards) == 4
    assert all(len(shard["rows"]) == 1 for shard in shards)
    assert {shard["group"]["locale"] for shard in shards} == {"en-US", "en-GB"}
