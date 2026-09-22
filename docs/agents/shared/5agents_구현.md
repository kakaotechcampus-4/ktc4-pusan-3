# 도메인 Agent 4종 구현 계획 — Food · Activity · Growth · Health

> 기준 문서: [`5agents.md`](5agents.md) (설계 정본) · 현재 코드 `apps/api/app/agents/`
>
> 이 문서는 **"5agents.md를 지금 코드 위에 어떻게 올리나"** 만 다룬다.
>
> **갱신 이력:** 2026-09-20 — `readouts` 채널 신설 · 복약 CRUD · 근거 티어 구현 · 라벨 10개 · §10 미결 절반 닫힘
> 2026-09-22 (2차) — Food 권장 열량에 키·몸무게 복귀(성별만 제외) · 필터 후 3개 미만 시 재호출 1회 · 묶음 키 불필요 · 복약 수정 게이트·중단 처리 · Growth 핵심은 관찰 기반 연결 추천
> 2026-09-22 — Health 성장 판정 제거(`assess_growth_percentile` · 성별 gating · LMS 선행 삭제) · 복약 승인 게이트 · suggestion 3개 + 승인 후 Memory 이관 · OCR 3갈래 · Food 급식 선행 추가
> 2026-09-22 (3차) — **기피(−1)는 근거, 제외 필터 아님** · Food는 영양이 선호보다 앞섬 · 복약 초안은 payload(DB 저장 없음) · `routine_coaching` 근거 0행은 전부 역질의 · 성장폭 최소 간격 폐지
> 2026-09-22 (4차) — §11 미결 13건 전부 닫힘 · 알레르기 후보 감지 제거 · `suggestion_evidence` 테이블 · 급식은 `daycare_meal`로 분리(Food 소유) · **API 계약서 v1은 레거시**

---

## 0. 전제 — 지금 코드가 이미 만족하는 것

| 5agents.md 규칙 | 지금 코드 | 상태 |
| --- | --- | --- |
| Supervisor는 아무것도 write 하지 않는다 | `supervisor/agent.py` — LLM 1회, `route` tool만 | ✅ |
| 입력 분해 · 도메인 Agent 최대 2개 | `supervisor/routing.py` — `MAX_DOMAIN_AGENTS = 2` | ✅ |
| 관찰 저장 먼저, 추천은 그다음 | `pipeline.handle_input()` | ✅ |
| Memory가 공유 테이블 단일 writer | Memory만 쓰기 tool을 갖는다 | ✅ |
| Tool Gating은 코드가 결정 | `food/registry.tools_for(task, stage)` | ✅ |
| 알레르기 필터는 모델에게 보이지 않는 코드 tool | `food/registry.CODE_TOOLS` | ✅ |
| 도메인 Agent는 `suggestion`에만 write | 없음 | ⛔ §4 |
| **`readouts` 읽기 전용 출력 채널** | **없음** | ⛔ §5 |
| **복약 도메인 전용 쓰기(Health)** | **없음** | ⛔ §5 |
| event 이관 · 역질의 | 없음 | ⛔ §5 |
| **근거 티어 랭킹** | **없음** | ⛔ §6 |
| Curator · OCR 파이프라인 | `app/agents/` 밖 — 범위 아님 | — |

실제 작업은 **① 공통 뼈대 ② Agent 3종 추가 ③ 출력 채널 4개 ④ 근거 랭킹**이다.

---

## 1. 공통 뼈대 — Agent 하나의 모양

```
app/agents/<domain>/
├── agent.py       run(task, context, *, client=None) -> DomainAgentResult
├── context.py     <Domain>Context — 아이 id · now · timezone · gating 축 · 읽기 포트
├── prompt.py      구획별 system prompt (하지 않는 일 포함)
├── registry.py    TOOL_HANDLERS · CODE_TOOLS · 묶음 표 · tools_for(...) · execute_tool(..., allowed=)
├── result.py      ToolResult · ErrorCode
├── schemas/       common.py · task.py · <기능별>.py · tool_defs.py
├── store/ports.py 읽기 포트 Protocol (구현체는 app/api가 주입)
└── tools/         tool 하나당 함수 하나
```

### `common/` 으로 올릴 것 (두 번째 Agent에서)

