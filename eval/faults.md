# 장애 점검표 — 스위트 F

설계 · 판정 기준 · 우선순위는 [`docs/eval/eval-v1.md` §6](../docs/eval/eval-v1.md). 여기는 칸마다 **기대 동작 · 실제 코드 위치 · 판정하는 테스트 · 상태** 만 둔다.

- 조사 기준 커밋 `9ef4fc52` (develop `99822b1d` 위 eval 설계 커밋). 코드 줄 번호는 그 커밋 기준이다 — 줄이 밀리면 함수 이름으로 찾는다.
- 경로는 `apps/api/` 기준. 테스트는 pytest 노드 id 모양(`파일::클래스::이름`)이고, `[...]` 는 parametrize 한 갈래다.
- 전부 가짜 모델로 결정적이라 `make test` 에 들어간다. **(통합)** 표시만 통합 DB(`icatch_it`)가 있어야 돈다.
- **Health 는 기록만 본다 (11-07 팀 결정).** 앱에서 Health 를 막아 두기로 해서, 증상 · 건강 관찰 저장만 점검하고 복약 알림(복약 초안 · `health_medication*` 포트) · 병원 찾기(`health_places` · 국립중앙의료원 어댑터)는 판정하지 않는다 — eval-v1 §13-6 · 맨 아래 "범위 밖" 절.
- 코드가 기대와 다르게 움직이는 칸은 `xfail(strict=True)` 로 걸고 이슈 초안을 [`faults-issues.md`](faults-issues.md) 에 둔다. 이 점검이 고치지는 않는다.

상태 — ✅ 판정하는 테스트가 있다 · 🕳️ 구멍 (판정 테스트 없음, F 번호가 메운다) · ⚠️ 기대와 달라 xfail 로 걸어 둠

## 행 — 부품 10개

스펙 §6 의 부품 이름은 12개다(도메인 Agent 4종을 한 행으로 셀 때). 40칸을 맞추려고 둘씩 묶은 곳이 두 군데다.

| 행 | 부품 | 묶은 것 |
| --- | --- | --- |
| SUP | Supervisor | |
| MEM | Memory | |
| DOM | 도메인 Agent 4종 | Food · Activity · Growth · Health — 실행 · 20초 · 부분 결과를 pipeline 한 곳이 다룬다. Health 는 기록만 (복약 · 병원 범위 밖) |
| CUR | Curator (판정 · 임베딩) | |
| SAFE | 알레르기 조회 | 안전 포트(`health_safety` 읽기) |
| SRCH | `memory.search` | 도메인 Agent 의 기억 읽기 포트 |
| EXT | 외부 API | 날씨 · 행사 · 도서 · 식약처 (병원은 범위 밖) |
| SAVE | 저장 (commit) | Memory commit · 추천 저장 · 도메인 쓰기 포트 |
| IDEM | 접수 창구 | **Redis(멱등 키) + 한도(하루 입력 · run 당 모델 호출 상한)** — 둘 다 run 을 시작하기 전 · 끝난 뒤의 문지기다 |
| RUN | 러너 · SSE | **러너(60초 안전망 · 끝 신호) + SSE(번역 · 프레임 · ping)** — 화면에 나가는 끝 신호 하나를 같이 지킨다 |

열 — **A** 예외 (5xx · 429 · 연결 · 인증) · **B** 멈춤 · 시간 초과 · **C** 형식 깨짐 (JSON · tool 미호출 · 스키마) · **D** 형식은 맞는데 틀림 (없는 근거 id · 알레르기 후보 · 상태 직접 쓰기 · 진단 문구)

## 한눈에

**11-08 조사: ✅ 29 · 🕳️ 11 · ⚠️ 0** (스펙의 "약 31칸" 보다 둘 적다 — 아래 "조사하며 바로잡은 것")

| | A 예외 | B 멈춤 | C 형식 깨짐 | D 맞는데 틀림 |
| --- | --- | --- | --- | --- |
| SUP | ✅ | ✅ F2 | ✅ | ✅ |
| MEM | ✅ F1 | ✅ F2 | ✅ | ✅ |
| DOM | ✅ F3 | ✅ | ✅ | ✅ |
| CUR | ✅ | 🕳️ F8 | ✅ | ✅ |
| SAFE | ✅ | ✅ | ✅ | ✅ |
| SRCH | ✅ F4 | ✅ | ✅ | ✅ |
| EXT | ✅ | ✅ | ✅ | ✅ |
| SAVE | ✅ | ✅ | 🕳️ F6 | ✅ |
| IDEM | ✅ | ✅ | 🕳️ F7 | 🕳️ F5 |
| RUN | ✅ F1 | ✅ F1 | ✅ | ✅ |

## 칸마다 공통으로 보는 것

칸별 표의 "기대 동작" 에는 그 칸에만 있는 것을 적는다. 아래 다섯은 모든 칸에 걸리고, 대부분은 한 곳의 코드가 구조로 막는다.

