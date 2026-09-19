# Alembic 마이그레이션 협업 규칙 v1

문서 목적: 팀 병렬 개발 중 마이그레이션 충돌·누락 없이 스키마를 관리하기 위한 규칙과 명령어를 고정한다.

기준 브랜치: `fix/be-30-integrate-enum`
작성일: 2026-09-10
담당: 김명성

---

## 평소에는 이것만

대부분은 아래 세 줄로 끝난다. §4 이후는 여기서 문제가 생겼을 때 펴보는 부분이다.

```bash
make db-migrate                   # 브랜치를 바꾼 뒤 한 번
make db-revision msg="한 줄 설명"  # 모델을 바꿨으면 — 생성된 파일은 반드시 열어서 확인 (§3)
make db-check                     # PR 올리기 전 — "No new upgrade operations detected"
```

**로컬 DB가 꼬였으면 고치려 하지 말고 `make db-reset` 으로 초기화한다.** 개발 DB에는 지켜야 할 데이터가 없다. §5의 복잡한 절차는 대부분 "데이터를 살리려고" 필요한 것이고, 로컬에서는 그럴 이유가 없다.

---

## 0. Alembic이란

**마이그레이션** = Python 코드(ORM 모델)와 실제 DB 테이블을 동기화하는 작업.

팀원이 모델 파일을 바꿔도 각자의 로컬 DB는 자동으로 바뀌지 않는다. 마이그레이션 파일이 그 "변경 명세서" 역할을 한다.

```
모델 파일 (Python)   →  마이그레이션 파일  →  실제 DB 스키마
 class Event(...)        upgrade()              CREATE TABLE event (...)
 starts_at: datetime     ALTER TABLE ...        ALTER TABLE event ADD COLUMN ...
```

Alembic은 마이그레이션 파일을 **revision** 단위로 관리하고, 각 revision은 이전 revision을 `down_revision`으로 가리켜 체인을 형성한다.

```
6016b3029fba  →  69c9a5bdfe8f  →  70e8ab2bf4b9  →  a1b2c3d4e5f6  (최신)
(pgvector)       (초기 테이블)     (memory 도메인)    (nickname 수정)
```

`make db-migrate`를 실행하면 아직 적용 안 된 revision을 순서대로 DB에 반영한다.

---

## 1. 원칙

- **스키마 변경과 마이그레이션은 반드시 같은 PR에 올린다.** 모델 파일만 바꾸고 migration 없이 머지하면 다음 사람이 `alembic check` 실패를 직접 겪게 된다.
- **`--autogenerate` 결과를 검토 없이 커밋하지 않는다.** 아래 §3 체크리스트를 반드시 확인한다.
- **PR 머지 전 `make db-check` 를 실행한다.** "No new upgrade operations detected" 가 나와야 한다.
- **로컬 개발 DB는 언제든 버릴 수 있는 상태로 둔다.** 로컬에만 있는 데이터에 의존하지 않는다. 꼬이면 `make db-reset`.
- **한 스프린트에 스키마를 바꾸는 PR이 여러 개면 순서를 정한다.** 동시에 만들지 않으면 §5의 충돌 자체가 생기지 않는다.

---

## 2. 명령어 치트시트

| 명령어 | 동작 | 언제 |
|--------|------|------|
| `make db-migrate` | 모든 pending migration 적용 (`upgrade heads`) | 브랜치 전환 후, PR 리뷰 전 |
| `make db-revision msg="설명"` | `--autogenerate` 로 새 revision 파일 생성 | 스키마 변경 후 |
| `make db-check` | ORM 모델과 DB 스키마 일치 검증 | PR 올리기 전 필수 |
| `make db-reset yes=1` | 로컬 DB를 비우고 처음부터 다시 적용 | 로컬이 꼬였을 때 |
| `make db-current` | 현재 DB에 적용된 revision 확인 | 상태 확인 |
| `make db-heads` | 파일 기준 head 목록 | 충돌 판정 |
| `make db-history` | 전체 revision 체인 출력 | 충돌 분석 |
| `make db-rollback` | 마지막 migration 한 단계 되돌리기 | 직전 migration 취소 (§5.3) |
| `make db-merge msg="설명"` | 갈라진 head를 합치는 revision 생성 | §5.4 |
| `make db-psql` | 컨테이너 안 psql 접속 | 실제 스키마 확인 |

