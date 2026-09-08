# Contributing

## Local setup

```bash
python -m pip install -e ".[dev]"
python scripts/setup-source-cache.py
make check
```

Upstream checkouts and generated work belong in the sibling source-cache and
work directories configured by `config.toml`; do not commit them into this repo.

## Corpus changes

Read `DATA_MODEL.md`, `docs/SOURCES.md`, and `docs/PROMOTION.md` before changing
canonical Gold. Keep source revisions and grammatical context explicit.