| 공통 점검 | 어디서 막나 | 판정하는 테스트 |
| --- | --- | --- |
| 끝 이벤트 1번 (I12) | 채널이 끝 신호 뒤 이벤트를 버린다 `app/api/runs/registry.py:59-73` · 끝 신호 없이 끝난 job 에 러너가 failed 를 붙인다 `app/api/runs/runner.py:395-402` | `tests/unit/api/test_run_registry.py::test_events_after_an_end_signal_are_dropped` · `tests/unit/api/test_runner.py::test_job_that_returns_without_an_end_signal_ends_with_failed` |
| 반쯤 저장된 기록 없음 | Memory 가 실패 · 미완료면 commit 하지 않는다 `app/agents/pipeline.py:740-754` · commit 안 된 run 세션은 러너가 닫으며 되돌린다 `app/api/runs/runner.py:189` | `tests/unit/agents/supervisor/test_pipeline.py::test_Memory_가_쓰다가_죽으면_commit_하지_않고_failed_로_끝난다` · `::test_Memory_가_반복_상한에_걸리면_commit_하지_않고_failed_로_끝난다` |
| 승인 없는 쓰기 없음 (I7) | 일정 초안은 저장하지 않는다 — 쓰기 길은 Memory tool · 도메인 쓰기 포트 · 추천 저장 셋뿐이고, 장애 경로가 새 쓰기를 만들지 않는다 | `tests/unit/agents/supervisor/test_pipeline.py::test_일정_초안만_만든_run_은_commit_된_쓰기가_없다` |
| 로그에 원문 없음 (I13) | 예외는 종류 · 위치만 남긴다 (`pipeline.py:1851-1857` · `runner.py:380-385` · `api/errors.py:199-209`) | `tests/unit/agents/supervisor/test_pipeline.py::test_로그에_원문도_조각도_메모도_없다` · `::test_Agent_예외_메시지는_로그에_남기지_않는다` · `tests/unit/api/test_runner.py::test_job_exception_log_keeps_type_and_place_but_not_message` · 장애 경로 셋은 F1 |
| 실패와 성공이 한 화면에 (NF-06) | 도메인 Agent 하나가 빠져도 나머지 결과 + `partial` `app/agents/pipeline.py:1881-1889` | `tests/unit/agents/supervisor/test_pipeline.py::test_RC16_한_Agent_가_죽어도_나머지_결과는_나간다` · 진짜 Agent 는 F3 |

## 칸별

### SUP · Supervisor

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| SUP-A | 입력 실패로 만들지 않는다. 강등(degraded)해 Memory 가 원문 전체로 기록한다. tool_choice 를 거절(400)하면 다음 값으로 물러선다(최대 3번). 로그엔 예외 종류만 | `app/agents/supervisor/agent.py:98` · `:121-124` · `app/agents/pipeline.py:703-705` | `tests/unit/agents/supervisor/test_pipeline.py::test_Supervisor_가_죽어도_입력은_실패가_아니다` · `tests/unit/agents/supervisor/test_supervisor_agent.py::test_LLM_오류는_예외를_올리지_않는다` · `::test_강제_호출을_거절하면_다음_값으로_물러선다` · `::test_전부_거절되면_LLM_오류로_끝낸다` (400 이 원문을 되돌려 줄 때의 로그는 F1 ③ 이 더 본다) | ✅ |
| SUP-B | 20초에 끊지 않는다(의도 — 저장이 반만 남지 않게). SDK 상한(60초 × 최대 3번, `app/core/agent_config.py:80-81`) 전에 러너 60초 안전망이 끊어 `failed(timeout)` 하나로 끝내고, commit 없음, 멱등 키를 푼다. 보호자가 60초를 기다리는 문제는 ISSUE-F2-UX | `app/agents/pipeline.py:97-100` · `app/api/runs/runner.py:45` · `:211` · `:242-253` · `:411-412` | 가짜 진입점으로만: `tests/unit/api/test_runner.py::test_agent_job_stops_a_run_past_the_deadline` · F 테스트: `tests/eval/faults/test_f2_stuck_supervisor_memory.py::test_F2_멈춘_Supervisor_Memory_는_러너_안전망이_failed_로_끝내고_키를_푼다[supervisor]` · `::test_F2_pipeline_은_Supervisor_와_Memory_를_20초에_끊지_않는다` | ✅ F2 |
| SUP-C | 재시도하지 않고 강등 — Memory 가 원문으로 돈다. 일부 조각만 깨지면 나머지로 간다. 로그엔 실패 코드 · 필드 경로만 | `app/agents/supervisor/agent.py:131-140` | `tests/unit/agents/supervisor/test_pipeline.py::test_Supervisor_출력이_깨져도_Memory_는_돈다` · `tests/unit/agents/supervisor/test_supervisor_agent.py::test_검증에_실패하면_error_로_돌려준다` · `::test_전부_깨지면_출력을_내지_않는다` | ✅ |
| SUP-D | 원문에 없는(의역한) 조각은 버린다. 요청을 기록으로 잘못 나눠 아무도 처리하지 않은 조각은 한 번만 다시 나눈다. 위험 물질은 모델 전에 규칙이 막는다 | `app/agents/supervisor/agent.py:157` · `app/agents/pipeline.py:829` · `:650-653` | `tests/unit/agents/supervisor/test_supervisor_agent.py::test_의역한_조각만_버리고_나머지로_간다` · `tests/unit/agents/supervisor/test_pipeline.py::test_T14_Memory_가_적지_않은_물음은_Supervisor_가_다시_나눠_Food_로_보낸다` · `::test_RC28_위험_물질은_사전검사가_막고_모델을_부르지_않는다` · `::test_Supervisor_의_hazard_라벨이면_Memory_를_부르지_않는다` | ✅ |

