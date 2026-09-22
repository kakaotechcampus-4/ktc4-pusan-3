# Tool 공통 규약

> Food · Growth · Health tool 명세가 공유하는 것만. 위치: `app/agents/common/`
>
> **이 문서가 `LifeStage` · `Gate` · `rank_evidence` 세 가지의 정본이다.** 다른 문서는 여기를 가리키고 값을 복제하지 않는다.
> **2026-09-22 갱신** — 기피(−1)를 제외 필터에서 근거로 · 성별 게이팅 축 삭제 · `Gate` 필드 확정 · `FeedingStage`를 `LifeStage.stage`로 통일 · 출력 개수 3

## 1. 실행 규약

| 항목 | 규칙 |
| --- | --- |
| 공개 | **모델 tool** = 모델이 호출 · **코드 tool** = registry가 호출, 모델에게 스펙 비노출 |
| 허용 목록 | `execute_tool(name, args, allowed=tools_for(task_type, gate))` — 목록 밖이면 `TOOL_NOT_ALLOWED` |
| 반환 | `ToolResult(ok, data, error: ErrorCode \| None, source_refs)` |
| 빈 결과 | 성공 + 빈 목록. **조회 실패를 빈 목록으로 숨기지 않는다** (`UPSTREAM_ERROR`) |
| 외부 API | 런타임 직접 호출 금지 → **캐시 테이블 조회**. 캐시 미스만 API 호출 + 저장 (예외: 병원·응급 실시간) |
| 수치 | 모델은 tool 결과에 있는 수치만 옮긴다. 계산·추정·단위 환산 금지 |

`ErrorCode`: `TOOL_NOT_ALLOWED` · `INVALID_ARGS` · `NOT_FOUND` · `UPSTREAM_ERROR` · `CONSENT_REQUIRED` · `SAFETY_UNAVAILABLE` · `EVIDENCE_REQUIRED` · `NO_RECORDS`

> 이 여덟 개가 `common/tool_runtime.py` 에 있다. `food/result.py` · `memory/result.py` 는 여기서 가져다 쓴다. Memory 의 되묻기 코드 6개(`TARGET_REQUIRED` · `TARGET_NOT_FOUND` · `AMBIGUOUS_TARGET` · `UNKNOWN_EVENT` · `DATE_UNPARSEABLE` · `OUT_OF_SCOPE`)는 대상마다 모델의 다음 행동이 달라 `class ErrorCode(CommonErrorCode)` 로 얹었다.

## 2. `life_stage` — 코드

```python
def life_stage(birth_date: date, today: date, gestational_weeks: int | None = None) -> LifeStage
# LifeStage(months: int, corrected_months: int, stage: Stage, big: Literal["infant","toddler"])
```

| stage | 월령 | big |
| --- | --- | --- |
| `infant_milk` | 0–3 | 영아기 |
| `infant_weaning` | 4–11 | 영아기 |
| `toddler` | 12–35 | 유아기 |
| `preschool` | 36+ | 유아기 |

- **공통이 강제하는 것은 월령 계산까지다.** `life_stage()`가 `months` · `corrected_months` · `stage`를 돌려주지만, 그 값으로 tool을 어떻게 가를지는 **도메인마다 다르다** — Food는 `stage`를 배타적 범주로 쓰고, Growth는 tool별 `min_month` 눈금을 쓰고, Health는 tool을 여닫지 않고 계산 방식만 바꾼다. `stage`는 참고값이지 공통 게이팅 축이 아니다.
- 조산(`gestational_weeks < 37`)은 24개월까지 `corrected_months`로 판정한다. 계산은 **월 단위 반올림**이다 — `corrected_months = months - round((40 - gestational_weeks) / 4.35)`. 34주면 1개월 보정. 주수를 일 단위로 환산하지 않는 것은 보호자가 기억하는 값이 대개 주 단위라서다
- 경계값은 `config/life_stage.yaml` 한 곳
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
     allergy_status: Literal["none", "has", "unknown"],       # child.allergy_status (F-4)
     growth_log_count: int,
     has_location: bool, outdoor_ok: bool,
     data: DataReady)

DataReady(daycare_meal: bool, notice: bool, book_api: bool)
```

- **성별은 게이팅 축이 아니다.** `has_gender`는 삭제됐다(2026-09-22 성장 판정 제거). 추천·계산 어디에도 쓰지 않는다
- `stage`가 월령을 들고 있으므로 `age_months` · `corrected_months`를 따로 넘기지 않는다
- `data`는 "그 아이에게 그 데이터가 있는가"다 — 급식 행 0건이면 `lookup_daycare_menu`가, `notice` 0건이면 `lookup_notice`가, 도서 API 장애면 도서 tool이 빠진다
- **Gating은 각 Agent의 registry가 한다.** Supervisor는 tool을 모른다
- 빈 튜플이면 **모델을 부르지 않고** 코드 readout으로 끝낸다(`model_calls=0`). 연령 때문에 닫혔으면 언제 열리는지 함께 알린다

## 4. `rank_evidence` — 코드 (Food 식단 · Growth)

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

- **개수는 정확히 3개.** 3개가 아니면 거절한다. 안전 필터 뒤에 3개 미만이 되면 그 Agent만 모델을 1회 재호출하고, 걸러진 항목을 제외 목록으로 넣는다(**사유는 넣지 않는다**). 재호출 후에도 부족하면 **남은 만큼만** 낸다 — 개수 규칙의 유일한 예외다
- 후보마다 `evidence_ids` ⊂ `rank_evidence` 상위 N(=10) — 위반 시 `EVIDENCE_REQUIRED`. 인용된 id는 `suggestion_evidence` 행이 된다 (`memory_kind` + `memory_id`)
- **기피 근거를 인용했으면 `reason`에 무엇을 피했는지가 있어야 한다.** 없으면 해당 후보 거절 — 인용만 하고 말하지 않으면 보호자에게는 근거 없는 추천과 같다
- 근거 0건 → `kind="general"`, `reason`은 단계별 코드 템플릿으로 **덮어쓴다**
- 금지 표현 필터(Agent별 목록) 통과 못 하면 해당 후보 삭제
