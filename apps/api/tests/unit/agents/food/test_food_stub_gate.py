"""모델에게 열린 tool 중 스텁(NotImplementedError)이 있으면 실패한다 (#357 리뷰 4-②).

포트를 붙이는 쪽이 구현 순서를 몰라도, 스텁을 모델에게 여는 순간 이 테스트가 막는다.
식단 추천 · 급식 갱신은 tool 이 구현될 때까지 `not_ready.*` 로 닫혀 있다.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.common.age import life_stage
from app.agents.common.gate import DataReady, Gate
from app.agents.food.context import FoodContext
from app.agents.food.registry import TOOL_HANDLERS, tools_for
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import in_memory_ports

CHILD = UUID(int=1)
NOW = datetime(2026, 9, 27, tzinfo=UTC)
BIRTHS = {
    "infant_milk": date(2026, 8, 1),
    "infant_weaning": date(2026, 3, 1),
    "toddler": date(2024, 9, 1),
    "preschool": date(2022, 9, 1),
}
# 지금 구현이 안 된 핸들러. 구현하면 여기서 뺀다 — 아래 판별이 맞는지 확인하는 기준이다
KNOWN_STUBS = frozenset(
    {
        "search_food_memory",
        "analyze_meal_records",
        "check_repeated_menus",
        "lookup_daycare_menu",
        "update_daycare_meal",
        "delete_daycare_meal",
        "guide_weaning_stage",
        "propose_meal_candidates",
    }
)


def _context() -> FoodContext:
    return FoodContext(child_id=CHILD, run_id="r", now=NOW, timezone=UTC, ports=in_memory_ports())


async def _is_stub(name: str) -> bool:
    """핸들러를 인자 없이 불러 본다. 스텁은 docstring 다음 줄에서 NotImplementedError 를 낸다.
    다른 예외(인자가 None 이라 생기는 것)는 구현된 핸들러다."""
    try:
        await TOOL_HANDLERS[name](_context(), None)
    except NotImplementedError:
        return True
    except Exception:
        return False
    return False


async def test_스텁_판별이_알려진_스텁_목록과_같다():
    found = {name for name in TOOL_HANDLERS if await _is_stub(name)}
    assert found == KNOWN_STUBS


@pytest.mark.parametrize("task", list(FoodTaskType))
@pytest.mark.parametrize("stage", list(BIRTHS))
@pytest.mark.parametrize("safety_ok", [True, False])
@pytest.mark.parametrize("daycare", [True, False])
async def test_모델에게_열린_tool_에_스텁이_없다(task, stage, safety_ok, daycare):
    gate = Gate(
        stage=life_stage(BIRTHS[stage], NOW.date()),
        consent_child_health=True,
        safety_ok=safety_ok,
        data=DataReady(daycare_meal=daycare),
    )
    opened = tools_for(task, gate)
    # 판별(_is_stub)만 믿지 않는다 — 스텁이 인자를 먼저 읽어 다른 예외를 내면 판별에서 빠진다
    assert [name for name in opened if name in KNOWN_STUBS or await _is_stub(name)] == []
