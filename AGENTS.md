# Agent guidance

## Repository boundary

Treat this Git checkout as the durable source of truth for code, schemas,
source metadata, and reviewed canonical data only.

Do not write downloaded upstream repositories into this Git tree. Use
`config.toml` and the sibling `../numeralform-gold-source-cache` directory.
Do not write generated candidates, exhaustive ranges, comparison output or
review packets into this Git tree. Use `../numeralform-gold-work`.

## Gold policy

Do not use numeralform, num2words, ICU, CLDR, or another implementation as its
own ground truth.

Source classes:

- expert/human data can become Gold anchors when licensing/provenance is clear;
- rule/generated systems are reference oracles until independently reviewed;
- grammar-sensitive forms require explicit grammar features and evidence;
- citation forms must not be generalized to every case/gender context.

## Stable identity

A record ID is derived from the semantic test key, not from row order. The key
includes language/locale, numeric input, mode, and grammatical context. A source
observation has its own source identity.

## Development sequence

1. `python scripts/setup-source-cache.py`
2. `numeralform-gold status`
3. import source data into `<WORK>/candidates/`
4. validate before comparison or promotion
5. keep generated range data under `<WORK>/reference/`
6. add reviewed canonical rows to `data/corpus/<language>.jsonl` only through an
   explicit review/promotion change

## MVP limitation
The review workflow now provides independent A/B packets, adjudication, preflight, and explicit finalization. Do not bypass those gates by bulk-marking generated or imported data as Gold.