### MEM · Memory

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| MEM-A | 기본값으로 대체하지 않고 `failed(llm_unavailable)` — 원문은 화면으로 돌려준다. commit 없음 · 도메인 Agent 안 부름. 로그에 원문 · 제공처 오류 메시지(400 이 원문을 되돌려 줄 수 있다, `app/agents/common/llm_client.py:166-168`)가 없다 | `app/agents/pipeline.py:740-747` · `app/agents/memory/agent.py:438-440` | `tests/unit/agents/supervisor/test_pipeline.py::test_Memory_가_죽으면_입력을_그대로_돌려준다` · `::test_Memory_가_쓰다가_죽으면_commit_하지_않고_failed_로_끝난다` · `::test_이어받기에서_memory_가_실패하면_failed_로_끝난다` — 동작은 보지만 로그는 안 본다 · F 테스트: `tests/eval/faults/test_f1_log_leak.py::test_F1_Memory_모델_오류의_원문_되돌림이_로그에_없다` | ✅ F1 |
| MEM-B | SUP-B 와 같다 — 20초에 끊지 않고 러너 60초 안전망이 `failed(timeout)` 으로 끝낸다. commit 없음 · 키 풀림 | `app/agents/pipeline.py:97-100` · `app/api/runs/runner.py:242-253` | F 테스트: `tests/eval/faults/test_f2_stuck_supervisor_memory.py::test_F2_멈춘_Supervisor_Memory_는_러너_안전망이_failed_로_끝내고_키를_푼다[memory]` | ✅ F2 |
| MEM-C | 깨진 tool 인자는 실행하지 않고 모델에게 돌려준다. 빈 턴은 한 번 다시 묻는다. 7바퀴 상한이면 commit 없이 `failed(unparsable)`. tool 없이 "저장했어요" 라고만 하면 표시(Unwritten)한다 | `app/agents/memory/agent.py:636-644` · `app/agents/pipeline.py:750-754` · `:790-792` | `tests/unit/agents/memory/test_agent_loop.py::test_JSON_이_깨져도_루프가_죽지_않는다` · `::test_MAX_STEPS_를_넘기면_미완료로_알린다` · `::test_스키마와_맞지_않는_답은_message_로_둔다` · `tests/unit/agents/supervisor/test_pipeline.py::test_Memory_가_반복_상한에_걸리면_commit_하지_않고_failed_로_끝난다` · `::test_저장했다고_답하고_tool_을_안_부르면_표시한다` | ✅ |
| MEM-D | 알레르기 · 만성질환 · 아이 기록이 아닌 말 · 위험 물질은 저장 게이트(코드)가 거절한다. 알레르기는 LLM 이 쓰지 않고 등록 안내로 잇는다 (루트 §2) | `app/agents/memory/agent.py:1207` · `:1214-1218` · `:1236` · `app/agents/pipeline.py:800-805` | `tests/unit/agents/memory/test_agent_loop.py::test_알레르기_만성질환은_건강_관찰로_저장하지_않는다` · `::test_unclear_조각만_있으면_관찰_저장을_거절하고_이유를_돌려준다` · `tests/unit/agents/supervisor/test_pipeline.py::test_Supervisor_가_죽어도_위험_물질_관찰은_저장_게이트가_막는다` | ✅ |

### DOM · 도메인 Agent 4종

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| DOM-A | 그 Agent 만 빠지고(`partial` agent_error) 나머지 결과는 화면에 나간다. 기록은 남는다. 실패한 추천 task 는 다시 시도 맥락을 남긴다. 로그엔 예외 종류만. 공통 tool 루프는 모델 오류를 삼키지 않는다 | `app/agents/common/tool_loop.py:103-151` · `app/agents/pipeline.py:1847-1857` · `:1881-1889` | 가짜 실행 함수(`RuntimeError`)로만: `tests/unit/agents/supervisor/test_pipeline.py::test_RC16_한_Agent_가_죽어도_나머지_결과는_나간다` · `::test_Agent_예외_메시지는_로그에_남기지_않는다` · F 테스트: `tests/eval/faults/test_f3_real_agent_llm_error.py::test_F3_진짜_Agent_하나가_모델_오류를_내도_나머지_결과는_나간다[food]` · `[activity]` | ✅ F3 |
| DOM-B | 입력부터 20초 중 남은 몫만 주고 넘으면 끊는다(`partial` timeout_20s). 끊긴 Agent 는 뒤에서 이어 돌지 않는다 | `app/agents/pipeline.py:1698` · `:1916` · `:1835-1840` | `tests/unit/agents/supervisor/test_pipeline.py::test_RC16_20초를_넘긴_Agent_는_끊고_나머지로_끝낸다` · `::test_끊긴_Agent_는_뒤에서_계속_돌지_않는다` · `tests/unit/agents/common/test_tool_loop.py::TestCancel::test_모델을_기다리다_끊기면_결과를_내지_않고_시간_초과로_끝난다` | ✅ |
| DOM-C | 깨진 인자는 `INVALID_ARGS` 로 모델에게 돌려준다. 말로 끝내거나 왕복 상한이면 `status=failed` → 카드 없이 `partial` 로만 알린다 | `app/agents/common/tool_loop.py:150` · `:116` · `app/agents/pipeline.py:1860-1865` | `tests/unit/agents/common/test_tool_loop.py::TestEnding::test_모델이_tool_없이_말하면_출력_없이_끝난다` · `::TestCalls::test_인자가_JSON_객체가_아니면_실행하지_않고_INVALID_ARGS_로_돌려준다` · `tests/unit/agents/activity/test_activity_agent.py::TestNormal::test_그래도_말로_끝내면_실패다` · `tests/unit/agents/food/test_food_meal_run.py::test_모델이_출력_tool_없이_말로_끝내면_failed_다` · `tests/unit/agents/supervisor/test_pipeline.py::test_status_failed_로_돌아온_Agent_는_예외처럼_partial_로만_알린다` | ✅ |
| DOM-D | 조회 결과 밖 id · 예시에 없는 문서 id 를 인용한 후보는 `EVIDENCE_REQUIRED` 로 거절한다(근거만 빼고 통과시키지 않는다). 평가 · 단정 표현이 든 후보는 코드가 빼고 다시 부른다(Growth). 알레르기 후보는 SAFE-D | `app/agents/common/evidence.py:188` · `app/agents/common/tool_runtime.py:27` · `app/rules/evaluative.py:116` · `app/agents/growth/review.py:473` | `tests/unit/agents/common/test_tool_loop.py::TestEvidenceRequired::test_조회하지_않은_id_를_인용하면_거절되고_고쳐_내면_끝난다` · `tests/unit/agents/food/test_food_meal_run.py::test_선조회하지_않은_id_를_인용하면_그_후보는_거절된다` · `::test_예시에_없는_문서_id_를_인용하면_그_후보는_거절된다` · `tests/unit/rules/test_evaluative.py::test_평가_표현을_잡는다` · `tests/unit/agents/growth/test_growth_output.py::TestLearning::test_평가_표현은_빼고_재호출_사유로_남긴다` | ✅ |

