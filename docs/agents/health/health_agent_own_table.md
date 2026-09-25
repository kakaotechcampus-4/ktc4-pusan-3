# Health Agent 소유 테이블

> `medication_schedule.md`를 흡수하고, 복약 기록·복약 이유·전달 서류에 필요한 컬럼을 더한 **정본**.
>
> **2026-09-22 갱신** — 생성·수정은 **초안 payload**(DB 저장 없음, `event` 초안과 같은 방식) · 중단 soft delete(`status='stopped'`) · `child_growth_log`는 Health가 더 이상 읽지 않음
> 관련: [`Health_Agent_명세.md`](Health_Agent_명세.md) · [`Health_Tool_명세.md`](Health_Tool_명세.md) · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) §2 · [`data_model.md`](../data_model.md)(표기 규칙)

---

## 0. 무엇이 Health 소유인가

| 테이블 | 소유 | 다른 Agent |
| --- | --- | --- |
| `medication_schedule` | **Health (CRUD)** | 읽지 않음 |
| `medication_dose` | **Health (CRUD)** | 읽지 않음 |
| `medication_dose_log` | **Health (CRUD)** — 신규 | 읽지 않음 |
| `prescription_draft` | **OCR 파이프라인** | Health는 **읽기만**, write 권한 없음 |
| `health_safety` | 앱 API (보호자 권한) | Food 읽음 · Health 읽음 |
| `observation_health` · `event` | Memory | Health 읽음 |
| `reference/health_phrases.yaml` | 마이그레이션이 아니라 **코드와 함께 배포되는 상수 파일** | Health만 읽음 |

**Health에는 문서 테이블(`*_doc`)이 없다**(2026-09-22). 꺼내는 방식이 키 정확 일치뿐이라 상수 파일이 맞다 — [`RAG_plan.md`](../shared/RAG_plan.md) §5.

> **기준은 누가 쓰느냐가 아니라 누가 읽느냐다.** 공유 테이블의 단일 writer는 Memory이고, 아무도 읽지 않는 도메인 전용 테이블은 소유 Agent가 쓴다. 이 원칙이 유지되려면 **Food·Activity·Growth가 `medication_*`을 읽지 않아야** 한다. "약 시간이랑 밥 시간이 겹쳐요" 같은 요구가 실제로 오면 그때 규칙을 다시 연다.

DB 권한: Agent role에 `medication_*` write 부여 · `health_safety` write **비부여** · `prescription_draft` write **비부여**.

---

## 1. 기준 시각 — 시스템 상수

보호자는 "식후", "자기 전"처럼 **시점**으로 말한다. 저장은 `기준점 + 오프셋(분)` 두 값으로 하고, 실제 알림 시각은 DB가 계산한다.

| `timing_anchor` | 기준 시각 |
| --- | --- |
| `wake_up` | 07:30 |
| `breakfast` | 08:00 |
| `lunch` | 12:00 |
| `dinner` | 18:00 |
| `bedtime` | 20:30 |
| `fixed` | 보호자가 지정 (`fixed_time`) |

`offset_minutes`는 **부호 있는 분**이다. 식전 = 음수, 식후 = 양수. "식후"의 기본값 **+30**은 `resolve_dose_timing` 사전의 한 줄이지 DB 값이 아니다.

- "아침식사 30분 후" → `breakfast` + 30 → **08:30**
- "저녁 먹기 30분 전" → `dinner` − 30 → **17:30**

---

