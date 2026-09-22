# Growth Agent Tool 명세

> 기준: [`Growth_Agent_명세.md`](Growth_Agent_명세.md) · [`Tool_공통.md`](../shared/Tool_공통.md)
>
> **2026-09-22 갱신** — 핵심은 `observation_routine`·`observation_education` × 외부 소스·문서(`growth_doc`·도서) 연결 추천 · `lookup_notice`는 보조 · 추천 3개 · 성장 판정 요청도 `growth_review`로
> **2026-09-22 (2차)** — 문서 조회 경로를 `search_growth_doc` 하나로 통합(별도 KB 인덱스 없음) · `rhythm_info`는 readout 하나 · 승인 시 관찰은 라벨로 가름 · `pick_next_step` 신설

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
- 연령 필터는 코드가 넣는다: 정보나루 연령 코드 · 청구기호(유아 `유` 계열) · 서명 키워드(보드북·촉감책). 정확한 연령 코드는 API 문서로 확정.
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

추가 닫힘: `growth_review` + `consent_child_health=False` → readout · `lookup_notice`는 `notice` 0건이면 목록에서 제외 · 도서 API 장애 → `book_suggestion` `()` + readout.

`routine_coaching`의 카테고리 판정: 모델이 `propose_routine_plan.category`로 선언 → 코드가 연령 허용표와 대조, 불허면 해당 readout으로 교체 (모델 호출 1회 유지).

---

## 2. 모델 tool

| Tool | 입력 | 출력 | 규칙 |
| --- | --- | --- | --- |
| `search_education_memory` | `query?`, `period` | `observation_education` + engagement | – |
| `search_routine_memory` | `category?`, `period` | `observation_routine` (context · assistance · completion · **trigger**) | – |
| `search_activity_memory` | `query?`, `period` | `observation_activity` (참고) | 결과 ref에 `kind="observation_activity"` 표시 → 화면 문구 분기 |
| `search_affinity` | `domains ⊂ {education, routine, activity}` | `rank_evidence` 정렬 결과 | – |
| `lookup_notice` | `period: "this_week"\|"next_week"` | 일반 기관 공지 요약 (보조) | 읽기만. 준비물·행사는 `event`로 가므로 여기 없음 |
| `search_books` | `keywords[]`, `k≤10` | `{isbn, title, author, publisher, cover_url}` | 연령 필터 코드 주입 · 캐시 우선 |
| `propose_learning_activity` | `items[3]: {title, steps[≤4], evidence_ids[], curriculum_ref?, reason}` | `SuggestionDraft[]` | 금지 표현 필터 |
| `propose_routine_plan` | `category`, `items[3]: {next_step, how, evidence_ids[], reason}` (`rhythm_info` 모드면 `items` 없음) | `SuggestionDraft[]` \| readout | `mode=rhythm_info`(0–11개월)면 **`Readout(code)` 하나**로 끝낸다(suggestion 없음) · `habit` + `trigger` NULL → `needs_observation` · **근거 0 → `needs_observation` (카테고리 무관)**. `routine_coaching`은 일반 추천을 내지 않는다 — 아이와 무관한 생활 조언이 되기 때문 |
| `propose_books` | `items[3]: {isbn, reason, evidence_ids[]}` | `SuggestionDraft[]` | ISBN이 직전 `search_books` 결과에 없으면 거절 · **만료 전 `suggestion`에 이미 있는 ISBN도 거절** ("다른 책도"에 같은 책이 다시 나오지 않게) |

### 0–11개월은 추천을 내지 않는다

`mode=rhythm_info`는 **`Readout(code)` 하나**다. 수면·수유 리듬 안내는 "다음에 해볼 것"이 아니라 일반 안내라서 승인할 것이 없고, 카드 3장을 띄우면 화면이 거짓말을 한다. 18개월 미만은 `profile_affinity`가 구조적으로 0행이라 개인화도 되지 않는다.

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

**개수·재호출** — 출력은 3개. 사후 필터(`hazard_term`·금지 표현)로 3개 미만이 되면 걸러진 항목을 제외 목록으로 넣어 **모델을 1회 재호출**한다. 그 외 이유로는 재호출하지 않는다. **기피(−1)는 필터가 아니라 근거라 여기 들어가지 않는다** — 걸러내지 않고 `reason`에 무엇을 피했는지 적는다.

**금지 표현 필터** (전 propose 공통, 코드): 또래보다 · 뛰어나 · 재능 · 소질 · 늦 · 느리 · 발달이 · 잘 크고 · 정체 · 부족해 · 문제 행동

## 3. 코드 tool

