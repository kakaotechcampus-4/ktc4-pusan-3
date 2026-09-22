# 5-Agent 육아 보조 AI 설계안

> **구성:** Child Supervisor + Memory + Food + Activity + Growth + Health (+ Curator — Agent 아님)
>
> **핵심 방향:** 각 도메인 Agent는 자기 영역의 기록을 읽고 필요한 행동을 제안한다. Curator는 여러 기록에서 장기적으로 재사용할 관심·선호·제약만 정리한다. 모든 Agent는 하나의 Child Profile을 공유하되, 아이의 연령·상태에 따라 사용할 수 있는 Tool은 달라진다.
>
> **갱신 이력:** 2026-09-20 — Growth 재정의(루틴·신체 성장 포함) · Health 확장(복약 CRUD·성장 판정) · Activity↔Growth 경계 규칙 · 근거 티어 · Tool Gating 주체 정정
> 2026-09-22 — OCR 3갈래 분류(notice / event / 급식 행) · suggestion 3개 + 승인 후 Memory 이관 · **Health 성장 판정 제거** · 복약 승인 게이트 · 성별 미사용 · Food 급식 흐름
> 2026-09-22 (3차) — **기피(−1)는 근거, 제외 필터 아님** · Food는 영양이 선호보다 앞섬 · 복약 초안은 payload(DB 저장 없음) · `routine_coaching` 근거 0행은 전부 역질의 · 성장폭 최소 간격 폐지

---

## 1. 전체 구조

```
부모 입력 / 기관 공지(텍스트) / 외부 이벤트        기관 공지(이미지)
        ↓                                          ↓
  Child Supervisor                           OCR 파이프라인 (Agent 아님)
  입력 해석 · Agent Routing                   텍스트 추출 → 3갈래 분류
        ↓                                     ├─ 일반 공지 → notice
   Memory Agent  ←────── 일정성 공지 원문 ──────┤
   관찰·일정 저장 (공유 테이블 단일 writer)       └─ 급식표 → `daycare_meal` (+ 대체식 확인 안내)
        ↓                            ▲
  ┌─────────┼─────────┬─────────┐    │ 승인된 suggestion 이관
  Food   Activity   Growth    Health │
  └─────────┼─────────┴─────────┘    │
        ↓                            │
   suggestion (draft, 3개) ── 사용자 승인
   readout / event 이관 / 역질의
        ↓
    Curator (Agent 아님, 배치)
  정규화 · 병합 · 승격 · 감쇠
        ↓
    profile_affinity
  확정 관심 · 선호/기피
        ↓
   다음 추천의 1순위 근거
```

### 공통 원칙

- **쓰기 통로는 하나다.** 여러 Agent가 읽는 테이블(`observation_*` · `profile_affinity` · `event`)은 Memory만 쓴다.
- **도메인 전용 테이블은 소유 Agent가 쓴다.** 다른 Agent가 읽지 않는 테이블은 그 Agent가 CRUD한다. 현재 해당하는 것은 `medication_schedule` · `medication_dose`(Health)뿐이다.
- **OCR 파이프라인은 한 Agent만 읽는 테이블을 직접 쓴다** — `notice`(Growth) · `daycare_meal`(Food, 이후 갱신은 Food가 직접). 일정성 공지는 직접 쓰지 않고 추출 원문을 Memory에 넘긴다. `event`의 writer는 Memory 하나로 유지된다.
- **승인된 suggestion은 Memory로 이관된다.** 도메인 Agent는 suggestion을 `draft`로 만들 뿐이고, 승인 후 Memory로 넘기는 것은 Activity Agent 또는 백엔드 로직이다 `[미정]`.
- 장기적으로 공유할 정보는 **Curator만** `profile_affinity`로 승격한다.
- 한 번의 행동을 곧바로 아이의 성향이나 능력으로 확정하지 않는다.
- 아이의 나이, 알레르기, 식이 단계처럼 확정적인 정보는 공통 Child Profile에서 관리한다.
- 추천에는 어떤 기록을 근거로 사용했는지 표시한다. 근거가 없으면 일반 추천임을 표시한다.

---

## 2. 근거 조립 규칙 (Food · Activity · Growth 공통)

추천을 만들 때 어떤 기록을 먼저 보는지는 **코드가 정한다.** 모델은 이미 정렬된 근거 목록을 받는다.