- `common/tool_runtime.py` — `ToolResult` · `ErrorCode` · `execute_tool(...)` · `specs_for(names)` 캐시
- `common/schemas/task.py` — `DomainTask` 봉투
- `common/suggestion.py` — `SuggestionDraft` · `build()`
- **`common/readout.py`** — `Readout` (신규, §5)
- **`common/evidence.py`** — `RankedEvidence` · `rank_evidence()` (신규, §6)
- `common/tool_schema.py` (이미 있음)

### 의존 방향

```
supervisor → memory.schemas.task · <domain>.schemas       (어휘만 import)
pipeline   → supervisor · memory · <domain> 전부
<domain>   → common 만
```

Growth가 `observation_activity`를 읽는 것은 **import가 아니라 포트 주입**이다. Growth는 Activity 패키지를 import하지 않는다.

---

## 2. Supervisor가 바뀌는 자리

`DomainAgentName`에 food · activity · growth · health가 이미 있다. 바뀌는 것은 **2차 라벨이 2개에서 10개로 늘어난 것**이다.

| Agent | 라벨 | 왜 묶음이 갈리나 |
| --- | --- | --- |
| Food | `meal_recommendation` · `nutrient_analysis` · `daycare_meal` | 묶음 5 vs 7, `health_safety` 사전 확인 여부. `daycare_meal`만 쓰기를 갖는다 |
| Activity | 없음 | 실내/야외는 조회 **결과**로 갈린다 — 런타임 판단 |
| Growth | `learning_suggestion` · `routine_coaching` · `book_suggestion` · `growth_review` | `growth_doc` 조회 · 도서 API · 차분(모델 0회)이 서로 겹치지 않는다 |
| Health | `visit_summary` · `schedule_check` · `place_lookup` · `medication` · `care_handoff` | tool 집합이 서로 겹치지 않고, `medication`만 쓰기를 갖는다. `care_handoff`는 모델 0회 |

> **라벨이 10개다.** 이전 판이 스스로 "라벨을 늘릴 때마다 오분류가 는다"고 적어뒀는데 그 사이 다섯 배가 됐다. 명세서 작성 시 **라벨별 경계 예시를 한 번에 뽑아 검토**해야 한다.

### 반드시 넣어야 하는 경계 예시

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

[health / medication]
"하루 세 번 항생제 먹여야 해"       → health / medication  (Memory 아님)
```

애매하면 **판정·확인이 붙는 쪽**으로 보낸다.

---

## 3. routing · pipeline이 바뀌는 자리

### routing

```python
IMPLEMENTED_AGENTS = frozenset({"food", "activity", "growth", "health"})

@dataclass(frozen=True)
class DomainTask:
    run_id: str
    agent: str
    task_type: str | None
    request_texts: tuple[str, ...]
```

유형별 묶기는 agent × task_type 당 하나, 상한(2)은 **agent 단위**로 센다.

### pipeline

```python
_RUNNERS: dict[str, DomainRunner] = {
    "food": run_food, "activity": run_activity,
    "growth": run_growth, "health": run_health,
}

results = await asyncio.gather(
    *(_RUNNERS[t.agent](t, contexts[t.agent]) for t in routing.domain_tasks),
    return_exceptions=True,   # NF-06
)
```

1. **동시 실행** — 도메인 Agent끼리는 순서가 없다. Memory 다음이라는 순서는 유지한다.
2. **부분 실패** — 한 Agent가 실패해도 나머지는 결과를 낸다. `partial` 이벤트를 여기서 만든다.
3. **호출 예산** — NF-01 재정의 완료: **입력 run당 최대 5회 · 도메인 Agent당 1회 이내** (§10 #6 닫힘).

---

## 4. `suggestion` 쓰기

```python
@dataclass(frozen=True)
class SuggestionDraft:
    agent: str
    kind: Literal["general", "personalized"]
    content: dict[str, Any]
    reason: str
    source_refs: tuple[Ref, ...]

def build(...) -> SuggestionDraft:
    """personalized 인데 source_refs 가 0행이면 거절한다."""
