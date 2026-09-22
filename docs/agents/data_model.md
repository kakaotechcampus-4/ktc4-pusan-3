## Identity

### parent

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| nickname | text | nullable. 표시 이름. 보호자 목록·이관 화면·기록 작성자 표시에 필요 |
| deleted_at | timestamptz | nullable. 계정 탈퇴 요청 시각, N일 후 파기 |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now(), on update now(). nickname 등 수정 시각 추적 |

### auth_identity

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK |
| parent_id | uuid | FK → parent.id, NOT NULL, ON DELETE CASCADE |
| provider | enum | kakao / apple / google / naver, NOT NULL |
| provider_user_id | text | 제공자 발급 식별자. 카카오는 회원번호(AppUserId), NOT NULL |
| linked_at | timestamptz | NOT NULL, default now() |

**제약**

- `UNIQUE (provider, provider_user_id)`
- 제공자 부가정보(이메일·프로필 이미지 등) 미저장 — NF-04 최소 수집

**구현 메모 (Identity — parent, auth_identity)**

- `parent.nickname` → NOT NULL로 확정
- `parent.updated_at` 컬럼 추가 (nickname 등 수정 시각 추적용)
- `auth_identity.parent_id` FK에 `ON DELETE CASCADE` 추가 (parent 파기 시 연쇄 삭제)
- `auth_identity.provider` enum 값이 `kakao/apple/google/naver` 그대로 저장되도록 SQLAlchemy `values_callable` 지정 (기본값 사용 시 대문자로 저장되는 버그 방지)
- `provider`는 postgres native ENUM 타입 대신 VARCHAR + CHECK 제약으로 구현 (`native_enum=False, create_constraint=True`) — 값 추가/삭제가 훨씬 가벼고 Alembic autogenerate가 더 잘 잡아줌

## Child

### parent-child

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK. surrogate PK로 구현 (아래 구현 메모 참고, (parent_id, child_id)는 UNIQUE 제약으로만 유지) |
| parent_id | uuid | FK → parent.id, NOT NULL, ON DELETE CASCADE |
| child_id | uuid | FK → child.id, NOT NULL, ON DELETE CASCADE |
| relation | enum | mother / father / grandparent / sitter / other, NOT NULL |
| connected_at | timestamptz | 연결 시각, NOT NULL, default now() |

### child

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| nickname | text | NOT NULL. 아이의 이름, 가명, 별칭 아무거나 상관없음 |
| owner_parent_id | uuid | FK → parent.id, NOT NULL, ON DELETE 미지정(NO ACTION). 대표 권한자 1명. 이관은 이 값 변경 |
| birth_date | date | NOT NULL. 나이는 계산 (규칙, 저장 X) |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now(), on update now(). 최근 수정 시각 추적 |
| deleted_at | timestamptz | nullable. 값이 있으면 전원 접근 차단 |
| deleted_by | uuid | FK → parent.id, nullable, ON DELETE SET NULL |
| gender | — | **수집하지 않는다.** 성장 판정 제거(2026-09-22)로 소비처가 사라졌다 |
| allergy_status | enum | `none` / `has` / `unknown`, NOT NULL, default `unknown`. **F-4.** `health_safety` 0행이 "없다고 확인함"인지 "물어본 적 없음"인지를 가른다 — 행으로는 표현할 수 없다 |
| gestational_weeks | smallint | nullable. 조산(37주 미만)이면 24개월까지 교정연령 계산 |

#### child_growth_log (키·몸무게 로그) — 이름·타입 확정 2026-09-22

| 필드 | 타입 | 비고  |
| --- | --- | --- |
| id | uuid | PK, `uuidv7()` |
| child_id | uuid | FK → `child.id`, NOT NULL, ON DELETE CASCADE |
| height | numeric(4,1) | **cm.** 95.5 같은 값이 들어간다 — int면 성장폭이 계단처럼 나온다 |
| weight | numeric(4,1) | **kg.** 14.2 |
| check_date | date | **NOT NULL.** `timestamptz`가 아니다 — 잰 날짜만 의미가 있다 |
| created_at / updated_at | timestamptz | NOT NULL, default now() |

