"""API가 부르는 agents 진입점.

api 레이어는 이 모듈만 import한다. 밖으로는 handle_input 과 그 입출력·이벤트 타입만 내보낸다.
"""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.food.context import FoodContext
from app.agents.food.store import InMemoryProfile, in_memory_ports
from app.agents.memory.agent import MemoryReply
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.schemas.task import PendingMemoryContext
from app.agents.memory.store import InMemoryStore, MemoryStore
from app.agents.memory_bridge import StoreFoodMemory
from app.agents.pipeline import (
    Commit,
    DomainRouted,
    Done,
    Emit,
    Event,
    EventDrafts,
    Failed,
    MemoryNote,
    Partial,
    PendingReply,
    PipelineResult,
    Ref,
    Rerouted,
    Saved,
    Step,
    Unavailable,
    Unwritten,
)
from app.agents.pipeline import handle_input as _handle_input
from app.agents.run_writes import RunWrites, record_food_writes
from app.agents.supervisor.routing import Guidance

# api가 이 파일만 보면 되도록 진행 이벤트 타입도 여기서 내보냄
# pipeline에 이벤트를 추가하면 여기에도 작성
__all__ = [
    "Commit",
    "DomainRouted",
    "Done",
    "Emit",
    "Event",
    "EventDraft",
    "EventDrafts",
    "Failed",
    "Guidance",
    "InMemoryStore",
    "MemoryNote",
    "MemoryReply",
    "MemoryStore",
    "Partial",
    "PendingMemoryContext",
    "PendingReply",
    "PipelineResult",
    "Ref",
    "Rerouted",
    "Saved",
    "Step",
    "Unavailable",
    "Unwritten",
    "handle_input",
]

KST = ZoneInfo("Asia/Seoul")

# 생일을 못 받았을 때만 쓰는 fallback. 만 2세로 가정해 확실히 toddler 단계로 연다
# (LifeStage.stage 네 값 중 toddler·preschool 은 Food 의 tool 묶음이 같다)
_FALLBACK_BIRTH_OFFSET = timedelta(days=365 * 2)


async def handle_input(
    *,
    child_id: UUID,
    parent_id: UUID,
    raw_text: str,
    run_id: str,
    birth_date: date | None = None,
    emit: Emit | None = None,
    store: MemoryStore | None = None,
    continuation: PendingMemoryContext | None = None,
    commit: Commit | None = None,
) -> PipelineResult:
    """입력 한 줄을 처리한다. 진행 상황은 emit 으로 나간다.

    - birth_date를 넘기면 Food Agent가 build_gate에서 그 값으로 식이 단계를 계산한다.
    - store를 넘기면 그것에 쓴다.
    - agents는 infra에 직접 닿지 않으므로 DB 어댑터는 api가 만들어 넘긴다.
    - continuation을 넘기면 raw_text는 이전 질문에 대한 보호자의 답이다. api는 reply_to를
      검증·복원한 뒤 이 값만 넘긴다.
    - commit을 넘기면 Memory가 끝난 직후 한 번 부른다. 기록 단계(Memory 의 쓰기)를
      확정하는 함수이고, 러너가 run 마다 만든다. 저장 안내는 그 뒤에 나간다. 되묻기 맥락은
      같은 트랜잭션에 넣지 않는다 — commit 뒤 PendingReply 로 나가 러너가 프로세스 메모리에
      둔다(팀 합의). Memory 가 반복 상한에 걸려 기록을 다 끝내지 못하면 commit 을 부르지
      않는다. 러너는 commit 되지 않은 세션을 되돌린다.
    """
    now = datetime.now(KST)
    # 안 넘기면 run 하나가 store 하나를 쓴다. 앞선 run에 저장한 관찰은 다음 run에서
    # 조회되지 않고, 저장된 행을 api가 다시 읽을 곳도 존재하지 않다
    if store is None:
        store = InMemoryStore(now=now)

    memory_context = AgentContext(
        child_id=child_id,
        source_writer=parent_id,
        now=now,
        timezone=KST,
        store=store,
    )

    # Food 포트는 아직 DB 에 연결되지 않았다. birth_date 를 InMemoryProfile 에 심어
    # build_gate 가 LifeStage 를 계산하게 하고, 기억 포트는 같은 run 의 store 를 읽게 한다.
    # 방금 저장한 관찰이 같은 run 의 추천 근거로 잡혀야 해서다 (루트 §4).
    # 나머지 포트는 빈 기본값이다.
    # TODO: 실제 DB 어댑터가 붙으면 이 자리를 api 가 넘긴 FoodPorts 로 바꾼다.
    resolved_birth_date = birth_date or (now.date() - _FALLBACK_BIRTH_OFFSET)
    food_ports = in_memory_ports(
        profile=InMemoryProfile({child_id: resolved_birth_date}),
        memory=StoreFoodMemory(store),
    )
    # 쓰기 포트는 호출마다 바로 commit
    writes = RunWrites()
    food_ports = record_food_writes(food_ports, writes)

    food_context = FoodContext(
        child_id=child_id,
        run_id=run_id,
        now=now,
        timezone=KST,
        ports=food_ports,
    )

    return await _handle_input(
        raw_text,
        memory_context,
        {"food": food_context},
        run_id=run_id,
        emit=emit,
        continuation=continuation,
        commit=commit,
        writes=writes,
    )