```

- **`status`는 Agent가 건드리지 않는다.** 생성은 항상 `draft` · `expires_at=+24h`.
- **요청 1건당 정확히 3개.** 출력 tool이 개수를 검사한다 — 3개가 아니면 거절. 근거 0행 general 추천도 같다.
- **필터 후 3개 미만이면 재호출 1회** — 안전 필터(알레르기·연령 금지식품·`hazard_term`)를 거른 뒤 후보가 3개 미만이면 해당 Agent만 모델을 **한 번 더** 부른다. 기피는 필터가 아니라 근거라 재호출 사유가 아니다. 재호출 프롬프트에 걸러진 항목을 제외 목록으로 넣는다. 재호출은 1회로 끝나고, 그래도 부족할 때의 처리는 §11 #10. `model_calls` 상한 검사는 이 경우에만 Agent당 2를 허용한다.
- **생성 즉시 화면 전환** — draft 저장과 동시에 pipeline이 추천 카드 화면 이벤트를 내보낸다. 카드 화면은 **그 run의 응답**을 그대로 쓰므로 묶음 키가 필요 없다. 별도의 목록 화면은 `expires_at > now()`인 suggestion 전부를 조회한다.
- **승인되면 Memory Agent가 관찰로 만든다** — `approved` 전환 직후 백엔드가 Memory Agent를 부르고, Memory가 `suggestion`을 `observation_*`로 재구조화해 저장한다. 값이 확정값이 아니라 자연어라 코드로는 못 쪼갠다 — 승인 1건당 모델 호출 1회가 붙는다. feedback(`liked`/`disliked`/`not_acted`)은 그 관찰의 `polarity`(+1/−1/0)를 갱신한다.
- **근거 0행 = `kind="general"`** — 이제 정상 경로다(§6). `reason`을 코드 템플릿으로 교체하고, 응답에 `scarcity`(쌓인 기록 건수 + 되물을 질문 1개)를 함께 싣는다. 추천을 빼고 `scarcity`만 내리지 않는다. 이것으로 food.md **F-5가 닫힌다.**
- 실제 INSERT는 `store/ports.py`의 `SuggestionWriter` Protocol로, 구현체는 app/api가 주입한다.
- 근거는 `suggestion.source_refs`(jsonb)가 아니라 **`suggestion_evidence` 테이블**이다. `PRIMARY KEY (suggestion_id, memory_kind, memory_id)`. 컬럼 이름은 루트 CLAUDE.md의 어휘를 따라 `memory_*`를 유지한다.
- `memory_kind`는 **두 무리**다. 아이 기록(`observation_*` 5종 · `profile_affinity` · `child_growth_log` · `notice` · `intake_daily` · `daycare_meal`)과 문서 행(`food_doc` · `growth_doc` · `activity_doc`). **품질 지표는 아이 기록만 센다** — 문서 행만 달고 나간 개인화 추천은 근거 0행과 같다.

---

## 5. 출력 채널 다섯 개

```python
@dataclass(frozen=True)
class DomainAgentResult:
    agent: str
    task_type: str | None
    status: Literal["completed", "blocked", "unsupported", "failed", "degraded"]
    suggestions: tuple[SuggestionDraft, ...] = ()
    readouts: tuple[Readout, ...] = ()                          # 신설
    event_requests: tuple[EventRequest, ...] = ()
    needs_observation: tuple[str, ...] = ()
    medication_drafts: tuple[MedicationDraft, ...] = ()         # 복약 초안 payload (Health)
    model_calls: int = 0
```

### 5-1. `readouts` — 읽기 전용 출력 (신설)

```python
@dataclass(frozen=True)
class Readout:
    kind: str          # growth_delta · symptom_timeline
                       # place_list · unsupported
    title: str
    body: str
    source_refs: tuple[Ref, ...] = ()
    authored_by: Literal["code", "model"] = "model"
