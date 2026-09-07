.PHONY: install dev test lint fmt

install:
	cd apps/api && uv sync

dev:
	cd apps/api && uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port 8000

test:
	cd apps/api && uv run pytest

lint:
	cd apps/api && uv run ruff check .

fmt:
	cd apps/api && uv run ruff format .
