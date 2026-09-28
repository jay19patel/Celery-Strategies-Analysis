.PHONY: help install run test lint fmt check token clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies including dev extras
	uv sync --extra dev

run:  ## Start everything: http://127.0.0.1:8080
	uv run python -m tradebuddy

test:  ## Run the tests
	uv run pytest

lint:  ## Lint
	uv run ruff check tradebuddy tests

fmt:  ## Auto-fix lint and format
	uv run ruff check --fix tradebuddy tests
	uv run ruff format tradebuddy tests

check: lint test  ## Everything CI runs

token:  ## Generate an API_TOKEN
	@uv run python -c "import secrets; print(secrets.token_urlsafe(32))"

clean:  ## Remove caches
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