| Tool | 입력 → 출력 | 규칙 |
| --- | --- | --- |
| `compute_growth_delta` | `child_growth_log[]`(**전부**), `asks_judgement: bool` → `Readout(code, kind="growth_delta")` | 측정 로그 전체를 시간순으로 읽어 **실제 수치 그대로** 서술. 최소 간격 없음 · 연령 축 없음. 측정일 전부 표기. 판정어 없음. **신체 성장의 유일한 계산** — 판정 요청("잘 크고 있어?")이면 `delta.checkup_hint`를 덧붙인다 |
| `rank_evidence` | [`Tool_공통.md`](../shared/Tool_공통.md) §4 | domains 복수 |
| `check_routine_category` | `category`, `stage` → allow \| readout key | 예절 ≥24개월 · 습관 ≥36개월 |
| `search_growth_doc` | run 시작 시 자동. 월령·`row_type`·`routine_category`·`trigger_tags` 필터 → 의미 검색 top-3 → 프롬프트 `[예시]` 구획 | 쿼리는 **코드가 조립**한다(라벨·단계·관심사 `merge_key`·루틴 카테고리). 보호자 발화를 넣지 않는다. 결과는 근거가 아니라 참고라 `memory_kind='growth_doc'`으로 담는다 |
| `pick_next_step` | `subject` + 관찰의 `assistance_level` → `growth_doc` 행 | `next_step_of` 사슬에서 **현재 칸의 바로 다음 행**을 코드가 고른다. 모델은 그 행을 집 상황에 맞춰 문장으로 쓸 뿐 단계를 고르지 않는다 — 건너뛰면 아이가 아직 못 하는 걸 시키게 된다 |

`growth_delta` 템플릿 — 요약 한 줄 + 측정 전부를 시간순으로. 감소값은 부호 그대로, 해석 문구 없음.

```
{first_date}부터 {last_date}까지 {months}개월간 키 {dh}cm · 몸무게 {dw}kg 늘었어요.
  {date}  키 {h}cm · 몸무게 {w}kg
  {date}  키 {h}cm · 몸무게 {w}kg  (직전 대비 키 {dh}cm · 몸무게 {dw}kg)
  …
```

- **간격으로 막지 않는다.** 2주 간격이라 키가 0.2cm 늘었으면 0.2cm라고 적는다. "말하기 어려워요"로 숨기는 쪽이 보호자에게 덜 정확하다
- 변화가 0이면 0으로 적는다. 측정 로그가 많으면 전부 나열하되, 요약 한 줄이 먼저 온다
- 보호자가 이해할 수 있게 **측정일과 실제 수치만** 쓴다. 속도·경향·전망을 만들지 않는다

## 4. 출력 상수 (code readout)

| key | 문구 |
| --- | --- |
| `closed.infant_learning` | 돌 전에는 놀이가 곧 배움이에요. 놀이 추천에서 함께 알려드릴게요. |
| `closed.habit_under36` | 이 시기에는 흔히 보이는 행동이라 교정보다 지켜보는 걸 권해요. 걱정되면 영유아 검진 때 상담해 보세요. |
| `closed.manner_under24` | 예절 연습은 두 돌쯤부터 알려드릴게요. 지금은 어른이 보여주는 것만으로 충분해요. |
| `closed.book_api` | 지금은 도서 정보를 불러오지 못했어요. 잠시 뒤에 다시 시도해 주세요. |
| `delta.need_more` | 성장폭을 보려면 측정 기록이 두 번 이상 필요해요. 최근에 재보실래요? |
| `delta.checkup_hint` | 성장 평가는 영유아 건강검진에서 확인해 보세요. |
| `closed.consent` | 키·몸무게를 보려면 건강정보 동의가 필요해요. |
| `ask.habit_trigger` | {subject}은(는) 주로 어떤 상황에서 하나요? |
| `ask.habit_current` | 요즘도 {subject} 하나요? |
| `ask.routine_current` | {subject}은(는) 요즘 어느 정도까지 혼자 하나요? |
| `rhythm.no_row` | 아직 이 시기 리듬 안내를 준비하지 못했어요. |
| `doc.no_row` | 이 시기에 맞는 자료가 아직 없어서 또래 기준으로 알려드려요. |
| `next_step.chain_end` | 이건 이미 혼자 잘 하고 있어요. 다음 단계는 준비되면 알려드릴게요. |

`rhythm_info` 본문은 상수가 아니라 `growth_doc`의 `rhythm_info` 행에서 온다 — 월령마다 다르기 때문이다. 행이 없을 때만 `rhythm.no_row`로 떨어진다.
