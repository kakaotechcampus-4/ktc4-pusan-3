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
| DB | PostgreSQL 17 + pgvector |
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
- **허용**: rules, core, sqlalchemy
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
- **허용**: domains, core, agents 진입점
- **금지**: agents 내부 구현 직접 import

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

## 금지

- `app/rules/` 안에서 DB 나 LLM 에 접근
- 요청받지 않은 파일 추가 생성
- `.github/workflows/{assign-mentor,notify-discord,convention-check}.yml` 과
  `.github/CODEOWNERS` 수정 (운영진 소유 — [루트 §8](../../CLAUDE.md#8-협업-규칙) 참조)
