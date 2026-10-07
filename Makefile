.PHONY: help install run test lint format format-check verify check hooks backup wheel clean

help: ## Show the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the application and development tooling into the active environment
	python -m pip install -e '.[dev]'

run: ## Start the local server with the documented defaults (loopback only)
	python -m app

test: ## Run the pytest suite with the 90% coverage gate
	python -m pytest

lint: ## Run the Ruff linter
	ruff check .

format: ## Apply Ruff formatting
	ruff format .

format-check: ## Verify formatting without rewriting files
	ruff format --check .

verify: ## Check version/metadata/docs consistency across the repository
	python tools/check_repo_consistency.py

check: lint format-check verify test ## Full local quality gate (same as CI)

hooks: ## Install the pre-commit hooks for this checkout
	pre-commit install

backup: ## Create a verified online SQLite backup: make backup DEST=backups/health-YYYYMMDD.db
	@test -n "$(DEST)" || (echo 'Usage: make backup DEST=backups/health-monitor-YYYYMMDD.db' >&2; exit 2)
	whm-backup "$(DEST)"

wheel: ## Build a wheel to verify packaging (dashboard assets included)
	python -m pip wheel --no-deps --wheel-dir dist .

clean: ## Remove local caches and build output
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
