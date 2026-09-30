"""Supervisor 출력으로부터 실제 호출 대상을 결정한다.
추가 LLM 호출 없이 코드 규칙만으로 처리한다.

이 모듈에서 결정하는 것:
  - 의도 유형
      record(기록형) / request(요청형) / mixed(혼합형)
      Supervisor 결과를 바탕으로 코드에서 결정하며, LLM에 다시 묻지 않는다.

  - MemoryTask
      원문 전체와 RECORD 조각 힌트를 전달한다.
      원문이 전부 요청·안내 조각이면 만들지 않는다 (SKIP_MEMORY_FOR_PURE_REQUEST).

  - DomainTask
      구현된 agent 로 간 REQUEST 조각을 agent × task_type 당 하나로 묶는다.
      task_type 은 Supervisor 라벨 문자열 그대로다. enum 변환은 각 Agent 의 task_type_of 가 한다.
      라벨이 없는 Agent(activity)는 None 이다.

  - 미구현 Agent 처리
      activity / growth / health 로 라우팅된 조각은
      대상 Agent 정보만 남기고 실제 호출은 하지 않는다.

  - Agent 호출 상한
      도메인 Agent는 최대 2개까지 호출한다.
      상한을 초과하면 뒤쪽 Agent를 제외한다.

  - 안내 문구
      guarded / out_of_scope 조각에는 코드에 정의된 고정 문구를 사용한다.

  - 강등(fallback)
      Supervisor 처리에 실패하면 Memory만 호출한다.
      이때 RECORD 힌트와 도메인 Agent 호출은 사용하지 않는다.

이 모듈에서 결정하지 않는 것:
  - 식이 단계: 아이 나이를 바탕으로 각 pipeline에서 계산
  - Memory가 사용할 테이블
  - 도메인 Agent가 사용할 tool
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.agents.common.schemas.task import MAX_DOMAIN_AGENTS, DomainTask
from app.agents.memory.schemas.task import MemoryHint, MemoryTask, WorkType
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.schemas import (
    DomainAgentName,
    GuardReason,
    SegmentKind,
    SupervisorOutput,
    align_segment,
    normalize,
)

logger = logging.getLogger(__name__)

IntentType = Literal["record", "request", "mixed"]

IMPLEMENTED_AGENTS: frozenset[str] = frozenset({DomainAgentName.FOOD.value})

# True: 순수 요청형(원문이 전부 요청, 안내 조각)이면 Memory를 건너뛴다.
#       (Supervisor가 의도를 제대로 나눴다고 보는 정책으로, 잘못 나누면 입력 정보가 그대로 삭제)
# False: Supervisor가 요청으로만 잘라도 Memory가 원문을 한 번 더 확인
SKIP_MEMORY_FOR_PURE_REQUEST = True


@dataclass(frozen=True)
class Guidance:
    """정해진 안내 한 건.

    계약서의 guards 와 code · message · deeplink 를 공유하지만 같은 것이 아니다.
    guards 는 POST /suggestions 응답 안의 칸이고 blocked_agents 를 더 싣는다.
    이쪽은 입력 run 의 SSE 로 나간다.
    """

    code: str
    message: str
    deeplink: str | None = None


# guard · out_of_scope 별 문구
_GUIDANCE: dict[str, Guidance] = {
    GuardReason.SAFETY_RECORD.value: Guidance(
        code=GuardReason.SAFETY_RECORD.value,
        message="알레르기·건강 정보는 직접 입력해 주세요. 대신 등록해 드릴 수 없어요.",
        # 앱에서 deeplink를 열면 알레르기 등록 화면으로 이동하도록
        deeplink="settings/health-safety",
    ),
    GuardReason.DIAGNOSIS.value: Guidance(
        code=GuardReason.DIAGNOSIS.value,
        message="진단이나 결핍 판정, 영양제·치료식 추천은 도와드릴 수 없어요. 기록은 남겨 둘게요.",
    ),
    SegmentKind.OUT_OF_SCOPE.value: Guidance(
        code=SegmentKind.OUT_OF_SCOPE.value,
        message="구매·예약·연락처럼 대신 해 드려야 하는 일은 도와드릴 수 없어요.",
    ),
}

# 한 번에 부를 수 있는 도메인 Agent 상한을 넘겨 빠진 요청이 있을 때 알리는 용도
# TODO: 사용자에게 제공될 답변 점검
AGENT_LIMIT = "agent_limit"
_AGENT_LIMIT = Guidance(
    code=AGENT_LIMIT,
    message=f"한 번에 {MAX_DOMAIN_AGENTS}가지까지 준비할 수 있어요. 나머지는 다시 말씀해 주세요.",
)


@dataclass(frozen=True)
class Routing:
    intent_type: IntentType
    memory_task: MemoryTask | None  # None은 SKIP_MEMORY_FOR_PURE_REQUEST가 켜졌을 때만
    domain_tasks: tuple[DomainTask, ...] = ()  # 구현된 agent 몫만
    unavailable_agents: tuple[str, ...] = ()  # 이동하려 했지만 아직 없는 agent
    dropped_agents: tuple[str, ...] = ()  # 호출 상한을 넘어 제외된 agent
    guidance: tuple[Guidance, ...] = ()
    degraded: bool = False  # Supervisor 실패 -> Memory 단독


def route(raw_text: str, result: SupervisorResult, *, run_id: str) -> Routing:
    """Supervisor 결과에서 이번 입력의 작업을 만든다."""
    if result.output is None:
        # 재시도하지 않고 Memory 단독으로 넘겨봄
        routing = Routing(
            intent_type="record", memory_task=MemoryTask(raw_text=raw_text), degraded=True
        )
        _log(routing, result.error)
        return routing

    segments = result.output.segments
    records = [s for s in segments if s.kind == SegmentKind.RECORD]
    requests = [s for s in segments if s.kind == SegmentKind.REQUEST]

    hints = tuple(MemoryHint(text=s.text, work=WorkType(s.work)) for s in records)
    skip_memory = SKIP_MEMORY_FOR_PURE_REQUEST and covers_only_requests(raw_text, result.output)
    memory_task = None if skip_memory else MemoryTask(raw_text=raw_text, hints=hints)

    # 요청한 agent는 처음 나온 순서대로, 상한까지만
    wanted = list(dict.fromkeys(str(s.agent) for s in requests))
    selected, dropped = wanted[:MAX_DOMAIN_AGENTS], wanted[MAX_DOMAIN_AGENTS:]

    domain_tasks = _domain_tasks(
        [s for s in requests if str(s.agent) in selected and str(s.agent) in IMPLEMENTED_AGENTS],
        run_id=run_id,
    )

    routing = Routing(
        intent_type=_intent(has_record=bool(records), has_request=bool(requests)),
        memory_task=memory_task,
        domain_tasks=domain_tasks,
        unavailable_agents=tuple(a for a in selected if a not in IMPLEMENTED_AGENTS),
        dropped_agents=tuple(dropped),
        guidance=_guidance(result.output, dropped=bool(dropped)),
    )
    _log(routing, None)
    return routing


def _intent(*, has_record: bool, has_request: bool) -> IntentType:
    """의도 3형. guarded · out_of_scope · unclear는 영향이 없다."""
    if has_request:
        return "mixed" if has_record else "request"
    return "record"


def _domain_tasks(segments: list, *, run_id: str) -> tuple[DomainTask, ...]:
    """agent × task_type 당 하나. 처음 나온 순서를 따른다."""
    texts: dict[tuple[str, str | None], list[str]] = {}
    for segment in segments:
        texts.setdefault((str(segment.agent), _task_label(segment)), []).append(segment.text)
    return tuple(
        DomainTask(run_id=run_id, agent=agent, task_type=task_type, request_texts=tuple(items))
        for (agent, task_type), items in texts.items()
    )


def _task_label(segment) -> str | None:
    """Supervisor 가 agent 별로 붙이는 라벨. 지금은 food_task 하나뿐이다.

    TODO: Activity·Growth·Health 라벨이 Supervisor 스키마에 생기면 여기만 늘린다.
    """
    if segment.agent == DomainAgentName.FOOD:
        return str(segment.food_task)
    return None


def _guidance(output: SupervisorOutput, *, dropped: bool = False) -> tuple[Guidance, ...]:
    """guard · out_of_scope 별 안내. 같은 코드는 한 번만.

    상한을 넘겨 버린 요청이 있으면 그것도 알린다 — 조용히 빠지면 사용자는 답이 없는 이유를 모른다.
    """
    codes = []
    for segment in output.segments:
        if segment.kind == SegmentKind.GUARDED:
            codes.append(str(segment.guard))
        elif segment.kind == SegmentKind.OUT_OF_SCOPE:
            codes.append(SegmentKind.OUT_OF_SCOPE.value)
    guidance = [_GUIDANCE[code] for code in dict.fromkeys(codes)]
    if dropped:
        guidance.append(_AGENT_LIMIT)
    return tuple(guidance)


def covers_only_requests(raw_text: str, output: SupervisorOutput) -> bool:
    """원문의 내용 글자가 전부 request · guarded · out_of_scope 조각에 들어가는가.

    참이면 기록할 게 없는 순수 요청형이다 — Memory를 건너뛸 후보.
    RECORD · UNCLEAR 조각이 하나라도 있거나, 어느 조각에도 안 들어간 글자가 있으면
    Supervisor 가 놓친 기록일 수 있다.
    """
    if any(s.kind in (SegmentKind.RECORD, SegmentKind.UNCLEAR) for s in output.segments):
        return False

    haystack = normalize(raw_text)
    covered = [False] * len(haystack)
    for segment in output.segments:
        for start, end in align_segment(raw_text, segment) or []:
            for index in range(start, end):
                covered[index] = True
    return all(covered[i] for i, char in enumerate(haystack) if char.isalnum())


def _log(routing: Routing, supervisor_error: str | None) -> None:
    """원문·조각 없이 개수와 라벨만"""
    logger.info(
        "routing intent=%s degraded=%s supervisor_error=%s hints=%d lookup_edit=%s "
        "domain_tasks=%s unavailable=%s dropped=%s guidance=%s memory=%s",
        routing.intent_type,
        routing.degraded,
        supervisor_error,
        len(routing.memory_task.hints) if routing.memory_task else 0,
        routing.memory_task.open_lookup_edit if routing.memory_task else False,
        [f"{task.agent}:{task.task_type}" for task in routing.domain_tasks],
        list(routing.unavailable_agents),
        list(routing.dropped_agents),
        [guidance.code for guidance in routing.guidance],
        routing.memory_task is not None,
    )
