.PHONY: check test lint typecheck architecture no-docstrings

check: lint typecheck no-docstrings architecture test

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy

architecture:
	lint-imports

no-docstrings:
	python scripts/no_docstrings.py