- 소유는 Memory. Growth가 성장폭 서술에, Food가 권장 열량 계산에 **읽기만** 한다
- 측정 출처(보호자 측정 / 검진)는 저장하지 않는다. 그래서 화면에 **측정일을 반드시 함께** 보여준다
- 백분위·BMI·또래 비교는 **어디서도 하지 않는다**. Growth가 로그 전부를 시간순으로 늘어놓을 뿐이다

### invite

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| child_id | uuid | FK → child.id, NOT NULL, ON DELETE CASCADE |
| created_by | uuid | FK → parent.id, nullable. 발행한 owner (owner 파기 시 SET NULL) |
| token | text | UNIQUE, NOT NULL. 딥링크와 코드가 공유 |
| expires_at | timestamptz | NOT NULL |
| used_at | timestamptz | nullable. 값이 있으면 재사용 불가 |
| used_by | uuid | FK → parent.id, nullable, ON DELETE SET NULL |

**구현 메모 (Child — parent_child, child, invite)**

- `parent_child`는 별도 uuid PK(`id`)로 구현하고 `(parent_id, child_id)`는 `UNIQUE` 제약으로만 유지
- `parent_child.parent_id`/`child_id`, `invite.child_id`에 `ON DELETE CASCADE` — 부모·아이가 파기되면 연결된 관계·남은 초대도 같이 정리
- `child.owner_parent_id`는 의도적으로 ON DELETE 미지정 — postgres 기본값인 NO ACTION이 적용되어 owner가 다른 보호자에게 이관하기 전까지는 파기 불가, 아이 데이터 유실 방지가 우선
- `child.deleted_by` / `invite.created_by` / `invite.used_by`(감사용 FK 3개) 모두 `ON DELETE SET NULL`로 통일 — 가리키는 보호자가 파기되어도 누가 했는지 기록은 잃을 뿐 파기는 막히지 않음
- `invite.created_by`를 이 정책에 맞추려고 NOT NULL → nullable로 변경 — "생성 시점엔 발행자가 있어야 한다"는 앱 단의 규칙이고, 행이 존재하는 동안 영구히 유지되어야 하는 DB 제약은 아니라고 판단
- `relation` enum도 identity와 동일하게 VARCHAR + CHECK로 구현

## Consent

### consent

동의 이력은 **append-only**다. 철회와 재동의도 기존 행을 수정·삭제하지 않고 새 행으로 쌓으며, 현재 상태는 대상과 scope가 같은 행 중 `acted_at`이 가장 최신인 action으로 계산한다.

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| subject_parent_id | uuid | 계정 단위 동의 대상. FK → `parent.id`, nullable, `ON DELETE RESTRICT` |
| child_id | uuid | 아동 단위 동의 대상. FK → `child.id`, nullable, `ON DELETE RESTRICT` |
| actor_parent_id | uuid | 실제 동의 행위자. FK → `parent.id`, nullable, `ON DELETE SET NULL`. 행위자가 탈퇴해도 동의 효력은 유지 |
| actor_ref | uuid | NOT NULL. 동의 시점의 행위자 ID 스냅샷이며 FK가 아님 |
| scope | enum | `service_terms / privacy_account / child_basic / child_health / quality_improve`, NOT NULL |
| action | enum | `granted / withdrawn`, NOT NULL |
| policy_version_id | uuid | 동의 당시 정책 본문. FK → `policy_version.id`, NOT NULL, `ON DELETE RESTRICT` |
| guardian_attested | boolean | nullable. 아동 단위 동의의 법정대리인 자기확인 여부 |
| acted_at | timestamptz | NOT NULL, default `now()` |

**DB 제약**

- 계정 scope(`service_terms`, `privacy_account`)는 `subject_parent_id`만, 아동 scope는 `child_id`만 가져야 한다. 둘 다 채우거나 둘 다 비울 수 없다.
- 동의 대상 FK와 정책 버전 FK는 `RESTRICT`다. 증빙 이관 없이 대상이나 정책 본문을 삭제하는 경로를 DB가 차단한다.
- 동의 행위자와 대상은 분리한다. Owner 이관이나 행위자 탈퇴가 기존 아동 동의를 무효화하지 않는다.
- 같은 action 재요청은 repository에서 멱등 처리하여 이력을 중복 생성하지 않는다.

