# Agent 공통 규약

> 모든 도메인 Agent(Food · Activity · Growth · Health)가 지키는 규칙. Agent별 문서가 이 규약을 어기면 **이 문서가 이긴다.**
>
> **2026-09-22 갱신** — 쓰기 주체(OCR · suggestion 이관) · suggestion 3개 · 필터 후 재호출 1회 · 복약 승인 게이트 · 성장 판정 제거 · 성별 미사용
> **2026-09-22 (3차)** — **기피(−1)는 근거이지 제외 필터가 아니다**(§4) · Food는 영양이 선호보다 앞선다 · 복약 초안은 DB 저장 없이 payload(§2·§3) · **suggestion은 정확히 3개**(2~4 범위 폐지)
> **2026-09-22 (4차)** — 알레르기 후보 감지 제거(`safety_confirmations` 폐기) · 근거는 `suggestion_evidence` 테이블 · `daycare_menu`를 `intake_daily`로 흡수 · 승인 시 Memory가 관찰 생성 · 복약 중단 soft delete · **API 계약서 v1은 레거시**(이 문서들이 정본)
> **2026-09-22 (5차)** — 급식은 `daycare_meal`로 다시 분리하고 **Food 소유(UPDATE/DELETE)** · Food 라벨 3개(`daycare_meal` 신설) · `intake_daily`에서 끼니 슬롯 폐지 · 기록 충분 판정을 행 수로
> **2026-09-22 (6차)** — Growth 문서 조회를 `search_growth_doc` 하나로(별도 KB 없음) · `rhythm_info`는 readout · 승인 이관은 라벨로 · **Health는 문서 테이블 대신 상수 파일** · `event_requests`도 초안 payload · Health 근거 규칙 명시
> 하위 문서: [`Tool_공통.md`](Tool_공통.md)(tool 실행 세부) · [`연령별_Tool_전략.md`](연령별_Tool_전략.md)(게이팅 정본) · [`RAG_plan.md`](RAG_plan.md)(문서 행) · [`외부연결_계획.md`](외부연결_계획.md)(API·출처)

---

## 0. 전체 구조

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

라우팅과 Agent 경계는 [supervisor-agent-v1.md](../supervisor-agent-v1.md), 관찰·일정 저장은 [memory-agent-v1.md](../memory-agent-v1.md) 에 있다.

### Curator와 OCR 파이프라인은 Agent가 아니다

대화 입력을 받지 않고 Supervisor의 라우팅 대상도 아니다.

| | 입력 | 출력 |
| --- | --- | --- |
| Curator | `observation_*` 누적 | `profile_affinity` (병합·승격·감쇠) |
| OCR 파이프라인 | 기관 공지 **이미지** | 일반 공지 → `notice` · 급식표 → `daycare_meal` · 일정성 공지 → **추출 원문을 Memory로** (`event` · `event_item`은 Memory가 쓴다) |
| OCR 파이프라인 | 처방전·약봉투 **이미지** | `prescription_draft` (Health가 읽기만. 확인 카드의 "확인·등록"이 복약 초안 제출을 겸한다) |

어느 갈래인지는 OCR 파이프라인이 추출 텍스트를 보고 판단한다. 일정성 공지는 Memory가 구조화한 뒤 이벤트 draft 확인 모달이 뜬다.

텍스트로 붙여넣은 공지는 OCR을 거치지 않고 **Memory Agent**가 처리한다. 텍스트로 들어온 **일반 공지**를 `notice`에 누가 쓰는지, 한 장에 일정과 일반 안내가 섞인 공지를 어떻게 나누는지는 미정이다. Growth는 `notice` 행을 **읽기만** 한다.

---

## 1. Agent의 모양

```python
async def run(task: DomainTask, context: <Domain>Context, *, client=None) -> DomainAgentResult
```

- Agent는 **DB를 모른다.** 읽기 포트(Protocol)와 writer를 주입받는다. 구현체는 `app/api`가 붙인다.
- Agent는 다른 Agent 패키지를 **import하지 않는다.** 남의 테이블이 필요하면 포트 주입이다.
- 의존 방향: `supervisor → 어휘만` · `<domain> → common만` · `pipeline → 전부`.
- 밖으로 열린 함수는 `run` 하나.

---

## 2. 쓰기 권한

