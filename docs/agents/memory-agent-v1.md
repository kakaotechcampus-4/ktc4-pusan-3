# Memory Agent v1

문서 목적: 일반 발화를 observation·schedule CRUD 로 구조화하는 Memory Agent 의 구조 · 결정 · 검증을 고정한다.

최초 작성: 2026-09-09 · `feat/ai-17-memory-agent-text`
마지막 갱신: 2026-09-21 · `refactor/ai-110-event-tool-revise` — 일정 초안(D9·D10) 반영 + 저장소 현재 상태로 전체 대조
담당: 이시하
선행 문서: [테크스펙 §아키텍처 설계](../overview/tech-spec.md#아키텍처-설계) (Agent 계층의 위치)

---

## 1. 무엇을 만들었나

보호자의 한 줄 발화를 받아, 어떤 테이블에 무엇을 저장·조회·수정·삭제할지 판단하고 실행한다.

```
raw_text ──▶ run()  ──▶ [ LLM: tool 선택 + 인자 추출 ]
                   ◀──  tool_calls
             registry: 이름 조회 → Pydantic 검증 → 실행
             tools:    날짜 확정 · context 주입 · 기본값 채움
             store:    관찰 저장 / 조회
             drafts:   일정은 저장 대신 초안 버퍼로 (보호자 제출 뒤 백엔드가 쓴다)
                   ──▶ ToolResult 를 모델에 되돌림 (반복)
             final_message
```

밖으로 열린 것은 **`run(raw_text, context, *, client, max_steps, task)` 하나**다.

`max_steps`(기본 `MAX_STEPS = 7`)는 **LLM 왕복 수** 상한이지 tool 건수 상한이 아니다. 한 왕복에 tool 이 여러 개 와도 그 바퀴에서 전부 실행하고 `steps` 는 1만 올라간다 — 실행 건수는 `len(calls)` 다. run 예산(`model_calls`)은 이 루프를 몇 바퀴 돌았든 Memory 를 1로 센다 ([Agent_공통규약.md](shared/Agent_공통규약.md) §7).
`apps/api/CLAUDE.md` §레이어 경계가 *"app/api — 허용: agents 진입점 / 금지: agents 내부 구현 직접 import"*
라서 registry·prompt·client 는 전부 내부 구현으로 둔다. 초판에는 이 규칙을 import-linter 가 강제한다고
적었는데 그런 설정은 저장소에 없다. 지금은 규칙 문서와 리뷰가 전부다.

pipeline 을 거치는 경로에서는 `pipeline.handle_input()` 이 Supervisor → Memory → Food 순서로 부른다.
`run()` 을 직접 부르는 것은 Supervisor 없이 Memory 만 잴 때다.

### tool 27개

| 묶음 | 개수 | 이름 |
| --- | --- | --- |
| observation | 20 | `create/query/update/delete_observation_{food,health,education,activity,routine}` |
| event | 4 | `create/query/update/delete_event` |
| event_item | 3 | `create/update/delete_event_item` |

`routine` 은 #107 에서 다섯 번째 도메인으로 붙었다. 생활 행동(양치·정리·인사)이 `activity` 에 섞여
들어가던 것을 뗀 것이다.

27개를 한꺼번에 열지 않는다. `bundles.py` 가 작업 종류에 따라 두 묶음으로 나눈다 —
기록 기본 묶음 `RECORD_BASE` 8개는 항상 열고, 수정·삭제 묶음 `LOOKUP_EDIT` 19개는 Supervisor 가
`lookup_edit` 조각을 짚었을 때만 더한다. Supervisor 없이 `run()` 을 직접 부르면 27개를 다 연다.

`event_item` 에는 조회 tool 이 없다. `query_event` 가 딸린 준비물을 함께 돌려준다 —
"기존 일정에 준비물 추가" 가 조회 한 번으로 끝나야 하기 때문이다.

알림 tool 은 없다. 알림은 등록된 일정을 기준으로 자동 발송이라 Agent 가 만들 것이 없고,
알림 요청이 와도 일정을 만들거나 고치지 않고 안내만 한다.

준비물이 어디로 가는지는 새 일정이냐 아니냐로 갈린다 (D9).

| 발화 | 호출 |
| --- | --- |
| 새 일정 + 준비물 | `create_event(items=[...])` 한 번. 준비물을 따로 부르지 않는다 |
| 기존 일정에 준비물 추가 | `query_event` → `create_event_item` 을 준비물마다 |
| 준비물 이름 변경 | `update_event_item(item_name=...)` — 수정 초안으로 간다 |
| 준비물 챙김 표시 | `update_event_item(is_prepared=...)` — 초안 없이 바로 쓴다 |

---

## 2. 구조

```
apps/api/app/agents/
├── pipeline.py            Supervisor → Memory → Food 순서 · SSE 이벤트
├── common/                세 Agent 가 공유
│   ├── config.py          AgentSettings — .env 에서 역할별 API_KEY / BASE_URL / MODEL / REASONING_EFFORT
│   ├── llm_client.py      LLMClient.chat() — 한 번의 왕복만 책임진다
│   ├── tool_schema.py     Pydantic → tool JSON Schema
│   └── datetime_rules.py  날짜·시각 표현 → 확정값 (표준 라이브러리만)
├── supervisor/            발화를 조각으로 나누고 Agent 로 라우팅 (옛 parse_input 자리, D7)
├── food/                  도메인 Agent
└── memory/
    ├── agent.py           run() — tool calling loop
    ├── bundles.py         작업 종류 → 열어 줄 tool 묶음
    ├── context.py         AgentContext (child_id · source_writer · now · timezone · store · drafts)
    ├── drafts.py          EventDraft · DraftItem · DraftBook (run 단위 초안 버퍼)
    ├── prompt.py          system prompt
    ├── registry.py        이름 → 함수 매핑 + 인자 검증
    ├── result.py          ToolResult · ErrorCode
    ├── schemas/           tool argument 스키마 + tool 정의 27개 + MemoryTask
    ├── tools/             observation.py(20) · schedule.py(7)
    └── store/             ports.py(Protocol) · inmemory.py

apps/api/tests/
├── unit/agents/memory/    mock 회귀 테스트 (API 호출 없음)
└── eval/agents/
    ├── test_input.txt     오답 유도 입력 34개
    ├── routing_cases.py   조각 나누기 정답
    ├── memory/test_memory.py   Memory 단독 eval (`-m live`)
    └── supervisor/test.py      Supervisor → Memory → Food 전 구간 eval (`-m live`)
```

### 계층별 책임

| | 하는 일 | 안 하는 일 |
| --- | --- | --- |
| **프롬프트** | 무엇을 어디에 담을지, 무엇을 하지 말지 | 값 계산 |
| **LLM** | tool 선택, 자연어에서 인자 추출, 과거/미래 문맥 판단 | 날짜 계산, id 생성 |
| **registry** | 이름 조회, Pydantic 검증 | 도메인 로직 |
| **tools** | 날짜 확정, context 주입, NOT NULL 기본값, 일정 초안 작성 | DB 직접 접근, 일정 쓰기 |
| **store** | 관찰 저장·조회, 일정 조회·삭제, id·created_at 생성, CASCADE | 값 해석, 일정 쓰기 |
| **agent 루프** | 다단계 진행, 깨진 인자·중복 쓰기·미종료 방어 | 인자 검증 |

---

## 3. 고정한 결정

### D1. 전 계층 async

`app/domains/` 가 SQLAlchemy async 스택이고 `asyncio_mode = "auto"` 가 이미 설정돼 있다.
나중에 동기 코드를 뒤집는 비용이 커서 처음부터 async 로 썼다. `datetime_rules.py` 만 순수 동기다.

### D2. 날짜·시각은 코드가 계산한다

LLM 은 **원문 표현**(`"모레"`, `"저녁 8시"`)만 넘기고 `datetime_rules.py` 가 확정값을 만든다.
ISO 문자열도 같은 필드로 받는다 — 모델·API 조합에 따라 표현을 주기도 하고 ISO 를 주기도 하기 때문이다.
필드를 둘로 나누면 모델이 둘 다 채우거나 서로 어긋난 값을 넣는다.

**단, 과거/미래 판단은 LLM 이 한다.** "금요일"·"9월 11일" 은 계산으로 어느 주·어느 해인지 정할 수 없다.
그래서 날짜를 받는 tool 은 `temporal_direction`(`past` / `future` / `nearest`)을 함께 받는다.

`"전날"` · `"당일"` 처럼 **기준 일정이 있어야 풀리는 표현은 거절한다**(`datetime_rules.py` 의 `_ANCHOR_RELATIVE`).
오늘 기준으로 풀면 하루 어긋난 일정이 조용히 저장된다. 알림 tool 이 있던 때는 `offset_days_from_event` 로
유도했지만, 알림이 자동 발송으로 바뀌면서 그 인자도 같이 없어졌다. 지금은 되묻는다.

### D2-1. tool 을 쓰면 reasoning 을 못 쓴다

현재 모델은 `chat/completions` 에서 `tools` 와 `reasoning_effort` 를 동시에 지원하지 않는다 (HTTP 400).
`REASONING_EFFORT=none` 으로 고정했고, `LLMClient` 가 왕복 전에 `LLMConfigError` 로 막는다.
reasoning 을 유지하려면 `/v1/responses` 로 옮겨야 하는데, 그때 입력 토큰도 함께 줄어든다(실측 189 → 108).

### D3. LLM 에게 노출하지 않는 필드

`id` · `child_id` · `source_writer` · `created_at`/`updated_at` · `embedding` · `affinity_id` ·
`created_by` · `sent_at` · `observed_range` — 전부 context · 규칙 · store 책임이다.
`event.status` 와 `event.expires_at` 은 D9 로 컬럼째 없어진다.

`observation_health` 는 승격 파이프라인 밖이라 `subject` · `polarity` · `strong_signals` 를 쓰지 않는다.
이를 주석이 아니라 **상속으로** 막았다 — `ObservationCreateArgs`(공통) → `PromotableCreateArgs`(+승격 필드)로
나누고 health 는 앞의 것만 상속한다.

### D4. tool 결과는 항상 `ToolResult`

```json
{"success": true,  "operation": "create", "resource": "observation_food", "data": {...}}
{"success": false, "operation": "update", "resource": "event", "error": {"code": "...", "message": "..."}}
```

`error.code` 8종은 상수로 고정한다. 모델이 코드를 보고 다음 행동을 정하므로 문자열을 그때그때 지으면 안 된다.

| 코드 | 모델이 해야 할 일 |
| --- | --- |
| `VALIDATION_ERROR` | 인자를 고쳐 재호출 |
| `TARGET_NOT_FOUND` | 조회 tool 을 먼저 |
| `UNKNOWN_EVENT` | `query_event` 먼저 (D9 이후 `create_event` 는 id 를 돌려주지 않는다) |
| `DATE_UNPARSEABLE` | 날짜·시각 표현을 고치거나 비움 |
| `UNKNOWN_TOOL` · `TARGET_REQUIRED` · `AMBIGUOUS_TARGET` · `OUT_OF_SCOPE` | — |

### D5. 모든 스키마 필드에 description

Pydantic `Field(description=...)` 이 그대로 tool JSON Schema 로 나가고, 모델의 필드 채움 정확도를 좌우한다.
description 이 없는 필드는 테스트가 잡는다.

### D6. 모델명을 저장소에 남기지 않는다

이 저장소는 public 이다. `MODEL` 은 `API_KEY` 와 같은 취급으로 **코드에 기본값 없이 `.env` 에서만** 온다.
클래스·로그·마커·파일명에서도 모델명을 뺐다. eval 의 비용 추정도 모델명 분기 대신
`LLM_INPUT_PRICE_PER_M` / `LLM_OUTPUT_PRICE_PER_M` 환경변수로만 계산한다.

### D7. 발화 나누기는 Memory 가 하지 않는다 (`parse_input` 폐기)

처음에는 Memory 가 `parse_input` tool 로 직접 발화를 나눴다. 기준은 **"서로 독립된 정보·요청이
2개 이상인가"** 였고, 한 일정에 딸린 준비물은 부속이라 하나로 셌다.

지금은 Supervisor 가 그 일을 한다. 나누는 것과 어느 Agent 로 보낼지 정하는 것이 같은 판단이라
두 곳에서 하면 어긋난다 — Memory 가 "정보 2개" 로 본 것을 Superviser 가 한 조각으로 보내면
한쪽이 사라진다. `parse_input` 을 지우고 Supervisor 출력(`MemoryTask.hints`)을 참고용으로 받는다.

참고용이라는 게 중요하다. Memory 는 힌트가 아니라 **원문 전체**에서 기록할 것을 찾는다
(`agent.py` 의 `_user_message`). Supervisor 가 조각을 놓쳐도 Memory 가 메울 수 있어야 하기 때문이다.
힌트는 두 가지에만 쓰인다 — 열어 줄 tool 묶음(`bundles.py`)과 조기 종료 판정(`_covered`).

### D8. 아직 일어나지 않은 일

`observation` 은 이미 일어난 일만 담는다. 미래 계획은 도메인에 따라 갈린다.

| 도메인 | 처리 |
| --- | --- |
| food | 저장하지 않고 짧게 안내 |
| health · education · activity | **일정으로 등록할지 한 번 묻는다** |

어느 쪽이든 답을 듣기 전에는 저장하지 않는다.

### D9. 일정은 저장하지 않고 초안으로 내보낸다

9/17 결정이다. `event` 테이블에서 `status` 를 없애기로 하면서, 승인 전 행을 DB 에 두는 방식 자체를 접었다.
`create_event` 와 `update_event` 는 시각 해석(`resolve_when`)과 구간 검증(`check_when`)을 통과한 값을
`EventDraft` 로 만들어 `AgentContext.drafts` 에 넣는다. pipeline 이 run 끝에 `EventDrafts` 이벤트로
한 번 내보내고, 저장은 보호자가 제출한 뒤 백엔드가 한다. `MemoryStore` 계약에서 일정 쓰기 세 개
(`create_event` · `update_event` · `create_event_item`)를 지운 것도 같은 이유다 — 계약에 남겨 두면
다음에 tool 을 붙이는 사람이 승인 없이 쓰는 길을 다시 연다.

새 일정의 준비물을 `create_event` 의 `items` 로 받는 것도 여기서 따라온다. 초안에는 아직 event id 가
없어서 `create_event_item` 으로 이어 붙일 수 없다. 같은 이유로 `create_event` 결과에서 `id` 를 빼고
`draft: true` 와 요약만 싣는다.

한 run 에서 같은 일정을 두 번 고치면 초안은 하나다. 두 번째 호출은 DB 현재 값이 아니라 버퍼의 초안 위에
쌓는다. 그래서 `update_event` 와 `create_event_item` 이 한 응답에 같이 와도, 어느 쪽이 먼저 실행되든
결과가 같다. `changed` 에는 모델이 넘긴 인자가 아니라 현재 값과 실제로 다른 필드만 적는다 —
같은 제목으로 "바꾸면" 바뀐 게 없고, 그때는 초안을 만들지 않는다.

초안 payload 모양은 `app/agents/memory/drafts.py` 의 `EventDraft.to_payload()` 가 정본이다.
9/22 회의에서 **`op` 별로 두 모양**으로 확정했다 — create 는 `POST`, update 는 `PATCH` 로 가기 때문이다.

```
create : draft_id · op · source · event · items
update : draft_id · op · source · event_id · event · before · items
```

`event_id` 와 `before` 는 create 에서 언제나 `null` 이라 싣지 않는다. `op` 는 두 모양 모두에 남긴다 —
화면이 이 값으로 부를 엔드포인트를 고른다.

`before` 는 수정 전 원본 전체(준비물 포함)다. 화면이 "오후 3시 → 오후 5시" 를 그리는 데 쓴다.
바뀐 필드 이름(`changed`)은 payload 에 싣지 않는다. 보호자가 시트에서 값을 고치는 순간 서버가 보낸
`changed` 는 못 쓰는 값이 되고, 화면은 어차피 `before` 와 비교해야 한다. dataclass 에는 남겨서
tool 결과와 "바뀐 게 없으면 초안을 만들지 않는다" 판정에 쓴다.

`source` 는 제안에서 온 초안의 출처를 담을 자리다. Memory 는 그 경로를 거치지 않아 언제나 `null` 이고,
#121 의 제안 경로가 붙을 때 suggestion 도메인이 채운다.

준비물의 `is_prepared` 는 payload 에 싣지 않는다. `items` 가 최종 목록이라 실어 보내면, SSE 로 나간 뒤
보호자가 `PATCH /event-items/{iid}` 로 누른 체크가 제출하는 순간 풀린다. 체크는 그 PATCH 만의 몫이다.
`DraftItem.is_prepared` 필드는 남는다 — `_diff` 가 `items` 를 통째로 비교해서, 필드를 지우면
체크만 한 run 이 빈 수정 초안을 만든다.

### D10. 지우는 것은 승인을 거치지 않는다

`delete_event` 와 `delete_event_item` 은 바로 지운다. 준비물 챙김 표시(`is_prepared`)도 바로 쓴다.
승인 게이트는 되돌릴 수 없는 것에만 둔다는 루트 §3 의 판단 기준을 따른 것인데, 삭제는 되돌릴 수 없으면서도
게이트 밖이다. 9/17 에 그렇게 정했고 루트 §3 표의 "일정 등록/변경" 에도 삭제가 없어 충돌하지는 않는다.

대신 대상이 모호하면 되묻는 규칙이 유일한 안전장치다 (eval "모호한 삭제 → 삭제 0회 + 재질문 1개").
같은 run 에서 고쳤다가 지운 일정이면 버퍼의 초안도 같이 뺀다.

### D11. 초안은 run 이 끝나면 사라진다

9/22 회의 결정이다. 초안은 `AgentContext` 안에 run 단위로만 살고, 화면을 벗어나거나 새로고침하면
되받을 경로가 없다. 승인 시트 문구도 "하루 뒤 만료" 가 아니라 이 동작에 맞춰 바뀐다.

run 단위로 어딘가 남기는 안도 봤지만, `event` 테이블이 아닌 별도 저장 구조가 필요하고 "승인 전 행을 DB 에 두지 않는다" 와 어디까지 다른지 따져야 해서 접었다. 보호자가 다시 말해야 하는
비용은 받아들인다.

---

## 4. 실측으로 바뀐 것

계획서에 적어둔 것과 실제 모델이 다르게 움직인 지점들이다.

### 에러 메시지에 복구 방법이 없으면 모델이 포기한다

`observed_time="오늘 낮"` 을 `"해석할 수 없는 시각 표현"` 으로만 거절하자 모델이 이렇게 끝냈다.

> 기록에 실패했어요. "오늘 낮"의 시각 표현을 해석할 수 없습니다.

**저장된 행이 0건**이었다. 시각 하나 못 읽었다는 이유로 콧물 관찰 전체가 날아갔다.
메시지에 *"몇 시인지 알 때만 넣고, 아니면 비워두고 다시 호출한다"* 를 붙이자 재호출해 성공했다.

→ `error.message` 는 사유가 아니라 **다음 행동**을 적는다.

### 프롬프트는 필드 이름을 지목해야 먹힌다

`"낮"·"아침"처럼 특정되지 않으면 비운다` 로는 모델이 계속 `observed_time="낮"` 을 보냈다.
필드 설명이 *"발화에 드러날 때만 채우기"* 인데 사용자가 "낮"이라고 말했으니 모델 입장에선 드러난 것이다.
일반 문장이 필드 옆 설명을 못 이긴다.

`observed_time · starts_time · remind_time 에는 ... 시간대만 말한 경우에는 그 필드를 아예 비운다` 로
**필드 이름을 나열**하자 첫 호출에 성공했다. (`remind_time` 은 알림 tool 과 함께 없어졌다.)

### 문장을 통째로 비교하는 테스트는 못 쓴다

프롬프트를 다듬자 조사 띄어쓰기(`symptom 에` → `symptom에`)만 바뀌었는데 테스트 3건이 깨졌고,
정작 같은 편집에서 **문구 하나가 통째로 사라진 건 못 잡았다.**
공백을 지우고 핵심 어구로 비교하도록 바꾸고, 빠지면 안 되는 것을 개별 케이스로 쪼갰다.

### 루프가 생기니 프롬프트 캐시가 걸린다

| | prompt_tokens | cached |
| --- | --- | --- |
| 단발 호출 (Step 4) | 6,548 | 0 |
| 루프 4 step (Step 10) | 34,194 | **23,585 (69%)** |

같은 접두(system + tool 스펙 27개)를 반복해 보내면서 캐시가 먹는다.
tool 27개의 비용 부담이 계획에서 걱정하던 것보다 작다.

### 프롬프트에 예시 문구를 적으면 모델이 그대로 베낀다

일정이 저장 전이라는 것을 알리게 하려고 `[응답]` 에 이렇게 적었다.

> "등록했어요"·"저장했어요" 대신 "이렇게 만들어 뒀어요, 확인해 주세요" 라고 답한다.

라이브 eval T24("다음 주 수요일 병원 예약 있어. 진료 전날 밤 9시에 알려줘")에서 모델이 tool 을
하나도 부르지 않고 `"병원 예약 일정은 확인했어요"` 로 끝냈다. 몇 시인지 되물어야 하는 입력이다.
따라 쓸 문장을 주니 할 일을 한 것처럼 답하는 데 갖다 썼다.

예시 문구를 빼고 규칙이 도는 조건을 `create_event·update_event 를 불렀으면` 으로 좁히자 통과했다.

→ 프롬프트에는 **하지 말 것**을 적고, 할 말을 대신 써 주지 않는다.

### 규칙끼리 부딪히면 나중에 읽은 쪽이 아니라 구체적인 쪽이 이긴다

`[id를 쓸 때]` 에 "준비물을 붙일 일정은 항상 query_event 로 먼저 찾는다" 가 있는데도,
"운동회에 물통이랑 모자도 챙겨야 해" 에 대해 모델이 `query_event` 를 부르지 않고 날짜를 되물었다.
`[되물어야 할 때]` 의 "일정에 시작 시각이 없으면 몇 시인지 묻는다" 를 먼저 적용한 것이다.
준비물만 더하는 발화에 시각이 없는 건 당연한데 새 일정으로 읽혔다.

`[되물어야 할 때]` 쪽에 예외를 박아서야 잡혔다 — 규칙이 부딪히는 자리에 예외를 쓰지 않으면,
다른 구획에 일반론으로 적어 둔 것은 안 읽힌다.

### Windows 에는 IANA 타임존 DB 가 없다

`ZoneInfo("Asia/Seoul")` 이 `ZoneInfoNotFoundError` 다. `tzdata` 를 의존성에 추가했다.
리눅스 컨테이너에서는 안 드러나고 팀원 로컬에서만 터지는 종류다.

---

## 5. 검증

### 단위 테스트 (`make test`, API 호출 없음)

`tests/unit` 전체 548개 중 Memory 몫은 아래 네 파일이다. 나머지는 Supervisor · Food · rules · providers 다.

| 파일 | 개수 | 무엇을 지키나 |
| --- | --- | --- |
| `agents/memory/test_store_clear.py` | 94 | "키 없음 / null / clear" 세 갈래, 비울 수 있는 필드 목록, 일정 종료 지우기 |
| `agents/memory/test_event_when.py` | 70 | 시작·종료·all_day 의 일관성. 어떤 수정을 해도 종료가 시작보다 앞서지 않는다 |
| `agents/memory/test_event_draft.py` | 22 | 초안 payload 모양, 같은 일정은 초안 하나, `changed` 판정, 준비물 경로 |
| `agents/memory/test_agent_loop.py` | 20 | 깨진 JSON, 중복 차단, 실패 재시도 허용, MAX_STEPS, 조기 종료 |

`tests/unit/agents/test_store_clear.py` 가 `agents/memory/` 쪽과 바이트 단위로 같다. #109 머지에서 이동
커밋의 삭제가 빠진 흔적이고, 지우는 커밋이 따로 필요하다.

문서 초판이 적었던 `test_datetime_rules.py` · `test_tool_schema.py` · `test_store.py` · `test_registry.py` ·
`test_prompt.py` · `test_observation_tools.py` · `test_schedule_tools.py` 는 지금 저장소에 없다.
파일이 합쳐지고 이름이 바뀌는 과정에서 사라졌다 — 날짜 규칙과 프롬프트 구획을 직접 겨누는 테스트가
지금은 없다는 뜻이다. §6 에 남긴다.

### 라이브 eval

두 벌이다. 기본 실행에서는 `addopts = "-m 'not live'"` 로 제외된다.

```bash
uv run pytest tests/eval/agents/memory/test_memory.py -m live      # Memory 단독 34 케이스
uv run pytest tests/eval/agents/supervisor/test.py -m live         # Supervisor → Memory → Food 95 케이스
```

입력은 `tests/eval/agents/test_input.txt` 한 곳에 모으고, 케이스 정의가 그 파일과 다르면 수집 단계에서
`AssertionError` 다. 조각 나누기 정답은 `routing_cases.py` 에 있고 두 eval 이 같이 쓴다.

`EVAL_MEMORY_MODE=task` 를 주면 Supervisor 출력(정답)을 넣고 tool 묶음을 좁혀 돌린다. 기본값 `solo` 는
27개를 다 열고 Memory 만 잰다. `EVAL_RETRY_BUDGET` 로 "몇 번까지 고쳐 부르는 것을 통과로 볼지" 를 정한다
(기본 1).

2026-09-21 `refactor/ai-110-event-tool-revise` 기준 **Memory 34/34 · 전 구간 95/95**, 재시도 0건.
한 번에 돌린 것은 아니다 — T01~T31 과 전 구간 92개를 돌린 뒤 일정 초안 엣지 3개(T32~T34)를 더하고
그 3개만 양쪽에서 따로 돌렸다.

일정 초안과 관련해 확인한 것:

| 입력 | 결과 |
| --- | --- |
| 새 일정 + 준비물 2개 | `create_event(items=[...])` 한 번. 초안 1장에 준비물 2개 |
| 기존 일정에 준비물 추가 | `query_event` → `create_event_item` 2회 → 초안 1장 |
| 수정과 준비물이 한 발화에 | 호출 순서와 무관하게 초안 1장 (`changed = starts_at, items`) |
| 준비물 이름 변경 | 초안 1장. 저장된 이름은 그대로 |
| 준비물 챙김 표시 | 초안 0장. `is_prepared` 와 `prepared_at` 이 바로 저장된다 |
| 일정 수정 | 초안 1장. 저장된 행은 그대로 |
| 알림 요청 | tool 0회 + 안내. 일정을 만들지도 고치지도 않는다 |
| 미래 식사 계획 | tool 0회 + 안내 |
| 모호한 삭제 | 삭제 0회 + 재질문 1개 |
| 알레르기 등록 요청 | tool 0회 + 보호자 직접 입력 안내 |
| 진단 요청 | 증상만 기록 + 진단 거부 |

---

## 6. 아직 안 한 것

| | 이유 |
| --- | --- |
| 실제 DB 연결 | ORM 모델은 `app/domains/` 에 생겼지만 `store/` Protocol 을 구현하는 어댑터가 없다. tool 은 여전히 `InMemoryStore` 를 쓴다 |
| 초안 제출 API | D9 의 초안을 받아 event·event_item 에 쓰는 엔드포인트. 백엔드 몫이고, 보호자가 고친 시각을 `check_when` 으로 다시 검증해야 한다 |
| SSE 엔드포인트 | `GET /runs/{rid}/events` 가 아직 없다. pipeline 의 `EventDrafts` 를 `event_draft` 로 실어야 한다 |
| `app/api` 연결 | `run()` 을 부르는 엔드포인트. `child_id`/`source_writer` 를 인증 context 에서 채워야 한다 |
| embedding | `subject` 가 임베딩 입력인데 update 로 바뀔 수 있다. 재계산 시점을 Curator 앞으로 둘지 결정 필요 |
| 날짜 규칙·프롬프트 단위 테스트 | 초판에 있던 `test_datetime_rules.py` · `test_prompt.py` 가 지금 없다. `datetime_rules.py` 는 `test_event_when.py` 가 간접적으로만 덮고, 프롬프트 구획을 겨누는 테스트는 아예 없다 |
| 낡은 초안이 뒤 결과를 덮어쓰는 것 | 초안은 만들어진 시점의 DB 값을 들고 화면에 떠 있다. 두 장을 띄워두고 나중 것부터 제출하면 앞 초안이 뒤 결과를 지운다(`items` 가 최종 목록이라). 일단 현상유지로 두고 잠금은 나중에 보기로 했다 |
| 승인 전 재발화 | 초안을 제출하기 전에 같은 일정을 다시 말하면 `query_event` 가 0건을 돌려주고 create 초안이 두 장 생긴다. `DraftBook` 이 run 단위라서다(D11). 보호자가 한 장만 승인하면 된다 |
| `calendar` 연동 | `event.calendar_id` 컬럼이 아직 없다. 일기·사진은 `diary_entry` · `shared_photo` 로 갈라졌다 |
| `EventCreate.ends_on` | 생성은 하루짜리만, 수정으로는 여러 날로 바꿀 수 있는 비대칭 |
| 주·월 단위 조회 | `resolve_query_bound` 에 "이번 주" 가 없다 |

### 이관 검토

- `memory/tools/schedule.py` 의 `prepared_at_for` → `app/rules/` — 웹의 준비물 체크(`PATCH /event-items/{iid}`)도
  같은 규칙을 써야 한다. ai 브랜치에서 be 파트 폴더를 건드릴 수 없어 tool 에 둔 것이지 제자리가 아니다.
- `common/datetime_rules.py` → `app/rules/` — 루트 §3 상 날짜 계산은 rules 소속이다. 표준 라이브러리만 쓰도록
  만들어 그대로 옮길 수 있다. 제출 API 가 `check_when` 을 다시 불러야 해서 이제는 필요가 더 분명해졌다.
  `app/rules/` 는 공동 소유라 양쪽 리뷰가 필요하다.
- `common/config.py` 의 `AgentSettings` → `app/core/config.py` — 인증·API 레이어가 붙을 때 협의.
- `common/llm_client.py` 의 `openai` SDK 부분 → `app/providers/` — `apps/api/CLAUDE.md` 상 외부 모델 SDK 는
  providers 소속이다. 지금 agents 에 둔 건 providers 가 아직 비어 있어서지 설계 판단이 아니다.
  옮길 때는 통째로가 아니라 쪼갠다 — SDK 왕복·예외 번역만 providers 로 가고, `AgentSettings` 를 읽는 부분과
  "tool 쓰려면 `REASONING_EFFORT=none`" 같은 Agent 정책은 agents 에 남는다
  (`app/providers/` 는 agents import 금지라 `get_agent_settings()` 를 그대로 데려갈 수 없다).
  `providers/meal_plan/` 과 공용화할지는 그쪽 구조가 굳은 뒤에 판단한다.

---

## 7. 파트 경계를 넘은 변경

### `.env` 를 여러 레이어가 나눠 읽는다 (해결됨)

`.env` 에 LLM 키가 생기자 pydantic-settings 기본값(`extra="forbid"`)이 백엔드의 `Settings()` 를
크래시시켰고, **에러 메시지에 API 키 값이 평문으로 출력**됐다 (CONTRIBUTING §9).

한때 `app/core/config.py` 에 `extra="ignore"` 를 넣어 막았는데, 지금은 반대로 되어 있다 —
백엔드 `Settings` 는 `extra` 기본값(forbid)을 그대로 두고, Agent 쪽 `AgentSettings` 가
`extra="ignore"` 로 백엔드 키를 흘려보낸다(`common/config.py:57`). 소유 파일을 각자 지키는 모양이라
이쪽이 맞다.

### `app/domains/schedule/models.py` 의 `Event.status` 제거

D9 가 요구한 ORM 변경이다. 이 브랜치가 아니라 백엔드 쪽에서 처리됐다 — `EventStatus` enum,
`Event.status`, `Event.expires_at` 이 빠졌고 모델 docstring 에 "draft 는 DB 에 쓰지 않는다 (9/17 결정)" 가
적혔다. Agent 가 아직 이 ORM 을 쓰지 않아(자체 in-memory store) 그 불변식을 코드가 강제하지는 않는다.
어댑터를 붙일 때 확인할 것.

### `prepared_at` 판정이 tool 에 있다

`is_prepared` 가 바뀔 때 `prepared_at` 을 어떻게 둘지는 규칙이라 `app/rules/` 가 제자리인데,
그 폴더가 be 파트라 ai 브랜치에 담을 수 없었다. `memory/tools/schedule.py` 의 `prepared_at_for` 에
순수 함수로 두었다. 백엔드가 `PATCH /event-items/{iid}` 를 만들 때 같이 옮긴다.
