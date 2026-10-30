# Supervisor Agent 명세 v1

> 입력을 가장 먼저 받아 **무엇을 기록해야 하는지, 어떤 Agent가 필요한지**를 정하는 오케스트레이터.
> 코드는 `app/agents/supervisor/`. 관련: [shared/Agent_공통규약.md](shared/Agent_공통규약.md) · [shared/공통_구현_계획.md](shared/공통_구현_계획.md) · [memory-agent-v1.md](memory-agent-v1.md)

---

## 1. 하는 일 / 하지 않는 일

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
- **Tool Set 결정** — 각 Agent의 registry가 한다 ([Agent_공통규약.md](shared/Agent_공통규약.md) §5)
- 아무것도 write 하지 않는다

---

## 2. 2차 라벨

| Agent | 라벨 | 왜 묶음이 갈리나 |
| --- | --- | --- |
| Food | `meal_recommendation` · `nutrient_analysis` · `daycare_meal` · `food_check` | 묶음 5 vs 7, `health_safety` 사전 확인 여부. `daycare_meal`만 쓰기를 갖는다. `food_check`(정해진 음식을 먹여도 되는지)는 열린 tool 없이 코드가 등록 알레르기 · 연령 규칙으로 답한다(모델 0회) |
| Activity | 없음 (놀이 추천 하나) | 실내/야외는 날씨·시간 조회 **결과**로 갈린다 — 런타임 판단이지 라벨이 아니다 |
| Growth | `learning_suggestion` · `routine_coaching` · `book_suggestion` · `growth_review` | `growth_doc` 조회 · 도서 API · 차분(모델 0회)이 서로 겹치지 않는다 |
| Health | `visit_summary` · `schedule_check` · `place_lookup` · `medication` · `care_handoff` | tool 집합이 서로 겹치지 않고, `medication`만 쓰기를 갖는다. `care_handoff`는 모델 0회 |

라벨은 **Tool 묶음이 갈릴 때만** 만든다.

> **라벨이 13개다.** 이전 판이 스스로 "라벨을 늘릴 때마다 오분류가 는다"고 적어 뒀는데 그 사이 다섯 배가 됐다.
> 프롬프트를 쓸 때 **라벨별 경계 예시를 한 번에 뽑아 검토**해야 한다.

---

## 3. Agent 경계

도메인이 겹쳐 보이는 네 곳의 판정 기준이다. 이 기준이 없으면 같은 입력에 두 Agent가 붙어 비슷한 추천이 두 장 나간다.

### 3-1. Activity ↔ Growth

| | Activity | Growth |
| --- | --- | --- |
| 자기 관찰 | `observation_activity` | `observation_education` · `observation_routine` |
| 참고 | `profile_affinity` | `profile_affinity` · **`observation_activity`** |
| 시간축 | 지금/오늘 (날씨·거리 의존) | 반복·누적 (장소 무관) |
| 출력 | 무엇을 하고 놀까 | 무엇을 익히게 할까 |

**기본은 Activity.** "가르치다 · 배우다 · 책 · 글자 · 숫자 · 습관 · 혼자 하게" 같은 **학습·자립 신호**가 있을 때만 Growth로 간다. 신호어는 코드 키워드 매칭이 아니라 Supervisor 프롬프트의 경계 예시로 판단한다 — "오늘 블록 쌓는 거 배웠대"는 신호어가 있지만 요청이 아니라 관찰이다.

읽기 포트는 **비대칭이다.** Growth는 놀이 기록을 읽지만 Activity는 학습·루틴 기록을 읽지 않는다. "관심을 학습으로 확장"이 Growth의 역할이라 놀이 기록이 입력으로 필요하고, 반대는 필요가 없다.

### 3-2. Growth ↔ Health (신체 성장)

**겹치지 않는다 — 신체 성장은 Growth만 다룬다.** Health의 성장 백분위·BMI 판정은 제거됐다(2026-09-22). 시스템 어디에서도 성장을 판정하지 않는다.

| | Growth `growth_review` |
| --- | --- |
| 하는 것 | `child_growth_log`를 시간순으로 정리해 성장 추이를 서술 |
| 계산 | 차분·구간 계산 (코드, 모델 호출 0회) |
| 하지 않는 것 | 백분위 · 또래 비교 · "잘 크고 있다/아니다" 판정 |

판정을 요구하는 질문에도 판정 문구를 만들지 않는다. "성장 평가는 영유아 건강검진에서 확인해 보세요" 한 줄은 **코드 템플릿**이다.

