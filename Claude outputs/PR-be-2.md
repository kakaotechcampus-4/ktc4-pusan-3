## 이번에 한 일

`apps/api` 뼈대를 잡고 FastAPI 앱이 뜨는 지점까지 만들었습니다. **기능 코드는 없습니다.**

> 파일 대부분이 **빈 폴더의 `.gitkeep`(31개)** 입니다. 실제 코드는 6개 파일 약 40줄이고, 나머지는 설정과 문서입니다.

- [x] 디렉토리 스켈레톤 생성 (`.gitkeep` 만)
- [x] `apps/api/CLAUDE.md` — 레이어 경계 · 소유 범위 · ORM/raw SQL 기준
- [x] uv 세팅 (`pyproject.toml`, `uv.lock` 커밋 — 6명 동일 버전 고정)
- [x] `GET /api/v1/health` + 통합 테스트 1개
- [x] 루트 `Makefile`, `apps/api/README.md` (실행법 · 트러블슈팅)
- [x] 루트 `CLAUDE.md` §6 구조 표 갱신 (**§6 외 수정 없음**)

이번 PR에 **없는 것** (별도 이슈 · 다른 Owner)

- [ ] `app/agents/` 내부 구조 — 이시하님
- [ ] `apps/web/` — 고태영님
- [ ] PostgreSQL + pgvector, Alembic, 도메인 모델
- [ ] import-linter + CI

---

## 실행 · 검증

`uv` 가 없으면 먼저 설치합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # macOS / Linux
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**1. 클론 · 세팅**

```bash
git clone -b feat/be-2-backend-dir https://github.com/kakaotechcampus-4/ktc4-pusan-3.git
cd ktc4-pusan-3
cp apps/api/.env.example apps/api/.env
make install
```

**2. 명령 목록 · 검사 · 테스트**

```bash
make          # 사용 가능한 명령 목록
make lint     # All checks passed!
make test     # 1 passed
```

**3. 실행 · 확인**

```bash
make dev                                  # Uvicorn running on http://127.0.0.1:8000
curl -s localhost:8000/api/v1/health      # {"status":"ok","env":"local"}
open http://localhost:8000/docs           # Swagger
```

**4. 종료**

```bash
lsof -ti:8000 | xargs kill
```

별도 디렉토리에 새로 클론해서 위 절차가 그대로 되는 것까지 확인했습니다.

> `uv sync` 후 `ImportError: cannot import name 'extensions' from 'websockets'` 가 나면 로컬 uv 캐시가 손상된 것입니다. `uv cache clean && rm -rf .venv && uv sync` 로 해결됩니다. `apps/api/README.md` 트러블슈팅에도 적어뒀습니다.

---

## 디렉토리 구조

`.gitkeep` 은 표시에서 뺐습니다. 파일이 없는 폴더에는 `.gitkeep` 만 들어 있습니다. `docs/`, `.github/` 는 기존 그대로라 생략했습니다.

```text
.
├── apps/
│   └── api/
│       ├── alembic/
│       │   └── versions/
│       ├── app/
│       │   ├── agents/                    Agent — 이시하 (내부 구조는 AI Owner 결정)
│       │   ├── api/                       라우터 · 스키마 — 김명성
│       │   │   ├── deps/
│       │   │   ├── v1/
│       │   │   │   ├── routers/
│       │   │   │   │   ├── __init__.py
│       │   │   │   │   └── health.py
│       │   │   │   ├── schemas/
│       │   │   │   └── __init__.py
│       │   │   └── __init__.py
│       │   ├── core/                      설정 · 예외 · 로깅
│       │   │   ├── __init__.py
│       │   │   └── config.py
│       │   ├── domains/                   모델 · 리포지토리 · 서비스
│       │   │   ├── child/
│       │   │   ├── consent/
│       │   │   ├── correction/
│       │   │   ├── identity/
│       │   │   ├── memory/
│       │   │   │   ├── observation/
│       │   │   │   └── profile/
│       │   │   ├── safety/
│       │   │   ├── schedule/
│       │   │   └── suggestion/
│       │   ├── infra/                     DB 세션 · 관찰가능성
│       │   │   ├── db/
│       │   │   └── observability/
│       │   ├── integrations/              외부 공공 API
│       │   │   ├── mfds/
│       │   │   └── neis/
│       │   ├── providers/                 외부 모델 SDK 격리
│       │   ├── rules/                     LLM 이 판단하지 않는 순수 계산
│       │   ├── workers/                   알림 발송 · 배치
│       │   ├── __init__.py
│       │   └── main.py
│       ├── scripts/
│       ├── tests/
│       │   ├── fixtures/
│       │   ├── integration/
│       │   │   ├── api/
│       │   │   │   ├── __init__.py
│       │   │   │   └── test_health.py
│       │   │   ├── db/
│       │   │   └── __init__.py
│       │   ├── unit/
│       │   │   ├── agents/
│       │   │   ├── domains/
│       │   │   └── rules/
│       │   ├── __init__.py
│       │   └── conftest.py
│       ├── .env.example
│       ├── CLAUDE.md
│       ├── pyproject.toml
│       ├── README.md
│       └── uv.lock
├── deploy/                                compose · nginx · 배포 스크립트
│   ├── docker/
│   ├── nginx/
│   └── scripts/
├── eval/                                  평가 케이스 — 오현식 · 이도헌
│   ├── cases/
│   └── runners/
├── .gitignore
├── CLAUDE.md
├── CONTRIBUTING.md
├── Makefile
└── README.md
```

각 폴더의 책임과 import 규칙은 `apps/api/CLAUDE.md` 에 적어뒀습니다.

---

## 보안 체크 (첫 커밋 5종)

| 항목 | 결과 |
|---|---|
| `.env` 추적 안 됨 | ✅ 0건 |
| 코드·문서에 키/접속 문자열 없음 | ✅ `.env.example` 은 `APP_ENV`, `APP_NAME` 뿐 |
| `uv.lock` 커밋됨 | ✅ |
| 운영진 소유 파일 4개 미수정 | ✅ 0건 |
| 인증 없는 엔드포인트 | `/api/v1/health` 1개 (헬스체크) |