| 테이블 | 쓰는 주체 | 도메인 Agent |
| --- | --- | --- |
| `observation_*` · `profile_affinity` · `event` | **Memory** (공유 테이블 단일 writer). OCR이 일정성 공지로 분류한 것도 추출 원문을 Memory가 받아 초안 payload로 만든다 — `event` 행은 보호자가 제출할 때 생긴다 | 읽기만 |
| `notice` (일반 기관 공지) | **OCR 파이프라인**. 텍스트로 붙여넣은 일반 공지의 저장 경로는 미정 | Growth만 읽음 (보조) |
| `daycare_meal` | **OCR 파이프라인·급식 배치**가 INSERT · **Food**가 UPDATE/DELETE (도메인 전용 — 아무도 안 읽는다). Food에 INSERT를 주지 않아 없는 급식을 지어낼 수 없다. 승인 게이트 없음 | Memory·Activity·Growth·Health는 읽지 않는다 |
| `prescription_draft` | **OCR 파이프라인**(처방전·약봉투) | Health만 읽음 |
| `medication_schedule` · `medication_dose` · `medication_dose_log` | **Health** (도메인 전용 — 아무도 안 읽는다). 단 코스 생성·수정은 **초안 payload**로 내보내고 보호자 제출 시 백엔드가 쓴다(`event` 초안과 같은 방식). Agent가 직접 쓰는 것은 복용 기록과 중단(`status='stopped'`) | Food·Activity·Growth는 읽지 않는다 |
| `suggestion` · `suggestion_evidence` | 주입된 writer (`status='draft'`, `expires_at=+24h`). **승인되면 Memory Agent가 `observation_*`로 재구조화**해 저장한다 | 값만 만든다 |
| `health_safety` | **앱 API가 보호자 권한으로만.** Agent는 후보도 제시하지 않는다 (알레르기 후보 감지는 v1에서 뺐다) | 손대지 않음 |
| `*_doc` · `hazard_term` · 기준 상수 | 배치·마이그레이션 | 읽기만 |

기준은 **누가 쓰느냐가 아니라 누가 읽느냐**다. 여러 Agent가 읽는 테이블은 통로가 하나여야 한다.

---

## 3. 출력 채널 다섯

```python
@dataclass(frozen=True)
class DomainAgentResult:
    agent: str
    task_type: str | None
    status: Literal["completed", "blocked", "unsupported", "failed", "degraded"]
    suggestions: tuple[SuggestionDraft, ...] = ()
    readouts: tuple[Readout, ...] = ()
    event_requests: tuple[EventRequest, ...] = ()
    needs_observation: tuple[str, ...] = ()
    medication_drafts: tuple[MedicationDraft, ...] = ()
    model_calls: int = 0
```

| 채널 | 저장 | 승인 | 쓰는 Agent |
| --- | --- | --- | --- |
| `suggestions` | `suggestion` draft +24h · **요청 1건당 정확히 3개** | 사용자 → 승인 시 Memory 이관 | Food · Activity · Growth |
| `readouts` | **안 함** (세션 한정) | – | 전부 |
| `event_requests` | **저장 안 함** — pipeline이 Memory의 일정 tool을 코드로 불러 초안 payload로 내보낸다 | 사용자 | Health |
| `needs_observation` | 안 함 — 화면이 한 줄로 묻는다 | – | 전부 |
| `medication_drafts` | 안 함 — 제출 시 백엔드가 `medication_schedule`에 | 사용자 | Health |

- `status`와 근거 모드(`personalized`/`general`)는 **직교한다.** 날씨 조회는 실패했지만 개인화 근거는 있는 run이 `degraded` + `personalized`다.
- `Readout.authored_by="code"`면 코드가 만든 문자열이 **그대로** 화면에 간다. 모델 입력에도 넣지 않는다.
- **되묻기는 한 번에 하나.** 후보가 여럿이면 순위가 가장 높은 하나만 낸다.
- **초안은 저장하지 않는다.** `medication_drafts`는 Memory의 `event` 초안과 같다 — run 단위 버퍼에 모였다가 JSON payload로 한 번에 나가고, run이 끝나면 사라진다. 비어 있는 NOT NULL 칸 목록(`missing`)을 함께 실어 화면이 제출을 막는다. **만들자마자 행을 쓰는 `suggestion`과 혼동하지 말 것** — 복약은 행이 있다는 것 자체가 승인의 증거다.
- suggestion은 저장과 동시에 화면이 **추천 카드로 전환**된다. 카드 화면은 그 run의 응답을 그대로 쓴다(묶음 키 없음). 별도의 suggestion 목록 화면은 `expires_at > now()`인 suggestion 전부를 보여준다.

---

## 4. 근거 규칙

