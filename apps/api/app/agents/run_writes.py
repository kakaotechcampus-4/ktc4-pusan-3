"""도메인 쓰기가 실제로 commit된 run인지 기록한다.

쓰기 성공 후 실패한 run이 재전송되어 같은 작업이 중복되는 것을 막기 위해 사용한다.
Agent는 이 상태를 모르며 entrypoint가 쓰기 포트를 감싸서 관리한다.

한계: commit 응답을 기다리는 중에 취소되면 실제로는 들어갔어도 표시가 남지 않는다.
다시 보내도 안전하게 하는 건 쓰기 쪽 몫 — UPDATE는 같은 갱신을 두 번 적용해도
결과가 같게, INSERT는 도메인 값으로 만든 중복 방지키로 막는다(포트 계약).
"""

from dataclasses import replace
from datetime import date
from uuid import UUID

from app.agents.food.store.ports import (
    DaycareMealRow,
    DaycareMealStore,
    FoodPorts,
    MenuCatalogRow,
    MenuCatalogStore,
    NutrientBand,
    NutrientBandStore,
)


class RunWrites:
    """run 하나 동안 도메인 쓰기 포트가 성공한 적이 있는지."""

    def __init__(self) -> None:
        self.wrote = False

    def mark(self) -> None:
        self.wrote = True


class _RecordedDaycareMeals:
    def __init__(self, inner: DaycareMealStore, writes: RunWrites) -> None:
        self._inner = inner
        self._writes = writes

    async def rows(self, *, child_id: UUID, date_from: date, date_to: date) -> list[DaycareMealRow]:
        return await self._inner.rows(child_id=child_id, date_from=date_from, date_to=date_to)

    async def has_rows(self, *, child_id: UUID) -> bool:
        return await self._inner.has_rows(child_id=child_id)

    async def update(self, row: DaycareMealRow) -> None:
        await self._inner.update(row)
        self._writes.mark()  # 돌아온 뒤에만. 예외면 여기 오지 않는다

    async def delete(self, *, child_id: UUID, row_ids: tuple[UUID, ...]) -> None:
        await self._inner.delete(child_id=child_id, row_ids=row_ids)
        self._writes.mark()


class _RecordedNutrientBands:
    def __init__(self, inner: NutrientBandStore, writes: RunWrites) -> None:
        self._inner = inner
        self._writes = writes

    async def last(self, *, child_id: UUID) -> dict[str, NutrientBand]:
        return await self._inner.last(child_id=child_id)

    async def save(self, *, child_id: UUID, bands: dict[str, NutrientBand]) -> None:
        await self._inner.save(child_id=child_id, bands=bands)
        self._writes.mark()


class _RecordedMenuCatalog:
    def __init__(self, inner: MenuCatalogStore, writes: RunWrites) -> None:
        self._inner = inner
        self._writes = writes

    async def get(self, menu_key: str) -> MenuCatalogRow | None:
        return await self._inner.get(menu_key)

    async def put(self, row: MenuCatalogRow) -> None:
        await self._inner.put(row)
        self._writes.mark()

    async def all_resolved(self) -> list[MenuCatalogRow]:
        return await self._inner.all_resolved()


def record_food_writes(ports: FoodPorts, writes: RunWrites) -> FoodPorts:
    """Food 쓰기 포트 셋(급식 · 영양 구간 · 메뉴 캐시)만 감싼다. 나머지 포트는 그대로다."""
    return replace(
        ports,
        daycare=_RecordedDaycareMeals(ports.daycare, writes),
        bands=_RecordedNutrientBands(ports.bands, writes),
        catalog=_RecordedMenuCatalog(ports.catalog, writes),
    )
