# Alembic 마이그레이션 협업 규칙 v1

문서 목적: 팀 병렬 개발 중 마이그레이션 충돌·누락 없이 스키마를 관리하기 위한 규칙과 명령어를 고정한다.

기준 브랜치: `fix/be-30-integrate-enum`
작성일: 2026-09-10
담당: 김명성

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
| `make db-heads` | 파일 기준 head 목록 (`alembic heads`) | 충돌 판정, PR 올리기 전 |
| `make db-merge msg="설명"` | 갈라진 head를 합치는 merge revision 생성 | §5 케이스 3·4·6·7 |
| `make db-psql` | 컨테이너 안 psql 접속 | 실제 스키마 눈으로 확인 |

```bash
# 새 마이그레이션 만들기 예시
make db-revision msg="add observation_food.serving_size column"
```

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
- [ ] `drop_table` / `drop_column` 이 있다면, 삭제가 맞는가 이름 변경인가 (§4.2)
- [ ] PostgreSQL 전용 타입이 들어갔다면 import와 `sa.` 접두어가 맞는가 (§4.1)
- [ ] 기존 컬럼의 타입을 바꿨다면 `postgresql_using` 이 필요한 변환인가 (§4.1)
- [ ] `upgrade()` 본문이 `pass` 인데 모델은 바꿨다면 §4.3 항목인가
- [ ] `server_default` 가 있는 컬럼에 올바른 기본값이 설정되었는가 (기본값 *변경* 은 자동 감지되지 않는다 — §4.3)
- [ ] `nullable=False` 컬럼을 추가할 때 기존 행의 기본값이 처리되었는가
- [ ] 생성된 파일을 실제로 `make db-migrate` 로 한 번 실행해봤는가 (§4.1은 실행 시점에만 드러난다)

---

## 4. autogenerate 결과를 그대로 믿으면 안 되는 것

autogenerate는 "모델 ↔ DB 차이"를 비교해 코드를 써준다. **항목마다 감지 범위가 다르고, 실패하는 방식도 다르다.** 세 가지로 나눠서 본다.

| 분류 | 무슨 일이 생기나 | 어디 |
|---|---|---|
| 감지는 되는데 **코드가 불완전** | 파일은 생기는데 `make db-migrate` 에서 죽는다 | §4.1 |
| 감지는 되는데 **의도와 다른 코드** | 에러 없이 돌아가고 **데이터가 사라진다** | §4.2 |
| 아예 **감지되지 않음** | `upgrade()` 본문이 `pass` 로 비어 있고 `make db-check` 도 통과한다 | §4.3 |

> 아래 재현 결과는 프로젝트와 같은 alembic 1.19.2 기준이다(2026-09-13).

---

### 4.1 감지는 되지만 코드 보완이 필요한 것 — PostgreSQL 전용 타입

`ARRAY`, `JSONB`, `DATERANGE`, `vector` 는 **autogenerate가 감지한다.** 실제로 `70e8ab2bf4b9` 마이그레이션에 이 타입들이 이미 자동 생성돼 들어가 있다. 문제는 **생성된 코드가 그대로 실행되지 않을 수 있다**는 것이다.

모델에 네 타입의 컬럼을 추가하고 `make db-revision` 을 돌린 결과:

```python
# 파일 상단 import — 이게 전부다
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade() -> None:
    op.add_column("memo", sa.Column("tags", postgresql.ARRAY(Text()), nullable=True))
    op.add_column("memo", sa.Column("payload", postgresql.JSONB(astext_type=Text()), nullable=True))
    op.add_column("memo", sa.Column("observed_range", postgresql.DATERANGE(), nullable=True))
    op.add_column("memo", sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True))
```

두 군데가 깨져 있다.

- `Text()` 가 `sa.` 접두어 없이 렌더링된다 (`postgresql.ARRAY` / `JSONB` 안쪽)
- `pgvector.sqlalchemy.vector` 모듈 import가 없다

**파일을 열어봐도 멀쩡해 보이고 파이썬 import도 성공한다.** 이름이 함수 본문 안에 있어서, `make db-migrate` 로 실제 실행할 때가 되어서야 죽는다.

```
NameError: name 'Text' is not defined
```

보완:

