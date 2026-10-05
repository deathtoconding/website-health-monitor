.PHONY: install run test lint format format-check check backup

install:
	python -m pip install -e '.[dev]'

run:
	python -m app

test:
	python -m pytest

lint:
	ruff check .

format:
	ruff format .

format-check:
	ruff format --check .

check: lint format-check test

backup:
	@test -n "$(DEST)" || (echo 'Usage: make backup DEST=backups/health-monitor-YYYYMMDD.db' >&2; exit 2)
	whm-backup "$(DEST)"