---

## 3. 새 마이그레이션 만들기

### autogenerate란

`make db-revision`은 내부적으로 `alembic revision --autogenerate`를 실행한다.

동작 방식:
1. `alembic/env.py`에 import된 모든 모델을 읽어 "현재 코드의 스키마"를 파악
2. 실제 DB에 연결해 "현재 DB의 스키마"를 조회
3. 둘을 비교해 차이를 Python 코드(`upgrade()` / `downgrade()`)로 자동 작성

```python
# 자동 생성된 파일 예시 (alembic/versions/xxxx_add_column.py)

def upgrade() -> None:
    op.add_column("event", sa.Column("location", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("event", "location")
```

- `upgrade()` = 이 migration을 앞으로 적용할 때 실행
- `downgrade()` = 이 migration을 되돌릴 때 실행 (`make db-rollback`)

### 실행 순서

```bash
# 1. DB가 최신 상태인지 확인
make db-migrate

# 2. 모델 파일 수정 후 자동 생성
make db-revision msg="한 줄 설명"
#   → alembic/versions/ 에 새 파일이 생긴다. 반드시 열어서 내용 확인 필요.

# 3. 생성된 파일 검토 (아래 체크리스트)

# 4. 적용 테스트
make db-migrate

# 5. 스키마 일치 확인
make db-check
#   → "No new upgrade operations detected" 가 나와야 정상
```

### 검토 체크리스트

생성된 `alembic/versions/*.py` 파일을 열어 아래를 확인한다.

- [ ] `upgrade()` 와 `downgrade()` 가 서로 역연산인가
- [ ] **생성된 파일을 실제로 `make db-migrate` 로 한 번 실행해봤는가** (§4.1은 실행해야만 드러난다)
- [ ] `drop_table` / `drop_column` 이 있다면, 삭제가 맞는가 이름 변경인가 (§4.2)
- [ ] `upgrade()` 본문이 `pass` 인데 모델은 바꿨다면 §4.3 항목인가
- [ ] `nullable=False` 컬럼을 추가할 때 기존 행의 기본값이 처리되었는가

---

## 4. autogenerate 결과를 그대로 믿으면 안 되는 것

autogenerate는 "모델 ↔ DB 차이"를 비교해 코드를 써준다. **항목마다 감지 범위가 다르고, 실패하는 방식도 다르다.**

| 분류 | 무슨 일이 생기나 | 언제 드러나나 |
|---|---|---|
| 감지는 되는데 **코드가 불완전** (§4.1) | 파일은 생기는데 실행하면 죽는다 | `make db-migrate` 할 때 |
| 감지는 되는데 **의도와 다른 코드** (§4.2) | 에러 없이 돌아가고 데이터가 사라진다 | 한참 뒤에 |
| 아예 **감지되지 않음** (§4.3) | 본문이 `pass` 이고 `make db-check` 도 통과한다 | 운이 나쁘면 배포 후 |

> 아래는 프로젝트와 같은 alembic 1.19.2 로 재현한 결과다(2026-09-13).

### 4.1 감지는 되지만 코드 보완이 필요한 것 — PostgreSQL 전용 타입

`ARRAY`, `JSONB`, `DATERANGE`, `vector` 는 **autogenerate가 감지한다.** (`70e8ab2bf4b9` 에 이미 자동 생성돼 들어가 있다) 문제는 **생성된 코드가 그대로 실행되지 않는다**는 것이다.

```python
# 파일 상단 import — 이게 전부다
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade() -> None:
    op.add_column("memo", sa.Column("tags", postgresql.ARRAY(Text()), nullable=True))
    #                                                       ^^^^^^ sa. 접두어 없음
    op.add_column("memo", sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), ...))
    #                                            ^^^^^^^^ 이 모듈 import 없음
```