```
티어 1  confirmed  · polarity ∈ {+1, −1} · archived 제외
티어 2  candidate  · polarity ∈ {+1, −1} · strength ≥ 임계
티어 3  최근 14일 관찰 (polarity 무관)        (도메인이 허용할 때만)
── 전부 0행 ──  kind="general"
```

> 🔴 **기피(−1)는 제외 필터가 아니라 근거다.** "브로콜리를 싫어해서"도 추천을 고르는 이유다. 후보에서 지워버리면 무엇을 피해 골랐는지 화면에 말할 수 없고, 보호자는 그 추천이 우리 아이를 보고 나온 것인지 알 수 없다.
> 근거에서 빠지는 건 `polarity IS NULL`인 candidate 하나뿐이다 — 선호인지 기피인지 모르는 신호라 역질의로 되돌린다.

### 도메인별 소비 규칙

| polarity / state | Food | Activity | Growth |
| --- | --- | --- | --- |
| +1 / confirmed | 근거 | 근거 | 근거 |
| **−1 / confirmed** | **근거 — 피할 이유** | **근거** | **근거** |
| +1 / candidate | 근거(티어2) | 근거(티어2) | 근거(티어2) |
| **−1 / candidate** | **근거(티어2)** | **근거(티어2)** | **근거(티어2)** |
| NULL | 안 씀 → 역질의 후보 | 안 씀 → 역질의 후보 | 안 씀 → 역질의 후보 |
| 관찰(티어 3) | 쓴다 | 쓴다 | 쓴다 |

세 도메인이 같다. `rank_evidence()` 와 `pick_followup()` 은 도메인 인자를 받지 않아서 애초에 갈릴 수 없다 — Activity 만 달랐던 앞 판은 9/20 회의 결론과 `suggestion_evidence` 의 허용 `memory_kind` 양쪽과 안 맞았다(2026-09-23 정정). **Health 만 이 표의 대상이 아니다** — 맨 아래 항목을 볼 것.

- **코드가 후보에서 지우는 것은 안전뿐이다** — 알레르기(`health_safety`) · 연령 금지식품 · `hazard_term`. 기피는 여기 들어가지 않는다. 안전 필터는 여전히 가중치가 아니라 제거다.
- **기피를 근거로 썼으면 문장에도 남긴다.** 기피 근거를 인용한 추천은 무엇을 피했는지, 또는 왜 그럼에도 골랐는지를 `reason`에 밝힌다. 인용만 하고 말하지 않으면 보호자에게는 근거 없는 추천과 같다.
- **Food는 영양이 선호보다 앞선다.** 기피 근거가 있어도 영양 분석이 부족을 가리키면 그 식품군을 뺀 추천을 내지 않는다 — [`Food_Agent_명세.md`](../food/Food_Agent_명세.md) §6.
- **신선도는 Curator가 맡는다.** 오래된 관심은 Curator가 `strength`를 내리거나 `archived`로 바꾼다. Agent는 그 결과(`state`·`strength`)만 보고 티어를 매기고, 유예일을 다시 세지 않는다 — 값이 두 벌이 되면 한쪽만 갱신된다. NF-08(180일)도 Curator 쪽 규칙이다.
- 근거 0행이면 **일반 추천을 낸다**(`kind="general"`). `reason`을 코드 템플릿으로 덮어쓰고, 화면이 "또래 기준"과 함께 `scarcity`(쌓인 기록 건수 + 되물을 질문 1개)를 표시한다. 추천을 빼고 `scarcity`만 내리지 않는다 — 루트 CLAUDE.md §2가 "일반 추천을 낸다"이기 때문이다.
- **문서 행(`*_doc`)은 근거가 아니다.** `reference_refs`에 따로 담는다. `source_refs`는 아이 기록만.
- 18개월 미만은 `profile_affinity`가 구조적으로 0행이라 **티어 1·2 가 없다.** 티어 3(최근 14일 관찰)은 그대로 있어서 개인화가 아예 막히는 구간은 아니다 → 정상 경로로 테스트한다.
- **`observation_routine` 은 승격은 안 하지만 근거로는 쓴다.** `profile_affinity.domain` 에 `routine` 이 없어 티어 1·2 가 생기지 않는다. 관찰은 티어 3 로 그대로 인용된다 — Growth 의 `routine_coaching` 이 이 경로다 (2026-09-23).
- **Health는 이 규칙의 대상이 아니다.** `profile_affinity.domain`에 `health`가 없고 `observation_health`에는 `embedding`도 없다. Health의 근거는 **항상 관찰 직접 참조**이고, 티어도 `rank_evidence`도 벡터 검색도 쓰지 않는다 — 증상은 선호가 아니라서 쌓인다고 성향이 되지 않는다. Health는 `suggestion`을 만들지 않으므로 `suggestion_evidence`도 없고, 근거는 `Readout.source_refs`에 담긴다.

