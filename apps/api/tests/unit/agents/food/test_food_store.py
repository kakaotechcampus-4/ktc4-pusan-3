"""Food 포트의 InMemory 구현 검증.

DB 는 아직 없다 — 여기서 확인하는 것은 세 가지다.
    - 인터페이스가 문서가 정한 테이블 모양과 이름을 그대로 따르는가
    - 쓰기 포트(daycare)가 update·delete 로만 열려 있고 INSERT 통로가 없는가
    - 아이 기록 행(FoodObservation·Measurement·DaycareMealRow)의 child_id 가 실제로
      아이 경계를 지키는가 — 잘못된 id 하나가 남의 아이 급식 행을 건드리면 안 된다
"""

from dataclasses import fields
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from app.agents.food.store import (
    DaycareMealRow,
    FoodDocRow,
    FoodObservation,
    FoodPorts,
    InMemoryDaycareMeals,
    InMemoryMenuCatalog,
    InMemoryMenuSource,
    InMemorySafety,
    Measurement,
    MenuCatalogRow,
    MenuSourceError,
    NutritionFacts,
    SafetyEntry,
    SafetyLookupError,
    in_memory_ports,
)

CHILD = UUID(int=1)  # 아이 A
OTHER_CHILD = UUID(int=2)  # 아이 B
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


# ── daycare: 쓰기 포트는 update·delete 뿐 ──────────────────────────
def test_daycare_meals_에는_insert_add_create_속성이_없다() -> None:
    store = InMemoryDaycareMeals()

    assert not hasattr(store, "insert")
    assert not hasattr(store, "add")
    assert not hasattr(store, "create")


async def test_daycare_meals_update_는_있는_행만_고친다() -> None:
    row = DaycareMealRow(
        id=UUID(int=10),
        child_id=CHILD,
        serve_date=date(2026, 9, 27),
        meal_slot="lunch",
        menu_keys=("김치찌개",),
    )
    store = InMemoryDaycareMeals(rows=[row])

    changed = DaycareMealRow(
        id=row.id,
        child_id=row.child_id,
        serve_date=row.serve_date,
        meal_slot=row.meal_slot,
        menu_keys=("김치찌개", "밥"),
        caregiver_checked=True,
    )
    await store.update(changed)

    [stored] = await store.rows(child_id=CHILD, date_from=row.serve_date, date_to=row.serve_date)
    assert stored.menu_keys == ("김치찌개", "밥")
    assert stored.caregiver_checked is True


async def test_daycare_meals_update_는_없는_행을_새로_만들지_않는다() -> None:
    store = InMemoryDaycareMeals()
    ghost = DaycareMealRow(
        id=UUID(int=99),
        child_id=CHILD,
        serve_date=date(2026, 9, 27),
        meal_slot="snack_am",
        menu_keys=("바나나",),
    )

    with pytest.raises(KeyError):
        await store.update(ghost)

    assert await store.has_rows(child_id=CHILD) is False


async def test_daycare_meals_delete_는_지정한_행만_지운다() -> None:
    kept = DaycareMealRow(
        id=UUID(int=1),
        child_id=CHILD,
        serve_date=date(2026, 9, 27),
        meal_slot="lunch",
        menu_keys=("밥",),
    )
    removed = DaycareMealRow(
        id=UUID(int=2),
        child_id=CHILD,
        serve_date=date(2026, 9, 27),
        meal_slot="snack_pm",
        menu_keys=("사과",),
    )
    store = InMemoryDaycareMeals(rows=[kept, removed])

    await store.delete(child_id=CHILD, row_ids=(removed.id,))

    remaining = await store.rows(
        child_id=CHILD, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )
    assert [row.id for row in remaining] == [kept.id]


# ── daycare: child_id 가 아이 경계를 지킨다 ─────────────────────────
async def _seeded_two_children() -> tuple[InMemoryDaycareMeals, DaycareMealRow, DaycareMealRow]:
    """아이 A(CHILD) 급식 한 행, 아이 B(OTHER_CHILD) 급식 한 행을 심어 둔다."""
    row_a = DaycareMealRow(
        id=UUID(int=101),
        child_id=CHILD,
        serve_date=date(2026, 9, 27),
        meal_slot="lunch",
        menu_keys=("김치찌개",),
    )
    row_b = DaycareMealRow(
        id=UUID(int=102),
        child_id=OTHER_CHILD,
        serve_date=date(2026, 9, 27),
        meal_slot="lunch",
        menu_keys=("된장찌개",),
    )
    store = InMemoryDaycareMeals(rows=[row_a, row_b])
    return store, row_a, row_b


async def test_daycare_meals_rows_는_그_아이_행만_돌려준다() -> None:
    store, row_a, _row_b = await _seeded_two_children()

    result = await store.rows(
        child_id=CHILD, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )

    assert [row.id for row in result] == [row_a.id]


async def test_daycare_meals_has_rows_는_그_아이_행이_없으면_False() -> None:
    store, _row_a, _row_b = await _seeded_two_children()
    third_child = UUID(int=3)

    assert await store.has_rows(child_id=third_child) is False
    assert await store.has_rows(child_id=CHILD) is True  # A 는 행이 있다


