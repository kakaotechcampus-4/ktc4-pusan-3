# 스위트 F — 이슈 초안

[`faults.md`](faults.md) 에서 나온 이슈 초안이다. **아직 올리지 않았다** — 담당자 · PM 에게 보여 주고 올린다. 번호를 받으면 `ISSUE-…` 를 `#번호` 로 바꾼다(이 파일 · faults.md · 테스트 docstring · xfail reason).
형식은 CONTRIBUTING §4 — 제목 `[<PART> <Type>] 요약`, 본문 목적 · 현재 문제 · 작업 범위 · 완료 기준 · 검증 방법. PART 는 바꿀 파일의 폴더로 정한다(`app/api` · `domains` · `infra` · `rules` → BE, `app/agents` · `providers` → AI).

| id | 제목 | 칸 | 담당 (제안) | 테스트 |
| --- | --- | --- | --- | --- |
| ISSUE-F7 | [BE Fix] 멱등 재생 값이 깨져 있으면 같은 키 재시도가 24시간 500 이다 | IDEM-C | 명성 | `test_f7_broken_replay.py` (xfail) |
| ISSUE-F8-a | [BE Chore] DB 쿼리 · 잠금 대기 시간 상한(statement_timeout · lock_timeout) 정하기 | CUR-B · SAVE-B · SAFE-B | 명성 | 없음 — 설정 결정이 먼저 |
| ISSUE-F8-b | [BE Refactor] Curator 가 아이 잠금을 쥔 채 임베딩 · 판정 모델을 부른다 | CUR-B | 시하 (명성 협의 — `app/domains` ↔ `app/agents`) | 없음 — 설계 결정이 먼저 |
| ISSUE-F2-UX | [AI Feat] 모델이 멈추면 보호자가 60초를 기다린 뒤 실패를 본다 — Supervisor 상한 여부 결정 | SUP-B · MEM-B | 시하 · PM 결정 | `test_f2_stuck_supervisor_memory.py` 가 지금 동작을 고정 |

---

## ISSUE-F7 — [BE Fix] 멱등 재생 값이 깨져 있으면 같은 키 재시도가 24시간 500 이다

**목적** — Redis 에 깨진 재생 값이 있어도 같은 키 재시도가 처리 안 된 예외(500)가 아니라 정해진 답을 받게 한다.

**현재 문제**
- `app/api/idempotency.py:83` 이 Redis 값을 `json.loads` 로 바로 읽는다. 값이 깨져 있으면 `JSONDecodeError` 가 그대로 올라가 마지막 그물(`app/api/errors.py:199-209`)이 500 `internal_error` 를 낸다.
- 키 TTL 이 24시간(`_TTL_SECONDS`)이라 그동안 그 보호자가 같은 키로 다시 보내면 매번 500 이다. 화면은 같은 키로 재시도한다.
- 부르는 창구: `app/api/v1/routers/children.py:294` · `:469` (한 줄 입력), `delete_proposals.py:130` · `:140`, `health_safety.py:125` · `:214`.
- 깨진 값이 생기는 길 — 값 형식을 바꾼 배포 · 수동 편집 · 다른 프로그램이 같은 키 이름을 쓴 경우. 흔하지 않지만 생기면 24시간 막힌다.

**작업 범위** — 깨진 값을 어떻게 답할지 정하고 `recall` 에 반영한다. 두 안 중 하나:
1. Redis 장애와 같게 503 `temporarily_unavailable` + 깨진 키를 지운다 — 다음 재시도는 새 요청이 된다.
2. 깨진 키를 지우고 바로 새 요청으로 처리한다 — 처음 요청이 저장까지 끝냈는지 모르므로 두 번 저장될 수 있다.
로그에는 지금처럼 키 대신 오류 종류만 남긴다.

**완료 기준** — `apps/api/tests/eval/faults/test_f7_broken_replay.py` 의 `xfail` 마커를 지우고 통과한다 (마커를 남기면 strict 라 XPASS 로 실패한다).

**검증 방법** — `cd apps/api && uv run pytest tests/eval/faults/test_f7_broken_replay.py` · 기존 `tests/unit/api/test_idempotency_redis.py` 통과.

---

## ISSUE-F8-a — [BE Chore] DB 쿼리 · 잠금 대기 시간 상한(statement_timeout · lock_timeout) 정하기

**목적** — DB 가 느리거나 잠금을 오래 기다릴 때 끝이 있게 한다.

**현재 문제**
- `app/infra/db/session.py:18-20` 의 엔진에 `statement_timeout` · `lock_timeout` 이 없다. 저장소 전체(`deploy/` 포함)에서 0건이다.
- 입력 run 은 러너 60초 안전망(`app/api/runs/runner.py:211`)이 끊고, 도메인 Agent 안의 조회(안전 정보 · 기억)는 20초 예산이 끊는다. 그 밖 — API 핸들러 · 백그라운드 Curator · 배치(`app/workers/`) — 에는 상한이 없다.
- 아이 잠금(`pg_advisory_xact_lock`, `app/domains/memory/profile/service.py:55-67`)을 기다리는 쪽도 끝없이 기다린다 (ISSUE-F8-b 와 같이 본다).

