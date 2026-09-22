# Medication — 복약 일정

> ⚠ **이 문서는 [`health_agent_own_table.md`](health_agent_own_table.md)에 흡수됐다 — 정본은 그쪽이다.** 2026-09-22 결정은 양쪽에 반영했고, 미결 번호는 정본 기준(M-11 게이트 범위 · M-12 중단 시 복용 기록 · M-13 처방전 카드 = 승인 · M-14 수정 시 복용 기록).

> 기준 문서: [`data_model.md`](../data_model.md) (표 형식·제약 표기) · [`5agents.md`](../shared/5agents.md) §5 Health Agent · [`5agents_구현.md`](../shared/5agents_구현.md) §5 (event 이관)
>
> **소유 Agent: Health.** 이 도메인의 두 테이블은 Health Agent 가 직접 CRUD 한다. 다른 Agent 는 읽지 않는다.
>
> **2026-09-22 변경** — 복약 알림 **생성·수정은 초안 payload 로 나가고 보호자가 제출할 때 저장된다** (§7). **중단은 soft delete** (`status='stopped'`). `status` 는 `active` / `stopped` / `completed` 이고 `draft` · `expires_at` · `replaces_id` 는 없다.

---

## 1. 왜 테이블을 둘로 나누나

하나의 약(title)에 여러 알림이 붙는다 — 하루 3~4회, 또는 2주 이상 반복. 한 테이블로 두면

- `title` · `dosage` · 복약 기간이 알림 행마다 반복되고,
- "약 끊었어" 를 처리할 때 어느 행까지 손대야 하는지가 애매해진다.

그래서 **복약 코스 1건(`medication_schedule`) + 그 안의 복용 시점 N개(`medication_dose`)** 로 나눈다. Health Agent 는 이 쌍을 **하나의 단위**로 다룬다 (코스 없이 dose 만 만들지 않는다).

---

## 2. 기준 시각 — 시스템 상수

보호자는 "식후", "자기 전" 처럼 **시점**으로 말한다. 시각으로 말하는 경우가 오히려 적다.

기준 시각은 **시스템 전역 상수**다. 보호자별·아이별로 다르게 두는 필드가 아니다. 저장은 `기준점 + 오프셋(분)` 두 값으로 하고, 실제 알림 시각은 DB 가 계산한다.

| `timing_anchor` | 기준 시각 | 의미 |
| --- | --- | --- |
| `wake_up` | 07:30 | 기상 |
| `breakfast` | 08:00 | 아침식사 시작 |
| `lunch` | 12:00 | 점심식사 시작 |
| `dinner` | 18:00 | 저녁식사 시작 |
| `bedtime` | 20:30 | 취침 |
| `fixed` | — | 보호자가 시각을 직접 지정. `fixed_time` 사용 |

`offset_minutes` 는 **부호 있는 분**이다. 식전 = 음수, 식후 = 양수.

- "아침식사 30분 후" → `breakfast` + `30` → **08:30**
- "저녁 먹기 30분 전" → `dinner` + `-30` → **17:30**

---

## 3. medication_schedule (복약 코스 1건)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()` |
| child_id | uuid | NOT NULL. FK → `child.id`, `ON DELETE CASCADE` |
| title | text | NOT NULL. 보호자 발화 그대로. "항생제" / "타이레놀" |
| dosage | text | nullable. "5ml" / "1포". 자유 텍스트, 원문 보존 |
| starts_on | date | NOT NULL |
| ends_on | date | nullable. NULL = 무기한(만성질환 상시 복약) |
| status | enum | `active` / `stopped` / `completed`, NOT NULL, default `active`. VARCHAR + CHECK. **행이 존재한다 = 보호자가 이미 제출했다.** 중단은 `stopped` 로 전환 |
| note | text | nullable. 보호자 자유 기술 |
| created_by | enum | `agent` / `caregiver`, NOT NULL. VARCHAR + CHECK |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now(), on update now() |

**제약**

- `CHECK (ends_on IS NULL OR ends_on >= starts_on)`
- **중단은 soft delete** — "약 끊었어" 는 `status='stopped'`. 행이 남아 복용 기록(`medication_dose_log`)이 보존되고, 발송만 멈춘다
- **승인 전 상태는 이 테이블에 없다** — 초안은 payload 로 나가고, 보호자가 제출해야 행이 생긴다 (§7)
- **발송 잡은 `status='active'` 인 코스의 dose 만 읽는다**
- 아이 파기 시 연쇄 삭제 (`event` · `calendar` 와 동일 패턴)

