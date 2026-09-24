# Food Agent 소유 테이블

> Food가 쓰거나 전용으로 읽는 테이블의 **정본**. 계산 규칙은 [`영양소_계산_설계.md`](영양소_계산_설계.md), 문서 행 제작 절차는 [`RAG_plan.md`](../shared/RAG_plan.md).
> 관련: [`Food_Agent_명세.md`](Food_Agent_명세.md) · [`Food_Tool_명세.md`](Food_Tool_명세.md) · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) §2
>
> **2026-09-22 갱신** — `child_growth_log` 다시 읽음(권장 열량, 성별 제외) · 급식은 `daycare_meal`(Food 소유, CRUD) · `intake_daily`는 집에서 먹은 것만 · 끼니 슬롯 폐지

---

## 0. 무엇이 Food 소유인가

**Food가 직접 쓰는 것은 `daycare_meal` 하나입니다.** 나머지는 전부 참조·캐시·파생이고, 아이의 관찰·프로필은 손대지 않습니다.

`daycare_meal`이 예외인 이유는 Health의 `medication_*`과 같습니다 — **다른 Agent가 읽지 않는 도메인 전용 테이블**이라 공유 테이블의 단일 writer 원칙에 걸리지 않습니다.

| 테이블 | 쓰는 주체 | 읽는 주체 | 성격 |
| --- | --- | --- | --- |
| `menu_catalog` · `menu_alias` | 동기화 배치 (`integrations`) | Food | 외부 API 캐시 |
| `intake_daily` | `compute_intake_daily` (코드) | Food | **파생** — 언제든 재계산 |
| **`daycare_meal`** | 최초 적재는 OCR·급식 배치(INSERT) · 이후 갱신·삭제는 **Food** | Food | **도메인 전용** — 아무도 안 읽는다 |
| `nutrient_reference` | 마이그레이션 시드 | Food | 참조 상수 |
| `food_doc` | 마이그레이션 시드 | Food | 문서 행 |
| `allergen_term` | 마이그레이션 시드 | Food · **Health** | 공유 참조 |

| 읽기만 하는 공유 테이블 | 소유 |
| --- | --- |
| `observation_food` · `profile_affinity(domain=food)` | Memory |
| `health_safety` | 앱 API (보호자 권한) |
| `child` (생년월일 · `gestational_weeks`) | 앱 |
| `child_growth_log` (키·몸무게·측정일) | Memory |
| `suggestion` | 주입된 writer가 INSERT (`status='draft'`) |

> `child_growth_log`는 **권장 열량 계산 입력으로 읽습니다**(2026-09-22, 개정 2 번복). 영양소 기준은 연령군 일반값이고, **권장 열량만** 키·몸무게로 조정합니다. 측정이 없으면 연령군 일반값을 씁니다. 계산 결과는 내부 전용이고, 측정값으로 과체중·저체중을 판정하지 않습니다. **성별은 읽지 않습니다.**

DB 권한: Agent role에 `intake_daily` write · **`daycare_meal` UPDATE/DELETE**(INSERT는 비부여) · OCR·배치 role에 `daycare_meal` INSERT 부여 · `menu_catalog`·`nutrient_reference`·`food_doc`·`allergen_term` write **비부여**(배치 role만) · `health_safety` write **비부여**.

---

## 1. `menu_catalog` — 메뉴 해석 캐시

메뉴명 문자열을 식품코드·재료·영양으로 바꾸는 유일한 경로. 미스일 때만 외부 API를 부르고 결과를 여기 쌓는다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `menu_key` | text | **PK.** 정규화 메뉴명 (공백·괄호·급식 알레르기 번호 제거, 동의어 치환) |
| `display_name` | text | NOT NULL. 화면에 쓰는 이름 |
| `food_code` | text | nullable. 식약처 식품코드 |
| `source` | varchar(16) | `mfds_nutri` / `mfds_recipe` / `center_standard` / `manual` |
| `serving_g` | numeric(7,1) | nullable. 1인분 중량 — **성인 기준** |
| `nutrients` | jsonb | NOT NULL, default `'{}'`. 100g 기준 + 1인분 |
| `ingredients` | text[] | NOT NULL, default `'{}'` |
| `allergen_codes` | smallint[] | NOT NULL, default `'{}'`. 19종 — 재료 + 메뉴명 사전의 **합집합** |
| `food_groups` | text[] | NOT NULL, default `'{}'`. 6개 식품군 |
| `stage_min` | varchar(20) | 이 메뉴가 가능한 최소 단계. `LifeStage.stage` 네 값(`infant_milk` / `infant_weaning` / `toddler` / `preschool`) — [`Tool_공통.md`](../shared/Tool_공통.md) §2 (수동 태깅) |
| `resolved` | boolean | NOT NULL, default false |
| `source_version` | varchar(32) | 데이터 기준 버전 |
| `synced_at` | timestamptz | NOT NULL |

