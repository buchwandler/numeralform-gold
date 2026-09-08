from pathlib import Path

import pytest

from numeralform_gold.io import read_jsonl, write_json, write_jsonl
from numeralform_gold.model import make_record
from numeralform_gold.packets import PacketError, merge_review_rows, review_packet_rows
from numeralform_gold.review import (
    blind_review_case,
    hidden_field_paths,
    review_preflight,
)
from numeralform_gold.review_anomaly import build_review_anomaly_report
from numeralform_gold.review_lineage import (
    build_review_evidence,
    validate_review_evidence,
    write_review_evidence,
)
from numeralform_gold.work_layout import BatchLayout
from numeralform_gold.workflow import finalize_batch


def candidate(value="42"):
    return make_record(
        language="en",
        locale="en-US",
        value=value,
        form="forty-two",
        quality="quarantine",
        grammar={"context": "citation"},
        source_observations=[
            {"benchmark": "uninum", "role": "gold_anchor", "source_id": f"eng:{value}"}
        ],
    )


def completed(case, slot, reviewer_id, model_family):
    row = dict(case)
    row["reviewer_slot"] = slot
    row["reviewer"] = {
        "reviewer_id": reviewer_id,
        "kind": "llm",
        "model_family": model_family,
        "protocol_version": "numeral-review-v1",
    }
    row["annotation"] = {
        "status": "form",
        "oracle": {"canonical": "forty-two", "accepted": ["forty-two"], "rejected": []},
        "grammar_assessment": {
            "request_is_well_formed": True,
            "features_supported": True,
        },
    }
    row["review"] = {"status": f"review_{slot.lower()}_complete"}
    return row


def decision(case_id):
    return {
        "case_id": case_id,
        "adjudicator": {"adjudicator_id": "adj", "model_family": "family-c"},
        "decision": "accept",
        "final_oracle": {
            "canonical": "forty-two",
            "accepted": ["forty-two"],
            "rejected": [],
        },
        "rationale": "source-backed",
        "evidence_used": ["review_a", "review_b"],
    }


def test_review_packets_are_bounded_isolated_and_resumable():
    cases = [blind_review_case(candidate(str(value)), "A") for value in (42, 43, 44)]
    packet = review_packet_rows(cases, max_cases=2, max_bytes=65536)
    assert len(packet) == 2
    assert not hidden_field_paths(packet)
    assert (
        len(review_packet_rows(cases, packet[:1], max_cases=50, max_bytes=65536)) == 2
    )
    assert all("source_observations" not in row for row in packet)


def test_review_gate_requires_distinct_reviewers_and_families():
    case = blind_review_case(candidate(), "A")
    a = completed(case, "A", "review-a", "family-a")
    b = completed(case, "B", "review-b", "family-b")
    assert review_preflight([case], [a], [b])["ready"]
    assert not review_preflight(
        [case], [a], [completed(case, "B", "review-a", "family-a")]
    )["ready"]


def test_merge_rejects_mutated_blind_fields_and_is_idempotent(tmp_path: Path):
    case = blind_review_case(candidate(), "A")
    row = completed(case, "A", "review-a", "family-a")
    output = tmp_path / "complete.jsonl"
    assert merge_review_rows([case], [], [row], slot="A", output=output) == [row]
    assert merge_review_rows([case], [row], [row], slot="A", output=output) == [row]
    mutated = dict(row)
    mutated["input"] = {"kind": "integer", "value": "99"}
    with pytest.raises(PacketError):
        merge_review_rows([case], [], [mutated], slot="A")


def test_anomaly_and_lineage_are_deterministic(tmp_path: Path):
    cases = [blind_review_case(candidate(str(value)), "A") for value in range(10, 20)]
    rows = [completed(case, "A", "review-a", "family-a") for case in cases]
    anomaly = build_review_anomaly_report(rows, substantial_packet_size=10)
    assert anomaly["fresh_review_required"]
    records = [candidate("10")]
    one_case = cases[0]
    a, b = rows[0], completed(one_case, "B", "review-b", "family-b")
    entries = build_review_evidence(
        [one_case],
        [a],
        [b],
        [{"case_id": one_case["case_id"], "classification": "exact_agreement"}],
        [decision(one_case["case_id"])],
        records=records,
        batch_id="batch-1",
    )
    assert not validate_review_evidence(entries)
    path = tmp_path / "evidence.jsonl"
    write_review_evidence(path, entries)
    write_review_evidence(path, entries)
    assert len(read_jsonl(path)) == 1


def test_batch_finalization_writes_only_accepted_records(tmp_path: Path):
    batch = BatchLayout(tmp_path / "batch")
    batch.init()
    record = candidate()
    case = blind_review_case(record, "A")
    write_jsonl(batch.cases, [case])
    write_jsonl(
        batch.source_observations,
        [{"case_id": case["case_id"], "observation": record["source_observations"][0]}],
    )
    write_json(batch.metadata, {"batch_id": "batch"})
    a, b = (
        completed(case, "A", "review-a", "family-a"),
        completed(case, "B", "review-b", "family-b"),
    )
    write_jsonl(batch.review_complete("A"), [a])
    write_jsonl(batch.review_complete("B"), [b])
    write_jsonl(batch.adjudication_decisions, [decision(case["case_id"])])
    corpus, lineage = tmp_path / "corpus", tmp_path / "lineage.jsonl"
    dry = finalize_batch(batch.root, corpus, lineage_path=lineage)
    assert dry["state"] == "ready_to_finalize"
    assert not corpus.exists()
    done = finalize_batch(batch.root, corpus, lineage_path=lineage, write=True)
    assert done["state"] == "finalized"
    assert read_jsonl(corpus / "en.jsonl")[0]["quality"] == "gold"
    assert len(read_jsonl(lineage)) == 1