```

`authored_by="code"` 면 코드가 만든 문자열이 **그대로 화면으로 간다.** 모델이 편집할 수 없다. 성장 추이 서술과 검진 안내가 여기 해당한다 — 모델이 다시 쓰다가 "또래보다 작아요" 같은 판정을 붙이는 것을 막는다.

readout은 **저장하지 않는다.** 피드백·만료·승인이 없는 일회성 출력이다.

한 번에 닫히는 것:

| 쓰는 곳 | kind | authored_by | 모델 호출 |
| --- | --- | --- | --- |
| Growth `growth_review` | `growth_delta` | code | **0** |
| Health `visit_summary` | `symptom_timeline` | model | 1 |
| Health `place_lookup` | `place_list` | code | 0 |
| Food 영아기 × 영양소 | `unsupported` | code | **0** — food.md **F-11 닫힘** |

### 5-2. 복약 — Health의 도메인 전용 쓰기

`medication_schedule` · `medication_dose`는 다른 Agent가 읽지 않는다. **공유 테이블 단일 writer 원칙에 걸리지 않는다.**

> 원칙 재정의: **공유 테이블의 단일 writer는 Memory다.** 도메인 전용 테이블은 소유 Agent가 쓴다. 기준은 누가 쓰느냐가 아니라 **누가 읽느냐**다.

Food · Activity · Growth는 `medication_*`을 읽지 않는다. 읽을 이유가 생기면 그때 규칙을 다시 연다.

DB 권한: Agent role에 `medication_*` write 부여, `health_safety` write는 계속 비부여.

**생성·수정은 초안 payload다**(2026-09-22). `create/update_medication_schedule`은 DB를 건드리지 않고 `medication_drafts` 채널로 JSON 초안을 내보낸다 — Memory가 `event` 초안을 다루는 방식 그대로다. 보호자가 제출하면 백엔드가 INSERT(생성) 또는 같은 행 UPDATE(수정)한다. 수정이 DELETE+INSERT가 아니라 UPDATE라 `medication_dose_log`가 남는다. **중단은 soft delete**(`status='stopped'`) — 행이 남아 복용 기록이 보존된다. `status`는 `active`/`stopped`/`completed`이고 `draft`·`expires_at`·`replaces_id`는 없다. 모달의 실제 알림 시각(`notice_times`)과 빈 필수 칸 목록(`missing`)은 초안의 필수 필드. 상세는 health_agent_own_table.md §6.

### 5-3. event 이관 — 모델을 다시 부르지 않는다

pipeline이 `EventRequest`(제목·시각·반복·event_type)를 받아 **Memory의 일정 tool을 코드로 직접 호출**한다. 값이 이미 확정값이라 LLM이 필요 없다.

> **복약은 이 경로에서 빠졌다.** event 이관에 남는 건 **재방문 · 검진 window**뿐이다.

승인 게이트는 그대로 — Agent가 만든 일정도 `draft`이고 사람이 승인한다.

> OCR 파이프라인이 **일정성 공지**로 분류한 것은 이 경로가 아니다. 추출 원문을 Memory에 넘기고, Memory가 모델로 구조화해 **초안 payload**를 만든 뒤 확인 모달이 뜬다. 제출해야 `event` 행이 생긴다. OCR은 `event`를 직접 쓰지 않는다.

### 5-4. 역질의

| 경우 | 질문 |
| --- | --- |
| `observation_routine.trigger`가 NULL | "손톱은 주로 어떤 상황에서 물어뜯나요?" |
| `polarity IS NULL`인 candidate affinity | "블록놀이는 좋아하는 편인가요?" |
| `routine_coaching` 근거 0행 | "요즘도 손톱 물어뜯나요?" |

질문은 **한 번에 하나.** pipeline이 이벤트로 내보내고 화면이 한 줄로 묻는다. 보호자 답이 다음 입력으로 들어오면 그때 Memory가 저장한다.

---

## 6. 근거 랭킹 — `rank_evidence()` (신규)

프롬프트에 "affinity를 우선 봐라"를 쓰면 테스트로 검증할 수 없고, tool 결과가 길어질수록 모델이 뒤쪽을 무시한다. **코드가 미리 정렬해서 건넨다.**

```python
# common/evidence.py — 모델에게 보이지 않는 코드 tool
def rank_evidence(
    affinities: Sequence[AffinityRow],
    observations: Sequence[ObservationRow],
    *, today: date, strength_threshold: float = 0.5,
) -> tuple[RankedEvidence, ...]:
    """
    티어 1  confirmed  (polarity in {+1, -1}, archived 제외)
    티어 2  candidate  (polarity in {+1, -1}, strength >= threshold)
    티어 3  최근 14일 관찰 (polarity 무관)
    전부 0행이면 빈 튜플 → 호출부가 kind="general"
    """
