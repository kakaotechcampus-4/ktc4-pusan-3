"""API가 부르는 agents 진입점.

api 레이어는 이 모듈만 import한다. 밖으로는 handle_input 과 그 입출력 타입만 내보낸다.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FeedingStage
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import Emit, PipelineResult
from app.agents.pipeline import handle_input as _handle_input

__all__ = ["Emit", "PipelineResult", "handle_input"]

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

    # 아직 FRAME 상태라서 주석처리
    # food_context = FoodContext(
    #     child_id=child_id,
    #     now=now,
    #     timezone=KST,
    #     stage=_DEFAULT_STAGE,
    # )

    return await _handle_input(
        raw_text,
        memory_context,
        # food_context,
        run_id=run_id,
        emit=emit,
    )
