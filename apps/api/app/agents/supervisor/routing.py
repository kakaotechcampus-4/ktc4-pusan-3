"""Supervisor 출력으로부터 실제 호출 대상을 결정한다.
추가 LLM 호출 없이 코드 규칙만으로 처리한다.

이 모듈에서 결정하는 것:
  - 의도 유형
      record(기록형) / request(요청형) / mixed(혼합형)
      Supervisor 결과를 바탕으로 코드에서 결정하며, LLM에 다시 묻지 않는다.

  - MemoryTask
      원문 전체와 RECORD 조각 힌트를 전달한다.
      Memory는 항상 호출한다.

  - FoodTask
      agent=food인 REQUEST 조각을
      식단 추천 / 영양소 분석 유형별로 묶어 전달한다.

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
      이때 RECORD 힌트와 Food 호출은 사용하지 않는다.

이 모듈에서 결정하지 않는 것:
  - 식이 단계: 아이 나이를 바탕으로 각 pipeline에서 계산
  - Memory가 사용할 테이블
  - Food가 사용할 tool
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.task import FoodTask
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

MAX_DOMAIN_AGENTS = 2  # 한 입력에서 부르는 도메인 Agent 상한
IMPLEMENTED_AGENTS: frozenset[str] = frozenset({DomainAgentName.FOOD.value})

# 순수 요청형에서 Memory를 건너뛸지. 켜면 Supervisor 오류가 곧 기록 누락이 된다
SKIP_MEMORY_FOR_PURE_REQUEST = False


@dataclass(frozen=True)
class Guidance:
    """정해진 안내 한 건. 계약서 guards 와 같은 모양(code · message · deeplink)."""

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


@dataclass(frozen=True)
class Routing:
    intent_type: IntentType
    memory_task: MemoryTask | None  # None은 SKIP_MEMORY_FOR_PURE_REQUEST가 켜졌을 때만
    food_tasks: tuple[FoodTask, ...] = ()
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

    food_tasks: tuple[FoodTask, ...] = ()
    if DomainAgentName.FOOD.value in selected:
        food_tasks = _food_tasks(
            [s for s in requests if s.agent == DomainAgentName.FOOD], run_id=run_id
        )

    routing = Routing(
        intent_type=_intent(has_record=bool(records), has_request=bool(requests)),
        memory_task=memory_task,
        food_tasks=food_tasks,
        unavailable_agents=tuple(a for a in selected if a not in IMPLEMENTED_AGENTS),
        dropped_agents=tuple(dropped),
        guidance=_guidance(result.output),
    )
    _log(routing, None)
    return routing


def _intent(*, has_record: bool, has_request: bool) -> IntentType:
    """의도 3형. guarded · out_of_scope · unclear는 영향이 없다."""
    if has_request:
        return "mixed" if has_record else "request"
    return "record"


def _food_tasks(segments: list, *, run_id: str) -> tuple[FoodTask, ...]:
    """유형별로 하나씩 — 식단 추천 1개 / 영양소 분석 1개까지. 처음 나온 순서를 따른다."""
    texts: dict[FoodTaskType, list[str]] = {}
    for segment in segments:
        texts.setdefault(FoodTaskType(segment.food_task), []).append(segment.text)
    return tuple(
        FoodTask(run_id=run_id, task_type=task_type, request_texts=tuple(items))
        for task_type, items in texts.items()
    )


def _guidance(output: SupervisorOutput) -> tuple[Guidance, ...]:
    """guard · out_of_scope 별 안내. 같은 코드는 한 번만."""
    codes = []
    for segment in output.segments:
        if segment.kind == SegmentKind.GUARDED:
            codes.append(str(segment.guard))
        elif segment.kind == SegmentKind.OUT_OF_SCOPE:
            codes.append(SegmentKind.OUT_OF_SCOPE.value)
    return tuple(_GUIDANCE[code] for code in dict.fromkeys(codes))


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
        "food_tasks=%s unavailable=%s dropped=%s guidance=%s memory=%s",
        routing.intent_type,
        routing.degraded,
        supervisor_error,
        len(routing.memory_task.hints) if routing.memory_task else 0,
        routing.memory_task.open_lookup_edit if routing.memory_task else False,
        [str(task.task_type) for task in routing.food_tasks],
        list(routing.unavailable_agents),
        list(routing.dropped_agents),
        [guidance.code for guidance in routing.guidance],
        routing.memory_task is not None,
    )
