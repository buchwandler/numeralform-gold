import json
from pathlib import Path

from numeralform_gold.cli import main
from numeralform_gold.io import read_json, read_jsonl, write_json, write_jsonl
from numeralform_gold.model import make_record
from numeralform_gold.packets import adjudication_packet_rows, select_packet_rows
from numeralform_gold.review import blind_review_case, neutral_review_case
from numeralform_gold.review_anomaly import build_review_anomaly_report
from numeralform_gold.work_layout import BatchLayout, WorkLayout
from numeralform_gold.workflow import (
    aggregate_candidates,
    batch_preflight,
    check_reviews,
)


def candidate(value: str, form: str | None = None, language: str = "en"):
    return make_record(
        language=language,
        locale=f"{language}-US" if language == "en" else f"{language}-DE",
        value=value,
        form=form or f"word-{value}",
        quality="quarantine",
        grammar={"context": "citation"},
        source_observations=[
            {
                "benchmark": "fixture",
                "role": "gold_anchor",
                "source_id": f"{language}:{value}",
            }
        ],
    )


def completed(row, slot, reviewer_id, family):
    result = dict(row)
    result["reviewer_slot"] = slot
    result["reviewer"] = {
        "reviewer_id": reviewer_id,
        "kind": "llm",
        "model_family": family,
        "protocol_version": "numeral-review-v1",
    }
    result["annotation"] = {
        "status": "form",
        "oracle": {
            "canonical": f"word-{row['input']['value']}",
            "accepted": [f"word-{row['input']['value']}"],
            "rejected": [],
        },
        "grammar_assessment": {
            "request_is_well_formed": True,
            "features_supported": True,
        },
    }
    result["review"] = {"status": f"review_{slot.lower()}_complete"}
    return result


def decision(row):
    form = f"word-{row['input']['value']}"
    return {
        "case_id": row["case_id"],
        "adjudicator": {"adjudicator_id": "adj", "model_family": "family-c"},
        "decision": "accept",
        "final_oracle": {"canonical": form, "accepted": [form], "rejected": []},
        "rationale": "independent source and review agreement",
        "evidence_used": ["source_evidence", "review_a", "review_b"],
    }


def test_candidate_validation_rejects_duplicates_before_batch_creation(tmp_path: Path):
    work = tmp_path / "work"
    candidates = tmp_path / "candidates.jsonl"
    row = candidate("42")
    write_jsonl(candidates, [row, row])
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-create",
                "--batch",
                "bad",
                "--candidates",
                str(candidates),
            ]
        )
        == 2
    )
    assert not (work / "batches" / "bad").exists()


def test_uniform_form_status_is_informational():
    rows = [
        completed(blind_review_case(candidate(str(i)), "A"), "A", "a", "family-a")
        for i in range(10)
    ]
    report = build_review_anomaly_report(rows, slot="A")
    assert "uniform_annotation_status" in {item["code"] for item in report["signals"]}
    assert not report["blocking_signals"]
    assert report["ready"]


def test_repeated_oracle_and_decimal_echo_are_blocking():
    repeated = []
    for i in range(10):
        row = completed(blind_review_case(candidate(str(i)), "A"), "A", "a", "family-a")
        row["annotation"]["oracle"] = {
            "canonical": "same",
            "accepted": ["same"],
            "rejected": [],
        }
        repeated.append(row)
    repeated_report = build_review_anomaly_report(repeated)
    assert "repeated_oracle_object" in {
        item["code"] for item in repeated_report["blocking_signals"]
    }

    echoed = []
    for i in range(10):
        row = blind_review_case(candidate(str(i)), "A")
        row["annotation"] = {
            "status": "form",
            "oracle": {"canonical": str(i), "accepted": [str(i)], "rejected": []},
            "grammar_assessment": {},
        }
        echoed.append(row)
    echoed_report = build_review_anomaly_report(echoed)
    assert "canonical_equals_decimal_input" in {
        item["code"] for item in echoed_report["blocking_signals"]
    }


def test_multilingual_completed_reviews_are_checked_per_language():
    records = [
        candidate(str(index), language="en" if index < 6 else "de")
        for index in range(12)
    ]
    cases = [neutral_review_case(record) for record in records]
    review_a = [
        completed(blind_review_case(record, "A"), "A", "review-a", "family-a")
        for record in records
    ]
    review_b = [
        completed(blind_review_case(record, "B"), "B", "review-b", "family-b")
        for record in records
    ]

    report = check_reviews(cases, review_a, review_b)

    assert report["ready"]
    assert report["issues"] == []
    assert not report["anomalies"]["A"]["blocking_signals"]
    assert not report["anomalies"]["B"]["blocking_signals"]
    assert "mixed_language_review_artifact" in {
        item["code"] for item in report["anomalies"]["A"]["signals"]
    }