---

## 5. 게이팅

```python
tools_for(task_type, gate: Gate) -> tuple[str, ...]
```

`Gate`와 `LifeStage`의 필드는 [`Tool_공통.md`](Tool_공통.md) §2·§3이 정본이다. **여기에 복제하지 않는다** — 두 곳에 적어 두면 한쪽만 고쳐진다(실제로 `has_gender`가 그렇게 남아 있었다).

| 축 | 어디서 |
| --- | --- |
| 연령 | `birth_date` → [`연령별_Tool_전략.md`](연령별_Tool_전략.md) |
| 동의 | `consent(scope=child_health)` 최신 행이 `granted`인가 |
| 안전 | `health_safety` 조회 성공 여부 |
| 데이터 | 측정 건수 · 급식 행 · 공지 유무 |
| 환경 | 위치 권한 · 외부 API 가용 |

- **Gating은 각 Agent의 registry가 한다.** Supervisor는 tool을 모른다.
- 빈 튜플이면 **모델을 부르지 않고** 코드 readout으로 끝낸다(`model_calls=0`).
- 연령 때문에 닫혔으면 **언제 열리는지 함께 알린다.**
- **성별은 게이팅 축이 아니다.** 추천·계산 어디에도 쓰지 않는다.

---

## 6. Tool 규약 (요약 — 세부는 `Tool_공통.md`)

| | 모델 tool | 코드 tool |
| --- | --- | --- |
| 누가 부르나 | 모델 | registry·pipeline |
| 모델에게 | 스펙 노출 | **비노출** |
| 무엇이 여기 오나 | 조회·출력 | 안전·정확성·순위·판정 |

코드 tool: `filter_food_safety` · `filter_activity_safety` · `rank_evidence` · `compute_growth_delta` · `resolve_dose_timing` · `check_symptom_repetition` · `resolve_menu` · `search_*_doc`

- 허용 목록 밖 호출은 `TOOL_NOT_ALLOWED`.
- **조회 실패를 빈 목록으로 숨기지 않는다** (`UPSTREAM_ERROR` / 전용 예외).
- 외부 API는 런타임 직접 호출이 아니라 **포트 + 캐시**다.
- 출력 tool은 반드시 검증한다: 근거 id가 이번 run 조회 결과 안인가 · 후보가 코드가 만든 풀 안인가 · 금지어 · 안전 재검사.
- `expires_at` · `status` · `kind`는 **tool 인자에 두지 않는다.** 모델이 만료나 승인 상태를 정할 수 없다.

---

## 7. 호출 예산과 실패

| 규칙 | 값 |
| --- | --- |
| `model_calls` 가 세는 것 | **Agent 진입 1회 + 재시도 1회당 +1.** 구분 기준은 "하려던 일을 하는 중인가 / 실패해서 다시 하는가" 다 |
| 안 세는 것 | 정상 tool calling 루프. Memory 가 7바퀴를 돌아도 1이다 — **tool 을 잘못 골랐으면 그 안에서 다시 고른다.** 실제 왕복 수는 로그의 `steps` 에 남는다 (아래 표) |
| 세는 것 | Supervisor 의 `tool_choice` 폴백(기본 시도 3회 = 진입 1 + 폴백 2, `SUPERVISOR_STRICT=1` 이면 4회), 도메인의 안전 필터 재호출(1회), 위임이 어긋나 다시 나누는 Supervisor 재호출(1회) |
| run당 정상값 | **4** (Supervisor 1 + Memory 1 + 도메인 2). 재시도가 붙으면 그만큼 올라간다 |
| 예산을 넘으면 | 실행을 끊지 않는다. `pipeline._log` 가 경고를 남길 뿐이다 — 재시도가 붙었다는 신호이지 잘못이 아니다 |
| 도메인 Agent당 | **1회 이내** — 아래 재호출 예외만 2회 |
| 0회 경로 | 게이트 닫힘 · Growth 성장 추이 · Health 검진/병원/전달 서류 · 안전 조회 실패 |
| **재호출** | **안전 필터(사전·사후) 후 suggestion 후보가 3개 미만일 때만**, 그 Agent만 1회. 걸러진 항목을 제외 목록으로 넣는다. 기피는 필터가 아니라 근거라 재호출 사유가 되지 않는다. **재호출 후에도 3개를 못 채우면 남은 만큼만 낸다** — 개수 규칙의 유일한 예외다 |
| 그 외 출력 tool 거절 | 재호출하지 않는다 (2026-09-22 — 이전의 "거절 시 run당 1회 재시도"는 위 규칙으로 대체) |
| 동시 실행 | 도메인 Agent끼리 `asyncio.gather`, Memory 다음이라는 순서만 유지 |
| Activity | 진입 수를 **Activity 문서에서 따로 정한다.** 조회를 전부 사전 조회로 돌리면 모델을 부르는 자리가 출력 tool 하나뿐이라 위 표와 달라질 수 있다 — 담당자(이도헌)가 이 줄을 그 값으로 바꾼다 |
| 부분 실패 | 한 Agent가 죽어도 나머지 결과를 낸다 (`return_exceptions=True`) |
| 20초 초과 | 부분 결과로 전환 |

