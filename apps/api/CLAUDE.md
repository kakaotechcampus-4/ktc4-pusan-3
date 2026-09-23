# apps/api — 백엔드 구현 상세

> 절대 규칙(안전·기억·실행·개인정보)은 [루트 §2](../../CLAUDE.md#2-절대-규칙) 참조.
> 자율성 경계(누가 무엇을)는 [루트 §3](../../CLAUDE.md#3-자율성-경계--누가-무엇을-하는가) 참조.
> 처리 파이프라인은 [루트 §4](../../CLAUDE.md#4-처리-파이프라인) 참조.

---

## 스택

| 항목 | 버전·선택 |
|------|----------|
| 언어 | Python 3.12 |
| 웹 프레임워크 | FastAPI |
| ORM | SQLAlchemy 2.0 (async, asyncpg) |
| 마이그레이션 | Alembic |
| DB | PostgreSQL 18 + pgvector |
| 패키지 관리 | uv |
| 테스트 | pytest |
| 린트·포맷 | ruff |
| import 규칙 강제 | import-linter (CI) |

---

## 레이어 경계

import-linter 로 CI 에서 강제한다. 위반 = PR 차단.

### `app/rules/`
- **허용**: 표준 라이브러리만
- **금지**: fastapi, sqlalchemy, agents, providers 전부
- **책임**: [루트 §3](../../CLAUDE.md#3-자율성-경계--누가-무엇을-하는가) "코드(규칙)가 하는 것" 전부
  — 날짜·나이 계산, 일정 충돌, 알레르기·금지식품 필터, 감쇠, 반복 승격

### `app/domains/`
- **허용**: rules, core, infra/db, sqlalchemy
- **금지**: fastapi, agents, providers, integrations

### `app/agents/`
- **허용**: domains, rules, providers
- **금지**: fastapi, infra 직접 접근
- ⚠️ **내부 구조는 이시하(AI Owner)가 정한다.** 이 문서는 경계만 정하고 하위 폴더를
  미리 만들지 않았다 (`CLAUDE.md` §8 "기능 내부 기술 결정 → 해당 기능 Owner").
  LangGraph 등 채택 프레임워크에 따라 구성이 달라질 수 있다.

**단, 아래 한 가지는 파트 경계라 협의 대상이다.**
- Agent 는 DB·외부에 **한 계층(tool 계층)을 통해서만** 닿는다.
  도메인 Agent 가 `domains/*/repository` 를 직접 import 하지 않는다.
- 이유: `docs/` 의 Tool 권한 매트릭스를 코드로 강제하려면 통로가 하나여야 한다.
- 그 계층의 이름·형태는 AI Owner 가 정한다.

### `app/api/`
- **허용**: domains, core, agents 진입점, integrations
- **금지**: agents 내부 구현 직접 import
- **agents 진입점은 `app/agents/entrypoint.py` 하나다.** `handle_input` 과 그 입출력·이벤트 타입을
  여기서 내보낸다. `pipeline` 이나 `memory`·`food` 하위 모듈을 직접 import 하지 않는다.
- **DB 예외**: `app/api/deps/db.py` 한 곳만 `app/infra/db/session.py` 를 import 한다.
  라우터는 엔진이나 `get_session` 을 직접 가져오지 않고 `SessionDep` 를 받는다.
- FastAPI 전용 오류 모델·예외 핸들러는 `app/api/errors.py` 에 둔다. `app/core/` 에
  FastAPI·Starlette 의존성을 추가하지 않는다.

### `app/integrations/`
- 카카오 OAuth·NEIS·MFDS처럼 서비스 밖의 HTTP API 호출을 격리한다.
- 카카오 요청 URL, 타임아웃, 응답 파싱, 외부 오류 변환은 `app/integrations/kakao/`가 맡는다.
- **허용**: core, 사용하는 외부 SDK·HTTP 클라이언트
- **금지**: api, domains, agents, infra import
- 인증 라우터는 카카오 integration을 호출할 수 있지만, 카카오 응답 모양을 다른 도메인으로
  퍼뜨리지 않는다.

### `app/providers/`
- 외부 모델 SDK 전용
- **금지**: domains, agents import

### `app/workers/`
- **허용**: domains, rules, core
- **금지**: agents import
  — 승인 없는 알림 발송 경로를 구조적으로 차단 ([루트 §2 실행](../../CLAUDE.md#실행))

---

## 소유

| 경로 | Owner |
|------|-------|
| `app/api/`, `app/domains/`, `app/infra/`, `app/core/` | 김명성 |
| `app/agents/` | 이시하 |
| `app/rules/` | **공동** — 변경 시 양쪽 리뷰 필수 |
| `eval/` (루트) | 오현식 · 이도헌 |

경계를 넘는 변경은 두 Owner 협의 대상이다.
`app/api/` ↔ `app/agents/` 사이도 파트 경계다.

---

## ORM vs raw SQL

| 케이스 | 방식 |
|--------|------|
| 단건 INSERT / UPDATE / 조회 | SQLAlchemy ORM |
| 집계·검색·복잡 조인 | `text()` raw SQL, `.sql` 파일을 리포지토리 옆에 배치 |

raw SQL 대상 예시: 승격 판정 집계, observation 4테이블 병합 페이징, pgvector 유사도 검색, 영양소 기간 집계.

**판단 기준**: `EXPLAIN` 을 봐야 하는 쿼리면 SQL 로 쓴다.

---

## Alembic

- 스키마 변경은 반드시 **마이그레이션과 같은 PR** 에 올린다.
- `--autogenerate` 결과를 확인 없이 커밋하지 않는다.
  자동 인식 안 되는 것: `enum`, `CHECK` 제약, `daterange`, `vector` 타입, `GRANT`
- 첫 revision 에 `CREATE EXTENSION IF NOT EXISTS vector` 를 직접 넣는다.

---

## 카카오 OAuth #34 구현 하네스

이 절은 #34 작업자가 폴더 위치나 기본 보안 규칙을 다시 결정하느라 멈추지 않게 하는 실행
기준이다. 아래 값은 백엔드 Owner가 확정했으므로 다시 검토 질문으로 되돌리지 않는다.

### 이미 확정한 경계

| 대상 | 구현 위치와 규칙 |
|------|------------------|
| 6개 인증 라우트 | `app/api/v1/routers/auth.py`. 루트 보안 목록의 5개만 무인증이고 `/auth/logout`만 Bearer 인증이다. |
| 현재 보호자 주입 | `app/api/deps/auth.py`. 라우터는 ORM 객체가 아닌 `CurrentParent`만 받는다. |
| DB 세션 연결 | `app/api/deps/db.py`만 infra 세션을 import하고, 모든 인증 라우터는 `SessionDep`를 받는다. |
| 카카오 HTTP 호출 | `app/integrations/kakao/`. 토큰 교환·회원번호 조회·토큰 폐기·타임아웃·외부 오류 변환을 맡는다. |
| DB 모델 | `app/domains/identity/`. `session`과 `auth_handoff` 모델 및 저장 규칙을 둔다. |
| DB 스키마 | 모델 변경과 같은 PR의 새 Alembic revision. 원문 토큰·인가 코드·bind는 컬럼에 저장하지 않는다. |
| 공통 API 오류 | `app/api/errors.py`. JSON 엔드포인트만 오류 봉투를 쓰고, 302 엔드포인트 실패는 허용된 복귀 URL로 오류 코드만 보낸다. |
| 환경 설정 | `app/core/config.py`와 `.env.example`. 실제 키나 실제 배포 URL은 커밋하지 않는다. |

### 구현할 때 지킬 순서

1. `Settings`와 `.env.example`에 `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`,
   `KAKAO_CALLBACK_URL`, `KAKAO_API_TIMEOUT`, `AUTH_RETURN_URL_WEB`,
   `AUTH_RETURN_URL_APP`을 추가한다. `KAKAO_ADMIN_KEY`는 실제 연결 끊기 구현이 이 PR
   범위에 포함될 때만 읽는다.
2. `session`·`auth_handoff` 모델과 migration을 먼저 만들고, 저장값이
   `docs/api/auth-kakao-v1.md` §5와 같은지 확인한다.
3. 카카오 호출 코드는 integration에 구현하고 HTTP 응답은 테스트에서 스텁으로 바꿀 수 있게
   클라이언트 경계를 한 곳으로 모은다.
4. 라우터 6개를 계약서 순서대로 구현한다. 일회용 코드 소비와 신규 가입은 각각 하나의 DB
   트랜잭션 안에서 끝낸다.
5. `docs/api/auth-kakao-v1.md` A-01~A-20을 자동 테스트로 옮긴 뒤 수동 검증 M-01·M-02로
   배포·실기기 경로를 확인한다.

### 자동 검증

DB를 보지 않는 테스트는 `client`, DB를 읽거나 쓰는 테스트는 `db_client` fixture를 쓴다.
DB 테스트를 기본 테스트에서 제외하지 않는다.

```bash
make db-up
make test
make lint
```

- `make test`는 실제 PostgreSQL에 연결한다. 각 DB 테스트는 바깥 트랜잭션을 마지막에
  롤백하므로 테스트끼리 데이터를 공유하지 않는다.
- 카카오 서버를 실제 호출하는 테스트는 만들지 않는다. integration 응답을 스텁으로 바꾸고
  호출 여부와 입력값을 함께 검증한다.
- M-01은 배포 환경의 시작 URL과 콜백 URL이 같은 오리진인지 확인한다.
- M-02는 iOS·Android 실기기에서 앱 복귀와 웹뷰 세션 생성을 확인한다.

### 혼자 바꾸지 않고 확인할 때

- 문서의 경로, 요청·응답 필드, 상태 코드처럼 웹·앱과 맞물린 API 계약을 바꿔야 할 때는
  김명성·고태영과 먼저 맞춘다.
- 신규 `parent`는 필수 동의를 검증한 signup 트랜잭션에서만 생성한다. 동의 전에
  생성하도록 이 순서를 바꾸려면 PM 결정을 받는다.
- 배포 오리진, 카카오 콘솔 등록값, 앱 딥링크처럼 저장소 밖 값을 모르면 임의의 값을 넣지
  않고 M-01·M-02의 미검증 항목으로 남긴다.
- 그 밖의 함수 분리, 내부 이름, 테스트 파일 분리는 #34 작업자가 결정한다.

---

## 금지

- `app/rules/` 안에서 DB 나 LLM 에 접근
- 요청받지 않은 파일 추가 생성
- `.github/workflows/{assign-mentor,notify-discord,convention-check}.yml` 과
  `.github/CODEOWNERS` 수정 (운영진 소유 — [루트 §8](../../CLAUDE.md#8-협업-규칙) 참조)
