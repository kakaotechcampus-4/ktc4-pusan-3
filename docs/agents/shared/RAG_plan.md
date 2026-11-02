# RAG_plan — 도메인별 근거 문서 테이블 전처리 계획

> 대상: Food · Growth · Health (Activity는 `activity_doc` 선례로 함께 정렬)
> 관련: [activity-agent-v1.md](../activity/activity-agent-v1.md) D9·D10 · [`Food_Tool_명세.md`](../food/Food_Tool_명세.md) · [`Growth_Tool_명세.md`](../growth/Growth_Tool_명세.md) · [`Health_Tool_명세.md`](../health/Health_Tool_명세.md)

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
| `search_text` | text | NOT NULL. 임베딩 입력 = **title + tags. 로더가 만들고 시드 YAML 에 적지 않는다** — 손으로 적으면 본문을 고칠 때 어긋난다. 검색에 걸려야 하는 말은 title 이나 tags 에 넣는다. `body` 전체를 넣지 않음 |
| `embedding` | vector(1536) | nullable. Health는 NULL |
| `source_title` · `source_org` · `source_year` | text · text · smallint | NOT NULL |
| `source_locator` | text | 쪽 · 조항 · 절. "p.42" / "제3장 2절" |
| `source_url` | text | nullable |
| `license_basis` | varchar + CHECK | `public_law`(법령·고시) · `kogl_1`~`kogl_4`(공공누리) · `fact_rewrite`(사실만 추출해 재작성) · `permission`(허락 받음) |
| `status` | varchar + CHECK | `draft` · `approved` · `retired`. **조회는 `approved`만** |
| `authored_by` · `reviewed_by` | text | 작성자 · 검수자 (서로 달라야 함, CHECK). **GitHub 아이디로 적는다** — 이름과 아이디가 섞이면 같은 사람을 다른 사람으로 본다. 로더는 앞뒤 공백을 지운 뒤 비교한다 |
| `reviewed_at` | date | `approved`면 NOT NULL |
| `version` | smallint | 원문 개정 시 +1, 이전 행은 `retired` |

```sql
CHECK (min_month < max_month),
CHECK (authored_by <> reviewed_by),
CHECK (status <> 'approved' OR reviewed_at IS NOT NULL)
```