### CUR · Curator (판정 · 임베딩)

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| CUR-A | run 은 Curator 를 기다리지 않는다. 임베딩이 실패하면 아무것도 저장하지 않고 다음 실행이 다시 집는다. 판정이 실패하면 보류하고, 계정 오류면 이번 실행의 남은 판정을 멈춘다. 로그엔 종류만 | `app/agents/curator/embedding/embed_step.py:51-58` · `app/agents/curator/embedding/link_step.py:321-328` · `app/domains/memory/curator/trigger.py:69-77` | `tests/unit/agents/curator/test_embed_step.py::test_실패하면_아무것도_저장하지_않고_다음_실행에서_다시_대상이_된다` · `tests/unit/agents/curator/test_link_step.py::test_판정기_실패는_보류하고_다음_실행에서_다시_시도한다` · `::test_계정_오류가_나면_이번_실행의_남은_관찰은_판정기를_부르지_않는다` · `tests/unit/agents/curator/test_judge.py::test_HTTP_오류는_LLMError_계열로_바꾼다` | ✅ |
| CUR-B | **정해지지 않음.** 한 호출은 `LLM_TIMEOUT_S` 로 끊기지만, Curator 는 아이 잠금(`pg_advisory_xact_lock`)을 쥔 채 임베딩 · 판정을 부르고 실행 전체 상한이 없다. 그동안 같은 아이의 관찰 고치기 · 지우기와 commit 직전 기억 재계산이 잠금을 기다린다. DB 쪽 `statement_timeout` · `lock_timeout` 도 없다 | `app/domains/memory/curator/trigger.py:49` · `:61` · `app/domains/memory/profile/service.py:55-67` · `app/domains/memory/store/db_store.py:192` · `app/infra/db/session.py:18-20` | 한 호출의 시간 초과만: `tests/unit/agents/curator/test_judge.py::test_통신_실패는_LLMUnavailableError[시간초과]` · 쥔 시간을 남기기만: `tests/integration/test_trigger_curator_background.py::test_잠금을_쥔_시간을_child_id_와_함께_남긴다` **(통합)** · 테스트보다 설정 결정이 먼저 — ISSUE-F8-a · ISSUE-F8-b | 🕳️ F8 |
| CUR-C | 판정 응답 모양이 다르면 `LLMError` 로 바꿔 보류한다. 벡터 차원 · 개수가 다르면 막는다. 잘못된 벡터면 생성도 판정도 하지 않는다 | `app/agents/curator/embedding/jev.py:173` · `app/agents/curator/embedding/embedder.py:123` | `tests/unit/agents/curator/test_judge.py::test_응답_형식이_다르면_LLMError` · `tests/unit/agents/curator/test_embedder.py::test_차원이_다르면_막는다` · `::test_개수가_다르면_막는다` · `tests/unit/agents/curator/test_link_step.py::test_관찰_벡터가_잘못되면_생성도_판정도_하지_않는다` | ✅ |
| CUR-D | 후보 밖 답 · uncertain 이면 추측하지 않고 보류한다. 판정은 연결만 하고 `state` 를 바꾸지 않는다 — 승격은 규칙(재계산)만 (루트 §2) | `app/agents/curator/embedding/link_step.py:203-215` · `app/domains/memory/curator/recompute.py` | `tests/unit/agents/curator/test_link_step.py::test_후보에_없는_값을_답하면_보류한다` · `::test_판정기가_uncertain_이면_보류한다` · `::test_archived_도_후보이고_연결해도_state_는_그대로다` | ✅ |

