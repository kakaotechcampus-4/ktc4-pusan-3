# Growth Agent 소유 테이블

> Growth가 쓰거나 전용으로 읽는 테이블의 **정본**. 문서 행 제작 절차는 [`RAG_plan.md`](../shared/RAG_plan.md).
> 관련: [`Growth_Agent_명세.md`](Growth_Agent_명세.md) · [`Growth_Tool_명세.md`](Growth_Tool_명세.md) · [`Agent_공통규약.md`](../shared/Agent_공통규약.md) §2
>
> **2026-09-22 갱신** — `growth_doc`이 핵심 기능(관찰 × 문서 연결 추천)의 재료 · `notice`는 보조 · 성장 판정은 시스템 전체에서 없음

---

## 0. 무엇이 Growth 소유인가

**Growth는 아이 데이터를 하나도 쓰지 않습니다.** 관찰도 Memory가 쓰고, 측정값도 Memory가 씁니다. 소유한 것은 **문서 행과 도서 캐시** 둘뿐입니다.

| 테이블 | 쓰는 주체 | 읽는 주체 | 성격 |
| --- | --- | --- | --- |
| `growth_doc` | 마이그레이션 시드 | Growth | 문서 행 (교육과정·루틴 자료를 **재작성한** 행) — **핵심 기능의 재료**. 별도 KB 인덱스는 없다 |
| `book_catalog` · `book_query_cache` | 동기화 배치 (`integrations`) | Growth | 도서 API 캐시 |
| `hazard_term` · `hazard_guard` | **Activity 소유** | Activity · **Growth** | 안전 사전 — 구현은 하나만 |
| `notice` | **OCR 파이프라인** (일반 기관 공지). 텍스트 일반 공지 경로 미정 | Growth | 읽기만 · **보조** |

| 읽기만 하는 공유 테이블 | 소유 |
| --- | --- |
| `observation_education` · `observation_routine` · `observation_activity`(참고) | Memory |
| `profile_affinity` (education · routine · activity) | Curator (배치) |
| `child_growth_log` | Memory |
| `child` (생년월일 · `gestational_weeks`) | 앱 |
| `suggestion` | 주입된 writer가 INSERT (`status='draft'`) |

DB 권한: Agent role에 `growth_doc`·`book_catalog` write **비부여**(배치·마이그레이션 role만). Growth는 전 테이블 SELECT + `suggestion` INSERT(writer 경유)뿐입니다.

> **Growth가 소유 테이블이 적은 건 설계대로입니다.** 이 Agent의 출력은 `suggestion`과 readout이고, 판정도 저장도 하지 않습니다. 성장폭 서술은 계산해서 보여주고 버립니다.
>
> Food는 `daycare_meal`로 쓰기를 갖게 됐지만 **Growth는 그대로 쓰기가 없습니다.** 고쳐야 할 기관 데이터가 Growth 쪽에는 없기 때문입니다 — `notice`는 읽기만 하는 보조 입력이고, 관찰과 측정값은 Memory가 씁니다.

### 승인된 추천이 가는 곳

Growth는 관찰을 직접 쓰지 않지만, 보호자가 추천을 승인하면 **Memory가 라벨에 따라** 관찰을 만듭니다.

| 라벨 | 관찰 |
| --- | --- |
| `learning_suggestion` · `book_suggestion` | `observation_education` |
| `routine_coaching` | `observation_routine` |
| `growth_review` | 없음 |

🚨 **`observation_activity`로는 가지 않습니다.** Growth는 놀이 기록을 읽어 학습·루틴으로 잇는 Agent이지 놀이를 추천하는 Agent가 아닙니다. 근거로 인용하는 것과 그 테이블에 쓰는 것은 다른 일입니다.

---

## 1. `growth_doc` — 문서 행