- 시드는 **YAML**(행 옆 주석에 원문 발췌 위치)로 버전 관리 → Alembic이 `doc_key` 기준 upsert. 스키마·시드 revision 분리.
- 임베딩은 시드 반영 후 배치로 채운다. `title` · `tags` 가 바뀌면(= `search_text` 가 바뀌면) 다시 계산.

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
| `food_groups` | text[] — 6개 식품군. 값은 `grains` · `meat_fish_egg_legumes` · `vegetables` · `fruits` · `dairy` · `fats_sugars` (#337 잠정 — `menu_catalog.food_groups` 와 같은 값이어야 해서 둘 곳은 리뷰에서 정한다) |
| `nutrients` | text[] — `nutrient_note`만 (`protein`, `iron`, `calcium` …) |
| `texture` | varchar nullable — 이유기 행(`weaning_stage` · `weaning_ingredient`)만. `puree` · `mashed` · `minced` · `soft_pieces` · `regular` |
| `allergen_codes` | smallint[] — 제목 · 본문에 나오는 19종 코드를 다 적는다. 시드 테스트가 Food 사전으로 문장을 훑어 빠진 코드를 잡는다(`doc_seed.allergen_codes_in`). 조회 후 `filter_food_safety`에 통과시킴 |

### 소스 (#337)

**상업 서비스 기준으로 좁혔다** — 상업적 이용과 변경(다시 쓰기)이 둘 다 허락된 것만 행 출처로 쓴다. 공공누리 제0 · 1유형, 법령 · 고시, CC BY 처럼 둘을 명시한 라이선스다. 공공누리 제2 · 3 · 4유형, 표시가 없는 공공기관 자료, "All rights reserved", 비상업 라이선스는 사실만 다시 쓰는 방식(`fact_rewrite`)으로도 넣지 않는다. 미국 연방기관 자료는 외국 저작권법 예외가 있어서 검수 대조용으로만 쓴다.

| 소스 | 쓸 곳 | 라이선스 |
| --- | --- | --- |
| 뉴질랜드 보건부 「Healthy Eating Guidelines for New Zealand Babies and Toddlers (0–2 years old)」 2021 | `weaning_stage` · `weaning_ingredient` · `feeding_tip` | CC BY 4.0 — 값 집합에 CC BY 가 없어 `fact_rewrite` 로 적고 시드 머리말에 라이선스를 적는다 |
| 한국소비자원 「영유아 이유식 안전실태조사」 2023 붙임4 | 시작 시기(4~6개월) · 한 가지씩 · 먹이는 태도 | 공공누리 제1유형 (`kogl_1`) |
| 표준보육과정 · 누리과정 고시 | `feeding_tip` (연령별 식사 태도) | 고시 (`public_law`) — 국가법령정보센터 본문 |
| CDC · USDA WIC · 미국 식생활지침 | 대조용 (행 출처 아님) | 미국 연방 저작물 |

뺀 소스와 이유(식생활 영양정보 · 2025 한국인 영양소 섭취기준 · 국가건강정보포털 · 학회 자료 · 급식관리지원센터 · WHO 등)는 #337 에 있다. 이 기준을 만족하는 한국 식단 구성 자료를 아직 못 찾아서 `meal_pattern` · `nutrient_note` 는 비어 있다.

### 행 예시

```yaml
- doc_key: food.weaning.stage.m6_7.texture
  row_type: weaning_stage
  title: 6~7개월 이유식 질감
  body: >-
    6개월 무렵부터는 곱게 간 음식을 점점 되직하게 만들고, 으깬 음식과 부드러운 음식도 준다. …
  min_month: 6
  max_month: 8
  tags: [weaning, texture, mashed]
  texture: mashed
  <<: [*nz, *draft]  # 출처 칸 묶음 — reference/food_doc.yaml 의 origins
  source_locator: Eating Statement 2 — Increasing food texture, Starting out (p.28)
```

### 분량 · 우선순위

| 순위 | 유형 | 목표 행 |
| --- | --- | --- |
| P0 | `weaning_stage` · `weaning_ingredient` | 30 (공식 API가 없는 유일한 영역. 재료별이 아니라 식품군 단위로 써서 45 → 30, #337) |
| P1 | `meal_pattern` | 30 (12–35 · 36+ 각 15) — 소스를 찾은 뒤 |
| P1 | `nutrient_note` | 20 — 필요한지부터 따로 정한다 (#337) |
| P2 | `feeding_tip` | 10 (#337) |

이유식 단계(`weaning_stage`)는 4–5 / 6–7 / 8–11 개월로 나눈다. 시작 시기는 한국소비자원(4~6개월), 질감 진행은 뉴질랜드 지침을 따랐다. 한국 관행의 초기 · 중기 · 후기(4–5 / 6–8 / 9–11)는 기준을 만족하는 소스에 없다 (#337).

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
| `rhythm_info` | 영아 잠 · 하루 일과 순서 안내 (수유 · 이유식은 Food, 시간 · 횟수 숫자 없음) | `propose_routine_plan` (`rhythm_info`) | 0–11개월 |
| `book_guide` | 이 나이에 맞는 책 형태 (보드북·그림책·지식책) | `search_books` 필터 보조 | 전체 — 지금은 쓰지 않는다(읽는 코드 · 출처 없음) |
| `measure_guide` | 키·몸무게를 정확히 재는 법 | `compute_growth_delta`의 `delta.need_more` 옆 | 전체 |

**넣지 않는 것** → 발달 이정표 · 또래 비교 · "이 나이면 ~할 수 있다" 식 문장. 이런 행은 평가 문장의 씨앗이 된다. 그리고 실제 책 목록(도서 API).

### 도메인 컬럼

| 컬럼 | 비고 |
| --- | --- |
| `area` | varchar — 고시 5개 영역(`physical_health` · `communication` · `social` · `art` · `nature`). 2024 개정 표준보육과정은 0~5세 모두 5영역이고, `basic_life`(기본생활)는 제4차(2020) 0~2세 영역이라 쓰지 않는다 |
| `routine_category` | `observation_routine.routine_category` enum과 같은 값 |
| `trigger_tags` | text[] — `habit_strategy`용 (`bored`, `anxious`, `tired` …) |
| `materials` | text[] — **`hazard_term` 스캔 대상** (Activity 필터 재사용) |
| `setting` | `indoor` · `outdoor` · `either` |

### 소스 후보

| 소스 | 쓸 곳 | 라이선스 |
| --- | --- | --- |
| 「표준보육과정 고시」(교육부고시 제2024-23호 — 0~1세 · 2세 · 3~5세(누리과정)) | `learning_activity` 영역 틀 · `routine_step` | 고시 본문 = `public_law`(저작권법 제7조 2호). 출처 문자열은 `표준보육과정 고시 (교육부고시 제2024-23호)` 하나 — activity_doc 과 같다(D10) |
| 「2019 개정 누리과정」 고시 (3–5세) | 대조용 — 3~5세 내용이 위 고시와 같아서 행 출처로는 위 고시를 적는다 | `public_law` |
| 위 과정의 **해설서·놀이 사례집** | – | **쓰지 않음** — 공공누리 제4유형(상업 이용 금지 · 변경 금지), #337 기준 |
| 국가건강정보포털 배변·수면·양치 자료 | – | **쓰지 않음** — 공공누리 제4유형, #337 기준 |
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

## 4-1. Activity — `activity_doc`

설계 근거는 [activity-agent-v1.md](../activity/activity-agent-v1.md) D9 · D10. 이 절은 행 모양과 제작 규칙이다.

### 무엇을 담나

| `row_type` | 답하는 질문 | 쓰는 곳 | 단계 |
| --- | --- | --- | --- |
| `play_idea` | 이 월령에 지금 바로 해 볼 놀이 하나는 어떤 모양인가? | `search_activity_doc` → 프롬프트 `[예시]` 5행 | 0–71개월 |

종류는 하나다. Activity 는 `[예시]` 한 곳에서만 쓰여서 종류로 나눌 곳이 없다.

**넣지 않는 것** → 발달 이정표 · "이 나이면 ~할 수 있다" 식 문장(Growth 와 같은 이유) · 위험 월령(`reference/hazard_terms.yaml`) · 장소 목록(`place` 테이블).

### 도메인 컬럼 — 출력 후보와 같은 칸

예시가 출력 모양을 그대로 보여 주도록 `ActivityCandidate` 와 같은 칸을 둔다. 재료를 따로 적어야 위험 용어 검사가 후보와 같은 방식(문장 · 재료 따로)으로 돈다.

| 컬럼 | 비고 |
| --- | --- |
| `setting` | `indoor` · `outdoor` · `either` |
| `materials` | text[] — **`hazard_term` 스캔 대상.** 없으면 `[]` |
| `caregiver_role` | `together` · `nearby` · `independent`. `min_month < 18` 이면 `together` 만 |
| `physical_intensity` | `low` · `medium` · `high` |
| `involves_food` | boolean |
| `area` | 고시 영역 — `basic_life` · `physical_health` · `communication` · `social` · `art` · `nature` (신체운동 · 신체운동·건강은 하나로) |

### 소스

🚨 **규칙은 하나다 — 사실만 읽고 문장은 새로 쓴다** (D10).

| 소스 | 쓸 곳 | 라이선스 |
| --- | --- | --- |
| 「표준보육과정 고시」 (교육부고시 제2024-23호, 2025-03-01 시행, 0~1세 · 2세 · 3~5세) | 행마다 영역 · 내용범주 · 내용 | 고시 본문 = `public_law`. 국가법령정보센터 행정규칙 첨부 본문으로 대조했다. `source_title` 은 Growth · Food 와 같은 문자열이다(D10 — 한 줄로 뺄 수 있게) |
| 표준보육과정 · 누리과정 **해설서 · 사례집** 본문 | – | **보류.** 해설서 판권면에 "사전승인 없이 무단 복제를 금합니다"(0~2세 해설서)가 있어 아래 놀이자료와 같은 줄로 본다. 이번 판은 **고시 본문**만 썼다 — 고시는 법령이라 영향 없음 |
| i-누리 · 육아종합지원센터 놀이자료 · 누리과정 놀이자료(공공누리 제4유형) | – | **보류.** Activity D10 은 `fact_rewrite` 로 읽을 수 있다고 봤고 Growth(§4)는 사용 안 함으로 정해서 규칙이 둘이다. 하나로 맞춘 뒤 정한다 |

### 행 예시

```yaml
- doc_key: activity.play.m18_23.cushion_crawl
  row_type: play_idea
  title: 쿠션 길 기어 넘기
  body: 거실 바닥에 쿠션을 두세 개 늘어놓아 낮은 언덕을 만들고, 어른이 끝에서 불러 주면 기어서 넘어오게 한다.
  min_month: 18
  max_month: 24              # 미만
  setting: indoor
  materials: [쿠션]
  caregiver_role: together
  physical_intensity: medium
  involves_food: false
  area: physical_health
  tags: [gross_motor]
  source_title: 표준보육과정 고시 (교육부고시 제2024-23호)
  source_org: 교육부
  source_year: 2024
  source_locator: 0~1세 신체운동·건강 · 신체활동 즐기기 · 기본 운동을 시도한다
  license_basis: public_law
  status: draft
  authored_by: 03leedo       # 검수는 다른 사람이 원문을 펴 놓고 확인한 뒤 approved
  version: 1
```

시드 파일은 `apps/api/reference/activity_doc.yaml`, 로더는 `app/agents/activity/doc_seed.py` (공통 칸 검사는 `app/agents/common/reference_docs.py` `parse_doc_meta`). `search_text` 는 로더가 `title + tags` 로 만든다 — YAML 에 적지 않는다.

### 분량

| 월령 | 0–5 | 6–11 | 12–17 | 18–23 | 24–35 | 36–47 | 48–71 | 합계 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 행 | 8 | 10 | 12 | 12 | 14 | 12 | 12 | **80** |
| 실내 / 바깥·어디서나 | 8 / 0 | 7 / 3 | 8 / 4 | 8 / 4 | 9 / 5 | 8 / 4 | 8 / 4 | |

구간 경계는 판정 경계(18 보호자 동반 · 36 작은 부품 · 48 질식 음식)와 맞췄다. 첫 판은 구간마다 1행(7행)이고, 80행을 채우면 아래 커버리지 기준을 5행으로 올린다.

### 전처리 추가 검사 (테스트로 고정 — `test_activity_doc_seed.py`)
- **위험 용어** — 그 행의 `min_month` 에서 `title` · `body` · 재료 각각이 **아무 축에도 걸리지 않는다(경고 포함).** 예시가 출력 검증에 걸리는 모양이면 모델에게 거절될 문장을 가르친다. 축 판정은 나이가 많을수록 약해져서 가장 어린 월령만 보면 된다.
- **보호자 역할** — `min_month < 18` 이면 `together`.
- **평가 표현** — `app/rules/evaluative.py` 공통 목록.
- **커버리지** — 0–71개월 모든 월령에서 덮는 행이 기준 이상.
- 원문과 8어절 이상 겹치는지(§6)는 원문이 저장소 밖이라 검수자가 본다.

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
| Food | `search_food_doc` (**코드 tool**, run 시작 시 자동 조회) | 단계 · `row_type` → 의미 검색 | 3 | 프롬프트 `[예시]` | `suggestion_evidence`(`*_doc`) → "참고: ○○ 자료" |
| Growth | `search_growth_doc` (코드 tool) | 단계 · 영역 · `routine_category` · `trigger_tags` → 의미 검색 | 3 | 프롬프트 `[예시]` | 동일 |
| Activity | `search_activity_doc` (코드 tool) | 월령 슬라이스 → 의미 검색 | 5 | 프롬프트 `[예시]` | 동일 |
| Health | `get_health_phrase(kind, key, months)` — 상수 파일 | 키 정확 일치 | 1 | readout 본문 그대로 | 출처 한 줄 고정 |

- 조회는 **코드 tool**로 둔다. 모델에게 검색 tool을 주면 쿼리를 자유 문장으로 만들면서 아이 발화를 넣을 수 있고, 조회 결과 수와 조회 여부를 테스트로 고정하기 어렵다.
- 의미 검색 쿼리는 **코드가 조립**한다: 라벨 · 단계 · 관심사 `merge_key` · 루틴 카테고리. 보호자 원문은 넣지 않는다.
- 문서 행은 `suggestion` 에 칸을 따로 두지 않고 `suggestion_evidence` 에 `source_kind='*_doc'` 으로 쌓는다 (§0-3). `source_kind` 가 텍스트라 스키마 변경이 없고, 개인화 근거 집계에서는 `count_child_records()` 가 뺀다. (이전 판의 `reference_refs` 컬럼 계획은 이것으로 대체됐다.)

---

## 8. 일정 (제안)

| 주 | Food | Growth | Health |
| --- | --- | --- | --- |
| 1 | 소스 목록 · 라이선스 · 질문 목록 | 동일 | 동일 + 의료 자문 섭외 |
| 2 | `weaning_*` 45행 | `routine_step` 40행 | `emergency_sign` · `seek_care` 25행 |
| 3 | `meal_pattern` · `nutrient_note` | `learning_activity` 60행 | 검진·접종 문구 |
| 4 | lint · 검수 · 적재 · recall 평가 | 동일 | 의료 검수 · 규칙 1:1 테스트 |

공통 선행: `*_doc` 스키마 PR · lint 스크립트 · `reference/hazard_terms.yaml` (Growth lint가 사용). 시드 공통 칸 검사는 `app/agents/common/reference_docs.py` `parse_doc_meta` 가 있다 (Activity 가 먼저 씀).

---

## 9. 미결

| # | 내용 |
| --- | --- |
| R-2 | 공공 발간물별 공공누리 유형 실제 확인 (특히 급식관리지원센터 · 학회 자료) |
| R-3 | Health 의료 검수자 확보 여부. 없으면 `emergency_sign`은 국가건강정보포털 문구 범위로만 제한 |
| R-4 | 원문 복제 검사 기준 (8어절) 확정 |
| R-5 | 문서 근거(`suggestion_evidence` 의 `*_doc` 행)를 화면에 노출할지 (출처 표시 UI) |
| R-6 | 임베딩 모델·차원 — 기존 `vector(1536)`과 통일 |
