"""Health 케이스 한 건을 돌려 `Ran` 으로 만든다. 라이브 eval 과 오프라인 검증이 같이 쓴다.

테스트 파일이 아니다(수집되지 않는다). `client` 만 다르다 — 라이브는 실제 모델, 오프라인 검증
(`tests/unit/agents/health/test_health_eval_run.py`)은 정해 둔 tool 호출을 돌려주는 가짜다.

되묻고 멈추는 케이스는 `case.followups` 의 입력을 차례로 이어 돈다. 앞 턴 결과의 `pending` 을 다음
턴 `HealthContext.pending` 에 넣고(연결 이슈의 `reply_to` 를 흉내 낸다), **now 를 2분 뒤로 둔다** —
중단 시각이 묻던 턴의 now 가 아니라 답한 턴의 now 인지 가리려고. 턴마다 저장소의 쓰기 카운터가
얼마나 늘었는지도 기록한다.

Memory 저장 케이스(`case.memory`)는 Health 대신 Memory Agent 를 인메모리 저장소로 한 번 돌리고,
저장된 관찰 행(전 도메인)을 `Ran.memory_rows` 에 담는다. `client` 는 Memory 몫이다. 저장소에는
`case.memory_seed` 의 기록만 미리 있다(T21-memory 의 앞선 열 기록 — 따로 말한 약을 잇지 않는지
보려고).

라벨 케이스는 모델 자리를 `_Recorder` 로 감싸 모델에게 간 system · tool 메시지를 `Ran.model_inputs`
에 담는다 — 진료 준비가 기록 원문 · 약 이름 · 용량을 외부 LLM 으로 보내지 않는지 판정이 본다 (#551).
"""

import time
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID

from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.schemas.task import DomainTask
from app.agents.health import agent as health_agent
from app.agents.health.envelope import result as hook_result
from app.agents.health.hook import check_infant_fever, check_symptom_repetition
from app.agents.memory import agent as memory_agent
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from tests.eval.agents.health.cases import Case, doc_inputs
from tests.eval.agents.health.judges import EVAL_GUIDANCE, Ran, Turn
from tests.eval.agents.health.world import CHILD, DAY, NIGHT, World, build_world

WRITER = UUID("00000000-0000-7000-8000-0000000000ff")  # 말한 보호자 (지어낸 id)
_DOMAINS = ("food", "health", "education", "activity", "routine")


class _Recorder:
    """모델 자리를 감싸 모델에게 간 system · tool 메시지를 모은다. 나머지는 그대로 넘긴다."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.messages: list[dict] = []

    async def chat(self, messages, **kwargs):
        # loop 가 messages 를 이어 쓰므로 부를 때마다 처음부터 다시 담긴다 — 판정은 중복을
        # 개의치 않는다
        self.messages.extend(
            {"role": m.get("role"), "content": m.get("content")}
            for m in messages
            if m.get("role") in {"system", "tool"}
        )
        return await self._inner.chat(messages, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def run_case(case: Case, *, client: Any | None) -> Ran:
    world = build_world(case.world, now=NIGHT if case.night else DAY, run_id=f"live-{case.case_id}")
    request = case.text or doc_inputs()[case.input_id]
    started = time.perf_counter()

    if case.memory:
        return await _run_memory(case, world, request, client=client, started=started)

    turns: list[Turn] = []
    recorder = _Recorder(client) if client is not None else None
    if case.label is None:
        # hook 케이스 — 라벨이 아니라 Memory 저장 직후 pipeline 이 부르는 함수를 직접 부른다.
        # pipeline 연결은 다른 이슈라 여기서는 결과를 봉투에 싸기만 한다
        if case.hook == "infant_fever":
            readouts = await check_infant_fever(world.context)
        else:
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
            turn_result = await health_agent.run(task, context, client=recorder)
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
        model_inputs=tuple(recorder.messages) if recorder is not None else (),
    )


async def _run_memory(
    case: Case, world: World, request: str, *, client: Any | None, started: float
) -> Ran:
    """Memory 를 한 번 돌린다. Health 결과는 없다(`result=None`).

    저장소는 비어 있거나 `case.memory_seed` 의 기록(오늘)만 있다 — 시드 id 는 `Ran.memory_seeded`.
    """
    store = InMemoryStore(now=world.context.now)
    seeded = [
        await store.create_observation(
            domain="health",
            child_id=CHILD,
            source_writer=WRITER,
            raw_text=seed.raw_text,
            observed_on=world.today,
            observed_range=build_observed_range(world.today),
            fields={"symptom": list(seed.symptom), "confidence_source": "parent_direct"},
        )
        for seed in case.memory_seed
    ]
    context = AgentContext(
        child_id=CHILD,
        source_writer=WRITER,
        now=world.context.now,
        timezone=world.context.timezone,
        store=store,
    )
    outcome = await memory_agent.run(request, context, client=client)
    rows: list = []
    for domain in _DOMAINS:
        rows += await store.query_observations(domain=domain, child_id=CHILD)
    return Ran(
        case_id=case.case_id,
        world=world,
        request=request,
        result=None,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        tools=tuple(outcome.tool_names),
        memory_rows=tuple(rows),
        memory_seeded=tuple(row.id for row in seeded),
    )