---

## 4. medication_dose (복용 시점 1개)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()` |
| schedule_id | uuid | NOT NULL. FK → `medication_schedule.id`, `ON DELETE CASCADE` |
| timing_anchor | enum | `fixed` / `wake_up` / `breakfast` / `lunch` / `dinner` / `bedtime`, NOT NULL. VARCHAR + CHECK |
| offset_minutes | smallint | NOT NULL, default `0`. 부호 있는 분. 식전 = 음수, 식후 = 양수. `-120 ~ +180` |
| fixed_time | time | nullable. `timing_anchor='fixed'` 일 때만 값 |
| days_of_week | smallint[] | NOT NULL, default `'{}'`. **빈 배열 = 매일.** ISO 1=월 … 7=일 |
| is_active | boolean | NOT NULL, default `true` |
| scheduled_time | time | **GENERATED ALWAYS … STORED.** 기준 시각 + `offset_minutes`. 발송 잡이 읽는 유일한 값 |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now(), on update now() |

**제약**

- `CHECK ( (timing_anchor = 'fixed') = (fixed_time IS NOT NULL) )` — 시각 지정과 anchor 지정이 섞이지 않는다
- `CHECK ( timing_anchor <> 'fixed' OR offset_minutes = 0 )`
- `CHECK ( days_of_week <@ ARRAY[1,2,3,4,5,6,7]::smallint[] )`
- `UNIQUE (schedule_id, scheduled_time)` — 같은 약 같은 시각 중복 알림 차단
- 발송 조회용 부분 인덱스: `(scheduled_time) WHERE is_active`

### 발화 → 저장 예시

| 보호자 발화 | timing_anchor | offset_minutes | fixed_time | scheduled_time |
| --- | --- | --- | --- | --- |
| 아침식사 30분 후 | `breakfast` | 30 | – | 08:30 |
| 저녁 먹기 30분 전 | `dinner` | −30 | – | 17:30 |
| 점심 먹고 바로 | `lunch` | 0 | – | 12:00 |
| 자기 전에 | `bedtime` | 0 | – | 20:30 |
| 일어나자마자 | `wake_up` | 0 | – | 07:30 |
| 오후 3시에 | `fixed` | 0 | 15:00 | 15:00 |
| 하루 세 번 식후 | `breakfast` / `lunch` / `dinner` | 각 30 | – | 08:30 · 12:30 · 18:30 (**행 3개**) |

---

## 5. DDL

```sql
-- 복약 코스 1건
CREATE TABLE medication_schedule (
    id          uuid        PRIMARY KEY DEFAULT uuidv7(),
    child_id    uuid        NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    title       text        NOT NULL,
    dosage      text,
    starts_on   date        NOT NULL,
    ends_on     date,
    status      varchar(16) NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','stopped','completed')),
    -- 승인 전 값은 이 테이블에 없다. 초안은 payload 로 나가고 제출 때 INSERT 된다(§7)
    note        text,
    created_by  varchar(16) NOT NULL
                CHECK (created_by IN ('agent','caregiver')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE INDEX medication_schedule_child_idx
    ON medication_schedule (child_id, status);
```

