# Source plan

## UniNum

Role: `gold_anchor`

The Google Research UniNum repository provides number names for many languages,
locales and scripts. The upstream README states that citation forms are used in
languages where pronunciation changes with morphosyntactic context.

MVP use:

- import the source TSV values as citation-form cardinal anchors;
- preserve source code/variety metadata;
- do not infer case/gender variants from citation forms.

Pinned revision: see `sources/source-lock.json`.

## Unicode CLDR RBNF

Role: `generated_reference`

CLDR contains rule-based number-formatting data. ICU can use RBNF to spell out
numbers. This is the intended source for exhaustive differential ranges such as
0..9999.

MVP use:

- keep a pinned CLDR checkout in the external source cache;
- optionally generate reference rows through ICU;
- mark those rows `quality=reference`;
- require independent evidence/review before promotion to Gold.

## Later sources

Candidates for later ingestion include Numeralbank/Chan numerals and dedicated
contextual normalization corpora for inflected languages. They are deliberately
not required by the MVP.
