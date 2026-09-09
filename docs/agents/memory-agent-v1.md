# Memory Agent v1

문서 목적: 일반 발화를 observation·schedule CRUD 로 구조화하는 Memory Agent 의 구조 · 결정 · 검증을 고정한다.

기준 브랜치: `feat/ai-17-memory-agent-text`
작성일: 2026-09-09
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
             store:    저장 / 조회
                   ──▶ ToolResult 를 모델에 되돌림 (반복)
             final_message
```

밖으로 열린 것은 **`run(raw_text, context, directive)` 하나**다. `apps/api/CLAUDE.md` 가
*"app/api — 허용: agents 진입점 / 금지: agents 내부 구현 직접 import"* 를 import-linter 로 강제하므로,
registry·prompt·client 는 전부 내부 구현으로 둔다.

### tool 27개

| 묶음 | 개수 | 이름 |
| --- | --- | --- |
| 발화 분리 | 1 | `parse_input` |
| observation | 16 | `create/query/update/delete_observation_{food,health,education,activity}` |
| event | 4 | `create/query/update/delete_event` |
| event_item | 3 | `create/update/delete_event_item` |
| reminder | 3 | `create/update/delete_reminder` |

`event_item` · `reminder` 에는 조회 tool 이 없다. `query_event` 가 딸린 준비물·알림을 함께 돌려준다 —
"기존 일정에 알림 추가" 가 조회 한 번으로 끝나야 하기 때문이다.

---

## 2. 구조

```
apps/api/app/agents/
├── common/
│   ├── config.py          AgentSettings — .env 에서 API_KEY / BASE_URL / MODEL / REASONING_EFFORT
│   ├── llm_client.py      LLMClient.chat() — 한 번의 왕복만 책임진다
│   └── datetime_rules.py  날짜·시각 표현 → 확정값 (표준 라이브러리만)
├── memory/
│   ├── agent.py           run() — tool calling loop
│   ├── context.py         AgentContext (child_id · source_writer · now · timezone · store)
│   ├── prompt.py          system prompt
│   ├── registry.py        이름 → 함수 매핑 + 인자 검증
│   ├── result.py          ToolResult · ErrorCode
│   ├── schemas/           tool argument 스키마 + JSON Schema 변환 + tool 정의 27개
│   ├── tools/             parse_input · observation 16 · schedule 10
│   └── store/             ports.py(Protocol) · inmemory.py
└── test/
    ├── test.py            라이브 eval (opt-in, `-m live`)
    ├── test_input.txt     오답 유도 입력 25개
    └── unit/              mock 회귀 테스트 (API 호출 없음)