```sql
-- 코스 안의 복용 시점 1개
CREATE TABLE medication_dose (
    id             uuid        PRIMARY KEY DEFAULT uuidv7(),
    schedule_id    uuid        NOT NULL REFERENCES medication_schedule(id) ON DELETE CASCADE,
    timing_anchor  varchar(16) NOT NULL
                   CHECK (timing_anchor IN
                          ('fixed','wake_up','breakfast','lunch','dinner','bedtime')),
    offset_minutes smallint    NOT NULL DEFAULT 0
                   CHECK (offset_minutes BETWEEN -120 AND 180),
    fixed_time     time,
    days_of_week   smallint[]  NOT NULL DEFAULT '{}',
    is_active      boolean     NOT NULL DEFAULT true,

    -- 기준 시각은 시스템 상수. 여기가 유일한 정의 지점이다
    scheduled_time time GENERATED ALWAYS AS (
        CASE timing_anchor
            WHEN 'fixed'     THEN fixed_time
            WHEN 'wake_up'   THEN TIME '07:30' + make_interval(mins => offset_minutes)
            WHEN 'breakfast' THEN TIME '08:00' + make_interval(mins => offset_minutes)
            WHEN 'lunch'     THEN TIME '12:00' + make_interval(mins => offset_minutes)
            WHEN 'dinner'    THEN TIME '18:00' + make_interval(mins => offset_minutes)
            WHEN 'bedtime'   THEN TIME '20:30' + make_interval(mins => offset_minutes)
        END
    ) STORED,

    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    CHECK ( (timing_anchor = 'fixed') = (fixed_time IS NOT NULL) ),
    CHECK ( timing_anchor <> 'fixed' OR offset_minutes = 0 ),
    CHECK ( days_of_week <@ ARRAY[1,2,3,4,5,6,7]::smallint[] ),
    UNIQUE (schedule_id, scheduled_time)
);

CREATE INDEX medication_dose_send_idx
    ON medication_dose (scheduled_time) WHERE is_active;
```

**생성 컬럼을 쓴 이유** — 기준 시각이 상수라면 앱이 계산해 저장할 필요가 없고, 계산 결과가 DB 와 코드에서 갈릴 여지도 없다. 발송 잡은 `scheduled_time` 만 보면 되고, `UNIQUE(schedule_id, scheduled_time)` 이 중복 알림을 DB 에서 막는다.

**대가** — 기준 시각 상수를 바꾸면 테이블 재작성 마이그레이션이 필요하다. 상수가 자주 바뀔 것 같으면 `scheduled_time` 을 평범한 `time NOT NULL` 로 두고 앱이 채우는 쪽으로 돌린다. 지금은 "시스템 내부적으로 미리 설정" 이 전제라 생성 컬럼을 택했다.

**발송 조건** — `medication_dose.is_active AND medication_schedule.status = 'active'`. dose 쪽 부분 인덱스만으로는 `completed` 코스를 걸러내지 못하므로 발송 쿼리는 **반드시 schedule 과 조인**한다.

---

## 6. 단일 writer 원칙 재정의

`5agents_구현.md` §0 의 **"Memory 가 단일 writer"** 를 이 테이블이 깬다. 원칙을 버리지 말고 기준을 다시 쓴다.

> **공유 테이블의 단일 writer 는 Memory 다.** `observation_*` · `profile_affinity` · `event` 는 여러 Agent 가 읽으므로 쓰기 통로가 하나여야 한다.
>
> **도메인 전용 테이블은 그 Agent 가 쓴다.** 다른 Agent 가 읽지 않는 테이블은 소유 Agent 가 CRUD 한다.

기준은 **누가 쓰느냐가 아니라 누가 읽느냐**다. 따라서 이 원칙이 유지되려면 **Food · Growth · Activity 는 `medication_*` 을 읽지 않아야 한다.** 지금 설계상 읽을 이유가 없다. "약 먹는 시간이랑 식사 시간이 겹쳐요" 같은 요구가 실제로 오면 그때 규칙을 다시 연다.

DB 권한도 같이 간다 — Agent role 에 `health_safety` write 는 계속 비부여, `medication_schedule` · `medication_dose` 만 부여.

---

## 7. 승인 게이트 — 초안 payload 로 내보내고 제출 때 저장

> **2026-09-22 변경.** 이전 판은 "`stop` 으로 되돌릴 수 있으니 게이트 밖" 이었다. 이미 울린 알림 한 번은 되돌릴 수 없고, 오인식된 약·시각이 확인 없이 발송되는 비용이 더 크다고 보고 **생성도 게이트를 거친다.** 방식은 `suggestion` 이 아니라 **`event` 초안**을 따른다 — 승인 전 값은 DB 에 넣지 않는다. 정본은 [`health_agent_own_table.md`](health_agent_own_table.md) §6.

```
보호자 발화 → Health 가 코스 + dose 를 초안 payload 로 만듦 → 확인 모달
                                          ├ 제출 → 백엔드가 INSERT (status='active', 발송 시작)
                                          └ 취소·이탈 → 아무 일도 없음 (run 과 함께 사라짐)
```

초안에는 `missing`(비어 있는 NOT NULL 칸 이름)이 실린다. 비어 있으면 화면이 제출 버튼을 잠그고, 보호자가 채워 보내야 INSERT 가 성립한다.

