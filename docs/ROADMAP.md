# Roadmap

## MVP (included)

- sibling source-cache/work layout
- pinned UniNum + CLDR source metadata
- source cache setup/verification
- UniNum importer
- grammar-aware record schema
- deterministic IDs
- validation
- ICU range generator (optional dependency)
- prediction comparison/reporting
- tests and CI

## P1: corpus construction

- locale mapping registry separate from importer code
- source census and coverage reports
- semantic-key aggregation of independent observations
- explicit source conflict artifacts
- reviewed promotion command
- canonical language shards under `data/corpus/`
- independent A/B review, adjudication, preflight, and durable lineage

## P2: grammar Gold

- grammatical feature registry per language
- CLDR ruleset census for case/gender/cardinal variants
- evaluate `unicode-rbnf` ruleset exposure as a non-Gold discovery/reference adapter
- case/gender/number paradigms
- contextual source importers (Polish/Russian/etc.)
- grammar-sensitive A/B review follows the same source-evidence boundary
- negative/rejected forms for high-value morphology regressions

## P3: exhaustive validation

- reproducible CLDR/ICU generator identity and version locking
- generated `0..9999` reference suites per supported locale
- numeralform adapter that emits prediction JSONL
- coverage dashboards by language, mode and grammar feature
- differential reports against multiple independent sources

## Release rule

Generated output is not promoted to Gold merely because two software libraries
agree. Promotion requires independent data or review evidence.