#### consent_scope 정의

| **값** | **단위** | **필수/선택** | **비고** |
| --- | --- | --- | --- |
| service_terms | 계정 | 필수 | 서비스 이용약관 |
| privacy_account | 계정 | 필수 | 개인정보 수집·이용 (보호자 본인) |
| child_basic | 아동 | 필수 | 개인정보 수집·이용 (아이 기본정보) |
| child_health | 아동 | 필수 | 민감정보 처리 (아이 건강·알레르기) |
| quality_improve | 아동 | 선택 | 거부해도 기본 서비스 동작 |

### policy_version

정책 본문의 정본이며 **immutable**이다. 문구가 바뀌면 기존 행을 고치지 않고 같은 scope에 새 version 행을 추가한다.

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| scope | enum | `consent_scope`, NOT NULL |
| version | text | NOT NULL. `UNIQUE(scope, version)` |
| content | text | NOT NULL. 실제 동의 본문 |
| content_hash | text | NOT NULL. 본문 무결성 확인값 |
| effective_at | timestamptz | NOT NULL. 효력 시작 시각 |
| ended_at | timestamptz | nullable. 효력 종료 시각 |
| created_at | timestamptz | NOT NULL, default `now()` |
- 활성 버전은 `effective_at <= now`이고 `ended_at IS NULL OR ended_at > now`인 행이다.
- update/delete repository는 두지 않는다. `consent.policy_version_id`로 동의 당시 본문을 재현한다.

### consent_retention

대상 hard delete 전에 운영 동의 이력을 옮겨 두는 **구현 완료된 증빙 보관 테이블**이다.

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| source_consent_id | uuid | 원본 consent ID 스냅샷, NOT NULL, FK 아님 |
| actor_ref | uuid | 동의 행위자 스냅샷, NOT NULL, FK 아님 |
| subject_parent_ref | uuid | 계정 대상 스냅샷, nullable, FK 아님 |
| child_ref | uuid | 아동 대상 스냅샷, nullable, FK 아님 |
| scope / action | enum | 원본 동의의 scope와 action, NOT NULL |
| policy_version_id | uuid | FK → `policy_version.id`, NOT NULL, `ON DELETE RESTRICT`. 보관 중에도 본문 재현 |
| guardian_attested | boolean | 원본 자기확인 값, nullable |
| acted_at | timestamptz | 원본 동의 시각, NOT NULL |
| retained_at | timestamptz | 대상 hard delete 시각, NOT NULL |
| purge_at | timestamptz | `retained_at + 365일`, NOT NULL. 현재 서비스 정책이며 법정 기간은 아님 |

**삭제 순서**: 증빙 복사 → 운영 `consent` 삭제 → 대상 hard delete. 호출자의 단일 트랜잭션 안에서 수행해 중간 실패 시 모두 롤백한다. 만료된 보관 행은 worker가 `purge_at` 기준으로 삭제한다.

## Memory/Observation

### observation 계층 테이블 5개(food/health/education/activity/routine)

