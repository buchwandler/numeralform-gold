# Isolated Numeralform Gold reviewer task

TASK_NOT_BOUND

This template is internal and is not a runnable reviewer assignment. If the generated assignment manifest and bounded input are absent, stop immediately with exactly `TASK_NOT_BOUND`. Do not inspect the repository, work root, source cache, previous reviews, canonical corpus, or any other filesystem path.

When a generated assignment is present, read only the embedded assignment manifest and input. Preserve every blind field and `case_id`. Review each request independently.

Return only `result.jsonl` with one completed row per assigned case. Do not run commands or perform file discovery. Do not invent rejected forms merely to populate the optional evidence list. A `form` result requires an oracle. A capability blocker requires a retryable structured blocker and no fabricated oracle.
