# run 실패 이유와 화면 문구 (#768)

SSE `failed` 는 "저장 없음" 이 보장된 끝이다. 화면이 부모에게 할 말이 이유마다 달라서, 서버가 이유(`reason`)와 문구(`message`)를 같이 보낸다. SSE 흐름이라 Swagger 가 담지 못해 여기에 둔다 (루트 §5).

```
failed { reason, raw_text, message }
```

- `message` — 코드 카탈로그([`failure.readout.yaml`](../../apps/api/app/agents/common/failure.readout.yaml))의 문구. 화면이 그대로 그리고 `reason` 으로 문구를 만들지 않는다 (`guidance.message` 와 같은 규칙)
- 화면의 둘째 줄("아무것도 저장하지 않았어요")과 버튼(다시 시도 · 고쳐 쓰기)은 그대로다. 그래서 문구는 기록 여부를 다시 말하지 않는다

## 이유 8가지

| `reason` | 언제 | 문구 |
| --- | --- | --- |
| `timeout` | 러너 60초 · 도메인 20초 마감 · LLM 응답 시간 초과 | 시간 안에 답을 만들지 못했어요. 잠시 뒤 다시 보내 주세요. |
| `llm_unavailable` | LLM 연결 실패 · 그 밖의 LLM 오류(5xx · 빈 응답) · 도메인 Agent 가 결과 없이 돌아옴 | 지금은 답을 만들 수 없어요. 잠시 뒤 다시 보내 주세요. |
| `llm_rate_limited` | rate limit | 지금 요청이 많아요. 잠시 뒤 다시 보내 주세요. |
| `llm_misconfigured` | 인증 · 설정 · 요청 거절 | 서비스 쪽 문제로 지금은 처리할 수 없어요. 다시 보내도 같을 수 있어요. |
| `guard_unavailable` | 위험 물질 검사를 하지 못함 | 안전 확인을 하지 못했어요. 잠시 뒤 다시 보내 주세요. |
| `lookup_unavailable` | 조회 실패가 Agent 밖으로 샌 경우 (지금 경로 없음) | 필요한 정보를 불러오지 못했어요. 잠시 뒤 다시 보내 주세요. |
| `unparsable` | Memory 반복 상한 · Health 답을 이어 가지 못함 · 한 일 없음 | 말씀을 정리하지 못했어요. 조금 바꿔서 다시 적어 주세요. |
| `internal_error` | 그 밖 (계통 밖 예외) | 처리하다 문제가 생겼어요. 다시 보내 주세요. |

모르는 `reason` 은 `internal_error` 문구가 나간다 — 문구 없는 `failed` 는 없다. 웹은 `reason` 을 열린 문자열로 읽는다.

## 이유를 정하는 곳

예외 종류가 이유를 정한다 — `app/agents/common/errors.py` 의 `AgentFailure` 계통, 클래스마다 `reason`. `failure_reason(exc)` 가 한 곳에서 읽고, 계통 밖 예외는 `internal_error`.

| 예외 | 이유 |
| --- | --- |
| `LLMTimeoutError` (openai `APITimeoutError`) | `timeout` |
| `LLMUnavailableError` · `LLMError` | `llm_unavailable` |
| `LLMRateLimitError` | `llm_rate_limited` |
| `LLMAuthError` · `LLMConfigError` · `LLMBadRequestError` | `llm_misconfigured` |
| `GuardrailUnavailableError` | `guard_unavailable` |
| `LookupUnavailable` (`SafetyLookupError` · `MenuSourceError` · `SafetyRulesUnavailable`) | `lookup_unavailable` — Agent 안에서 닫혀 run 실패로는 안 나온다. 로그 분류용 |

- 🚨 `LLMTimeoutError` 는 내장 `TimeoutError` 를 상속하지 않는다. 러너의 `except TimeoutError` 가 60초 안전망으로, 도메인 단계가 20초 마감으로 오인한다
- 기본 설정(`LLM_TIMEOUT_S=60` · 재시도 2회)에서는 러너 60초 · 도메인 20초 마감이 늘 먼저라, LLM 시간 초과만으로 run 이 실패하는 일은 드물다. 그래도 `LLMTimeoutError` 는 로그에서 갈리고, 화면 이유는 `timeout` 하나다 — 부모가 할 일이 같다