공통 컬럼은 [`RAG_plan.md`](../shared/RAG_plan.md) §1. Growth 전용 컬럼만 적습니다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `row_type` | varchar(24) | `learning_activity` / `routine_step` / `habit_strategy` / `manner_practice` / `rhythm_info` / `book_guide` / `measure_guide` |
| `area` | varchar(24) | nullable. 누리과정 5영역(`physical_health`·`communication`·`social`·`art`·`nature`) / 표준보육과정은 기본생활 포함 6영역 |
| `routine_category` | varchar(24) | nullable. `observation_routine.routine_category`와 **같은 값 집합** |
| `trigger_tags` | text[] | NOT NULL, default `'{}'`. `habit_strategy`용 (`bored` · `anxious` · `tired` …) |
| `materials` | text[] | NOT NULL, default `'{}'`. **`hazard_term` 스캔 대상** |
| `setting` | varchar(16) | `indoor` / `outdoor` / `either` |
| `next_step_of` | uuid | nullable. self → 자립 단계의 앞 단계 행 (`routine_step` 사슬) |

**규칙**
- 조회는 `status='approved'`만. 단계·`row_type`·`routine_category` 필터 → 의미 검색 top-3.
- **행의 `min_month`·`max_month`가 tool 게이트 다음의 두 번째 관문**입니다. tool이 열려도 그 월령 행이 없으면 일반 템플릿으로 갑니다.
- 적재 시 두 검사를 통과해야 합니다 — **평가 표현 lint**(또래·발달·늦·뛰어나 …)와 **`hazard_term` 스캔**(`materials`·`body`가 행의 `min_month`와 충돌하면 오류).
- `next_step_of`로 자립 단계를 사슬로 묶습니다. **`pick_next_step`(코드)이 관찰의 `assistance_level`로 지금 칸을 찾고 사슬의 바로 다음 행을 고릅니다.** 모델은 그 행을 집 상황에 맞춰 문장으로 옮길 뿐이라 단계를 건너뛸 수 없습니다.
- **교육과정 자료와 루틴 자료가 같은 테이블에 있습니다.** 별도 KB 인덱스를 만들지 않습니다 — 조회 경로는 `search_growth_doc` 하나뿐이고, 갈리는 것은 `row_type`과 월령입니다.

**담지 않는 것** — 발달 이정표, "이 나이면 ~할 수 있다" 형태의 문장, 또래 비교. 이런 행은 평가 문장의 씨앗이 됩니다.

---

## 2. `book_catalog` — 도서 캐시

모델이 책 제목을 지어내지 못하게 하는 장치입니다. `propose_books`는 **직전 검색 결과의 ISBN만** 통과시킵니다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `isbn` | text | **PK** (ISBN-13) |
| `title` · `author` · `publisher` | text | `title` NOT NULL |
| `pub_year` | smallint | nullable |
| `age_min_month` · `age_max_month` | smallint | **코드가 정합니다.** API 연령 코드 · 청구기호 · 서명 키워드로 산출 |
| `form` | varchar(16) | `board` / `picture` / `info` / `unknown` |
| `subjects` | text[] | NOT NULL, default `'{}'`. 주제어 (관심사 매칭용) |
| `cover_url` | text | nullable |
| `source` | varchar(24) | `data4library` / `librarian_pick` / `manual` |
| `pick_weight` | smallint | NOT NULL, default 0. 사서 추천 등 가중치 |
| `synced_at` | timestamptz | NOT NULL |

- `form`이 `unknown`이면 **영아기 추천에서 제외**합니다(보드북 여부를 모르면 안전한 쪽).
- 도서 API 한도가 하루 500회(서버 IP 등록 시 30,000회)라 **캐시가 필수**입니다.
- 절판·정보 부족 행은 지우지 않고 `synced_at`으로 노후를 판단합니다.

### `book_query_cache` — 검색어 단위 캐시

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `query_key` | text | PK. `키워드+연령대+form` 정규화 |
| `isbns` | text[] | NOT NULL. 결과 순서 보존 |
| `fetched_at` | timestamptz | NOT NULL |
| `ttl_days` | smallint | NOT NULL, default 30 |

