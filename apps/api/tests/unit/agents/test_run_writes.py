"""run_writes 검증. 쓰기 포트 호출이 성공하고 돌아온 뒤에만 표시가 남는다."""

from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from app.agents.food.store import (
    DaycareMealRow,
    InMemoryDaycareMeals,
    MenuCatalogRow,
    in_memory_ports,
)
from app.agents.run_writes import RunWrites, record_food_writes

CHILD = UUID(int=1)
DAY = date(2026, 9, 9)
_ROW = DaycareMealRow(
    id=UUID(int=900), child_id=CHILD, serve_date=DAY, meal_slot="lunch", menu_keys=("test",)
)


def _recorded() -> tuple[RunWrites, object]:
    writes = RunWrites()
    ports = record_food_writes(in_memory_ports(daycare=InMemoryDaycareMeals([_ROW])), writes)
    return writes, ports


async def test_급식_수정이_성공하면_표시가_남는다() -> None:
    writes, ports = _recorded()

    await ports.daycare.update(replace(_ROW, menu_keys=("두유",)))  # type: ignore[attr-defined]

    assert writes.wrote is True


async def test_쓰기가_예외로_끝나면_표시가_남지_않는다() -> None:
    writes, ports = _recorded()
    missing = replace(_ROW, id=UUID(int=901))

    with pytest.raises(KeyError):
        await ports.daycare.update(missing)  # type: ignore[attr-defined]

    assert writes.wrote is False


async def test_급식_삭제도_표시가_남는다() -> None:
    writes, ports = _recorded()

    await ports.daycare.delete(child_id=CHILD, row_ids=(_ROW.id,))  # type: ignore[attr-defined]

    assert writes.wrote is True


async def test_영양_구간_메뉴_카탈로그_저장은_표시를_남기지_않는다() -> None:
    # 보호자 말을 반영한 쓰기가 아니라 추천 직전 계산이 남기는 내부 값이다. 덮어쓰기라
    # 다시 보내도 같은 값이 된다. 표시를 세우면 질문만 한 run이 모델 실패에도 done이 된다
    writes, ports = _recorded()

    await ports.bands.save(child_id=CHILD, bands={"iron": "low"})  # type: ignore[attr-defined]
    await ports.catalog.put(  # type: ignore[attr-defined]
        MenuCatalogRow(
            menu_key="두유",
            display_name="두유",
            source="manual",
            resolved=True,
            synced_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
    )

    assert writes.wrote is False


async def test_조회는_표시를_남기지_않는다() -> None:
    writes, ports = _recorded()

    assert await ports.daycare.has_rows(child_id=CHILD)  # type: ignore[attr-defined]
    rows = await ports.daycare.rows(  # type: ignore[attr-defined]
        child_id=CHILD, date_from=DAY, date_to=DAY
    )
    await ports.bands.last(child_id=CHILD)  # type: ignore[attr-defined]
    await ports.catalog.get("두유")  # type: ignore[attr-defined]
    await ports.catalog.all_resolved()  # type: ignore[attr-defined]

    assert rows == [_ROW]
    assert writes.wrote is False


def test_쓰기_포트가_아닌_포트는_그대로_둔다() -> None:
    original = in_memory_ports()

    recorded = record_food_writes(original, RunWrites())

    assert recorded.memory is original.memory
    assert recorded.safety is original.safety
    # 계산 · 캐시 저장 포트는 감싸지 않는다
    assert recorded.bands is original.bands
    assert recorded.catalog is original.catalog
    assert recorded.daycare is not original.daycare