```
티어 1  confirmed affinity   (polarity ∈ {+1, −1} · archived 제외)
티어 2  candidate affinity   (polarity ∈ {+1, −1} · strength ≥ 임계)
티어 3  최근 14일 관찰
── 셋 다 0행 ──
        kind="general" 일반 추천
```

### 규칙 다섯 개

1. **`profile_affinity`가 관찰보다 우선한다.** Curator가 여러 관찰을 증류한 결과이기 때문이다.
2. **`polarity IS NULL`인 candidate는 근거로 쓰지 않는다.** 선호인지 기피인지 모르는 신호다. 대신 역질의로 되돌린다.
3. **`polarity=-1`은 제외 필터가 아니라 근거다.** 기피는 후보를 지우는 값이 아니라 무엇을 피해 고를지 정하는 값이다. 후보에서 지워버리면 화면이 그 이유를 말할 수 없다. 제외 필터 자리에 남는 것은 알레르기·연령 금지식품·`hazard_term` 뿐이다.
4. **6개월 이상 지난 관심 기록은 Curator가 `archived`로 내린다**(NF-08). 도메인 Agent는 `archived`를 근거에서 빼는 것으로 이 규칙을 지킨다 — 유예일을 직접 세지 않는다.
5. **관찰은 14일 컷, affinity는 무기한.** 의도된 비대칭이다. affinity의 존재 이유가 장기 신호의 증류이고, 감쇠는 Curator가 `strength`를 내리는 것으로 표현된다.

### 근거 0행일 때

`kind="general"` 일반 추천을 만들고, `reason`은 모델이 쓴 개인화 문장이 아니라 **코드 템플릿**으로 교체한다("또래 아이들이 많이 하는 놀이예요"). 화면은 "또래 기준"임을 표시한다.

예외 세 가지 — 일반 추천이 나가지 않는다.

| 경우 | 대신 |
| --- | --- |
| Growth `routine_coaching` | 역질의 ("요즘도 손톱 물어뜯나요?") |
| Health 전체 | readout만. 일반 건강 추천은 성립하지 않는다 |
| Food 영아기 × 영양소 분석 | 미지원 안내 readout |

---

## 3. Child Supervisor

### 역할

사용자의 입력을 가장 먼저 받아 **무엇을 기록해야 하는지, 어떤 Agent가 필요한지**를 결정하는 오케스트레이터.

### 하는 일

- 기록형 / 요청형 / 혼합형 입력 판정
- Memory / Food / Activity / Growth / Health 중 필요한 Agent 선택 (**최대 2개**)
- 2차 라벨(task_type) 분류 — 라벨이 Tool 묶음을 가르는 Agent만
- 알레르기·증상 등 안전 관련 입력 우선 감지 (규칙)
- 혼합형 입력에서 **새 관찰을 먼저 저장한 뒤** 추천 Agent가 실행되도록 순서 조정

### 하지 않는 일

- 직접 식사·놀이·교육 콘텐츠 추천
- 건강 진단
- 관심 확정
- `profile_affinity` 직접 수정
- **Tool Set 결정** — 이건 각 Agent의 registry가 한다 (§7)
- 아무것도 write 하지 않는다

### 2차 라벨

| Agent | 라벨 |
| --- | --- |
| Food | `meal_recommendation` · `nutrient_analysis` · `daycare_meal` |
| Activity | 없음 (놀이 추천 하나) |
| Growth | `learning_suggestion` · `routine_coaching` · `book_suggestion` · `growth_review` |
| Health | `visit_summary` · `schedule_check` · `place_lookup` · `medication` · `care_handoff` |

라벨은 **Tool 묶음이 갈릴 때만** 만든다. Activity의 실내/야외 분기는 날씨·시간 조회 **결과**로 갈리므로 런타임 판단이지 라벨이 아니다.

---

## 4. Agent 경계

도메인이 겹쳐 보이는 네 곳의 판정 기준이다. 이 기준이 없으면 같은 입력에 두 Agent가 붙어 비슷한 추천이 두 장 나간다.

### 4-1. Activity ↔ Growth

| | Activity | Growth |
| --- | --- | --- |
| 자기 관찰 | `observation_activity` | `observation_education` · `observation_routine` |
| 참고 | `profile_affinity` | `profile_affinity` · **`observation_activity`** |
| 시간축 | 지금/오늘 (날씨·거리 의존) | 반복·누적 (장소 무관) |
| 출력 | 무엇을 하고 놀까 | 무엇을 익히게 할까 |

