# apps/api — 백엔드 개발 가이드

백엔드 + AI를 하나의 FastAPI 서비스로 제공합니다.
아키텍처·레이어 경계·소유 규칙은 [CLAUDE.md](CLAUDE.md)를 먼저 읽으세요.

---

## 사전 준비

**Python 3.12** 와 **uv** 가 필요합니다.

### macOS

```bash
# Python 3.12 (pyenv 사용 예시)
brew install pyenv
pyenv install 3.12
pyenv global 3.12

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows (PowerShell)

```powershell
# Python 3.12 — https://www.python.org/downloads/ 에서 설치 후 PATH 추가

# uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## 처음 세팅 (클론 후 한 번)

```bash
# 저장소를 받을 위치에서
git clone https://github.com/kakaotechcampus-4/ktc4-pusan-3.git
cd ktc4-pusan-3

# 환경변수 파일 생성 (실제 값은 팀 채널에서 공유)
cp apps/api/.env.example apps/api/.env

# 의존성 설치 (uv.lock 기준)
make install
```

---

## 앱 실행 및 동작 확인

```bash
# 개발 서버 시작 (코드 변경 시 자동 재시작)
make dev

# 다른 터미널에서 health 확인
curl http://localhost:8000/health
# 예상 응답: {"status":"ok","env":"local"}
```

> 헬스체크는 `/api/v1` **밖**에 있습니다. API 계약서 §01이 "경로는 모두 `/api/v1` 하위 ·
> 인증 예외 없음" 이라고 정하는데, 로드밸런서와 컨테이너 healthcheck 는 토큰 없이
> 호출해야 하기 때문입니다. 도메인 엔드포인트는 전부 `/api/v1` 하위에 붙습니다.

---

## 자주 쓰는 명령

| 명령 | 실행 내용 |
|------|-----------|
| `make install` | `uv sync` — 의존성 설치 (uv.lock 기준) |
| `make dev` | uvicorn 개발 서버 시작 (포트 8000, hot-reload) |
| `make test` | pytest 전체 실행 |
| `make lint` | ruff check — 린트 검사 |
| `make fmt` | ruff format — 코드 포맷 |

> 모든 명령은 저장소 **루트**에서 실행합니다.

---

## 로그와 알림