### 0. 공통 컬럼

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()` |
| child_id | uuid | NOT NULL. FK → `child.id`, `ON DELETE CASCADE` |
| raw_text | text | NOT NULL, 원본 발화/관찰 내용 |
| subject | text | NOT NULL (임베딩·병합 판정 입력이라 값이 항상 있어야 함). 정규화 대상 문자열 |
| embedding | vector(1536) | nullable, `subject`를 임베딩한 벡터 |
| affinity_id | uuid | nullable. FK → `profile_affinity.id`, `ON DELETE SET NULL`. 소속 프로필 및 집계 키 |
| polarity | smallint | NOT NULL, DEFAULT `0`, `-1 / 0 / 1`만 허용 |
| strong_signals | text[] | NOT NULL, DEFAULT `'{}'`. 허용값: `resistance_to_redirect`, `self_initiated`, `comparative_choice`, `asks_questions`, `role_extension` |
| confidence_source | text | NOT NULL. `institution_notice / parent_direct / parent_hedged / parent_hearsay` |
| status | enum | NOT NULL, DEFAULT `active`. `active / stand_alone / inactive`  |
| observed_range | daterange | NOT NULL, 빈 범위 불가, 상한이 무한대인 범위 불가 |
| source_writer | uuid  |  FK → `parent.id`  set null  |
| source_notice_id | uuid | FK → `notice.id`, nullable. 값이 존재하면 기관 공지 기반 |
| created_at | timestamptz | NOT NULL, DEFAULT `now()` |
| updated_at | timestamptz | NOT NULL, DEFAULT `now()` |
- `health` 에서는 다음 필드 제외 제외: `subject` · `embedding` · `affinity_id` · `polarity` · `strong_signals` (승격 파이프라인 밖이라 profile 행 자체가 없음)

### 1. observation_food (섭취·영양)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| subject(공통컬럼) | text | 대상 음식, 예: "당근", NOT NULL |
| action | text | "먹었다" / "뱉었다" / "남김" |
| amount | text | "반 그릇" / "다 먹음" — 값 집합 미확정이라 enum 대신 text로 구현 |
| reaction | text | "좋아함" / "싫어함" / "무반응" — 값 집합 미확정이라 enum 대신 text로 구현 |

### 2. observation_health (증상·컨디션)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| symptom | text[] | "발열" / "콧물" / "두드러기", NOT NULL |
| severity | enum | mild / moderate / severe / emergency |
| body_part | text | "얼굴" / "팔" |
| suspected_trigger | text | "우유 먹은 뒤"/”쌓기놀이 직후” |
| action_taken | text | "병원" / "해열제" / "관찰" |
| observed_time | timestamptz | 관찰 시점, nullable |

### 3. observation_education (학습 세션)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| topic | text | "숫자세기" / "한글 자모", NOT NULL |
| session_type | text | "책읽기" / "놀이학습" / "활동지" |
| duration_min | int | 세션 길이(분) |
| engagement_level | enum | low / mid / high |

### 4. observation_activity (놀이·활동)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| activity | text | "레고 조립" / "블록놀이", NOT NULL |
| location | text | "집" / "기관" / "놀이터" |
| companions | text | "혼자" / "친구와" / "부모" |
| duration_min | int | 활동 길이(분) |
| engagement_level | enum | low / mid / high |

### 5. observation_routine (일상적인 행동) - (ENUM만 다시 파악하기)

아이의 반복적 생활 행동, 자립 수행, 생활 습관 또는 사회적 생활기술에 대한 관찰

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| subject | text | 공통 컬럼. 정규화된 행동 대상. `"양치하기"`, `"손톱 물어뜯기"`, `"인사하기"` |
| routine_category | enum | `self_care / mealtime / household_task / social_manner / habit / transition` ,not null |
| context | text | 행동이 나타난 상황. `"식사 중"`, `"등원 준비"`, `"놀이 후"`, `"잠들기 전"` , nullable |
| assistance_level | enum | `independent / verbal_prompt / partial_assist / full_assist`, nullable |
| completion_status | enum | `completed / partial / refused / interrupted`, nullable |
| trigger | text | 특정 행동을 유발한 맥락. `"긴장할 때"`, `"심심할 때"`, `"부모가 정리를 요청했을 때"` 등, nullable |
- 예시
    
    
    | raw_text | subject | routine_category | context | assistance_level | completion_status |
    | --- | --- | --- | --- | --- | --- |
    | 혼자 양치하고 잠옷도 입었어 | 양치하기 | self_care | 자기 전 | independent | completed |
    | 외출할 때 몇 번 말해야 인사해 | 인사하기 | social_manner | 외출/만남 | verbal_prompt | completed |
    | 요즘 손톱을 자주 물어뜯어 | 손톱 물어뜯기 | habit | NULL | NULL | NULL |
    | 밥 먹을 때 포크만 사용해 | 식사도구 사용 | mealtime | 식사 중 | independent | NULL |
    | 장난감 정리하라고 하면 혼자 잘 정리해 | 장난감 정리 | household_task | 놀이 후 | verbal_prompt | completed |

**구현 메모 (Memory/Observation — food, health, education, activity, routine)**

- PK 기본값: 노션엔 `gen_random_uuid()`로 있었으나 다른 테이블과 동일하게 `uuidv7()`로 통일해 구현
- `child_id`(CASCADE) / `affinity_id`(SET NULL) / `source_writer`(SET NULL) FK 적용. 작성자가 탈퇴해도 공동 관찰 기록은 유지됨
- `subject`: 노션엔 nullable로 있었으나 NOT NULL로 확정 (임베딩·병합 판정 입력이라 값이 항상 있어야 함)
- `confidence`(`confidence_source` 값으로 0.9/0.8/0.5/0.3을 계산하는 generated 컬럼)를 한때 추가했다가 제거 — `confidence_source` 원본 값만 저장하고 점수화는 상위 레이어 책임으로 이동
- `health`는 `ObservationCommon`을 상속하지 않고 필요한 공통 필드를 직접 선언 (승격 파이프라인 밖이라서)
- `observation_food.amount`/`reaction`: 노션엔 enum으로 있었으나 값 집합이 아직 미확정이라 text로 구현, 검증은 애플리케이션 레이어
- `observation_health.observed_time`: 노션엔 default now()로 있었으나 구현은 default 없이 nullable
- `observation_routine`: 노션 원안 그대로 구현 (ObservationCommon 상속 + `routine_category`/`context`/`assistance_level`/`completion_status`/`trigger`).

**미결**: `observed_range` 빈 범위·무한 상한 금지 CHECK는 아직 없음. `source_notice_id` FK는 `notice` 도메인 생성 후 추가

## Memory/Profile

### profile_affinity(routine 추가 대응하기)

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| id | uuid | PK, DEFAULT `uuidv7()`  |
| child_id | uuid | NOT NULL. FK → `child.id`, `ON DELETE CASCADE` |
| merge_key | text | NOT NULL, 사람이 읽는 프로필 라벨. 자유롭게 변경 가능 |
| embedding | vector(1536) | nullable, `merge_key`를 임베딩한 벡터. 병합 후보 검색용 |
| domain | text | NOT NULL. `food / activity / education/ routine` |
| state | text | NOT NULL, DEFAULT `candidate`. `candidate / confirmed / archived` |
| polarity | smallint | nullable, `-1 / 1`만 허용. 단, `confirmed` 상태에서는 NOT NULL |
| strength | real | NOT NULL, DEFAULT `0.3`. 프로필 강도 |
| last_observed_on | date | NOT NULL, 마지막 관찰 날짜 |
| created_at | timestamptz | NOT NULL, DEFAULT `now()` |
| updated_at | timestamptz | NOT NULL, DEFAULT `now()` |

**구현 메모 (Memory/Profile — profile_affinity)**

- PK 기본값 `uuidv7()`로 통일 (observation과 동일)
- `child_id`: FK → `child.id`, `ON DELETE CASCADE` 추가 완료 (이전엔 plain uuid)
- `polarity`가 `confirmed` 상태에서 NOT NULL이어야 한다는 제약은 아직 CHECK로 걸려있지 않음 (ORM 레벨은 nullable)

**미결**: `polarity` NOT NULL(when confirmed) CHECK — 아직 추가 안 됨

## Saftey

### health_safety (안전·제약) — 알레르기 + 만성질환, 강등 X - 시하님과 얘기

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| child_id | uuid | NOT NULL. FK → `child.id`, `ON DELETE CASCADE` |
| kind | enum | allergy / chronic_disease / dietary_restriction / behavioral / environmental / other_medical, NOT NULL |
| label | text | 아이가 앓고 있는 만성 질환 및 알레르기 종류 이름. "우유" / "소아 당뇨" / "천식" / "고소공포", NOT NULL |
| aliases | text[] | 별칭 배열, default '{}' |
| category | text | nullable. kind별로 달라지는 하위 분류. allergy → "식품"/"약물"/"환경", chronic_disease → "내분비"/"호흡기" 등 — kind마다 값 집합이 달라 enum 대신 text로 구현, 검증은 애플리케이션 레이어 |
| severity | enum | mild / moderate / severe / anaphylaxis (nullable) |
| reactions | text[] | 알레르기 반응 목록, 예: ["두드러기","호흡곤란"], default '{}' |
| management | jsonb | 만성질환 관리 정보. 예: `{insulin:"식전", carb_limit_g:150, meds:[…]}`, NOT NULL, default '{}' |
| notes | text | 보호자 자유 기술 |
| state(수정 필요)  | enum | `active / retracted`, NOT NULL, DEFAULT `active`
(active / retracted/none /unknown, NOT NULL default unknown으) |
| created_by | uuid | nullable. FK → `parent.id`, `ON DELETE SET NULL`. 최초 등록 보호자 |
| updated_by | uuid | nullable. FK → `parent.id`, `ON DELETE SET NULL`. 마지막 수정 보호자 |
| created_at | timestamptz | default now() |
| updated_at | timestamptz | default now() |

제약:

- `UNIQUE(child_id, kind, label)`
- 안전 조회는 항상 `state='active'` 필터
- DB 계층: Agent/Curator role 에는 이 테이블 write 권한 비부여 (권한 분리로 LLM 쓰기 원천 차단)

**구현 메모 (Safety — health_safety)**

- `state`(active/retracted) 컬럼을 표에 추가 — "안전 조회는 항상 state='active' 필터" 문구를 실제 컬럼으로 구현한 것 (감쇠 없음 — 보호자만 retracted로 전환)
- `category`: kind마다 값 집합이 달라 enum이 아니라 text로 구현, 검증은 애플리케이션 레이어
- `management`: 구현은 NOT NULL, default `{}`
- `child_id`(CASCADE) / `created_by`(RESTRICT) / `updated_by`(SET NULL) FK 추가 완료 (이전엔 plain uuid) — `created_by`가 RESTRICT인 건 child 섹션의 "감사용 FK는 SET NULL" 원칙과 다름, 의도적인지 확인 필요

**미결**: `UNIQUE(child_id, kind, label)` 제약이 아직 ORM `__table_args__`에 선언되지 않음 — autogenerate라 마이그레이션도 못 잡음, 수동 추가 필요. Agent/Curator role의 write 권한 분리(DB 계층 권한)도 아직 미구현

### Food 영양소

- 1/7/30 영양소 계산
- 급식 메뉴 text []
- 간식 여부
- 열량
- 단백질

## Suggestion

### suggestion (추천 1건)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| child_id | uuid | NOT NULL. FK → `child.id`, `ON DELETE CASCADE` |
| kind | enum | `general` / `personalized`, NOT NULL. **근거 0행이면 `general`이 정상**이고, 그래서 개인화로 집계되면 안 된다 |
| feedback | enum | `liked` / `disliked` / `not_acted` , nullable. 승인 뒤 만들어진 관찰의 `polarity`(+1 / −1 / 0)로 이어진다 — `not_acted`는 "안 했다"가 아니라 "반응이 딱히 없음" |
| ~~source_refs~~ | — | **제거.** 근거는 `suggestion_evidence` 테이블로 옮겼다 (2026-09-22) |
| agent | enum | `food` / `activity` / `growth` / `health` |
| content | text | NOT NULL. 실제로 추천하는 내용 |
| reason | text | nullable. 근거 문구 |
| status | enum | `draft` / `approved` / `rejected` / `expired`, NOT NULL, default `draft` |
| expires_at | timestamptz | 생성 +24h, NOT NULL |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now(), on update now() |

### suggestion_evidence (추천이 쓴 근거)

루트 CLAUDE.md §2가 이름으로 지목한 테이블이다 — "개인화 추천에는 사용한 `memory_id`를 반드시 첨부한다. 0행이면 버그(품질 지표 하드 기준 0건)". jsonb로 두면 그 지표를 COUNT로 셀 수 없어 행으로 뺐다.

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| suggestion_id | uuid | FK → `suggestion.id`, NOT NULL, `ON DELETE CASCADE` |
| memory_kind | text | NOT NULL. 아래 두 무리 중 하나 |
| memory_id | uuid | NOT NULL. **다형 참조라 FK가 아니다** — `correction.target_id`와 같은 패턴이고, 대상 존재 여부와 같은 `child_id`인지는 서버가 검증한다 |
| created_at | timestamptz | NOT NULL, default `now()` |

- `PRIMARY KEY (suggestion_id, memory_kind, memory_id)`
- `memory_kind` 인덱스를 따로 둔다 — 품질 지표가 kind로 거르기 때문

**`memory_kind`는 두 무리다.**

| 무리 | 값 | 개인화 근거로 셈 |
| --- | --- | --- |
| 아이 기록 | `observation_food` · `observation_health` · `observation_education` · `observation_activity` · `observation_routine` · `profile_affinity` · `child_growth_log` · `notice` · `intake_daily` | ✅ |
| 문서 행 | `food_doc` · `growth_doc` · `activity_doc` | ❌ 참고만 (Health는 문서 행을 쓰지 않는다 — 상수 파일이다) |

🚨 **품질 지표는 아이 기록만 센다.** `kind='personalized'`인데 아이 기록 행이 0이면 버그다 — 문서 행만 달고 나가면 COUNT는 통과하지만 근거 없는 추천이다.

**구현 메모 (Suggestion — suggestion)**

- `child_id`: FK → `child.id`, `ON DELETE CASCADE` 추가 완료 (이전엔 plain uuid)
- `id` PK 기본값은 다른 테이블과 동일하게 `uuidv7()`

**미결**: `suggestion_evidence` 모델·마이그레이션 미구현. `suggestion.kind` 컬럼도 아직 없음. `source_refs` jsonb는 제거 대상이다 (2026-09-22 결정)

## Correction

### correction(verdict 대응하기)

관찰 5종과 `profile_affinity`에 대한 보호자 판정 이력이다. **append-only**이며 되돌리기도 UPDATE가 아니라 새 판정 행을 추가하는 방식이다.

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK |
| child_id | uuid | FK → `child.id`, NOT NULL, `ON DELETE CASCADE`. 아이별 이력 조회와 대상 검증에 사용 |
| target_kind | enum | `observation_food / observation_health / observation_education / observation_activity / observation_routine / profile_affinity` |
| target_id | uuid | NOT NULL. 다형 참조라 FK가 아니며, 대상 존재 여부와 같은 `child_id`인지는 서버가 검증 |
| verdict | enum | 관찰: `once_only / wrong`
프로필: `need_more_observation / outdated / wrong` |
| created_by | uuid | 작성자 감사 정보. FK → `parent.id`, nullable, `ON DELETE SET NULL` |
| created_at | timestamptz | NOT NULL, default `now()` |

**제약과 효과**

- DB CHECK가 대상 종류와 verdict의 허용 조합을 강제한다.
- `confirm`은 저장하지 않는다. “맞아요”는 상태를 바꾸지 않으므로 이력 값에서도 제외했다.
- 관찰의 `once_only`는 `status = stand_alone`으로 바꿔 검색에는 남기고 Curator 집계에서만 제외한다.
- 관찰의 `wrong`은 `status = inactive`로 바꾸고 같은 affinity를 재계산한다.
- 프로필의 `need_more_observation`은 한 단계 강등, `outdated`와 `wrong`은 archived 처리한다. 이 상태 전이 서비스 로직은 후속 구현 범위다.
- `target_kind + target_id`, `child_id` 조회 인덱스를 둔다.

## Schedule

### event (보호자가 제출한 일정)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, UUIDv7 |
| child_id | uuid | FK → `child.id`, NOT NULL, ON DELETE CASCADE |
| title | text | NOT NULL |
| event_type | enum | `core | episodic`, NOT NULL |
| starts_at | timestamptz | NOT NULL |
| ends_at | timestamptz | nullable |
| all_day | bool | NOT NULL, DEFAULT false |
| category | enum | `institution | health | activity | etc`, NOT NULL |
| created_by | enum | `agent | caregiver`, NOT NULL |
| source_notice_id | uuid | nullable, 현재 FK 없는 근거 식별자 |
| source_refs | jsonb | nullable, 근거 참조 |
- `status`, `expires_at`, `EventStatus`는 제거됐다.
- DB에는 보호자가 제출한 일정만 저장한다. Agent 초안은 DB에 저장하지 않고 SSE payload로 전달한다 (2026-09-22 확정). **복약 초안도 같은 방식이다** — `medication_schedule`에 `draft` 상태를 두지 않는다.
- 일정 취소는 상태 변경이 아니라 **hard delete**이며, 연결된 `event_item`·`reminder`는 CASCADE 삭제된다.

### event_item (준비물 체크리스트)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| item_id | uuid | PK, UUIDv7 |
| event_id | uuid | FK → `event.id`, NOT NULL, ON DELETE CASCADE |
| item_name | text | NOT NULL. "수영복", "여벌옷" |
| is_prepared | bool | NOT NULL, DEFAULT false |
| prepared_at | timestamptz | nullable |
| created_at / updated_at | timestamptz | NOT NULL, 공통 timestamp mixin |

### reminder (보호자별 일정 알림)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, UUIDv7 |
| event_id | uuid | FK → `event.id`, NOT NULL, ON DELETE CASCADE, index |
| parent_id | uuid | FK → `parent.id`, NOT NULL, ON DELETE CASCADE, index |
| remind_at | timestamptz | NOT NULL |
| sent_at | timestamptz | nullable, 실제 발송 시점 |

`parent_id`로 같은 Event를 보는 보호자마다 독립적인 알림을 소유한다.

### push_device (푸시 수신 기기)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, UUIDv7 |
| parent_id | uuid | FK → `parent.id`, NOT NULL, ON DELETE CASCADE, index |
| device_token | text | NOT NULL, UNIQUE. APNs/FCM 원문 토큰 |
| platform | enum | `ios | android`, NOT NULL |
| enabled | bool | NOT NULL, DEFAULT true |
| last_seen_at | timestamptz | NOT NULL, DEFAULT now() |
| created_at / updated_at | timestamptz | NOT NULL |

같은 기기에서 계정이 바뀌면 upsert 시 `parent_id`, `platform`, `enabled`, `last_seen_at`을 함께 갱신한다.

### diary_entry (보호자 개인 일기)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, UUIDv7 |
| child_id | uuid | FK → `child.id`, NOT NULL, ON DELETE CASCADE |
| author_parent_id | uuid | FK → `parent.id`, NOT NULL, ON DELETE CASCADE |
| date | date | NOT NULL |
| content | text | NOT NULL |
| created_at / updated_at | timestamptz | NOT NULL |
- UNIQUE: `(child_id, author_parent_id, date)`
- 작성자만 조회·수정·삭제하는 개인 데이터다.
- 보호자–아이 연결 해제 시 일기 정리는 FK가 아닌 서비스 계층 책임이다.

### shared_photo (연결 보호자 공유 사진)

| **필드** | **타입** | **비고** |
| --- | --- | --- |
| id | uuid | PK, UUIDv7 |
| child_id | uuid | FK → `child.id`, NOT NULL, ON DELETE CASCADE |
| uploader_parent_id | uuid | FK → `parent.id`, nullable, ON DELETE SET NULL |
| date | date | NOT NULL |
| image_url | text | NOT NULL |
| created_at / updated_at | timestamptz | NOT NULL |
- 아이와 연결된 보호자들이 공동 조회·관리한다.
- 업로더 탈퇴 시 사진 행은 유지되고 작성자만 NULL 처리한다.
- 아이 삭제 시 DB 행은 CASCADE 삭제되지만, 실제 스토리지 파일 삭제는 서비스/스토리지 계층에서 별도 처리해야 한다.

### calendar 화면용 read model (물리 테이블 없음)

기존 `calendar` 테이블은 제거됐다. 캘린더 화면은 날짜별 `event` + 로그인 보호자의 `diary_entry` + `shared_photo` + observation을 조합해 제공한다.

**구현·검증 상태**

- Calendar 제거와 5개 모델 변경은 마이그레이션에 반영됐고 Alembic head는 `1421f6d856de` 하나다.
- Schedule 모델 테스트와 작성자 탈퇴 통합 테스트가 통과했다.
- Agent SSE 초안 흐름(#110), 실제 CRUD/API·인가·스토리지 파일 정리 및 계약 문서(#121)는 후속 작업이다.