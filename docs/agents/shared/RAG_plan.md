# RAG_plan — 도메인별 근거 문서 테이블 전처리 계획

> 대상: Food · Growth · Health (Activity는 `activity_doc` 선례로 함께 정렬)
> 관련: `activity/`(Activity 담당자 몫 — 아직 없음) D6·D10 · [`Food_Tool_명세.md`](../food/Food_Tool_명세.md) · [`Growth_Tool_명세.md`](../growth/Growth_Tool_명세.md) · [`Health_Tool_명세.md`](../health/Health_Tool_명세.md)

---

## 0. 원칙

1. **원문을 청크로 자르지 않는다.** 사람이 원문을 읽고, 추천에 필요한 내용만 **한 행 = 한 가지 쓸모**로 새로 쓴다.
2. **행마다 원문 출처를 남긴다.** 문서명 · 발행처 · 연도 · 쪽/조항. 라이선스 문제가 생기면 **행 단위로 뺄 수 있어야** 한다.
3. **문서 행은 개인화 근거가 아니다.** `suggestion_evidence` 에 `source_kind='*_doc'` 으로 같이 쌓되 품질 지표에서는 빼고 센다. 섞이면 "근거 0행인데 개인화"가 문서 행 때문에 가려진다.
4. **숫자 기준표는 문서 테이블에 넣지 않는다.** 섭취기준 · 검진 주기 · 접종 일정 · 위험 월령은 **버전 붙은 상수 파일**이다. 문서 행은 "무엇을 어떻게 하나"의 서술만 담는다.
5. **도메인마다 쓰임이 다르다.**

| 도메인 | 테이블 | 조회 방식 | 모델이 하는 일 |
| --- | --- | --- | --- |
| Food | `food_doc` | 단계 필터(SQL) → 의미 검색 | 예시로 참고해 후보·설명 작성 |
| Growth | `growth_doc` | 단계·영역 필터 → 의미 검색 | 예시로 참고해 활동·루틴 제안 작성 |
| Activity | `activity_doc` | 월령 슬라이스 필터 → 의미 검색 | few-shot 예시 |
| **Health** | **테이블 없음** — `reference/health_phrases.yaml` | 키 정확 일치 (코드) | **없음.** 문구를 그대로 출력 (`authored_by="code"`) |

> Health는 "모델이 출처를 읽고 답하는 경로를 만들지 않는다"는 기존 원칙을 유지한다. 그래서 테이블도 만들지 않는다 — 키로만 꺼낼 문구라면 상수 파일이 맞다(§5).

---

## 1. 공통 스키마

도메인 테이블은 아래 공통 컬럼 + 도메인 컬럼(§3~§5).

| 컬럼 | 타입 | 비고 |
| --- | --- | --- |
| `id` | uuid | PK, `uuidv7()` |
| `doc_key` | text | UNIQUE. 사람이 읽는 키. `food.weaning.texture.m6` |
| `row_type` | varchar + CHECK | 도메인별 값 집합 |
| `title` | text | NOT NULL. 한 줄 |
| `body` | text | NOT NULL. **우리가 새로 쓴 문장.** 원문 복사 금지 |
| `min_month` · `max_month` | smallint | NOT NULL. 적용 월령 (이상·미만) |
| `tags` | text[] | 필터·검색용 (`indoor`, `self_care`, `protein` …) |
| `search_text` | text | NOT NULL. 임베딩 입력 = title + 요약 + tags. `body` 전체를 넣지 않음 |
| `embedding` | vector(1536) | nullable. Health는 NULL |
| `source_title` · `source_org` · `source_year` | text · text · smallint | NOT NULL |
| `source_locator` | text | 쪽 · 조항 · 절. "p.42" / "제3장 2절" |
| `source_url` | text | nullable |
| `license_basis` | varchar + CHECK | `public_law`(법령·고시) · `kogl_1`~`kogl_4`(공공누리) · `fact_rewrite`(사실만 추출해 재작성) · `permission`(허락 받음) |
| `status` | varchar + CHECK | `draft` · `approved` · `retired`. **조회는 `approved`만** |
| `authored_by` · `reviewed_by` | text | 작성자 · 검수자 (서로 달라야 함, CHECK) |
| `reviewed_at` | date | `approved`면 NOT NULL |
| `version` | smallint | 원문 개정 시 +1, 이전 행은 `retired` |