def test_packet_scope_and_grouped_blockers_remain_blocking():
    mixed_rows = [
        completed(
            blind_review_case(
                candidate(str(index), language="en" if index < 5 else "de"), "A"
            ),
            "A",
            "review-a",
            "family-a",
        )
        for index in range(10)
    ]
    mixed_report = build_review_anomaly_report(mixed_rows, slot="A")
    assert "mixed_language_packet" in {
        item["code"] for item in mixed_report["blocking_signals"]
    }

    records = [
        candidate(str(index), language="en" if index < 10 else "de")
        for index in range(12)
    ]
    cases = [neutral_review_case(record) for record in records]
    review_a = [
        completed(blind_review_case(record, "A"), "A", "review-a", "family-a")
        for record in records
    ]
    review_b = [
        completed(blind_review_case(record, "B"), "B", "review-b", "family-b")
        for record in records
    ]
    for row in review_a[:10] + review_b[:10]:
        row["annotation"]["oracle"] = {
            "canonical": "same",
            "accepted": ["same"],
            "rejected": [],
        }
    report = check_reviews(cases, review_a, review_b)
    assert any(
        signal["code"] == "repeated_oracle_object" and signal["language"] == "en"
        for signal in report["anomalies"]["A"]["blocking_signals"]
    )

    for row in review_a[:10] + review_b[:10]:
        value = row["input"]["value"]
        row["annotation"]["oracle"] = {
            "canonical": value,
            "accepted": [value],
            "rejected": [],
        }
    report = check_reviews(cases, review_a, review_b)
    assert any(
        signal["code"] == "canonical_equals_decimal_input"
        and signal["language"] == "en"
        for signal in report["anomalies"]["A"]["blocking_signals"]
    )


def test_slot_b_projection_and_adjudication_boundary():
    record = candidate("42")
    case = neutral_review_case(record)
    a = blind_review_case(record, "A")
    b = blind_review_case(record, "B")
    assert case["case_id"] == a["case_id"] == b["case_id"]
    assert a["reviewer_slot"] == "A"
    assert b["reviewer_slot"] == "B"
    adjudication = adjudication_packet_rows(
        [case],
        [completed(a, "A", "a", "family-a")],
        [completed(b, "B", "b", "family-b")],
        source_evidence=[
            {
                "case_id": case["case_id"],
                "observed_oracle": record["oracle"],
                "source_observations": record["source_observations"],
            }
        ],
    )
    assert adjudication[0]["source_evidence"][0]["observed_oracle"] == record["oracle"]
    assert "source_evidence" not in a
    assert "source_observations" not in a


def test_existing_source_observations_are_preserved(tmp_path: Path):
    batch = BatchLayout(tmp_path / "batch")
    batch.init()
    record = candidate("42")
    case = neutral_review_case(record)
    write_jsonl(batch.cases, [case])
    write_jsonl(
        batch.source_observations,
        [{"case_id": case["case_id"], "observation": record["source_observations"][0]}],
    )
    write_jsonl(batch.review_blind("A"), [blind_review_case(record, "A")])
    write_jsonl(batch.review_blind("B"), [blind_review_case(record, "B")])
    write_jsonl(
        batch.review_complete("A"),
        [completed(blind_review_case(record, "A"), "A", "a", "family-a")],
    )
    write_jsonl(
        batch.review_complete("B"),
        [completed(blind_review_case(record, "B"), "B", "b", "family-b")],
    )
    write_jsonl(batch.adjudication_decisions, [decision(case)])
    from numeralform_gold.workflow import finalize_batch

    corpus = tmp_path / "corpus"
    lineage = tmp_path / "lineage.jsonl"
    finalize_batch(batch.root, corpus, lineage_path=lineage, write=True)
    first = read_jsonl(corpus / "en.jsonl")[0]
    assert first["source_observations"] == record["source_observations"]


