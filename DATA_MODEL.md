# Data model

The stable interchange unit is one JSON object per line.

A record represents one semantic number-to-words assertion:

```json
{
  "schema_version": "1.0.0",
  "id": "nfg-en-...",
  "language": "en",
  "locale": "en-US",
  "input": { "kind": "integer", "value": "42" },
  "mode": "cardinal",
  "grammar": { "context": "citation" },
  "oracle": {
    "canonical": "forty two",
    "accepted": ["forty two"],
    "rejected": []
  },
  "quality": "gold",
  "source_observations": [
    {
      "benchmark": "uninum",
      "role": "gold_anchor",
      "source_id": "eng_us:42"
    }
  ]
}
```

## Numeric input

`input.value` is stored as a string so the schema is not limited by JSON number
precision and can later support very large integers and exact decimals.

Initial `input.kind` values are:

- `integer`
- `decimal`
- `fraction`

The MVP importer emits integers only.

## Mode

`mode` describes the requested numeral transformation. The MVP uses
`cardinal`. The field is intentionally extensible for future compatibility with
numeralform/num2words-style operations such as `ordinal`, `ordinal_num`, `year`,
and `currency`.

## Grammar

Grammar is independent of locale. Known fields include:

- `context`: `citation`, `standalone`, or a project-defined context label
- `case`
- `gender`
- `grammatical_number`
- `animacy`
- `definiteness`
- `noun_class`
- `counter`
- `variant`

Unknown future string-valued features are allowed. Absence means "not specified",
not "the language has no such feature".

## Quality

- `gold`: accepted benchmark assertion
- `reference`: generated/reference assertion, not automatically Gold
- `quarantine`: needs review

## Sources

`source_observations` is plural because independent sources can support the same
semantic assertion. Each observation preserves source version/revision and its
role (`gold_anchor`, `generated_reference`, `manual_review`, etc.).

Canonical data should never silently discard conflicting observations. A future
review/adjudication layer should retain disagreement explicitly.
