# Agent 설계 원칙

Agent 파트가 지킬 원칙 세 가지와 거기서 나온 할 일을 적는다. 원칙마다 지금 코드가 어디까지 지키는지와 빈 곳을 같이 적었다.

작성일: 2026-10-04
선행: 루트 [CLAUDE.md](CLAUDE.md) §2·§3 · [Agent_공통규약](docs/agents/shared/Agent_공통규약.md) · [일정 초안 흐름 v1](docs/event/event-draft-flow-v1.md)

다른 문서에 이미 있는 열린 항목(공통규약 C-8, 공통_구현_계획 K-1~K-10, 일정 초안 흐름 §6)은 다시 적지 않는다.

---

## 1. 원칙

### 1-1. 누구의 기록인지는 LLM이 정하지 않는다

LLM 이 tool 인자로 채우는 건 발화에서 나온 값뿐이다. 누구의 기록인지(`child_id`), 누가 썼는지(`source_writer`), 지금이 언제인지(`now`, `timezone`)는 요청을 받은 코드가 정해서 `AgentContext` 로 넘긴다. tool 인자에는 넣지 않는다([context.py:3-4](apps/api/app/agents/memory/context.py#L3-L4)).

LLM 이 `child_id` 를 채우게 하면 지어낸 id 하나, 발화에 섞인 지시("동생 기록으로 넣어 줘") 하나로 다른 아이의 기억을 읽고 쓸 수 있다. 모델이 이 값을 볼 이유도 없다. 루트 §2 는 모델 입력에도 필요한 필드만 보내라고 한다.

지금 `child_id` 가 넘어가는 길은 이렇다.

```
POST /children/{cid}/inputs         경로에서 받는다            children.py:23
  → runner.agent_job(child_id=cid)  접수할 때 클로저에 잡는다   children.py:84-90
  → entrypoint.handle_input(...)                               runner.py:92-99
  → AgentContext.child_id           tool 은 이 값만 읽는다      entrypoint.py:107-113
```

헤더로 받지 않는 이유는 멱등 범위다. 범위가 `parent_id + method + path + key` 라서 경로에 있는 아이가 저절로 들어간다([children.py:41-46](apps/api/app/api/v1/routers/children.py#L41-L46)). 아이를 헤더로 옮기면 같은 키로 다른 아이 요청이 왔을 때 앞 아이의 run 이 재생되고 뒤 입력은 저장되지 않는다.

job 인자로 넘기는 이유는 실행 시점이다. 창구가 202 를 먼저 돌려주고 job 은 그 뒤에 돌아서, job 이 실행될 때는 Request 가 남아 있지 않다. `app/agents` 는 fastapi 를 import 하지 못하니 헤더를 읽을 방법도 없다. 창구가 검사한 값과 Agent 가 쓰는 값이 같은 변수라는 점도 있다. 이어받기 맥락의 아이 확인([children.py:54-56](apps/api/app/api/v1/routers/children.py#L54-L56))과 나중에 붙을 소유 확인이 이 값으로 한다.

경로든 헤더든 클라이언트가 바꿀 수 있는 값이라, 실제로 막는 건 "이 보호자가 이 아이의 보호자인가"를 보는 403 이다. 아직 없다([children.py:5](apps/api/app/api/v1/routers/children.py#L5)).

### 1-2. LLM 이 고른 id 는 그 아이 안에서만 찾는다

LLM 이 직접 채우는 id 도 있다. `event_id`, `item_id`, `observation_id` 는 조회 tool 결과에서 골라 넘긴다. 정상 흐름에서는 그 아이의 행만 나오지만, 모델이 결과 밖의 id 를 넘겨도 저장소가 아이 범위로 걸러서 찾지 못하게 해야 한다.

지금 `MemoryStore` 포트의 id 조회 메서드는 아이를 받지 않는다([ports.py:111-174](apps/api/app/agents/memory/store/ports.py#L111-L174)). `get_observation` · `update_observation` · `delete_observation` · `get_event` · `delete_event` · `get_event_item` · `list_event_items` · `update_event_item` · `delete_event_item` 이 그렇다. run 마다 새 `InMemoryStore` 를 쓰는 동안은 다른 아이 행이 없어서 드러나지 않는다. DB 저장이 붙으면 그대로 구멍이 된다.

DB 리포지토리는 이미 모든 함수에서 `child_id` 를 받는다([observation/repository.py](apps/api/app/domains/memory/observation/repository.py), [schedule/repository.py](apps/api/app/domains/schedule/repository.py)). 어댑터가 넘기기만 하면 된다.

### 1-3. 초안은 Agent 가 만들고, 기준이 바뀌었는지는 제출 API 가 본다

LLM 은 지금도 바뀐 값만 낸다. `update_event` 의 인자가 patch 다. 현재 행 읽기, 병합, diff, `before` 붙이기는 tool 핸들러가 한다([schedule.py:171-261](apps/api/app/agents/memory/tools/schedule.py#L171-L261)).

이 일을 백엔드로 옮기지 않는다. tool 이 어차피 현재 행을 읽어야 해서다. "5시로 바꿔 줘"는 기존 날짜가 있어야 풀리고, 같은 값으로 바꾸는 요청에는 `changed: []` 를 돌려줘야 모델이 "이미 그렇게 되어 있어요"라고 답한다. 한 응답에 `update_event` 와 `create_event_item` 이 같이 오면 `DraftBook` 이 둘을 합친다. 백엔드가 patch 로 after 를 다시 만들면 같은 병합 로직이 두 곳에 생긴다. 초안은 run 끝에 SSE 로 한 번 나가고 서버에 남지 않아서, SSE 번역 시점에 DB 를 다시 읽어도 값이 더 최신이 되지 않는다.

빠진 건 제출할 때의 비교다. `before` 가 낡는 건 SSE 가 나간 뒤 보호자가 제출하기까지의 시간이고, 누가 초안을 만들든 같다. 그래서 제출 API 가 `before` 와 현재 행을 맞춰 보고 다르면 409 로 돌려야 한다. 일정 초안 흐름 §6 의 "낡은 초안 덮어쓰기"와 "유령 `item_id`"가 이 비교 하나로 같이 잡힌다.

다른 에이전트 시스템도 둘 중 하나를 쓴다. LangGraph 의 `interrupt()` 와 OpenAI Agents SDK 의 `needs_approval` 은 run 을 저장해 두고 멈췄다가 승인 뒤에 tool 을 실행한다. 이 저장소는 run 을 프로세스 메모리에만 두고(`--workers 1`) 초안도 남기지 않기로 해서 이 방식과 맞지 않는다. Claude Code 의 Edit 은 승인 전에 diff 를 만들어 보여 주고, 적용할 때 파일이 그 사이 바뀌었으면 거부한다. 지금 구조가 이쪽이고, 마지막 확인만 없다.

---

## 2. 할 일

2-1 ~ 2-4 는 DB 저장과 제출 API 보다 먼저 끝나야 한다. 2-5 는 선행이 없고 안전 쪽 빈자리라 가장 먼저 하는 걸 권한다.

### 2-1. 저장소 포트에 아이 범위를 넣는다 (1-2)

포트 메서드마다 `child_id` 인자를 더하는 방법과, 저장소가 만들어질 때 아이를 받아 모든 조회에 거는 방법이 있다. 뒤의 것을 권한다. tool 핸들러가 바뀌지 않고, 핸들러를 새로 쓰는 사람이 인자를 빠뜨릴 자리가 없다.

- `MemoryStore` docstring 에 "모든 id 조회는 이 저장소의 아이로 거른다"를 계약으로 적는다. 어댑터는 `app/api` 가 만들어서(공통규약 §1) BE 가 읽는 계약이 이 docstring 이다.
- `InMemoryStore` 도 같은 규칙으로 거른다. 지금 `get_event` 는 id 만 본다([inmemory.py:174-175](apps/api/app/agents/memory/store/inmemory.py#L174-L175)). `EventRow` 에 아이가 없어서([ports.py:62-68](apps/api/app/agents/memory/store/ports.py#L62-L68)) 행에 아이를 달아야 한다.
- 테스트는 다른 아이의 행을 시드하고, `update_event` 가 `UNKNOWN_EVENT` 로 끝나는지와 관찰 수정·삭제 tool 이 대상 없음으로 끝나는지 본다. DB 어댑터도 같은 테스트를 통과해야 한다.
- Food · Activity 포트도 같은 기준으로 훑는다. 이번에는 확인하지 않았다.

### 2-2. tool 인자에 신원 필드가 없다는 걸 테스트로 건다 (1-1)

지금은 docstring 으로만 지킨다([context.py:3-4](apps/api/app/agents/memory/context.py#L3-L4), [schemas/observation.py:4](apps/api/app/agents/memory/schemas/observation.py#L4), [schemas/schedule.py:4](apps/api/app/agents/memory/schemas/schedule.py#L4)). Agent 스키마 파일을 훑어 보니 지금은 그런 필드가 없다. 비슷한 테스트는 Food 저장소 dataclass 에 대한 것 하나뿐이다([test_food_store.py:210](apps/api/tests/unit/agents/food/test_food_store.py#L210)).

각 Agent 의 tool 스펙을 꺼내 `properties`(중첩 포함)에 `child_id` · `parent_id` · `source_writer` · `created_by` 가 있으면 실패하는 테스트를 `tests/unit/agents/common/` 에 하나 둔다. Agent 가 늘어나도 테스트를 고칠 필요가 없다.

### 2-3. 시각 규칙을 `app/rules/` 로 옮긴다 (1-3)

제출 API 는 보호자가 시트에서 고친 시각도 `check_when` 으로 다시 검증해야 한다(일정 초안 흐름 §4-2). 그런데 `app/api` 는 `app/agents` 를 진입점으로만 본다. `datetime_rules.py` 는 표준 라이브러리만 import 해서([datetime_rules.py:16-19](apps/api/app/agents/common/datetime_rules.py#L16-L19)) `app/rules/` 의 허용 범위 안에 있다. 파트 리드는 `app/rules/` 를 리뷰 없이 고칠 수 있다(공통_구현_계획 K-4).

`prepared_at_for`([schedule.py:470-480](apps/api/app/agents/memory/tools/schedule.py#L470-L480))도 같이 옮긴다. `PATCH /event-items/{iid}` 가 같은 규칙을 써야 한다는 TODO 가 이미 붙어 있다.

### 2-4. 제출 API 의 비교 규칙을 BE 와 정한다 (1-3)

9/21 회의에서 "낡은 초안은 현상유지, 잠금은 나중에"로 정한 결정을 다시 여는 일이라 BE(김명성)와 회의에서 정한다. 제출 API 의 요청 스키마가 아직 미정이라 지금 꺼내는 게 가장 싸다.

Agent 쪽에서 들고 갈 안은 이렇다.

- PATCH 요청에 `before` 를 같이 받는다. `event` 테이블에 `updated_at` 이 없어서 버전 컬럼을 새로 만드는 대신 `before` 를 비교 기준으로 쓴다.
- 비교하는 것은 `event` 의 여섯 필드와 준비물의 `(item_id, item_name)` 목록이다. 다르면 409.
- 시각은 문자열이 아니라 시점으로 비교한다. `before` 는 DB 에서 읽어 `+00:00` 으로, `event` 는 `+09:00` 으로 나간다([event_draft.py:14](apps/api/app/core/event_draft.py#L14)).
- `is_prepared` 는 비교하지 않는다. payload 에 없으니 보호자가 그 사이 체크를 눌러도 409 가 나지 않는다.
- 비교 함수는 2-3 과 같이 `app/rules/` 에 둔다.
- `event_item.updated_at` 을 CAS 토큰으로 쓰려는 #76 과 방식을 맞춘다([schedule/models.py:82-88](apps/api/app/domains/schedule/models.py#L82-L88)).

같이 할 것으로 `UpdateEventDraft.before` 를 필수로 바꾼다([event_draft.py:67](apps/api/app/core/event_draft.py#L67)). 지금 타입은 `EventBefore | None` 인데 update 경로에서 `None` 이 나오는 길은 없다. `current_draft` 가 DB 행에서 늘 채우고([schedule.py:226-261](apps/api/app/agents/memory/tools/schedule.py#L226-L261)) 버퍼의 초안도 그 값을 이어받는다. `None` 을 허용해 두면 제출 API 에 비교를 건너뛰는 분기가 생기고, 나중에 붙는 제안·OCR 경로가 `before` 없이 통과한다.

### 2-5. 응급 신호를 규칙으로 잡는다

`_safety_precheck` 는 아무것도 하지 않는다([pipeline.py:750](apps/api/app/agents/pipeline.py#L750), 호출은 [pipeline.py:339](apps/api/app/agents/pipeline.py#L339)). Supervisor 의 `guarded` 에도 응급 라벨이 없다. 알레르기 등록과 진단 문의 둘뿐이다([supervisor/prompt.py:52-57](apps/api/app/agents/supervisor/prompt.py#L52-L57)). 그래서 지금은 응급 신호를 아무 데서도 잡지 않는다. "증상은 잘라서 record 로 보낸다"는 프롬프트 규칙을 따르면 "숨을 못 쉬어"는 관찰 기록으로 갈 것이다. 실제로 돌려 보지는 않았다.

루트 §4 와 공통규약 §9 는 규칙이 Agent 라우팅보다 먼저 잡도록 정했다. Health 명세도 응급 신호는 Agent 를 거치면 늦다고 적었다([Health_Agent_명세.md:70](docs/agents/health/Health_Agent_명세.md)). health_plan S10 에 잡혀 있지만 Health 가 올리는 순서의 마지막이라 그때까지 비어 있게 된다.

최소 응급 키워드 규칙과 고정 안내 문구를 `app/rules/` 에 먼저 넣고, `emergency_sign` 행이 생기면 health_plan 의 1:1 검사로 바꾸는 걸 권한다.

### 2-6. 생일 기본값을 없앤다

`birth_date` 를 못 받으면 만 2세로 가정하고 Food 를 연다([entrypoint.py:74-76](apps/api/app/agents/entrypoint.py#L74-L76), [entrypoint.py:120](apps/api/app/agents/entrypoint.py#L120)). 아이 정보를 실제로 읽기 전까지 쓰는 임시값이다. 월령으로 식이 단계와 tool 묶음이 갈리니, 실제 아이 정보가 붙은 뒤에는 생일을 못 읽은 run 에서 Food 를 돌리지 않아야 한다. 루트 §2 가 알레르기 조회 실패에 "기본값으로 넘기지 말 것"이라고 한 것과 같은 이유다.

[entrypoint.py:119](apps/api/app/agents/entrypoint.py#L119) 의 TODO 는 FoodPorts 교체만 적고 있다. fallback 상수도 같이 지운다는 걸 TODO 에 더해 두면 9단계에서 빠뜨리지 않는다.

### 2-7. partial은 추천 카드보다 먼저 열었다 

처음에는 카드와 `Partial` 을 같은 PR 에서 열려고 했다. #196 멘토 리뷰(5번)에서 끝난 결과부터 보여 주고 추천이 빠진 이유를 안내하자는 의견을 받아, #202 에서 `Partial` 만 먼저 화면으로 보낸다([translate.py:75](apps/api/app/api/runs/translate.py#L75)). 문구는 모델이 아니라 `PARTIAL_MESSAGES` 코드 상수다([translate.py:44](apps/api/app/api/runs/translate.py#L44)). pipeline 은 도메인 단계가 다 끝나거나 20초가 된 뒤 `Partial` 을 한 번 낸다([pipeline.py:682](apps/api/app/agents/pipeline.py#L682)).

그래서 카드가 붙기 전까지는 앞서 걱정한 것과 반대 구간이 생긴다. 실패한 Agent 는 `partial` 로 화면에 보이고, 성공한 Agent 의 추천은 아직 나가지 않는다(`DomainRouted` 는 로그용이다). 루트 §2 의 "성공과 실패를 한 화면에"는 카드가 붙어야 다 성립한다. 카드 번역은 그대로 공통_구현_계획 §4-3 의 채널 처리 때 `translate.py` 를 가진 BE 와 같이 연다.

남은 것은 화면 문구다. 부분 결과 카드는 저장된 게 없어도 "아래 결과는 그대로 저장됐어요" 를 띄운다([run-result.tsx:217-224](apps/web/src/components/run-result.tsx#L217-L224)). 순수 요청형 run 이나 급식 수정만 실패한 run 에서는 틀린 말이라, 서버 `message` 를 쓰거나 저장 여부로 조건을 거는 걸 FE(고태영)와 정한다.
