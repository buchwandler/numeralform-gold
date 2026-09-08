.PHONY: test lint format check source-cache work-init

test:
	python -m pytest -q

lint:
	ruff check .

format:
	ruff format .

check: lint test

source-cache:
	python scripts/setup-source-cache.py

work-init:
	numeralform-gold work-init