**`model_calls` · `steps` · `calls` 는 서로 다른 값이다.** 셋을 섞으면 예산 얘기가 엉킨다.

| 이름 | 무엇을 세나 | 상한 |
| --- | --- | --- |
| `model_calls` | **Agent 가 불려 나간 수** (진입 1 + 재시도마다 +1). run 단위 합계 | 정상값 4 — 넘으면 경고만 |
| `steps` | 한 Agent 안의 **LLM 왕복 수** | Memory 는 `MAX_STEPS = 7` |
| `calls` | 실제로 **실행한 tool 건수** | 없다 |

`steps` 와 `calls` 가 다른 이유는 **한 왕복에 tool 이 여러 개 올 수 있기 때문**이다. 모델이 한 턴에 `create_observation_food` 와 `create_event` 를 같이 부르면 그 바퀴에서 둘 다 실행되고 `steps` 는 1만 올라간다. 그래서 `MAX_STEPS` 는 tool 건수 상한이 아니다 — tool 건수에는 상한이 없고, 같은 `(tool 이름, 인자)` 재호출만 `_dedup_key` 가 막는다.

실패는 기본값으로 메우지 않는다. 날씨 조회 실패는 "맑음"이 아니고, 알레르기 조회 실패는 "제한 없음"이 아니다.

---

## 8. 전 Agent 절대 금지

1. **수치 생성** — tool 결과에 있는 수치만 옮긴다. 계산·추정·단위 환산 금지.
2. **진단·처방** — 질환 추정, 약 제안, 용량 변경, 치료식.
3. **평가·비교** — 또래·평균·정상·발달·빠르다/느리다. 색으로도 표현하지 않는다.
4. **한 번의 관찰을 성향·능력으로 확정.**
5. **보호자의 해석을 아이의 사실로 승격** ("요즘 산만하다" → 집중력 특성).
6. **예약·결제·연락** — 경로 자체를 만들지 않는다.
7. **발화에 없는 값 채우기** — 약명·용량·복용 시점·알레르기.
8. **필터에 걸린 후보를 고쳐서 통과시키기** — 제외만 한다.
9. **모델에게 필터 사유 알려주기** — 어휘 회피를 가르치게 된다.
10. **조회 결과에 없는 것 생성** — 장소명·책 제목·메뉴·날씨.

---

## 9. 안전 처리 순서

```
Supervisor 안전 사전검사(규칙)  ── 응급·진단 문의는 Agent에 오지 않는다
        ↓
코드 사전 필터                 ── 알레르기·연령·위험 용어. 실패하면 모델을 부르지 않는다
        ↓
모델 1회                       ── 열린 tool 안에서만
        ↓
코드 사후 필터                 ── 같은 필터를 출력에 다시. 근거·금지어 검증
```

같은 필터를 앞뒤로 두 번 거는 것이 규약이다. 모델이 부를 수 있는 안전 tool은 만들지 않는다.

---

## 10. 문구의 작성 주체

| 문구 | 작성 | 저장 |
| --- | --- | --- |
| 판정·안내·경고·미지원 | **코드 상수** (`*.readout.yaml`) | – |
| 일반 추천 이유 | **코드 템플릿** (연령별) | `suggestion.reason` |
| 개인화 이유·서술 | 모델 (금지어 lint 통과) | `suggestion.reason` · readout |
| 문서 행 | 사람이 원문 보고 재작성 | `*_doc` |

