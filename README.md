# numeralform-gold

`numeralform-gold` is a small, source-aware benchmark repository for validating
multilingual **number -> words** behavior, including languages where the output
changes with grammatical context.

The repository follows the same separation used by `spokenform-gold`:

```text
parent/
├── numeralform-gold/                 # Git repository: code, schemas, canonical Gold
├── numeralform-gold-source-cache/    # external pinned upstream checkouts
└── numeralform-gold-work/            # external disposable/generated work state
```

The default paths are committed in `config.toml`:

```toml
[paths]
source_cache = "../numeralform-gold-source-cache"
work = "../numeralform-gold-work"
```

## MVP goals

1. Import expert/citation-form anchors from Google UniNum.
2. Keep CLDR RBNF as an independent standards/reference source for exhaustive
   generated ranges such as `0..9999`.
3. Represent case, gender, grammatical number, animacy and future grammatical
   features explicitly instead of flattening them into locale strings.
4. Keep source data and generated work outside Git.
5. Compare `numeralform` predictions against accepted Gold forms without making
   `num2words` the ground truth.

## Quick start

```bash
git init  # optional when starting from the MVP zip
python -m pip install -e ".[dev]"
python scripts/setup-source-cache.py
numeralform-gold status
numeralform-gold import-uninum \
  --out ../numeralform-gold-work/candidates/uninum.jsonl
numeralform-gold validate ../numeralform-gold-work/candidates/uninum.jsonl
```

Optional ICU reference generation:

```bash
python -m pip install -e ".[icu]"
numeralform-gold generate-icu-range \
  --locale en_US --start 0 --end 9999 \
  --out ../numeralform-gold-work/reference/cldr-icu-en_US-0-9999.jsonl
```

`generate-icu-range` records the runtime ICU generator identity separately from
CLDR source provenance. It is a **reference oracle**, not automatically Gold.

## Pinned MVP sources

| Source            | MVP role                            | Pin                              | Stored in Git?  |
| ----------------- | ----------------------------------- | -------------------------------- | --------------- |
| Google UniNum     | expert/citation-form Gold anchors   | `b3d9064655a4…`                  | no              |
| Unicode CLDR RBNF | generated standards/reference basis | `release-48-2` / `11299982335b…` | no              |
| `icu-rbnf`        | optional runtime generator          | `0.1.0`                          | dependency only |

See `docs/RESEARCH_NOTES.md` for the web-researched source rationale and
`docs/SOURCES.md` for benchmark policy.

## Record layers

- `quality=gold`: human/expert-backed or independently reviewed forms.
- `quality=reference`: standards/rule-generated output useful for differential
  testing but not automatically accepted as canonical Gold.
- `quality=quarantine`: imported or proposed data awaiting review.

UniNum imports are `gold_anchor` observations but use `grammar.context="citation"`
because UniNum explicitly supplies citation forms when morphosyntax can change
pronunciation.

## Prediction comparison

Predictions are JSONL keyed by record ID:

```json
{ "id": "nfg-...", "output": "forty two" }
```

Then run:

```bash
numeralform-gold compare \
  --gold ../numeralform-gold-work/candidates/uninum.jsonl \
  --predictions predictions.jsonl \
  --report ../numeralform-gold-work/reports/numeralform-vs-uninum.json
```

By default comparison is exact after Unicode NFC normalization. Use
`--normalization spacefold` only for exploratory diagnostics; do not silently
weaken canonical Gold.

## What is intentionally not in the MVP

- no automatic promotion of CLDR-generated rows to Gold;
- no claim that UniNum validates inflected/case-sensitive forms;
- no complete review campaign/adjudication subsystem yet;
- no bundled upstream datasets;
- no coupling to `num2words`.

See `docs/ROADMAP.md` for the next implementation steps.