```sql
CHECK (min_month < max_month),
CHECK (authored_by <> reviewed_by),
CHECK (status <> 'approved' OR reviewed_at IS NOT NULL)
```

- 시드는 **YAML**(행 옆 주석에 원문 발췌 위치)로 버전 관리 → Alembic이 `doc_key` 기준 upsert. 스키마·시드 revision 분리.
- 임베딩은 시드 반영 후 배치로 채운다. `search_text`가 바뀌면 다시 계산.

---

## 2. 공통 전처리 절차

| 단계 | 할 일 | 산출물 | 완료 기준 |
| --- | --- | --- | --- |
| ① 소스 목록 | 도메인별 후보 문서 나열 + 라이선스 확인 | `sources.yaml` (문서·발행처·연도·URL·`license_basis`) | 라이선스 불명 문서는 **목록에서 빼거나** `fact_rewrite`로만 |
| ② 질문 목록 | Agent가 실제로 필요한 질문을 먼저 정한다 ("10개월 이유식 질감은?") | `questions.md` | 질문 없는 행은 만들지 않는다 |
| ③ 원문 확보 | 원문 파일 보관 (저장소 밖, 팀 드라이브) + 해시 | 원문 PDF · 해시 | 버전 추적 가능 |
| ④ 행 작성 | 질문 하나에 답하는 행을 템플릿대로 **새로 씀** | 도메인 YAML (`status: draft`) | 원문 문장 8어절 이상 일치 없음 |
| ⑤ 자동 검사 | lint 실행 (§6) | lint 리포트 | 오류 0 |
| ⑥ 교차 검수 | 작성자 ≠ 검수자. **원문을 펴 놓고** 대조 | `status: approved` | 사실 · 월령 · 출처 위치 3개 확인 |
| ⑦ 적재 | Alembic upsert → 임베딩 배치 | DB 행 | 행 수 · 임베딩 NULL 0 |
| ⑧ 조회 평가 | 질문 목록으로 top-k 조회 | recall 리포트 | 질문의 90%에서 정답 행이 top-3 |

분담은 **행이 아니라 `row_type` 단위**로 한다. 같은 유형을 두 사람이 쓰면 `doc_key`가 충돌한다. 한 사람이 끝내지 못한 유형은 절반만 넣지 않고 통째로 보류한다.

---

## 3. Food — `food_doc`

### 무엇을 담나

| `row_type` | 답하는 질문 | 쓰는 tool | 단계 |
| --- | --- | --- | --- |
| `weaning_stage` | 이 월령 이유식의 질감·횟수·양 리듬은? | `guide_weaning_stage` | 4–11개월 |
| `weaning_ingredient` | 이 재료는 언제부터, 어떻게 처음 주나? | `guide_weaning_stage` · 후보 풀 | 4–11개월 |
| `meal_pattern` | 이 나이 한 끼는 어떻게 구성하나? (밥·국·반찬, 간식 횟수) | `propose_meal_candidates` 예시 | 12개월+ |
| `nutrient_note` | 이 영양소가 적을 때 늘릴 식품군은? | `report_nutrient_analysis` | 12개월+ |
| `feeding_tip` | 편식·거부 상황의 일반적 대응 | `propose_meal_candidates` · weaning `refusal` | 전체 |

**넣지 않는 것** → 섭취기준 수치(`kdri_2025.yaml`) · 메뉴 영양성분(`menu_catalog`, API) · 알레르기 19종(`ALLERGEN_CODES`) · 연령 금지식품(`filter_food_safety` 규칙)

