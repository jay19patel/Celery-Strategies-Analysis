.PHONY: help dev backend install test lint fmt typecheck check clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

dev:  ## Backend and dashboard with hot reload
	./start.sh

backend:  ## Backend only (production mode, single worker)
	./start_backend.sh run


install:  ## Install backend and frontend dependencies
	uv venv -p 3.13 .venv
	uv pip install -p .venv/bin/python -e "backend[dev]"
	cd frontend && npm install

test:  ## Backend tests
	cd backend && ../.venv/bin/pytest

lint:  ## Lint backend and frontend
	.venv/bin/ruff check backend
	cd frontend && npx eslint src

typecheck:  ## Typecheck the dashboard
	cd frontend && npx tsc --noEmit

fmt:  ## Auto-fix backend lint
	.venv/bin/ruff check --fix backend

check: lint typecheck test  ## Everything CI should run

clean:  ## Remove caches
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/.next