**작업 범위** — 값을 정하고(실측 뒤 — API 세션과 배치 세션을 따로 둘지 포함), 거는 방식(엔진 `connect_args` 의 서버 설정 또는 세션마다 `SET LOCAL`)을 정해 넣는다. 상한에 걸린 예외가 기존 DB 오류 처리기(원문 없는 로그)로 가는지 맞춘다.

**완료 기준** — 상한 값이 설정에 있고, 상한을 넘긴 쿼리가 정해진 오류로 끝나는 통합 테스트 1개 (`icatch_it` 에서 `pg_sleep` 으로).

**검증 방법** — 통합 테스트 · 배포 compose 에서 설정 값 확인.

---

## ISSUE-F8-b — [BE Refactor] Curator 가 아이 잠금을 쥔 채 임베딩 · 판정 모델을 부른다

**목적** — Curator 가 느린 모델 때문에 같은 아이의 다른 저장을 오래 막지 않게 한다.

**현재 문제**
- `app/domains/memory/curator/trigger.py:49` 에서 아이 잠금을 잡고, `:61` 의 `link_observations` 안에서 임베딩(`embedder.embed`)과 판정(`judge.judge`, 관찰마다)을 부른다. 한 호출 상한은 `LLM_TIMEOUT_S` 60초 · SDK 재시도 최대 2번(`app/core/agent_config.py:80-81`)이고, 실행 전체 상한은 없다.
- 그동안 같은 아이의 관찰 고치기 · 지우기(`app/domains/memory/store/db_store.py:192`)와 commit 직전 기억 재계산이 잠금을 기다린다. 입력 run 이면 러너 60초 안전망에 걸려 `failed(timeout)` 이 된다.
- 잠금을 쥔 시간은 이미 로그로 남는다(#636, `trigger.py:79-87` 의 `held_ms`) — 근거로 쓸 수 있다.

**작업 범위** — `held_ms` 분포를 먼저 본 뒤 둘 중 하나:
1. 모델 호출(임베딩 · 판정)을 잠금 밖에서 하고, 연결 · 재계산만 잠금 안에서 한다.
2. Curator 실행 전체에 상한을 두고 넘으면 rollback 한다 — 연결 안 된 관찰은 다음 실행이 다시 집는다(지금도 실패하면 그렇게 된다).
`app/domains` 와 `app/agents/curator` 를 같이 건드리므로 두 담당 협의 대상이다(루트 CLAUDE.md §6).

**완료 기준** — 느린 판정기 가짜로 "잠금을 쥔 시간이 정한 상한을 넘지 않는다" 를 보는 테스트 1개.

**검증 방법** — 그 테스트 · `tests/integration/test_trigger_curator_background.py` 통과.

---

## ISSUE-F2-UX — [AI Feat] 모델이 멈추면 보호자가 60초를 기다린 뒤 실패를 본다 — Supervisor 상한 여부 결정

**목적** — 모델이 멈출 때 보호자가 진행 화면을 60초 본 뒤 실패를 받는 것을 그대로 둘지 정한다.

**현재 문제**
- Supervisor · Memory 는 20초 예산(`app/agents/pipeline.py:97-100`)에 끊지 않는다 — "저장이 반만 남지 않게" 한 의도된 설계다.
- SDK 는 한 호출을 60초까지, 재시도까지 더 기다린다(`app/core/agent_config.py:80-81`). 그래서 모델이 멈추면 러너 60초 안전망(`app/api/runs/runner.py:45`)이 끊어 `failed(timeout)` 으로 끝난다.
- 화면의 20초 무응답 타이머는 10초마다 오는 ping(`app/api/runs/sse.py:36`)이 되살려서, 보호자는 진행 화면을 60초 본다.
- 지금 동작은 `apps/api/tests/eval/faults/test_f2_stuck_supervisor_memory.py` 가 고정한다 (commit 없음 · 멱등 키 풀림은 지킨다).

**결정할 것** (하나씩)
1. Supervisor 에 짧은 상한(N초)을 두고, 넘으면 지금의 강등 경로로 보낼지 — Supervisor 가 실패하면 Memory 가 원문 전체로 기록한다(`pipeline.py:703-705` · `app/agents/supervisor/agent.py:123-124`). Supervisor 는 아무것도 저장하지 않아 끊어도 반쪽 기록이 없다.
2. Memory 는 그대로 둘지 — 코드로 보면 Memory 의 쓰기는 run 세션에 있고 commit 은 Memory 가 끝난 뒤 한 번이라(`runner.py:189-201`), commit 전에 끊기면 세션이 닫히며 되돌려진다. "반만 남는다" 는 이유가 지금 구조에서도 맞는지 확인을 부탁한다.
3. 60초 안전망에 걸린 `failed(timeout)` 의 화면 문구 · 다시 시도 안내를 바꿀지 (FE · PM).

**작업 범위** — 결정 1 이 "예" 면 pipeline 에 Supervisor 상한 상수를 두고 강등 경로 테스트를 더한다. 그때 F2 테스트의 supervisor 갈래 기대를 "N초 뒤 강등 → Memory 저장" 으로 바꾼다.

**완료 기준** — 결정이 `docs/` 기능 문서에 남는다. 결정 1 이 "예" 면 Supervisor 가 멈춰도 N초 뒤 Memory 가 저장하는 테스트가 통과한다.

**검증 방법** — `cd apps/api && uv run pytest tests/eval/faults/test_f2_stuck_supervisor_memory.py`.