**기본은 Activity.** "가르치다 · 배우다 · 책 · 글자 · 숫자 · 습관 · 혼자 하게" 같은 **학습·자립 신호**가 있을 때만 Growth로 간다. 신호어는 코드 키워드 매칭이 아니라 Supervisor 프롬프트의 경계 예시로 판단한다 — "오늘 블록 쌓는 거 배웠대"는 신호어가 있지만 요청이 아니라 관찰이다.

읽기 포트는 **비대칭이다.** Growth는 놀이 기록을 읽지만 Activity는 학습·루틴 기록을 읽지 않는다. "관심을 학습으로 확장"이 Growth의 역할이라 놀이 기록이 입력으로 필요하고, 반대는 필요가 없다.

경계 예시:
```
"농구 좋아하는데 뭐 하고 놀까"        → activity
"농구 좋아하는데 관련된 책 있어?"      → growth (book_suggestion)
"밥 먹을 때 혼자 하게 하고 싶은데"     → growth (routine_coaching)
```

### 4-2. Growth ↔ Health (신체 성장)

**겹치지 않는다 — 신체 성장은 Growth만 다룬다.** Health의 성장 백분위·BMI 판정은 제거됐다(2026-09-22). 시스템 어디에서도 성장을 판정하지 않는다.

| | Growth `growth_review` |
| --- | --- |
| 하는 것 | `child_growth_log`를 시간순으로 정리해 성장 추이를 서술 |
| 계산 | 차분·구간 계산 (코드, 모델 호출 0회) |
| 하지 않는 것 | 백분위 · 또래 비교 · "잘 크고 있다/아니다" 판정 |

```
"얼마나 컸어?"               → Growth  growth_review
"잘 크고 있어?"              → Growth  growth_review  (추이만 + 검진 안내)
"10cm 컸는데 많이 큰 거야?"   → Growth  growth_review  (같음)
```

판정을 요구하는 질문에도 판정 문구를 만들지 않는다. "성장 평가는 영유아 건강검진에서 확인해 보세요" 한 줄은 **코드 템플릿**이다.

### 4-3. Food ↔ Growth (식사 루틴)

**무엇을 먹었나 = Food(`observation_food`), 어떻게 먹나 = Growth(`observation_routine`).**

편식·섭취량·알레르기는 Food, 식사 자립·식사 예절·식사 중 행동은 Growth. Memory가 관찰을 어느 테이블에 넣느냐에서 이미 갈리므로, **Memory 프롬프트에 이 한 줄이 있어야** 경계가 성립한다.

### 4-4. Food ↔ Health (알레르기)

겹치지 않는다. Food는 확정된 `health_safety`를 **코드 필터**로 쓸 뿐이고, **둘 다 `health_safety`에 쓰지 않는다.** 알레르기 후보 감지는 v1에서 뺐다 — 등록은 보호자가 앱에서 직접 하는 것 하나뿐이다.

---

## 5. Food Agent

**역할** — "오늘 무엇을 먹일까?"를 아이의 실제 섭취 기록, 기관 급식, 선호·기피, 알레르기와 연결해 결정하기 쉽게 만든다.

상세는 [`Food_Agent_명세.md`](../food/Food_Agent_명세.md) · 원안과 구현 이력은 `food.md`(저장소 밖).

### 하는 일
- 식사·간식 후보 추천 (요청 1건당 3개)
- 최근 반복 메뉴·식단 편중 확인
- 기관 급식과 가정 식사 연결 — OCR이 채운 `daycare_meal` + 보호자가 알려준 대체식 기준
- 연령·식이 단계에 맞는 식사 보조
- 알레르기·금지식품 필터링 (코드)
- 영양소 과잉/부족 **비중** 분석
- **영양이 선호보다 앞선다** — 기피 근거가 있어도 영양 분석이 부족을 가리키면 그 식품군을 빼지 않는다. 대신 기피를 피해 가는 형태로 고르고, 기피 사실을 문장에 밝힌다 ([`Food_Agent_명세.md`](../food/Food_Agent_명세.md) §6)

### 하지 않는 일
- 질병이나 영양 결핍 진단 · 치료식 처방
- LLM이 정확한 영양 수치를 생성 (항상 '비중', 정확한 섭취량 계산 금지)
- 알레르기를 추론해 확정
- **섭취 기록 저장** — Memory가 한다
- **체중 감량·증량 식단** — 의료 영역. 시스템은 체중 판정 자체를 하지 않는다