### 도메인 컬럼

| 컬럼 | 비고 |
| --- | --- |
| `food_groups` | text[] — 6개 식품군 |
| `nutrients` | text[] — `nutrient_note`용 (`protein`, `iron`, `calcium` …) |
| `texture` | varchar nullable — `puree` · `mashed` · `minced` · `soft_pieces` · `regular` |
| `allergen_codes` | smallint[] — 행이 특정 재료를 권하면 필수. 조회 후 `filter_food_safety`에 통과시킴 |

### 소스 후보

| 소스 | 쓸 곳 | 라이선스 확인 |
| --- | --- | --- |
| 보건복지부 「2025 한국인 영양소 섭취기준」 | `nutrient_note` (급원 식품군 서술) | 정부 발간물 — 공공누리 유형 확인 |
| 식약처 어린이 식생활 안전·영양 자료 | `meal_pattern` · `feeding_tip` | 공공누리 확인 |
| 지역 어린이급식관리지원센터 연령별 식단·레시피 | `meal_pattern` 예시 | **센터별로 다름** — 불명이면 `fact_rewrite` |
| 대한소아청소년과학회 이유식 자료 | `weaning_stage` · `weaning_ingredient` | 학회 저작물 → `fact_rewrite` 또는 허락 |
| 질병관리청 국가건강정보포털 | `weaning_stage` · `feeding_tip` | 공공누리 확인 |

### 행 예시

```yaml
- doc_key: food.weaning.texture.m6_7
  row_type: weaning_stage
  title: 6~7개월 이유식 질감
  body: 곱게 간 미음에서 시작해 조금씩 되직하게 만든다. 하루 1~2회, 새 재료는 한 번에 하나씩 며칠 간격으로 더한다.
  min_month: 6
  max_month: 8
  texture: puree
  tags: [weaning, texture]
  source_title: (문서명)
  source_org: (발행처)
  source_year: 2025
  source_locator: p.00
  license_basis: fact_rewrite
```

### 분량 · 우선순위

| 순위 | 유형 | 목표 행 |
| --- | --- | --- |
| P0 | `weaning_stage` · `weaning_ingredient` | 45 (공식 API가 없는 유일한 영역) |
| P1 | `meal_pattern` | 30 (12–35 · 36+ 각 15) |
| P1 | `nutrient_note` | 20 |
| P2 | `feeding_tip` | 15 |

### 조회 연결
- 단계 필터 → `row_type` 필터 → `search_text` 의미 검색 top-3
- 결과는 **프롬프트 맨 뒤 `[예시]` 구획**에 넣는다 (앞에 두면 프롬프트 캐시가 깨진다)
- `body`에 수치가 있어도 모델은 옮겨 쓰지 않는다 — 수치 출력은 기존 규칙대로 tool 결과만

---

## 4. Growth — `growth_doc`

### 무엇을 담나

| `row_type` | 답하는 질문 | 쓰는 tool | 단계 |
| --- | --- | --- | --- |
| `learning_activity` | 이 관심사·영역을 이 나이에 어떻게 넓히나? | `propose_learning_activity` | 12개월+ |
| `routine_step` | 이 자립 행동의 다음 단계는? (도움 받아 → 혼자) | `propose_routine_plan` | 12개월+ |
| `habit_strategy` | 이 습관이 이런 상황(`trigger`)에서 나올 때 대응은? | `propose_routine_plan` | 36개월+ |
| `manner_practice` | 이 예절을 어떤 상황극으로 연습하나? | `propose_routine_plan` | 24개월+ |
| `rhythm_info` | 영아 수면·수유 리듬 일반 안내 | `propose_routine_plan` (`rhythm_info`) | 0–11개월 |
| `book_guide` | 이 나이에 맞는 책 형태 (보드북·그림책·지식책) | `search_books` 필터 보조 | 전체 |
| `measure_guide` | 키·몸무게를 정확히 재는 법 | `compute_growth_delta`의 `delta.need_more` 옆 | 전체 |