## 2. `medication_schedule` — 복약 코스 1건

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()` |
| child_id | uuid | NOT NULL. FK → `child.id`, CASCADE |
| title | text | NOT NULL. **보호자 발화 원문 복사.** "항생제" / "타이레놀" |
| dosage | text | nullable. "5ml" / "1포". 원문 보존 |
| **indication_text** | text | nullable. **신규** — 복약 이유. 보호자 발화 원문 복사. 없으면 NULL (추정 금지) |
| **symptom_codes** | text[] | NOT NULL, default `'{}'`. **신규** — `normalize_symptom_term`이 정규화한 코드. 모델이 고르지 않는다 |
| **storage** | varchar(16) | nullable. **신규** — `room` / `fridge`. 보호자 선택값 |
| **med_form** | varchar(16) | nullable. **신규** — `liquid` / `powder` / `tablet` / `ointment` / `drops` |
| starts_on | date | NOT NULL |
| ends_on | date | nullable. NULL = 무기한 |
| status | varchar(16) | `active` / `stopped` / `completed`, NOT NULL, default `active`. **행이 존재한다 = 보호자가 이미 제출했다.** 중단은 `stopped`로 전환(soft delete) — 행이 남아야 복용 기록이 살아남는다 |
| note | text | nullable |
| created_by | varchar(16) | `agent` / `caregiver`, NOT NULL |
| **source** | varchar(16) | **신규** — `utterance` / `prescription_ocr`. 값의 출처 추적 |
| **source_draft_id** | uuid | nullable. **신규** — FK → `prescription_draft.id`, `ON DELETE SET NULL` |
| created_at · updated_at | timestamptz | NOT NULL, default now() |

**제약**
- `CHECK (ends_on IS NULL OR ends_on >= starts_on)`
- `CHECK (storage IS NULL OR storage IN ('room','fridge'))`
- `CHECK (med_form IS NULL OR med_form IN ('liquid','powder','tablet','ointment','drops'))`
- `CHECK (source IN ('utterance','prescription_ocr'))`
- **중단은 soft delete** — "약 끊었어"는 `status='stopped'`. 행이 남으므로 `medication_dose_log`가 보존되고, `visit_summary`·열 타임라인에서 과거 복약이 그대로 보인다. `event` 삭제(hard delete)와는 방식이 다르다 — event는 남길 기록이 없지만 복약은 있다
- **승인 전 상태는 이 테이블에 없다.** 생성·수정은 초안 payload로 나가고, 보호자가 제출한 뒤에야 행이 생기거나 바뀐다 (§6)

**새 컬럼의 성격**

| 컬럼 | 누가 채우나 | 금지 |
| --- | --- | --- |
| `indication_text` | 모델이 발화에서 **복사만** (span 검증) | 추정·보정 |
| `symptom_codes` | **코드**가 사전으로 | 모델이 코드 선택 |
| `storage` · `med_form` | 보호자 선택 또는 처방전 추출값 | 모델 추론 |

> `indication_text`는 **진단이 아니라 보호자 기록**이다. `health_safety`로 승격되지 않고, 다른 Agent에 전달되지 않는다. 같은 증상이 다시 왔을 때 "지난번엔 이 약을 먹였어요"라는 **사실 제시**까지만 쓰이고, "그 약을 먹이세요"는 처방이라 하지 않는다.

---

## 3. `medication_dose` — 복용 시점 1개

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()` |
| schedule_id | uuid | NOT NULL. FK → `medication_schedule.id`, CASCADE |
| timing_anchor | varchar(16) | `fixed`/`wake_up`/`breakfast`/`lunch`/`dinner`/`bedtime`, NOT NULL |
| offset_minutes | smallint | NOT NULL, default 0. `-120 ~ +180` |
| fixed_time | time | nullable. `timing_anchor='fixed'`일 때만 |
| days_of_week | smallint[] | NOT NULL, default `'{}'`. **빈 배열 = 매일.** ISO 1=월 … 7=일 |
| is_active | boolean | NOT NULL, default true |
| scheduled_time | time | **GENERATED ALWAYS … STORED.** 발송 잡이 읽는 유일한 값 |
| created_at · updated_at | timestamptz | NOT NULL, default now() |

**제약**
- `CHECK ((timing_anchor = 'fixed') = (fixed_time IS NOT NULL))`
- `CHECK (timing_anchor <> 'fixed' OR offset_minutes = 0)`
- `CHECK (days_of_week <@ ARRAY[1,2,3,4,5,6,7]::smallint[])`
- `UNIQUE (schedule_id, scheduled_time)` — 같은 약 같은 시각 중복 알림 차단
- 발송용 부분 인덱스: `(scheduled_time) WHERE is_active`

### 발화 → 저장

| 발화 | anchor | offset | scheduled_time |
| --- | --- | --- | --- |
| 아침식사 30분 후 | `breakfast` | 30 | 08:30 |
| 저녁 먹기 30분 전 | `dinner` | −30 | 17:30 |
| 점심 먹고 바로 | `lunch` | 0 | 12:00 |
| 자기 전에 | `bedtime` | 0 | 20:30 |
| 오후 3시에 | `fixed` | 0 | 15:00 |
| 하루 세 번 식후 | breakfast/lunch/dinner | 각 30 | 08:30 · 12:30 · 18:30 (**3행**) |
| 하루 세 번 (시점 없음) | — | — | **저장 안 함. 되묻기** |

---

## 4. `medication_dose_log` — 실제로 먹인 기록 (신규)

