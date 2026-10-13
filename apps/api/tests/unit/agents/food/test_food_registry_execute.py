"""Food registry.execute_tool — 허용 목록 · 인자 검증 · 핸들러 예외."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.food import registry
from app.agents.food.context import FoodContext
from app.agents.food.registry import execute_tool
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


async def test_핸들러_예외는_삼키지_않고_그대로_올린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """스텁은 모델에게 열지 않는 것으로 막는다 — execute_tool 이 감싸면 구현 뒤 진짜 버그를
    가린다 (Tool_공통 · #357 리뷰 4-①)."""

    async def broken(context: FoodContext, args: object) -> None:
        raise NotImplementedError("스텁")

    monkeypatch.setitem(registry.TOOL_HANDLERS, "compare_diet_balance", broken)

    with pytest.raises(NotImplementedError):
        await execute_tool(
            "compare_diet_balance",
            {"period": "last_7_days"},
            _context(),
            allowed=("compare_diet_balance",),
        )
