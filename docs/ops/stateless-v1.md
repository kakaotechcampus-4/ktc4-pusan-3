# 프로세스 메모리 상태를 DB 로 — v1

> 이슈 #877 · 작성 김명성 · 2026-11-09
> 상태: **1단계 설계 승인(#887) · 구현 PR 리뷰 중**. 2~5단계는 단계마다 이 문서에 절을 더한다.

## 왜

api 는 `--workers 1` 전제(#134)다. 되묻기 맥락 · 다시 시도 맥락 · 사진 결과 · run 채널 · 하루 입력 횟수 ·
초대 실패 횟수를 프로세스 메모리에 둬서, 워커를 늘리면 다음 요청이 맥락 없는 워커로 가 결과가 달라진다.
워커를 늘리기 전에 이 상태를 워커 밖으로 옮긴다.

## 결정 — 전부 PostgreSQL (A 안, #877)

Redis 와 비교한 표는 #877 본문에 있다. 고른 이유만 남긴다.

- 새 장애 지점이 없다. 지금 Redis 가 죽으면 idempotency 만 503 인데, 옮기면 되묻기 · 한도까지 같이 멈춘다
- Redis 클라이언트가 동기라 요청마다 부르려면 async 전환과 별도 예외 처리가 따라온다. DB 는 asyncpg 를 이미 쓴다
- 테스트가 실제 Postgres 로 돈다(`make test`). Redis 경로는 CI 에서 돌지 않는다
- 이 트래픽에서는 Redis 의 속도 이점이 드러나지 않는다

idempotency 는 이미 Redis 이고 동작하므로 옮기지 않는다.

## 단계

`--workers` 는 5단계까지 1 이다. 중간 PR 이 각자 배포돼도 동작이 같다.

1. 보관함 테이블 + 되묻기 맥락(`pending_reply`) — **이 문서의 범위**
2. 다시 시도 · 사진 run (`suggestion_retry` · `photo_run`) — 1단계 테이블에 얹는다
3. 카운터 (`quota` · `invite_attempts`)
4. run 채널(SSE) — 이벤트 테이블 + 짧은 주기 읽기. LISTEN/NOTIFY 는 워커마다 전용 연결이 필요하고 내용이 8,000바이트까지라 뒤로 미룬다
5. 워커 수 올리기 · `#134` 주석 정리 · 외부 API 일 한도 점검

---

## 1단계 — 되묻기 맥락을 테이블로

### 지켜야 하는 동작 (지금과 같아야 한다)

`app/api/runs/pending_reply.py` 머리말과 `docs/safety/retention-v1.md` 36행이 정한 것이다. 바꾸지 않는다.

- 넣은 뒤 **15분**. 처음 넣은 시각부터 센다 — "사용 중" 을 풀어도 다시 세지 않는다 (#564)
- 꺼낼 수 있는 것은 **넣은 보호자 · 같은 아이**뿐. 없음 · 남의 것 · 다른 아이 · 사용 중 · 만료를 밖에 구분해 알리지 않는다
- 답이 오면 지우지 않고 "사용 중". 이어받기 run 이 저장까지 가면 지우고, 저장 전에 실패하면 표시만 푼다
- **같은 질문에 거의 동시에 온 두 답 중 하나만** 받는다
- 하루 한도(429)에 걸려도 맥락은 남는다
- 탈퇴 · 그 아이와 연결 끊기 · 아이 삭제 때 지체 없이 지운다 (등록 보호자 탈퇴로 아이가 지워지면 함께 보던 보호자의 것도)
- 원문 조각이 들어 있다 — 로그에 내용을 남기지 않는다

### 테이블

2단계(다시 시도 · 사진)도 같은 테이블에 담을 수 있게 `kind` 로 나눈다.

| 칸 | 타입 | 뜻 |
| --- | --- | --- |
| `kind` | text, CHECK (`'pending_reply'`) | 무엇의 보관함인가. 2단계에서 CHECK 에 값을 더한다(`# expand-ok:`) |
| `key` | text | `pending_reply` 는 run_id |
| `parent_id` | uuid, FK `parent` **ON DELETE CASCADE** | 넣은 보호자 |
| `child_id` | uuid, FK `child` **ON DELETE CASCADE** | 그 아이 |
| `payload` | jsonb | 맥락. 모양은 kind 마다 다르다 |
| `expires_at` | timestamptz | 넣은 시각 + 15분. 넣을 때 정하고 바꾸지 않는다 |
| `in_use` | boolean, 기본 false | 답을 받아 이어받기 run 이 쓰는 중 |

- PK `(kind, key)`
- 인덱스 `parent_id` · `child_id`(CASCADE 가 찾는다 — Postgres 는 FK 쪽 인덱스를 만들어 주지 않는다) · `expires_at`(만료 삭제)
- **UNLOGGED** 로 만든다 (#887 리뷰 동의 · 복제본이 없어 잃는 것이 없다). 🚨 autogenerate 로는 안 붙는다 — 마이그레이션의 `op.create_table` 에 `prefixes=["UNLOGGED"]` 를 직접 적는다. DB 가 비정상 종료되면 비워지는데, 지금 메모리 보관함과 같은 동작이다. WAL 에 원문이 남지 않는다. 일반 테이블을 참조하는 FK 는 UNLOGGED 에서도 걸 수 있다
- 시각은 DB 의 `now()` 기준이다. 워커마다 시계가 달라도 판정이 같다

### 코드 배치

| 무엇 | 어디 | 비고 |
| --- | --- | --- |
| 모델 · 저장 함수 | `app/domains/run_state/` (새 폴더) | `payload` 는 dict 로만 다룬다 — domains 는 agents 를 import 하지 않는다 |
| 맥락 ↔ JSON 변환 | `app/api/runs/pending_reply.py` | pydantic `TypeAdapter(PendingMemoryContext)` 로 바꾸고 되돌린다. agents 코드는 고치지 않는다. `DomainPending.payload` 는 이미 "JSON 으로 바뀌는 값만" 이 규칙이다(`agents/common/result.py` · 날짜는 `isoformat()` 으로 싣는다) |
| 마이그레이션 | 새 Alembic revision | 새 테이블만 — expand-only 문제 없음 |

`pending_reply.py` 의 함수 이름(`put` · `get` · `claim` · `release` · `drop` · `forget_*`)은 그대로 두고 **async 로 바뀐다**.
부르는 곳(아래)이 `await` 을 붙인다. 함수마다 **자기 세션을 열고 바로 commit 하는 짧은 트랜잭션**이다 —
요청 트랜잭션과 섞지 않는다. 섞으면 아이 삭제(아이 행 `FOR UPDATE`)가 FK 잠금(`FOR KEY SHARE`) 때문에 요청이 끝날 때까지 기다린다.
예외는 연결 끊기 하나다(아래 "지우기").

세션을 여는 함수는 모듈 변수로 두고 테스트가 바꿔 끼운다 (작성자 판단 · `test_trigger_curator_background.py` 와 같은 방식).
자기 세션을 바로 commit 하면 `db_client` 의 바깥 트랜잭션 밖이라 테스트가 만든 보호자 · 아이 행을 못 보고 FK 오류가 난다.
- DB 테스트: 테스트 연결에 묶인 세션을 끼운다
- DB 를 안 쓰는 단위 테스트(`test_runner.py` · `test_create_input_consent.py` · `test_chat_context.py` 등): `tests/unit` conftest 의 메모리 가짜 보관함 fixture 를 끼운다

### 동시에 온 두 답 — DB 가 하나만 통과시킨다

지금은 `children.py` 의 `get` 과 `claim` 사이에 await 가 없다는 것에 기댄다. 워커가 둘이면 성립하지 않는다.

- `claim` 은 한 문장: `UPDATE … SET in_use = true WHERE kind · key · parent_id · child_id 일치 AND in_use = false AND expires_at > now() RETURNING payload`. 행이 안 나오면 None
- 순서를 바꾼다. 지금: `get`(확인) → 하루 한도 → `claim`. 바뀐 뒤: **`claim` → 하루 한도 → 러너 시작**. 맥락이 없으면 한도를 쓰기 전에 400 으로 끝나는 것은 지금과 같다
- `claim` 부터 러너 시작까지를 `try` 로 감싸 **어떤 예외든 `release`** 한다 — 429 만 풀면 3단계에서 `quota.consume` 이 DB 오류로 터질 때 15분 동안 "사용 중" 으로 남는다 (#887 리뷰)
- `children.py` 의 "get 과 consume 사이에 await 를 넣지 말 것" 주석들은 더 이상 맞지 않으니 같이 정리한다
- `idempotency.remember` 실패 시 `release` 하는 기존 처리는 위 `try` 에 합쳐진다

### 넣기 — 질문 카드 · 끝 신호보다 먼저

`runner.py` 의 `emit` 은 동기 콜백이라 그 자리에서 DB 를 await 할 수 없다.

- `PendingReply` 를 받으면 저장 태스크를 띄운다. **저장이 도는 동안 나온 이벤트는 잡아 뒀다가, 저장이 끝나면 원래 순서대로 낸다.** 질문 카드(`AgentQuestion`)가 보이면 보호자가 바로 답할 수 있어서, 끝 신호만 잡아 두면 모자란다 (기존 테스트 `test_답을_받는_질문은_보내기_전에_맥락이_들어간다` 가 이것을 지킨다). 저장 전에 나온 이벤트는 기다리지 않는다
- 🚨 끝 신호를 따로 띄운 태스크에서만 내면 안 된다 — `run()` 이 먼저 끝나 `_guarded` 가 끝 신호가 없다고 보고 failed 를 붙인다. `run()` 이 `handle_input` 뒤(`RUN_DEADLINE_SECONDS` 시간 제한 안)에 저장을 기다린다 (#887 리뷰)
- 저장이 실패하면(DB 오류) run 은 그대로 끝낸다. 그 질문에 답하면 400 이 나는 것으로 끝난다. 로그에는 run_id 와 오류 종류만
- **지운 아이 앞으로 늦게 온 저장**은 FK 오류가 난다 — `pending_reply.put` 이 False 를 돌려주고 조용히 버린다. run 실패 · 알림으로 만들지 않는다. 지금은 메모리에 15분 남는 빈틈이었는데 이게 막는다
- **알려진 틈** — Memory 가 되물을 때 pipeline 은 질문 메모(`MemoryNote`)를 먼저, 맥락(`PendingReply`)을 나중에 낸다(`agents/pipeline.py` `_send_reply` 끝). 러너는 맥락보다 앞선 이벤트를 붙잡을 수 없어서, DB 왕복(수 ms) 동안 질문이 먼저 보일 수 있다. 사람이 그 사이에 답을 보낼 수는 없어 두었다. 순서를 바꾸면(도메인 Agent 의 `_send_question` 처럼 맥락 먼저) 틈이 없어진다 — agents 쪽 제안 거리

### 꺼내기 · 정리

- `get` 은 `claim` 과 같은 조건의 SELECT
- `chat.py` 의 `reply_open` 은 지금 턴마다 `get` 을 부른다. 그날 턴들의 run_id 를 한 번에 묻는 함수 하나로 바꾼다 (N 번 조회 방지)
- 러너 `finally` 의 `drop` · `release` 는 그대로 — async 가 될 뿐
- `drop` 은 지금처럼 run 이 끝난 뒤 따로 지운다. Memory commit 과 한 트랜잭션으로 묶지 않는다 (위 잠금 이유)

### 지우기

| 언제 | 어떻게 |
| --- | --- |
| 15분 지남 | 읽을 때 `expires_at > now()` 로 거른다. 실제 삭제는 api lifespan 의 작은 루프가 **1분마다** `DELETE … WHERE expires_at < now()` (워커마다 돌아도 무해). 처리방침이 "15분 안에 지운다" 라 하루 1회 정리 배치로는 모자란다 |
| 아이 삭제 | `child_id` FK CASCADE. `children.py` 의 `_forget_child_in_memory` 에서 `pending_reply` 줄을 뺀다 |
| 탈퇴 | `parent_id` FK CASCADE + 등록 아이가 지워지며 `child_id` CASCADE. `auth.py` `_forget_in_memory` 에서 `pending_reply` 를 뺀다 |
| 연결 끊기 | 보호자 · 아이가 둘 다 남아 CASCADE 가 안 걸린다. `child_parents.py` 에서 `unlink_parent` 와 **같은 트랜잭션**으로 `DELETE … WHERE parent_id = pid AND child_id = cid` — 커밋되면 같이, 실패하면 같이 되돌아간다 |

`suggestion_retry` · `photo_run` 의 메모리 삭제 줄은 2단계까지 그대로 둔다.

### 백업

`deploy/scripts/backup.sh`(#758)가 `pg_dump -Fc` 로 DB 전체를 매일 올려 28~30일 남긴다.
보관함은 복원해도 이미 만료라 1단계 PR 에서 `--exclude-table-data=<보관함 테이블>` 을 더한다. UNLOGGED 여도 `pg_dump` 는 내용을 넣으므로 필요하다.

**삭제 이력 트리거는 걸지 않는다** — 새 표는 삭제 트리거를 걸거나 `app/domains/deletion_log/models.py` 의 `NOT_LOGGED` 에
이유를 적어야 한다(#758, `apps/api/CLAUDE.md` Alembic 절). 이 표는 `NOT_LOGGED` 에 둔다: 백업에 내용이 없어 복원해도
되살아날 행이 없고, 15분마다 생기고 지워져 이력을 걸면 행만 쌓인다.

### 처리방침 — 결정됨(#877) · 새 판 대기

`docs/api/chat-v1.md` §5(10-06 "되묻기 맥락은 DB 로 옮기지 않는다") · 처리방침 2026-11-03 판 · `retention-v1.md` 36행이
"서버 메모리에만" 이라고 적었다. 보관 시간과 삭제 시점은 그대로고 **보관 장소**와 **백업 제외**만 바뀐다 — #877 에서 정했다.

- 처리방침 새 판은 #166 항목으로 jjaa0803 이 쓴다
- `chat-v1.md` · `retention-v1.md` 는 1단계 구현 PR 에서 고친다
- **1단계 구현 PR 은 열고 리뷰를 받되, 처리방침 새 판이 나온 뒤에 develop 에 머지한다** (develop push 는 dev 에 바로 배포되고 멘토 PR 로 prod 에 실린다)

### 검증

| 무엇 | 어떻게 |
| --- | --- |
| 마이그레이션 | `alembic upgrade head` → `downgrade -1` → `upgrade head`, `python -m scripts.check_expand_only` |
| 지금 동작 유지 | `tests/unit/api/test_pending_reply.py` 의 판정(남의 것 · 다른 아이 · 사용 중 · 15분 · 표시 풀어도 15분 유지)을 DB 테스트로 옮겨 그대로 통과 |
| 동시에 두 답 | 세션 두 개로 같은 run_id 를 동시에 `claim` — 하나만 payload 를 받는다 (`test_create_input_consent.py` 의 같은 시험을 이 방식으로 바꾼다) |
| 변환 | 모든 칸(`found` · `resume` · `asked` · `transcript` 포함)을 채운 맥락을 넣고 꺼내 `==` |
| 지우기 | 아이 삭제 · 탈퇴 · 연결 끊기 뒤 행 0 (integration) · 지운 아이로 `put` 해도 예외가 밖으로 안 나온다 |
| 끝 신호 순서 | `PendingReply` 뒤 바로 done 을 내는 가짜 진입점으로, done 을 받은 시점에 `get` 이 맥락을 돌려준다 |
| 전체 | `make test` · `make lint`, 로컬에서 되묻기 → 답하기 한 번 |

`--workers 2` 로 띄워 보는 확인은 4단계(SSE) 뒤에 의미가 있다 — 그 전에는 화면 연결이 워커에 묶여 있다.

### 이번 범위 밖

- `suggestion_retry` · `photo_run` (2단계) · 카운터 (3단계) · run 채널 (4단계)
- `MAX_QUESTIONS` 같은 되묻기 규칙 — 바꾸지 않는다

### 구현자에게 맡기는 것

- 새 폴더 · 테이블 · 함수의 이름
- 만료 삭제 루프의 위치(lifespan 안 · 별도 모듈)와 실패 시 로그 수준 — 단 로그에 내용 없이
- 저장 태스크를 끝 신호 전에 기다리는 구체적인 자리(`emit` 이 끝 신호를 받을 때 · `run` 의 마무리)