**규칙**
- `resolved=false` 행은 **후보 풀에서 제외**되고, 급식 표시에서는 "알레르기 확인 못 함"이 된다.
- `allergen_codes`는 재료에서 뽑은 것과 메뉴명 사전에서 뽑은 것의 **합집합**이다. 한쪽만 쓰면 "크림수프"의 우유가 빠진다(F-1).
- `stage_min`이 비면 `toddler`로 간주한다 — **보수적인 쪽**(이유기 아이에게 나가지 않는다).
- 운영 트래픽을 API에 직접 걸지 않는다. 야간 배치 + 캐시 미스 시 1회.

### `menu_alias` — 표기 흔들림 흡수

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `alias` | text | PK. 정규화된 표기 ("카레", "카레라이스", "커리") |
| `menu_key` | text | NOT NULL. FK → `menu_catalog.menu_key`, CASCADE |
| `created_by` | varchar(16) | `seed` / `batch` / `manual` |

---

## 2. `intake_daily` — 집에서 먹은 것 (파생)

`observation_food`가 원문이고 이 테이블은 **파생**이다. 재계산으로 언제든 덮어쓴다. 계산식은 [`영양소_계산_설계.md`](영양소_계산_설계.md) §3~§5.

기관에서 먹는 것은 여기 들어오지 않는다 — §5 `daycare_meal`이다. 둘을 합쳤다가 다시 나눴다(FT-8): 급식표는 아직 안 온 날짜를 갖고 기관이 정한 끼니 구분을 따르는데, 파생 테이블에 미래 행을 두면 재계산이 그걸 덮어쓴다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK, `uuidv7()` |
| `child_id` | uuid | NOT NULL. FK → `child.id`, CASCADE |
| `intake_date` | date | NOT NULL. **미래 날짜 없음** |
| `meal_type` | varchar(8) | `meal` / `snack`. **끼니 슬롯이 아니다** |
| `menu_keys` | text[] | NOT NULL, default `'{}'` |
| `amount_factor` | numeric(3,2) | NOT NULL, default 1.00 |
| `amount_known` | boolean | NOT NULL, default true. "남김"은 false |
| `nutrients` | jsonb | NOT NULL, default `'{}'`. **내부 전용 — 화면·모델 입력 금지** |
| `energy_ratio` | jsonb | `{carb, protein, fat}` %kcal |
| `food_groups` | text[] | NOT NULL, default `'{}'` |
| `resolved_count` · `unresolved_count` | smallint | NOT NULL, default 0 |
| `catalog_version` | varchar(32) | 계산에 쓴 `menu_catalog` 버전 |
| `computed_at` | timestamptz | NOT NULL, default now() |

**규칙**
- **끼니를 정의하지 않는다.** 아침·점심·저녁을 가르지 않고, 그날 먹은 것을 먹은 만큼 행으로 쌓는다. 가르는 것은 식사냐 간식이냐뿐이다. 보호자는 끼니 단위로 말하지 않는다 — "오늘 간식 두 번 줬어"가 그냥 두 행이 된다.
- 그래서 **UNIQUE 제약이 없다.** 같은 날 같은 `meal_type`이 여러 행일 수 있다.
- **"결측 끼니"라는 개념도 없다.** 기록이 충분한지는 기간 안의 행 수로 판단한다(§2-1).
- 보존 **30일**. 배치로 삭제(NF-04 최소 보관).
- `catalog_version`이 현재와 다르면 조회 시 **재계산 대상**으로 표시한다.

### 2-1. 기록이 충분한가 — 행 수

끼니 비율(`기록 끼니 / 기대 끼니`)로 세던 커버리지를 버렸다. 끼니 슬롯이 없어졌으니 분모가 성립하지 않는다.