같은 관심사("공룡")가 반복되므로 검색어 캐시가 호출을 가장 크게 줄입니다.

---

## 3. 읽기 전용 외부 테이블

### `hazard_term` (Activity 소유)

Growth의 활동 행과 출력 후보도 같은 사전으로 검사합니다. **구현을 복제하지 않습니다** — 사전이 두 벌이 되면 한쪽만 갱신됩니다.

| Growth가 쓰는 곳 | 시점 |
| --- | --- |
| `growth_doc` 적재 | 배치 (행 `min_month`와 충돌 검사) |
| `propose_learning_activity` 출력 | 사후 필터 |

### `notice` (Memory · OCR 파이프라인 소유)

**일반 기관 공지**(공사·반 이동·교사 변경 등)만 담깁니다. 행사·준비물은 OCR이 Memory로 넘겨 `event`가 되고, 급식표는 `daycare_meal`로 갑니다. **파싱은 Growth의 일이 아닙니다.** 아직 테이블이 없습니다(§4). **보조 입력이라 없어도 핵심 기능(관찰 × 문서 연결 추천)은 온전히 동작해야 합니다.**

### `child_growth_log` (Memory 소유)

Growth는 **차분만** 계산합니다. 백분위·저성장·정체기 판정은 **시스템 어디에서도 하지 않습니다**(Health 성장 판정 제거, 2026-09-22). 판정 요청에는 추이 + 검진 안내만 냅니다.

---

## 4. 공유 테이블 변경 요청 (Growth 소유 아님)

| 테이블 | 요청 | 왜 |
| --- | --- | --- |
| `notice` | 테이블 신설 (**우선순위 낮음**) | 기관 맥락 연결만 빠짐. 핵심 기능과 무관 |
| `child_growth_log` | ✅ 확정(09-22) — `numeric(4,1)` cm/kg · `check_date date NOT NULL` | 성장폭 계산 (G-5 닫힘) |
| `profile_affinity` | `domain`에 `routine` 포함 확인 | Growth는 education·routine·activity 셋을 읽음 |
| `suggestion` | **`kind`만** 추가 · `source_refs` jsonb 제거 → `suggestion_evidence` 테이블 | 개인화/일반 표시 · 근거를 행으로 세기 위해 |

소유자는 Memory·Curator·앱입니다.

---

## 5. DDL (발췌)

