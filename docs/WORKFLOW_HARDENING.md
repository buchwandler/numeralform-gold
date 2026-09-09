# Review workflow hardening

The review session recorded in `01_todo.md` established that the historical B artifact for `uninum-mvp-002` is procedurally contaminated. It read current review A answers and a prior reviewer B oracle-generation script. That result must not be adjudicated or promoted.

Required remediation:

1. Preserve the historical B directory as forensic evidence.
2. Reset slot B with `numeralform-gold review-reset --batch uninum-mvp-002 --slot B --reason process_contamination --archive`.
3. Generate a fresh bounded B assignment with `next-task`.
4. Run it in a fresh isolated context using only the generated bundle.
5. Import the returned `result.jsonl` through `task-import`.
6. Require receipt and provenance checks before review readiness.

Reviewer and adjudicator contexts must not mount the checkout, source cache, work root, previous review artifacts, canonical corpus, or generated reference output. The trusted orchestrator owns assignment creation, identity binding, result import, merge, receipt creation, and finalization.

Rejected forms are optional, non-exhaustive evidence. Reviewers must not invent negative examples merely to populate the field.
