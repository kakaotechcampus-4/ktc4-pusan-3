"""사용자 입력을 Supervisor, Memory, 도메인 Agent 순서로 처리한다.

Memory 저장이 끝난 뒤 도메인 Agent를 실행해 같은 요청에서 저장된 관찰도 바로 조회할 수 있게 한다.
Supervisor 실패 시에는 Memory가 원문을 처리하고, Memory 실패 시 전체 요청을 실패로 처리한다.

Memory 가 끝나면 러너가 넘긴 commit 으로 기록 단계를 확정하고, 저장 안내는 그 뒤에 낸다.
도메인 단계는 쓰는 task 를 먼저 끝낸 뒤 읽는 task 를 돌린다.
도메인 결과는 끝나는 대로 내보내고, Partial은 다 끝나거나 20초가 된 뒤 한 번 낸다.

Supervisor가 요청을 기록으로 잘못 나누면 그 조각은 어디서도 처리되지 않는다.
Memory가 적지 않은 RECORD 조각이 남으면 그 이유를 Supervisor에 돌려주고 한 번만 다시 나눈다.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from app.agents.common.llm_client import LLMClient, LLMError
from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run as run_food
from app.agents.food.registry import WRITING_TASKS as FOOD_WRITING_TASKS
from app.agents.memory.agent import MemoryAgentResult
from app.agents.memory.agent import run as run_memory
from app.agents.memory.bundles import MUTATING_PREFIXES, WRITES_FOR
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.schemas.reply import ReplyKind
from app.agents.memory.schemas.task import MemoryTask, PendingMemoryContext, WorkType
from app.agents.run_writes import RunWrites
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.agent import run as run_supervisor
from app.agents.supervisor.routing import Guidance, Routing, route
from app.agents.supervisor.schemas import DomainAgentName, normalize

logger = logging.getLogger(__name__)

# model_calls는 Agent 진입 1회 + 재시도 1회당 +1 로 센다.
#   - 정상 tool calling 루프는 안 센다. Memory가 7바퀴를 돌아도 1이다
#   - 재시도는 센다. Supervisor의 tool_choice 폴백, 도메인의 안전 필터 재호출
# 구분 기준은 "하려던 일을 하는 중인가(안 센다) / 실패해서 다시 하는가(센다)" 다.
#
# 정상값은 Supervisor 1 + Memory 1 + 도메인 2 = 4. 다시 나눈 run 은 아래에서 +1 한다.
# 이 값은 실행을 막지 않고, 넘으면 _log가 경고만 남긴다.
# Agent 안의 루프는 각자의 상한이 막는다 (Memory는 MAX_STEPS=7).
MAX_MODEL_CALLS = 4

# NF-06 — 입력부터 이만큼 지나면 도메인 Agent를 끊고 있는 결과로 끝낸다.
# Supervisor·Memory는 끊지 않는다. 저장을 중간에 자르면 기록이 반만 남는다.
# 둘이 쓴 만큼 도메인 몫이 줄고, 이미 다 썼으면 도메인 Agent는 시작하자마자 끊긴다
RUN_DEADLINE_S = 20.0

# Memory 가 기록하지 않은 RECORD 조각이 있으면 Supervisor 에게 이유를 주고 한 번 더 나눠 본다.
# 조각을 잘못 위임하면 그 요청은 아무 데서도 처리되지 않는다 — 호출 하나를 더 쓰는 값이 있다.
# 다시 나누는 것은 run 당 한 번뿐이고, 그때만 예산이 MAX_MODEL_CALLS + 1 이 된다
REROUTE_MISDELEGATED = True

# no_child_observation 판정 기준은 추후 추가
FailReason = Literal["unparsable", "llm_unavailable"]


class DomainOutcome(Protocol):
    """pipeline 이 도메인 Agent 결과에서 읽는 것."""

    @property
    def agent(self) -> str: ...
    @property
    def task_type(self) -> str | None: ...
    @property
    def status(self) -> str: ...
    @property
    def model_calls(self) -> int: ...


DomainRunner = Callable[[DomainTask, Any], Awaitable[DomainOutcome]]

# 기록 단계를 확정하는 함수. 러너가 run마다 만들어 넘기고, pipeline은 Memory 다음에 한 번 부른다.
# 세션과 commit 구현은 러너(백엔드). pipeline은 기록 단계가 끝난 시점만 알린다.
# 러너 약속 — commit이 한 번이라도 끝난 run은 failed로 끝내지 않는다. 60초 안전망은
#   pipeline 결과 없이 끝나므로, 러너는 이 함수 안에서 commit 여부를 직접 표시해 두고
#   _guarded · 안전망 · forget_run 이 그 표시를 본다 (러너 구현은 백엔드 몫).
# TODO(러너 계약): commit 도중 연결이 끊겨 결과를 모르면 failed 로 확정하지 않고
#   Idempotency 키도 풀지 않는다. 요청 식별자와 처리 결과를 DB 에 남겨 같은 키로 다시
#   보내면 기존 결과를 확인한다(멘토 #196 답변). 모양은 러너 DB 세션 작업 전에 정한다.
Commit = Callable[[], Awaitable[None]]


class DomainContext(Protocol):
    def for_task(self) -> Self:
        """task 하나 몫의 context. 같은 Agent 의 task 둘이 동시에 돌아서 run state 를 나눈다."""
        ...


PartialReason = Literal["timeout_20s", "agent_error"]

# 구현된 도메인 Agent 의 실행 함수. routing.IMPLEMENTED_AGENTS 와 키가 같아야 한다
_RUNNERS: dict[str, DomainRunner] = {
    DomainAgentName.FOOD.value: run_food,
}

# 같은 run의 다른 task가 읽는 행을 쓰는 라벨. 각 Agent registry가 정한다.
# 이 task를 먼저 끝낸 뒤 나머지를 동시에 돌린다(기록 단계 → 쓰는 task → 읽는 task)
_WRITING_TASKS: dict[str, frozenset[str]] = {
    DomainAgentName.FOOD.value: frozenset(task.value for task in FOOD_WRITING_TASKS),
}


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
class DomainRouted:
    """도메인 Agent 하나가 결과를 냈을 때 로그·지표용. 화면에 보내는 건 app/api가 정한다.

    끝나는 대로 나간다. 쓰는 task의 것이 먼저고, 읽는 task 끼리는 끝난 순서다.
    """

    agent: str
    task_type: str | None
    status: str
    model_calls: int


@dataclass(frozen=True)
class Unavailable:
    agents: tuple[str, ...]  # 아직 지원하지 않는 도메인 Agent


@dataclass(frozen=True)
class MemoryNote:
    text: str  # Memory 응답 메시지
    kind: ReplyKind  # question 이면 화면이 "이어서 적기" 를 연다. 맥락이 있을 때만 question


@dataclass(frozen=True)
class PendingReply:
    """되묻기로 끝난 run. API가 run_id에 매달아 두고 다음 입력의 reply_to로 찾는다.

    받는 쪽은 API의 pending store이다.
    """

    run_id: str
    context: PendingMemoryContext


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
    domain_tasks: tuple[str, ...] = ()  # 다시 나눈 뒤의 도메인 작업. "food:meal_recommendation" 꼴


@dataclass(frozen=True)
class Partial:
    """도메인 Agent 일부가 결과를 못 낸 run. SSE 이름은 partial.

    화면 계약(apps/web/src/lib/api/sse.ts 의 PartialEvent)과 같은 모양으로,
    한 Agent의 task 둘 중 하나만 실패하면 그 Agent는 succeeded와 failed 양쪽에 들어간다.
    """

    reason: PartialReason
    succeeded: tuple[str, ...]
    failed: tuple[str, ...]


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
    | DomainRouted
    | Unavailable
    | Guidance
    | MemoryNote
    | PendingReply
    | Unwritten
    | Rerouted
    | Partial
    | Failed
    | Done
)
Emit = Callable[[Event], None]

_LABELS = ("입력을 살펴보고 있어요", "관찰을 나누고 있어요", "다음 행동을 준비하고 있어요")
# 도메인 작업이 없으면 세 번째 단계는 생략
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
class StageTimes:
    """단계마다 걸린 시간(ms). 그 단계를 돌지 않았으면 None. 원문 없이 로그로만 남는다.

    도메인 단계에 최소 예산을 따로 줄지 실측으로 정하기 위해 남긴다.
    """

    supervisor_ms: int | None = None  # 다시 나눈 run 은 두 번째 호출까지 더한다
    memory_ms: int | None = None  # 기록 단계. Memory + commit
    budget_ms: int | None = None  # 도메인 단계를 시작할 때 20초에서 남은 몫
    writers_ms: int | None = None  # 쓰는 도메인 task 묶음
    readers_ms: int | None = None  # 읽는 도메인 task 묶음


@dataclass(frozen=True)
class _DomainRun:
    """_run_domain 결과."""

    outcomes: tuple[DomainOutcome, ...]  # 결과를 낸 것만, task 순서
    partial: Partial | None
    writers_ms: int | None = None  # 그 묶음이 없었으면 None
    readers_ms: int | None = None


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    supervisor: SupervisorResult  # 원문 조각은 로그나 저장소에 남기지 않는다
    routing: Routing
    memory: MemoryAgentResult | None  # Memory 미실행 또는 실패 시 None
    domain: tuple[DomainOutcome, ...] = ()  # 결과를 낸 도메인 Agent
    rerouted: Rerouted | None = None  # 다시 나눴으면 그 결과. routing 은 다시 나눈 쪽이다
    partial: Partial | None = None  # 도메인 Agent가 일부 빠진 경우
    failed: Failed | None = None
    # 이 run 에서 확정된 쓰기가 있었는지. True면 failed가 아니다
    committed: bool = False
    disagreement: Disagreement = Disagreement()
    model_calls: int = 0
    latency_ms: int = 0
    times: StageTimes = StageTimes()

    @property
    def ok(self) -> bool:
        return self.failed is None


async def handle_input(
    raw_text: str,
    memory_context: AgentContext,
    contexts: Mapping[str, DomainContext],
    *,
    run_id: str,
    supervisor_client: LLMClient | None = None,
    memory_client: LLMClient | None = None,
    emit: Emit | None = None,
    continuation: PendingMemoryContext | None = None,
    commit: Commit | None = None,
    writes: RunWrites | None = None,
) -> PipelineResult:
    """사용자 입력 한 건을 처리한다.

    continuation 이 있으면 raw_text 는 이전 run 의 질문에 대한 답이다 (_handle_continuation).
    contexts는 도메인 Agent 이름 → 그 Agent 의 context.
    구현된 Agent 몫은 전부 있어야 하고, 포트는 호출 전에 만들어 넘긴다.
    commit은 Memory가 끝난 직후 한 번 부른다. 기록 단계 세션을 확정하는 러너의 함수다.
    TODO: 러너가 DB 세션을 붙이면 기본값 None을 없앤다. 지금은 run 마다 InMemoryStore라
      확정할 것이 없어 러너가 넘기지 않는다.
    writes는 도메인 쓰기 포트가 성공했는지 남는 표시다. entrypoint가 쓰기 포트를 감싸 넘긴다.
    """
    started = time.perf_counter()
    send = emit or _ignore
    _safety_precheck(raw_text)

    if continuation is not None:
        return await _handle_continuation(
            raw_text,
            memory_context,
            run_id=run_id,
            continuation=continuation,
            memory_client=memory_client,
            send=send,
            started=started,
            commit=commit,
        )

    send(Step(1, _TOTAL_STEPS, _LABELS[0]))
    supervisor_started = time.perf_counter()
    supervisor = await run_supervisor(raw_text, client=supervisor_client)
    supervisor_ms = _ms_since(supervisor_started)
    routing = route(raw_text, supervisor, run_id=run_id)
    model_calls = supervisor.model_calls

    for guidance in routing.guidance:
        send(guidance)
    if routing.unavailable_agents:
        send(Unavailable(routing.unavailable_agents))

    memory: MemoryAgentResult | None = None
    failed: Failed | None = None
    committed = False
    memory_ms: int | None = None
    # 기록할 조각이 있으면 Memory를 부른다. 원문이 전부 요청·안내 조각이면 건너뛴다
    if routing.memory_task is not None:
        send(Step(2, _TOTAL_STEPS, _LABELS[1]))
        record_started = time.perf_counter()
        try:
            memory = await run_memory(
                raw_text, memory_context, client=memory_client, task=routing.memory_task
            )
        except LLMError:
            memory_ms = _ms_since(record_started)
            # Memory 실패 시 기본값으로 대체하지 않는다. commit 전이라 러너가 되돌린다
            failed = Failed("llm_unavailable", raw_text)
        else:
            model_calls += 1  # Agent 하나가 1. 루프를 몇 바퀴 돌았는지는 memory.steps
        if memory is not None and not memory.completed:
            memory_ms = _ms_since(record_started)
            # 반복 상한에 걸려 기록을 다 끝내지 못하면 일부만 남지 않게 commit 하지 않고
            # (러너가 되돌린다) failed로 끝낸다. 저장 안내/되묻기 맥락도 내지 않는다
            failed = Failed("unparsable", raw_text)
        elif memory is not None:
            # 기록 단계를 먼저 확정한다. 저장 안내 · 되묻기 맥락은 확정된 것만 나간다
            committed = await _commit_record(memory, commit)
            memory_ms = _ms_since(record_started)  # 기록 단계 = Memory + commit
            # TODO(#149-integration): DB 저장소가 붙으면 여기서 Curator 를 백그라운드로 띄운다.
            #   관찰이 commit 된 바로 뒤다. InMemoryStore 에서는 동작하지 않는다.
            #   from app.domains.memory.curator.trigger import trigger_curator_background
            #   trigger_curator_background(child_id, today, embedder, judge)
            refs = _saved_refs(memory)
            if refs:
                send(Saved(refs))
            # 저장된 것과 제출을 기다리는 것을 한 run에서 같이 내보낸다
            if memory.drafts:
                send(EventDrafts(memory.drafts))
            note = _memory_note(memory)
            if note is not None:
                send(note)
            if memory.pending is not None:
                send(PendingReply(run_id, memory.pending))
            unwritten = _unwritten(routing.memory_task, memory)
            if unwritten is not None:
                send(unwritten)

    # 위임이 어긋났으면 Supervisor에게 이유를 주고 한 번만 다시 나눈다.
    # Memory는 다시 돌리지 않는다.
    rerouted: Rerouted | None = None
    bounced = _bounced_hints(routing.memory_task, memory) if failed is None else ()
    # 도메인 Agent로 간 조각이 있으면 다시 나누지 않는다. 요청이 통째로 사라진 경우만 본다.
    stranded = not routing.domain_tasks and not routing.unavailable_agents
    # 되묻고 있는 조각은 위임이 어긋난 게 아니라 답을 기다리는 것이다. 다시 나누면 Supervisor
    # 호출을 한 번 더 쓰고, 그 조각이 도메인 Agent로 옮겨가 답이 와도 이어 붙일 자리가 없어진다
    waiting = memory is not None and memory.pending is not None
    if bounced and stranded and REROUTE_MISDELEGATED and not waiting:
        reroute_started = time.perf_counter()
        retry = await run_supervisor(
            raw_text, client=supervisor_client, feedback=_reroute_feedback(bounced)
        )
        supervisor_ms += _ms_since(reroute_started)
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
            rerouted = Rerouted(
                len(bounced), tuple(_label(t.agent, t.task_type) for t in routing.domain_tasks)
            )
            send(rerouted)
        else:
            logger.info("reroute 실패 run_id=%s error=%s", run_id, retry.error)

    domain: list[DomainOutcome] = []
    partial: Partial | None = None
    budget_ms: int | None = None
    writers_ms: int | None = None
    readers_ms: int | None = None
    if failed is None and routing.domain_tasks:
        # Memory 가 성공한 뒤에 돈다. 방금 저장한 관찰이 도메인 Agent 조회에 잡혀야 한다
        send(Step(3, _TOTAL_STEPS, _LABELS[2]))
        remaining = max(0.0, RUN_DEADLINE_S - (time.perf_counter() - started))
        budget_ms = int(remaining * 1000)
        # 결과는 끝나는 대로 내보낸다 (K-11). Partial 은 다 끝나거나 20초가 된 뒤 아래에서 한 번
        ran = await _run_domain(
            routing.domain_tasks,
            contexts,
            timeout=remaining,
            on_outcome=lambda outcome: send(_routed(outcome)),
        )
        partial = ran.partial
        writers_ms, readers_ms = ran.writers_ms, ran.readers_ms
        # 쓰기 포트는 호출마다 바로 commit. 포트 호출이 성공한 뒤에만 표시가 남는다
        committed = committed or (writes is not None and writes.wrote)
        for outcome in ran.outcomes:
            domain.append(outcome)
            model_calls += outcome.model_calls

    if failed is None and not committed and not _did_anything(memory, domain, routing):
        # 도메인 Agent 가 전부 실패했고 남은 결과도 없으면 부분 결과가 아니라 실패다
        failed = Failed("llm_unavailable" if partial is not None else "unparsable", raw_text)
    if failed is not None:
        send(failed)
    elif partial is not None:
        send(partial)
    send(Done(run_id, model_calls))

    result = PipelineResult(
        run_id=run_id,
        supervisor=supervisor,
        routing=routing,
        memory=memory,
        domain=tuple(domain),
        rerouted=rerouted,
        partial=partial,
        failed=failed,
        committed=committed,
        disagreement=_disagreement(routing, memory),
        model_calls=model_calls,
        latency_ms=_ms_since(started),
        times=StageTimes(
            supervisor_ms=supervisor_ms,
            memory_ms=memory_ms,
            budget_ms=budget_ms,
            writers_ms=writers_ms,
            readers_ms=readers_ms,
        ),
    )
    _log(result)
    return result


# 이어받기 run의 routing 자리. 안내·준비 중 agent가 없어 _did_anything에는 쓴 것과 말만 남는다
_NO_ROUTING = Routing(intent_type="record", memory_task=None)

# 이어받기 답에 섞여 온 다른 말은 처리하지 않는다.
# 다시 보내도 두 번 저장되지 않게 반영하지 않았다는 것까지 알린다.
LEFTOVER_NOTE = "답변과 함께 적은 다른 내용은 반영하지 않았어요. 따로 한 줄로 보내 주세요."
# 다시 묻는 중이면 화면이 note 전체를 질문으로 잡고 다음 한 줄을 그 답으로 보낸다.
# 따로 보낼 것은 질문에 답한 뒤에 보내게 한다
LEFTOVER_NOTE_QUESTION = (
    "적어 주신 다른 내용은 반영하지 않았어요. 이 질문에 먼저 답한 뒤 따로 한 줄로 보내 주세요."
)
# 맥락(pending) 없이 묻는 질문. question으로 내보내면 화면이 "이어서 적기"를 열고,
# 그 답은 reply_to로 가서 400을 받는다. message로 내리고 새 한 줄로 받는다
NO_CONTEXT_NOTE = "답은 무엇에 대한 것인지 함께 적어 따로 한 줄로 보내 주세요."
LEFTOVER_NOTE_NO_CONTEXT = (
    "적어 주신 다른 내용은 반영하지 않았어요. 그 내용과 이 질문의 답은 각각 따로 한 줄로 "
    "보내 주세요. 답에는 무엇에 대한 것인지 함께 적어 주세요."
)


def _memory_note(memory: MemoryAgentResult) -> MemoryNote | None:
    """Memory 의 마지막 말을 note로 만든다. 뒤에 붙는 문장은 코드가 정한다.

    화면은 kind 만 보고 "이어서 적기" 를 연다. 그래서 question은 맥락이 있을 때만 내보낸다.
    """
    reply = memory.reply
    if reply is None:
        return None
    if reply.kind == "question" and memory.pending is None:
        suffix = LEFTOVER_NOTE_NO_CONTEXT if memory.leftover else NO_CONTEXT_NOTE
        return MemoryNote(text=f"{reply.text} {suffix}", kind="message")
    if memory.leftover:
        suffix = LEFTOVER_NOTE_QUESTION if reply.kind == "question" else LEFTOVER_NOTE
        return MemoryNote(text=f"{reply.text} {suffix}", kind=reply.kind)
    return MemoryNote(text=reply.text, kind=reply.kind)


async def _handle_continuation(
    answer: str,
    memory_context: AgentContext,
    *,
    run_id: str,
    continuation: PendingMemoryContext,
    memory_client: LLMClient | None,
    send: Emit,
    started: float,
    commit: Commit | None,
) -> PipelineResult:
    """이전 run의 되묻기를 이어받는다. Supervisor와 도메인 Agent를 타지 않는다."""
    send(Step(1, 1, "이어서 적은 내용을 살펴보고 있어요"))
    memory: MemoryAgentResult | None = None
    failed: Failed | None = None
    committed = False
    model_calls = 0
    memory_ms: int | None = None
    record_started = time.perf_counter()
    try:
        memory = await run_memory(
            answer, memory_context, client=memory_client, continuation=continuation
        )
    except LLMError:
        memory_ms = _ms_since(record_started)
        failed = Failed("llm_unavailable", answer)
    else:
        model_calls = 1
    if memory is not None and not memory.completed:
        memory_ms = _ms_since(record_started)
        # 반복 상한 — 일반 run과 같이 commit 하지 않고 failed로 끝냄
        failed = Failed("unparsable", answer)
    elif memory is not None:
        committed = await _commit_record(memory, commit)
        memory_ms = _ms_since(record_started)
        refs = _saved_refs(memory)
        if refs:
            send(Saved(refs))
        if memory.drafts:
            send(EventDrafts(memory.drafts))
        note = _memory_note(memory)
        if note is not None:
            send(note)
        if memory.pending is not None:
            send(PendingReply(run_id, memory.pending))

    # 쓴 것도 한 말도 없으면 실패 처리
    if failed is None and not _did_anything(memory, [], _NO_ROUTING):
        failed = Failed("unparsable", answer)
    if failed is not None:
        send(failed)
    send(Done(run_id, model_calls))

    result = PipelineResult(
        run_id=run_id,
        supervisor=SupervisorResult(output=None),
        routing=_NO_ROUTING,
        memory=memory,
        failed=failed,
        committed=committed,
        model_calls=model_calls,
        latency_ms=_ms_since(started),
        times=StageTimes(memory_ms=memory_ms),
    )
    _log(result)
    return result


async def _run_domain(
    tasks: tuple[DomainTask, ...],
    contexts: Mapping[str, DomainContext],
    *,
    timeout: float | None,
    on_outcome: Callable[[DomainOutcome], None] | None = None,
) -> _DomainRun:
    """쓰는 task 를 먼저 끝내고 읽는 task 를 동시에 돌린다.

    결과는 끝나는 대로 on_outcome 으로 넘긴다 (K-11). 쓰는 task 의 결과는 읽는 task 가 시작하기
    전에 나가고, 읽는 task 끼리는 끝난 순서다. 돌려주는 outcomes 는 그대로 task 순서다.
    하나가 죽거나 늦어도 나머지 결과는 낸다 (NF-06). 쓰는 task 도 같은 timeout 안에서 센다.
    쓰는 쪽이 늦으면 읽는 쪽 몫이 준다.
    """
    # 표에 없는 agent·빠진 context 는 배선 버그다. 부분 실패로 숨기지 않고 시작 전에 올린다
    prepared = [(task, _RUNNERS[task.agent], contexts[task.agent]) for task in tasks]
    deadline = None if timeout is None else time.perf_counter() + timeout
    writers = [index for index, task in enumerate(tasks) if _writes(task)]
    readers = [index for index, task in enumerate(tasks) if not _writes(task)]

    elapsed_ms: dict[int, int] = {}  # 시작도 못 하고 끊긴 task 는 없다

    async def run_one(index: int) -> DomainOutcome:
        task, runner, context = prepared[index]
        task_started = time.perf_counter()
        try:
            return await runner(task, context.for_task())
        finally:
            elapsed_ms[index] = _ms_since(task_started)

    settled: dict[int, DomainOutcome | BaseException] = {}
    group_ms: list[int | None] = []
    # 읽는 task 가 갱신된 행을 보게 쓰는 task 를 먼저 끝낸다.
    # 쓰는 task 가 실패하거나 끊겨도 읽는 task 는 돈다 (부분 결과 원칙 · 공통규약 §7)
    for group in (writers, readers):
        if not group:
            group_ms.append(None)
            continue
        group_started = time.perf_counter()
        remaining = None if deadline is None else max(0.0, deadline - time.perf_counter())
        settled.update(await _settle(group, run_one, remaining, on_outcome))
        group_ms.append(_ms_since(group_started))
    writers_ms, readers_ms = group_ms

    outcomes: list[DomainOutcome] = []
    succeeded: list[str] = []
    failed: list[str] = []
    timed_out = False
    for index, task in enumerate(tasks):
        item = settled[index]
        if isinstance(item, TimeoutError):
            timed_out = True
            failed.append(task.agent)
            result = "timeout"
            logger.warning("도메인 Agent 시간 초과 run_id=%s agent=%s", task.run_id, task.agent)
        elif isinstance(item, (Exception, asyncio.CancelledError)):
            failed.append(task.agent)
            result = "error"
            # 예외 메시지에는 발화 조각이 섞일 수 있다. 종류만 남긴다
            logger.error(
                "도메인 Agent 실패 run_id=%s agent=%s error=%s",
                task.run_id,
                task.agent,
                type(item).__name__,
            )
        elif isinstance(item, BaseException):
            raise item  # KeyboardInterrupt·SystemExit 는 부분 실패가 아니다
        else:
            outcomes.append(item)
            succeeded.append(task.agent)
            result = "ok"
        # 최소 예산을 정할 실측. Agent/유형별로 모으려고 task 마다 한 줄 (라벨과 ms 만)
        logger.info(
            "도메인 task run_id=%s task=%s result=%s ms=%s",
            task.run_id,
            _label(task.agent, task.task_type),
            result,
            elapsed_ms.get(index),
        )

    if not failed:
        return _DomainRun(tuple(outcomes), None, writers_ms, readers_ms)
    partial = Partial(
        reason="timeout_20s" if timed_out else "agent_error",
        succeeded=tuple(dict.fromkeys(succeeded)),
        failed=tuple(dict.fromkeys(failed)),
    )
    return _DomainRun(tuple(outcomes), partial, writers_ms, readers_ms)


async def _settle(
    group: list[int],
    run_one: Callable[[int], Awaitable[DomainOutcome]],
    timeout: float | None,
    on_outcome: Callable[[DomainOutcome], None] | None,
) -> dict[int, DomainOutcome | BaseException]:
    """묶음 하나를 동시에 돌리고, 끝나는 대로 on_outcome 으로 넘긴다. 실패는 예외 객체로 돌려준다.

    같은 틱에 같이 끝난 것만 task 순서로 넘긴다. 그 밖에는 끝난 순서다.
    on_outcome(화면 번역)이 터지면 부분 실패로 숨기지 않고 그대로 올린다.
    빠져나갈 때(그 예외 · 바깥 취소) 남은 task 를 끊는다 — gather 와 달리 wait 는 자식을 안 끊는다.
    """
    running = {
        asyncio.create_task(asyncio.wait_for(run_one(index), timeout)): index for index in group
    }
    settled: dict[int, DomainOutcome | BaseException] = {}
    pending = set(running)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for future in sorted(done, key=running.__getitem__):
                item = _result_of(future)
                settled[running[future]] = item
                if on_outcome is not None and not isinstance(item, BaseException):
                    on_outcome(item)
    finally:
        for future in pending:
            future.cancel()
        # 넘기지 못한 결과의 예외도 꺼내 둔다. 안 꺼내면 asyncio 가 GC 때 예외 메시지째 로그에
        # 찍는다(발화 조각이 섞일 가능성 존재). 끝난 것은 지금, 끊는 중인 것은 끝날 때 꺼낸다
        for future in running:
            if future.done():
                _retrieve(future)
            else:
                future.add_done_callback(_retrieve)
    return settled


def _retrieve(future: asyncio.Future[Any]) -> None:
    """예외를 꺼냈다고 표시만 한다. 메시지는 어디에도 쓰지 않는다."""
    if not future.cancelled():
        future.exception()


def _result_of(future: asyncio.Task[DomainOutcome]) -> DomainOutcome | BaseException:
    """끝난 task 의 결과. 예외도 값으로 돌려준다 (gather 의 return_exceptions 와 같다)."""
    if future.cancelled():
        return asyncio.CancelledError()
    return future.exception() or future.result()


def _writes(task: DomainTask) -> bool:
    """같은 run 의 다른 task 가 읽는 행을 쓰는 task 인가."""
    return task.task_type in _WRITING_TASKS.get(task.agent, frozenset())


def _ignore(event: Event) -> None:
    """emit 콜백이 없을 때 이벤트를 무시한다."""


def _safety_precheck(raw_text: str) -> None:
    """안전 사전검사를 위한 자리.

    실제 규칙은 app/rules/에 추가한다.
    """


def _ms_since(started: float) -> int:
    """perf_counter 로 잰 시작 시각부터 지금까지 ms."""
    return int((time.perf_counter() - started) * 1000)


async def _commit_record(memory: MemoryAgentResult, commit: Commit | None) -> bool:
    """기록 단계를 확정한다. DB 에 실제로 쓴 것이 있으면 True.

    일정 초안 · 바뀐 것 없는 성공은 세지 않는다(`MemoryAgentResult.persisted`) — 보호자가
    다시 보내도 두 번 저장될 것이 없다. commit 이 예외를 내면 잡지 않고 러너에 올린다.
    결과를 모르는 commit(연결이 끊긴 경우)을 어떻게 닫을지는 러너 계약에서 정한다.
    """
    if commit is not None:
        await commit()
    return memory.persisted


def _memory_wrote(memory: MemoryAgentResult | None) -> bool:
    """Memory 가 성공한 쓰기 tool 을 하나라도 불렀는가. 일정 초안도 센다.

    처리한 일이 있었는지를 볼 때 쓴다. 저장됐는지는 `MemoryAgentResult.persisted` 로 본다.
    """
    return memory is not None and any(
        call.success and call.name.startswith(MUTATING_PREFIXES) for call in memory.calls
    )


def _saved_refs(memory: MemoryAgentResult) -> tuple[Ref, ...]:
    """성공한 관찰 저장 결과만 반환"""
    return tuple(
        Ref(kind=call.result["resource"], id=call.result["data"]["id"])
        for call in memory.calls
        if call.success and call.name.startswith("create_observation_")
    )


def _routed(outcome: DomainOutcome) -> DomainRouted:
    task_type = None if outcome.task_type is None else str(outcome.task_type)
    return DomainRouted(outcome.agent, task_type, str(outcome.status), outcome.model_calls)


def _label(agent: str, task_type: object) -> str:
    """로그용 이름. 라벨이 없는 Agent 는 이름만."""
    return agent if task_type is None else f"{agent}:{task_type}"


def _unwritten(task: MemoryTask | None, memory: MemoryAgentResult) -> Unwritten | None:
    """기록할 조각을 짚었는데 쓰기가 하나도 없으면 남긴다. 판정은 하지 않는다."""
    if task is None or not task.hints:
        return None
    if _memory_wrote(memory):
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
    memory: MemoryAgentResult | None, domain: list[DomainOutcome], routing: Routing
) -> bool:
    """저장, 추천, 안내, 메모 중 하나라도 처리됐는지 확인한다.

    순수 요청형+구현중인 에이전트로 분기했을 경우 화면에 "준비 중" 을 띄울 수 있다.
    """
    wrote = _memory_wrote(memory)
    note = memory is not None and bool(memory.final_message)
    return (
        wrote or note or bool(domain) or bool(routing.guidance) or bool(routing.unavailable_agents)
    )


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
        "ended_by=%s saved=%d drafts=%d domain=%s guidance=%s unavailable=%s note=%s "
        "rerouted=%s partial=%s failed=%s committed=%s memory_only=%s supervisor_only=%s "
        "model_calls=%d latency_ms=%d supervisor_ms=%s memory_ms=%s budget_ms=%s "
        "writers_ms=%s readers_ms=%s",
        result.run_id,
        result.routing.intent_type,
        result.routing.degraded,
        result.supervisor.error,
        len(result.routing.memory_task.hints) if result.routing.memory_task else 0,
        memory.steps if memory else None,
        memory.ended_by if memory else None,
        len(_saved_refs(memory)) if memory else 0,
        len(memory.drafts) if memory else 0,
        [_label(item.agent, item.task_type) for item in result.domain],
        [guidance.code for guidance in result.routing.guidance],
        list(result.routing.unavailable_agents),
        bool(memory and memory.final_message),
        result.rerouted.bounced if result.rerouted else 0,
        (result.partial.reason, list(result.partial.failed)) if result.partial else None,
        result.failed.reason if result.failed else None,
        result.committed,
        list(result.disagreement.memory_only),
        list(result.disagreement.supervisor_only),
        result.model_calls,
        result.latency_ms,
        result.times.supervisor_ms,
        result.times.memory_ms,
        result.times.budget_ms,
        result.times.writers_ms,
        result.times.readers_ms,
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
