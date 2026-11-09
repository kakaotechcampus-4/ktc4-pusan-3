.DEFAULT_GOAL := help
.PHONY: help install dev test lint fmt db-up db-down db-logs \
        db-migrate db-rollback db-current db-history db-heads db-check db-revision \
        db-merge db-reset db-psql \
        web-install web-dev web-build web-start web-test web-e2e web-e2e-live web-lint web-fmt web-fmt-check \
        web-typecheck web-check web-contract web-image web-up web-down web-logs \
        api-image api-up api-down api-logs deploy-up deploy-down deploy-migrate \
        deploy-load-food-dry deploy-load-food deploy-load-places-dry deploy-load-places \
        eval-smoke eval eval-week eval-report

# 로컬 개발 DB 와 배포는 compose 파일이 다르다. .env 는 한 곳(deploy/docker/.env)을 같이 쓴다.
COMPOSE_DEV    := docker compose -f deploy/docker/docker-compose.yml --env-file deploy/docker/.env
# 배포 이미지 태그는 마지막으로 배포에 성공한 커밋 sha 다 (deploy/scripts/deploy.sh 가 적는다). 서버에서 make 로
# 띄우거나 적재할 때도 지금 서비스 중인 그 이미지를 쓴다 — :local 을 쓰면 옛 이미지로 조용히 돌아가거나,
# 없으면 그 자리에서 다시 굽는다. 로컬은 파일이 없어 local 이다.
DEPLOYED_TAG   := $(shell cat deploy/docker/.deployed-tag 2>/dev/null || echo local)
COMPOSE_FILES  := docker compose -f deploy/docker/docker-compose.deploy.yml --env-file deploy/docker/.env
COMPOSE_DEPLOY := env API_IMAGE_TAG=$(DEPLOYED_TAG) WEB_IMAGE_TAG=$(DEPLOYED_TAG) $(COMPOSE_FILES)
# 적재는 배포와 같은 잠금을 잡는다 — 4GB 서버에서 빌드와 겹치지 않게. 맥에는 flock 이 없어 그냥 돈다.
# (COMPOSE_DEPLOY 가 `env` 로 시작하는 이유 — flock 뒤에 오는 첫 단어는 명령이어야 해서 VAR=값 을 못 받는다)
DEPLOY_LOCK    := $(shell command -v flock >/dev/null 2>&1 && echo flock -w 1200 /var/lock/ktc4-deploy.lock)
# 떠 있는 스택 · 배포 DB 를 건드리는 타깃은 .env 가 dev · prod 중 하나와 통째로 맞는지 먼저 본다 (deploy.sh 와 같은 검사).
# dev .env 에서 COMPOSE_PROJECT_NAME 한 줄만 빠져도 운영 프로젝트(ktc4-deploy) · 운영 DB 로 떨어지기 때문이다.
DEPLOY_GUARD   := bash deploy/scripts/deploy.sh --check-env
# 마이그레이션 · 적재용 — 잠금을 잡은 뒤에 .deployed-tag 를 읽는다. make 가 미리 펼친 DEPLOYED_TAG 는 잠금을 기다리는
# 동안 끝난 배포를 모른다 (#753 리뷰). 🚨 api 이미지가 없으면 멈춘다 — compose run 에는 --no-build 가 없어서
# (v2.34, --pull never 여도) 지금 작업 트리를 배포된 sha 이름으로 구워 롤백할 이미지를 덮는다 (#808 리뷰).
COMPOSE_DEPLOY_NOW := sh -c 'tag=$$(cat deploy/docker/.deployed-tag 2>/dev/null || echo local); export API_IMAGE_TAG=$$tag WEB_IMAGE_TAG=$$tag; \
	img=$$($(COMPOSE_FILES) config --images | grep -- "-api:" | head -n 1); \
	docker image inspect "$$img" >/dev/null 2>&1 || { echo "$$img 이미지가 없다 — 굽지 않고 멈춘다. 로컬이면 make api-image 먼저, 서버면 deploy.sh 로 다시 배포"; exit 1; }; \
	exec $(COMPOSE_FILES) "$$@"' compose
# 🚨 서버(.deployed-tag 가 있는 checkout)에서는 이미지를 손으로 굽지 않는다. 지금 소스를 배포된 sha 태그로
#    구워 롤백할 이미지를 덮어쓴다. 서버 빌드는 deploy.sh 만 한다 (#753 리뷰).
NO_SERVER_BUILD := [ ! -f deploy/docker/.deployed-tag ] || { echo "서버에서는 이미지를 손으로 굽지 않는다 — 배포된 sha 태그를 덮어 롤백할 곳이 사라진다. bash deploy/scripts/deploy.sh <sha> <dev|prod> 로 (docs/ops/deploy-v1.md)"; exit 1; }