게이트가 생겨도 아래 세 규칙은 그대로 **코드로** 강제한다.

1. **실제 시각 표시 의무** — 확인 모달에 매핑 결과 시각을 반드시 노출한다. "아침식사 30분 후 → **08:30** 으로 잡았어요."
   보호자가 승인하는 대상은 발화가 아니라 **실제로 울릴 시각**이어야 한다. 프롬프트 규칙이 아니라 **출력 tool 의 필수 필드**로 박는다.
2. **약 이름·용량은 모델이 만들지 않는다** — `title` · `dosage` 는 보호자 발화에서 **복사만** 한다. 추정·보정·단위 환산 금지. (Food 의 "LLM 이 정확한 수치 생성 금지" 와 같은 규칙, 여기선 더 강하게)
3. **anchor 는 모델이 고르고 시각은 코드가 정한다** — 모델은 `fixed` 를 임의로 선택할 수 없다. 보호자가 시각을 명시한 경우에만 `fixed`.

### 수정도 게이트를 거친다 — 초안 payload

수정은 기존 코스를 바로 고치지 않는다. 고치는 순간 승인 전에 알림 시각이 바뀌기 때문이다.

```
"항생제 저녁은 7시로 바꿔줘"
  → 기존 코스(active)는 그대로 발송 유지
  → 초안 payload (op="update", schedule_id, before) → 확인 모달 (바뀐 시각 표시)
     ├ 제출 → 한 트랜잭션: 코스 UPDATE + dose 재생성
     └ 취소·이탈 → 기존 코스 그대로
```

같은 행을 UPDATE 하므로 `medication_dose_log` 가 살아남는다 — 수정할 때마다 먹인 기록이 사라지던 문제(M-14)가 여기서 닫힌다.

### 중단은 soft delete

`status='stopped'` 로 바꾼다. 행과 dose 를 남기므로 먹인 기록이 사라지지 않는다. 발송 잡이 `active` 만 읽으니 알림은 즉시 멈춘다. `event` 삭제(hard delete)와 방식이 다른 이유는 event 에는 남길 기록이 없고 복약에는 있기 때문이다.

### `5agents_구현.md` 에서 바뀌는 것

§5 "event 이관 — Health 가 주 고객" 이 절반만 남는다. 복약은 이관이 아니라 자체 테이블 CRUD 로 빠지고, **event 이관에 남는 건 재방문 · 검진 window 뿐**이다. §7 Health 항목의 "event 이관의 주 고객: 복약·재방문·검진 window" 에서 복약을 뺀다.

---

## 8. Health Agent 쪽 계약

`medication` 2차 라벨(`5agents_구현.md` §2 참고)이 여는 tool 묶음.

| tool | 방향 | 비고 |
| --- | --- | --- |
| `search_medication_schedules` | 읽기 | `status='active'` 기본. 코스 + dose 를 함께 돌려준다 |
| `create_medication_schedule` | 초안 | DB 를 건드리지 않고 **초안 payload** 를 만든다. `notice_times`(실제 알림 시각) 필수 |
| `update_medication_schedule` | 초안 | 기간·용량·시점 변경. 기존 코스를 건드리지 않고 `op="update"` 초안을 만든다. 바뀐 `notice_times` 필수 |
| `stop_medication_schedule` | 쓰기 | `status='stopped'` (soft delete). dose·복용 기록 보존 |
| `resolve_dose_timing` | **코드 tool** | 발화 표현 → `(timing_anchor, offset_minutes)`. 모델에게 보이지 않는다 |

제출은 tool 이 아니다 — 보호자가 확인 모달에서 누르면 백엔드가 INSERT(생성) 또는 UPDATE(수정)한다. 모델은 저장 시점을 건드리지 못한다.

`resolve_dose_timing` 을 코드 tool 로 두는 이유는 Food 의 `filter_food_safety` 와 같다 — 모델이 부르는 tool 이면 건너뛸 수 있고, 시각 해석은 규칙으로 고정돼야 한다.

---

## 9. 구현 메모

