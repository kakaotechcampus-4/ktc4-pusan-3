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


async def test_급식_삭제_영양_구간_메뉴_캐시_쓰기도_표시가_남는다() -> None:
    for write in (
        lambda p: p.daycare.delete(child_id=CHILD, row_ids=(_ROW.id,)),
        lambda p: p.bands.save(child_id=CHILD, bands={"iron": "low"}),
        lambda p: p.catalog.put(
            MenuCatalogRow(
                menu_key="두유",
                display_name="두유",
                source="manual",
                resolved=True,
                synced_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
            )
        ),
    ):
        writes, ports = _recorded()
        await write(ports)
        assert writes.wrote is True


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
    assert recorded.daycare is not original.daycare