복약 일정만 있고 "먹였는지"가 없으면 다음 예정 시각도, 코스 요약도 만들 수 없다. `log_dose_taken` · `get_next_dose`의 근거 테이블이다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()` |
| schedule_id | uuid | NOT NULL. FK → `medication_schedule.id`, CASCADE |
| dose_id | uuid | nullable. FK → `medication_dose.id`, `ON DELETE SET NULL`. 어느 회차인지 (모르면 NULL) |
| taken_at | timestamptz | NOT NULL. 실제 복용 시각 (기본값 = 기록 시각) |
| taken_date | date | NOT NULL. `taken_at`의 로컬 날짜 — 하루 회차 집계용 |
| status | varchar(16) | `taken` / `skipped`, NOT NULL, default `taken` |
| note | text | nullable. 보호자 원문 ("반만 먹음") |
| created_by | varchar(16) | `agent` / `caregiver`, NOT NULL |
| created_at | timestamptz | NOT NULL, default now() |

**제약**
- `CHECK (status IN ('taken','skipped'))`
- `CHECK (taken_at <= now() + interval '5 minutes')` — **미래 기록 금지**
- `UNIQUE (dose_id, taken_date) WHERE dose_id IS NOT NULL` — 같은 회차 이중 기록 차단
- 인덱스: `(schedule_id, taken_at DESC)`
- **수정·삭제는 보호자만.** 오기록 정정 경로는 UI에서 (Agent는 INSERT만)

**규칙**
- `taken_at`을 생략하면 **지금**이다. "아까 먹였어"에서 시각을 추정하지 않는다. 필요하면 되묻는다.
- 오늘 예정 회차가 이미 모두 기록돼 있으면 **중복 확인**을 묻고, 임의로 추가하지 않는다.
- `skipped`는 보호자가 명시적으로 "안 먹였어"라고 할 때만. 기록이 없는 것은 `skipped`가 아니라 **모름**이다.
- 복약 순응도(adherence)를 **점수로 환산하지 않는다.** "7일 중 5일 기록됨" 같은 사실만.

---

## 5. `prescription_draft` — OCR 산출물 (Health는 읽기만)

처방전·약봉투 이미지는 **OCR 파이프라인**이 처리한다. Health는 결과 행을 읽어 확인 카드로 보여줄 뿐이다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK |
| child_id | uuid | NOT NULL. FK → `child.id`, CASCADE |
| title | text | nullable. 추출 실패 시 **NULL** (추측 금지) |
| dosage | text | nullable |
| frequency_text | text | nullable. "1일 3회" |
| timing_text | text | nullable. "식후 30분" → `resolve_dose_timing` 입력 |
| days | smallint | nullable. 투약 일수 |
| confidence | jsonb | NOT NULL, default `'{}'`. 필드별 0~1 |
| status | varchar(16) | `pending` / `confirmed` / `discarded`, NOT NULL, default `pending` |
| created_at · expires_at | timestamptz | 확인하지 않은 draft는 만료 (기간 미정) |

**규칙**
- **원본 이미지는 저장하지 않는다.** 추출 결과만 남기고 파이프라인이 파기한다. 처방전은 민감정보 중에서도 무게가 다르다.
- 신뢰도가 낮은 칸은 화면에서 구분되고, **보호자가 채우기 전까지 등록 버튼이 잠긴다.**
- 보호자가 "확인·등록"을 누르면 `create_medication_schedule`이 돌고, `status='confirmed'` · `source='prescription_ocr'` · `source_draft_id`가 연결된다.
- 약품명은 **대조하지 않는다.** 일치 여부 표시조차 하지 않는다 — 표시를 붙이는 순간 "비슷한 이름 제안"으로 번질 여지가 생기고, 의약품 정보 API를 쓰지 않는다는 원칙과 같은 자리다(H-10).

---

## 6. 승인 게이트 — 초안은 DB에 넣지 않는다 (2026-09-22 변경)

이전 판은 `draft` 행을 먼저 쓰고 승인하면 `active`로 바꿨다. **변경: 승인 전 값은 DB에 들어가지 않는다.** Memory가 `event` 초안을 다루는 방식과 같게 맞춘다 — 초안은 run 단위 버퍼에 모였다가 **JSON payload로 화면에 나가고**, 보호자가 제출하면 그때 백엔드가 INSERT/UPDATE한다.

`suggestion`과는 다르다. suggestion은 생성 즉시 `status='draft'` 행으로 저장되고 24시간 뒤 만료된다. 복약은 **행 자체가 승인의 증거**라, 승인 전 행이 있으면 발송 잡이 그것을 걸러야 하고 만료 배치가 하나 더 붙는다. 초안을 DB 밖에 두면 둘 다 필요 없다.

```
생성:  발화 → 초안 payload(op="create") → 확인 모달(실제 시각)
                                          ├ 제출 → INSERT 코스 + dose (status='active')
                                          └ 취소·이탈 → 아무 일도 없음 (run과 함께 사라짐)