```python
import pgvector.sqlalchemy.vector      # ← 추가

op.add_column("memo", sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True))
#                                                        ^^^ sa. 붙이기
```

#### 기존 컬럼의 타입 변경은 `USING` 이 빠진다

타입 비교(`compare_type`)는 **기본값이 `True`** 라서 타입 변경 자체는 감지된다. 다만 autogenerate 렌더러는 `type_=` 만 쓰고 **`postgresql_using` 은 절대 넣지 않는다.** PostgreSQL이 암묵적으로 캐스팅하지 못하는 변환(`text` → `jsonb` 등)은 그대로 돌리면 거부당한다.

```python
op.alter_column(
    "memo", "payload",
    type_=postgresql.JSONB(astext_type=sa.Text()),
    existing_type=sa.Text(),
    postgresql_using="payload::jsonb",   # ← 직접 추가
)
```

기존 행이 있는 테이블의 타입을 바꿀 때는 **변환식이 모든 기존 값에 대해 성공하는지** 먼저 확인한다.

---

### 4.2 감지는 되지만 의도와 다르게 나오는 것 — 이름 변경

autogenerate는 "이름이 바뀌었다"를 이해하지 못한다. **없어진 것 + 새로 생긴 것**으로 본다. 재현 결과:

```python
# 테이블명 변경: observation_food → food_observation
op.create_table("food_observation", ...)
op.drop_table("observation_food")          # ← 기존 데이터 전부 삭제

# 컬럼명 변경: serving_size → portion_size
op.add_column("observation_food", sa.Column("portion_size", sa.String(length=20), nullable=True))
op.drop_column("observation_food", "serving_size")   # ← 기존 값 전부 삭제
```

에러 없이 실행되고 데이터만 사라진다. **생성된 코드를 지우고 rename 연산으로 교체한다.**

```python
def upgrade() -> None:
    op.rename_table("observation_food", "food_observation")
    op.alter_column("food_observation", "serving_size", new_column_name="portion_size")

def downgrade() -> None:
    op.alter_column("food_observation", "portion_size", new_column_name="serving_size")
    op.rename_table("food_observation", "observation_food")
```

`drop_table` / `drop_column` 이 생성된 migration은 **의도한 삭제인지 이름 변경인지 반드시 확인한다.**

---

### 4.3 아예 감지되지 않는 것

`upgrade()` 본문이 비어 있어도 실제로는 변경이 필요한 경우다. **`make db-check` 도 통과하기 때문에 직접 챙기는 수밖에 없다.**

| 항목 | 이유 |
|---|---|
| 기존 컬럼의 `server_default` 변경 | `compare_server_default` 가 꺼져 있음 (아래) |
| `Enum` / `CHECK` 제약 | autogenerate의 비교 대상이 아님 |
| `GRANT` / 권한 | DDL 권한은 Alembic 관할 밖 |
| `CREATE EXTENSION` | 첫 revision에 직접 삽입 필요 |
| 이름만 다른 중복 인덱스 | 정의가 같아도 이름이 다르면 둘 다 유효한 객체로 남음 |

#### `server_default` 변경 — 지금 우리 설정에서는 조용히 무시된다

`compare_server_default` 의 기본값은 **`False`** 이고, `alembic/env.py` 의 `context.configure(...)` 에 이 옵션이 없다. 모델의 `server_default` 만 바꾸면 이렇게 된다.

```
모델: server_default="off"  →  server_default="on" 으로 변경

make db-check     → No new upgrade operations detected.     ← 통과해버린다
make db-revision  → upgrade() 본문이 pass (빈 migration)
```

옵션을 켜면 정상적으로 잡힌다.

```
make db-check     → FAILED: New upgrade operations detected: [... 'modify_default' ...]
make db-revision  → op.alter_column("cfg", "flag", server_default="on", existing_type=..., ...)
```

켜는 방법 (online / offline 양쪽 `context.configure` 에 모두 추가):

```python
# alembic/env.py
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    compare_server_default=True,     # ← 추가
)
```

> **켜기 전에 알아둘 것.** PostgreSQL은 기본값을 `'{}'::text[]` 처럼 캐스팅이 붙은 형태로 저장해서, 모델의 `server_default="{}"` 와 문자열 비교에서 어긋나 **실제 변경이 없는데도 diff가 잡히는 경우**가 있다. 우리 테이블에도 `ARRAY ... server_default='{}'` 컬럼이 여러 개라 켜자마자 `make db-check` 가 깨질 수 있다. 켜는 건 별도 작업으로 다루고, **그때까지는 기본값을 바꿀 때 migration을 직접 작성한다.**

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

