"""`health_safety` 어댑터가 붙기 전에는 동의와 상관없이 Food 가 닫혀 있다 (#372 PR 2).

생일 · 동의를 실제 값으로 넘기면, 동의하지 않은 아이는 `build_gate` 가 `health_safety` 를 아예 읽지
않아서(루트 §2) 안전 조회 실패가 드러나지 않는다. 그대로 두면 안전 조회가 안 붙은 서비스에서 Food 가
"알레르기 없는 아이 기준" 으로 열린다. 연결되지 않은 안전 포트(`UnwiredSafety`)는 동의를 보지 않고
조회 실패로 읽힌다.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.common.child_values import ChildValues
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.registry import closed_readout_key, tools_for
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import in_memory_ports, unwired_ports
from app.agents.food.store.ports import SafetyLookupError, UnwiredSafety

CHILD = UUID(int=1)
NOW = datetime(2026, 9, 27, tzinfo=UTC)
TODDLER_BIRTH = date(2024, 9, 1)  # 만 2세 — 식이 단계가 열린 월령


def _context(*, granted: bool) -> FoodContext:
    ports = unwired_ports(
        profile=ChildValues(child_id=CHILD, birth=TODDLER_BIRTH, health_consent=granted),
        consent=ChildValues(child_id=CHILD, birth=TODDLER_BIRTH, health_consent=granted),
    )
    return FoodContext(child_id=CHILD, run_id="r", now=NOW, timezone=UTC, ports=ports)


@pytest.mark.parametrize("granted", [True, False])
async def test_동의와_상관없이_안전_조회_실패로_닫힌다(granted):
    context = _context(granted=granted)

    gate = await build_gate(context)

    assert gate.consent_child_health is granted  # 실제 동의 값이 Food 까지 들어온다
    assert gate.safety_ok is False
    assert context.state.safety is None  # "거를 것 없음" 인 빈 튜플이 아니다
    for task in (FoodTaskType.MEAL_RECOMMENDATION, FoodTaskType.NUTRIENT_ANALYSIS):
        assert closed_readout_key(task, gate) == "blocked.safety"
        assert tools_for(task, gate) == ()


async def test_동의가_없으면_연결되지_않은_안전_포트를_부르지_않는다():
    """읽을 수 없는 것을 읽으려 하지 않는다 — 동의 없는 아이의 건강정보 경로를 지나지 않는다."""
    calls: list[UUID] = []

    class Spy(UnwiredSafety):
        async def food_safety(self, *, child_id):
            calls.append(child_id)
            raise SafetyLookupError("연결되지 않음")

    ports = in_memory_ports(
        profile=ChildValues(child_id=CHILD, birth=TODDLER_BIRTH, health_consent=False),
        consent=ChildValues(child_id=CHILD, birth=TODDLER_BIRTH, health_consent=False),
        safety=Spy(),
    )
    context = FoodContext(child_id=CHILD, run_id="r", now=NOW, timezone=UTC, ports=ports)

    gate = await build_gate(context)

    assert calls == []
    assert gate.safety_ok is False


async def test_연결되지_않은_안전_포트를_직접_부르면_조회_실패다():
    with pytest.raises(SafetyLookupError):
        await UnwiredSafety().food_safety(child_id=CHILD)


async def test_급식_라벨은_영향받지_않는다():
    """급식 기록 갱신은 안전 필터와 별개다 — 급식 행이 없어서 닫혀 있는 것은 그대로다."""
    gate = await build_gate(_context(granted=False))
    assert closed_readout_key(FoodTaskType.DAYCARE_MEAL, gate) == "closed.no_daycare"
