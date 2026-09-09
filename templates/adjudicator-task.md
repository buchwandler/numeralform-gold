# Isolated Numeralform Gold adjudicator task

TASK_NOT_BOUND

This template is internal and is not a runnable adjudicator assignment. If the generated assignment manifest and bounded input are absent, stop immediately with exactly `TASK_NOT_BOUND`. Do not inspect the repository, work root, source cache, previous reviews, canonical corpus, or any other filesystem path.

When a generated assignment is present, read only the embedded assignment manifest and bounded adjudication input. Preserve every `case_id` and use only the supplied evidence.
Return only `result.jsonl` with one decision per assigned case. Do not run commands or perform file discovery. Accept requires a complete final oracle. Unresolved requires a retryable structured blocker. Do not force consensus or invent evidence.