help:
	@echo "사용 가능한 명령"
	@echo ""
	@echo "── 백엔드 · AI (apps/api) ──"
	@echo "  make install      의존성 설치 (클론 직후)"
	@echo "  make dev          개발 서버 실행  http://localhost:8000"
	@echo "  make test         테스트 실행"
	@echo "  make lint         코드 검사 (ruff check + format --check) — PR 올리기 전"
	@echo "  make fmt          코드 포맷팅 (ruff)"
	@echo "  make eval-smoke   AI 품질 점검 스모크 (실제 모델 · ~15 입력 · 크레딧 사용)"
	@echo "  make eval         AI 품질 점검 전체 (실제 모델 · ~100 입력 · 크레딧 사용)"
	@echo "  make eval-week    W 일주일 시나리오 (실제 모델 · icatch_it 에 시험 행을 씀 · 크레딧 사용)"
	@echo "  make eval-report  가장 최근 P 전체 실행 결과를 요약 md 로 (eval/results/raw/ · 커밋 안 함)"
	@echo ""
	@echo "── 프론트 (apps/web) ──"
	@echo "  make web-install    의존성 설치 (클론 직후 · pnpm)"
	@echo "  make web-dev        개발 서버 실행  http://localhost:3000"
	@echo "  make web-build      프로덕션 빌드 (프리렌더까지 — dev 에서 안 보이는 에러가 여기서 난다)"
	@echo "  make web-start      빌드 결과를 로컬에서 실행 (web-build 먼저)"
	@echo "  make web-test       테스트 실행 (vitest)"
	@echo "  make web-e2e        화면 규칙 테스트 (Playwright · 목 서버로 개발 서버를 띄운다)"
	@echo "  make web-e2e-live   실서버 e2e (실제 api + e2e 용 DB · make db-up 먼저 · LLM 은 안 부른다)"
	@echo "  make web-typecheck  타입 검사 (next typegen + tsc)"
	@echo "  make web-lint       코드 검사 (eslint)"
	@echo "  make web-fmt        코드 포맷팅 (prettier)"
	@echo "  make web-fmt-check  포맷 검사만 (고치지 않는다 — CI 가 보는 것)"
	@echo "  make web-check      format + typecheck + lint + test — PR 올리기 전 (CI 와 같은 검사, build 만 빠짐)"
	@echo "  make web-contract   웹 API 타입 ↔ 서버 계약(OpenAPI) 비교 — lib/api 나 서버 스키마를 바꿨을 때 (apps/api 의 uv 필요)"
	@echo ""
	@echo "── 로컬 개발 DB (deploy/docker) ──"
	@echo "  make db-up        로컬 Postgres+pgvector+Redis 기동 (최초 1회 deploy/docker/.env 필요)"
	@echo "  make db-down      로컬 DB 중지"
	@echo "  make db-logs      로컬 DB 로그"
	@echo ""
	@echo "── 마이그레이션 (alembic) ──"
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
	@echo "── 배포 (docker) ── up · migrate · 적재는 굽지 않는다. 로컬 처음이면 api-image · web-image 먼저 (서버에선 굽기 거부)"
	@echo "  make api-image    API 이미지 굽기"
	@echo "  make api-up       API 컨테이너 기동 (db+redis 의존)"
	@echo "  make api-down     API 컨테이너 중지"
	@echo "  make api-logs     API 컨테이너 로그"
	@echo "  make web-image    프론트 이미지 굽기 (deploy/docker/.env 의 NEXT_PUBLIC_API_BASE_URL 이 박힌다)"
	@echo "  make web-up       프론트 컨테이너 기동"
	@echo "  make web-down     프론트 컨테이너 중지"
	@echo "  make web-logs     프론트 컨테이너 로그"
	@echo "  make deploy-up      전체 기동 (db + redis + api + web)"
	@echo "  make deploy-down    전체 중지"
	@echo "  make deploy-migrate 배포 DB 마이그레이션 (api 이미지의 일회용 상자 api-task 로 alembic upgrade heads)"
	@echo "  make deploy-load-food-dry  배포 DB 에 Food 카탈로그를 받고 세기만 (쓰지 않음)"
	@echo "  make deploy-load-food      배포 DB 에 Food 카탈로그 적재 (영양DB + 레시피, 한 트랜잭션)"
	@echo "  make deploy-load-places-dry  배포 DB 에 장소(공원 · 도서관 · 놀이시설)를 받고 세기만 (쓰지 않음)"
	@echo "  make deploy-load-places      배포 DB 에 장소 적재 (덮고 없어진 곳은 지움, 한 트랜잭션)"
	@echo ""
	@echo "  install/dev/test/lint/fmt/db-* 는 apps/api 안에서 uv 로 실행됩니다."
	@echo "  web-* 는 apps/web 안에서 pnpm 으로 실행됩니다 (배포 4개는 docker)."