로그는 **stderr 로만** 찍는다 (`app/core/logging_config.py`). 어디에 모을지는 실행 환경이 정하고 앱은
파일을 열지 않는다. 결정과 근거는 [`docs/ops/logging-alerts-v1.md`](../../docs/ops/logging-alerts-v1.md)
(멘토 #267 2번). 여기는 절차만 둔다.

### 로그는 어디서 보나

| 어디서 | 어떻게 |
|------|------|
| 로컬 `make dev` | 터미널에 그대로 |
| 배포 서버 | `docker compose -f deploy/docker/docker-compose.deploy.yml logs -f api` |
| 배포 서버, 컨테이너를 지운 뒤에도 | `journalctl CONTAINER_NAME=ktc4-api --since "1 hour ago"` |

🚨 docker 기본 로그(json-file)는 새 이미지로 `compose up` 할 때 컨테이너와 함께 지워진다. 그래서
`deploy/docker/docker-compose.deploy.yml` 은 전 서비스를 journald 로 둔다 (`x-logging` 앵커 · 서버는
Ubuntu 24.04 라 journald 가 기본으로 디스크에 남는다). api 를 올릴 때는 서비스 아래에 한 줄이다 (#166).

```yaml
    logging: *logging
```

맥의 Docker Desktop 에는 journald 가 없다. 배포 compose 를 로컬에서 띄워 볼 때만 `deploy/docker/.env` 에
`LOG_DRIVER=json-file` 을 둔다 (빌드 `make web-image` 는 상관없다). 그때는 회전이 없으니 오래 돌리지
않는다 — journald 없는 호스트에서 오래 돌릴 일이 생기면 json-file 에 `max-size` · `max-file` 회전을 건다.

### 에러 알림 (Discord)

api 프로세스의 `app.*` 로거에서 **ERROR 이상**이 나면 Discord 웹훅으로 한 건씩 간다 (`app/core/alerts.py`,
전송은 `app/integrations/discord.py`). 본문은 이렇게 생겼고, 이것뿐이다.

```
api-alert      🟠 [prod] ERROR app.api.runs.runner:_guarded:172 — run %s 의 job 이 %s 로 끝났다
               2026-10-09 23:01:02+0900
               → journalctl CONTAINER_NAME=ktc4-api --since "10 min ago" | grep ERROR
browser-alert  🟡 [prod] ERROR app.api.v1.routers.client_errors:report_client_error:39 — 화면 오류 … · TypeError - /children/…/records android 14 app
infra-alert    🔴 [prod] ktc4-web: running healthy → exited -
               → docker compose -f deploy/docker/docker-compose.deploy.yml logs --tail 100 <이름>
web-alert      🟡 [prod] ERROR web render /children/[cid]/records — TypeError digest=abc123 path=/children/…/records
```

머리의 표시가 세기다 — 🔴 지금 봐야 함(서비스가 안 됨) · 🟠 오늘 안에 · 🟡 내일 봐도 됨 · 🟢 복구. 전부 같은
세기로 오면 다 무시하게 된다. 셋째 줄은 "다음에 칠 명령" 이다 — 알림을 보고 뭘 할지 바로 알게.

`%s` 를 채운 값 · 원문 · 트레이스백 · 예외 메시지는 **서버 로그에만** 있다 (루트 CLAUDE.md §2). 알림을
보면 그 시각의 서버 로그에서 같은 줄을 찾는다. 값을 내보내는 유일한 길은 로그를 찍을 때
`extra={"alert_detail": "..."}` 로 표시하는 것이고, 서버가 모양을 검사한 값(화면 오류 보고)만 그렇게
한다. 채널 하나에 **보내는 이름**이 출처별로 갈린다 — `api-alert`(서버) · `browser-alert`(화면 오류) ·
`infra-alert`(컨테이너 감시) · `web-alert`(Next 서버 오류 — `apps/web/src/instrumentation.ts` 가 웹훅으로
직접 보낸다, 토큰이 없어 api 를 못 거치니까). 로그에 `extra={"alert_source": "browser"}` 을 붙이면 그
이름으로 간다. 같은 오류가 쏟아지면 분당 10건까지만 보내고 넘친 건수는
다음 알림 머리에 "(앞서 N건 생략)" 으로 적는다. 웹훅이 죽어도 요청 처리는 영향이 없다.

**화면 오류도 온다 (로그인한 보호자만).** 보호자 폰 · 브라우저에서 화면이 깨지면 오류 바운더리가
`POST /client-errors` 로 예외 종류 · digest · 화면 경로 · 기기 요약(`android 14 app`)만 보내고, 서버가
ERROR 로 찍어 같은 알림이 간다 (`apps/web/src/lib/report-render-error.ts` ·
`app/api/v1/routers/client_errors.py`). 예외 메시지 · User-Agent 전체 · 쿼리 문자열은 못 들어오고
(서버가 모양을 검사한다), DB 에 넣지 않는다. 로그인 전 화면(로그인 · 동의)의 오류는 그 기기의
콘솔에만 남는다 — 인증 없는 창구는 아무나 팀 채널을 울릴 수 있어서다.

**못 잡는 것** — `uvicorn` · `sqlalchemy` 로거, `alembic` · `scripts/*` 같은 다른 프로세스, 로그인 전
화면의 브라우저 오류. 부팅 실패와 컨테이너 죽음은 아래 "컨테이너 감시" 가 잡는다. Discord 가 조용하다고
다 괜찮은 건 아니다 (문서 §5).

켜는 법:

1. Discord 채널 설정 › 연동 › 웹훅 › 새 웹훅 (이름 `api-alert`) → 웹훅 URL 복사
2. 서버 `.env` 의 `ALERT_WEBHOOK_URL=` 에 붙여 넣고 api 를 재시작. 웹훅 주소 모양
   (`https://discord.com/api/webhooks/…`)이 아니면 서버가 뜨지 않는다 — 채널 링크를 넣은 것이다

🚨 **웹훅 URL 은 비밀이다.** 아는 사람은 누구나 그 채널에 글을 올릴 수 있다. `.env` 에만 두고 GitHub ·
Discord 메시지에 붙이지 않는다 (루트 CLAUDE.md §9). **`APP_ENV=local` 이면 주소가 있어도 알림이
꺼진다** — 노트북의 오류가 팀 채널로 가지 않게. `make test` 도 `.env` 에 무엇이 있든 보내지 않는다
(`tests/__init__.py`).

로컬에서 한 번 확인하려면 `.env` 에 URL 을 넣고 아래를 돌린다 (`APP_ENV=dev` 로 local 규칙을 비켜
간다). 서버가 뜰 때와 같은 순서로 설정을 읽고 ERROR 하나를 찍는다 — Discord 에
`[dev] ERROR app.probe:<module>:1 — 알림 시험` 이 뜨면 된다.

```bash
cd apps/api && APP_ENV=dev uv run python -c "import logging, app.main; logging.getLogger('app.probe').error('알림 시험'); logging.shutdown()"
```

### 컨테이너 감시 (배포 실패 · redis 죽음 · 재시작 반복)

위 알림은 api 가 살아 있을 때만 말할 수 있다. 컨테이너가 못 뜨거나 redis 가 죽은 건 밖에서 봐야 해서,
서버의 cron(정해진 시각마다 명령을 돌리는 리눅스 장치)이 1분마다
[`deploy/scripts/health-alert.sh`](../../deploy/scripts/health-alert.sh) 를 돌린다. 배포 compose 의
컨테이너 전부의 상태(docker healthcheck 결과)를 읽어 **지난번과 달라진 것만** 같은 웹훅으로 한 줄
보낸다 — `[prod] ktc4-web: running healthy → exited -`, 복구되면 `… → running healthy`. 웹훅 전송이
실패하면 다음 분에 다시 보낸다. 1분마다 돌아도 `docker inspect` 몇 밀리초라 부담이 없다 (healthcheck
자체가 이미 5~30초마다 돈다).

서버 준비 — `deploy/docker/.env` 에 `ALERT_WEBHOOK_URL=` (api 의 `.env` 와 같은 값) 을 넣고 `crontab -e`:

```
* * * * * cd /home/ubuntu/ktc4-pusan-3 && deploy/scripts/health-alert.sh >> /var/tmp/ktc4-health-alert.log 2>&1
```

로컬에서 보려면 개발 DB compose 를 가리켜 `--dry-run` 으로 돌린다 (웹훅 대신 화면에 찍는다).
`ktc4-postgres` 를 멈췄다 켜면서 다시 돌리면 달라진 줄만 찍힌다.

```bash
deploy/scripts/health-alert.sh --dry-run deploy/docker/docker-compose.yml
```

---

## 디렉토리 구조

```
apps/api/
├── pyproject.toml           의존성 · ruff · pytest 설정 (uv 관리)
├── uv.lock                  의존성 버전 고정 — 반드시 커밋
├── .env.example             환경변수 템플릿 (실제 값 없음)
├── app/
│   ├── main.py              FastAPI 앱 진입점, include_router 등록
│   ├── core/
│   │   ├── config.py        pydantic-settings 로 .env 읽기
│   │   ├── logging_config.py  app.* 로그를 stderr 로 (시간 · 레벨 · 이름) · 접근 로그 가리기
│   │   └── alerts.py        ERROR 이상 → Discord 웹훅 (전송은 integrations/discord.py)
│   ├── api/
│   │   ├── health.py        운영용 헬스체크 (/api/v1 밖)
│   │   ├── deps/           인증 · 권한 · 동의 검사
│   │   └── v1/             도메인 엔드포인트 (다음 이슈에서 추가)
│   ├── domains/             도메인 모델 · 리포지토리 (다음 이슈에서 추가)
│   ├── agents/              Agent — 내부 구조는 AI Owner(이시하)가 결정
│   ├── rules/               규칙 로직 (순수 Python — DB·LLM 접근 금지)
│   ├── infra/db/            DB 세션 · 엔진 (다음 이슈에서 추가)
│   ├── providers/           외부 LLM SDK 래퍼
│   ├── integrations/        외부 API 연동 (카카오 · Discord 웹훅)
│   └── workers/             백그라운드 작업 (승인 없는 실행 경로 차단)
└── tests/
    ├── conftest.py           공통 픽스처 (ASGITransport AsyncClient)
    ├── integration/          HTTP 레이어 통합 테스트
    ├── unit/                 규칙·도메인·Agent 단위 테스트 (agents/ 는 Agent 별로 나눈다)
    ├── eval/                 실제 LLM 을 부르는 라이브 eval (-m live)
    └── fixtures/             테스트용 고정 데이터
```

---

## 문제가 생기면

### `ImportError: cannot import name 'extensions' from 'websockets'`

`uv sync` 는 성공했는데 서버를 띄울 때 이 에러가 나면, **uv 캐시가 깨진 것**입니다. 코드나 `uv.lock` 문제가 아닙니다. uv 는 캐시에서 하드링크로 패키지를 심기 때문에, 캐시가 손상되면 새 가상환경을 만들 때마다 같은 결함이 복사됩니다.

```bash
uv cache clean
rm -rf .venv
uv sync
```

### `make: command not found` (Windows)

`make` 는 Windows 기본 설치에 없습니다. `scoop install make` 또는 `choco install make` 로 설치하거나, WSL 안에서 작업하세요. 설치가 번거로우면 위 "앱 실행" 항목의 원본 명령어를 그대로 쓰셔도 **완전히 동일합니다**.

### 서버가 계속 재시작될 때

`--reload` 가 `.venv` 까지 감시하면 라이브러리 파일 변화에도 재시작합니다. `make dev` 는 `--reload-dir app` 으로 `app/` 만 보도록 설정돼 있으니, 직접 `uvicorn` 을 실행할 때도 이 옵션을 붙이세요.

### 포트가 이미 사용 중일 때

```bash
lsof -ti:8000 | xargs kill
```

---

## 다음에 붙일 것

현재 이 브랜치에는 다음이 **없습니다**. 별도 이슈에서 추가됩니다.

| 항목 | 상태 |
|------|------|
| PostgreSQL + pgvector 연결 | 다음 이슈 |
| SQLAlchemy ORM 모델 | 다음 이슈 |
| Alembic 마이그레이션 | 다음 이슈 |
| 도메인 API 엔드포인트 | 도메인별 이슈 |
| Agent 구현 | 이시하 담당 이슈 |