```

시그니처·티어 조건의 정본은 [`Tool_공통.md`](Tool_공통.md) §4다. **감쇠 인자는 받지 않는다** — `profile_affinity`는 Curator가 만들고 내린 결과이고 Agent는 읽기만 한다.

### 규칙 다섯 개 (전부 코드)

1. **`polarity IS NULL`인 candidate는 순위에서 제외** — 선호인지 기피인지 모른다. 역질의로 돌린다.
2. **`polarity=-1`은 근거** — 후보를 지우는 값이 아니라 무엇을 피해 고를지 정하는 값이다. 제외 필터 자리에 남는 것은 알레르기·연령 금지식품·`hazard_term` 뿐이다.
3. **기피만 있는 구간도 근거 0행이 아니다** — 최근 14일이 기피 관찰로만 차 있어도 그것이 근거다. `reason`에 무엇을 피했는지 적으면 개인화가 성립한다.
4. **NF-08** — 6개월 초과 affinity는 Curator가 `archived`로 내린다. Agent는 `archived`를 빼는 것으로 지킨다.
5. **관찰 14일 컷, affinity 무기한** — 의도된 비대칭. affinity 감쇠는 Curator의 `strength`가 표현한다.

### 출력 tool이 강제하는 것

각 추천 후보는 `rank_evidence()` 결과 중 **상위 N개 안에서 최소 1건을 인용**해야 한다. 이것으로 "affinity를 우선 봤는가"가 출력 검사로 확인된다.

### 순서 의존 위험

티어 1·2는 **Curator 산출물**이다. Curator보다 도메인 Agent가 먼저 나오면 모든 추천이 티어 3 또는 일반 추천으로 돌아간다. **1·2티어가 빈 상태를 정상 경로로 테스트**해야 한다.

`strength_threshold`는 Curator의 승격·감쇠 곡선이 나와야 정할 수 있다. 그때까지 **설정값으로 빼두고 테스트에서 고정**한다(기본 제안 0.5, `strength` 기본값이 0.3).

---

## 7. Tool Gating 일반화

```python
def tools_for(task_type: str | None, gate: Gate) -> tuple[str, ...]:
    """(공통 ∪ gate별) ∩ task별. 지원하지 않는 조합이면 빈 튜플 — 모델을 부르지 않는다."""
