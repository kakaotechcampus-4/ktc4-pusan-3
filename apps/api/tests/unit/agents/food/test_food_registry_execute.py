"""Food registry.execute_tool — 허용 목록 · 인자 검증 · stub tool 예외 처리."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.food.context import FoodContext
from app.agents.food.registry import execute_tool
from app.agents.food.result import ErrorCode
from app.agents.food.store import in_memory_ports

KST = ZoneInfo("Asia/Seoul")


def _context() -> FoodContext:
    return FoodContext(
        child_id=UUID(int=1),
        run_id="run-1",
        now=datetime(2026, 10, 2, 9, 0, tzinfo=KST),
        timezone=KST,
        ports=in_memory_ports(),
    )


async def test_DB_연결_전_stub_tool_은_예외_대신_실패_결과를_돌려준다() -> None:
    # analyze_meal_records 는 DB 연결 전이라 핸들러가 NotImplementedError 를 낸다(records.py).
    # 모델이 그걸 불러도 run() 전체가 죽지 않고, 모델이 다른 tool 로 이어갈 수 있는 실패로 받는다.
    result = await execute_tool(
        "analyze_meal_records",
        {"period": "last_7_days", "meal_slots": []},
        _context(),
        allowed=("analyze_meal_records",),
    )

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.UPSTREAM_ERROR