def test_batch_status_respects_review_readiness_gate(tmp_path, capsys):
    work = tmp_path / "work"
    candidates = tmp_path / "candidates.jsonl"
    rows = [candidate(str(index)) for index in range(10)]
    write_jsonl(candidates, rows)
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-create",
                "--batch",
                "status",
                "--candidates",
                str(candidates),
            ]
        )
        == 0
    )
    capsys.readouterr()
    layout = WorkLayout(work).batch("status")
    cases = read_jsonl(layout.cases)
    review_a = [
        completed(row, "A", "review-a", "family-a")
        for row in read_jsonl(layout.review_blind("A"))
    ]
    review_b = [
        completed(row, "B", "review-b", "family-b")
        for row in read_jsonl(layout.review_blind("B"))
    ]
    for row in review_a + review_b:
        row["annotation"]["oracle"] = {
            "canonical": "same",
            "accepted": ["same"],
            "rejected": [],
        }
    write_jsonl(layout.review_complete("A"), review_a)
    write_jsonl(layout.review_complete("B"), review_b)

    assert main(["--work-root", str(work), "batch-status", "--batch", "status"]) == 0
    blocked = json.loads(capsys.readouterr().out)
    assert not blocked["review_ready"]
    assert blocked["next_role"] == "review-remediation"
    assert "review A: anomaly requires fresh review" in blocked["review_issues"]

    review_a = [completed(row, "A", "review-a", "family-a") for row in cases]
    review_b = [completed(row, "B", "review-b", "family-b") for row in cases]
    write_jsonl(layout.review_complete("A"), review_a)
    write_jsonl(layout.review_complete("B"), review_b)
    assert main(["--work-root", str(work), "batch-status", "--batch", "status"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert not ready["review_ready"]
    assert ready["next_role"] == "review-remediation"
    assert any("receipts" in issue for issue in ready["review_issues"])


def test_true_cli_workflow_uses_slot_artifacts_and_finalizes(tmp_path: Path):
    work = tmp_path / "work"
    candidates = tmp_path / "candidates.jsonl"
    rows = [candidate(str(i)) for i in range(2)]
    write_jsonl(candidates, rows)
    args = [
        "--work-root",
        str(work),
        "batch-create",
        "--batch",
        "pilot",
        "--candidates",
        str(candidates),
    ]
    assert main(args) == 0
    layout = WorkLayout(work).batch("pilot")
    assert all(
        row["reviewer_slot"] == "B" for row in read_jsonl(layout.review_blind("B"))
    )

    for slot, reviewer_id, family in (
        ("A", "review-a", "family-a"),
        ("B", "review-b", "family-b"),
    ):
        assert (
            main(
                [
                    "--work-root",
                    str(work),
                    "review-packet",
                    "--batch",
                    "pilot",
                    "--slot",
                    slot,
                ]
            )
            == 0
        )
        packet = layout.review_packet(slot, 1)
        completed_rows = [
            completed(row, slot, reviewer_id, family) for row in read_jsonl(packet)
        ]
        result = tmp_path / f"{slot}.result.jsonl"
        write_jsonl(result, completed_rows)
        assert (
            main(
                [
                    "--work-root",
                    str(work),
                    "review-merge",
                    "--batch",
                    "pilot",
                    "--slot",
                    slot,
                    "--packet",
                    str(packet),
                    "--packet-result",
                    str(result),
                ]
            )
            == 0
        )
        receipt = layout.review_packet_receipt(slot, 1)
        assert read_json(receipt)["packet"] == packet.name
        assert read_json(receipt)["anomaly_ready"]

    assert main(["--work-root", str(work), "review-check", "--batch", "pilot"]) == 0
    assert (
        main(["--work-root", str(work), "adjudication-packet", "--batch", "pilot"]) == 0
    )
    adj_packet = layout.adjudication_packet(1)
    decisions = [decision(row["case"]) for row in read_jsonl(adj_packet)]
    adj_result = tmp_path / "adjudication.result.jsonl"
    write_jsonl(adj_result, decisions)
    assert (
        main(
            [
                "--work-root",
                str(work),
                "adjudication-merge",
                "--batch",
                "pilot",
                "--packet",
                str(adj_packet),
                "--packet-result",
                str(adj_result),
                "--finalize",
            ]
        )
        == 0
    )
    corpus = tmp_path / "corpus"
    lineage = tmp_path / "lineage.jsonl"
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-preflight",
                "--batch",
                "pilot",
                "--corpus",
                str(corpus),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-finalize",
                "--batch",
                "pilot",
                "--corpus",
                str(corpus),
                "--lineage",
                str(lineage),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-finalize",
                "--batch",
                "pilot",
                "--corpus",
                str(corpus),
                "--lineage",
                str(lineage),
                "--write",
            ]
        )
        == 0
    )
    assert len(read_jsonl(corpus / "en.jsonl")) == 2
    assert len(read_jsonl(lineage)) == 2


def test_work_layout_lineage_is_repository_controlled(tmp_path: Path):
    assert (
        WorkLayout(tmp_path / "external-work").lineage
        == Path(__file__).parents[1] / "data" / "lineage" / "review-evidence.jsonl"
    )


