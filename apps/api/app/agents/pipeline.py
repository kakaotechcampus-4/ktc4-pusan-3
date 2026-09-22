"""사용자 입력을 Supervisor, Memory, Food 순서로 처리한다.

Memory 저장이 끝난 뒤 Food를 실행해 같은 요청에서 저장된 관찰도 바로 조회할 수 있게 한다.
Supervisor 실패 시에는 Memory가 원문을 처리하고, Memory 실패 시 전체 요청을 실패로 처리한다.

Supervisor가 요청을 기록으로 잘못 나누면 그 조각은 어디서도 처리되지 않는다.
Memory가 적지 않은 RECORD 조각이 남으면 그 이유를 Supervisor에 돌려주고 한 번만 다시 나눈다.
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
from app.agents.memory.drafts import EventDraft
from app.agents.memory.schemas.task import MemoryTask, WorkType
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.agent import run as run_supervisor
from app.agents.supervisor.routing import Guidance, Routing, route
from app.agents.supervisor.schemas import normalize

logger = logging.getLogger(__name__)

MAX_MODEL_CALLS = 3  # 한 요청에서 허용하는 모델 호출 수

# Memory 가 기록하지 않은 RECORD 조각이 있으면 Supervisor 에게 이유를 주고 한 번 더 나눠 본다.
# 조각을 잘못 위임하면 그 요청은 아무 데서도 처리되지 않는다 — 호출 하나를 더 쓰는 값이 있다.
# 다시 나누는 것은 run 당 한 번뿐이고, 그때만 예산이 MAX_MODEL_CALLS + 1 이 된다
REROUTE_MISDELEGATED = True

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
class EventDrafts:
    """보호자 제출을 기다리는 일정 초안. SSE 이름은 event_draft.

    JSON 변환은 EventDraft.to_payload() 가 한다.
    run당 한 프레임+초안이 배열로 실리기 때문에 화면이 초안 묶음을 한 번에 디스플레이

    초안은 이 이벤트로 나가고 끝이다. run이 끝나면 사라지고 되받을 경로가 없다.

    그래서 초안 두 장을 띄워두고 나중 것부터 제출하면 앞 초안이 뒤 결과를 지운다.
    초안이 만들어진 시점의 DB 값을 들고 있는데 items가 최종 목록이라서다.
    같은 회의에서 현상유지로 두고, 잠금은 나중에 보기로 했다.

    # TODO: 제안에서 온 일정도 같은 모양으로 내야 한다. (#121 이 POST /suggestions/{sid}/event와
    #   POST /events/{eid}/confirm을 하나로 합쳐서, 합친 엔드포인트가 호출 즉시 event를 쓰면
    #   보호자가 확인하는 단계=승인 게이트 사라짐.
    #   초안만 내고 제출은 한 엔드포인트로 모아야 승인 시트를 하나로 유지 가능)
    """

    drafts: tuple[EventDraft, ...]


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
class Unwritten:
    """기록할 조각을 짚었는데 성공한 쓰기가 하나도 없는 run.

    사용자 응답은 그대로 두어 화면에는 Memory가 한 말이 그대로 나간다.
    TODO: 테스트 케이스 확장과 미구현 Agent 도입 후에 빈도를 체크하고 처리 방식을 확정
    """

    hints: int  # Supervisor가 짚은 기록 조각 수
    tools: int  # Memory가 부른 tool 수. 0이면 말만 한 것이다
    note: bool  # Memory가 문장을 냈는지


@dataclass(frozen=True)
class Rerouted:
    """Memory 가 기록하지 않은 조각을 Supervisor 에게 돌려주고 다시 나눈 것."""

    bounced: int  # 되돌린 조각 수. 원문은 싣지 않는다 (S10)
    food_tasks: tuple[str, ...] = ()  # 다시 나눈 뒤의 Food 작업 유형


@dataclass(frozen=True)
class Failed:
    reason: FailReason
    raw_text: str  # 실패 시 원문 유지


@dataclass(frozen=True)
class Done:
    run_id: str
    model_calls: int


Event = (
    Step
    | Saved
    | EventDrafts
    | FoodRouted
    | Unavailable
    | Guidance
    | MemoryNote
    | Unwritten
    | Rerouted
    | Failed
    | Done
)
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
    rerouted: Rerouted | None = None  # 다시 나눴으면 그 결과. routing 은 다시 나눈 쪽이다
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
    # 기록할 조각이 있으면 Memory를 부른다. 원문이 전부 요청·안내 조각이면 건너뛴다
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
            # 저장된 것과 제출을 기다리는 것을 한 run에서 같이 내보낸다
            if memory.drafts:
                send(EventDrafts(memory.drafts))
            if memory.final_message:
                send(MemoryNote(memory.final_message))
            unwritten = _unwritten(routing.memory_task, memory)
            if unwritten is not None:
                send(unwritten)

    # 위임이 어긋났으면 Supervisor에게 이유를 주고 한 번만 다시 나눈다.
    # Memory는 다시 돌리지 않는다.
    rerouted: Rerouted | None = None
    bounced = _bounced_hints(routing.memory_task, memory) if failed is None else ()
    # 도메인 Agent로 간 조각이 있으면 다시 나누지 않는다. 요청이 통째로 사라진 경우만 본다.
    stranded = not routing.food_tasks and not routing.unavailable_agents
    if bounced and stranded and REROUTE_MISDELEGATED:
        retry = await run_supervisor(
            raw_text, client=supervisor_client, feedback=_reroute_feedback(bounced)
        )
        model_calls += retry.model_calls
        if retry.output is not None:
            before, supervisor = routing, retry
            routing = route(raw_text, retry, run_id=run_id)
            for guidance in routing.guidance:  # 새로 생긴 안내만 내보낸다
                if guidance not in before.guidance:
                    send(guidance)
            appeared = tuple(
                agent
                for agent in routing.unavailable_agents
                if agent not in before.unavailable_agents
            )
            if appeared:
                send(Unavailable(appeared))
            rerouted = Rerouted(len(bounced), tuple(str(t.task_type) for t in routing.food_tasks))
            send(rerouted)
        else:
            logger.info("reroute 실패 run_id=%s error=%s", run_id, retry.error)

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
        rerouted=rerouted,
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


def _unwritten(task: MemoryTask | None, memory: MemoryAgentResult) -> Unwritten | None:
    """기록할 조각을 짚었는데 쓰기가 하나도 없으면 남긴다. 판정은 하지 않는다."""
    if task is None or not task.hints:
        return None
    if any(call.success and call.name.startswith(MUTATING_PREFIXES) for call in memory.calls):
        return None

    event = Unwritten(len(task.hints), len(memory.calls), bool(memory.final_message))
    # tool을 한 번도 안 부르고 답만 낸 것은 "했다고 말만 한" 것이므로, 그때만 경고로 처리
    log = logger.warning if event.tools == 0 and event.note else logger.info
    log(
        "기록 힌트가 있는데 쓰기가 없다 hints=%d tools=%d note=%s",
        *(event.hints, event.tools, event.note),
    )
    return event


def _bounced_hints(task: MemoryTask | None, memory: MemoryAgentResult | None) -> tuple[str, ...]:
    """Supervisor가 기록이라고 보냈는데 Memory가 적지 않은 조각.

    어느 조각을 적었는지 raw_text로 확인할 수 있는 건 관찰뿐이고,
    일정·수정 힌트는 조회로 끝나거나 되묻고 끝나는 게 정상이라 여기서 판단하지 않음.
    """
    if task is None or memory is None:
        return ()

    written = [
        normalize(str(call.arguments.get("raw_text", "")))
        for call in memory.calls
        if call.success and call.name.startswith("create_observation_")
    ]
    return tuple(
        hint.text
        for hint in task.hints
        if WorkType(hint.work) is WorkType.OBSERVE and not _kept(normalize(hint.text), written)
    )


def _kept(hint: str, written: list[str]) -> bool:
    """힌트와 기록된 조각을 비교해 어느 쪽이 다른 쪽에 들어가면 적힌 것으로 본다."""
    return any(hint and text and (hint in text or text in hint) for text in written)


def _reroute_feedback(bounced: tuple[str, ...]) -> str:
    """Supervisor에게 돌려줄 실패 원인. 모델 입력이라 조각 원문이 들어간다 (로그에는 안 남긴다)."""
    listed = "\n".join(f"- {text}" for text in bounced)
    return (
        "[다시 나누기] 아래 조각을 record 로 보냈는데 Memory 가 기록하지 않았다.\n"
        f"{listed}\n"
        "기록할 사실이 아니라 요청이면 request로 바꾸고 담당 agent와 food 유형을 정한다. "
        "기록이 맞으면 record로 두고, 발화 전체를 다시 나눠 route를 부른다."
    )


def _did_anything(
    memory: MemoryAgentResult | None, food: list[FoodAgentResult], routing: Routing
) -> bool:
    """저장, 추천, 안내, 메모 중 하나라도 처리됐는지 확인한다.

    순수 요청형+구현중인 에이전트로 분기했을 경우 화면에 "준비 중" 을 띄울 수 있다.
    """
    wrote = memory is not None and any(
        call.success and call.name.startswith(MUTATING_PREFIXES) for call in memory.calls
    )
    note = memory is not None and bool(memory.final_message)
    return wrote or note or bool(food) or bool(routing.guidance) or bool(routing.unavailable_agents)


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
        "ended_by=%s saved=%d drafts=%d food=%s guidance=%s unavailable=%s note=%s "
        "rerouted=%s failed=%s memory_only=%s supervisor_only=%s model_calls=%d latency_ms=%d",
        result.run_id,
        result.routing.intent_type,
        result.routing.degraded,
        result.supervisor.error,
        len(result.routing.memory_task.hints) if result.routing.memory_task else 0,
        memory.steps if memory else None,
        memory.ended_by if memory else None,
        len(_saved_refs(memory)) if memory else 0,
        len(memory.drafts) if memory else 0,
        [str(item.task_type) for item in result.food],
        [guidance.code for guidance in result.routing.guidance],
        list(result.routing.unavailable_agents),
        bool(memory and memory.final_message),
        result.rerouted.bounced if result.rerouted else 0,
        result.failed.reason if result.failed else None,
        list(result.disagreement.memory_only),
        list(result.disagreement.supervisor_only),
        result.model_calls,
        result.latency_ms,
    )
    # 다시 나눈 run 은 Supervisor 호출이 하나 더 붙는다. 그만큼만 예산을 늘려 잡는다
    budget = MAX_MODEL_CALLS + (1 if result.rerouted else 0)
    if result.model_calls > budget:
        logger.warning(
            "model_calls 예산 초과 run_id=%s model_calls=%d 예산=%d",
            result.run_id,
            result.model_calls,
            budget,
        )
