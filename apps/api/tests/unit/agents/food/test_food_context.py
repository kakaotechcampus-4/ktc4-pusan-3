"""FoodContext.for_task — task 하나 몫의 context.

pipeline 이 같은 Agent 의 task 둘을 동시에 돌린다.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.food.context import FoodContext
from app.agents.food.store import in_memory_ports

KST = ZoneInfo("Asia/Seoul")


def test_for_task_는_state_만_새로_만든다() -> None:
    context = FoodContext(
        child_id=UUID(int=1),
        run_id="run-1",
        now=datetime(2026, 9, 9, 9, 0, tzinfo=KST),
        timezone=KST,
        ports=in_memory_ports(),
    )
    context.state.utterance = "앞 task 의 발화"

    fresh = context.for_task()

    assert fresh.state is not context.state
    assert fresh.state.utterance == ""
    assert (fresh.child_id, fresh.run_id, fresh.now) == (
        context.child_id,
        context.run_id,
        context.now,
    )
    assert fresh.ports is context.ports  # 포트는 run 이 같이 쓴다
