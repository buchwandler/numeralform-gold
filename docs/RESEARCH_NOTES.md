# Research notes for the MVP

Checked: 2026-09-08.

## UniNum

- Repository: https://github.com/google-research-datasets/uninum
- Pinned commit: `b3d9064655a492e5cb71f6237383154d3ba74546`
- Repository status: archived/read-only since 2024-03-07.
- Upstream description: number names for 186 languages/locales/scripts.
- Morphology caveat: upstream says it provides a citation form where pronunciation
  varies with morphosyntactic context.
- Repository license: Apache-2.0.
- Observed shape: language/variety TSVs contain 0..99 plus 100 and selected powers
  of ten; this is anchor data, not exhaustive 0..9999 data.

MVP decision: import these rows as `quality=gold`, `role=gold_anchor`, with
`grammar.context="citation"`.

## Unicode CLDR RBNF

- Repository: https://github.com/unicode-org/cldr
- Release used for source pinning: `release-48-2`
- Release date: 2026-03-17.
- Pinned commit: `11299982335beb974c1c63c45265184e759c0f41`
- Relevant tree: `common/rbnf/`.
- Unicode data/software license: Unicode License v3 (`Unicode-3.0`).

MVP decision: keep the rules checkout as an independent standards/reference
source. Generated rows are `quality=reference` until supported by independent
human/expert evidence or review.

## `icu-rbnf`

- PyPI package: https://pypi.org/project/icu-rbnf/
- Current release checked for this MVP: 0.1.0, 2026-04-17.
- Provides `spellout`, numeric ordinals and word ordinals over ICU RBNF.

MVP decision: optional runtime for quickly generating exhaustive differential
ranges. Its runtime version is recorded separately from the pinned CLDR source
checkout because the package/runtime data is not asserted to be byte-identical
to that CLDR commit.

## `unicode-rbnf`

- PyPI package: https://pypi.org/project/unicode-rbnf/
- Current release checked: 2.4.0, 2025-10-07.
- Pure-Python RBNF implementation using CLDR data.
- Its public examples expose per-ruleset variants such as German masculine,
  feminine and other cardinal forms, which is interesting for numeralform.
- Its documentation points to CLDR release 44 and explicitly lists unimplemented
  RBNF features.

MVP decision: do not use it as canonical ground truth. Revisit it in P1/P2 as a
ruleset discovery/reference adapter for grammatical variants.

## Numeralbank

Numeralbank/Chan remains a strong later source for broad lexical coverage. It is
not required for the first working repository because the MVP needs one expert
anchor source plus one exhaustive reference mechanism first.

## Design conclusion

The benchmark must keep three concepts separate:

1. **Gold anchors**: human/expert-backed observations such as UniNum.
2. **Reference output**: standards/rule-generated rows such as CLDR/ICU.
3. **Grammar Gold**: context-sensitive case/gender/number forms that require
   explicit features and independent evidence/review.

This avoids circular validation when numeralform itself eventually implements
CLDR-derived rules.
