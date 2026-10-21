"""Health 케이스 한 건을 돌려 `Ran` 으로 만든다. 라이브 eval 과 오프라인 검증이 같이 쓴다.

테스트 파일이 아니다(수집되지 않는다). `client` 만 다르다 — 라이브는 실제 모델, 오프라인 검증
(`tests/unit/agents/health/test_health_eval_run.py`)은 정해 둔 tool 호출을 돌려주는 가짜다.

되묻고 멈추는 케이스는 `case.followups` 의 입력을 차례로 이어 돈다. 앞 턴 결과의 `pending` 을 다음
턴 `HealthContext.pending` 에 넣고(연결 이슈의 `reply_to` 를 흉내 낸다), **now 를 2분 뒤로 둔다** —
중단 시각이 묻던 턴의 now 가 아니라 답한 턴의 now 인지 가리려고. 턴마다 저장소의 쓰기 카운터가
얼마나 늘었는지도 기록한다.
"""

import time
from dataclasses import replace
from datetime import timedelta
from typing import Any

from app.agents.common.schemas.task import DomainTask
from app.agents.health import agent as health_agent
from app.agents.health.envelope import result as hook_result
from app.agents.health.hook import check_symptom_repetition
from tests.eval.agents.health.cases import Case, doc_inputs
from tests.eval.agents.health.judges import EVAL_GUIDANCE, Ran, Turn
from tests.eval.agents.health.world import DAY, NIGHT, build_world


async def run_case(case: Case, *, client: Any | None) -> Ran:
    world = build_world(case.world, now=NIGHT if case.night else DAY, run_id=f"live-{case.case_id}")
    request = doc_inputs()[case.input_id]
    started = time.perf_counter()

    turns: list[Turn] = []
    if case.label is None:
        # hook 케이스 — 라벨이 아니라 Memory 저장 직후 pipeline 이 부르는 함수를 직접 부른다.
        # pipeline 연결은 다른 이슈라 여기서는 결과를 봉투에 싸기만 한다
        readouts = await check_symptom_repetition(world.context, guidance=EVAL_GUIDANCE)
        result = hook_result(None, "completed", readouts=readouts)
    else:
        context = world.context
        for index, text in enumerate((request, *case.followups)):
            if index:
                context = replace(
                    context.for_task(),
                    now=context.now + timedelta(minutes=2),
                    pending=turns[-1].result.pending,
                )
            written = len(world.medication.writes)
            task = DomainTask(
                run_id=context.run_id,
                agent="health",
                task_type=case.label.value,
                request_texts=(text,),
            )
            # 모듈 속성으로 부른다 — conftest 가 감싼 run 이 걸려야 결과 파일에 agent call 이 남는다
            turn_result = await health_agent.run(task, context, client=client)
            turns.append(
                Turn(text, turn_result, context.now, tuple(world.medication.writes[written:]))
            )
        result = turns[-1].result

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return Ran(
        case_id=case.case_id,
        world=world,
        request=request,
        result=result,
        elapsed_ms=elapsed_ms,
        turns=tuple(turns),
    )
