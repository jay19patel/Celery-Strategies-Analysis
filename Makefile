.PHONY: help up down logs install test lint fmt check token clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up:  ## Start everything in the background (same as: docker compose up -d)
	docker compose up -d

down:  ## Stop everything
	docker compose down

logs:  ## Follow logs
	docker compose logs -f --tail=100

install:  ## Local dev dependencies (tests, lint)
	uv sync --extra dev

test:  ## Run the tests
	uv run pytest

lint:  ## Lint
	uv run ruff check tradebuddy tests

fmt:  ## Auto-fix lint and format
	uv run ruff check --fix tradebuddy tests
	uv run ruff format tradebuddy tests

check: lint test  ## Everything CI runs

token:  ## Generate an API_TOKEN for .env
	@uv run python -c "import secrets; print(secrets.token_urlsafe(32))"

clean:  ## Remove caches
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