### SAFE · 알레르기 조회

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| SAFE-A | 기본값으로 넘기지 않는다 — 조회가 실패하면(`SafetyLookupError`) Food · Activity 는 모델을 0번 부르고 `blocked.safety` 로 닫는다. 빈 목록과 실패를 가른다 (루트 §2) | `app/agents/common/gate.py:129-137` · `app/agents/food/registry.py:138-139` | `tests/unit/agents/food/test_food_agent.py::test_안전_조회_실패는_blocked_safety_로_닫는다` · `tests/unit/agents/activity/test_activity_context.py::TestClosedRun::test_알레르기_조회에_실패하면_모델을_부르지_않고_닫는다` · `tests/unit/agents/test_entrypoint.py::test_안전_어댑터가_못_읽으면_둘_다_닫힌다` · `tests/unit/domains/test_safety_agent_reader.py::TestFailure::test_DB_를_못_읽으면_빈_목록이_아니라_SafetyLookupError` · `::TestFailure::test_연결이_안_돼도_SafetyLookupError` | ✅ |
| SAFE-B | 조회는 도메인 Agent 실행 안(`build_gate`)에서 돌아 Agent 와 같이 20초에 끊긴다 → `partial`. DB 쿼리 상한은 ISSUE-F8-a | `app/agents/food/agent.py:82` · `app/agents/pipeline.py:1916` | 구조로 같은 길 — 실행 함수 전체를 `wait_for` 로 감싸서 안에서 무엇이 멈춰도 같다: `tests/unit/agents/supervisor/test_pipeline.py::test_RC16_20초를_넘긴_Agent_는_끊고_나머지로_끝낸다` | ✅ |
| SAFE-C | 다른 이름으로 적은 알레르기도 같은 항목으로 본다. 0행과 실패를 가른다. 코드 버그는 조회 실패로 숨기지 않고 올린다 | `app/agents/common/allergy.py:688` · `app/agents/common/gate.py:129-137` | `tests/unit/agents/common/test_allergy_states.py::test_다른_이름으로_적어도_같은_항목이다` · `tests/unit/agents/activity/test_activity_context.py::TestBuildGate::test_조회_실패는_0행과_다르다` · `tests/unit/domains/test_safety_agent_reader.py::TestFailure::test_버그는_조회_실패로_바꾸지_않는다` | ✅ |
| SAFE-D | 모델이 알레르기 식품을 후보로 내도 코드 필터가 뺀다(모델에게 묻지 않는다). 모자라면 재호출로 채운다 | `app/agents/common/food_safety.py:345` · `app/agents/activity/tools/filters.py:147` | `tests/unit/agents/food/test_food_meal_run.py::test_알레르기_메뉴는_풀에서_빠져_모델에게_보이지_않는다` · `::test_사후_필터로_빠져_3개_미만이면_재호출해_빈자리를_채운다` · `tests/unit/agents/activity/test_activity_safety_filter.py::TestAllergens::test_재료에서도_찾는다` · `tests/unit/agents/activity/test_activity_agent.py::TestRecall::test_안전_필터로_빠지면_한_번_더_불러_빈자리만_채운다` | ✅ |

### SRCH · `memory.search`

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| SRCH-A | 근거를 못 읽었으면 개인화 추천을 내지 않는다 — 그 Agent 는 `partial` 로 빠지고 기록은 남는다 (루트 §2) | `app/agents/memory_bridge.py:134-148` (예외를 잡지 않는다) · `app/agents/food/tools/records.py:83-100` · `app/agents/common/tool_loop.py:151` | 기존 없음 · F 테스트: `tests/eval/faults/test_f4_memory_search_failure.py::test_F4_기억을_못_읽으면_개인화_추천이_나가지_않고_partial_로_빠진다` | ✅ F4 |
| SRCH-B | 조회 tool 이 멈추면 Agent 와 같이 20초에 끊긴다 — 늦은 결과를 성공으로 넣지 않는다 | `app/agents/common/tool_loop.py:151` · `app/agents/pipeline.py:1916` | `tests/unit/agents/common/test_tool_loop.py::TestCancel::test_tool_을_실행하다_끊기면_결과를_내지_않고_시간_초과로_끝난다` · `tests/unit/agents/supervisor/test_pipeline.py::test_RC16_20초를_넘긴_Agent_는_끊고_나머지로_끝낸다` | ✅ |
| SRCH-C | status 가 없거나 inactive · deleted 인 행은 근거로 읽지 않는다 | `app/agents/memory_bridge.py:147` · `:118-124` | `tests/unit/agents/test_memory_bridge.py::test_status_가_없는_관찰은_읽지_않는다` · `::test_Food_는_stand_alone_까지_읽고_inactive_deleted_는_읽지_않는다` | ✅ |
| SRCH-D | 21일 넘게 관찰이 없는 관심은 archived 로 읽어 근거에서 뺀다. 6개월 지난 기록만 인용하면 일반 추천으로 내린다 (I10). 조회 안 한 id 인용은 DOM-D | `app/agents/memory_bridge.py:101-116` · `app/rules/profile.py:82` · `app/rules/staleness.py:17` | `tests/unit/agents/test_memory_bridge.py::test_마지막_관찰_뒤_21일이_지난_관심은_그날_기준_archived_로_읽는다` · `tests/unit/agents/test_entrypoint.py::test_21일_넘게_관찰이_없는_관심은_근거로_나가지_않는다` · `tests/unit/agents/food/test_food_recommend.py::test_6개월_넘은_관찰만_근거면_일반_추천이다` · `tests/unit/agents/common/test_output_channels.py::TestStaleEvidence::test_6개월_지난_아이_기록만_인용하면_일반_추천이고_그_인용은_빠진다` | ✅ |

