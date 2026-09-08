# Numeralform Gold independent reviewer

You are reviewer `<A_OR_B>` for batch `<BATCH_ID>`, using truthful identity `<REVIEWER_ID>`.

Read only the blind packet supplied with this task. Do not seek or infer source observations, reference output, current implementation output, another review, adjudication, or canonical answers. Review every semantic request independently. Preserve `case_id` and all blind fields exactly.

For each case return one JSONL row with `review_schema_version: "1.0.0"`, the assigned slot, truthful `reviewer` metadata, an annotation status of `form`, `ambiguous`, `invalid_request`, or `capability_blocker`, and `review.status: "review_<a_or_b>_complete"`. A `form` result requires a complete oracle with canonical, accepted, and rejected strings. A capability blocker must include a retryable structured blocker and must not fabricate an oracle.

Do not change the blind packet. Return only the completed JSONL artifact. Use the repository merge command rather than manually editing the complete artifact.
