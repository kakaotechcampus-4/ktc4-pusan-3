# 위험 물질 규칙 — 목록과 판정 분리 v1

> 2026-11-05 · 이슈 #731 · 커리어 멘토 리뷰 반영 · 2026-11-07 검사 자리(`Guardrail`) 추가
> 아이가 먹으면 안 되는 것을 먹었다는 말을 코드로 거르는 사전검사(#626)의 **목록**과 **판정 방법**을 나눈 기준, 검사 방법을 바꿔 끼우는 자리, 목록을 코드 밖으로 옮길 때 같이 정할 것을 적는다.

## 1. 어디에 무엇이 있나

| 무엇 | 어디 |
| --- | --- |
| 목록 — 위험 물질 이름 · 섭취 동사 · 예외 표기(`aliases` · `guards`) · 저장 게이트가 보는 이름 묶음(`FOOD_NAME_TERMS` · `PLAY_NAME_TERMS`) | `apps/api/app/rules/hazard_rules.py` |
| 판정 — 마디 나누기 · 부정 · 금지 · 어른 주어 · 어른을 꾸미는 동사 · 이름과 동사 사이 거리 | `apps/api/app/rules/hazard.py` |
| 검사 자리 — `Guardrail`(Protocol) · `GuardResult` · `StaticRuleGuardrail` | `apps/api/app/agents/common/guardrail.py` (§4) |
| 검사 방법 교체 지점 | Memory `AgentContext.guardrail` — 사전검사와 저장 게이트가 같은 인스턴스를 쓴다 |
| 목록 교체 지점 | `StaticRuleGuardrail(rules=...)` → `find_hazards(text, *, rules=...)` — 목록 묶음(`HazardRules`)을 넘긴다 |

호출하는 쪽(`agents/pipeline.py` 의 사전검사 · `agents/memory/agent.py` 의 저장 게이트)은 규칙 함수 대신 `guardrail.check` 를 부른다. 걸리는 범위는 바뀌지 않았다 — 규칙 테스트와 `StaticRuleGuardrail` 테스트가 같은 케이스 표(`tests/unit/rules/hazard_cases.py`)를 돈다.

`HazardRules` 는 아래 묶음을 **만들 때** 거부한다. 그대로 쓰면 검사 하나가 조용히 빠지기 때문이다.

- 이름 묶음이나 동사 사전이 비었다 · 별칭이 없거나 공백 · 문장부호뿐이라 어느 글에도 걸리지 않는다
- 모르는 위험 종류 키가 있다 (오타)
- 다섯 위험 종류(`HAZARD_KINDS`) 중 빠진 것이 있다

한 종류를 어느 묶음(이름만 / 섭취 동사 필요 / 삼키다 필요)에 넣을지는 검사하지 않는다. 외부 공급원을 도입할 때 같이 정한다.

## 2. 나눈 기준

멘토 기준을 그대로 따랐다. 한 줄로는 이렇다.

> 값이 바뀔 때 처리 로직도 함께 바뀌어야 하면 코드, 기존 로직에 넣어주는 값만 바뀌면 목록.

| 판단 기준 | 코드에서 관리하기 좋은 경우 | DB · 외부 설정에서 관리하기 좋은 경우 |
| --- | --- | --- |
| 얼마나 자주 바뀌나 | 거의 바뀌지 않는다 | 운영 중 자주 바뀔 수 있다 |
| 누가 바꾸나 | 개발자 | 운영자 · 기획자도 |
| 값이 바뀌면 코드도 바뀌나 | 처리 로직도 함께 바뀐다 | 기존 로직에 전달하는 값만 바뀐다 |
| 얼마나 빨리 반영해야 하나 | 다음 배포까지 기다릴 수 있다 | 배포를 기다리지 않고 반영해야 한다 |
| 대상마다 다른 값이 필요한가 | 모든 환경 · 대상이 같다 | 환경 · 사용자 · 실험군별로 다를 수 있다 |
| 변경 이력 | Git 으로 충분하다 | 별도 운영 변경 이력이 필요하다 |

이 기준으로 보면 위험 물질 이름 · 동사 활용형 · 예외 표기는 값이 늘어나도 판정이 그대로라서 목록이다.

**어른 · 아이를 가리키는 낱말(`_ADULTS` · `_CHILDREN` 등)은 `hazard.py` 에 남겼다.** 낱말 목록처럼 보이지만 `_adult_subject` · `_modifies_adult` 가 낱말의 꼴(조사가 붙는 방식, 낱말 앞부분 일치)에 기대 동작한다. 낱말을 늘리면 판정 방법도 같이 봐야 해서 "값만 바뀌는 것"이 아니라고 봤다. 마디 끝 표기(`_CLAUSE_ENDINGS`) · 부정 표기(`_NEGATIONS` · `_NOT_DONE`) · 거리(`INTAKE_WORDS`)도 같은 이유로 남았다.

하나 더, 어른 낱말 목록은 **검사를 느슨하게 하는 쪽**이다(어른이 주어면 걸지 않는다). 위험 물질 이름처럼 검사를 조이는 목록은 빨리 반영할 이유가 크지만, 풀어 주는 예외 목록은 잘못 넣으면 놓침으로 이어진다. 그래서 `외삼촌` 같은 호칭 하나를 더할 때도 회귀 테스트와 같은 PR 로 바꾼다 (#731 리뷰).

## 3. 지금은 코드 상수로 둔다

- 운영 중에 목록을 바꿔야 한다는 요구가 아직 확인되지 않았다.
- 사용자 · 월령별로 다른 목록이 필요하지 않다.
- 변경 이력은 Git 으로 충분하다.
- **목록을 파일로 나누는 것과 배포 없이 갱신하는 것은 다른 일이다.** YAML 파일로만 빼도 배포 없이 바뀌지 않는다. 지금 한 일은 앞의 것이다.

그래서 DB · Redis · 원격 설정은 만들지 않았다. 새 표현이 나오면 `hazard_rules.py` 의 값만 고치고 케이스 표(`tests/unit/rules/hazard_cases.py`)에 반드시 걸 문장과 통과할 문장을 더한다.

목록을 어디에 두느냐와 **검사 방법을 바꾸느냐는 다른 일**이다. `rules=` 는 같은 판정 방법에 목록만 바꿔 끼운다. 모더레이션 API 나 여러 검사의 조합처럼 방법 자체를 바꾸려면 검사하는 역할이 따로 있어야 해서, 그 자리(`Guardrail`)는 지금 열어 뒀다 (§4).

## 4. 검사 자리 — `Guardrail`

```python
GuardContext = Literal["input", "food_name", "play_name"]

class Guardrail(Protocol):
    async def check(self, text: str, context: GuardContext) -> GuardResult: ...
```

| `context` | 무엇을 보나 | 부르는 곳 |
| --- | --- | --- |
| `input` | 보호자가 보낸 한 줄 전체 | 안전 사전검사 (Supervisor 보다 먼저) |
| `food_name` | 음식 · 생활 관찰의 대상 이름 칸. 술 · 담배 · 화학제품 · 마약류 | Memory 저장 게이트 |
| `play_name` | 놀이 · 배움 관찰의 대상 이름 칸. 마약류만 | Memory 저장 게이트 |

- **구현은 `StaticRuleGuardrail` 하나다.** 지금 규칙(`find_hazards` · `hazard_names`)을 감싼다. DB · 모더레이션 구현은 만들지 않았다.
- **인스턴스는 run 마다 하나다.** Memory `AgentContext.guardrail` 에 두고 사전검사도 그것을 쓴다. 따로 받으면 한쪽만 바뀌어 두 곳이 다른 기준으로 거를 수 있다. 만드는 쪽이 안 넘기면 `StaticRuleGuardrail()` 이다.
- **어느 칸을 어느 `context` 로 볼지는 저장 게이트가 정한다** (`memory/agent.py` 의 `_HAZARD_FIELDS`). 관찰 칸 이름은 Memory 의 스키마 지식이라 규칙 쪽에 두지 않는다. 칸끼리 이어 붙이지 않고 하나씩 본다.
- **`GuardResult` 는 걸린 종류(`kinds`)만 싣는다.** 걸린 낱말은 원문 조각이라 로그에 찍히기 쉬워 싣지 않는다 (루트 §2). 화면 문구도 싣지 않는다 — 걸렸을 때 문구는 pipeline 이(`HAZARD_GUIDANCE`), 검사를 못 했을 때 문구는 실패 종류가 정한다.
- **fail-closed.** 검사를 하지 못하면 빈 결과("걸린 것 없음")를 돌려주지 않고 `GuardrailUnavailableError` 를 올린다. 부르는 쪽은 이 예외를 받으면 그 run 을 진행하지 않는다. pipeline 이 잡아 `Failed("guard_unavailable")` 로 끝낸다 (#768, [run-failure-v1](../api/run-failure-v1.md)) — Supervisor · Memory 를 부르지 않고 Memory commit 전이라 저장도 남지 않는다.
  - 실패를 `GuardResult` 의 칸으로 두지 않은 것은 부르는 곳마다 "실패면 막는다" 를 챙기지 않아도 되게 하려는 것이다. 한 곳만 빠뜨려도 검사가 조용히 빠진다.
  - 실패 종류는 한 곳(`common/errors.py` 의 `AgentFailure` 계통, #768)으로 모았고 이 예외도 그 계통이다. 화면 문구는 실패 종류에서 정한다(`common/failure.py`). `Guardrail` · `GuardResult` 와 두 호출 지점은 바뀌지 않았다.
- **`check` 는 async 다.** 다음 구현이 외부 API 면 await 가 필요하고, 두 호출 지점이 이미 async 함수 안이다.
- **`app/rules/` 가 아니라 `app/agents/common/` 에 뒀다.** 규칙 레이어는 표준 라이브러리만 쓰고, 다음 구현의 실패 예외는 Agent 쪽 실패 종류와 같은 계통이어야 한다 (`SafetyLookupError` 가 `common/errors.py` 에 있는 것과 같다).
- 테스트: 기존 케이스 전부를 `StaticRuleGuardrail` 로 돌리고(`tests/unit/agents/common/test_guardrail.py`), 바꿔 끼운 가짜가 두 곳에서 막는지 · 검사를 못 하면 run 이 멈추는지를 본다(`tests/unit/agents/supervisor/test_pipeline.py` 의 "바꿔 끼운 Guardrail").

## 5. 목록을 코드 밖으로 옮길 때 같이 정할 것

운영 중 갱신 요구가 생기면 `StaticRuleGuardrail(rules=...)` 에 다른 곳에서 읽은 `HazardRules` 를 넘기거나, 그 목록을 읽는 `Guardrail` 구현을 따로 두는 것으로 시작할 수 있다. 그때 아래를 먼저 정한다.

- **조회 실패.** 읽기에 실패해도 검사를 건너뛰지 않는다. 마지막으로 검증된 목록이나 `DEFAULT_RULES` 로 계속 검사한다. 빈 목록으로 대체하는 길은 열지 않는다 (`HazardRules` 가 막는다).
- **검사 자체가 불가능할 때.** 정해졌다 — 요청을 막는다 (fail-closed, §4).
- **이름 칸 묶음.** `FOOD_NAME_TERMS` · `PLAY_NAME_TERMS` 는 아직 `HazardRules` 밖이라 `rules=` 로 바뀌지 않는다. 목록을 밖으로 옮길 때 같이 넣는다.
- **변경 이력 · 롤백.** 누가 · 언제 · 왜 바꿨는지 남기고 이전 버전으로 되돌릴 수 있어야 한다. 잘못 넣은 규칙 하나가 정상 입력을 많이 막을 수 있다.
- **캐시 · 갱신.** 요청마다 읽지 않도록 캐시를 두면 갱신 주기와 변경 알림을 같이 정한다 (멘토 예: 로컬 30초 → 원격 60초 → 저장소).
- **회귀 테스트.** 목록이 어디에 있든 "반드시 걸 문장 / 통과할 문장" 테스트는 유지한다.

## 6. 다른 사전과의 관계

`apps/api/reference/hazard_terms.yaml` 은 Activity · Growth 의 **월령별 놀이 · 활동 안전 필터** 사전이다. 이 문서의 대상인 위험 물질 섭취 사전검사와 목적이 달라 합치지 않았다.