### 권장 섭취량 기준
**연령(발달 단계)별 일반값**을 외부 소스에서 가져오고, **권장 열량은 `child_growth_log`의 키·몸무게로 계산**한다(코드). 측정이 없으면 연령별 일반값만 쓴다. **성별은 반영하지 않는다** — 성별에 따른 섭취량 차이는 확인할 수 없다. 측정값으로 과체중·저체중을 판정하지 않는다.

### 기관 급식 흐름 (Food의 핵심)
```
① OCR 파이프라인이 급식표 원문으로 `daycare_meal` 행을 채움
② "대체식·알레르기로 빼놓은 메뉴가 있으면 알려주세요" 안내와 함께 저장
③ 보호자가 알려주면 백엔드가 그 행의 `menu_keys`를 대체 메뉴로 **교체**
```
급식은 `daycare_meal`, 집에서 먹은 것은 `intake_daily`다. **미래 날짜를 갖는 것은 급식뿐**이라 내일 급식을 보고 저녁을 고를 수 있다. `daycare_meal`은 다른 Agent가 읽지 않는 도메인 전용 테이블이라 **Food가 직접 CRUD**한다. 상세는 Food_Agent_명세.md §4.

---

## 6. Activity Agent

**역할** — 아이의 현재 관심과 실제 놀이 반응을 바탕으로 지금 실행하기 좋은 놀이·외출 활동을 추천한다.

### 하는 일
- 집/실내/야외 놀이 추천
- `candidate` 관심을 확인해볼 가벼운 탐색 활동
- `confirmed` 관심을 더 깊게 즐길 활동
- 최근 했던 놀이와 중복되는 추천 제거 (코드)
- 날씨·시간·거리·가족 일정 반영
- 추천은 요청 1건당 3개. 승인된 추천은 Memory로 이관된다 (§1)
- 안전 필터 후 3개 미만이면 모델 재호출 1회 허용 (모든 추천 Agent 공통, NF-01의 유일한 예외)

### 연결할 것
`observation_activity` · `profile_affinity(domain=activity)` · Child Profile · Calendar · Weather · 위치 기반 장소 정보

### 하지 않는 일
- 한 번 즐긴 놀이를 장기 취향으로 확정
- 외부 리뷰만 보고 최적 장소라고 단정
- 보호자 승인 없이 예약·결제 (경로 자체를 만들지 않는다)
- `observation_education` · `observation_routine` 읽기

---

## 7. Growth Agent

**역할** — 아이를 평가하는 Agent가 아니라, **최근 관심 · 학습 경험 · 생활 루틴 · 신체 성장** 네 신호를 이어서 "다음에 어떤 경험과 도움을 줄지" 제안한다.

상세는 [`Growth_Agent_명세.md`](../growth/Growth_Agent_명세.md).

### 하는 일
- 관심사를 새로운 교육 경험과 연결
- 과거 활동의 몰입·거절·반복 요청을 다음 추천에 반영
- 공식 교육·놀이 자료를 검색해서 활동 후보 생성
- 관심사와 연결되는 실제 도서 검색
- **생활 루틴 지원** — `routine_category`별로 갈린다
  - 자립(`self_care` · `household_task`) → 다음 단계 제안
  - 습관(`habit`) → 교정안
  - 예절(`social_manner`) → 상황 연습
- **성장 추이 정리** — `child_growth_log` 기반. "6개월간 10cm 자랐네요". 판정하지 않는다. **신체 성장을 다루는 유일한 Agent**

### 핵심 기능 — 관찰 기반 연결 추천
**가장 중요한 건 `observation_routine` · `observation_education`을 외부 소스(`growth_doc` · 도서 API)와 연결해 추천하는 것**이다. 기관 공지(`notice`)는 보조 입력이고, 없어도 핵심 기능은 온전히 동작해야 한다.

### 습관 교정은 `trigger`가 있어야 시작한다
`observation_routine.trigger`가 NULL이면 교정안을 만들지 않고 역질의로 되돌린다. 언제 나오는 행동인지 모르면 교정안이 아이와 무관해진다.

### 연결할 것
**핵심**: `observation_routine` · `observation_education` × `growth_doc` · 교육과정/놀이자료 KB · 도서 검색 API

그 외: `observation_activity`(참고) · `profile_affinity(domain ∈ education, routine, activity)` · `child_growth_log` · Calendar · `notice`(보조)