```

### 계층별 책임

| | 하는 일 | 안 하는 일 |
| --- | --- | --- |
| **프롬프트** | 무엇을 어디에 담을지, 무엇을 하지 말지 | 값 계산 |
| **LLM** | tool 선택, 자연어에서 인자 추출, 과거/미래 문맥 판단 | 날짜 계산, id 생성 |
| **registry** | 이름 조회, Pydantic 검증 | 도메인 로직 |
| **tools** | 날짜 확정, context 주입, NOT NULL 기본값 | DB 직접 접근 |
| **store** | 저장·조회, id·created_at 생성, CASCADE | 값 해석 |
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

`"전날"` · `"당일"` 처럼 **기준 일정이 있어야 풀리는 표현은 거절한다.** 오늘 기준으로 풀면 하루 어긋난 알림이
조용히 저장되기 때문이다. 대신 `offset_days_from_event`(전날 `-1`)로 유도한다.

### D2-1. tool 을 쓰면 reasoning 을 못 쓴다

현재 모델은 `chat/completions` 에서 `tools` 와 `reasoning_effort` 를 동시에 지원하지 않는다 (HTTP 400).
`REASONING_EFFORT=none` 으로 고정했고, `LLMClient` 가 왕복 전에 `LLMConfigError` 로 막는다.
reasoning 을 유지하려면 `/v1/responses` 로 옮겨야 하는데, 그때 입력 토큰도 함께 줄어든다(실측 189 → 108).

### D3. LLM 에게 노출하지 않는 필드

`id` · `child_id` · `source_writer` · `created_at`/`updated_at` · `embedding` · `affinity_id` ·
`expires_at` · `status` · `created_by` · `sent_at` · `observed_range` — 전부 context · 규칙 · store 책임이다.

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
| `UNKNOWN_EVENT` | `create_event` / `query_event` 먼저 |
| `DATE_UNPARSEABLE` | 날짜·시각 표현을 고치거나 비움 |
| `UNKNOWN_TOOL` · `TARGET_REQUIRED` · `AMBIGUOUS_TARGET` · `OUT_OF_SCOPE` | — |

### D5. 모든 스키마 필드에 description

Pydantic `Field(description=...)` 이 그대로 tool JSON Schema 로 나가고, 모델의 필드 채움 정확도를 좌우한다.
description 이 없는 필드는 테스트가 잡는다.

### D6. 모델명을 저장소에 남기지 않는다

이 저장소는 public 이다. `MODEL` 은 `API_KEY` 와 같은 취급으로 **코드에 기본값 없이 `.env` 에서만** 온다.
클래스·로그·마커·파일명에서도 모델명을 뺐다. eval 의 비용 추정도 모델명 분기 대신
`LLM_INPUT_PRICE_PER_M` / `LLM_OUTPUT_PRICE_PER_M` 환경변수로만 계산한다.

### D7. `parse_input` 은 복합 입력에서만

기준은 tool 개수도 문장 수도 아니라 **"서로 독립된 정보·요청이 2개 이상인가"** 다.
한 일정에 딸린 준비물·알림은 부속이라 하나로 센다.

| 입력 | `parse_input` |
| --- | --- |
| "오늘 2시에 모래놀이했어" | ❌ 정보 1개 |
| "오늘 2시에 모래놀이하고 떡볶이 먹었어" | ✅ 한 문장이어도 정보 2개 |
| "금요일에 물놀이 있어. 수영복이랑 여벌옷 챙겨야 해" | ❌ 한 일정 + 그 부속 |

모든 입력에서 부르면 단순 입력에도 왕복 1회가 붙는다.

### D8. 아직 일어나지 않은 일

`observation` 은 이미 일어난 일만 담는다. 미래 계획은 도메인에 따라 갈린다.

| 도메인 | 처리 |
| --- | --- |
| food | 저장하지 않고 짧게 안내 |
| health · education · activity | **일정으로 등록할지 한 번 묻는다** |

어느 쪽이든 답을 듣기 전에는 저장하지 않는다.

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
**필드 이름을 나열**하자 첫 호출에 성공했다.

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

### Windows 에는 IANA 타임존 DB 가 없다

`ZoneInfo("Asia/Seoul")` 이 `ZoneInfoNotFoundError` 다. `tzdata` 를 의존성에 추가했다.
리눅스 컨테이너에서는 안 드러나고 팀원 로컬에서만 터지는 종류다.

---

## 5. 검증

### 단위 테스트 402개 (`make test`, API 호출 없음)

| 파일 | 무엇을 지키나 |
| --- | --- |
| `test_datetime_rules.py` | 상대 날짜·요일·월일·시각 해석, 기준 일정 상대 표현 거절 |
| `test_tool_schema.py` | tool 27개, 필드별 description, D3 금지 필드 미노출, `$ref` 인라인 |
| `test_store.py` | Protocol 시그니처 일치, child_id 격리, CASCADE, `None` 은 "안 바꿈" |
| `test_registry.py` | 없는 이름·깨진 인자를 예외가 아닌 `ToolResult` 로, 에러에 발화 원문 없음 |
| `test_observation_tools.py` | T01·T03·T04·T05·T08·T09·T10, 기본값 주입, 하루 종일 1440 |
| `test_schedule_tools.py` | T06·T07·T11·T12·T13, draft 24h 만료, 일정 기준 알림 |
| `test_prompt.py` | 구획·규칙 문구, tool description 과 호출 조건 일치, 길이 상한 |
| `test_agent_loop.py` | 의존 체인, 깨진 JSON, 중복 차단, 실패 재시도 허용, MAX_STEPS |

### 라이브 eval (`pytest app/agents/test/test.py -m live`)

`test_input.txt` 에 오답 유도 입력 25개. 기본 실행에서는 `addopts = "-m 'not live'"` 로 제외된다.

수동으로 확인한 케이스와 결과:

| 입력 | 결과 |
| --- | --- |
| 단일 관찰 | `parse_input` 없이 바로 저장 |
| 한 문장 복합 | `parse_input` → 2개 저장 |
| 일정 + 준비물 2개 | `parse_input` 없이 event + item 2회 |
| 복합 5종 | 4 step · 실패 0건 · `create_event` id 를 뒤 step 이 사용 |
| 미래 식사 계획 | tool 0회 + 안내 |
| 추천 요청 | 관찰만 저장 + 추천 거부 |
| 모호한 삭제 | 삭제 0회 + 재질문 1개 |
| 전해 들은 증상 + 부정 표현 | `parent_hearsay`, 없다고 한 증상 제외 |
| 알레르기 등록 요청 | tool 0회 + 보호자 직접 입력 안내 |
| 진단 요청 | 증상만 기록 + 진단 거부 |

---

## 6. 아직 안 한 것

| | 이유 |
| --- | --- |
| 실제 DB 연결 | `app/domains/` 에 ORM 모델이 없다. `store/` Protocol 을 구현하는 어댑터만 추가하면 tool 코드는 안 바뀐다 |
| embedding | `subject` 가 임베딩 입력인데 update 로 바뀔 수 있다. 재계산 시점을 Curator 앞으로 둘지 결정 필요 |
| `app/api` 연결 | `run()` 을 부르는 엔드포인트. `child_id`/`source_writer` 를 인증 context 에서 채워야 한다 |
| eval 기대값 | `test_input.txt` T16~T25 는 입력만 있고 기대값이 없다 |
| `calendar` 연동 | `event.calendar_id` 컬럼이 아직 없다 |
| `EventCreate.ends_on` | 생성은 하루짜리만, 수정으로는 여러 날로 바꿀 수 있는 비대칭 |
| 주·월 단위 조회 | `resolve_date_range` 에 "이번 주" 가 없다 |

### 이관 검토

- `common/datetime_rules.py` → `app/rules/` — 루트 §3 상 날짜 계산은 rules 소속이다. 표준 라이브러리만 쓰도록
  만들어 그대로 옮길 수 있다. `app/rules/` 는 공동 소유라 양쪽 리뷰가 필요하다.
- `common/config.py` 의 `AgentSettings` → `app/core/config.py` — 인증·API 레이어가 붙을 때 협의.

---

## 7. 파트 경계를 넘은 변경

`app/core/config.py` 에 `extra="ignore"` 한 줄을 추가했다. 백엔드 소유 파일이다.

`.env` 에 LLM 키가 생기자 pydantic-settings 기본값(`extra="forbid"`)이 `Settings()` 를 크래시시켰고,
**에러 메시지에 API 키 값이 평문으로 출력**됐다 (CONTRIBUTING §9 "민감정보가 에러 응답에 남지 않는가").
하나의 `.env` 를 여러 레이어가 나눠 읽는 구조를 이 방식으로 둘지 백엔드 Owner 확인이 필요하다.