### EXT · 외부 API (날씨 · 행사 · 도서 · 식약처)

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| EXT-A | 실패를 빈 결과로 바꾸지 않는다(`UpstreamUnavailable`) — 그 tool 만 닫히고 Agent 는 계속 간다(날씨를 모르면 맑음이 아니라 실내만). 메시지 · 로그에 키 · 주소가 없다 | `app/integrations/weather/forecast.py:85` · `app/agents/activity/tools/outing.py:129` · `:276` · `:511-517` · `app/agents/growth/agent.py:258` · `app/agents/food/menu_resolver.py:224` | `tests/unit/integrations/test_weather_forecast.py::TestFailure::test_실패는_전부_UpstreamUnavailable` · `tests/unit/integrations/test_data4library_client.py::TestFailure::test_어떻게_실패하든_UpstreamUnavailable_이고_키_검색어가_없다` · `tests/unit/integrations/test_mfds_client.py::test_연결_실패는_주소와_키_없이_MfdsApiError` · `tests/unit/agents/activity/test_activity_weather_tool.py::TestLookupWeather::test_예보나_특보를_못_읽으면_맑음이_아니라_실패` | ✅ |
| EXT-B | 호출마다 시간 상한이 있고 넘으면 실패로 본다. 캐시가 멈춰도 검색마다 기다리지 않는다 | `app/integrations/weather/public_data.py:25` · `app/integrations/mfds/client.py:174` | `tests/unit/integrations/test_weather_forecast.py::TestFailure::test_실패는_전부_UpstreamUnavailable` (ReadTimeout 갈래) · `tests/unit/integrations/test_mfds_client.py::test_시간_초과도_MfdsApiError` · `::test_HTTP_클라이언트는_느린_응답을_기다릴_만큼_길게_걸되_끝없지는_않다` · `tests/unit/integrations/test_data4library_client.py::TestCache::test_캐시가_응답_없이_멈춰도_검색마다_기다리지_않는다` | ✅ |
| EXT-C | HTTP 200 에 오류 본문 · 이상한 모양이 와도 빈 결과로 보지 않고 실패로 본다 | `app/integrations/mfds/client.py:136-142` · `app/integrations/weather/forecast.py:85` | `tests/unit/integrations/test_tour_events.py::TestFailure::test_실패는_UpstreamUnavailable_이고_키와_주소가_새지_않는다` · `tests/unit/integrations/test_mfds_client.py::test_모양이_깨진_응답은_빈_결과로_보지_않는다` | ✅ |
| EXT-D | 그날 예보 칸이 없으면 실패(맑음으로 읽지 않는다). 하루만 못 읽으면 그날만 "확인 못 함". 결과 0건은 장애가 아니다 | `app/integrations/weather/forecast.py:128` | `tests/unit/integrations/test_weather_forecast.py::TestForecast::test_그날_칸이_없으면_실패다` · `tests/unit/agents/activity/test_activity_weather_tool.py::TestLookupWeather::test_하루만_못_읽으면_그날만_확인_못_함으로_표시한다` · `tests/unit/integrations/test_mfds_client.py::test_결과가_없으면_빈_튜플이고_장애가_아니다` | ✅ |

### SAVE · 저장 (Memory commit · 추천 저장 · 도메인 쓰기 포트)

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| SAVE-A | Memory commit 이 터지면 저장 안내 · 되묻기 맥락을 내지 않는다. 추천 저장이 터지면 그 Agent 만 `partial` · 카드 없음 · 확정된 저장으로 세지 않는다. 예외 메시지(추천 문장)는 로그에 없다 | `app/agents/pipeline.py:2087-2095` · `:2292-2316` · `app/api/runs/runner.py:185-201` | `tests/unit/agents/supervisor/test_pipeline.py::test_commit_이_실패하면_저장_안내도_되묻기_맥락도_나가지_않는다` · `::test_추천_저장에_실패하면_그_Agent_는_partial_이고_카드가_없다[예외]` · `tests/unit/api/test_runner.py::test_추천_저장이_실패하면_확정된_저장으로_세지_않는다` · `tests/unit/agents/test_run_suggestions.py::test_저장에_실패하면_그_Agent_는_실패다` | ✅ |
| SAVE-B | 저장은 Agent 의 20초 밖에서 기다린다(중간에 끊으면 들어갔는지 모르는 저장이 남는다). 60초 안전망이 저장 뒤에 걸리면 done · 키 유지, 저장 중이면 취소를 부분 실패로 숨기지 않는다. DB 쿼리 상한은 ISSUE-F8-a | `app/agents/pipeline.py:1909-1910` · `:1926` · `app/api/runs/runner.py:242-253` | `tests/unit/agents/supervisor/test_pipeline.py::test_저장이_느려도_이미_끝난_Agent_는_끊기지_않는다` · `::test_저장_중_취소는_부분_실패로_숨기지_않는다` · `tests/unit/api/test_runner.py::test_추천_저장_뒤_시간_안전망에_걸려도_done_이다` · `::test_timeout이_commit_뒤에_걸려도_done으로_끝난다` | ✅ |
| SAVE-C | 저장이 돌려준 id 수가 추천 수와 다르면(적든 많든) 카드와 추천의 짝이 어긋난다 — 그 Agent 를 실패로 센다. 입력 run · 05 다시 만들기 둘 다 | `app/agents/pipeline.py:2313-2315` | 입력 run · id 가 적은 경우만: `tests/unit/agents/supervisor/test_pipeline.py::test_추천_저장에_실패하면_그_Agent_는_partial_이고_카드가_없다[결과_수가_다름]` · F 테스트: `tests/eval/faults/test_f6_saved_id_count.py::test_F6_05_다시_만들기도_저장한_id_수가_다르면_그_Agent_는_실패다[적다]` · `[많다]` | 🕳️ F6 |
| SAVE-D | 확정된 저장이 있는 run 은 failed 로 끝내지 않고 키를 놓지 않는다(놓으면 재시도가 두 번 저장한다). 일정 초안만 만든 run 은 확정된 쓰기가 아니다 (I7). commit 했어도 쓴 것이 없으면 failed 가 맞다 | `app/api/runs/runner.py:395-401` · `app/api/runs/registry.py:51-57` · `app/agents/pipeline.py:886` | `tests/unit/api/test_runner.py::test_commit_후_예외가_나도_키를_풀지_않는다` · `::test_저장_안_한_run의_commit_후_예외는_failed로_끝난다` · `tests/unit/agents/supervisor/test_pipeline.py::test_일정_초안만_만든_run_은_commit_된_쓰기가_없다` · `::test_급식을_고친_뒤_죽으면_failed_가_아니라_partial_로_끝난다` | ✅ |