수정:  기존 코스는 그대로 발송 유지
       → 초안 payload(op="update", schedule_id, before) → 확인 모달(바뀐 시각)
          ├ 제출 → UPDATE 코스 + dose 재생성 (한 트랜잭션)
          └ 취소·이탈 → 기존 코스 그대로
중단:  status='stopped' (soft delete). dose는 그대로 두고 발송만 멈춘다. 초안 없이 즉시 실행
```

**초안 payload** — `EventDraft.to_payload()`와 같은 모양이다.

```json
{
  "draft_id": "m1",
  "op": "create",
  "source": null,
  "schedule": {"title": "항생제", "dosage": "5ml", "indication_text": null,
               "storage": null, "med_form": null,
               "starts_on": "2026-09-22", "ends_on": "2026-09-26"},
  "doses": [{"timing_anchor": "breakfast", "offset_minutes": 30, "fixed_time": null,
             "days_of_week": [], "scheduled_time": "08:30"}, …],
  "notice_times": ["08:30", "12:30", "18:30"],
  "missing": []
}
```

- `op="update"`면 `schedule_id`와 `before`(수정 전 원본)가 더 실린다. 화면이 "18:30 → 19:00"을 보여주는 데 쓴다
- **`missing`은 비어 있는 NOT NULL 칸의 이름이다**(`title` · `starts_on`). 비어 있지 않으면 화면이 제출 버튼을 잠근다. 보호자가 채워서 보내야 INSERT가 성립한다
- 처방전 카드의 "빈칸 + 등록 잠김"은 **다른 잠금이다.** 그쪽은 OCR 신뢰도가 낮은 칸을 잠그는 것이라 nullable 칸(`dosage` 등)도 대상이 된다. 둘 다 통과해야 제출된다
- `draft_id`는 화면이 초안을 가리키고 제출 요청에 되돌려 보내는 키다. 배열 인덱스로는 안 된다 — 초안 두 장 중 하나만 제출하면 인덱스가 밀린다
- 초안은 이 payload로 나가고 끝이다. **run이 끝나면 사라지고 되받을 경로가 없다.** 만료(`expires_at`)라는 개념이 필요 없어진 이유다
- **처방전 경로** — `prescription_draft` 확인 카드에 실제 알림 시각까지 보여주고, "확인·등록"이 제출을 겸한다(두 번 묻지 않음, M-13). 이때 `source="prescription_ocr"`
- **발송 잡은 `status='active'` 코스의 dose만 읽는다.** `medication_dose.scheduled_time` 부분 인덱스로 읽고 schedule과 조인해 `stopped`·`completed`를 거른다. 기존 `reminder` 테이블은 쓰지 않는다(`event_id`가 NOT NULL이라 재사용 불가 — M-1)
- 제출은 tool이 아니다. 보호자가 누르면 백엔드가 쓴다. 모델은 저장 시점을 건드리지 못한다

게이트가 생겨도 아래 셋은 그대로 **코드로** 강제한다.

1. **실제 시각 표시 의무** — 확인 모달에 매핑 결과 시각을 반드시 노출한다. "아침식사 30분 후 → **08:30**". 보호자가 승인하는 대상은 발화가 아니라 **실제로 울릴 시각**이다. payload의 `notice_times`는 **출력 tool의 필수 필드**다.
2. **약 이름·용량·이유는 복사만** — `title` · `dosage` · `indication_text`는 발화(또는 확인된 처방전 draft)에서 복사한다. 추정·보정·단위 환산 금지.
3. **anchor는 코드가 정한다** — 모델은 시점 **표현**을 복사하고, `resolve_dose_timing`이 사전으로 매핑한다. 모델이 `fixed`를 임의로 고를 수 없다.

복약은 `event` 이관 경로에서 빠졌다. **event 이관에 남는 건 재방문 · 검진 window뿐이다.**

---

## 7. DDL

```sql
-- 복약 코스 1건
CREATE TABLE medication_schedule (
    id              uuid        PRIMARY KEY DEFAULT uuidv7(),
    child_id        uuid        NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    title           text        NOT NULL,
    dosage          text,
    indication_text text,
    symptom_codes   text[]      NOT NULL DEFAULT '{}',
    storage         varchar(16) CHECK (storage IN ('room','fridge')),
    med_form        varchar(16) CHECK (med_form IN ('liquid','powder','tablet','ointment','drops')),
    starts_on       date        NOT NULL,
    ends_on         date,
    status          varchar(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','stopped','completed')),
    -- 승인 전 값은 이 테이블에 없다. 초안은 payload 로 나가고 제출 때 INSERT 된다(§6)
    note            text,
    created_by      varchar(16) NOT NULL CHECK (created_by IN ('agent','caregiver')),
    source          varchar(16) NOT NULL DEFAULT 'utterance'
                    CHECK (source IN ('utterance','prescription_ocr')),
    source_draft_id uuid        REFERENCES prescription_draft(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE INDEX medication_schedule_child_idx ON medication_schedule (child_id, status);
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

CREATE INDEX medication_dose_send_idx ON medication_dose (scheduled_time) WHERE is_active;
```

```sql
-- 실제로 먹인 기록
CREATE TABLE medication_dose_log (
    id          uuid        PRIMARY KEY DEFAULT uuidv7(),
    schedule_id uuid        NOT NULL REFERENCES medication_schedule(id) ON DELETE CASCADE,
    dose_id     uuid        REFERENCES medication_dose(id) ON DELETE SET NULL,
    taken_at    timestamptz NOT NULL DEFAULT now(),
    taken_date  date        NOT NULL,
    status      varchar(16) NOT NULL DEFAULT 'taken'
                CHECK (status IN ('taken','skipped')),
    note        text,
    created_by  varchar(16) NOT NULL CHECK (created_by IN ('agent','caregiver')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (taken_at <= now() + interval '5 minutes')
);

CREATE UNIQUE INDEX medication_dose_log_once_idx
    ON medication_dose_log (dose_id, taken_date) WHERE dose_id IS NOT NULL;
CREATE INDEX medication_dose_log_recent_idx
    ON medication_dose_log (schedule_id, taken_at DESC);
```

```sql
-- OCR 파이프라인 산출물 (Health는 SELECT만)
CREATE TABLE prescription_draft (
    id             uuid        PRIMARY KEY DEFAULT uuidv7(),
    child_id       uuid        NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    title          text,
    dosage         text,
    frequency_text text,
    timing_text    text,
    days           smallint,
    confidence     jsonb       NOT NULL DEFAULT '{}',
    status         varchar(16) NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','confirmed','discarded')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL
);
```

**생성 컬럼을 쓴 이유** — 기준 시각이 상수라면 앱이 계산해 저장할 필요가 없고, 계산 결과가 DB와 코드에서 갈릴 여지도 없다. 발송 잡은 `scheduled_time`만 보면 되고, `UNIQUE(schedule_id, scheduled_time)`이 중복 알림을 DB에서 막는다.

**대가** — 기준 시각 상수를 바꾸면 테이블 재작성 마이그레이션이 필요하다. 연령대별 기준 시각(M-3)을 도입하는 순간 생성 컬럼을 포기해야 한다.

---

## 8. 공유 테이블 변경 요청 (Health 소유 아님)

| 테이블 | 요청 | 왜 |
| --- | --- | --- |
| `observation_health` | **`temperature numeric(3,1)`** · **`measured_at timestamptz`** · **`measure_site enum`**(`ear`/`forehead`/`armpit`/`oral`/`rectal`, nullable) | `build_fever_timeline`의 하드 선행. 지금은 `symptom text[]`에 "발열"만 들어가 숫자가 없다 |
| `child` | `gestational_weeks smallint` (선택) | 검진·접종 안내의 조산아 처리. `gender`는 요청 철회(성별 미사용) |

소유자는 Memory다. Health는 읽기만 한다.

---

## 9. 구현 메모

- PK 기본값 `uuidv7()` — 다른 테이블과 통일
- enum은 전부 VARCHAR + CHECK (identity/child/consent/event와 통일)
- `make_interval` · `time + interval`은 IMMUTABLE이라 생성 컬럼에 쓸 수 있다. 마이그레이션에서 한 번 확인할 것
- `UNIQUE (schedule_id, scheduled_time)`은 생성 컬럼 위의 유니크 — PostgreSQL 12+
- `medication_schedule`에 `source_refs`를 두지 않았다. 복약은 보호자 발화에서 직접 나오므로 근거 추적 필요성이 낮다. `source` · `source_draft_id`로 출처만 남긴다
- `taken_date`는 애플리케이션이 아이 타임존 기준으로 채운다 (`taken_at`에서 UTC로 계산하면 자정 부근이 어긋난다)
- 로그에는 `{kind, id}`만. **약명·이유·증상 원문은 로그에 남기지 않는다**(NF-05)

---

## 10. 미결

| # | 쟁점 | 메모 |
| --- | --- | --- |
| M-1 | ✅ **닫힘(09-22)** — 발송 잡이 `medication_dose`를 직접 조회한다(schedule 조인으로 `active`만). `reminder` 테이블은 건드리지 않아 일정 알림과 서로 안 얽힌다 |
| M-2 | ~~복용 기록 저장처~~ | ✅ **닫힘** — `medication_dose_log` 신설 |
| M-3 | ✅ **닫힘(09-22)** — 상수는 1벌로 두고, 영아면 `resolve_dose_timing`이 `bedtime` 대신 **`fixed` 19:30**을 돌려준다. `scheduled_time` 생성 컬럼을 유지하고 모달에도 실제 시각이 뜬다 |
| M-4 | `route`(경구/외용/점안) | `med_form`이 일부 대체. 연고·안약 알림이 필요해지면 확장 |
| M-5 | `days_of_week`가 실제로 쓰이나 | "격일 복용" 요구가 없으면 죽은 컬럼. UI 노출 보류 |
| M-6 | **`prescription_draft` 만료 기간** | 미확인 draft를 며칠 둘지 |
| M-7 | **`symptom_codes` 사전 범위** (H-8과 동일) | 에피소드·반복 판정과 같은 어휘 |
| M-8 | 오기록 정정 UI | Agent는 INSERT만. 수정·삭제 경로는 보호자 화면 |
| M-9 | `storage` · `med_form`을 누가 묻나 | 등록 카드의 선택 입력. 비어도 서식은 생성 |
| M-10 | 보호자 입력 접종 이력(`immunization_record`) | 도입하면 접종 안내가 개인 일정이 된다. 이번 범위 밖 |
| M-5 | `days_of_week` | 설정값 — 컬럼은 두되 UI 노출은 보류. "격일 복용" 요구가 오면 연다 |
| M-6 | `prescription_draft` 만료 | 설정값 — **7일**로 시작 |
| M-11 | ✅ **승인 게이트 범위·방식** | 닫힘(09-22) — 생성·수정은 초안 payload → 제출 시 저장, 중단은 soft delete. `draft` 행·`expires_at`·`replaces_id` 없음 |
| M-12 | ✅ **닫힘(09-22)** — 중단을 soft delete(`status='stopped'`)로 바꿔 행이 남는다. 복용 기록이 지워지지 않는다 |
| M-13 | **처방전 확인 카드 = 승인 모달** | 카드에 실제 알림 시각까지 표시하고 "확인·등록"을 승인으로 겸하는 안. 두 번 묻지 않기 위함 |
| M-14 | ✅ **수정 시 기존 코스의 복용 기록** | 닫힘(09-22) — 수정이 DELETE+INSERT가 아니라 같은 행 UPDATE라 `medication_dose_log`가 그대로 남는다. dose 재생성은 `medication_dose`만 건드린다 |

---

## 부록 — `child_growth_log` (Memory 소유, Growth · Food가 읽음)

| 필드 | 비고 |
| --- | --- |
| id · child_id (CASCADE) · height · weight · check_date | 수정하지 않고 **append** |

| Agent | 읽는 목적 | 넘지 않는 선 |
| --- | --- | --- |
| **Growth** | 성장 추이 정리(`growth_review`). 마지막 측정이 오래되면 "재보실래요?" | 백분위·또래 비교·저성장·정체기 언급 |
| **Food** | **권장 열량 계산 입력** (09-22, 개정 2 번복) | 과체중·저체중 판정, 체중 조절 식단. 성별 미반영 |
| **Health** | **읽지 않음** (성장 판정 제거, 09-22) | – |

측정값은 전부 **읽기 전용**이다. 기록은 Memory가 한다. "잘 크고 있어?" 같은 판정 요청도 **Growth(`growth_review`)** 로 가고, 판정 없이 추이 + 검진 안내만 나간다.