**판정 기간 7일 안에 `daycare_meal` + `intake_daily` 합쳐 10행 미만이면 수치 기반 결론을 내지 않는다**(`insufficient`). 설정값이다.

- 하루 평균 1.5행이다. 급식이 있는 아이는 평일만으로 채워지고, 집에서만 먹는 아이도 하루 2건이면 닿는다.
- 메뉴 해석률(`unresolved_count`)은 따로 본다 — 행은 있는데 메뉴를 못 읽으면 그것도 결론을 막는다.

---

## 3. `nutrient_reference` — 섭취기준

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK |
| `age_group` | varchar(8) | `0-5m` / `6-11m` / `1-2y` / `3-5y` |
| `sex` | varchar(8) | nullable = 구분 없음 |
| `nutrient` | varchar(32) | `energy` · `protein` · `sodium` · `calcium` · `iron` … |
| `ref_type` | varchar(8) | `EAR` / `RNI` / `AI` / `UL` / `AMDR` |
| `value_min` · `value_max` | numeric(10,3) | `AMDR`만 범위 |
| `unit` | varchar(16) | `kcal` · `g` · `mg` · `%kcal` |
| `source_version` | varchar(16) | `kdri_2025` |
| `note` | text | 기준의 단서 |

- `UNIQUE (age_group, sex, nutrient, ref_type, source_version)`
- 개정 시 **새 버전을 넣고 조회 기본값만 바꾼다.** 기존 행을 덮어쓰지 않는다.
- 부족은 EAR, 과잉은 UL, 탄단지는 AMDR. **손으로 옮긴 값은 2인 대조**를 거친다 — 숫자 오타가 곧 판정 오류다.

---

## 4. `food_doc` — 문서 행

공통 컬럼은 [`RAG_plan.md`](../shared/RAG_plan.md) §1. Food 전용 컬럼만 적는다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `row_type` | varchar(24) | `weaning_stage` / `weaning_ingredient` / `meal_pattern` / `nutrient_note` / `feeding_tip` |
| `food_groups` | text[] | 6개 식품군 |
| `nutrients` | text[] | `nutrient_note`용 |
| `texture` | varchar(16) | `puree` / `mashed` / `minced` / `soft_pieces` / `regular` |
| `allergen_codes` | smallint[] | 행이 특정 재료를 권하면 필수 — 조회 후 `filter_food_safety` 통과 |

- 조회는 `status='approved'`만. 단계·`row_type` 필터 → 의미 검색 top-3.
- 이유기(4–11개월)는 `profile_affinity`가 구조적으로 0행이라 **이 테이블이 곧 추천 품질**이다.

---

## 5. `daycare_meal` — 기관에서 먹는 것 (Food가 고치는 테이블)

**Food가 직접 쓰는 유일한 테이블이다.** 단 **INSERT는 못 한다** — 행을 만드는 것은 OCR·급식 배치뿐이고, Food는 이미 있는 행을 고치거나 지울 뿐이다. 없는 급식을 지어내는 경로를 애초에 열지 않는다. 다른 Agent가 읽지 않는 도메인 전용 테이블이라 "공유 테이블의 단일 writer는 Memory" 원칙에 걸리지 않는다 — Health의 `medication_*`과 같은 자리다. 기준은 누가 쓰느냐가 아니라 **누가 읽느냐**다.

어린이집 급식은 전국 단일 API가 없다. **기관 공지 OCR이 주 경로**이고, 학교·해당 유치원은 NEIS, 그 외에는 지역 센터 표준식단을 참고로 쓴다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK, `uuidv7()` |
| `child_id` | uuid | NOT NULL. FK → `child.id`, CASCADE |
| `serve_date` | date | NOT NULL. **미래 날짜를 갖는 유일한 식사 테이블** |
| `meal_slot` | varchar(16) | `lunch` / `snack_am` / `snack_pm`. **기관이 정한 구분이라 그대로 둔다** |
| `menu_keys` | text[] | NOT NULL. 정규화 메뉴. 대체식이면 **교체**한다 |
| `amount_factor` | numeric(3,2) | NOT NULL, default 1.00. "계란말이를 엄청 많이 먹었대" → 큰 값 |
| `amount_known` | boolean | NOT NULL, **default false**. 급식은 보통 얼마나 먹었는지 모른다. 보호자가 말하면 true |
| `allergen_codes` | smallint[] | NOT NULL, default `'{}'`. 급식 표기에서 추출 |
| `allergen_mapped` | boolean | NOT NULL, default false. **false면 "알레르기 확인 못 함"** |
| `caregiver_checked` | boolean | NOT NULL, default false. 대체·제외를 확인했는가 ("없어요"도 true) |
| `origin` | varchar(16) | `ocr` / `neis` / `center_standard` |
| `source_notice_id` | uuid | nullable |
| `ocr_confidence` | numeric(3,2) | nullable |
| `created_at` · `updated_at` | timestamptz | NOT NULL, default now() |