**파일을 열어봐도 멀쩡해 보이고 파이썬 import도 성공한다.** 문제가 되는 이름이 함수 본문 안에 있어서, `make db-migrate` 로 실행할 때가 되어서야 죽는다.

```
NameError: name 'Text' is not defined
```

보완: `import pgvector.sqlalchemy.vector` 추가, `Text()` → `sa.Text()`.

**기존 컬럼의 타입 변경은 `USING` 이 빠진다.** 타입 비교는 기본으로 켜져 있어 변경 자체는 감지되지만, 렌더러는 `postgresql_using` 을 넣지 않는다. `text` → `jsonb` 처럼 암묵 캐스팅이 안 되는 변환은 PostgreSQL이 거부한다.

```python
op.alter_column("memo", "payload",
                type_=postgresql.JSONB(astext_type=sa.Text()),
                existing_type=sa.Text(),
                postgresql_using="payload::jsonb")   # ← 직접 추가
```

### 4.2 감지는 되지만 의도와 다르게 나오는 것 — 이름 변경

autogenerate는 "이름이 바뀌었다"를 이해하지 못한다. **없어진 것 + 새로 생긴 것**으로 본다.

```python
# 테이블명 변경: observation_food → food_observation
op.create_table("food_observation", ...)
op.drop_table("observation_food")          # ← 기존 데이터 전부 삭제

# 컬럼명 변경: serving_size → portion_size
op.add_column("observation_food", sa.Column("portion_size", ...))
op.drop_column("observation_food", "serving_size")   # ← 기존 값 전부 삭제
```

에러 없이 실행되고 데이터만 사라진다. rename 연산으로 교체한다.

```python
def upgrade() -> None:
    op.rename_table("observation_food", "food_observation")
    op.alter_column("food_observation", "serving_size", new_column_name="portion_size")

def downgrade() -> None:
    op.alter_column("food_observation", "portion_size", new_column_name="serving_size")
    op.rename_table("food_observation", "observation_food")
```

### 4.3 아예 감지되지 않는 것

`upgrade()` 본문이 비어 있어도 실제로는 변경이 필요한 경우다. **`make db-check` 도 통과하므로 직접 챙겨야 한다.**

| 항목 | 이유 |
|---|---|
| 기존 컬럼의 `server_default` 변경 | `compare_server_default` 가 꺼져 있음 (아래) |
| `Enum` / `CHECK` 제약 | autogenerate의 비교 대상이 아님 |
| `GRANT` / 권한 | DDL 권한은 Alembic 관할 밖 |
| `CREATE EXTENSION` | 첫 revision에 직접 삽입 필요 |

#### `server_default` 변경은 지금 설정에서 조용히 무시된다

`compare_server_default` 기본값은 `False` 이고 `alembic/env.py` 에 이 옵션이 없다.

```
모델의 server_default 를 "off" → "on" 으로 변경

make db-check     → No new upgrade operations detected.   ← 통과해버린다
make db-revision  → upgrade() 본문이 pass
```

`context.configure(...)` 에 `compare_server_default=True` 를 넣으면 `op.alter_column(..., server_default="on", ...)` 으로 정상 생성된다.

> **바로 켜지 않은 이유.** PostgreSQL은 기본값을 `'{}'::text[]` 형태로 저장해서 모델의 `server_default="{}"` 와 문자열 비교가 어긋난다. `ARRAY ... server_default='{}'` 컬럼이 여러 개라 켜자마자 `make db-check` 가 깨질 수 있다. 별도 작업으로 다루고, **그때까지는 기본값을 바꿀 때 migration을 직접 작성한다.**

#### enum / CHECK 제약 (수동 작성 예시)

