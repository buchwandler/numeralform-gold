# Numeralform Gold batch handoff

A batch is operated through the high-level workflow surface:

1. Run `numeralform-gold next-task --batch <batch>` in the trusted context.
2. Open the generated bounded bundle in a fresh isolated reviewer or adjudicator context.
3. Save only the returned `result.jsonl` artifact.
4. Run `numeralform-gold task-import --assignment <assignment.json> --result <result.jsonl>` in the trusted context.
5. Run `next-task` again.

The reviewer or adjudicator must not inspect packet directories, source data, another review, canonical data, or repository paths. The trusted orchestrator owns assignment creation, result import, provenance checks, and merge operations. Review A and B require distinct orchestrator-controlled independence groups.