```sql
-- 공통 문서 행 컬럼(RAG_plan §1) + Growth 전용
CREATE TABLE growth_doc (
    id               uuid        PRIMARY KEY DEFAULT uuidv7(),
    doc_key          text        NOT NULL UNIQUE,
    row_type         varchar(24) NOT NULL
                     CHECK (row_type IN ('learning_activity','routine_step','habit_strategy',
                                         'manner_practice','rhythm_info','book_guide',
                                         'measure_guide')),
    title            text        NOT NULL,
    body             text        NOT NULL,
    min_month        smallint    NOT NULL,
    max_month        smallint    NOT NULL,
    area             varchar(24),
    routine_category varchar(24)
                     CHECK (routine_category IS NULL OR routine_category IN
                            ('self_care','mealtime','household_task','social_manner','habit','transition')),
    trigger_tags     text[]      NOT NULL DEFAULT '{}',
    materials        text[]      NOT NULL DEFAULT '{}',
    setting          varchar(16) CHECK (setting IN ('indoor','outdoor','either')),
    next_step_of     uuid        REFERENCES growth_doc(id) ON DELETE SET NULL,
    tags             text[]      NOT NULL DEFAULT '{}',
    search_text      text        NOT NULL,
    embedding        vector(1536),
    source_title     text        NOT NULL,
    source_org       text        NOT NULL,
    source_year      smallint    NOT NULL,
    source_locator   text,
    source_url       text,
    license_basis    varchar(16) NOT NULL
                     CHECK (license_basis IN ('public_law','kogl_1','kogl_2','kogl_3','kogl_4',
                                              'fact_rewrite','permission')),
    status           varchar(16) NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','approved','retired')),
    authored_by      text        NOT NULL,
    reviewed_by      text,
    reviewed_at      date,
    version          smallint    NOT NULL DEFAULT 1,
    CHECK (min_month < max_month),
    CHECK (reviewed_by IS NULL OR reviewed_by <> authored_by),
    CHECK (status <> 'approved' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)),
    CHECK (row_type <> 'habit_strategy' OR min_month >= 36)
);
CREATE INDEX growth_doc_lookup_idx ON growth_doc (row_type, min_month, max_month)
    WHERE status = 'approved';

CREATE TABLE book_catalog (
    isbn          text        PRIMARY KEY,
    title         text        NOT NULL,
    author        text,
    publisher     text,
    pub_year      smallint,
    age_min_month smallint,
    age_max_month smallint,
    form          varchar(16) NOT NULL DEFAULT 'unknown'
                  CHECK (form IN ('board','picture','info','unknown')),
    subjects      text[]      NOT NULL DEFAULT '{}',
    cover_url     text,
    source        varchar(24) NOT NULL
                  CHECK (source IN ('data4library','librarian_pick','manual')),
    pick_weight   smallint    NOT NULL DEFAULT 0,
    synced_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX book_catalog_age_idx ON book_catalog (age_min_month, age_max_month);
CREATE INDEX book_catalog_subject_idx ON book_catalog USING gin (subjects);

CREATE TABLE book_query_cache (
    query_key  text        PRIMARY KEY,
    isbns      text[]      NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    ttl_days   smallint    NOT NULL DEFAULT 30
);
```

`CHECK (row_type <> 'habit_strategy' OR min_month >= 36)` — 36개월 미만 습관 교정 금지를 **DB에서** 막습니다. 게이팅이 뚫려도 행 자체가 없습니다.

---

## 6. 구현 메모

- `growth_doc`·`book_catalog`는 **아이 데이터가 아닙니다.** 파기·보관 정책 대상이 아닙니다
- 교육과정도 루틴 자료도 별도 KB가 아니라 `growth_doc`의 행입니다. 원문 청크 색인을 만들지 않습니다(라이선스·품질). 조회 경로는 `search_growth_doc` 하나입니다
- 도서 연령 필터는 **코드가 주입**합니다. 모델이 "좀 더 큰 아이용"을 고를 수 없습니다
- `hazard_term` 조회 실패는 빈 목록으로 폴백하지 않습니다 — 예외로 올리고 활동 추천을 닫습니다
- 로그에는 `{kind, id}`만

---

## 7. 미결

| # | 쟁점 | 메모 |
| --- | --- | --- |
| GT-1 | `growth_doc` 초기 행 수 | 루틴 40 · 교육 60 · 습관 20 · 예절/리듬 20 · 도서 8 |
| GT-2 | 해설서·사례집의 라이선스 | 전부 `fact_rewrite`로 시작 |
| GT-3 | 도서 API 연령 파라미터 코드 | 활용가이드 확인 전 `age_min/max_month` 산출식 확정 금지 |
| GT-4 | `next_step_of` 사슬을 어디까지 만들지 | 양치·배변·옷 입기 3종부터 |
| GT-5 | `notice` 스키마 | OCR 팀 결정 대기. 우선순위 낮음 |
| GT-6 | ~~`observation_education.source_suggestion_id`~~ | ✅ **폐기(09-22)** — 두지 않는다. 추천 → 관찰의 역방향 추적은 v1 범위 밖 |
| GT-7 | 루틴 행의 원문 출처 | 배변·수면 자료의 공신력 있는 원문 확보. 별도 KB가 아니라 `growth_doc` 행으로 들어간다 |