### IDEM · 접수 창구 (Redis 멱등 키 · 하루 입력 한도 · run 당 모델 호출 상한)

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| IDEM-A | Redis 가 죽으면 메모리로 넘어가지 않고 503 `temporarily_unavailable` — Agent 를 부르지 않는다. 쓰기만 거절돼도 처리 전에 503 | `app/api/idempotency.py:76-80` · `:112-121` | `tests/unit/api/test_idempotency_redis.py::TestRedisDown::test_장애면_recall_은_503` · `::TestRedisDown::test_쓰기만_거절되면_처리하기_전에_503` · `tests/integration/api/test_input_run.py::test_Redis_가_죽으면_입력은_503_이고_Agent_를_부르지_않는다` **(통합)** | ✅ |
| IDEM-B | 1초 상한 + 차단기. 멈춘 Redis 를 이벤트 루프 밖(스레드)에서 기다리고 1초 남짓에 503 | `app/infra/redis.py:19` · `:24` · `:31` · `app/api/idempotency.py:77` | `tests/unit/api/test_idempotency_redis.py::test_Redis_가_응답_없이_매달려도_1초_남짓에_503` · `::test_Redis_가_매달려도_이벤트_루프는_멈추지_않는다` | ✅ |
| IDEM-C | 재생 값이 깨져 있어도 처리 안 된 예외(500)가 아니라 정해진 답(예: 503 · 새 요청)이다 | `app/api/idempotency.py:83` · `app/api/errors.py:199-209` | 기존 없음 · F 테스트: `tests/eval/faults/test_f7_broken_replay.py::test_F7_깨진_재생_값은_처리_안_된_예외가_아니다` (지금 코드는 `JSONDecodeError` → 500 이라 xfail) | 🕳️ F7 |
| IDEM-D | 하루 한도를 넘으면 막고 날이 바뀌면 0부터, 같은 키 재생은 세지 않는다. run 의 모델 호출 수가 예산(`MAX_MODEL_CALLS`, 다시 나눈 run 은 +1)을 넘으면 경고를 남긴다(실행은 막지 않는다). 재시도(폴백 · 재호출)는 센다 | `app/api/quota.py:31-45` · `app/api/v1/routers/children.py:526-527` · `app/agents/pipeline.py:87-95` · `:2528-2536` | `tests/unit/api/test_quota.py::test_allows_up_to_the_limit_then_refuses` · `::test_a_new_day_starts_from_zero` · `tests/integration/api/test_input_run.py::test_replay_is_not_counted_and_still_works_after_the_limit` **(통합)** · 경고가 안 뜨는 쪽만: `tests/unit/agents/supervisor/test_pipeline.py::test_Memory_가_여러_바퀴_돌아도_호출_예산은_1만_먹는다` · F 테스트: `tests/eval/faults/test_f5_model_calls.py::test_F5_재시도를_세고_예산을_넘으면_경고한다[정상_4회]` · `[폴백_5회]` | 🕳️ F5 |

### RUN · 러너 · SSE

| 칸 | 기대 동작 | 코드 | 판정하는 테스트 | 상태 |
| --- | --- | --- | --- | --- |
| RUN-A | job 이 죽으면 `failed(internal_error)` 하나(원문은 화면으로)로 채널을 닫고, 저장 없는 실패면 키를 푼다. done 뒤에 터지면 failed 를 덧붙이지 않는다. 로그엔 예외 종류와 위치만 | `app/api/runs/runner.py:371-412` | `tests/unit/api/test_runner.py::test_job_exception_ends_with_failed_only_and_channel_closes` · `::test_job_exception_log_keeps_type_and_place_but_not_message` · `::test_failed_from_the_agents_releases_the_key` — 가짜 진입점으로만 · F 테스트(진짜 진입점 · pipeline 을 지난 로그): `tests/eval/faults/test_f1_log_leak.py::test_F1_러너부터_pipeline_까지_모델_오류의_원문_되돌림이_로그에_없다` | ✅ F1 |
| RUN-B | 60초에 끊는다. commit 전이면 `failed(timeout)` + 키 풀기, commit 뒤면 done. 조용한 동안 10초마다 ping. 로그에 원문이 없다 | `app/api/runs/runner.py:45` · `:211` · `:242-253` · `app/api/runs/sse.py:36` | `tests/unit/api/test_runner.py::test_agent_job_stops_a_run_past_the_deadline` · `::test_commit_전_timeout은_failed로_끝나고_키가_풀린다` · `tests/unit/api/test_sse_frame.py::test_stream_sends_ping_while_channel_is_quiet_then_ends_on_close` — 로그는 안 본다 · F 테스트: `tests/eval/faults/test_f1_log_leak.py::test_F1_러너_시간_초과_로그에_원문이_없다` | ✅ F1 |
| RUN-C | 번역이 터지면 조용히 빠지지 않고 run 이 failed 로 끝난다(로그엔 종류만). SSE 프레임은 값에 줄바꿈이 있어도 한 줄이다 | `app/api/runs/translate.py:239-274` · `app/api/runs/sse.py:21` | `tests/unit/api/test_runner.py::test_untranslatable_event_fails_the_run_loudly` · `tests/unit/api/test_sse_frame.py::test_frame_data_stays_on_one_line_even_if_value_has_newline` | ✅ |
| RUN-D | 끝 신호 없이 돌아온 job 에 failed 를 붙인다. 끝 신호는 하나 — 그 뒤 이벤트는 채널이 버린다. 부분 결과는 done 이고 키를 놓지 않는다 | `app/api/runs/runner.py:395-402` · `app/api/runs/registry.py:59-73` | `tests/unit/api/test_runner.py::test_job_that_returns_without_an_end_signal_ends_with_failed` · `::test_exception_after_done_keeps_done_as_the_only_end` · `tests/unit/api/test_run_registry.py::test_events_after_an_end_signal_are_dropped` · `tests/unit/api/test_runner.py::test_부분_결과는_done_으로_끝나고_키를_놓지_않는다` | ✅ |