install:
	cd apps/api && uv sync

dev:
	cd apps/api && uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port 8000

test:
	cd apps/api && uv run pytest

lint:
	cd apps/api && uv run ruff check . && uv run ruff format --check .

fmt:
	cd apps/api && uv run ruff format .

# AI 품질 점검 (docs/eval/eval-v1.md). 실제 모델을 부른다 — 크레딧을 쓴다.
# PYTEST_ADDOPTS 를 비워 셸에 남은 -m 'not live' 를 지운다
eval-smoke:
	cd apps/api && PYTEST_ADDOPTS= EVAL_TIER=smoke uv run pytest tests/eval/suite -m live -q

eval:
	cd apps/api && PYTEST_ADDOPTS= EVAL_TIER=full uv run pytest tests/eval/suite -m live -q --deselect tests/eval/suite/test_week_live.py

# W 는 보호자 · 아이 · 토큰 · 관찰을 DB 에 쓴다 — 통합 테스트 DB 에서만 돈다 (DB_NAME 이 다르면 시작 전에 실패)
eval-week:
	cd apps/api && PYTEST_ADDOPTS= EVAL_TIER=full DB_NAME=icatch_it uv run pytest tests/eval/suite/test_week_live.py -m live -q

eval-report:
	cd apps/api && uv run python -m tests.eval.suite.report

# ── 프론트 (apps/web) ────────────────────────────────────────────────
# 최초 1회: cd apps/web && cp .env.example .env.local
# NEXT_PUBLIC_API_BASE_URL 이 없으면 앱이 뜨지 않고 바로 에러를 던진다 (src/lib/env.ts).

web-install:
	cd apps/web && pnpm install

web-dev:
	cd apps/web && pnpm dev

web-build:
	cd apps/web && pnpm build

web-start:
	cd apps/web && pnpm start

web-test:
	cd apps/web && pnpm test

# 개발 서버가 떠 있으면 그것을 쓰고, 없으면 목 모드로 띄웠다 내린다 (playwright.config.ts).
web-e2e:
	cd apps/web && pnpm test:e2e

# 실서버 e2e (#756). 실제 api 와 DB 위에서 화면이 이어지는지 본다 (apps/web/playwright.live.config.ts).
# 개발 DB 서버(make db-up)에 e2e 용 DB 를 따로 만든다 — 테스트가 보호자와 아이를 계속 만들어서다.
# 🚨 빌드가 api 주소(8100)를 박는다. 이 빌드로 make web-start 를 하면 e2e api 를 부른다 — 다시 web-build 할 것.
E2E_DB_NAME ?= icatch_e2e
E2E_LIVE_BUILD_ENV := NEXT_PUBLIC_API_BASE_URL=http://localhost:8100 NEXT_PUBLIC_API_MOCKING=disabled \
	NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_FIREBASE_API_KEY= NEXT_PUBLIC_FIREBASE_PROJECT_ID= \
	NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID= NEXT_PUBLIC_FIREBASE_APP_ID= NEXT_PUBLIC_FIREBASE_VAPID_KEY=

web-e2e-live:
	docker exec ktc4-postgres sh -c 'createdb -U "$$POSTGRES_USER" $(E2E_DB_NAME) 2>/dev/null || true'
	cd apps/api && DB_NAME=$(E2E_DB_NAME) uv run alembic upgrade heads
	cd apps/web && $(E2E_LIVE_BUILD_ENV) pnpm build
	cd apps/web && E2E_DB_NAME=$(E2E_DB_NAME) pnpm test:e2e-live

web-typecheck:
	cd apps/web && pnpm typecheck

web-lint:
	cd apps/web && pnpm lint

web-fmt:
	cd apps/web && pnpm format

web-fmt-check:
	cd apps/web && pnpm format:check

# CI(.github/workflows/ci-web.yml)와 같은 검사. 거기에 build 하나가 더 붙는다.
web-check: web-fmt-check web-typecheck web-lint web-test