팀원이 동시에 같은 revision을 parent로 갖는 migration을 만들면 체인이 갈라지고, 아래 에러로 모든 upgrade가 막힌다.

```
FAILED: Multiple head revisions are present for given argument 'head'; please specify a
specific target revision, '<branchname>@head' to narrow to a specific head, or 'heads' for all heads
```

이 절에서는 아래 표기를 쓴다. (실제로 겪은 `fix/be-30` 상황의 revision ID)

```
A = 공통 부모                        70e8ab2bf4b9
B = develop에 먼저 머지된 migration   a1b2c3d4e5f6
C = 내 브랜치의 migration             c1d2e3f4a5b6
```

```
A  70e8ab2bf4b9  (공통 부모)
├── B  a1b2c3d4e5f6   (develop)
└── C  c1d2e3f4a5b6   (내 브랜치)   ← head 2개
```

> 이 절의 동작은 alembic 1.19.2 로 전부 재현해서 확인했다(2026-09-13). 명령별 실제 출력은 §5.7 표 참고.

---

### 5.1 손대기 전에 상태부터 확인한다

**해결 방법은 파일 상태가 아니라 "C를 어느 DB까지 실제로 실행했는지"로 갈린다.** 같은 파일 상태여도 DB 상태가 다르면 절차가 완전히 달라지므로, 아무 명령도 치기 전에 아래 네 개를 먼저 본다.

| 확인할 것 | 명령 | 보는 것 |
|---|---|---|
| 파일 기준 head 개수 | `make db-heads` | `versions/` 기준 head가 몇 개인가 |
| 내 DB에 적용된 revision | `make db-current` | `alembic_version` 행. **두 줄 이상이면 내 DB에 head가 2개 적용된 상태** |
| 전체 체인 | `make db-history` | 어디서 갈라졌는지, 각 revision의 `Revises` |
| 갈라진 지점 | `cd apps/api && uv run alembic branches` | 분기 부모 revision |

---

### 5.2 케이스 판정

```
make db-current 결과에 C가 있는가?
│
├── 없다 ──────────────────────────────────────→ [케이스 1] 아직 아무 DB에도 안 돌림
│
└── 있다
    ├── C가 내 로컬에만 있다 (push 전이거나, 아무도 이 브랜치를 받아가지 않음)
    │   ├── db-current 가 C 한 줄 ────────────→ [케이스 2] 로컬에만 적용
    │   └── db-current 가 B, C 두 줄 ─────────→ [케이스 4] 로컬에 두 head 다 적용
    │
    └── C가 다른 사람 로컬·공유 DB·배포 DB에도 올라갔다 → [케이스 3] 공유됨
```

**판정이 애매하면 무조건 케이스 3(merge revision)으로 처리한다.** 롤백은 되돌릴 수 없지만 merge revision은 잘못 만들어도 파일 하나 지우면 끝이다.

---

### 5.3 [케이스 1] 아직 로컬에도 C를 안 돌려본 경우

`make db-current` 에 C가 없다. 되돌릴 상태가 없으므로 **파일의 `down_revision` 만 고치면 된다.**

```bash
# 1. 현재 head 확인
make db-heads
# a1b2c3d4e5f6 (head)
# c1d2e3f4a5b6 (head)

# 2. 내 브랜치를 develop에 rebase
git fetch origin
git rebase origin/develop

# 3. C 파일의 down_revision을 B로 수정
#    apps/api/alembic/versions/c1d2e3f4a5b6_*.py
#    down_revision = "70e8ab2bf4b9"   (A)
#                  ↓
#    down_revision = "a1b2c3d4e5f6"   (B)
#    docstring의 "Revises:" 주석도 같이 고친다.

# 4. head가 하나로 줄었는지 확인
make db-heads

# 5. 적용 + 검증
make db-migrate
make db-check
```

---

### 5.4 [케이스 2] C를 로컬에만 돌려본 경우

`make db-current` 가 C 한 줄을 반환하고, B는 아직 내 DB에 적용되지 않았다.