### 하지 않는 일
- 지능·성적 예측 · "공간지각이 뛰어나다" 같은 능력 확정 · 교사의 평가 대체
- 한 번 잘한 활동을 소질이나 발달 수준으로 해석
- `strength` · `assistance_level` · `completion_status`를 또래 비교나 점수로 환산
- **성장 측정값으로 발달 수준·저성장 판단** — 백분위·BMI 판정은 시스템 전체에서 하지 않는다
- `polarity=-1`을 "못하는 것"으로 해석 (기피/거부일 뿐)
- 습관을 문제 행동으로 규정 (`habit` 관찰 1건 = 습관 아님)

---

## 8. Health Agent

**역할** — 진단을 대신하는 것이 아니라, 부모가 아이의 건강 관련 과거 기록을 다시 찾고 정리하는 노동을 줄인다.

상세는 [`Health_Agent_명세.md`](../health/Health_Agent_명세.md) · 복약 스키마는 [`medication_schedule.md`](../health/medication_schedule.md).

### 하는 일
- 건강검진·예방접종·추적관찰 일정 관리
- 집 근처 병원 찾기
- 진료 전에 최근 증상·식사·경과를 타임라인으로 정리
- **복약 일정 CRUD** — 이 Agent의 유일한 직접 쓰기. 생성·수정은 초안 payload → 보호자 제출, 중단은 soft delete(`status='stopped'`)

### 복약이 예외인 이유
`medication_schedule` · `medication_dose`는 **다른 Agent가 읽지 않는 도메인 전용 테이블**이다. 공유 테이블의 단일 writer 원칙(§1)에 걸리지 않는다. 기준은 누가 쓰느냐가 아니라 **누가 읽느냐**다.

**생성·수정은 초안 payload로 나간다**(2026-09-22 변경). Health는 코스와 복용 시점을 JSON 초안으로 만들 뿐이고, 보호자가 확인 모달에서 제출해야 백엔드가 `medication_schedule`에 쓴다 — Memory의 `event` 초안과 같은 방식이다. 승인 전 행이 DB에 없으니 발송 잡이 거를 것도, 만료시킬 것도 없다. 모달에는 실제 알림 시각을 반드시 표시한다 — 보호자가 승인하는 것은 발화가 아니라 실제로 울릴 시각이다. 중단은 soft delete(`status='stopped'`) — 행이 남아 복용 기록이 보존된다(M-12 닫힘). 처방전 경로에서는 확인 카드의 "확인·등록"이 제출을 겸한다.

### 하지 않는 일
- 질환 확진 · 처방 결정 · 약 용량 임의 변경
- 증상만 보고 알레르기나 질환을 장기 Profile로 확정
- 아이의 발달 수준이나 장애 판단
- 처방받지 않은 약 제안 · 발화에 없는 약명·용량을 채우는 것
- 증상 패턴으로 약을 늘리거나 줄이자고 제안
- 성장 백분위·BMI 판정 (제거됨 — 신체 성장은 Growth가 추이만 정리)
- 체중과 관련한 식이·운동 조언

---

## 9. Dynamic Tool Gating

### 왜 필요한가

Agent를 도메인별로 나누는 것만으로는 부족하다. 같은 Food Agent라도 영아와 유아에게 필요한 기능이 다르다.

### 1단계 — Tool Gating (시스템이 결정)

아이의 **연령 · 식이 단계 · 건강/안전 제약 · 데이터 보유 여부 · 보호자 권한**을 보고 이 Agent가 지금 쓸 수 있는 Tool을 제한한다.

> **주체 정정**: 이전 판에는 "Supervisor가 Tool Set을 결정"으로 적혀 있었으나, **각 Agent의 registry가 결정**한다. Supervisor가 tool을 알면 Agent를 고칠 때마다 Supervisor 프롬프트를 고쳐야 한다.

| Agent | Gating 축 | 닫히는 예 |
| --- | --- | --- |
| Food | 식이 단계(birth_date) | 영아기 × 영양소 분석 = 없음 |
| Activity | 나이 · 위치 정보 유무 · 날씨 포트 가용성 | 위치 없으면 외출 장소 tool OFF |
| Growth | 나이 · 공지 유무 · 도서 API 가용성 · 측정 2건 이상 | 측정 1건 이하면 성장 추이 tool OFF |
| Health | 나이(24개월) · `health_safety` 조회 성공 · 위치 | 위치 없으면 병원 검색 tool OFF |