상수 문구는 키로 관리하고 테스트가 **글자 단위로** 비교한다.

---

## 11. 로그·개인정보

- 로그에는 `{kind, id}`만. 발화 원문·약명·증상·메뉴명을 남기지 않는다.
- 월령 대신 밴드를 남긴다(월령은 준식별자).
- 좌표는 요청 바디로만 받고 즉시 격자로 뭉갠다. DB·로그·모델 입력·**예외 메시지** 어디에도 원좌표가 없다.
- 외부 API 쿼리에 보호자 발화를 넣지 않는다. 닫힌 enum·정규화 키만.
- 민감정보(키·몸무게·건강) 읽기는 `child_health` 동의가 살아 있을 때만. 성별은 수집·사용하지 않는다.

---

## 12. 이름 규칙

| 대상 | 규칙 | 예 |
| --- | --- | --- |
| tool | 동사 + 목적어 | `lookup_daycare_menu` |
| tool `description` | *무엇*이 아니라 ***언제 부르는가*** | – |
| task_type | `<명사>_<명사>` 소문자 | `meal_recommendation` |
| readout key | `<상태>.<주제>` | `unsupported.milk_meal` |
| 문서 행 키 | `<도메인>.<유형>.<주제>.<범위>` | `growth.routine.self_care.toothbrush.step2` |
| 설정 파일 | `config/<주제>.yaml` · `reference/<출처>_<연도>.*` | `reference/vaccine_2026.yaml` |

`confirmed`·`candidate`·`archived` 같은 상태값은 **코드가 정본**이다.

---

## 13. 공통 테스트

전 Agent가 같은 모양으로 가진다. 전부 가짜 LLM·가짜 포트로 돈다(외부 호출 0).

| 파일 | 지키는 것 |
| --- | --- |
| `test_<domain>_tool_schema.py` | 스펙 직렬화 · 쓰기 tool 없음(**Food·Health 제외** — 각자 도메인 전용 테이블을 쓴다) · 설명에 값 예시 없음 |
| `test_<domain>_registry.py` | 게이팅 표 · 허용 목록 밖 미실행 · 코드 tool 비노출 |
| `test_<domain>_evidence.py` | §4 소비 규칙 표 그대로 · 0행 → general |
| `test_<domain>_output.py` | 근거 id 검증 · 금지어 · 사후 안전 필터 · `expires_at` |
| `test_<domain>_gating.py` | 경계 월령 양쪽 · 동의 철회 · 데이터 0건 |
| `test_readouts.py` | `authored_by="code"` 문자열 불변 · 비저장 |

필수 상황: **Curator 없음(affinity 0행)** · **동의 철회** · **외부 API 실패** · **기록 0건** · **경계 월령**.

---

## 14. 미결

| # | 내용 |
| --- | --- |
| C-1 | ✅ 닫힘 — `suggestion`에 **`kind`만** 추가. `task_type`·`content` jsonb는 안 한다. `reference_refs`는 `suggestion_evidence`로 흡수 |
| C-2 | ✅ 닫힘 — `reason`은 **text 한 칸**. `{why_this, why_now}`는 쓰지 않는다 |
| C-3 | ✅ 닫힘 — **일반 추천 + `scarcity` 동봉**(§4). 루트 문서가 이긴다 |
| C-4 | ✅ 닫힘 — data_model.md가 이미 정했다. `confirm`은 **저장하지 않는다**(상태를 바꾸지 않으므로 이력 값에서 제외) |
| C-5 | ✅ 닫힘 — **그대로 둔다.** 24시간이면 만료되므로 무효화 배치를 두지 않는다 |
| C-6 | ✅ 폐기 — 알레르기 후보 감지를 v1에서 뺐다. `safety_confirmations` 채널도 함께 사라졌다 |
| C-7 | ✅ 닫힘 — **`confidence_source`로 갈음**한다. 별도 `type` 컬럼을 두지 않는다 |
| C-8 | ✅ 닫힘 — **남은 만큼만** 낸다(§7) |
| C-9 | ✅ 닫힘 — **승인 시점에 Memory Agent**가 `suggestion`을 `observation_*`로 재구조화해 저장한다. feedback(`liked`/`disliked`/`not_acted`)은 그 관찰의 `polarity`(+1/−1/0)를 갱신한다 |

미결이 전부 닫혔다. 새로 열리는 것은 이 표에 다시 적는다.

> **API 계약서 v1(`docs/api/api-interface-v1.html`)은 레거시다.** 어긋나는 곳이 있어도 이 문서들이 정본이다.