**규칙**
- `UNIQUE (child_id, serve_date, meal_slot)`
- **오늘 이전 행은 "먹었다"로 간주한다.** 그래서 영양 분석의 입력이 된다. 별도의 상태 컬럼을 두지 않는다 — 날짜가 그 일을 한다.
- **Food는 UPDATE·DELETE만 한다.** 고칠 행이 없으면 `NOT_FOUND`로 끝낸다 — "급식이 이렇게 나왔대"만으로 새 행을 만들지 않는다.
- **결석은 행 삭제다.** "내일모레는 어린이집 빠져서"는 그날 행을 지운다. `menu_keys`를 비우면 "결석"인지 "아직 모름"인지 구별되지 않는다.
- **대체·제외는 `menu_keys`를 교체한다.** 제외 메뉴를 빼고 대체 메뉴를 넣는다. 별도 배열을 두지 않는다 — 두 벌이면 무엇이 실제로 나갔는지가 흐려진다.
- 대체·제외 값은 **보호자가 알려준 것만.** 알레르기 등록 정보로 추정해 채우지 않는다. 보호자의 "우유 알레르기라 두유로 받아요"는 **식단 갱신이지 `health_safety` 등록이 아니다.**
- `origin='center_standard'`는 **아이의 실제 급식이 아니다.** 화면에 참고 식단임을 표시한다.
- 보존: **과거 30일 + 미래 전부.** 급식표는 아직 안 온 날짜라 30일 컷에 걸리면 안 된다.

### 무엇이 이 테이블로 오나

기관 신호(어린이집 · 유치원 · 급식 · 반찬으로 나온 …)가 있으면 여기, 없으면 `observation_food`다. 판정은 **Supervisor가 라우팅에서** 한다.

```
"어린이집에서 계란말이 엄청 많이 먹었대"   → food / daycare_meal   (amount_factor 갱신)
"오늘 급식 대신 두유 받았대"               → food / daycare_meal   (menu_keys 교체)
"내일모레는 어린이집 빠져"                 → food / daycare_meal   (행 삭제)
"집에서 계란말이 먹였어"                   → memory                (observation_food)
```

### 쓰기 tool

`update_daycare_meal` · `delete_daycare_meal` 둘뿐이다. **모델에게 보인다** — 발화에서 메뉴명·양·날짜를 뽑아야 해서다. 대신 복약과 같은 장치를 건다.

- 메뉴명·양은 **발화 원문의 부분 문자열**이어야 한다(span 검증, 코드)
- 날짜 해석은 코드가 한다. 모델은 "내일모레" 같은 표현을 복사만 한다
- **승인 게이트를 두지 않는다.** 급식 메뉴는 알림이 울리지 않고, 틀려도 다시 말하면 된다. 게이트는 캘린더 쓰기와 건강·알레르기 확정 두 곳뿐이다(CLAUDE.md §2)
- 삭제는 되돌릴 수 없지만(OCR 재적재 전까지) 확인 없이 즉시 반영한다. 대상 날짜가 모호하면 **되묻는다**

---

## 6. `allergen_term` — 19종 코드 사전 (Food · Health 공유)

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `code` | smallint | PK. 1~19 (식약처 표시 대상 순서) |
| `label` | text | NOT NULL. "알류" · "우유" · "메밀" … |
| `aliases` | text[] | NOT NULL. "계란" · "달걀" · "난류" … (F-3 동의어표) |
| `guards` | text[] | NOT NULL, default `'{}'`. 오탐 취소 목록 ("밀크티"는 밀 아님) |
| `source_version` | varchar(16) | 고시 개정 시 버전업 |

매칭은 공통 매처(`app/rules/term_match.py`, Activity와 동일)를 쓴다 — 공백 제거 부분 일치 + guards + **가장 엄한 판정 우선**.