Gating 축은 전부 **발화가 아니라 아이 데이터에서** 온다. **성별은 어느 Agent의 gating 축도 아니다** — 추천·계산에 쓰지 않는다.

### 2단계 — Tool Routing (Agent가 결정)

허용된 Tool 중 현재 요청에 필요한 것을 모델이 고른다.

### 모델에게 보이지 않는 코드 Tool

안전과 정확성이 걸린 계산은 모델이 부르는 tool로 두지 않는다. 모델이 부를 수 있으면 건너뛸 수 있기 때문이다.

`filter_food_safety` · `rank_evidence` · `compute_growth_delta` · `resolve_dose_timing` · `check_symptom_repetition`

---

## 10. Agent 결과의 다섯 채널

> 정본은 [`Agent_공통규약.md`](Agent_공통규약.md) §3. 아래는 그 요약이다.

```python
@dataclass(frozen=True)
class DomainAgentResult:
    agent: str
    task_type: str | None
    status: Literal["completed", "blocked", "unsupported", "failed", "degraded"]
    suggestions: tuple[SuggestionDraft, ...] = ()      # suggestion 테이블에 저장
    readouts: tuple[Readout, ...] = ()                 # 읽기 전용 출력, 비저장
    event_requests: tuple[EventRequest, ...] = ()      # Memory로 이관할 일정
    needs_observation: tuple[str, ...] = ()            # 역질의
    medication_drafts: tuple[MedicationDraft, ...] = ()         # 복약 초안 payload
    model_calls: int = 0
```

| 채널 | 저장 | 승인 | 쓰는 Agent |
| --- | --- | --- | --- |
| `suggestions` | `suggestion` (draft, +24h) · 요청 1건당 3개 | 사용자 → 승인 시 Memory 이관 | Food · Activity · Growth |
| `readouts` | **안 함** (세션 한정) | – | Growth · Health · Food(미지원 안내) |
| `event_requests` | `event` (draft) — **Memory가 코드로 생성** | 사용자 | Health |
| `needs_observation` | 안 함 — 화면이 한 줄로 물음 | – | 전부 |
| `medication_drafts` | 안 함 — 제출 시 백엔드가 `medication_schedule`에 | 사용자 | Health |

`Readout`에는 작성 주체(`authored_by`)가 있다. `"code"`면 코드가 만든 문자열이 그대로 화면으로 가고 **모델이 편집할 수 없다.** 성장 추이 서술과 검진 안내, 미지원 안내가 여기 해당한다.

suggestion은 생성과 동시에 `draft`로 저장되고 화면이 추천 카드로 전환된다. 이 화면은 그 요청의 응답을 그대로 보여주므로 묶음 키가 없다. 별도의 suggestion 목록 화면은 **만료 전 suggestion 전부**를 보여준다. 복약 초안은 이 채널이 아니라 `medication_drafts`로 나간다. DB에 쓰지 않고 payload로만 넘기는 점이 `suggestion`과 다르다.

---

## 11. 부록 — Curator와 OCR 파이프라인

둘 다 **Agent가 아니다.** 대화 입력을 받지 않고 Supervisor의 라우팅 대상도 아니다.

| | 입력 | 출력 |
| --- | --- | --- |
| Curator | `observation_*` 누적 | `profile_affinity` (병합·승격·감쇠) |
| OCR 파이프라인 | 기관 공지 **이미지** | 일반 공지 → `notice` · 급식표 → `daycare_meal` · 일정성 공지 → **추출 원문을 Memory로** (`event` · `event_item`은 Memory가 쓴다) |
| OCR 파이프라인 | 처방전·약봉투 **이미지** | `prescription_draft` (Health가 읽기만. 확인 카드의 "확인·등록"이 복약 초안 제출을 겸함) |

어느 갈래인지는 OCR 파이프라인이 추출 텍스트를 보고 판단한다. 일정성 공지는 Memory가 구조화한 뒤 이벤트 draft 확인 모달이 뜬다.

텍스트로 붙여넣은 공지는 OCR을 거치지 않고 **Memory Agent**가 처리한다. 텍스트로 들어온 **일반 공지**를 `notice`에 누가 쓰는지, 한 장에 일정과 일반 안내가 섞인 공지를 어떻게 나누는지는 미정이다. Growth는 `notice` 행을 **읽기만** 한다.
