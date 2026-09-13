.DEFAULT_GOAL := help
.PHONY: help install dev test lint fmt db-up db-down db-logs \
        db-migrate db-rollback db-current db-history db-heads db-check db-revision \
        db-merge db-reset db-psql

help:
	@echo "사용 가능한 명령"
	@echo "  make install      의존성 설치 (클론 직후)"
	@echo "  make dev          개발 서버 실행  http://localhost:8000"
	@echo "  make test         테스트 실행"
	@echo "  make lint         코드 검사 (ruff)"
	@echo "  make fmt          코드 포맷팅 (ruff)"
	@echo ""
	@echo "  make db-up        로컬 Postgres+pgvector 기동 (최초 1회 deploy/docker/.env 필요)"
	@echo "  make db-down      로컬 DB 중지"
	@echo "  make db-logs      로컬 DB 로그"
	@echo ""
	@echo "  make db-migrate   pending migration 전체 적용 (upgrade heads)"
	@echo "  make db-rollback  마지막 migration 한 단계 되돌리기 (downgrade -1)"
	@echo "  make db-current   현재 적용된 revision 확인"
	@echo "  make db-history   전체 revision 체인 출력"
	@echo "  make db-heads     현재 head 목록 (충돌 판정용)"
	@echo "  make db-check     ORM 모델과 DB 스키마 일치 검증 (PR 올리기 전 필수)"
	@echo "  make db-reset yes=1  로컬 DB를 비우고 migration 처음부터 재적용 (데이터 전부 삭제)"
	@echo "  make db-revision msg=\"설명\"  새 migration 파일 자동 생성"
	@echo "  make db-merge msg=\"설명\"     갈라진 head를 합치는 merge revision 생성"
	@echo "  make db-psql      로컬 DB에 psql 직접 접속"
	@echo ""
	@echo "  install/dev/test/lint/fmt/db-* 는 apps/api 안에서 uv 로 실행됩니다."

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

db-migrate:
	cd apps/api && uv run alembic upgrade heads

db-rollback:
	cd apps/api && uv run alembic downgrade -1

db-current:
	cd apps/api && uv run alembic current

db-history:
	cd apps/api && uv run alembic history --verbose

db-heads:
	cd apps/api && uv run alembic heads

db-check:
	cd apps/api && uv run alembic check

db-revision:
	@[ "$(msg)" ] || { echo "사용법: make db-revision msg=\"한 줄 설명\""; exit 1; }
	cd apps/api && uv run alembic revision --autogenerate -m "$(msg)"

db-merge:
	@[ "$(msg)" ] || { echo "사용법: make db-merge msg=\"한 줄 설명\""; exit 1; }
	cd apps/api && uv run alembic merge -m "$(msg)" heads

db-reset:
	@[ "$(yes)" = "1" ] || { echo "로컬 개발 DB의 데이터를 전부 지우고 migration을 처음부터 다시 적용합니다."; echo "실행하려면: make db-reset yes=1"; exit 1; }
	docker exec ktc4-postgres psql -U $$(docker exec ktc4-postgres printenv POSTGRES_USER) -d $$(docker exec ktc4-postgres printenv POSTGRES_DB) -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	cd apps/api && uv run alembic upgrade heads

db-psql:
	docker exec -it ktc4-postgres psql -U $$(docker exec ktc4-postgres printenv POSTGRES_USER) -d $$(docker exec ktc4-postgres printenv POSTGRES_DB)