---

## 7. DDL (발췌)

```sql
CREATE TABLE menu_catalog (
    menu_key       text        PRIMARY KEY,
    display_name   text        NOT NULL,
    food_code      text,
    source         varchar(16) NOT NULL
                   CHECK (source IN ('mfds_nutri','mfds_recipe','center_standard','manual')),
    serving_g      numeric(7,1),
    nutrients      jsonb       NOT NULL DEFAULT '{}',
    ingredients    text[]      NOT NULL DEFAULT '{}',
    allergen_codes smallint[]  NOT NULL DEFAULT '{}',
    food_groups    text[]      NOT NULL DEFAULT '{}',
    stage_min      varchar(20)
                   CHECK (stage_min IN ('infant_milk','infant_weaning','toddler','preschool')),
    resolved       boolean     NOT NULL DEFAULT false,
    source_version varchar(32),
    synced_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (allergen_codes <@ ARRAY(SELECT generate_series(1,19))::smallint[])
);
CREATE INDEX menu_catalog_stage_idx ON menu_catalog (stage_min) WHERE resolved;
CREATE INDEX menu_catalog_allergen_idx ON menu_catalog USING gin (allergen_codes);

CREATE TABLE menu_alias (
    alias      text        PRIMARY KEY,
    menu_key   text        NOT NULL REFERENCES menu_catalog(menu_key) ON DELETE CASCADE,
    created_by varchar(16) NOT NULL CHECK (created_by IN ('seed','batch','manual'))
);

CREATE TABLE intake_daily (
    id               uuid        PRIMARY KEY DEFAULT uuidv7(),
    child_id         uuid        NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    intake_date      date        NOT NULL CHECK (intake_date <= current_date),
    meal_type        varchar(8)  NOT NULL CHECK (meal_type IN ('meal','snack')),
    menu_keys        text[]      NOT NULL DEFAULT '{}',
    amount_factor    numeric(3,2) NOT NULL DEFAULT 1.00
                     CHECK (amount_factor BETWEEN 0 AND 2),
    amount_known     boolean     NOT NULL DEFAULT true,
    nutrients        jsonb       NOT NULL DEFAULT '{}',
    energy_ratio     jsonb       NOT NULL DEFAULT '{}',
    food_groups      text[]      NOT NULL DEFAULT '{}',
    resolved_count   smallint    NOT NULL DEFAULT 0,
    unresolved_count smallint    NOT NULL DEFAULT 0,
    catalog_version  varchar(32),
    computed_at      timestamptz NOT NULL DEFAULT now()
    -- UNIQUE 없음. 같은 날 같은 meal_type 이 여러 행일 수 있다
);
CREATE INDEX intake_daily_window_idx ON intake_daily (child_id, intake_date DESC);

CREATE TABLE daycare_meal (
    id                uuid        PRIMARY KEY DEFAULT uuidv7(),
    child_id          uuid        NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    serve_date        date        NOT NULL,   -- 미래 허용
    meal_slot         varchar(16) NOT NULL
                      CHECK (meal_slot IN ('lunch','snack_am','snack_pm')),
    menu_keys         text[]      NOT NULL,
    amount_factor     numeric(3,2) NOT NULL DEFAULT 1.00
                      CHECK (amount_factor BETWEEN 0 AND 2),
    amount_known      boolean     NOT NULL DEFAULT false,
    allergen_codes    smallint[]  NOT NULL DEFAULT '{}',
    allergen_mapped   boolean     NOT NULL DEFAULT false,
    caregiver_checked boolean     NOT NULL DEFAULT false,
    origin            varchar(16) NOT NULL
                      CHECK (origin IN ('ocr','neis','center_standard')),
    source_notice_id  uuid,
    ocr_confidence    numeric(3,2),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (child_id, serve_date, meal_slot),
    CHECK (allergen_codes <@ ARRAY(SELECT generate_series(1,19))::smallint[])
);
CREATE INDEX daycare_meal_window_idx ON daycare_meal (child_id, serve_date DESC);
CREATE TABLE nutrient_reference (
    id             uuid         PRIMARY KEY DEFAULT uuidv7(),
    age_group      varchar(8)   NOT NULL
                   CHECK (age_group IN ('0-5m','6-11m','1-2y','3-5y')),
    sex            varchar(8)   CHECK (sex IN ('male','female')),
    nutrient       varchar(32)  NOT NULL,
    ref_type       varchar(8)   NOT NULL
                   CHECK (ref_type IN ('EAR','RNI','AI','UL','AMDR')),
    value_min      numeric(10,3) NOT NULL,
    value_max      numeric(10,3),
    unit           varchar(16)  NOT NULL,
    source_version varchar(16)  NOT NULL,
    note           text,
    UNIQUE (age_group, sex, nutrient, ref_type, source_version),
    CHECK (value_max IS NULL OR value_max >= value_min),
    CHECK (ref_type <> 'AMDR' OR unit = '%kcal')
);

```

