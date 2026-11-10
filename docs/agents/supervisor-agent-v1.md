# Supervisor Agent 명세 v1

> 입력을 가장 먼저 받아 **무엇을 기록해야 하는지, 어떤 Agent가 필요한지**를 정하는 오케스트레이터.
> 코드는 `app/agents/supervisor/`. 관련: [shared/Agent_공통규약.md](shared/Agent_공통규약.md) · [shared/공통_구현_계획.md](shared/공통_구현_계획.md) · [memory-agent-v1.md](memory-agent-v1.md) · [api/chat-v1.md](../api/chat-v1.md)

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

### request_mode — 라벨이 아니라 request 공통 칸 (#574)

"보통 몇 살부터 혼자 양치해?" 처럼 **아이 일반**을 묻는 말은 추천 카드가 아니라 문서 근거 답변이다. 이 경계는 Agent 라벨을 늘리지 않고 `request` 조각의 공통 칸 `request_mode`(`suggest` 기본 · `info`) 하나로 가른다.

- `info` 는 **Growth · Health 조각에만** 붙는다. Activity · Food 의 경계는 이 칸으로 바꾸지 않는다 — 프롬프트가 두 Agent 에 붙이지 말라고 하고, 붙어 와도 스키마가 지워 지금처럼 추천으로 간다(`schemas._INFO_MODE_AGENTS`).
- Growth 의 `info` 면 `growth_task` 를 비운다(스키마가 지운다). routing 이 `DomainTask.task_type="info"`(`INFO_TASK`)로 내려보낸다. 추천 라벨 집합 · Growth 의 run 당 추천 하나 제한 · 05 · 0개 기록 대상에 들어가지 않는다.
- 분류는 모델이 한다. "보통" · "N세" 같은 낱말을 코드가 보고 가르지 않는다 — "우리 애 4살인데 숫자 공부 어떻게 시켜?" 는 나이가 있어도 suggest 다.
- Health 의 `info` 는 Agent 를 돌리지 않고 routing 이 닫힘 안내(`info_health_closed`)를 낸다 — 진단하지 않는 Agent 가 아이 일반의 건강 정보를 답으로 내지 않는다(루트 §2). 단 검진 · 예방접종 **시기** 를 묻는 말("몇 개월에 예방접종 맞아?" · "보통 몇 살에 영유아 검진 받아?")은 "보통" 이 붙어도 `info` 가 아니라 `suggest` 라 `schedule_check` 가 이 아이 월령으로 답한다 — 공개된 표준 일정이고 진단이 아니다(#574 리뷰). 닫힘은 그 밖의 건강 일반 질문("보통 아이들 정상 체온은 몇 도야?")에만 나간다. 닫힌 Health `info` 조각은 Agent 상한(2) 자리를 쓰지 않는다 — 안내만 내고 지나간다.
- 한 문장에 일반 질문과 우리 아이 추천이 같이 있으면 두 조각이고, 같은 Agent 의 서로 다른 두 task 로 간다.
- 질문 속 나이는 모델이 뽑지 않는다. `rules/age.asked_month_range` 가 읽는다(세 · 살은 만 나이, 개월, 돌).

- Growth 라벨이 빠지면 `learning_suggestion` 으로 채운다(Food 의 "애매하면 추천" 과 같은 방식).
- 한 run 에 Growth 추천 라벨(learning · routine · book)은 하나만 돈다 — 처음 것이되, 도서는 다른 추천 라벨이 같이 오면 자리를 내준다(`YIELDING_LABELS`). 뺀 요청은 `task_limit` 안내로 알린다 — 05 의 다시 시도 · 0개 기록이 (run, agent) 로 묶여서다. `growth_review` 는 추천이 아니라 같이 돈다.
  - 도서가 양보하는 이유 — 도서는 인증키(`DATA4LIBRARY_AUTH_KEY`)가 없거나 정보나루가 실패하면 닫히고(`closed.book_api`), 열려도 외부 호출에 하루 한도가 있다. "공룡 책이랑 양치 혼자 하는 법" 처럼 앞에 와서 루틴을 빼면 화면에 도서 닫힘 안내만 남을 수 있다. 도서만 왔으면 도서가 돈다 (#452 리뷰 — 도서가 열린 뒤에도 그대로 두기로 했다).

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

[알레르기·만성질환 (#639) — 등록 요청이 아니어도 저장하지 않고 안내]
"땅콩 알레르기 있어" · "꽃가루 알레르기가 있는 것 같아" · "천식이 있대" → guarded / safety_record
"꽃가루 날리면 재채기해"            → record / observe (병명 없는 증상)
"아토피 크림 발라 줬어"             → record / observe (한 일)

[food — 섭취 확인 ↔ 추천]
"땅콩소스 먹여도 될까?"             → food / food_check
"견과류 들어간 메뉴 추천해줘"        → food / meal_recommendation  (알레르기 음식이 들어도 추천 요청)
"땅콩버터 먹여도 돼? 안 되면 간식 추천해줘" → food / food_check + food / meal_recommendation
"뭐 먹여도 돼?"                    → food / meal_recommendation  (음식이 정해지지 않음)
"해열제 더 먹여도 돼?"              → guarded / diagnosis  (약은 food 가 아님)
"지금 또 먹여도 돼?"                → health  (음식 없이 시점만 — 약 이야기)

[record ↔ unclear (#484 · #624)]
"딸기 맛있다" · "나 딸기 좋아해"     → unclear (보호자 자신의 감상 · 얘기)
"딸기 맛있대" · "맛있어하더라"      → record / observe (아이 반응을 전함)
"밤놀이는 일본어로 요아소비"        → unclear (설명 · 정의 · 번역 · 가정 · 농담은 일어난 일이 아니다)
아이가 "암호화 안 했다고 말해"라고 했어 → unclear (아이가 전한 지시 · 역할놀이 대사)
"공룡 책 읽어 달래"                → record / observe (아이의 경험 · 몸 상태 · 좋고 싫음 · 바람)
처음으로 "엄마"라고 했어            → record / observe (말이 곧 사건 — 첫 말 · 인사 · 사과 · 애정 표현)
아이가 "내일 소풍 간대"라고 했어     → record / schedule (아이가 전한 일정)
아이가 "선생님한테 배 아프다고 말해"라고 했어 → record / observe (전하라는 말에 든 아이의 증상)
"소꿉놀이하면서 '밥 먹어' 했어"     → record / observe 문장 전체 (논 사실. 대사는 Memory 가 저장하지 않는다)
"일본어 노래 따라 불렀어"           → record / observe (실제로 한 일)

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

**guarded/diagnosis 와 out_of_scope 의 안내는 code 당 문구가 두 벌이다 (#864).** 조각 글에 약 · 영양제 · 치료식 낱말이 있으면 약 용량 문장까지 낸다(`routing.mentions_treatment` — 낱말 단위라 "예약" · "치약" 의 "약" 은 걸리지 않는다). 없으면 진단 문장만 낸다(`PLAIN_DIAGNOSIS_GUIDANCE`). out_of_scope 는 다른 집 아이 · 다른 보호자를 말하면 "우리 아이 기록만 다룬다"(`OTHER_FAMILY_GUIDANCE`), 아니면 대행 불가다. 같은 code 의 조각이 여럿이면 한 조각이라도 걸리는 쪽이다. code 는 그대로라 화면 계약은 바뀌지 않는다. 문구를 고르는 일은 코드가 한다 — Supervisor 는 조각 종류만 정한다.

### eval · 보정 규칙

이 예시와 프롬프트에 없는 변형을 `tests/eval/agents/routing_cases.py` 의 GR01~GR28 이 잰다(`supervisor/test.py -m live -k growth_routing`, `TEST_REPEAT=3`).

- 합격선 — agent 일치 90% · growth 유형 일치 90% 이상, 0건 규칙 위반 0
- 0건 규칙 — 신체 성장 3줄은 growth_review(Health · guarded 아님) · "배웠대" 는 요청이 아님 · "약 먹기 싫어해" · "깜빡이는데" 는 낱말이 Growth 로 간 조각이나 같은 입력의 기록 조각에 남음(Growth 의 코드 닫힘이 둘 다 본다)
- 보정 이력 — 끝까지 라이브(`growth/test_growth_supervisor_e2e.py`)에서 "숫자 놀이 뭐 없어?" 가 "놀이" 라는 낱말만 보고 activity 로 새는 것을 찾았다(직접 표본 3/10 만 growth). `_REQUEST` 에 "놀이라는 말이 있어도 배우는 낱말(숫자 · 한글 · 글자)이 있으면 growth/learning_suggestion, 없으면 activity" 한 줄을 더했고 12/12 가 됐다. 같은 말투(GR25 · GR26)와 반대 경계(GR27 · GR28 — 배우는 낱말이 없는 놀이는 activity)를 GR 에 더했다.
- 보정은 한 번에 하나, 최대 3회. "변형" 케이스의 문장을 프롬프트에 옮기지 않는다. 고친 뒤 split 합계(RC · T)가 떨어지면 되돌린다. 3회 뒤에도 미달이면 합격선을 낮추지 않고 숫자로 남긴다
- 앞 대화를 붙인 후속 질문은 FU01~FU14 가 잰다 — [§5-5](#5-5-eval)
- 일반 정보 경계는 IN 묶음(Growth · Health)이 잰다(`supervisor/test.py -m live -k info_routing`) — agent · info 판정 일치 90% 이상, info↔suggest 가 새는 건수 0. 한쪽으로 새면 추천이 안 나가거나 다른 나이의 추천이 저장되어 비율과 섞지 않는다.
- 말투만 다른 식사 추천(#861) — RQ01~RQ08 이 잰다(`-k request_tone`, `TEST_REPEAT=3`). 0건 규칙: 매번 Memory 없이 Food `meal_recommendation` 하나. 원인은 Supervisor 가 아니라 조각 밖에 남은 "야" 였다(`routing._covers_all` 이 추임새를 덮인 것으로 본다). 프롬프트는 서술어 없는 간식 요청("어린이집 끝나고 간식", 2/3 → 3/3)과 "말고" 정정 오인(RQ08)만 맡는다. "부르는 말은 뒤 조각에 붙여 자른다" 한 줄은 넣었다가 뺐다 — FC09(food_check) · T42(역할놀이 unclear)가 5번 중 5 · 4번 틀려(develop 3 · 1번) 코드가 맡는 쪽으로 정리했다.
- 거절 · 안내 문구는 RO01~RO08 이 잰다(`supervisor/test.py -m live -k readout_routing`, 0건 규칙, #864) — 약 얘기가 없는 진단 문의는 약 문구가 없다 · 다른 집 아이 · 다른 보호자가 하는 일을 묻는 말은 도메인 Agent 없이 "우리 아이 기록만 다룬다" 안내 · 또래 기준 추천은 request 그대로 · 친구네 집 · 옆집에서 겪은 일은 record. 프롬프트에는 예시 세 줄과 out_of_scope 정의 세 줄을 더했다. 바꾸기 전후 split(RC · T)는 [SUPERVISOR_EVAL.md](eval/SUPERVISOR_EVAL.md) §1 에 있다

---

## 5. 앞 대화 (후속 질문)

결과를 받은 뒤 채팅으로 이어 물으면("그럼 다른 건?" · "더 쉬운 거 없어?" · "아까 못 알려준 거 다시 알려줘") 이번 문장만으로는 무엇을 이어 묻는지 알 수 없다. 입력 1건 = run 1개 구조(루트 §4)는 그대로 두고, **후속 질문으로 보일 때만** 최근 대화를 Supervisor 에 넘긴다. 별도 Query Rewriter · LangGraph 는 쓰지 않고, Supervisor 호출 수도 그대로다(위임이 어긋나 다시 나눌 때만 두 번이고, 그때도 같은 맥락을 싣는다). 이슈 #734 — 감지 #784 · 조회 #785 · Supervisor #786 · 연결 #787.

### 5-1. 흐름

```text
입력 → (규칙) 후속 질문인가?
          ├─ 아니오 → Supervisor (이번 문장만, 지금과 같다)
          └─ 예 → chat_turn 조회(30분 · 3턴) → Supervisor (앞 대화 + 이번 문장)
                    → 조각은 이번 문장에서만, 이어 묻는 request 조각에는 resolved_request
                    → routing → 도메인 Agent (추천 task 만 해석한 요청을 받는다)
```

- **감지** — `supervisor/followup.py` 의 `looks_like_followup()`. 코드 규칙이고 모델에게 묻지 않는다. 앞 대화를 직접 가리키는 말("아까 추천한" · "그중" · "못 알려준" · "두 번째")은 주제어가 있어도 후속이다. 약한 말("다른 건" · "더 쉬운" · "그거")은 주제어(간식 · 놀이 · 책 …)가 없을 때만 후속이다 — "다른 실내 놀이 추천해줘" 는 혼자 읽어도 뜻이 통한다. "아까" · "방금" 은 "추천 · 알려줘 · 말한 · 보여줘" 꼴이 두 어절 안에 붙고 사이에 주어("선생님이")가 끼지 않을 때만 센 말이다 — "아까 좀 아파 보여서 해열제 먹였어" · "아까 친구 손을 물어서 혼났대" 같은 기록은 후속이 아니다. 놓침은 앞 대화 없이 처리되는 지금 동작 그대로라 놓침을 줄이는 쪽으로 쓰지만, 오탐도 공짜가 아니다 — 앞 대화용 프롬프트 · spec 으로 바뀌어 단발 요청의 분류가 조금 흔들린다(5-5 의 회귀 측정). 그래서 기록 입력이 후속으로 잡히는 표현은 좁혔고, 그 표를 `test_followup.py` 가 지킨다.
- **조회** — pipeline 의 `_conversation()` 이 후속일 때만 `agent_ports.recent_turns_loader` 를 부른다. 같은 보호자 · 같은 아이, 접수 30분 안, 최근 3턴(`domains/chat/repository.py` 의 `recent_turns`). 새 표 · 새 인덱스는 없다.
- **모델이 보는 글** — 앞 대화가 없는 호출은 이 기능이 생기기 전과 같은 system 프롬프트 · tool spec 을 쓴다(단발 요청의 분류가 달라지지 않는다). 프롬프트의 `[앞 대화가 올 때]` 절과 tool spec 의 `resolved_request` 칸은 앞 대화를 같이 보내는 호출에만 붙는다(`build_system_prompt(with_context=True)` · `ROUTE_TOOL_SPEC_CONTEXT`). 후속 질문 호출은 system 프롬프트가 달라 그 호출끼리만 캐시를 공유한다.
- **없을 때** — 앞 턴이 없거나 조회가 실패하면 run 을 멈추지 않고 **빈 맥락**으로 Supervisor 를 부른다. 프롬프트가 "[앞 대화] 없음" 이면 `unclear` 로 두라고 해서 질문 하나로 되묻는다 — 맥락 없이 부르면 "그럼 다른 건?" 이 아무 Agent 로나 간다. `chat_turn` 은 run 이 끝난 뒤에 쓰고 `done` 으로 끝난 run 만 남기므로, 결과를 받자마자 보낸 다음 입력에는 앞 턴이 아직 없을 수 있다. 이때도 같다. 조회가 실패했으면 "[앞 대화] 없음 — 앞 대화를 읽지 못했다" 로 보내 대화가 없었다고 거짓으로 말하지 않는다. 턴이 하나도 없으면 모델이 `resolved_request` 를 써도 지운다 — 이어 볼 것이 없는 해석은 지어낸 문장이다.

### 5-2. 무엇을 넘기고 무엇을 빼나

`agents/supervisor/context.py` 의 `ConversationContext.render()` 가 모델에 가는 글의 정본이다(자르기 · 개수 상한도 여기 한 곳).

| 넘긴다 | 뺀다 |
| --- | --- |
| 앞 입력 원문(300자까지) | readout 본문 — 알레르기 확인 · 성장 추이 같은 아이 기록 값이 든다. 코드가 쓴 문구는 모델 입력 금지 |
| 결과마다 Agent · 유형 · 상태 | 되묻기 질문 문장 — Health 는 코드가 쓴 readout 이고 등록한 복약 코스 이름이 든다 |
| 추천 이름(추천 행 `content` 의 첫 줄, 3개 · 60자까지) | 저장한 관찰 refs · 안내 · 일정 초안 · 삭제 제안 |
| 되물었다는 표시 · partial 에서 못 낸 Agent | |

추천 이름은 readout 이 아니라 `suggestion.content` 에서 읽는다(readout 제목은 대부분 비어 있다). **모델에 간 적 없는 말의 턴은 통째로 뺀다**(`chat_context._sendable`) — 위험 물질 안내가 나간 턴(규칙이 막아 모델을 부르지 않았다)과 되묻기에 답한 턴 전부(`reply_to` 가 있는 턴). Health 답은 이어받기가 모델 0회이고 답에 약 이름이 드는데, 응급 안내만 남거나 Health 가 실패한 턴은 결과 모양으로 Health 답인지 가를 수 없어 답한 턴을 다 뺀다. 후속 질문 때문에 처음으로 외부 LLM 에 가면 안 된다. Health 결과여도 새 입력(병원 찾기)은 Supervisor 가 읽은 말이라 싣는다. 뺄 턴이 끼어도 세 턴이 남게 9턴까지 읽고 거른다. Growth 교육 추천의 `content` 는 "제목 줄 + 본문" 이고 본문에는 아이 기록에서 온 말이 들어서 첫 줄만 이름으로 싣는다. DTO 의 글 칸은 `repr=False` 라 로그 · 예외 문구에 실리지 않는다.

### 5-3. 조각 text 와 resolved_request

조각 `text` 는 지금처럼 **이번 입력의 부분 문자열**이어야 한다(`validate_segments` · Memory 의 출처 단서 "했대"). 앞 대화로 뜻을 푼 문장은 `text` 를 고쳐 쓰지 않고 request 조각의 **`resolved_request`** 에 따로 낸다 — 앞 대화를 몰라도 뜻이 통하는 한 문장, 200자 이내. 비었거나 길거나 request 가 아닌 조각에 붙은 값은 지우고 조각은 남긴다. 앞 대화를 받지 않은 호출에서 나온 값도 지운다.

| 받는 쪽 | 받는 문장 |
| --- | --- |
| Memory | **이번 원문만**(`MemoryTask.raw_text` · 기록 힌트). 앞 대화 · 해석 문장이 기록 경로로 가지 않는다 — 앞에서 기록한 것을 다시 저장하지 않는다 |
| 추천 task — food/meal_recommendation · activity · growth 의 learning · routine · book | `resolved_request`(없으면 조각 원문). `routing.RESOLVABLE_TASKS` 가 `pipeline.RECOMMENDING_TASKS` 와 같은 값이다(테스트가 맞춘다) |
| 확인 · 쓰기 task — food_check · daycare_meal · nutrient_analysis · growth_review · health 전부 | 조각 원문. 모델이 만든 문장이 복약 대조 · 급식 갱신 · "먹여도 되나" 판정으로 가지 않게 한다 |

**해석한 문장이 "보호자가 말한 것" 으로 통과하지 않는다.** 해석한 문장에는 앞 추천 이름(AI 가 쓴 글)이 들어갈 수 있다. 그래서 해석을 받은 task 는 이번 입력의 보호자 원문을 `DomainTask.original_texts` 에 따로 들고(`originals()` — 비어 있으면 `request_texts` 가 곧 원문), 앞 턴들의 보호자 원문을 `prior_texts` 에 든다. 모델에게 보내는 요청 · 주제는 `request_texts`, "보호자가 말했는가" 를 코드가 보는 곳은 원문을 읽는다.

| 읽는 곳 | 읽는 글 |
| --- | --- |
| 추천 Agent 의 프롬프트 · 주제 | `request_texts`(해석한 문장) |
| Growth 닫힘(의료 처치 · 증상) · 화면 고민 판정 | `request_texts` 와 `original_texts` 둘 다 — 해석이 "약" 같은 닫힘 낱말을 빼먹어도 닫힌다 |
| Activity 지역 이름 · 요청한 놀이(`said_in_request`) | 이번 원문 + `prior_texts`(`ActivityRunState.said()`) — "해운대 근처 …" 뒤 "그럼 다른 데는?" 의 지역은 보호자가 앞에서 말했다 |
| Food 요청한 메뉴 · 재료(`said_in_food_request` · `requested_rows`) | 이번 원문 + `prior_texts`(`FoodRunState.said_texts()`) — 해석 문장에 든 앞 추천 이름("고구마 스틱 · 바나나 요거트 말고 …")이 "보호자가 재료를 정해 물었다"(#740 예외)로 통과하거나 후보 맨 앞에 서지 않는다. 앞에서 보호자가 정한 재료는 "그럼 다른 건?" 에서도 인정한다 |
| Growth 근거 수치 · 도서 검색어(`GrowthRunState.said_texts` — 원문 + 기록 조각 + 답) | 이번 원문(`originals()`) |
| 되묻기 맥락의 `hint_text`(답을 받으면 Memory · Supervisor 로 간다) | 이번 원문 — 이어 돌 task 는 `request_texts` 와 `original_texts` 를 둘 다 들고, `prior_texts` 는 비운다 |
| 05 다시 시도(`RetryContext`) | 셋 다 보관 — 해석한 요청으로 다시 만들고 출처 확인은 원문 · 앞 턴의 말로 한다 |

**Health 의 병원 찾기(`place_lookup`)는 분기만 지원한다.** 해석을 받는 task 는 추천 task 뿐이고(`RESOLVABLE_TASKS` 에 health 가 없다) Health 는 확인 · 쓰기 task 라 보호자 원문만 받는다. 그래서 "다른 데는 없어?" 는 health/place_lookup 으로 가지만 앞에서 무엇을 찾았는지(예: 소아과)는 Health 가 모른다. 이어 묻는 병원 찾기가 앞 조회를 이어 가게 하는 것은 이 기능의 범위가 아니다. FU07 은 이 분기까지만 잰다([5-5](#5-5-eval)).

Food 의 요청 안내(`request_notices`)는 `request_texts` 를 그대로 읽는다. 안내 문구에 해석 문장의 어절이 실릴 수 있지만, 알레르기 · 금지식품은 후보 풀을 코드가 이미 거른 뒤라 안전 판정에는 영향이 없다.

안전 사전검사(알레르기 · 건강 언급 · 위험 물질)와 응급 사전검사는 이번 원문만 본다. Supervisor 도 `kind` · `guard` 를 이번 입력의 말로 정한다 — 앞 대화가 요청이었어도 이번 입력이 증상 · 위험 물질 · 진단 문의면 그쪽이다.

### 5-4. 일부만 실패한 뒤 "아까 못 알려준 거"

`PartialReply.failed` 가 앞 턴에 남아 있어 "못 낸 것: activity" 로 Supervisor 에 간다. 못 낸 Agent 가 하나면 그 턴의 보호자 말에서 요청을 되살려 `resolved_request` 에 쓴다. 못 낸 것이 없거나 여럿이면 정할 수 없어 `unclear` 다. 05 다시 시도 보관함(`suggestion_retry`)은 쓰지 않는다 — 프로세스 메모리이고 15분이라 30분 창과 맞지 않는다.

### 5-5. eval

`tests/eval/agents/supervisor/followup_cases.py` 의 FU01~FU14 를 앞 대화를 붙여 잰다(`supervisor/test.py -m live -k followup_routing`, `TEST_REPEAT=3`).

- 합격선 — 이어 묻는 요청(FU01~FU08)의 agent(+유형) 일치 90% 이상, 0건 규칙 위반 0
- 0건 규칙(C1 표시는 어기면 치명 오류) — 앞 대화가 간식 · 놀이 요청이어도 증상 기록(FU09)은 request 가 되지 않고 위험 물질 섭취(FU10)는 guarded/hazard 다 · 영양제 문의(FU11)는 guarded/diagnosis 다 · 앞 입력의 기록(FU06)을 이번 조각으로 다시 내지 않는다 · 앞 대화가 없거나 이어 볼 것이 여럿이면(FU12~FU14) request 를 만들지 않는다 · 혼자 읽어도 뜻이 통하는 말(FU08)은 앞 주제로 끌려가지 않는다
- 회귀 — 단발 요청의 정확도가 떨어지지 않아야 한다. split 합계(RC · T …)를 앞 대화 없이 한 번, RC 에 상관없는 앞 대화를 붙여 한 번(`SPLIT_CONTEXT=unrelated -k "split and RC"`) 재서 변경 전과 견준다
- FU07(병원 찾기를 이어 묻는 말)은 health/place_lookup 으로 가는지만 잰다 — Health 가 앞 조회를 이어 받는지는 재지 않는다([5-3](#5-3-조각-text-와-resolved_request))
- `resolved_request` 를 채운 비율은 보고만 한다
- 상관없는 앞 대화를 붙인 RC 63 은 RECORD 43/47 · RECORD→REQUEST 4 로, 앞 대화 없는 기준선(44/47 · 2~3)보다 약간 낮다 — 후속으로 **잘못 감지된** 입력의 값이다. 완전히 막지는 못해서 감지 규칙을 좁히는 것으로 줄이고, 기준선 이상이라는 #786 의 조건은 이 경우에는 충족하지 못한다고 적어 둔다.
- 보정 이력 — 처음에는 앞 대화 절과 `resolved_request` 칸을 모든 호출에 실었다. 앞 대화가 없는 호출의 split(148)을 기준선(같은 모델 · 같은 입력 두 번: work 107 · 107, RECORD→REQUEST 2 · 3)과 견주니 두 번 모두 work 104 · 104, RECORD→REQUEST 3 · 6 으로 같은 방향으로 어긋났다("운동회 준비물" 일정 · 수정 경계 케이스가 일관되게 바뀜). 그래서 앞 대화가 있을 때만 싣도록 바꿨다. 지금은 앞 대화가 없는 호출의 system 프롬프트 · tool spec 이 변경 전과 해시까지 같다 — 단발 요청은 모델 입력이 같다.

### 5-6. 외부 LLM 으로 다시 가는 것

후속 질문에서는 보호자가 앞에서 이미 같은 모델에 보낸 문장이 한 번 더 간다.

- 앞 입력 원문과 추천 이름은 Supervisor 모델로 간다(`[앞 대화]` 메시지, 위 5-2 의 칸만). 모델에 간 적 없을 수 있는 말(위험 물질 안내가 나간 턴 · 되묻기에 답한 턴)은 싣지 않아서 "앞에서 이미 같은 모델에 보낸 문장" 이라는 전제가 지켜진다.
- `resolved_request` 는 추천 Agent 의 모델 입력이 된다 — 원래 요청 문장이 가던 자리다. 앞 대화의 추천 이름이 문장에 들어갈 수 있다.
- 보호자가 입력한 문장 · 추천 이름의 범위를 넘지 않는다고 보고 처리방침의 전달 항목은 늘리지 않는다. 아이 이름 · 알레르기 · 건강 정보 · 키 · 몸무게는 앞 대화로 가지 않는다(readout 을 싣지 않는다). Growth 가 기록 조각을 모델에 함께 싣는 것과 같은 판단이다([Growth_Agent_명세.md](growth/Growth_Agent_명세.md), PM 확인).
- 루트 `CLAUDE.md` §10 의 "외부 LLM 전달 범위" 는 이 결정으로 닫히지 않는다. 이 범위가 정해질 때 이 절도 같이 본다.