```python
# upgrade()
op.execute("ALTER TABLE suggestion DROP CONSTRAINT IF EXISTS suggestion_status")
op.execute("ALTER TABLE suggestion ADD CONSTRAINT suggestion_status CHECK (status IN ('draft', 'approved', 'rejected', 'expired', 'new_value'))")

# downgrade()
op.execute("ALTER TABLE suggestion DROP CONSTRAINT IF EXISTS suggestion_status")
op.execute("ALTER TABLE suggestion ADD CONSTRAINT suggestion_status CHECK (status IN ('draft', 'approved', 'rejected', 'expired'))")
```

> 제약 이름은 SQLAlchemy `Enum(name=..., create_constraint=True)` 의 `name=` 값으로 결정된다.
> 실제 DB에 등록된 이름 확인:
> ```sql
> SELECT conname FROM pg_constraint
> WHERE conrelid = 'suggestion'::regclass AND contype = 'c';
> ```

---
## 5. revision 체인 충돌 처리

두 사람이 같은 revision을 부모로 삼는 migration을 만들면 체인이 갈라지고, 아래 에러로 upgrade가 막힌다.

```
FAILED: Multiple head revisions are present for given argument 'head'
```

표기 (실제로 겪은 `fix/be-30` 상황의 revision ID):

```
A = 공통 부모                        70e8ab2bf4b9
B = develop에 먼저 머지된 migration   a1b2c3d4e5f6
C = 내 브랜치의 migration             c1d2e3f4a5b6

A ┬── B  (develop)
  └── C  (내 브랜치)     ← head 2개
```

> **로컬 개발 DB에서 났다면 `make db-reset` 후 §5.2 로 가는 게 가장 빠르다.**
> 아래 절차는 지울 수 없는 데이터가 있거나, 이미 다른 사람에게 공유된 경우를 위한 것이다.

> 이 절의 동작은 alembic 1.19.2 로 재현해 확인했다(2026-09-13).

---

### 5.1 어떤 상황인지 먼저 확인한다

**해결 방법은 파일 상태가 아니라 "C를 어느 DB까지 실제로 실행했는지"로 갈린다.**

| 확인할 것 | 명령 | 보는 것 |
|---|---|---|
| 파일 기준 head 개수 | `make db-heads` | 갈래가 몇 개인가 |
| 내 DB에 적용된 revision | `make db-current` | **두 줄 이상이면 내 DB에 두 갈래가 다 적용된 것** |
| 전체 체인 | `make db-history` | 어디서 갈라졌는지 |

```
make db-current 결과에 C가 있는가?
│
├── 없다 ──────────────────────────────→ §5.2  down_revision 수정
│
└── 있다
    ├── 내 로컬에만 있고 C 한 줄 ───────→ §5.3  롤백 후 수정
    └── 공유됐거나, 두 줄이 나온다 ─────→ §5.4  merge revision
```

**애매하면 §5.4(merge)로 간다.** 롤백은 되돌릴 수 없지만 merge revision은 잘못 만들어도 파일 하나 지우면 끝이다.

---

### 5.2 아직 아무 DB에도 C를 안 돌린 경우

되돌릴 상태가 없으므로 파일만 고치면 된다.

```bash
git fetch origin
git rebase origin/develop

# C 파일(alembic/versions/c1d2e3f4a5b6_*.py)의 down_revision 을 B로 수정
#   down_revision = "70e8ab2bf4b9"  →  "a1b2c3d4e5f6"
#   docstring의 "Revises:" 주석도 같이 고친다

make db-heads     # head가 1개인지 확인
make db-migrate
make db-check
```

---

### 5.3 C를 내 로컬에서만 돌린 경우

`make db-reset` 후 §5.2 로 가면 가장 간단하다. 로컬 데이터를 살려야 한다면 아래 순서를 지킨다.

**`롤백 → rebase → down_revision 수정 → 재적용` 순서가 핵심이다.**

```bash
make db-rollback     # C 파일이 아직 A를 가리키는 상태에서 먼저 실행
make db-current      # A 로 내려왔는지 확인

git fetch origin && git rebase origin/develop
# C 파일의 down_revision 을 B로 수정

make db-migrate      # 이번엔 B → C 순서로 실제 실행된다
make db-check
```