---

## 8. 공유 테이블 변경 요청 (Food 소유 아님)

| 테이블 | 요청 | 왜 |
| --- | --- | --- |
| `health_safety` | **`child` 생성 시 `kind='allergy'` 19행을 `unknown` 으로 생성** · `state` 에 `none`·`unknown` 추가 — ✅ 확정(09-23) | F-4 닫힘. "아직 안 물어봤다"가 `unknown` 값으로 남아 "없다고 확인함"과 갈린다. `child.allergy_status` 는 뺐다 |
| `child` | `gestational_weeks smallint` (선택) | 조산아 이유식 단계 |
| `suggestion` | **`kind`만** 추가 · `source_refs` jsonb 제거 → `suggestion_evidence` 테이블 | 개인화/일반 표시 · 근거를 행으로 세기 위해 |
| `observation_food` | `amount` 값 집합 확정 | 가중치 사전(F-18)의 입력 |
| `notice` | 테이블 신설 | `intake_daily.source_notice_id` FK |

소유자는 Memory·앱이다. Food는 읽기만 한다.

---

## 9. 구현 메모

- PK 기본값 `uuidv7()`, enum은 VARCHAR + CHECK — 다른 도메인과 통일
- `menu_catalog`는 아이 데이터가 아니다(개인정보 아님). `intake_daily`는 아이 데이터다 — 파기·보관 정책 대상
- `intake_daily.nutrients`는 **내부 전용**이다. tool 결과 밖으로 나가지 않는다
- 재계산 배치: `catalog_version` 불일치 행만 다시 계산 → 카탈로그 갱신이 과거 분석을 조용히 바꾸지 않게 로그를 남긴다
- 로그에는 `{kind, id}`만. **메뉴명·알레르기 라벨 원문을 남기지 않는다**(NF-05)

---

## 10. 미결

| # | 쟁점 | 메모 |
| --- | --- | --- |
| FT-1 | `menu_catalog.stage_min` 수동 태깅 범위 | 초기 몇 백 행만. 나머지는 보수적 기본값 |
| FT-2 | `intake_daily` 30일 보관이 동의·보관 정책과 맞는지 | NF-04 확정 후 |
| FT-3 | 유아 1인분 보정 계수 (`portion_scale.yaml`) | 섭취기준 기준 체위·1회 섭취참고량으로 산출 |
| FT-4 | 급식 `center_standard` 이용 조건 | 센터별로 다름 |
| FT-5 | NEIS 알레르기 번호 ↔ `allergen_term.code` 순서 일치 | 실호출 확인 전 매핑 확정 금지 |
| FT-6 | `menu_alias` 초기 사전 규모 | 급식 표기 흔들림이 가장 크다 |
| FT-7 | 레시피 재료 텍스트 파서 정확도 기준 | 알레르기 판정 입력이라 임계가 높아야 함 |
| FT-8 | ✅ **닫힘(09-22)** — 급식은 `daycare_meal`로 분리하고 **Food 소유(CRUD)**로 뒀다. 대체·제외는 Food가 tool로 `menu_keys`를 교체한다 |
| FT-9 | **성별 없는 권장 열량 계산식** | 흔히 쓰는 필요추정량 공식 중 유아기부터 성별로 나뉘는 것이 있어 선정 필요 |
| FT-10 | `nutrient_reference.sex` | 성별 미사용 확정 → 조회는 `sex IS NULL` 행만. 영유아 구간에 성별 구분 행이 있는 지표의 처리 |
| FT-11 | **급식 행의 보존 기간** | 과거 30일 + 미래 전부로 시작. 지난 급식표를 언제까지 둘지는 보관 정책 확정 후 |
