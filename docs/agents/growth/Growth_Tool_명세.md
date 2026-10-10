# Growth Agent Tool 명세

> 기준: [`Growth_Agent_명세.md`](Growth_Agent_명세.md) · [`Tool_공통.md`](../shared/Tool_공통.md)
>
> **2026-09-22 갱신** — 핵심은 `observation_routine`·`observation_education` × 외부 소스·문서(`growth_doc`·도서) 연결 추천 · `lookup_notice`는 보조 · 추천 3개 · 성장 판정 요청도 `growth_review`로
> **2026-09-22 (2차)** — 문서 조회 경로를 `search_growth_doc` 하나로 통합(별도 KB 인덱스 없음) · `rhythm_info`는 readout 하나 · 승인 시 관찰은 라벨로 가름 · `pick_next_step` 신설
> **2026-10-06** — 추천은 최대 3개(C-8 닫힘, #216) · 다 못 채우면 남은 만큼 + 개수 안내, 0개면 안내만
> **2026-10-08** — 안전 필터 범위(음식 용어는 교육·루틴, `health_safety` 는 교육만) · 18개월 미만 승격 미적용 · 루틴 근거는 같은 카테고리 14일 · 성장폭은 지표별 + 자세 단서 · 의료 처치 / 증상 닫힘 · `search_affinity` 는 {education, activity} · 교육 알레르기는 이름 대조 + 환경 알레르기 확인 문구(#261) · 도서 연령은 책 형태(`form`)로
> **2026-10-10** — #282 PM 리뷰 반영: 0–17개월 경고 → 차단 승격을 Growth 에도 적용 · G-9 확정(요약은 방향을 동사로, 숫자는 크기만) · 확인 문구는 식품 사전에 없는 알레르기 이름에(`caution.non_food_allergy`, `environmental` 제외) · 루틴 닫힘 목록 확정 · 교육 활동은 #249 경고 자리 전에는 파이프라인에 붙이지 않는다

## 0. 외부 데이터 소스

| 소스 | 제공 | 용도 | 연결 방식 |
| --- | --- | --- | --- |
| **도서관 정보나루** (국립중앙도서관) — data4library.kr | 도서 검색 · 연령대별 인기대출 · 도서 상세(ISBN·표지) | `search_books` | REST. 하루 500회, 서버 IP 등록 시 30,000회 → **`book_catalog` 캐시** |
| **국립중앙도서관 사서추천도서 API** — nl.go.kr | 사서 추천 목록(분류별) | 추천 가중치 (+) | 월 1회 배치 |
| 보건복지부 **제4차 어린이집 표준보육과정** (0–2세) | 영역별 놀이·경험 내용 | `search_growth_doc` (`learning_activity`, 12–35) | 사람이 읽고 **행으로 재작성** |
| 교육부·보건복지부 **2019 개정 누리과정** (3–5세) | 5개 영역 · 놀이 사례 | `search_growth_doc` (`learning_activity`, 36+) | 동일 |
| 루틴 자료 (배변·수면·식사 자립) | 소아청소년과학회·국가건강정보포털 | `search_growth_doc` (`routine_step`·`habit_strategy`·`manner_practice`·`rhythm_info`) | 동일. 확보 전에는 연령별 코드 템플릿만 |
| `child_growth_log` | 키·몸무게·측정일 | `compute_growth_delta` | 포트 |

- 도서는 **API 응답만 추천 가능** — 모델이 책 제목을 생성하면 `propose_books`가 ISBN 검증에서 거절.
- 연령 필터는 코드가 넣는다. 정보나루의 연령 코드(`age`)는 대출자 연령대라(0 = 영유아 0~5세) v1 월령 전체가 코드 하나에 들어가므로, 아동 도서(ISBN 부가기호 첫 자리 7)로 좁힌 뒤 서명 키워드(보드북·촉감책)와 KDC 로 책 형태(`form`)를 가르고 그 형태의 월령 범위를 쓴다. 처음 값은 [`growth_agent_own_table.md`](growth_agent_own_table.md) §7 GT-3 이다. 청구기호(`유` 계열)는 소장 도서관마다 달라 쓰지 않는다.
- **별도 KB 인덱스를 만들지 않는다.** 교육과정도 루틴 자료도 전부 `growth_doc` 행이다. 원문을 청크로 잘라 색인하면 라이선스가 걸리고 품질도 사람이 못 고친다 — 사람이 읽고 한 행 = 한 가지 쓸모로 새로 쓴다([`RAG_plan.md`](../shared/RAG_plan.md) §0).
- 단계는 인덱스가 아니라 행의 `min_month`·`max_month`로 갈린다. tool 게이트를 통과해도 그 월령 행이 없으면 일반 템플릿으로 간다.

---

## 1. 게이팅

> 정본은 [`연령별_Tool_전략.md`](../shared/연령별_Tool_전략.md) §4. Growth는 **tool별 `min_month` 임계값**으로 가른다 (배타적 tool이 없어 범주가 아니라 눈금이다).

| tool | `min_month` |
| --- | --- |
| routine · activity · affinity 검색 · `propose_routine_plan` · `search_books` · `propose_books` · `compute_growth_delta` | 0 |
| `search_education_memory` · `lookup_notice` · `propose_learning_activity` | **12** |

라벨 안에서 코드가 정하는 것: `propose_routine_plan.mode`(0–11 `rhythm_info` / 12+ `next_step` / 36+ `habit_fix`) · 허용 `routine_category`(12+ 자립·식사·전환·집안일, 24+ 예절, 36+ 습관) · `search_growth_doc`의 월령 필터(12–35는 표준보육과정에서 쓴 행, 36+는 누리과정에서 쓴 행) · 도서 연령 필터(보드북 / 그림책 / 지식책).

`growth_review`에는 연령 축이 없다 — `compute_growth_delta`는 월령과 무관하게 측정 로그 전부를 읽는다.

추가 닫힘: `growth_review` + `consent_child_health=False` → readout · `lookup_notice`는 `notice` 0건이면 목록에서 제외 · 도서 API 장애 → `book_suggestion` `()` + readout · `learning_suggestion` + `health_safety` 조회 실패 → `blocked.safety`(모델 0회, 다른 라벨은 그대로) · `routine_coaching` + 의료 처치 / 증상처럼 보이는 습관 → 해당 readout(§2 "루틴에서 닫는 것").

`routine_coaching`의 카테고리 판정: 모델이 `propose_routine_plan.category`로 선언 → 코드가 연령 허용표와 대조, 불허면 해당 readout으로 교체 (모델 호출 1회 유지).

---

## 2. 모델 tool

| Tool | 입력 | 출력 | 규칙 |
| --- | --- | --- | --- |
| `search_education_memory` | `query?`, `period` | `observation_education` + engagement | – |
| `search_routine_memory` | `category?`, `period` | `observation_routine` (context · assistance · completion · **trigger**) | – |
| `search_activity_memory` | `query?`, `period` | `observation_activity` (참고) | 결과 ref에 `kind="observation_activity"` 표시 → 화면 문구 분기 |
| `search_affinity` | `domains ⊂ {education, activity}` | `rank_evidence` 정렬 결과 | `routine` 은 affinity 가 없다(09-23) — 루틴 관찰은 티어 3 로만 인용한다 |
| `lookup_notice` | `period: "this_week"\|"next_week"` | 일반 기관 공지 요약 (보조) | 읽기만. 준비물·행사는 `event`로 가므로 여기 없음 |
| `search_books` | `keywords[]`, `k≤10` | `{isbn, title, author, publisher, cover_url}` | 연령 필터 코드 주입 · 캐시 우선 |
| `propose_learning_activity` | `items[3]: {title, steps[≤4], materials[], evidence_ids[], curriculum_ref?, reason}` | `SuggestionDraft[]` | 안전 필터(음식 용어 · `hazard_term` · 동의 시 알레르기) → 금지 표현 순 — 아래 "안전 필터", 순서는 [`Tool_공통.md`](../shared/Tool_공통.md) §5-1. `materials` 는 이 검사들의 대상이자 `suggestion.items` 를 채우는 자리다 |
| `propose_routine_plan` | `category`, `items[3]: {next_step, how, evidence_ids[], reason}` (`rhythm_info` 모드면 `items` 없음) | `SuggestionDraft[]` \| readout | `mode=rhythm_info`(0–11개월)면 **`Readout(code)` 하나**로 끝낸다(suggestion 없음) · `habit` + `trigger` NULL → `needs_observation` · **근거 0 → `needs_observation` (카테고리 무관)** — 근거를 세는 법은 아래 "루틴 근거". `routine_coaching`은 일반 추천을 내지 않는다 — 아이와 무관한 생활 조언이 되기 때문 · 음식 용어 → 금지 표현 필터 |
| `propose_books` | `items[3]: {isbn, reason, evidence_ids[]}` | `SuggestionDraft[]` | ISBN이 직전 `search_books` 결과에 없으면 거절 · **만료 전 `suggestion`에 이미 낸 책도 거절** ("다른 책도"에 같은 책이 다시 나오지 않게). "직전" 은 이번 run 에서 `search_books` 가 돌려준 책 전부다(검색을 여러 번 하면 쌓인다). 연령 필터에 걸러진 책은 돌려주지 않았으므로 낼 수 없다. 같은 호출 안의 같은 책과 4권째도 거절한다. 같은 책은 ISBN 이 아니라 `items` 에 들어가는 "제목(저자)"(`book_label`)로 가른다 — ISBN 은 저장하지 않아서 지난 추천과 맞춰 볼 값이 이것뿐이고, 보드북판 · 양장판처럼 ISBN 만 다른 같은 책도 함께 거른다. 거절은 후보 번호와 사유 코드로만 남기고 ISBN · 제목은 싣지 않는다. 코드는 `tools/books.py` |

### 0–11개월은 추천을 내지 않는다

`mode=rhythm_info`는 **`Readout(code)` 하나**다. 수면·수유 리듬 안내는 "다음에 해볼 것"이 아니라 일반 안내라서 승인할 것이 없고, 카드 3장을 띄우면 화면이 거짓말을 한다. 18개월 미만은 `profile_affinity`가 구조적으로 0행이라 티어 1·2 근거가 없다. 티어 3(최근 14일 관찰)은 살아 있어서 개인화가 아예 막히지는 않는다.

문구는 `growth_doc`의 `rhythm_info` 행에서 온다. 행이 없으면 연령별 코드 템플릿으로 간다.

### 승인된 추천이 가는 관찰 테이블

승인 시 Memory가 `observation_*`을 만든다([`Agent_공통규약.md`](../shared/Agent_공통규약.md) §14 C-9). **라벨이 테이블을 정한다** — 내용을 보고 고르지 않는다.

| 라벨 | 관찰 |
| --- | --- |
| `learning_suggestion` | `observation_education` |
| `book_suggestion` | `observation_education` |
| `routine_coaching` | `observation_routine` |
| `growth_review` | 없음 (suggestion을 만들지 않는다) |

🚨 **`observation_activity`로는 가지 않는다.** Growth는 놀이 기록을 **읽어서** 학습·루틴으로 잇는 Agent이지 놀이를 추천하는 Agent가 아니다. 근거로 인용하는 것과 그 테이블에 쓰는 것은 다른 일이다.

**개수·재호출** — 출력은 최대 3개. 사후 필터(`hazard_term` · 금지 표현 · 음식 용어 · 알레르기 · 의료 처치 — 아래 "안전 필터" · "루틴에서 닫는 것")로 3개 미만이 되면 걸러진 항목을 제외 목록으로 넣어 **모델을 1회 재호출**한다. 그래도 못 채우면 남은 만큼만 내고, 0개면 추천 없이 안내만 낸다([`Tool_공통.md`](../shared/Tool_공통.md) §5-2). 재호출 결과는 첫 호출에서 통과한 것 뒤 빈자리만 채우고, 같은 후보는 교육 · 루틴은 `content`, 도서는 "제목(저자)"(`book_label`)로 가른다. 금지 표현을 재호출 사유에 넣는 것은 Growth 의 확장이다 — Tool_공통 §5-2 는 안전 필터만 적고 늘릴지를 열어 두었는데, 금지 표현으로 지운 후보도 제외 목록에 넣어야 같은 후보가 다시 나오지 않는다. 그 외 이유로는 재호출하지 않는다. **기피(−1)는 필터가 아니라 근거라 여기 들어가지 않는다** — 걸러내지 않고 `reason`에 무엇을 피했는지 적는다.

**금지 표현 필터** (전 propose 공통, 코드): [`app/rules/evaluative.py`](../../../apps/api/app/rules/evaluative.py) 공통 목록 — 또래 비교 · 백분위 · 성장곡선 · 발달 속도 판정(늦 · 느리 · 발달이) · 능력 꼬리표(뛰어나 · 재능 · 소질) · 성장 정체 · 부족(정체 · 부족해) · 문제 행동. 한 글자 어간을 그대로 걸면 평범한 문장까지 걸려서 *"걸음마가 늦어요"* · *"체중이 부족해요"* 처럼 **성장 항목 뒤에 판정이 올 때**만 잡는다. `잘 크고` 는 넣지 않았다 — *"콩나물이 잘 크고 있어요"* 같은 기르기 놀이 문장이 걸린다.

### 안전 필터 — 무엇을 어디서 거르나 (2026-10-08)

**Growth 는 음식·재료가 들어가는 놀이를 내지 않는다** — 그건 Activity 몫이다. 이 경계를 모델에게 맡기지 않고 코드가 지킨다(루트 CLAUDE.md §2 "알레르기 필터는 규칙이 막는다").

| 라벨 | 음식 용어 스캔 | `health_safety` 대조 | `hazard_term` |
| --- | --- | --- | --- |
| `learning_suggestion` | ✅ | ✅ `allergy` · `environmental` · 동의가 있을 때 | ✅ |
| `routine_coaching` | ✅ | – | – |
| `book_suggestion` · `growth_review` | – | – | – |

- **음식 용어 스캔** — 후보 문장(교육은 `title` · `steps` · `materials`, 루틴은 `next_step` · `how`)에 `reference/allergen_terms.yaml`(식품 알레르기 표시 대상) 용어나 `reference/hazard_terms.yaml` 의 `food_choking` 축 용어가 나오면 **아이와 무관하게, 월령과도 무관하게** 후보를 뺀다. 건강정보를 읽지 않는 검사라 동의와 상관없이 돈다. 놀이 문장에서 오탐을 내는 별칭("에그 쉐이커" · "밀크 카톤" …)은 Activity 와 같은 제외 목록을 쓰고, 한 글자 별칭(밀 · 게 · 닭 · 잣 …)도 *"공 밀기"* · *"카드 게임"* 이 걸려서 Activity 처럼 쓰지 않는다(#261). "콩나물 기르기" 도 빠진다(대두 별칭) — 기르기 놀이 하나를 잃지만 경계와 맞다.
- **`health_safety` 대조 (교육만)** — Activity D7 과 같다. 동의가 있으면 `build_gate` 가 `allergy` · `environmental` 의 active 행을 run 당 한 번 읽어 run state 에 담고, 출력 검증은 그 값으로 **보호자가 적은 이름**을 후보 문장 · `materials` 와 대조한다. 19종은 사전 별칭으로 넓혀 보고(한 글자 별칭 제외), 19종 밖(라텍스 · 쑥 · 꽃가루 · 동물털)은 적은 이름 그대로 본다. 꽃가루 · 동물털 같은 환경 알레르기도 `allergy` 이고, `environmental` 은 고소공포 같은 것이다(`data_model.md`). 그래서 "라텍스" 로 적었는데 문장엔 *"풍선으로 숫자 세기"* 가 나오는 것처럼 이름이 다르면 걸리지 않는다. 이름을 물건 · 장소로 넓히는 대응표는 "어디까지 위험한가" 를 우리가 정하는 일이라 만들지 않고, 대신 **`allergy` active 행 중 이름이 식품 사전에 없는 것**이 있으면 교육 추천에 확인 문구(`caution.non_food_allergy`)를 붙인다(#261 리뷰 4번 · #282 리뷰). 식품 알레르기는 이름 대조로 빼고, 식품이 아닌 것은 뺄 활동을 코드가 못 정하니 보여 주고 확인하게 하는 것이다. 이름은 조각마다 공용 `read_label` 로 읽어 식품을 하나도 못 찾은 조각이 있으면 붙인다 — "땅콩, 꽃가루" 는 꽃가루 때문에 붙고, "달걀흰자" 는 달걀을 찾아서 붙지 않는다. 19종 밖 식품(쑥 · 키위)도 사전에 없어서 붙는다. `environmental`(고소공포)에는 붙이지 않는다 — `health_safety.category` 가 빠진 뒤로는 환경 알레르기만 고를 칸이 없어서 사전으로 가른다. 등록한 19종의 한 글자 이름(잣)을 문장에서 어떻게 볼지는 #261 의 결론을 따른다. 동의가 없으면 읽지 않고 진행한다. 조회에 실패하면 빈 목록으로 넘기지 않고 `learning_suggestion` 을 닫는다(`blocked.safety`, 모델 0회).
- `chronic_disease` · `behavioral` · `other_medical` 은 읽지 않는다 — Activity D7 과 같은 이유다. *"변비가 있으니 배변 훈련은…"* 같은 분기가 곧 LLM 없는 자동 진단이 된다.
- 루틴 · 도서는 `health_safety` 를 읽지 않는다. 루틴 문장에 나오는 음식은 음식 용어 스캔이 막는다.
- **0–17개월은 경고(warn)도 차단으로 올린다** (2026-10-10, #282 PM 리뷰). Activity 와 같은 선이다. 몇 개월부터 물놀이가 되는지는 어디에도 선이 없어서(AAP 도 "팔 닿는 거리에서 지켜봐라" 지 나이 제한이 없고, `hazard_terms.yaml` 출처 칸에도 "임계 월령 없음"), 서비스가 그은 선은 하나여야 한다 — 같은 아이에게 놀이는 막고 교육은 내보내면 설명이 안 된다. 잃는 것도 작다. `water` 축은 "들어가는 물"(욕조 · 풀 · 대야 · 바다)과 "물놀이" 라는 말뿐이라 수도꼭지 물 만지기 · 그릇에 물 담아 첨벙은 그대로 나간다. 음식 용어가 먼저 빠지므로 12–17개월 교육 활동에서 이 승격으로 막히는 축은 `water` · `suffocation_film` 이다. 승격은 지금 Growth `safety.py` 와 Activity(#261) 코드에 따로 있고, 두 Agent 가 한 규칙을 읽게 공용 판정(`level_at`)으로 옮기는 일은 별도 이슈다. 나중에 풀 때도 Growth 만이 아니라 Activity 와 같이, 축 단위로 푼다.
- 경고 · 확인 문구는 run state 에 남기고 화면 자리는 #249 를 따른다(`SuggestionDraft` 에 칸을 더하지 않는다). **#249 의 자리가 생기기 전에는 교육 활동을 파이프라인에 붙이지 않는다** (#282 PM 리뷰) — 자리 없이 나가면 "경고와 함께" 가 실제로는 경고 없이 나가는 것이다. 18개월 이상에도 경고는 남는다(`water` 72 · `suffocation_film` 96 · `small_parts` 36–72개월).
- `suggestion.allergens` 는 비운다 — 음식 용어나 등록한 이름이 든 후보는 빠지기 때문이다. `suggestion.items` 는 교육은 `materials` 로, 도서는 "제목(저자)"(`book_label`) 하나로 채운다 — 일정으로 만들면 준비물로 그대로 옮겨지기 때문이다. 도서의 ISBN 은 `suggestion` 에 저장하지 않는다(#292 리뷰). 저장한 뒤에 ISBN 을 읽는 곳이 없어서다 — 화면에 표지를 그리는 설계가 없고, `propose_books` 의 이미 낸 책 검사는 `items` 의 "제목(저자)" 로 한다. 그래서 `suggestion` 테이블에 칸을 더하지 않는다.

### 루틴 근거 — 무엇을 세나 (2026-10-08)

`routine_coaching` 의 "근거 0 → 역질의" 는 이렇게 센다.

- 근거 = 모델이 인용한 `observation_routine` 중 **선언한 `category` 와 같고 최근 14일 안**인 행이다. 코드가 대조한다. 공통 티어 3 과 같은 창이라 `rank_evidence` 는 그대로다.
- 교육 · 놀이 affinity 는 관심사를 잇는 데(*"공룡을 좋아해서 공룡 칫솔 노래"*) 더해서 인용할 수 있지만 **개수에 넣지 않는다.** 그것만 있으면 아이가 지금 어디까지 하는지 모르는 채 다음 단계를 내게 된다.
- `pick_next_step` 도 같은 창 안 관찰의 `assistance_level` 만 읽는다. 창 안 관찰이 없거나 `assistance_level` 이 비어 있으면 `ask.routine_current` 로 되묻는다. 오래된 수준으로 단계를 고르면 이미 넘어선 단계를 시킬 수 있다.
- 창을 넓히지 않는다. #246 의 재질문 상한(4회)은 Memory 되묻기 것이라 여기에 그대로 걸리지 않고, 되묻기가 잦아지는 문제는 도메인 Agent 되묻기에 답하는 길과 같이 #246 에서 정한다(#276).

### 루틴에서 닫는 것 — 의료 처치 · 증상 (2026-10-08)

사람이 정한 목록 두 개로 코드가 닫는다. 진단명은 말하지 않는다.

| 목록 | 예 | 결과 |
| --- | --- | --- |
| 의료 처치 용어 | 약 먹이기 · 안약 · 연고 · 흡입기 · 네뷸라이저 · 혈당 | `closed.medical_routine` — 처치 방법은 처방한 곳이 답한다. *"약을 주스에 섞어 보세요"* 는 약과 음식의 상호작용을 건드리는 의료 조언이다 |
| 증상처럼 보이는 반복 행동 | 눈 깜빡임 · 킁킁거림 · 머리 박기 | `closed.symptom_habit` — 습관인지 증상인지 Agent 가 가를 수 없어 교정안을 내지 않는다. 틱이라면 교정 시도가 오히려 해가 된다 |

- 요청 문장이나 인용한 관찰의 `subject` 에 걸리면 모델을 부르지 않고 닫는다(모델 0회). 모델 출력에 걸린 후보는 빼고 재호출 규칙을 따른다.
- Health 로 보내지 않는다. Health 는 보호자 기록을 옮겨 적을 뿐 조언하지 않아서, 보내면 보호자는 답을 받지 못한다.
- 목록 내용은 사람이 정한다(`hazard_terms.yaml` 과 같은 원칙). 목록은 `apps/api/reference/growth_closures.yaml` 에 두고, 처음 목록과 오탐 guard(*"치약"* · *"약속"* · *"약간"* · *"관장님"* …)는 구현 PR 에서 PM 확인을 받는다.

## 3. 코드 tool

| Tool | 입력 → 출력 | 규칙 |
| --- | --- | --- |
| `compute_growth_delta` | `child_growth_log[]`(**전부**), `asks_judgement: bool` → `Readout(code, kind="growth_delta")` | 측정 로그 전체를 시간순으로 읽어 **실제 수치 그대로** 서술. 최소 간격 없음 · 연령 축 없음. 측정일 전부 표기. 판정어 없음. 키 · 몸무게는 **지표별로** 판정 · 차분한다. 값은 `Decimal` 로 읽고 뺀다. **신체 성장의 유일한 계산** — 판정 요청("잘 크고 있어?")이면 `delta.checkup_hint`를 덧붙인다 |
| `rank_evidence` | [`Tool_공통.md`](../shared/Tool_공통.md) §4 | domains 복수 |
| `check_routine_category` | `category`, `stage` → allow \| readout key | 예절 ≥24개월 · 습관 ≥36개월 |
| `search_growth_doc` | run 시작 시 자동. 월령·`row_type`·`routine_category`·`trigger_tags` 필터 → 의미 검색 top-3 → 프롬프트 `[예시]` 구획 | 쿼리는 **코드가 조립**한다(라벨·단계·관심사 `merge_key`·루틴 카테고리). 보호자 발화를 넣지 않는다. 결과는 근거가 아니라 참고라 `source_kind='growth_doc'`으로 담는다 |
| `pick_next_step` | `subject` + 관찰의 `assistance_level` → `growth_doc` 행 | `next_step_of` 사슬에서 **현재 칸의 바로 다음 행**을 코드가 고른다. 모델은 그 행을 집 상황에 맞춰 문장으로 쓸 뿐 단계를 고르지 않는다 — 건너뛰면 아이가 아직 못 하는 걸 시키게 된다. **최근 14일 안 관찰의 `assistance_level` 만** 읽고, 없거나 비어 있으면 `ask.routine_current` (§2 "루틴 근거") |

`growth_delta` 템플릿 — 요약 + 측정 전부를 시간순으로. 요약은 방향을 동사로 말하고 숫자는 크기만 적는다(G-9). 직전 대비 줄은 부호 그대로, 해석 문구 없음.

```
{first_date}부터 {last_date}까지 {months}개월간 키 {|dh|}cm · 몸무게 {|dw|}kg 늘었어요.   ← 두 지표의 첫·마지막 측정일이 같고 같은 쪽으로 움직였을 때 (둘 다 줄었으면 "줄었어요")
{first_date}부터 {last_date}까지 {months}개월간 키 {|dh|}cm 늘고 몸무게 {|dw|}kg 줄었어요.  ← 방향이 다를 때. 그대로면 "키는 그대로이고" · "몸무게는 그대로예요", 둘 다 그대로면 "키와 몸무게 모두 그대로예요"
{h_first}부터 {h_last}까지 {h_months}개월간 키 {|dh|}cm,                                 ← 측정일이 다를 때. 방향이 다르면 "키 {|dh|}cm 늘고,"
{w_first}부터 {w_last}까지 {w_months}개월간 몸무게 {|dw|}kg 늘었어요.
  {date}  키 {h}cm · 몸무게 {w}kg
  {date}  몸무게 {w}kg  (직전 대비 몸무게 {dw}kg)                                        ← 한쪽만 잰 날. 직전 대비는 부호 그대로(-0.3)
  {date}  키 {h}cm  (직전 대비 키 {dh}cm)
  …
{delta.one_metric}       ← 한 지표만 2건 이상일 때
{delta.posture_hint}     ← 키 측정이 24개월 전과 후에 모두 있을 때
{delta.checkup_hint}     ← 판정 요청일 때
```

- **간격으로 막지 않는다.** 2주 간격이라 키가 0.2cm 늘었으면 0.2cm라고 적는다. "말하기 어려워요"로 숨기는 쪽이 보호자에게 덜 정확하다
- 요약에서 변화가 0이면 "그대로예요" 로 적는다(직전 대비 줄은 `0.0` 그대로). 측정 로그가 많으면 전부 나열하되, 요약이 먼저 온다
- 보호자가 이해할 수 있게 **측정일과 실제 수치만** 쓴다. 속도·경향·전망을 만들지 않는다
- **지표별로 따로 판정한다** (2026-10-08). 키는 키가 있는 행만, 몸무게는 몸무게가 있는 행만으로 "2건 이상" 을 본다. 둘 다 1건 이하면 `delta.need_more`, 한 지표만 2건 이상이면 그 지표만 요약하고 나머지는 `delta.one_metric` 한 줄
- **"직전 대비" 는 같은 지표의 바로 앞 측정과 비교한다.** 바로 위 줄이 그 지표를 안 쟀으면 더 위의 그 지표 측정과 비교한다. 줄에는 그날 잰 값만 적는다 — 빈 칸을 "–" 나 0 으로 채우지 않는다
- **자세 단서** (G-8) — 24개월 무렵 누워서 재다가 서서 재기 시작해 최대 0.7cm 차이가 난다. 키 측정이 24개월 전과 후에 모두 있으면 `delta.posture_hint` 를 덧붙인다. 월령은 `rules/age.py` 로 계산한다. 숫자는 고치지 않고, *"괜찮아요"* 같은 안심 문장도 넣지 않는다(T34 실패 유형). 측정 자세는 수집하지 않는다
- 값은 DB(`numeric(4,1)`)대로 `Decimal` 로 읽고 뺀다. float 로 빼면 `104.2 - 95.5` 가 `8.700000000000003` 이 되어 "반올림 0건" 과 부딪힌다

> **G-9 — 확정 (10-09, #282 PM 리뷰 · FE 동의 10-08)** — 증감 숫자는 보호자가 적은 두 값의 차이라 평가가 아니다. 조건은 증감 옆에 평가로 읽히는 말을 붙이지 않는 것, 색 · 강조로 늘고 줄음을 구분하지 않는 것 둘이다. 줄었을 때 "키 -0.3cm 늘었어요" 가 되지 않게 요약은 부호에 맞춰 동사만 바꾼다(늘었어요 / 줄었어요 / 그대로예요, 방향이 다르면 "키 2.1cm 늘고 몸무게 0.2kg 줄었어요"). 직전 대비 줄은 동사가 없어서 부호 그대로 둔다. [`Growth_Agent_명세.md`](Growth_Agent_명세.md) §9.

## 4. 출력 상수 (code readout)

| key | 문구 |
| --- | --- |
| `closed.infant_learning` | 돌 전에는 놀이가 곧 배움이에요. 놀이 추천에서 함께 알려드릴게요. |
| `closed.habit_under36` | 이 시기에는 흔히 보이는 행동이라 교정보다 지켜보는 걸 권해요. 걱정되면 영유아 검진 때 상담해 보세요. |
| `closed.manner_under24` | 예절 연습은 두 돌쯤부터 알려드릴게요. 지금은 어른이 보여주는 것만으로 충분해요. |
| `closed.book_api` | 지금은 도서 정보를 불러오지 못했어요. 잠시 뒤에 다시 시도해 주세요. |
| `delta.need_more` | 성장폭을 보려면 측정 기록이 두 번 이상 필요해요. 최근에 재보실래요? |
| `delta.one_metric` | {metric}는 두 번 이상 재면 변화도 함께 알려드릴게요. |
| `delta.posture_hint` | 두 돌 무렵에는 누워서 재다가 서서 재기 시작해서, 재는 방법에 따라 키가 조금 다르게 나올 수 있어요. |
| `delta.checkup_hint` | 성장 평가는 영유아 건강검진에서 확인해 보세요. |
| `blocked.safety` | 지금 알레르기 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요. (Activity 와 같은 문구) |
| `caution.non_food_allergy` | 등록된 알레르기({label})가 있어요. 장소랑 재료를 한 번 확인해 주세요. (#261 리뷰 4번 문구 — 식품 사전에 없는 알레르기 이름에 붙는다, #282 리뷰) |
| `closed.medical_routine` | 약이나 처치 방법은 처방한 병원이나 약국에 여쭤보시는 게 가장 정확해요. |
| `closed.symptom_habit` | 이런 행동은 집에서 고치는 방법을 알려드리기 어려워요. 걱정되면 영유아 검진 때나 소아청소년과에서 상담해 보세요. |
| `closed.consent` | 키·몸무게를 보려면 건강정보 동의가 필요해요. |
| `ask.habit_trigger` | {subject}은(는) 주로 어떤 상황에서 하나요? |
| `ask.habit_current` | 요즘도 {subject} 하나요? |
| `ask.routine_current` | {subject}은(는) 요즘 어느 정도까지 혼자 하나요? |
| `rhythm.no_row` | 아직 이 시기 리듬 안내를 준비하지 못했어요. |
| `doc.no_row` | 이 시기에 맞는 자료가 아직 없어서 또래 기준으로 알려드려요. |
| `next_step.chain_end` | 이건 이미 혼자 잘 하고 있어요. 다음 단계는 준비되면 알려드릴게요. |

`rhythm_info` 본문은 상수가 아니라 `growth_doc`의 `rhythm_info` 행에서 온다 — 월령마다 다르기 때문이다. 행이 없을 때만 `rhythm.no_row`로 떨어진다.