#### 순서를 바꾸면 에러 없이 어긋난다

`down_revision` 을 먼저 고치고 롤백하면:

```
[1] C만 적용된 상태            버전 = C,  테이블 = A것, C것
[2] down_revision 을 B로 수정
[3] make db-rollback           버전 = B,  테이블 = A것      ← B는 실행된 적이 없다
[4] make db-migrate            버전 = C,  테이블 = A것, C것  ← B의 테이블은 영영 안 생긴다
```

Alembic은 [3]에서 "한 칸 내렸으니 이제 B"라고 기록만 하고 B의 `upgrade()` 는 건너뛴다. 이후 `make db-migrate` 를 해도 B는 이미 적용된 것으로 본다.

**다만 `make db-check` 가 이 상태를 잡아준다.**

```
FAILED: New upgrade operations detected: [('add_table', Table('t_b', ...))]
```

§1의 "PR 전 `make db-check` 필수"는 이 사고를 잡는 장치다.

> **⚠️ 롤백은 데이터를 지운다.** `downgrade()` 에 `drop_column` / `drop_table` 이 있으면 그 데이터는 복구되지 않는다. 롤백 전에 해당 파일의 `downgrade()` 를 열어 확인한다.

---

### 5.4 공유됐거나, 두 갈래가 이미 다 적용된 경우 — merge revision

`make db-current` 가 두 줄이면 `make db-migrate`(`upgrade heads`)가 두 갈래를 다 적용한 상태다. 에러가 나지 않으므로 본인은 갈라진 줄 모른다.

**이 경우 롤백도, `down_revision` 수정도 하면 안 된다.** 이미 C를 적용한 팀원 DB에서 재현한 결과:

```
팀원 X:  버전 = C,  테이블 = A것, C것
  ↓  내가 C의 down_revision 을 고쳐서 push, X가 pull
X가 make db-migrate 실행
  ↓
버전 = C,  테이블 = A것, C것        ← 아무것도 실행되지 않는다
```

X는 이미 head에 있으므로 할 일이 없다고 판단한다. **B가 X의 DB에 영영 적용되지 않고 에러도 경고도 나지 않는다.**

대신 **B와 C를 부모로 갖는 revision을 하나 위에 얹는다.** B·C 파일은 건드리지 않으므로 이미 찍힌 버전이 그대로 유효하다.

```bash
git fetch origin
git rebase origin/develop     # C 파일의 down_revision 은 그대로 둔다

make db-heads                 # 이미 누가 merge를 만들어뒀는지도 여기서 확인
make db-merge msg="merge a1b2c3 and c1d2e3"
make db-heads                 # 1개로 줄었는지 확인

make db-migrate
make db-check
```

생성되는 파일은 **부모가 둘**인 것만 다르고, 본문이 비어 있는 게 정상이다.

```python
revision: str = "dd29a142089f"
down_revision: Union[str, Sequence[str], None] = ("a1b2c3d4e5f6", "c1d2e3f4a5b6")

def upgrade() -> None:
    pass
```

- `alembic merge` 는 DB에 접속하지 않는다. 파일만 보고 만들며, 실제 반영은 `make db-migrate` 시점이다.
- **생성된 파일은 반드시 커밋해 PR에 포함한다.** 안 올리면 다른 사람 쪽 head가 계속 2개다.
- 각자 DB 상태가 달라도 merge 하나로 수렴한다. C만 적용된 DB는 B를 실행한 뒤, B만 적용된 DB는 C를 실행한 뒤 같은 지점으로 올라간다.
- head가 3개 이상이어도 `make db-merge` 한 번으로 전부 합쳐진다.

---

### 5.5 merge revision을 쓴 뒤 알아둘 것

