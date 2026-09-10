# Alembic 마이그레이션 협업 규칙 v1

문서 목적: 팀 병렬 개발 중 마이그레이션 충돌·누락 없이 스키마를 관리하기 위한 규칙과 명령어를 고정한다.

기준 브랜치: `fix/be-30-integrate-enum`
작성일: 2026-09-10
담당: 김명성

---

## 1. 원칙

- **스키마 변경과 마이그레이션은 반드시 같은 PR에 올린다.** 모델 파일만 바꾸고 migration 없이 머지하면 다음 사람이 `alembic check` 실패를 직접 겪게 된다.
- **`--autogenerate` 결과를 검토 없이 커밋하지 않는다.** 아래 §3 체크리스트를 반드시 확인한다.
- **PR 머지 전 `make db-check` 를 실행한다.** "No new upgrade operations detected" 가 나와야 한다.

---

## 2. 명령어 치트시트

| 명령어 | 동작 | 언제 |
|--------|------|------|
| `make db-migrate` | 모든 pending migration 적용 (`upgrade heads`) | 브랜치 전환 후, PR 리뷰 전 |
| `make db-rollback` | 마지막 migration 한 단계 되돌리기 (`downgrade -1`) | 직전 migration 취소할 때 |
| `make db-current` | 현재 적용된 revision ID 확인 | 상태 확인 |
| `make db-history` | 전체 revision 체인 출력 | 충돌 분석 |
| `make db-check` | ORM 모델과 DB 스키마 일치 검증 | PR 올리기 전 필수 |
| `make db-revision msg="설명"` | `--autogenerate` 로 새 revision 파일 생성 | 스키마 변경 후 |

```bash
# 새 마이그레이션 만들기 예시
make db-revision msg="add observation_food.serving_size column"
```

---

## 3. 새 마이그레이션 만들기

```bash
# 1. DB가 최신 상태인지 확인
make db-migrate

# 2. 모델 파일 수정 후 자동 생성
make db-revision msg="한 줄 설명"

# 3. 생성된 파일 반드시 검토 (아래 체크리스트)

# 4. 적용 테스트
make db-migrate

# 5. 스키마 일치 확인
make db-check
```

### 검토 체크리스트

생성된 `alembic/versions/*.py` 파일을 열어 아래를 확인한다.

- [ ] `upgrade()` 와 `downgrade()` 가 서로 역연산인가
- [ ] 아래 §4 "autogenerate가 잡지 못하는 것" 항목이 빠지지 않았는가
- [ ] `server_default` 가 있는 컬럼에 올바른 기본값이 설정되었는가
- [ ] `nullable=False` 컬럼을 추가할 때 기존 행의 기본값이 처리되었는가

---

## 4. autogenerate가 잡지 못하는 것

아래 항목은 Alembic이 자동으로 감지하지 못한다. **직접 손으로 작성해야 한다.**

| 항목 | 이유 |
|------|------|
| `Enum` / `CHECK` 제약 | `native_enum=False` 사용 시 autogenerate가 변경을 감지 못함 |
| `DATERANGE`, `ARRAY`, `JSONB` 타입 변경 | PostgreSQL 전용 타입은 autogenerate 지원 범위 밖 |
| `vector` 컬럼 | pgvector 타입은 별도 extension, autogenerate 인식 불가 |
| `GRANT` / 권한 | DDL 권한은 Alembic 관할 밖 |
| `CREATE EXTENSION` | 첫 revision에 직접 삽입 필요 |

**enum 컬럼 변경 예시** (autogenerate가 감지 못하므로 수동 추가):

```python
# upgrade()
op.execute("ALTER TABLE suggestion DROP CONSTRAINT IF EXISTS suggestion_status_check")
op.execute("ALTER TABLE suggestion ADD CONSTRAINT suggestion_status_check CHECK (status IN ('draft', 'approved', 'rejected', 'expired', 'new_value'))")

# downgrade()
op.execute("ALTER TABLE suggestion DROP CONSTRAINT IF EXISTS suggestion_status_check")
op.execute("ALTER TABLE suggestion ADD CONSTRAINT suggestion_status_check CHECK (status IN ('draft', 'approved', 'rejected', 'expired'))")
```

---

## 5. revision 체인 충돌 처리

팀원이 동시에 같은 revision을 parent로 갖는 migration을 만들면 `Multiple head revisions` 에러가 발생한다.

```
ERROR: Multiple head revisions are present for given argument 'head'
```

### 원인

```
70e8ab2bf4b9  (공통 부모)
├── a1b2c3d4e5f6  (팀원 A — develop에 먼저 머지됨)
└── c1d2e3f4a5b6  (내 브랜치)   ← 충돌
```

### 해결 순서

```bash
# 1. 두 head 확인
make db-history

# 2. 내 브랜치를 develop에 rebase
git fetch origin
git rebase origin/develop

# 3. 내 migration 파일의 down_revision을 develop의 head로 수정
#    c1d2e3f4a5b6_*.py 에서:
#    down_revision = "70e8ab2bf4b9"  →  down_revision = "a1b2c3d4e5f6"

# 4. 체인 확인
make db-history   # head가 하나인지 확인

# 5. 적용 + 검증
make db-migrate
make db-check
```

---

## 6. PR 체크리스트

PR을 올리기 전 아래를 확인한다.

- [ ] `make db-check` → "No new upgrade operations detected"
- [ ] migration 파일이 PR에 포함되어 있는가
- [ ] `down_revision` 이 develop의 최신 head를 가리키는가 (`make db-history` 로 확인)
- [ ] `downgrade()` 가 구현되어 있는가 (롤백 가능 여부)
- [ ] §4 수동 항목이 필요한 변경인 경우 직접 작성했는가

---

## 7. env.py 모델 등록

새 도메인 테이블을 추가할 때 `alembic/env.py` 의 import 목록에 해당 모델을 추가해야 autogenerate가 인식한다.

```python
# alembic/env.py — 모델 import 목록
from app.domains.child import models as _child_models  # noqa: F401
from app.domains.new_domain import models as _new_domain_models  # noqa: F401  ← 추가
```

추가하지 않으면 `make db-revision` 을 실행해도 새 테이블이 migration에 포함되지 않는다.