```

| Agent | Gating 축 | 닫히는 예 |
| --- | --- | --- |
| Food | 식이 단계(birth_date) | 영아기 × 영양소 분석 = `()` |
| Activity | 나이 · 위치 유무 · 날씨 포트 | 위치 없으면 외출 장소 tool OFF |
| Growth | 나이 · 공지 유무 · 도서 API · **측정 2건 이상** | 측정 1건 이하 → `growth_review` OFF |
| Health | 나이 24개월 · `health_safety` 조회 성공 · 위치 | 위치 없으면 `place_lookup` OFF |

### 코드 Tool (모델에게 안 보임)

| 이름 | Agent | 왜 코드인가 |
| --- | --- | --- |
| `filter_food_safety` | Food | 모델이 부르는 tool이면 건너뛸 수 있다 |
| `rank_evidence` | Food · Activity · Growth | 순위를 프롬프트로 지키게 할 수 없다 |
| `compute_growth_delta` | Growth | 성장 추이 계산(차분·구간). 신체 성장의 유일한 계산 |
| `resolve_dose_timing` | Health | 시각 해석은 규칙으로 고정 |
| `check_symptom_repetition` | Health | 증상 3회 규칙은 판정이지 서술이 아니다 |

---

## 8. Agent별 구현 메모

각 Agent는 **① mock → ② 읽기 tool → ③ 출력 tool + 코드 규칙** 순서. Food가 ①을 끝냈다.

### Food
- **선행**: ORM 어댑터 · `health_safety` 조회 · **`intake_daily`에 급식 행(OCR 적재 + 대체식 교체)** · 영양 데이터 소스 · 권장 섭취량 외부 소스(연령별 일반값 + 성별 없는 열량 계산식) · 알레르기 "없음 vs 모름" 구분(F-4)
- **코드가 막는 것**: 알레르기 필터 · 정확한 섭취량 생성 금지 · 근거 매핑
- **영양이 선호보다 앞선다**: 기피 affinity가 있어도 부족한 식품군을 후보 풀에서 빼지 않는다. 기피는 풀을 줄이는 값이 아니라 `reason`을 쓰는 값이다
- **닫힌 것**: F-5(일반/개인화) · F-11(영아기 안내)
- **`child_growth_log`는 권장 열량 계산 입력으로 읽는다**(코드 계산). 측정 없으면 연령별 일반값. **성별은 읽지 않는다**

### Activity (두 번째)
- **왜 두 번째인가**: 외부 API가 날씨·장소 둘뿐이고 쓰기 계약이 `suggestion` 하나로 끝난다. 공통 뼈대 검증용
- **읽기 포트**: `observation_activity` · `profile_affinity(domain=activity)` · Calendar · Weather · Place
- **코드가 막는 것**: 중복 제거 · 나이/안전 필터 · **예약·결제 경로 없음**
- **읽지 않는 것**: `observation_education` · `observation_routine`

### Growth
- **핵심**: `observation_routine` · `observation_education` → `growth_doc` · 도서 API 연결 추천. `notice`는 보조라 없어도 동작해야 한다
- **읽기 포트**: `observation_education` · `observation_routine` · **`observation_activity`(참고)** · `profile_affinity(domain ∈ education, routine, activity)` · **`child_growth_log`** · `notice` · `growth_doc` · 도서 API
  - `domain`을 복수로 받는다 — 포트 시그니처는 `domains: tuple[str, ...]`
- **`routine_coaching`은 `trigger` 필수** — NULL이면 역질의
- **`growth_review`는 모델을 부르지 않는다** — 차분 + 템플릿. `model_calls=0`. 판정 요청("잘 크고 있어?")도 여기로 온다 — 추이 + 검진 안내 템플릿
- **코드가 막는 것**: `strength`·`assistance_level`·`completion_status`의 점수 환산 금지 · 단발 관찰 승격 금지 · `polarity=-1`을 "못하는 것"으로 해석 금지 · **백분위 판정 금지**
- **suggestion의 `source_refs`가 `observation_activity`를 가리킬 수 있다** — 화면 문구가 "놀이 기록을 참고했어요"로 나와야 한다

### Health (마지막)
- **왜 마지막인가**: 안전에 가장 민감하고 외부 API가 가장 많다(질병청·건보·E-Gen). 앞의 셋으로 뼈대가 굳은 뒤에 붙인다
- **경계**: 진단·처방 없음. 같은 증상 3회 반복이면 추천을 멈추고 "병원 확인이 필요해 보여요" + 정리된 기록만. 이 판정은 **규칙**이다
- **Supervisor와의 분담**: 진단 문의는 `guarded/diagnosis`로 걸러져 Health까지 가지 않는다
- **`medication`이 유일한 쓰기 라벨** — 생성·수정은 초안 payload(DB 저장 없음), 중단은 soft delete. 확인 모달에 실제 알림 시각 필수
- **성장 판정 없음**: 백분위·BMI 판정은 제거됐다. 체중과 관련한 식이·운동 조언은 어느 Agent도 하지 않는다

---

## 9. 테스트 뼈대

| 파일 | 무엇을 지키나 |
| --- | --- |
| `test_<domain>_tool_schema.py` | tool 스펙 JSON 직렬화 · 쓰기 tool 없음(Health 제외) · 설명에 값 예시 없음 |
| `test_<domain>_registry.py` | gating 표 · 허용 목록 밖 tool 미실행 · 코드 tool이 모델에게 안 보임 |
| `test_<domain>_agent_mock.py` | mock이 포트·클라이언트 미접촉 · import 경계 |
| `test_evidence_ranking.py` | **신규** — 티어 순서 · `polarity IS NULL` 제외 · `archived` 제외 · 0행 → general. **시드는 `profile_affinity`가 이미 있다고 두고 넣는다** |
| `test_readouts.py` | **신규** — `authored_by="code"` 문자열 불변 · 비저장 |
| `test_medication.py` | **신규** — anchor 매핑 · 생성·수정이 DB를 건드리지 않음(초안 payload만) · 제출 전 `medication_schedule` 0건 · `missing` 비지 않으면 제출 잠김 · 제출 시 create=INSERT / update=같은 행 UPDATE(복용 기록 유지) · 중단은 `status='stopped'`이고 `medication_dose_log`가 남음 · 발송 0건 · `notice_times` 필수 |
| `test_pipeline.py` (확장) | 라우팅 · 동시 실행 · 부분 실패 · event 이관 · 역질의 · readout · suggestion 3개 · 승인 → Memory 이관 |

유닛은 전부 가짜 LLM으로 돌린다(API 호출 0).

### 반드시 있어야 하는 케이스

- **Curator 미구현 상태** — affinity 0행에서 티어 3 또는 general로 정상 동작
- **성장 판정 요청** — "잘 크고 있어?"가 `growth_review`로 가고 출력에 판정 표현이 없다
- **추천 개수** — 필터 후 3개 미만이면 재호출이 정확히 1회 일어나고, 3개면 재호출이 없다
- **측정 1건** — `growth_review`가 "성장폭을 말하기 어려워요"
- **최근 14일이 `polarity=-1` 관찰뿐** — general로 내려감

---

## 10. 순서와 선행 조건

```
① 공통 뼈대        common/tool_runtime · schemas/task · suggestion · readout · evidence
   └ 선행: 없음
