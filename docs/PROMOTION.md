# Promotion policy

The MVP separates **import** from **canonical promotion**.

## Import

`import-uninum` writes source-backed candidate rows to the external work root.
The command validates structure and deterministic identity, but it does not
rewrite `data/corpus/`.

## Promotion in v0.1

Promotion is intentionally a reviewed Git change:

1. select source-backed rows from `<WORK>/candidates/`;
2. verify their source identity and language/locale mapping;
3. add any accepted spelling variants explicitly;
4. verify grammatical features describe only what the evidence supports;
5. copy reviewed rows to `data/corpus/<language>.jsonl`;
6. run `numeralform-gold validate data/corpus/<language>.jsonl` and tests;
7. commit the corpus change with source/review rationale.

A future command will automate the mechanical parts while retaining durable
review evidence.

## Prohibited shortcuts

Do not promote a row solely because:

- numeralform emits the same output;
- num2words emits the same output;
- ICU/CLDR emits the same output;
- two generated implementations agree with each other.

Generated agreement is useful differential evidence, not independent Gold.