**순서가 전부다. 반드시 `롤백 → rebase → down_revision 수정 → 재적용` 순으로 한다.**

```bash
# 1. 먼저 롤백 (C 파일이 아직 A를 가리키고 있는 상태에서 해야 한다)
make db-current      # c1d2e3f4a5b6
make db-rollback     # C.downgrade() 실행
make db-current      # 70e8ab2bf4b9 (A) 로 내려왔는지 확인

# 2. rebase
git fetch origin
git rebase origin/develop

# 3. C 파일의 down_revision을 B로 수정 (+ docstring Revises 주석)

# 4. 재적용 — 이번엔 B.upgrade() → C.upgrade() 가 실제로 순서대로 실행된다
make db-heads        # head 1개
make db-migrate
make db-check
```

#### 순서를 바꾸면 안 되는 이유 (재현 결과)

`down_revision` 을 먼저 고치고 롤백하면, **에러 없이** DB 스키마와 `alembic_version` 이 어긋난다.

```
[1] C만 적용된 상태          version = C        테이블 = A것, C것
[2] down_revision 을 B로 수정 (파일 체인: A → B → C)
[3] make db-rollback         version = B        테이블 = A것        ← B.upgrade()는 실행된 적이 없다
[4] make db-migrate          version = C        테이블 = A것, C것    ← B의 테이블은 영원히 없음
```

Alembic은 [3]에서 "C를 한 칸 내렸으니 이제 B다"라고 기록만 한다. B의 `upgrade()` 는 건너뛴다. 이후 `make db-migrate` 를 해도 이미 B는 적용된 것으로 보기 때문에 **B가 만든 테이블·컬럼이 내 DB에 영원히 생기지 않는다.**

**마지막 방어선은 `make db-check` 다.** 위 상태에서 실행하면 이렇게 잡힌다.

```
FAILED: New upgrade operations detected: [('add_table', Table('t_b', ...))]
```

그래서 §1의 "PR 전 `make db-check` 필수"는 형식이 아니라 이 사고를 잡는 장치다.

> **⚠️ 롤백은 데이터를 지운다.**
> `downgrade()` 에 `drop_column` / `drop_table` 이 있으면 그 컬럼·테이블의 데이터는 복구되지 않는다. 롤백 전에 해당 revision 파일의 `downgrade()` 를 열어 무엇이 지워지는지 확인한다. 로컬 개발 DB라도 넣어둔 시드·테스트 데이터는 날아간다.

---

### 5.5 [케이스 3] C가 다른 사람 로컬이나 공유 DB에 이미 올라간 경우

**이때 롤백하면 안 된다.** 내가 내 DB에서 C를 내려도 다른 사람 DB의 `alembic_version` 은 그대로 C이고, 그 위에서 이미 작업 중일 수 있다.

**그리고 `down_revision` 을 고쳐서 push하는 것도 안 된다.** 이미 C를 적용한 사람의 DB에서 무슨 일이 벌어지는지 재현해봤다.

```
팀원 X:  version = C,  테이블 = A것, C것
  ↓  내가 C.down_revision 을 A→B 로 고쳐서 push, X가 pull
X가 make db-migrate 실행
  ↓
version = C,  테이블 = A것, C것        ← 아무것도 실행되지 않는다
```

Alembic 입장에서 X는 이미 head(C)에 있으므로 할 일이 없다. **B는 X의 DB에 영원히 적용되지 않고, 에러도 경고도 나지 않는다.** 이게 공유된 revision의 부모를 고치면 안 되는 이유다.

#### 해결: merge revision 으로 합류시킨다

B, C 파일은 그대로 두고 **두 head를 부모로 갖는 revision을 하나 위에 얹는다.** 기존에 찍힌 `alembic_version` 값이 전부 그대로 유효하다.

```bash
# 1. develop 최신을 받아온다 (B 파일이 있어야 merge 대상이 된다)
git fetch origin
git rebase origin/develop     # C 파일의 down_revision 은 A 그대로 둔다

# 2. 현재 head 두 개 확인 (이미 누가 merge를 만들어뒀는지도 여기서 확인 — 케이스 8)
make db-heads

# 3. merge revision 생성
make db-merge msg="merge a1b2c3 and c1d2e3"
#   내부적으로: uv run alembic merge -m "..." heads

# 4. head가 하나로 줄었는지 확인
make db-heads

# 5. 적용 + 검증
make db-migrate
make db-check
```

