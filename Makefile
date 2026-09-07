.DEFAULT_GOAL := help
.PHONY: help install dev test lint fmt

help:
	@echo "사용 가능한 명령"
	@echo "  make install   의존성 설치 (클론 직후)"
	@echo "  make dev       개발 서버 실행  http://localhost:8000"
	@echo "  make test      테스트 실행"
	@echo "  make lint      코드 검사 (ruff)"
	@echo "  make fmt       코드 포맷팅 (ruff)"
	@echo ""
	@echo "  모든 명령은 apps/api 안에서 uv 로 실행됩니다."

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