- **`make db-rollback` 이 더 이상 안 된다.** merge revision이 head가 되면 `downgrade -1` 이 `FAILED: Ambiguous walk` 로 막힌다. 되돌리려면 목표를 명시한다: `cd apps/api && uv run alembic downgrade c1d2e3f4a5b6`
- **B와 C가 같은 객체를 건드렸다면 merge로도 안 된다.** merge는 체인 모양만 합친다. 같은 컬럼을 둘 다 추가했다면 head가 1개여도 실행에서 깨진다. 이때는 **merge revision의 빈 `upgrade()` 안에 조정 DDL을 직접 쓴다.** (`upgrade heads` 의 갈래 실행 순서는 보장되지 않으므로, 순서에 의존하는 변경도 여기서 정리한다)
- 실패하면 중간 상태로 멈출 수 있다. 이어서 작업하기 전에 `make db-current` 로 실제 위치를 확인한다.

---

### 5.6 하지 말아야 할 것

| 하지 말 것 | 무슨 일이 생기나 |
|---|---|
| 이미 적용한 revision 파일 삭제 | `Can't locate revision` 으로 `db-current`·`db-migrate`·`db-rollback`·`db-check` 가 전부 죽는다. 파일을 되살리는 게 1순위고, 안 되면 `uv run alembic stamp <버전> --purge` (일반 `stamp` 는 같은 에러로 죽는다). **스키마는 되돌아가지 않으니 남은 객체는 직접 정리해야 한다.** |
| 공유된 revision의 `down_revision` 수정 | §5.4 — 남의 DB에 migration이 영영 적용되지 않는다 |
| `make db-current` 가 두 줄인 상태에서 `make db-rollback` | 경고만 내고 두 갈래 중 한쪽이 임의로 내려간다 (`downgrade -1 from multiple heads is ambiguous`) |
| revision ID를 손으로 짓기 | `a1b2c3d4e5f6` / `c1d2e3f4a5b6` 처럼 한 글자 차이가 되어 `down_revision` 오타를 유발한다 |

#### 에러 메시지 → 원인 → 대처

| 에러 | 원인 | 대처 |
|---|---|---|
| `Multiple head revisions are present...` | 체인이 갈라짐 | §5.1 |
| `Can't locate revision identified by '<id>'` | 적용된 revision의 파일이 사라짐 | §5.6 |
| `Ambiguous walk` | merge revision에서 `downgrade -1` | §5.5 |
| `Target database is not up to date.` | 적용 안 된 migration이 있음 | `make db-migrate` |
| `New upgrade operations detected: [...]` | 모델에는 있는데 DB에 없음 | §5.3 순서 사고이거나 §4.3 |
| head는 1개인데 `db-migrate` 실패 | 두 migration의 내용 충돌 | §5.5 |

> **검토 과제.** `make db-migrate` 가 `upgrade heads`(복수형)라서 갈라진 상태를 에러 없이 통과시킨다. `head`(단수)로 바꾸면 갈라진 순간 바로 막혀 일찍 발견할 수 있다. CI에서 `alembic heads` 가 1줄인지 검사하는 것도 같이 검토한다.

---

## 6. PR 체크리스트

PR을 올리기 전 아래를 확인한다.

- [ ] `make db-check` → "No new upgrade operations detected"
- [ ] `make db-heads` → head가 1개
- [ ] migration 파일이 PR에 포함되어 있는가 (merge revision을 만들었다면 그것도)
- [ ] `downgrade()` 가 구현되어 있는가
- [ ] 이미 공유된 revision의 `down_revision` 을 고치지는 않았는가 (§5.4)
- [ ] §4 보완 항목(전용 타입 import · rename · server_default)을 확인했는가

---

## 7. env.py 모델 등록

새 도메인 테이블을 추가할 때 `alembic/env.py` 의 import 목록에 해당 모델을 추가해야 autogenerate가 인식한다.

```python
# alembic/env.py — 모델 import 목록
from app.domains.child import models as _child_models  # noqa: F401
from app.domains.new_domain import models as _new_domain_models  # noqa: F401  ← 추가
```

추가하지 않으면 `make db-revision` 을 실행해도 새 테이블이 migration에 포함되지 않는다.