# 웹 API 타입이 서버 계약(OpenAPI)과 맞는지 (#559 · ci-contract.yml). 서버 계약을 덤프한 뒤 비교한다.
# apps/api 의 .env 가 없어도 돈다 — 스크립트가 필수 설정을 더미로 채운다. DB 에는 붙지 않는다.
web-contract:
	cd apps/api && uv run python -m scripts.dump_openapi ../web/.openapi.json
	cd apps/web && pnpm test:contract

db-up:
	$(COMPOSE_DEV) up -d

db-down:
	$(COMPOSE_DEV) down

db-logs:
	$(COMPOSE_DEV) logs -f db

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

# ── 배포 (docker) ────────────────────────────────────────────────────

api-image:
	@$(NO_SERVER_BUILD)
	$(COMPOSE_DEPLOY) build api

api-up:
	@$(DEPLOY_GUARD)
	$(COMPOSE_DEPLOY) up -d --no-build api

api-down:
	@$(DEPLOY_GUARD)
	$(COMPOSE_DEPLOY) down api

api-logs:
	$(COMPOSE_DEPLOY) logs -f api

# 🚨 web-image 는 deploy/docker/.env 의 NEXT_PUBLIC_API_BASE_URL 을 번들에 박는다.
#    주소가 바뀌면 web-up 을 다시 하는 게 아니라 web-image 를 다시 굽는다 (서버에서는 deploy.sh 로 다시 배포).
# 🚨 up 은 --no-build 다 — 이미지가 없을 때 compose 가 지금 작업 트리를 배포된 sha 이름으로 구우면
#    롤백할 이미지를 덮는다 (#808 리뷰). 로컬은 처음 한 번 api-image · web-image 를 먼저 돈다.

web-image:
	@$(NO_SERVER_BUILD)
	$(COMPOSE_DEPLOY) build web

web-up:
	@$(DEPLOY_GUARD)
	$(COMPOSE_DEPLOY) up -d --no-build web

web-down:
	@$(DEPLOY_GUARD)
	$(COMPOSE_DEPLOY) down web

web-logs:
	$(COMPOSE_DEPLOY) logs -f web

# ktc4-edge 는 dev · prod 가 같이 쓰는 바깥 네트워크다 (deploy.sh 와 같은 값). 로컬에서 처음 띄울 때도 있어야 한다.
deploy-up:
	@$(DEPLOY_GUARD)
	docker network inspect ktc4-edge >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/24 --ip-range 172.30.0.128/25 ktc4-edge
	$(COMPOSE_DEPLOY) up -d --no-build
	@# api 상자만 EC2 메타데이터(S3 열쇠)에 닿게 한다 — 서버 재부팅 뒤에도 걸리게 등록까지. 맥은 건너뛴다
	@if [ "$$(uname -s)" = Linux ]; then sudo -n deploy/scripts/imds-firewall.sh --install; fi

deploy-down:
	@$(DEPLOY_GUARD)
	$(COMPOSE_DEPLOY) down

deploy-migrate:
	@$(DEPLOY_GUARD)
	$(DEPLOY_LOCK) $(COMPOSE_DEPLOY_NOW) run --rm api-task uv run --no-sync alembic upgrade heads

# Food 카탈로그 적재 (FT-19). 배포된 api 이미지 안의 스크립트를 프로세스로 돌린다 — 정리 표 ·
# 식품군 대응표는 이미지에 구워진 것을 쓴다. 출력은 건수뿐이다.
deploy-load-food-dry:
	@$(DEPLOY_GUARD)
	$(DEPLOY_LOCK) $(COMPOSE_DEPLOY_NOW) run --rm api-task uv run --no-sync python -m scripts.load_food_catalog --all --dry-run

deploy-load-food:
	@$(DEPLOY_GUARD)
	$(DEPLOY_LOCK) $(COMPOSE_DEPLOY_NOW) run --rm api-task uv run --no-sync python -m scripts.load_food_catalog --all

# 장소 적재 (#516). 공원 · 도서관 표준데이터와 어린이놀이시설(화이트리스트)을 place 로 싣는다.
# 출력은 건수뿐이다.
deploy-load-places-dry:
	@$(DEPLOY_GUARD)
	$(DEPLOY_LOCK) $(COMPOSE_DEPLOY_NOW) run --rm api-task uv run --no-sync python -m scripts.load_places --all --dry-run

deploy-load-places:
	@$(DEPLOY_GUARD)
	$(DEPLOY_LOCK) $(COMPOSE_DEPLOY_NOW) run --rm api-task uv run --no-sync python -m scripts.load_places --all
