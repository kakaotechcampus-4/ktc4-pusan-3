"""사용자 입력을 Supervisor, Memory, Food 순서로 처리한다.

Memory 저장이 끝난 뒤 Food를 실행해 같은 요청에서 저장된 관찰도 바로 조회할 수 있게 한다.
Supervisor 실패 시에는 Memory가 원문을 처리하고, Memory 실패 시 전체 요청을 실패로 처리한다.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.agents.common.llm_client import LLMClient, LLMError
from app.agents.food.agent import FoodAgentResult
from app.agents.food.agent import run as run_food
from app.agents.food.context import FoodContext
from app.agents.memory.agent import MemoryAgentResult
from app.agents.memory.agent import run as run_memory
from app.agents.memory.bundles import MUTATING_PREFIXES, WRITES_FOR
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import WorkType
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.agent import run as run_supervisor
from app.agents.supervisor.routing import Guidance, Routing, route

logger = logging.getLogger(__name__)

MAX_MODEL_CALLS = 3  # 한 요청에서 허용하는 모델 호출 수

# no_child_observation 판정 기준은 추후 추가
FailReason = Literal["unparsable", "llm_unavailable"]


# 이벤트
@dataclass(frozen=True)
class Step:
    """처리 단계 정보."""

    index: int
    total: int
    label: str


@dataclass(frozen=True)
class Ref:
    """저장된 리소스 참조."""

    kind: str
    id: str


@dataclass(frozen=True)
class Saved:
    refs: tuple[Ref, ...]  # 화면용 변환은 app/api에서 처리


@dataclass(frozen=True)
class FoodRouted:
    """Food 라우팅 결과를 담는 내부 이벤트."""

    task_type: str
    stage: str
    tools: tuple[str, ...]
    requires_safety_check: bool
    status: str


@dataclass(frozen=True)
class Unavailable:
    agents: tuple[str, ...]  # 아직 지원하지 않는 도메인 Agent


@dataclass(frozen=True)
class MemoryNote:
    text: str  # Memory 응답 메시지


@dataclass(frozen=True)
class Failed:
    reason: FailReason
    raw_text: str  # 실패 시 원문 유지


@dataclass(frozen=True)
class Done:
    run_id: str
    model_calls: int


Event = Step | Saved | FoodRouted | Unavailable | Guidance | MemoryNote | Failed | Done
Emit = Callable[[Event], None]

_LABELS = ("입력을 살펴보고 있어요", "관찰을 나누고 있어요", "다음 행동을 준비하고 있어요")
# Food 작업이 없으면 세 번째 단계는 생략한다
_TOTAL_STEPS = len(_LABELS)


# 결과
@dataclass(frozen=True)
class Disagreement:
    """Supervisor 예상 작업과 Memory 실제 작업의 차이."""

    memory_only: tuple[str, ...] = ()
    supervisor_only: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.memory_only) + len(self.supervisor_only)


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    supervisor: SupervisorResult  # 원문 조각은 로그나 저장소에 남기지 않는다
    routing: Routing
    memory: MemoryAgentResult | None  # Memory 미실행 또는 실패 시 None
    food: tuple[FoodAgentResult, ...] = ()
    failed: Failed | None = None
    disagreement: Disagreement = Disagreement()
    model_calls: int = 0
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.failed is None


async def handle_input(
    raw_text: str,
    memory_context: AgentContext,
    food_context: FoodContext,
    *,
    run_id: str,
    supervisor_client: LLMClient | None = None,
    memory_client: LLMClient | None = None,
    emit: Emit | None = None,
) -> PipelineResult:
    """사용자 입력 한 건을 처리한다.

    food_context.stage는 호출 전에 아이 나이를 기준으로 계산해 전달한다.
    """
    started = time.perf_counter()
    send = emit or _ignore
    _safety_precheck(raw_text)

    send(Step(1, _TOTAL_STEPS, _LABELS[0]))
    supervisor = await run_supervisor(raw_text, client=supervisor_client)
    routing = route(raw_text, supervisor, run_id=run_id)
    model_calls = supervisor.model_calls

    for guidance in routing.guidance:
        send(guidance)
    if routing.unavailable_agents:
        send(Unavailable(routing.unavailable_agents))

    memory: MemoryAgentResult | None = None
    failed: Failed | None = None
    # Memory는 기본적으로 항상 부른다. 순수 요청형에서 건너뛰려면
    # routing의 SKIP_MEMORY_FOR_PURE_REQUEST를 켠다
    if routing.memory_task is not None:
        send(Step(2, _TOTAL_STEPS, _LABELS[1]))
        try:
            memory = await run_memory(
                raw_text, memory_context, client=memory_client, task=routing.memory_task
            )
        except LLMError:
            # Memory 실패 시 기본값으로 대체하지 않는다
            failed = Failed("llm_unavailable", raw_text)
        else:
            model_calls += memory.steps
            refs = _saved_refs(memory)
            if refs:
                send(Saved(refs))
            if memory.final_message:
                send(MemoryNote(memory.final_message))

    food: list[FoodAgentResult] = []
    # 도메인 Agent는 지금 Food 하나뿐이라 이 블록도 Food 전용.
    # activity·growth·health가 생기면 agent 이름별 실행 함수를 도는 구조로 바꾼다
    # 도메인 Agent 끼리는 순서가 없으니 asyncio.gather로 묶어도 됨
    if failed is None and routing.food_tasks:
        # Memory가 성공한 뒤 Food를 실행한다
        send(Step(3, _TOTAL_STEPS, _LABELS[2]))
        for task in routing.food_tasks:
            result = await run_food(task, food_context)
            food.append(result)
            model_calls += result.model_calls
            send(_food_routed(result))

    if failed is None and not _did_anything(memory, food, routing):
        failed = Failed("unparsable", raw_text)
    if failed is not None:
        send(failed)
    send(Done(run_id, model_calls))

    result = PipelineResult(
        run_id=run_id,
        supervisor=supervisor,
        routing=routing,
        memory=memory,
        food=tuple(food),
        failed=failed,
        disagreement=_disagreement(routing, memory),
        model_calls=model_calls,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    _log(result)
    return result


def _ignore(event: Event) -> None:
    """emit 콜백이 없을 때 이벤트를 무시한다."""


def _safety_precheck(raw_text: str) -> None:
    """안전 사전검사를 위한 자리.

    실제 규칙은 app/rules/에 추가한다.
    """


def _saved_refs(memory: MemoryAgentResult) -> tuple[Ref, ...]:
    """성공한 관찰 저장 결과만 반환"""
    return tuple(
        Ref(kind=call.result["resource"], id=call.result["data"]["id"])
        for call in memory.calls
        if call.success and call.name.startswith("create_observation_")
    )


def _food_routed(result: FoodAgentResult) -> FoodRouted:
    return FoodRouted(
        task_type=str(result.task_type),
        stage=str(result.stage),
        tools=tuple(result.tools),
        requires_safety_check=result.requires_safety_check,
        status=result.status,
    )


def _did_anything(
    memory: MemoryAgentResult | None, food: list[FoodAgentResult], routing: Routing
) -> bool:
    """저장, 추천, 안내, 메모 중 하나라도 처리됐는지 확인한다."""
    wrote = memory is not None and any(
        call.success and call.name.startswith(MUTATING_PREFIXES) for call in memory.calls
    )
    note = memory is not None and bool(memory.final_message)
    return wrote or note or bool(food) or bool(routing.guidance)


def _disagreement(routing: Routing, memory: MemoryAgentResult | None) -> Disagreement:
    """강등된 요청은 Supervisor와 Memory 결과를 비교하지 않는다."""
    task = routing.memory_task
    if routing.degraded or task is None or memory is None:
        return Disagreement()

    hinted = {WorkType(hint.work) for hint in task.hints}
    written = {work for work in WorkType if _wrote(memory, work)}
    # lookup_edit는 조회만으로 끝날 수 있어 비교 대상에서 제외한다
    expected = hinted - {WorkType.LOOKUP_EDIT}
    return Disagreement(
        memory_only=tuple(sorted(work.value for work in written - hinted)),
        supervisor_only=tuple(sorted(work.value for work in expected - written)),
    )


def _wrote(memory: MemoryAgentResult, work: WorkType) -> bool:
    return any(call.success and call.name.startswith(WRITES_FOR[work]) for call in memory.calls)


def _log(result: PipelineResult) -> None:
    """민감한 원문은 제외하고 처리 상태만 로그로 남긴다."""
    memory = result.memory
    logger.info(
        "pipeline run_id=%s intent=%s degraded=%s supervisor_error=%s hints=%d steps=%s "
        "ended_by=%s saved=%d food=%s guidance=%s unavailable=%s note=%s failed=%s "
        "memory_only=%s supervisor_only=%s model_calls=%d latency_ms=%d",
        result.run_id,
        result.routing.intent_type,
        result.routing.degraded,
        result.supervisor.error,
        len(result.routing.memory_task.hints) if result.routing.memory_task else 0,
        memory.steps if memory else None,
        memory.ended_by if memory else None,
        len(_saved_refs(memory)) if memory else 0,
        [str(item.task_type) for item in result.food],
        [guidance.code for guidance in result.routing.guidance],
        list(result.routing.unavailable_agents),
        bool(memory and memory.final_message),
        result.failed.reason if result.failed else None,
        list(result.disagreement.memory_only),
        list(result.disagreement.supervisor_only),
        result.model_calls,
        result.latency_ms,
    )
    if result.model_calls > MAX_MODEL_CALLS:
        logger.warning(
            "model_calls 예산 초과 run_id=%s model_calls=%d", result.run_id, result.model_calls
        )