def test_aggregate_candidates_preserves_independent_observations_and_conflicts():
    first = candidate("42", "forty-two")
    second = candidate("42", "quarante-deux")
    second["source_observations"][0]["source_id"] = "other:42"
    merged, conflicts = aggregate_candidates([first, second])
    assert len(merged) == 1
    assert len(merged[0]["source_observations"]) == 2
    assert conflicts


def test_preflight_rejects_malformed_unresolved_decision(tmp_path: Path):
    batch = BatchLayout(tmp_path / "batch")
    batch.init()
    record = candidate("42")
    case = neutral_review_case(record)
    write_jsonl(batch.cases, [case])
    write_jsonl(
        batch.review_complete("A"),
        [completed(blind_review_case(record, "A"), "A", "a", "family-a")],
    )
    write_jsonl(
        batch.review_complete("B"),
        [completed(blind_review_case(record, "B"), "B", "b", "family-b")],
    )
    malformed = decision(case)
    malformed["decision"] = "unresolved"
    malformed.pop("final_oracle")
    write_jsonl(batch.adjudication_decisions, [malformed])
    report = batch_preflight(batch.root, tmp_path / "corpus")
    assert not report["ready_to_finalize"]
    assert report["adjudication_issues"]


def test_second_batch_appends_lineage_revision_two(tmp_path: Path):
    from numeralform_gold.workflow import finalize_batch

    corpus = tmp_path / "corpus"
    lineage = tmp_path / "lineage.jsonl"
    for index in (1, 2):
        batch = BatchLayout(tmp_path / f"batch-{index}")
        batch.init()
        record = candidate("42")
        case = neutral_review_case(record)
        write_json(batch.metadata, {"batch_id": f"batch-{index}"})
        write_jsonl(batch.cases, [case])
        write_jsonl(
            batch.source_observations,
            [
                {
                    "case_id": case["case_id"],
                    "observation": record["source_observations"][0],
                }
            ],
        )
        write_jsonl(
            batch.review_complete("A"),
            [completed(blind_review_case(record, "A"), "A", "a", "family-a")],
        )
        write_jsonl(
            batch.review_complete("B"),
            [completed(blind_review_case(record, "B"), "B", "b", "family-b")],
        )
        write_jsonl(batch.adjudication_decisions, [decision(case)])
        finalize_batch(batch.root, corpus, lineage_path=lineage, write=True)
    revisions = [row["review_revision"] for row in read_jsonl(lineage)]
    assert revisions == [1, 2]


def test_agent_bundle_resolves_role_and_batch_placeholders(tmp_path: Path):
    work = tmp_path / "work"
    candidates = tmp_path / "candidates.jsonl"
    write_jsonl(candidates, [candidate("42")])
    assert (
        main(
            [
                "--work-root",
                str(work),
                "batch-create",
                "--batch",
                "bundle",
                "--candidates",
                str(candidates),
            ]
        )
        == 0
    )
    output = tmp_path / "bundle.md"
    assert (
        main(
            [
                "--work-root",
                str(work),
                "agent-bundle",
                "--batch",
                "bundle",
                "--role",
                "review-b",
                "--out",
                str(output),
                "--reviewer-id",
                "review-b",
                "--model-family",
                "family-b",
            ]
        )
        == 0
    )
    text = output.read_text(encoding="utf-8")
    assert "<A_OR_B>" not in text
    assert "<BATCH_ID>" not in text
    assert "<REVIEWER_ID>" not in text
    assert "result.jsonl" in text
    assert "merge" not in text

def test_packet_language_selection_groups_before_limits():
    rows = [
        {"case_id": "a", "language": "en"},
        {"case_id": "b", "language": "de"},
        {"case_id": "c", "language": "en"},
    ]
    selected = select_packet_rows(rows, max_cases=2, max_bytes=1000)
    assert {row["language"] for row in selected} == {"en"}


def test_schema_contracts_are_json_and_slot_aware():
    completed_schema = json.loads(
        Path("schemas/completed-review.schema.json").read_text()
    )
    assert completed_schema["properties"]["reviewer_slot"]["enum"] == ["A", "B"]
    statuses = str(completed_schema["allOf"])
    assert "review_a_complete" in statuses and "review_b_complete" in statuses
    adjudication_schema = json.loads(
        Path("schemas/adjudication.schema.json").read_text()
    )
    assert adjudication_schema["properties"]["decision"]["enum"] == [
        "accept",
        "exclude",
        "unresolved",
    ]
