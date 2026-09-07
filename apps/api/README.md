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
# 저장소 루트에서
git clone <repo-url>
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
curl http://localhost:8000/api/v1/health
# 예상 응답: {"status":"ok","env":"local"}
```

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

## 디렉토리 구조

```
apps/api/
├── pyproject.toml           의존성 · ruff · pytest 설정 (uv 관리)
├── uv.lock                  의존성 버전 고정 — 반드시 커밋
├── .env.example             환경변수 템플릿 (실제 값 없음)
├── app/
│   ├── main.py              FastAPI 앱 진입점, include_router 등록
│   ├── core/
│   │   └── config.py        pydantic-settings 로 .env 읽기
│   ├── api/
│   │   └── v1/routers/      HTTP 엔드포인트 (현재: health)
│   ├── domains/             도메인 모델 · 리포지토리 (다음 이슈에서 추가)
│   ├── agents/              Supervisor · 도메인 Agent · Curator — 이시하
│   ├── rules/               규칙 로직 (순수 Python — DB·LLM 접근 금지)
│   ├── infra/               DB · 관찰가능성 인프라 (다음 이슈에서 추가)
│   ├── providers/           외부 LLM SDK 래퍼
│   ├── integrations/        외부 API 연동 (NEIS, MFDS)
│   └── workers/             백그라운드 작업 (승인 없는 실행 경로 차단)
└── tests/
    ├── conftest.py           공통 픽스처 (ASGITransport AsyncClient)
    ├── integration/          HTTP 레이어 통합 테스트
    ├── unit/                 규칙·도메인 단위 테스트
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