생성되는 파일은 **`down_revision` 이 튜플**인 것만 다르다.

```python
"""merge a1b2c3 and c1d2e3

Revision ID: dd29a142089f
Revises: a1b2c3d4e5f6, c1d2e3f4a5b6
"""

revision: str = "dd29a142089f"
down_revision: Union[str, Sequence[str], None] = ("a1b2c3d4e5f6", "c1d2e3f4a5b6")


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

- **본문이 비어 있는 게 정상이다.** 스키마를 바꾸지 않고 합류 지점만 표시한다. 단 B와 C가 같은 객체를 건드렸다면 여기에 조정 DDL을 직접 넣어야 한다(케이스 5).
- **`alembic merge` 는 DB에 접속하지 않는다.** `versions/` 만 보고 파일을 만들 뿐이라, DB가 꺼져 있어도 생성된다. 실제 반영은 `make db-migrate` 시점이다.
- **생성된 파일은 반드시 커밋해 PR에 포함한다.** 안 올리면 다른 사람 쪽 head가 계속 2개다.

#### `alembic_version` 테이블은 이렇게 변한다

```
merge 적용 전                        merge 적용 후
┌────────────────┐                  ┌────────────────┐
│ a1b2c3d4e5f6   │  2행             │ dd29a142089f   │  1행
│ c1d2e3f4a5b6   │                  └────────────────┘
└────────────────┘
```

head가 여러 개인 동안 `alembic_version` 은 head마다 한 행을 갖는다. merge 지점을 지나면 한 행으로 합쳐진다.

**각자 DB 상태가 달라도 merge 하나로 수렴한다.** C만 적용된 DB에서 `make db-migrate` 를 하면 B가 실행된 뒤 merge 지점으로 올라가고, B만 적용된 DB에서는 C가 실행된 뒤 같은 지점으로 올라간다. 재현해서 확인했다.

---

### 5.6 위 세 가지로 안 덮이는 케이스

케이스 1~3은 **"C를 어디까지 실행했는가"** 축 하나만 본다. 아래는 그 축 밖에 있어서 세 케이스 어디에도 정확히 들어맞지 않는다.

#### 케이스 4 — 내 로컬에 B와 C가 **둘 다** 적용된 경우

`make db-migrate` 는 `alembic upgrade heads`(복수형)다. 그래서 develop을 받아온 뒤 습관적으로 한 번 돌리면 **B와 C가 양쪽 다 적용되고 `alembic_version` 이 2행이 된다.** 에러가 안 나기 때문에 본인은 충돌 상태인 줄 모른다. 우리 팀에서 가장 자주 만날 상태다.

- 판정: `make db-current` 가 두 줄을 반환한다.
- 이 상태의 `make db-rollback`(`downgrade -1`)은 **막히지 않고 경고만 내고 실행된다.**
  ```
  UserWarning: downgrade -1 from multiple heads is ambiguous;
  this usage will be disallowed in a future release.
  ```
  재현했을 때 두 head 중 한쪽이 임의로 내려갔다. **어느 쪽이 내려갈지 보장되지 않으므로, 이 상태에서 `make db-rollback` 은 쓰지 않는다.**
- 해결: **케이스 3과 동일하게 merge revision.** B와 C가 실제로 둘 다 실행된 상태이므로 합류 지점만 얹으면 DB와 파일이 정확히 일치한다. 롤백할 이유가 없다.

#### 케이스 5 — B와 C가 **같은 객체**를 건드린 경우 (내용 충돌)

merge revision도 `down_revision` 수정도 **체인의 모양만** 고친다. 두 migration이 하는 일이 겹치면 head가 1개가 돼도 실행하는 순간 깨진다.

```
--- head는 1개 (체인 정상) ---
c1d2e3f4a5b6 (head)
--- 그런데 make db-migrate ---
sqlalchemy.exc.ProgrammingError: column "status" of relation "suggestion" already exists
```

| 상황 | 증상 |
|---|---|
| B와 C가 같은 컬럼을 추가 | `DuplicateColumn` |
| B가 테이블·컬럼명을 바꾸고 C가 옛 이름을 참조 | `UndefinedTable` / `UndefinedColumn` |
| B와 C가 같은 CHECK 제약을 서로 다른 정의로 생성 | 이름 충돌, 또는 나중 것이 앞의 것을 덮음 |
| 같은 인덱스를 서로 다른 이름으로 생성 | **에러 없이 중복 인덱스가 둘 다 남는다** (가장 위험) |

대처:

- 케이스 1·2라면 C의 `upgrade()` / `downgrade()` 본문을 직접 고친다. (B가 이미 추가한 컬럼이면 C에서 그 `add_column` 을 뺀다)
- 케이스 3·4라면 B, C는 그대로 두고 **merge revision의 `upgrade()` 안에 조정 DDL을 넣는다.** 비어 있는 merge revision에 본문을 쓰는 유일한 경우다.
- **실패하면 중간 상태로 멈출 수 있다.** 재현(SQLite)에서는 B까지 적용된 채 C에서 멈췄다. PostgreSQL은 DDL이 트랜잭션 안에서 돌기 때문에 통째로 롤백될 수도 있으니, 실패 후에는 반드시 `make db-current` 로 실제 위치를 확인하고 시작한다.
- 마지막에 `make db-check` 가 "No new upgrade operations detected" 를 내야 한다. 다만 중복 인덱스처럼 check가 못 잡는 것도 있으니 `make db-psql` 로 실제 스키마를 한 번 본다.

#### 케이스 6 — head가 3개 이상

브랜치 세 개가 동시에 갈라진 경우다. 두 개씩 나눠 merge할 필요 없이 한 번에 합친다.

```bash
make db-merge msg="merge three heads"
#   → down_revision = ("a1b2c3d4e5f6", "c1d2e3f4a5b6", "e5f6a1b2c3d4")
```

#### 케이스 7 — develop에 B와 C가 **둘 다 머지된** 경우

리뷰에서 못 잡고 두 PR이 다 머지되면 develop 자체가 head 2개가 된다. 모두의 기준 브랜치이므로 **누구도 롤백할 수 없고**, 선택지는 merge revision 하나뿐이다.

- merge revision만 담은 별도 PR(`fix(db): merge alembic heads`)로 올린다. 다른 변경을 섞지 않는다.
- 그 사이 develop을 받아간 사람들은 `make db-migrate` 때문에 전부 케이스 4 상태다. merge revision을 받으면 자동으로 정리된다.

#### 케이스 8 — 이미 누군가 merge revision을 만들어 둔 경우

`git fetch` 없이 각자 merge를 만들면 **merge revision이 두 개가 되어 head가 다시 2개로 갈라진다.** 재현했을 때 똑같은 `Multiple head revisions` 에러로 돌아갔다. merge를 만들기 전에 반드시 `git fetch` + `make db-heads` 로 현재 head를 확인한다. 이미 합류 지점이 있으면 `make db-migrate` 만 하면 된다.

#### 케이스 9 — 적용된 revision 파일을 지우거나 새로 뽑은 경우

"충돌났으니 그냥 지우고 다시 만들자"가 가장 흔한 사고 경로다. `alembic_version` 에 있는 revision ID의 파일이 사라지면 **거의 모든 alembic 명령이 죽는다.**

```
$ make db-current    FAILED: Can't locate revision identified by 'c1d2e3f4a5b6'
$ make db-migrate    FAILED: Can't locate revision identified by 'c1d2e3f4a5b6'
$ make db-rollback   FAILED: Can't locate revision identified by 'c1d2e3f4a5b6'
$ make db-check      FAILED: Can't locate revision identified by 'c1d2e3f4a5b6'
```

- **1순위: 파일을 되살린다.** `git checkout <커밋> -- apps/api/alembic/versions/<파일>` 로 복구한 뒤 `make db-rollback` 으로 정상적으로 내리고 다시 작업한다.
- 되살릴 수 없으면 최후 수단으로 버전 테이블을 직접 맞춘다. **`stamp` 만으로는 안 되고 `--purge` 가 필요하다** (일반 `stamp` 도 현재 revision을 해석하려다 같은 에러로 죽는다).
  ```bash
  cd apps/api && uv run alembic stamp 70e8ab2bf4b9 --purge
  ```
- **`stamp` 는 스키마를 되돌리지 않는다.** 재현에서 C가 만든 테이블은 그대로 남은 채 버전만 A로 바뀌었다. 남은 객체는 `make db-psql` 로 직접 지워야 한다.
- 그래서 원칙은 **"이미 적용한 revision 파일은 지우지 않는다."** 지우려면 롤백이 먼저다. 케이스 1(아무 DB에도 안 돌림)에서만 삭제 후 재생성이 안전하다.

---

### 5.7 충돌을 다루면서 같이 알아야 할 것

| 항목 | 확인된 동작 | 그래서 |
|---|---|---|
| merge revision이 head가 된 뒤 `make db-rollback` | `FAILED: Ambiguous walk` — `downgrade -1` 이 아예 안 된다 | 되돌리려면 목표를 명시한다: `uv run alembic downgrade c1d2e3f4a5b6` |
| merge 아래로 내려가면 | `alembic_version` 이 다시 2행으로 갈라진다 | 정상 동작이다. 다시 `make db-migrate` 하면 1행으로 합쳐진다 |
| `upgrade heads` 의 실행 순서 | 갈라진 두 브랜치의 실행 순서는 보장되지 않는다 (재현에서 C → B 순으로 실행됨) | 순서에 의존하는 변경은 케이스 5로 보고 직접 조정한다 |
| `down_revision` 을 고쳤는데 `make db-heads` 결과가 그대로 | `alembic/versions/__pycache__` 의 오래된 `.pyc` 를 읽을 수 있다 (수정 전후 파일 크기가 같고 같은 초에 저장되면 발생) | `rm -rf apps/api/alembic/versions/__pycache__` 후 다시 확인 |
| revision ID | 우리 저장소의 `a1b2c3d4e5f6` / `c1d2e3f4a5b6` 는 손으로 지은 값이다. 한 글자 차이라 `down_revision` 에 오타 내기 쉽다 | 새 revision의 ID는 손으로 짓지 말고 alembic이 생성한 값을 그대로 쓴다 |

#### 에러 메시지 → 원인 → 대처

| 에러 | 원인 | 대처 |
|---|---|---|
| `Multiple head revisions are present for given argument 'head'` | 체인이 갈라짐 | §5.1 부터 |
| `Can't locate revision identified by '<id>'` | DB에 적용된 revision 파일이 사라짐 | 케이스 9 |
| `Ambiguous walk` | merge revision에서 `downgrade -1` | §5.7, 목표 revision 명시 |
| `UserWarning: downgrade -1 from multiple heads is ambiguous` | 내 DB에 head 2개 적용 | 케이스 4 — 롤백 말고 merge |
| `Target database is not up to date.` (`make db-check`) | 적용 안 된 migration이 있음 | `make db-migrate` 먼저 |
| `New upgrade operations detected: [...]` (`make db-check`) | 모델에는 있는데 DB에 없음 | 케이스 2의 순서 사고이거나 §4 누락 |
| `DuplicateColumn` / `UndefinedTable` (head는 1개인데 실패) | 내용 충돌 | 케이스 5 |

> **검토 과제: `make db-migrate` 의 `upgrade heads`(복수형).**
> 복수형은 충돌 상태를 에러 없이 통과시켜 케이스 4를 만든다. `upgrade head`(단수)로 두면 head가 2개일 때 즉시 막히고, 충돌을 빨리 발견할 수 있다. merge revision을 적용한 뒤에는 head가 1개이므로 단수형으로도 문제없다. 팀 논의 후 결정한다.

---

## 6. PR 체크리스트

PR을 올리기 전 아래를 확인한다.

- [ ] `make db-check` → "No new upgrade operations detected"
- [ ] migration 파일이 PR에 포함되어 있는가
- [ ] `make db-heads` → head가 1개인가
- [ ] `make db-current` → 한 줄인가 (두 줄이면 §5 케이스 4)
- [ ] `down_revision` 이 develop의 최신 head를 가리키는가 (`make db-heads` 로 확인)
- [ ] 이미 공유된 revision의 `down_revision` 을 고치지는 않았는가 (§5 케이스 3)
- [ ] merge revision을 만들었다면 그 파일이 PR에 포함되어 있는가
- [ ] `downgrade()` 가 구현되어 있는가 (롤백 가능 여부)
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