### 계통 밖에 둔 것

`core/weather_raw.UpstreamUnavailable` · `core/book_raw.UpstreamUnavailable`(Health 별칭 `FacilityLookupError` 포함)는 계통에 넣지 않았다. core · integrations 는 agents 를 import 하지 못한다 (`apps/api/CLAUDE.md` 레이어 경계). 전부 Agent 안에서 닫히고, 새더라도 `internal_error` 다.

## 어디서 나는가

| 자리 | 이유 |
| --- | --- |
| 위험 물질 사전검사 (`handle_input` 맨 앞) | `guard_unavailable` — Supervisor · Memory 를 부르지 않는다 (fail-closed). 이어받기 · Health 답도 같은 자리를 지난다. 🚨 응급 신호가 있으면 `failed` 대신 응급 안내 + `done` 으로 끝낸다 — 응급 판정은 사전 · 규칙이라 검사기 없이도 되고, `failed` 면 화면이 실패 카드만 그려 119 안내가 사라진다. 저장은 그래도 없다 |
| Memory 호출 (저장 게이트의 검사 불가 포함) | 예외의 이유. commit 전이라 저장 없음 |
| 이어받기의 Memory | 같다 |
| 도메인 Agent 전부 실패 + 저장·결과 없음 | 아래 규칙 |
| 러너 60초 안전망 | `timeout` (commit 뒤면 `done`) |
| 러너가 잡은 그 밖 예외 | `internal_error` (commit 뒤면 `done`) |

### 도메인 Agent 가 전부 실패했을 때

task 마다 이유를 모은다: `TimeoutError` → `timeout`, `AgentFailure` → `exc.reason`, 추천 저장 실패 → `internal_error`, 그 밖 예외 → `internal_error`. 결과 없이 돌아온 task(`status="failed"`)는 부른 쪽이 정한다 — 모델을 부르는 Agent 는 모델이 출력하지 못한 것이라 `llm_unavailable`(공통 tool loop 의 "화면은 다시 시도"), 모델을 안 부르는 Health 답은 `unparsable`.

이유가 여럿이면 task 순서가 아니라 아래 순서로 앞의 것을 고른다. 부모가 할 일이 가장 분명한 것이 앞이다 — "다시 보내도 같다" 가 "잠시 뒤" 에 가려지면 안 되고, 서버 쪽 실패가 "바꿔서 다시 적어 주세요" 에 가려지면 보호자가 같은 실패를 되풀이한다.

`llm_misconfigured` → `llm_rate_limited` → `timeout` → `guard_unavailable` → `lookup_unavailable` → `llm_unavailable` → `internal_error` → `unparsable`

실패 task 가 없는데 한 일이 없으면 `unparsable`.

저장한 뒤에 도메인이 전부 실패한 run 은 `failed` 가 아니라 `partial`(`timeout_20s` · `agent_error`)로 끝난다. LLM 응답 시간 초과(`LLMTimeoutError`)도 `timeout_20s` 로 센다 — 저장 여부에 따라 같은 원인이 `failed=timeout` · `partial=agent_error` 로 갈리지 않게.

로그의 `도메인 task ... result=` 줄에 `reason=` 이 붙는다(결과 없음은 `None`). 원문은 싣지 않는다 (루트 §2).

Memory 가 실패로 끝나도 모델에 요청이 나갔으면 `model_calls` 에 1(Agent 하나)로 센다 — 요청 전에 나는 설정 오류(`LLMConfigError`)만 0 이다.

Curator 판정기 · 임베딩도 같은 분류를 쓴다 — 시간 초과는 `LLMTimeoutError`, 연결 실패는 `LLMUnavailableError`. Curator 는 run 밖이라 화면에는 안 나온다.
