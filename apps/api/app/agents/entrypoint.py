"""API가 부르는 agents 진입점.

api 레이어는 이 모듈만 import한다. 밖으로는 handle_input 과 그 입출력·이벤트 타입만 내보낸다.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FeedingStage
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import (
    Done,
    Emit,
    Event,
    EventDrafts,
    Failed,
    FoodRouted,
    MemoryNote,
    PipelineResult,
    Ref,
    Rerouted,
    Saved,
    Step,
    Unavailable,
    Unwritten,
)
from app.agents.pipeline import handle_input as _handle_input
from app.agents.supervisor.routing import Guidance

# api가 이 파일만 보면 되도록 진행 이벤트 타입도 여기서 내보냄
# pipeline에 이벤트를 추가하면 여기에도 작성
__all__ = [
    "Done",
    "Emit",
    "Event",
    "EventDraft",
    "EventDrafts",
    "Failed",
    "FoodRouted",
    "Guidance",
    "MemoryNote",
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

# 아이 나이를 아직 받지 않아서 식이 단계를 고정한다.
# TODO: child.birth_date 에서 코드가 계산한다 (영아기/유아기 경계 개월 수 미정)
_DEFAULT_STAGE = FeedingStage.TODDLER


async def handle_input(
    *,
    child_id: UUID,
    parent_id: UUID,
    raw_text: str,
    run_id: str,
    emit: Emit | None = None,
) -> PipelineResult:
    """입력 한 줄을 처리한다. 진행 상황은 emit 으로 나간다."""
    now = datetime.now(KST)
    # run 하나가 store 하나를 쓴다. 앞선 run에 저장한 관찰은 다음 run에서 조회되지 않는다.
    # TODO: DB 어댑터로 교체
    store = InMemoryStore(now=now)

    memory_context = AgentContext(
        child_id=child_id,
        source_writer=parent_id,
        now=now,
        timezone=KST,
        store=store,
    )

    # Food Agent가 아직 프레임 상태이므로 stage만 읽고 status="mock"을 반환
    # pipeline 이 필수 인자로 받기 때문에 컨텍스트는 만들어 넘겨야 함
    food_context = FoodContext(
        child_id=child_id,
        now=now,
        timezone=KST,
        stage=_DEFAULT_STAGE,
    )

    return await _handle_input(
        raw_text,
        memory_context,
        food_context,
        run_id=run_id,
        emit=emit,
    )
