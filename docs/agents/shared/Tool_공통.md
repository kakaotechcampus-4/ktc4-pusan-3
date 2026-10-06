# Tool 공통 규약

> Food · Growth · Health tool 명세가 공유하는 것만. 위치: `app/agents/common/`
>
> **이 문서가 `LifeStage` · `Gate` · `rank_evidence` 세 가지의 정본이다.** 다른 문서는 여기를 가리키고 값을 복제하지 않는다.
> **2026-09-22 갱신** — 기피(−1)를 제외 필터에서 근거로 · 성별 게이팅 축 삭제 · `Gate` 필드 확정 · `FeedingStage`를 `LifeStage.stage`로 통일 · 출력 개수 3
> **2026-10-06 갱신** — 출력 개수는 최대 3개(C-8 닫힘, #216) · 0개는 실패가 아니라 안내만 · `check_count(exhausted=)` · 개수 안내 상수 `suggestion.readout.yaml` · 더 채울 길이 없으면 0개도 `check_count` 통과 · 재호출 결과는 빈자리만 채움 (§5-2)

## 1. 실행 규약

| 항목 | 규칙 |
| --- | --- |
| 공개 | **모델 tool** = 모델이 호출 · **코드 tool** = registry가 호출, 모델에게 스펙 비노출 |
| 허용 목록 | `execute_tool(name, args, allowed=tools_for(task_type, gate))` — 목록 밖이면 `TOOL_NOT_ALLOWED` |
| 반환 | `ToolResult(ok, data, error: ErrorCode \| None, source_refs)` |
| 빈 결과 | 성공 + 빈 목록. **조회 실패를 빈 목록으로 숨기지 않는다** (`UPSTREAM_ERROR`) |
| 외부 API | 값이 크고 재사용되면 **캐시 테이블 조회** — 캐시 미스만 API 호출 + 저장. 메뉴 영양·레시피·급식·도서가 여기다 |
| 외부 API (실시간) | **지금 값이어야 하는 것은 저장하지 않는다** — 날씨 · 대기질 · 응급실 · 장소. 패스스루 + 프로세스 내 캐시만 쓴다. 틀린 값이 DB 에 남으면 근거처럼 보이고, 아이별 데이터가 아니라 붙이면 삭제 범위만 넓어진다. 날씨는 발표 시각(`nx` · `ny` · `base_date` · `base_time`)을 캐시 키로 쓰면 새 발표가 나올 때 자동으로 무효화된다 ([외부연결_계획.md](외부연결_계획.md) §1) |
| 수치 | 모델은 tool 결과에 있는 수치만 옮긴다. 계산·추정·단위 환산 금지 |

`ErrorCode`: `TOOL_NOT_ALLOWED` · `INVALID_ARGS` · `NOT_FOUND` · `UPSTREAM_ERROR` · `CONSENT_REQUIRED` · `SAFETY_UNAVAILABLE` · `EVIDENCE_REQUIRED` · `NO_RECORDS`

> 이 여덟 개가 `common/tool_runtime.py` 에 있다. `food/result.py` · `memory/result.py` 는 여기서 가져다 쓴다. Memory 의 되묻기 코드 6개(`TARGET_REQUIRED` · `TARGET_NOT_FOUND` · `AMBIGUOUS_TARGET` · `UNKNOWN_EVENT` · `DATE_UNPARSEABLE` · `OUT_OF_SCOPE`)는 대상마다 모델의 다음 행동이 달라 `class ErrorCode(CommonErrorCode)` 로 얹었다.

## 2. `life_stage` — 코드

```python
def life_stage(birth_date: date, today: date) -> LifeStage
# LifeStage(months: int, stage: Stage, big: Literal["infant","toddler"])
```

| stage | 월령 | big |
| --- | --- | --- |
| `infant_milk` | 0–3 | 영아기 |
| `infant_weaning` | 4–11 | 영아기 |
| `toddler` | 12–35 | 유아기 |
| `preschool` | 36+ | 유아기 |

- **공통이 강제하는 것은 월령 계산까지다.** `life_stage()`가 `months` · `stage`를 돌려주지만, 그 값으로 tool을 어떻게 가를지는 **도메인마다 다르다** — Food는 `stage`를 배타적 범주로 쓰고, Growth는 tool별 `min_month` 눈금을 쓰고, Health는 tool을 여닫지 않고 계산 방식만 바꾼다. `stage`는 참고값이지 공통 게이팅 축이 아니다.
- 경계값은 `reference/age_gates.yaml` 한 곳
- **`age_months`는 민법 기준 달력 계산이다.** `(today - birth).days // 30` 금지 — 6년이면 두 달 앞서 열린다. 구현은 `app/rules/age.py` 하나
- **`FeedingStage`는 별도 enum이 아니다.** Food 문서가 쓰던 `milk` · `weaning` · `toddler_meal`은 이 표의 값을 부르는 다른 이름이었다. 축이 하나인데 이름이 둘이면 `stage_min` 같은 컬럼에 두 집합이 섞인다 — **`LifeStage.stage` 네 값으로 통일한다.**

| 옛 이름 | 정본 |
| --- | --- |
| `milk` | `infant_milk` |
| `weaning` | `infant_weaning` |
| `toddler_meal` | `toddler` (12–35) · `preschool` (36+) |

Food 게이팅에서 `toddler`와 `preschool`은 tool 묶음이 같고 **섭취기준 연령군만 갈린다**(`1-2y` / `3-5y`).

## 3. `Gate` — registry 입력

```python
Gate(stage: LifeStage,
     consent_child_health: bool,                              # consent(child_health) 최신 행이 granted
     safety_ok: bool,                                         # health_safety 조회 성공 여부
     allergy_states: tuple[SafetyState, ...],                 # kind='allergy' 행들의 state (F-4)
     growth_log_count: int,
     has_location: bool, outdoor_ok: bool,
     data: DataReady)

DataReady(daycare_meal: bool, notice: bool, book_api: bool)
```

- **성별은 게이팅 축이 아니다.** `has_gender`는 삭제됐다(2026-09-22 성장 판정 제거). 추천·계산 어디에도 쓰지 않는다
- `stage`가 월령을 들고 있으므로 `age_months`를 따로 넘기지 않는다
- `data`는 "그 아이에게 그 데이터가 있는가"다 — 급식 행 0건이면 `lookup_daycare_menu`가, `notice` 0건이면 `lookup_notice`가, 도서 API 장애면 도서 tool이 빠진다
- **Gating은 각 Agent의 registry가 한다.** Supervisor는 tool을 모른다
- 빈 튜플이면 **모델을 부르지 않고** 코드 readout으로 끝낸다(`model_calls=0`). 연령 때문에 닫혔으면 언제 열리는지 함께 알린다

## 4. `rank_evidence` — 코드 (Food · Activity · Growth 공통)

```python
def rank_evidence(affinities, observations, *, today, strength_threshold=0.5)
    -> tuple[RankedEvidence, ...]   # 빈 튜플이면 호출부가 kind="general"
```

| 티어 | 조건 |
| --- | --- |
| 1 | `confirmed` · polarity ∈ {+1, −1} · 비archived |
| 2 | `candidate` · polarity ∈ {+1, −1} · strength ≥ 임계 |
| 3 | 최근 14일 관찰 (polarity 무관) |
| 역질의 | `candidate` + polarity NULL → `needs_observation` 후보로 반환 (1개만 사용) |
| 감쇠 | **여기서 하지 않는다.** Curator 가 `strength` 를 내리거나 `archived` 로 바꿔 이미 반영한다 |

> 🟡 **`profile_affinity` 는 Agent 가 만들지 않는다.** 관찰을 승격해 이 행을 만드는 것도, 오래된 관심을 내리는 것도 Curator(백엔드 배치)의 일이다. Agent 는 이미 만들어진 행을 **읽기만** 한다 — 유예일을 Agent 가 다시 세면 값이 두 벌이 되고 한쪽만 갱신된다. NF-08(6개월)도 Curator 가 `archived` 로 바꾸는 것으로 지켜진다.
>
> 🔴 **기피(−1)는 제외 필터가 아니라 근거다.** "브로콜리를 싫어해서"도 추천을 고르는 이유다. `rank_evidence`는 제거 목록을 반환하지 않는다.
> 후보에서 지우는 것은 **안전뿐** — 알레르기(`health_safety`) · 연령 금지식품 · `hazard_term`. 이건 `rank_evidence`가 아니라 Agent별 안전 필터의 일이다.
> 근거에서 빠지는 건 `polarity IS NULL`인 candidate 하나뿐이고, 그건 역질의로 되돌린다.

## 5. 출력 검증 — 모든 `propose_*`

코드는 [`common/suggestion.py`](../../../apps/api/app/agents/common/suggestion.py) 다.
후보 하나를 보는 `build()` 와 묶음을 보는 `check_count()` 로 나뉘고, 실패는 둘 다 `SuggestionRejected` 다.
모델에게는 사유 문장과 함께 돌려준다. 단 `check_count()` 의 거절 중 하나는 모델에게 돌려주지 않는다 — 안전 필터로 모자라졌으면 사유 없이 재호출 1회를 한다. 더 채울 길이 없는데 모자란 것(0개 포함)은 거절이 아니라 통과다 — 개수 안내와 함께 낸다 (5-2).

### 5-1. 순서

순서가 결과를 바꾼다. 안전에 걸린 후보가 뒤 검사를 타면 "기피를 말하지 않았다" 같은 엉뚱한 사유가 나간다.

| # | 무엇 | 대상 | 통과 못 하면 |
| --- | --- | --- | --- |
| 1 | 안전 필터 (알레르기 · 연령 금지식품 · `hazard_term`) | 후보 풀 | 그 후보를 **풀에서 뺀다**. 거절이 아니라 제거다 |
| 2 | 금지 표현 필터 (공통 목록 [`app/rules/evaluative.py`](../../../apps/api/app/rules/evaluative.py)) | 후보 하나 | 그 후보 **삭제** |
| 3 | 도메인별 출력 검증 | 후보 하나 | 그 후보 **거절** (5-5) |
| 4 | `build()` — 근거와 문구 | 후보 하나 | 그 후보 **거절** |
| 5 | `check_count()` — 개수 | 묶음 | **묶음 거절** → 재호출 판단. 더 채울 길이 없으면 0~2개도 통과하고 개수 안내 (5-2) |

### 5-2. 개수 — 최대 3개 (C-8 닫힘, 2026-10-06)

**요청 1건당 최대 3개(`MAX_SUGGESTIONS`)다.** 4개 이상이면 거절한다. 채울 수 있으면 3개를 다 채운다 — "최대" 는 모델이 덜 내도 된다는 뜻이 아니다. 덜 내도 되는 것은 더 채울 길이 없을 때뿐이다.

- 안전 필터(1번) 뒤에 3개 미만이 되면 **그 Agent 만 모델을 1회 재호출**한다. 걸러진 항목을 제외 목록으로 넣되 **사유는 넣지 않는다** — 알레르기 목록을 프롬프트로 되돌려 보내는 셈이 된다.
- 재호출은 1회로 끝난다. 그래도 못 채우거나 후보 풀이 처음부터 3개보다 적으면 **남은 만큼(1~2개)만** 낸다. 3개를 채우려고 안전 조건을 늦추거나 풀 밖 후보를 만들지 않는다. `check_count(exhausted=True)` 가 이 경우를 통과시킨다.
- **재호출 결과는 빈자리만 채운다.** 첫 호출에서 통과한 것을 먼저 두고, 재호출에서 통과한 것으로 3개가 될 때까지만 채운다. 첫 호출 것과 같은 후보는 건너뛰고 남는 것은 버린다 — 둘을 그대로 합치면 4개가 넘을 수 있고, 그러면 `check_count` 가 넘친다고 거절한다. 무엇을 같은 후보로 볼지는 Agent 가 정한다 — Food 는 `menu_key`, Activity 는 `content`(최근 중복 검사 `is_recent_duplicate` 가 보는 칸)로 본다 (#245 리뷰).
- **0개는 실패가 아니다.** 더 채울 길이 없는데 0개면 `check_count(exhausted=True)` 가 예외 없이 통과시키고, Agent 는 추천 없이 안내만 내고 끝낸다. 호출부는 0개를 따로 거르지 않고 `check_count` → `count_notice(len(drafts))` 순서로 부르면 0~3개가 다 처리된다 — 0개에 예외를 던지면 호출부가 놓쳤을 때 pipeline 이 그 Agent 를 실패로 세고 화면에 다시 시도가 뜬다 (#245 리뷰). 채울 수 있는데 0개(`exhausted=False`)는 1~2개처럼 거절한다. `status` 는 `completed` 다 — 실패가 아니라 안전 조건을 통과한 후보가 없다는 결과라, 화면은 다시 시도를 띄우지 않는다. 근거가 없어 일반 추천으로 가는 경우(5-3)와 다르다 — 그쪽은 추천이 나간다.
- 재호출 사유는 **안전 필터뿐이다.** 기피(`polarity = −1`)는 필터가 아니라 근거라 재호출 사유가 되지 않는다.
- 이때만 그 Agent 의 진입 수가 2가 된다 ([Agent_공통규약.md](Agent_공통규약.md) §7).
- `note` 가 비어 후보가 빠지면 개수가 3개 아래로 내려간다. 재호출 사유는 안전 필터뿐이라 지금은 묶음이 거절된다. 금지 표현 필터(5-1 의 2번)도 같은 자리에 있어 새로 생긴 문제는 아니다. 재호출 사유를 늘릴지는 아직 안 정했다 (2026-09-25).
- 다 채우지 못했으면 안내 한 줄이 같이 나간다. 문구는 코드 상수(`app/agents/common/suggestion.readout.yaml`)이고 `count_notice()` 가 나가는 개수로 고른다 — 아래 표.
- 화면이 받는 모양 — 0개여도 그 Agent 묶음은 오고 추천 목록만 빈다. 안내는 그 묶음에 실린다. 안내를 결과의 어느 칸에 실어 보낼지 · 응답 칸 이름 · Swagger 의 "0~3개" 설명은 추천 엔드포인트 이슈 #249 에서 정한다 ([공통_구현_계획.md](공통_구현_계획.md) K-12).

| 나가는 개수 | 안내 key | 문구 |
| --- | --- | --- |
| 3 | – | 없음 |
| 1~2 | `suggestion.partial` | 조건에 맞는 추천을 {count}개 준비했어요. |
| 0 | `suggestion.empty` | 조건에 맞는 추천이 없어요. |

0개 문구가 따로 있는 Agent 는 `count_notice(0, empty=...)` 로 그 문구를 쓴다 — Food 는 `pool.empty`("조건에 맞는 메뉴가 없어요.").

### 5-3. 근거 — `kind` 는 코드가 정한다

모델이 `kind` 를 고르지 않는다. `build()` 가 **아이 기록 근거의 행 수**로 정한다.

- 아이 기록(`observation_*` · `profile_affinity` · `child_growth_log` · `notice` · `daycare_meal`)이 1행 이상이면 `kind="personalized"`. `reason` 이 비어 있으면 거절한다.
- 0행이면 `kind="general"`. `reason` 을 **코드 템플릿(`general_reason`)으로 덮어쓴다.** 모델이 쓴 개인화 문장이 그대로 나가면 근거 없이 "우리 아이 맞춤"인 척하게 된다. 템플릿이 없으면 거절한다. 문서 행(`*_doc`)은 세지 않는다 — 있든 없든 아이 기록이 0행이면 general 이다.
- **general 은 거절 사유가 아니다.** 추천은 그대로 나가고 화면이 또래 기준임을 말한다 (루트 CLAUDE.md §2). `general` 인데 인용이 0행인 것은 문서에서도 근거를 못 찾았다는 뜻이라 품질 지표로 본다 (2026-09-25).
- **인용마다 `note` 가 있어야 한다.** 그 행에서 무엇을 근거로 봤는지를 Agent 가 한 줄로 쓴다. 비면 그 후보를 거절한다. 거절은 후보 단위라 묶음은 그대로 간다.
- 후보마다 `evidence_ids` ⊂ `rank_evidence` 상위 N(=10) — 위반은 `EVIDENCE_REQUIRED`. **아직 코드에 없다**(이번 run 에서 조회한 id 인지 대조하는 자리). 인용된 id 는 `suggestion_evidence` 행이 된다 (`source_kind` + `source_id`).

### 5-4. 문구 — 기피를 인용했으면 말해야 한다

`polarity = −1` 근거를 인용한 후보는 `reason` 에 **무엇을 피했는지**가 있어야 한다. 없으면 거절한다.
인용만 하고 말하지 않으면 보호자 화면에서는 근거 없는 추천과 구별이 안 된다.

지금 코드는 **기피 근거의 라벨 문자열이 `reason` 안에 있는지**만 본다. 문장 품질은 못 본다 —
라벨조차 안 나오면 확실히 말하지 않은 것이라는 하한만 거는 것이다.

### 5-5. 도메인별 확장 — 여기에 더하지 않는다

**후보를 지우는 공통 규칙은 안전뿐이다.** 도메인마다 더 지워야 할 것이 있으면
이 절이 아니라 **그 Agent 문서의 출력 검증**에 둔다. 이 절은 네 Agent 가 전부 통과하는 자리라
한 도메인의 사정을 넣으면 다른 도메인이 막힌다.

- Food 는 영양 분석이 부족을 가리키면 기피 식품군을 **일부러** 낸다. "기피 대상은 거절"을 여기 두면 이 경로가 막힌다.
- Activity 는 반대다. 꼭 해야 하는 놀이가 없어서 기피 대상을 다시 낼 이유가 없다 — 기피 근거와 `merge_key` 가 같은 후보를 거절하는 규칙을 Activity 쪽에 둔다 ([README.md](../README.md) §2).

둘 다 **제거 필터가 아니라 출력 검증**이다. 후보 풀은 그대로 두고 나가는 것만 막는다.