## 메울 구멍

| F | 칸 | 테스트 | 우선 | 지금 코드에서 |
| --- | --- | --- | --- | --- |
| F1 | MEM-A · RUN-A · RUN-B | `tests/eval/faults/test_f1_log_leak.py` | P0 | 통과 — 회귀 방지 |
| F2 | SUP-B · MEM-B | `tests/eval/faults/test_f2_stuck_supervisor_memory.py` | P0 | 통과 — 회귀 방지 · 화면 대기는 ISSUE-F2-UX |
| F3 | DOM-A | `tests/eval/faults/test_f3_real_agent_llm_error.py` | P0 | 통과 — 회귀 방지 |
| F4 | SRCH-A | `tests/eval/faults/test_f4_memory_search_failure.py` | P0 | 통과 — 회귀 방지 |
| F5 | IDEM-D | `tests/eval/faults/test_f5_model_calls.py` | P1 | 통과 — 회귀 방지 |
| F6 | SAVE-C | `tests/eval/faults/test_f6_saved_id_count.py` | P1 | 통과 — 회귀 방지 |
| F7 | IDEM-C | `tests/eval/faults/test_f7_broken_replay.py` | P1 | **실패 → xfail(strict) · ISSUE-F7** |
| F8 | CUR-B | 테스트 없음 | 이슈만 | ISSUE-F8-a · ISSUE-F8-b |

가짜는 `tests/eval/faults/fakes.py` 한 곳에 있다 — 새로 만든 것은 다른 테스트 모듈에서 import 할 수 없는 것뿐이다(그 파일 머리말).

## 조사하며 바로잡은 것

- **스펙 §6 F6 "테스트가 없다"** — 입력 run 쪽은 이미 판정한다 (`test_pipeline.py` 의 `[결과_수가_다름]` 갈래). 비어 있던 것은 05 다시 만들기(`run_suggestions`)와 id 가 **많은** 경우다.
- **스펙 §0 · §6 "약 31칸"** — 이 칸 나눔으로 29칸이다. 구멍 11칸은 F1 이 세 칸, F2 가 두 칸, F3~F8 이 한 칸씩이라 F 번호(8개)와 칸 수가 다르다.
- **`model_calls` 가 적게 센다 (F5)** — 코드가 정의한 대로다 (`pipeline.py:87-95`: Agent 진입 + 재시도만 센다). 예외로 끝난 Agent 를 0 으로 세는 것도 기존 테스트가 일부러 고정한다 (`test_RC16_한_Agent_가_죽어도_나머지_결과는_나간다` 의 `model_calls == 3`). 실제 호출 수와의 차이는 스위트 C 가 재고, 정의를 바꿀지는 eval-v1 §12-1 의 NF-01 갱신 몫이라 xfail 로 걸지 않았다.

## 범위 밖 (11-07 팀 결정 — Health 는 기록만)

앱에서 Health 를 당분간 막는다(권한 문제 · eval-v1 §13-6). 점검에서 Health 는 **증상 · 건강 관찰 저장(Memory)** 만 본다. 진단 문의를 막는 Supervisor 안내와 응급 사전검사는 Health Agent 가 아니라 그대로 본다(SUP-D).

- 판정하지 않는 기능 — 복약 알림 · 복약 초안(`health_medication` · `health_medication_writer` 포트, 복약 대조 `pipeline._match_registered_doses`), 병원 찾기(`health_places` · 국립중앙의료원 어댑터 `app/integrations/hospital/`), Health Agent 의 요청 결과(3일 규칙의 병원 확인 안내 · 진료 준비)
- 이 칸 나눔에는 그 기능에만 있는 칸이 없다 — 40칸은 그대로이고 "범위 밖" 으로 뺀 칸은 0이다. 대신 칸 안에서 그 기능의 근거를 뺐다: EXT-A · EXT-D 의 병원 어댑터 테스트, MEM-D 의 진행 중 복약 코스 테스트, DOM-D 의 Health 문구 테스트(평가 표현은 Growth 쪽 테스트로 판정한다)
- F1~F7 테스트는 그 기능을 부르지 않는다 — F3 · F4 는 Food · Activity, 나머지는 Supervisor · Memory · 러너 · 저장 · 멱등 키다
- 다시 넣을 때 칸을 늘리지 않고 위 근거를 되돌리면 된다