async def test_daycare_meals_update_는_다른_아이_행이면_KeyError() -> None:
    store, _row_a, row_b = await _seeded_two_children()

    hijacked = DaycareMealRow(
        id=row_b.id,
        child_id=CHILD,  # B 의 행을 A 것이라 주장
        serve_date=row_b.serve_date,
        meal_slot=row_b.meal_slot,
        menu_keys=("바꿔치기",),
    )

    with pytest.raises(KeyError):
        await store.update(hijacked)

    [still_b] = await store.rows(
        child_id=OTHER_CHILD, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )
    assert still_b.menu_keys == row_b.menu_keys


async def test_daycare_meals_delete_는_다른_아이_행이면_KeyError() -> None:
    store, _row_a, row_b = await _seeded_two_children()

    with pytest.raises(KeyError):
        await store.delete(child_id=CHILD, row_ids=(row_b.id,))

    [still_b] = await store.rows(
        child_id=OTHER_CHILD, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )
    assert still_b.id == row_b.id


async def test_daycare_meals_delete_는_없는_id_는_조용히_넘어간다() -> None:
    store, _row_a, _row_b = await _seeded_two_children()

    await store.delete(child_id=CHILD, row_ids=(UUID(int=999),))  # 예외 없이 지나간다


async def test_daycare_meals_delete_는_섞어_넘기면_A_의_행도_지우지_않는다() -> None:
    """검사를 먼저 다 끝내고 지운다는 것을 고정한다.

    A 의 행과 B 의 행을 한 번에 넘기면 B 때문에 KeyError 가 나야 하고, 그때 이미
    검사를 통과했을 A 의 행도 지워지지 않은 채로 남아야 한다 — 삭제 루프 안에서
    한 건씩 검사하며 지우면(먼저 구현했던 방식) A 는 지워진 뒤 B 에서 예외가 난다.
    """
    store, row_a, row_b = await _seeded_two_children()

    with pytest.raises(KeyError):
        await store.delete(child_id=CHILD, row_ids=(row_a.id, row_b.id))

    [still_a] = await store.rows(
        child_id=CHILD, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )
    assert still_a.id == row_a.id


# ── 아이 기록 행에만 child_id 가 있다 ────────────────────────────────
def test_아이_기록_행만_child_id_필드를_갖는다() -> None:
    with_child_id = {FoodObservation, Measurement, DaycareMealRow}
    without_child_id = {MenuCatalogRow, FoodDocRow, NutritionFacts}

    for cls in with_child_id:
        assert "child_id" in {f.name for f in fields(cls)}, cls
    for cls in without_child_id:
        assert "child_id" not in {f.name for f in fields(cls)}, cls


# ── safety: 조회 실패와 0행은 다르다 ───────────────────────────────
async def test_safety_fail_이면_SafetyLookupError() -> None:
    store = InMemorySafety(fail=True)

    with pytest.raises(SafetyLookupError):
        await store.food_safety(child_id=CHILD)


async def test_safety_기본은_넣은_행을_그대로_돌려준다() -> None:
    entry = SafetyEntry(kind="allergy", label="우유", state="active", allergen_code=2)
    store = InMemorySafety(entries=[entry])

    result = await store.food_safety(child_id=CHILD)

    assert result == [entry]


# ── menu catalog: 미해결 행은 all_resolved 에서 빠진다 ──────────────
async def test_menu_catalog_all_resolved_는_resolved_False_행을_뺀다() -> None:
    resolved = MenuCatalogRow(
        menu_key="김치찌개",
        display_name="김치찌개",
        source="mfds_nutri",
        resolved=True,
        synced_at=NOW,
        food_code="D000001",
    )
    unresolved = MenuCatalogRow(
        menu_key="모름메뉴",
        display_name="모름메뉴",
        source="manual",
        resolved=False,
        synced_at=NOW,
    )
    store = InMemoryMenuCatalog(rows=[resolved, unresolved])

    result = await store.all_resolved()

    assert result == [resolved]


async def test_menu_catalog_get_put_은_menu_key_로_찾는다() -> None:
    store = InMemoryMenuCatalog()
    row = MenuCatalogRow(
        menu_key="된장국",
        display_name="된장국",
        source="center_standard",
        resolved=True,
        synced_at=NOW,
    )

    assert await store.get("된장국") is None
    await store.put(row)
    assert await store.get("된장국") == row


# ── menu source: 외부 API 실패는 전용 예외 ─────────────────────────
async def test_menu_source_fail_이면_MenuSourceError() -> None:
    source = InMemoryMenuSource(fail=True)

    with pytest.raises(MenuSourceError):
        await source.nutrition(name="김치찌개")
    with pytest.raises(MenuSourceError):
        await source.ingredients(name="김치찌개")


async def test_menu_source_레시피_없음은_None() -> None:
    source = InMemoryMenuSource()

    assert await source.ingredients(name="없는메뉴") is None


# ── in_memory_ports: FoodPorts 와 필드가 같다 ──────────────────────
def test_in_memory_ports_의_필드가_FoodPorts_필드와_같다() -> None:
    ports = in_memory_ports()

    port_field_names = {f.name for f in fields(FoodPorts)}
    assert port_field_names == {f.name for f in fields(ports)}
    assert isinstance(ports, FoodPorts)


def test_in_memory_ports_는_override_를_받는다() -> None:
    safety = InMemorySafety(fail=True)

    ports = in_memory_ports(safety=safety)

    assert ports.safety is safety
    assert isinstance(ports.daycare, InMemoryDaycareMeals)