**넣지 않는 것** → 발달 이정표 · 또래 비교 · "이 나이면 ~할 수 있다" 식 문장. 이런 행은 평가 문장의 씨앗이 된다. 그리고 실제 책 목록(도서 API).

### 도메인 컬럼

| 컬럼 | 비고 |
| --- | --- |
| `area` | varchar — 누리과정 5개 영역(`physical_health` · `communication` · `social` · `art` · `nature`) / 표준보육과정은 6개 영역(기본생활 포함) |
| `routine_category` | `observation_routine.routine_category` enum과 같은 값 |
| `trigger_tags` | text[] — `habit_strategy`용 (`bored`, `anxious`, `tired` …) |
| `materials` | text[] — **`hazard_term` 스캔 대상** (Activity 필터 재사용) |
| `setting` | `indoor` · `outdoor` · `either` |

### 소스 후보

| 소스 | 쓸 곳 | 라이선스 |
| --- | --- | --- |
| 보건복지부 「제4차 어린이집 표준보육과정」 고시 (0–2세) | `learning_activity` 영역 틀 · `routine_step` | 고시 본문 = `public_law` |
| 교육부·보건복지부 「2019 개정 누리과정」 고시 (3–5세) | `learning_activity` 영역 틀 | `public_law` |
| 위 두 과정의 **해설서·놀이 사례집** | 구체 활동 아이디어 | 저작물 → **`fact_rewrite`만** |
| 국가건강정보포털 배변·수면·양치 자료 | `routine_step` · `rhythm_info` | 공공누리 확인 |
| i-누리 · 중앙육아종합지원센터 놀이자료 | – | **사용 안 함** — 사이트 표기가 `ALL RIGHTS RESERVED` 이고, 누리과정 놀이자료는 공공누리 제4유형(출처표시 + 상업금지 + 변경금지)이라 상업 금지에 걸린다 (2026-09-23 확인) |

### 행 예시

```yaml
- doc_key: growth.routine.self_care.toothbrush.step2
  row_type: routine_step
  title: 양치 — 어른이 마무리해 주는 단계에서 혼자 문지르는 단계로
  body: 아이가 먼저 칫솔질을 하고 어른이 마무리한다. 노래 한 곡 길이로 시간을 정하면 끝을 알기 쉽다.
  min_month: 24
  max_month: 48
  routine_category: self_care
  materials: [칫솔, 치약]
  tags: [self_care, next_step]
  source_title: (문서명)
  source_org: (발행처)
  source_year: 2020
  source_locator: (절)
  license_basis: fact_rewrite
```

### 분량 · 우선순위

| 순위 | 유형 | 목표 행 |
| --- | --- | --- |
| P0 | `routine_step` | 40 (자립·식사·전환) |
| P0 | `learning_activity` | 60 (12–35 · 36+ × 영역) |
| P1 | `habit_strategy` | 20 (상황 태그별) |
| P1 | `manner_practice` · `rhythm_info` | 20 |
| P2 | `book_guide` · `measure_guide` | 8 + 4 |

### 전처리 추가 검사
- `materials`와 `body`를 **`hazard_term` 스캔**에 통과시킨다. 적재 시점에 한 번, 추천 시점에 한 번 (Activity 필터 재사용).
- **Growth 금지어 lint** (§6)에 걸린 행은 적재 거부.

---

## 5. ~~Health — `health_doc`~~ → 상수 파일로

**테이블을 만들지 않는다**(2026-09-22). Health가 쓰는 문구는 임베딩도 의미 검색도 없이 **키 정확 일치**로만 꺼낸다. 그러면 YAML 상수 파일과 다를 게 없고, Health의 다른 기준표(검진 주기·접종 일정)가 이미 `reference/*.yaml`이라 둘로 나눌 이유가 없다.