- PK 기본값 `uuidv7()` — 다른 테이블과 통일
- `status` · `created_by` · `timing_anchor` enum 3개는 VARCHAR + CHECK 로 구현 (identity / child / consent / event 와 통일)
- `make_interval` · `time + interval` 은 IMMUTABLE 이라 생성 컬럼에 쓸 수 있다. 마이그레이션에서 한 번 확인할 것
- `UNIQUE (schedule_id, scheduled_time)` 은 생성 컬럼 위의 유니크 — PostgreSQL 12+ 에서 동작
- `medication_schedule` 에 `source_refs` 를 두지 않았다. 복약은 보호자 발화에서 직접 나오므로 근거 추적 필요성이 낮다. 관찰과 이어야 할 상황이 생기면 그때 추가

---

## 10. 미결

| # | 쟁점 | 메모 |
| --- | --- | --- |
| M-1 | **알림 발송 경로** | 기존 `reminder.event_id` 가 NOT NULL 이라 재사용 불가. 발송 잡이 `medication_dose` 를 직접 조회하는 쪽으로 제안. `reminder` 는 event 전용으로 유지 |
| M-2 | **복용 기록(adherence)** | "먹였어" 를 어디에 남기나. `observation_health.action_taken` 으로 갈지 `medication_dose_log` 를 새로 팔지 미정. 이번 범위 밖 |
| M-3 | **연령대별 기준 시각** | 영아기는 취침이 20:30 보다 이르다. 상수를 연령대 2벌로 둘지 결정 필요 — 두는 순간 생성 컬럼을 포기해야 한다 |
| M-4 | **`route`(경구/외용/점안)** | 연고·안약도 알림 대상이지만 MVP 범위 밖. 필요해지면 `medication_schedule` 에 컬럼 추가 |
| M-5 | **`days_of_week` 가 실제로 쓰이나** | "격일 복용" 같은 요구가 없으면 죽은 컬럼이 된다. 첫 사용례가 나올 때까지 UI 노출은 하지 않음 |
| M-11 | ✅ **닫힘** — 수정은 초안 payload, 중단은 soft delete (2026-09-22) | |

---

## 부록 — `child_growth_log` 확정 반영

`data_model.md` Child 섹션의 "키/몸무게 저장하는 로그 테이블" 은 **스키마 확정**으로 간주한다.

| **필드** | **비고** |
| --- | --- |
| id | PK |
| child_id | FK → `child.id`, `ON DELETE CASCADE` |
| height | 키 |
| weight | 몸무게 |
| check_date | 측정일 |

수정하지 않고 **로그처럼 계속 쌓는다** (append). 같은 날 재측정도 새 행.

이 테이블을 전제로 tool 을 구현한다.

> **2026-09-22 변경** — Health 의 성장 백분위 판정이 제거되고, **이 테이블을 읽는 Agent 는 Growth · Food 둘**이 됐다. 성별은 어디서도 쓰지 않는다.

| Agent | 읽는 목적 | 넘지 않는 선 |
| --- | --- | --- |
| **Growth** | `growth_review` — 측정 기록 기반 **성장 추이 정리**. 대근육 활동의 난이도·안전 범위 조정. 성장 급등기·정체기를 식사·수면 루틴 변화의 **가능한 맥락**으로만 언급. 마지막 `check_date` 가 오래됐으면 "재보실래요?" 한 줄 | 백분위 산출·해석, "또래보다 크다/작다" 판정, 저성장 언급 |
| Food | **권장 열량 계산 입력** — 연령별 일반값에 키·몸무게를 반영. 측정이 없으면 연령별 일반값만 | 과체중·저체중 판정, 체중 조절 식단. 성별 미반영 |
| Health | **읽지 않음** | 성장 판정 기능 없음 |

**공통 규칙** — 측정값은 **읽기 전용**이다. 기록은 Memory 가 한다. "우리 애 키 잘 크고 있어?" 같은 **판정 요청도 Growth(`growth_review`)로 간다.** Growth 는 판정 문구 없이 추이만 정리하고, "성장 평가는 영유아 건강검진에서 확인해 보세요" 를 코드 템플릿으로 붙인다. 시스템 어디에서도 성장을 판정하지 않는다.

> 남은 확인 — `height` · `weight` 의 타입과 단위(cm / kg), `check_date` 의 NOT NULL 여부, 측정 출처(보호자 측정 vs 검진) 구분 컬럼이 필요한지. 셋 다 tool 구현 전에 닫아야 한다.