② routing·pipeline domain_tasks · 실행 함수 표 · gather · partial · readout 처리
   └ 선행: ①
③ Activity         공통 뼈대 검증용. 날씨·장소 API 키
   └ 선행: ①② · suggestion 테이블 · ORM 어댑터
④ Food 실구현      `intake_daily` 급식 행(OCR) · 영양 데이터 · health_safety 조회 · 알레르기 없음/모름 구분
   └ 선행: ③
⑤ Growth           observation_routine/education × growth_doc·도서 API 연결 · child_growth_log · (notice는 후순위)
   └ 선행: ④
⑥ Health           질병청·건보·E-Gen · medication 마이그레이션(승인 게이트 포함)
   └ 선행: ⑤ · event 이관 채널이 실제로 돌아본 뒤
```

**공통 하드 선행 세 개**

| | 없으면 막히는 것 |
| --- | --- |
| `suggestion` 테이블 계약 + ORM 어댑터 | 전 Agent |
| `child_growth_log` | Growth `growth_review` · Food 권장 열량 |
| `intake_daily` 급식 행 + OCR 급식표 분류 | Food 급식 연결 |

---

## 11. 5agents.md와 코드가 어긋나는 곳 (갱신)

| # | 어긋난 점 | 상태 |
| --- | --- | --- |
| 1 | Growth vs education 저장 값 | ✅ **닫힘** — `growth`로 통일. 매핑하지 않는다 |
| 2 | Tool Gating 주체 | ✅ **닫힘** — 코드(각 Agent registry)가 맞다. 5agents.md §9 정정 완료 |
| 3 | 역질의 채널 | ✅ **닫힘** — Memory 재호출 없이 pipeline이 이벤트로 묻는다 |
| 4 | `affinity_id` · 임베딩 | 그대로 — 도메인 Agent는 `affinity_id=NULL` 관찰도 읽을 수 있어야 한다 |
| 5 | `expires_at` 24h vs Growth | Growth는 `suggestion.feedback` 대신 `observation_education.engagement_level`에서 배운다 |
| 6 | 호출 예산 | ✅ **닫힘** — NF-01 재정의: run당 5회 · Agent당 1회. `growth_review`·`unsupported`는 0회 |
| 7 | `correction.verdict`에 `confirm` 없음 | ✅ **닫힘** — 저장하지 않는다. "맞아요"는 상태를 바꾸지 않으므로 이력 값에서 뺀다(data_model.md) |
| 8 | 근거 철회 시 draft 무효화 | ✅ **닫힘** — 그대로 둔다. 24h면 만료되므로 배치를 두지 않는다 |
| 9 | 승인된 suggestion의 Memory 이관 | ✅ **닫힘(09-22)** — **승인 시점에 Memory Agent**가 `suggestion`을 `observation_*`로 재구조화해 저장한다. 승인 1건당 모델 호출 1회가 붙는 것을 받아들인다. feedback은 그 관찰의 `polarity`를 갱신한다 |
| 10 | 필터 후 추천이 3개 미만 | ✅ **닫힘(09-22)** — 재호출 1회 후에도 부족하면 **남은 만큼만** 낸다 |
| 11 | **공지 분류 경계** | **열림** — 텍스트로 붙여넣은 일반 공지의 `notice` 저장 경로 · 일정+일반이 섞인 공지 |
| 12 | suggestion 묶음 키 | ✅ **닫힘** — 불필요. 카드 화면은 run 응답을 그대로, 목록 화면은 만료 전 전부 |
| 13 | 복약 중단 시 복용 기록 | ✅ **닫힘(09-22)** — 중단을 **soft delete**(`status='stopped'`)로 바꿔 행이 남는다. M-12 · M-14가 함께 닫혔다 |
| 14 | **API 계약서 v1** | ✅ **닫힘(09-22)** — `docs/api/api-interface-v1.html`은 **레거시**다. 어긋나는 곳이 있어도 이 문서들이 정본이다 |