### 3-3. Food ↔ Growth (식사 루틴)

**무엇을 먹었나 = Food(`observation_food`), 어떻게 먹나 = Growth(`observation_routine`).**

편식·섭취량·알레르기는 Food, 식사 자립·식사 예절·식사 중 행동은 Growth. Memory가 관찰을 어느 테이블에 넣느냐에서 이미 갈리므로, **Memory 프롬프트에 이 한 줄이 있어야** 경계가 성립한다.

### 3-4. Food ↔ Health (알레르기)

겹치지 않는다. Food는 확정된 `health_safety`를 **코드 필터**로 쓸 뿐이고, **둘 다 `health_safety`에 쓰지 않는다.** 알레르기 후보 감지는 v1에서 뺐다 — 등록은 보호자가 앱에서 직접 하는 것 하나뿐이다. 섭취 확인(`food_check`)도 `health_safety` 를 읽기만 하고, 판정 · 문구는 코드다 — 약 · 영양제를 먹여도 되는지는 Food 가 아니라 `guarded/diagnosis` 다.

---

## 4. 프롬프트에 반드시 넣을 경계 예시

```
[activity ↔ growth]
"농구 좋아하는데 뭐 하고 놀까"      → activity
"농구 좋아하는데 관련된 책 있어?"    → growth / book_suggestion
"밥 먹을 때 혼자 하게 하고 싶은데"   → growth / routine_coaching
"오늘 블록 쌓는 거 배웠대"          → 관찰. 요청 아님 (Memory만)

[신체 성장 — 전부 growth]
"얼마나 컸어?"                    → growth / growth_review
"잘 크고 있어?"                   → growth / growth_review  (판정 없이 추이 + 검진 안내)
"10cm 컸는데 많이 큰 거야?"        → growth / growth_review  (같음)

[키 · 몸무게 기록 — 채팅으로 저장하지 않는다 (#625)]
"오늘 키 재 보니 105cm 였어"       → guarded / growth_record  (11-1 성장 기록 안내, deeplink profile/growth)
"몸무게는 1톤"                    → guarded / growth_record  (값이 터무니없어도 같다)
"점심에 카레 먹었어. 키는 110cm 됐어" → record / observe "점심에 카레 먹었어" + guarded / growth_record

[health / medication]
"하루 세 번 항생제 먹여야 해"       → health / medication  (Memory 아님)

[food — 섭취 확인 ↔ 추천]
"땅콩소스 먹여도 될까?"             → food / food_check
"견과류 들어간 메뉴 추천해줘"        → food / meal_recommendation  (알레르기 음식이 들어도 추천 요청)
"땅콩버터 먹여도 돼? 안 되면 간식 추천해줘" → food / food_check + food / meal_recommendation
"뭐 먹여도 돼?"                    → food / meal_recommendation  (음식이 정해지지 않음)
"해열제 더 먹여도 돼?"              → guarded / diagnosis  (약은 food 가 아님)
"지금 또 먹여도 돼?"                → health  (음식 없이 시점만 — 약 이야기)

[record ↔ unclear (#484)]
"딸기 맛있다" · "나 딸기 좋아해"     → unclear (보호자 자신의 감상 · 얘기)
"딸기 맛있대" · "맛있어하더라"      → record / observe (아이 반응을 전함)

[정정 (#483)]
"~를 ~로 바꿔줘" · "A말고 B"       → record / lookup_edit 문장 전체
"당근 말고 다 먹었어"              → record / observe (빼고 말한 것)
"어린이집 점심 우유 말고 두유로"    → food / daycare_meal (급식 변경)

[공부·놀이 + 생활 행동 (#482)]
"그림책 보고 나서 제자리에 갖다 놨어" → observe 두 조각 (하다가 보인 짜증·거부는 나누지 않는다)
```

애매하면 **판정·확인이 붙는 쪽**으로 보낸다.

**키 · 몸무게는 묻는 말이면 growth, 값을 알리는 말이면 guarded 다.** `child_growth_log` 는 보호자가 잰 날짜와
단위를 화면에서 넣는 기록이라(루트 §2) 채팅 쓰기 경로가 없다. 예전에는 record 로 가서 Memory 가 저장할 곳 없이
"기록했어요" 라고 답했다(멘토 리뷰). 같이 온 기록 · 요청은 다른 guarded 와 같이 잘라서 보낸다.
