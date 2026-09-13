.DEFAULT_GOAL := help
.PHONY: help install dev test lint fmt db-up db-down db-logs

help:
	@echo "사용 가능한 명령"
	@echo "  make install   의존성 설치 (클론 직후)"
	@echo "  make dev       개발 서버 실행  http://localhost:8000"
	@echo "  make test      테스트 실행"
	@echo "  make lint      코드 검사 (ruff)"
	@echo "  make fmt       코드 포맷팅 (ruff)"
	@echo "  make db-up     로컬 Postgres+pgvector 기동 (최초 1회 deploy/docker/.env 필요)"
	@echo "  make db-down   로컬 DB 중지"
	@echo "  make db-logs   로컬 DB 로그"
	@echo ""
	@echo "  install/dev/test/lint/fmt 는 apps/api 안에서 uv 로 실행됩니다."

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

db-up:
	docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env up -d

db-down:
	docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env down

db-logs:
	docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env logs -f db