문구는 `reference/health_phrases.yaml` 한 곳에 둔다. 코드와 함께 배포되므로 테스트가 **글자 단위로** 고정할 수 있다.

| `kind` | 쓰는 곳 | 조회 키 |
| --- | --- | --- |
| `checkup_round` | `schedule_check` — 차수별 "이번 검진에서 보는 것" 한 줄 | `round` |
| `vaccine_note` | `schedule_check` — 백신별 한 줄 설명 | `vaccine_code` |
| `seek_care` | 증상 반복 readout · `visit_summary` 하단 — "이럴 땐 병원에" | `symptom_code` |
| `emergency_sign` | **Supervisor 안전 사전검사 규칙의 근거 · 응급 문구** | `sign_code` |
| `visit_prep` | `visit_summary` — 진료 때 챙길 것 | 고정 |

**넣지 않는 것** → 검진 주기 · 접종 일정(별도 상수 파일), 증상 → 질환 설명, 약 정보.

`measure_guide`(키·몸무게 재는 법)는 **Growth로 옮겼다** — 성장 판정이 사라지면서 Health에 소비처가 없어졌고, 재는 법이 필요한 사람은 성장폭을 보는 쪽이다. `growth_doc`의 행이 되어 `delta.need_more`("재보실래요?") 옆에 붙는다.

### 파일 모양

```yaml
- key: emergency.fever_under_3m
  kind: emergency_sign
  lookup: fever_under_3m
  min_month: 0
  max_month: 3
  body: 생후 3개월이 안 된 아기가 열이 나면 바로 병원이나 응급실로 가세요.
  medical_reviewed: true
  source: { title: (문서명), org: 질병관리청, year: 2026, locator: (페이지), license: kogl_1 }
```

- `medical_reviewed: true`가 아니면 `seek_care`·`emergency_sign`을 **로드하지 않는다**(부팅 시 검사)
- 작성자 · 검수자 · 의료 검수(가능하면 소아청소년과 전문의 자문) 3단계는 그대로다
- 진단 금지어 lint(§6)를 CI에서 돌린다 — 테이블이 아니어도 검사는 같다
- `emergency_sign`의 `lookup`과 Supervisor 규칙 코드는 **1:1 대응을 테스트로 강제**한다. 규칙은 있는데 문구가 없거나 그 반대면 CI 실패
- 조회 결과는 `Readout(authored_by="code")`로 **글자 그대로** 나간다. 모델 입력에도 넣지 않는다

### 분량 · 우선순위

| 순위 | `kind` | 목표 | 비고 |
| --- | --- | --- | --- |
| P0 | `emergency_sign` | 10 | Supervisor 규칙과 1:1. **의료 검수 필수** |
| P0 | `seek_care` | 15 | 증상 코드별 |
| P1 | `checkup_round` · `vaccine_note` | 8 + 20 | 접종 지침 개정 시 파일 교체 |
| P2 | `visit_prep` | 3 | |

### 소스 후보

| 소스 | 쓸 곳 | 라이선스 |
| --- | --- | --- |
| 질병관리청 국가건강정보포털 | `seek_care` · `emergency_sign` | 공공누리 확인 |
| 질병관리청 예방접종도우미 · 「2026 국가예방접종 지침」 | `vaccine_note` | 공공누리 확인 |
| 국민건강보험공단 영유아 건강검진 안내 | `checkup_round` | 공공누리 확인 |
| 대한소아청소년과학회 보호자용 자료 | `seek_care` · `emergency_sign` 교차 확인 | `fact_rewrite` |

---

## 6. 자동 검사 (lint) — 적재 전 필수

| 검사 | 대상 | 실패 조건 |
| --- | --- | --- |
| 원문 복제 | 전 도메인 | 원문과 **8어절 이상 연속 일치** (원문 텍스트 대조, 저장소 밖에서 실행) |
| 출처 누락 | 전 도메인 | `source_title` · `source_org` · `source_year` · `source_locator` 중 하나라도 빈 값 |
| 월령 | 전 도메인 | `min_month ≥ max_month` · 도메인 허용 범위 밖 |
| 평가 표현 | Growth · Activity · Food | 또래 · 평균 · 정상 · 발달이 · 늦 · 느리 · 뛰어나 · 재능 · 소질 · 문제 행동 |
| 진단 표현 | Health · Food | ~염 · ~증 · 의심 · 진단 · 처방 · 복용량 수치 |
| 수치 | Food | `body`에 g · kcal · mg 수치 → 경고 (의도된 경우만 수동 승인) |
| 위험 용어 | Growth · Activity · Food | `hazard_term` 스캔 결과가 행의 `min_month`와 충돌 → 오류 |
| 알레르기 | Food · Growth(요리 활동) | 재료가 있는데 `allergen_codes`가 비어 있음 |
| 키 중복 | 전 도메인 | `doc_key` 중복 |

---

## 7. Agent 연결 요약

| Agent | 조회 tool | 필터 → 순위 | k | 들어가는 곳 | 출력 표시 |
| --- | --- | --- | --- | --- | --- |
| Food | `search_food_doc` (**코드 tool**, run 시작 시 자동 조회) | 단계 · `row_type` → 의미 검색 | 3 | 프롬프트 `[예시]` | `reference_refs` → "참고: ○○ 자료" |
| Growth | `search_growth_doc` (코드 tool) | 단계 · 영역 · `routine_category` · `trigger_tags` → 의미 검색 | 3 | 프롬프트 `[예시]` | 동일 |
| Activity | `search_activity_doc` (코드 tool) | 월령 슬라이스 → 의미 검색 | 5 | 프롬프트 `[예시]` | 동일 |
| Health | `get_health_phrase(kind, key, months)` — 상수 파일 | 키 정확 일치 | 1 | readout 본문 그대로 | 출처 한 줄 고정 |

- 조회는 **코드 tool**로 둔다. 모델에게 검색 tool을 주면 쿼리를 자유 문장으로 만들면서 아이 발화를 넣을 수 있고, 조회 결과 수와 조회 여부를 테스트로 고정하기 어렵다.
- 의미 검색 쿼리는 **코드가 조립**한다: 라벨 · 단계 · 관심사 `merge_key` · 루틴 카테고리. 보호자 원문은 넣지 않는다.
- `suggestion`에 `reference_refs jsonb` 컬럼 추가 필요 (`[{kind: "food_doc", id}]`). `source_refs`와 분리.

---

## 8. 일정 (제안)

| 주 | Food | Growth | Health |
| --- | --- | --- | --- |
| 1 | 소스 목록 · 라이선스 · 질문 목록 | 동일 | 동일 + 의료 자문 섭외 |
| 2 | `weaning_*` 45행 | `routine_step` 40행 | `emergency_sign` · `seek_care` 25행 |
| 3 | `meal_pattern` · `nutrient_note` | `learning_activity` 60행 | 검진·접종 문구 |
| 4 | lint · 검수 · 적재 · recall 평가 | 동일 | 의료 검수 · 규칙 1:1 테스트 |

공통 선행: `*_doc` 스키마 PR · lint 스크립트 · `reference_refs` 컬럼 · `hazard_term` 적재 (Growth lint가 사용).

---

## 9. 미결

| # | 내용 |
| --- | --- |
| R-2 | 공공 발간물별 공공누리 유형 실제 확인 (특히 급식관리지원센터 · 학회 자료) |
| R-3 | Health 의료 검수자 확보 여부. 없으면 `emergency_sign`은 국가건강정보포털 문구 범위로만 제한 |
| R-4 | 원문 복제 검사 기준 (8어절) 확정 |
| R-5 | `reference_refs`를 화면에 노출할지 (출처 표시 UI) |
| R-6 | 임베딩 모델·차원 — 기존 `vector(1536)`과 통일 |
