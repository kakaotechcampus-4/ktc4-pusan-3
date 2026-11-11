"""급식 저장소(`DaycareMealStore`) 계약 시나리오. 메모리 구현과 DB 어댑터가 같은 결과를 내는지 본다.

시나리오는 어느 저장소인지 모르고 Harness 만 받는다.

    단위  tests/unit/agents/food/test_daycare_store_contract.py      메모리 · DB 없음
    통합  tests/integration/agents/food/test_daycare_store_contract_db.py  DB 어댑터

메모리가 DB 와 다르게 나오면 메모리를 DB 에 맞춘다 — 단위 테스트가 실제 저장소와 같은 것을 보게
하려는 것이다(복약 계약 `tests/unit/agents/health/medication_store_contract.py` 와 같은 규칙).
복약과 달리 급식 행은 `child_id` 를 들고 있어서 다른 아이를 가르는 케이스도 여기서 돈다.
메뉴 · 날짜는 전부 가짜 값이다.
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import pytest

from app.agents.food.store.ports import DaycareMealRow, DaycareMealStore, DaycareSlot

BASE_DAY = date(2026, 10, 12)


class Harness(Protocol):
    child_id: UUID
    other_child_id: UUID
    store: DaycareMealStore

    async def seed(self, *rows: DaycareMealRow) -> None:
        """행을 그대로 넣는다. 각 행의 `child_id` 가 소유 아이다."""
        ...


def meal(
    h: Harness,
    *menu_keys: str,
    day: date = BASE_DAY,
    slot: DaycareSlot = "lunch",
    owner: UUID | None = None,
    **over: object,
) -> DaycareMealRow:
    return DaycareMealRow(
        id=uuid4(),
        child_id=owner or h.child_id,
        serve_date=day,
        meal_slot=slot,
        menu_keys=tuple(menu_keys),
        **over,  # type: ignore[arg-type]
    )


async def _all(h: Harness, *, child: UUID | None = None) -> list[DaycareMealRow]:
    return await h.store.rows(
        child_id=child or h.child_id,
        date_from=BASE_DAY - timedelta(days=30),
        date_to=BASE_DAY + timedelta(days=30),
    )


async def 칸을_그대로_읽는다(h: Harness) -> None:
    row = meal(
        h,
        "밥",
        "미역국",
        "두부조림",
        amount_factor=0.5,
        amount_known=True,
        caregiver_checked=True,
        origin="center_standard",
    )
    await h.seed(row)

    assert await _all(h) == [row]


async def rows_는_날짜_양끝을_포함한다(h: Harness) -> None:
    one = timedelta(days=1)
    rows = [
        meal(h, "가", day=BASE_DAY - one),
        meal(h, "나", day=BASE_DAY, slot="snack_pm"),
        meal(h, "다", day=BASE_DAY, slot="lunch"),
        meal(h, "라", day=BASE_DAY, slot="snack_am"),
        meal(h, "마", day=BASE_DAY + one),
        meal(h, "바", day=BASE_DAY + 2 * one),
    ]
    await h.seed(*rows)

    found = await h.store.rows(child_id=h.child_id, date_from=BASE_DAY, date_to=BASE_DAY + one)

    assert {row.id for row in found} == {rows[1].id, rows[2].id, rows[3].id, rows[4].id}
    # (날짜, 끼니 값) 순. 끼니 값은 문자열이라 lunch → snack_am → snack_pm 이다 — DB 도 같다
    # (`meal_slot` 은 VARCHAR). 간식 오전 → 점심 → 간식 오후는 화면을 만드는 쪽이
    # `DAYCARE_SLOT_ORDER` 로 다시 정렬한다
    assert [(row.serve_date, row.meal_slot) for row in found] == sorted(
        (row.serve_date, row.meal_slot) for row in found
    )


async def rows_는_다른_아이_행을_주지_않는다(h: Harness) -> None:
    await h.seed(meal(h, "밥", owner=h.other_child_id))

    assert await _all(h) == []


async def has_rows_는_그_아이_행이_있을_때만_True(h: Harness) -> None:
    assert await h.store.has_rows(child_id=h.child_id) is False

    await h.seed(meal(h, "밥", owner=h.other_child_id))
    assert await h.store.has_rows(child_id=h.child_id) is False

    await h.seed(meal(h, "밥"))
    assert await h.store.has_rows(child_id=h.child_id) is True


async def update_는_메뉴_양_확인_칸을_바꾼다(h: Harness) -> None:
    row = meal(h, "밥", "우유")
    await h.seed(row)

    changed = replace(
        row,
        menu_keys=("밥", "두유"),
        amount_factor=1.5,
        amount_known=True,
        caregiver_checked=True,
    )
    await h.store.update(changed)

    assert await _all(h) == [changed]


async def update_는_날짜_끼니_출처를_바꾸지_않는다(h: Harness) -> None:
    row = meal(h, "밥", "우유")
    await h.seed(row)

    await h.store.update(
        replace(
            row,
            serve_date=BASE_DAY + timedelta(days=1),
            meal_slot="snack_pm",
            origin="center_standard",
            menu_keys=("죽",),
        )
    )

    assert await _all(h) == [replace(row, menu_keys=("죽",))]


async def 없는_행_update_는_KeyError(h: Harness) -> None:
    with pytest.raises(KeyError):
        await h.store.update(meal(h, "밥"))


async def 다른_아이_행_update_는_KeyError_이고_그대로다(h: Harness) -> None:
    theirs = meal(h, "밥", owner=h.other_child_id)
    await h.seed(theirs)

    with pytest.raises(KeyError):
        await h.store.update(replace(theirs, child_id=h.child_id, menu_keys=("죽",)))

    assert await _all(h, child=h.other_child_id) == [theirs]


async def delete_는_그_아이_행만_지우고_없는_id_는_넘긴다(h: Harness) -> None:
    gone = meal(h, "밥")
    kept = meal(h, "국", slot="snack_pm")
    await h.seed(gone, kept)

    await h.store.delete(child_id=h.child_id, row_ids=(gone.id, uuid4()))

    assert await _all(h) == [kept]


async def 다른_아이_id_가_섞이면_KeyError_이고_하나도_안_지운다(h: Harness) -> None:
    mine = meal(h, "밥")
    theirs = meal(h, "국", owner=h.other_child_id)
    await h.seed(mine, theirs)

    with pytest.raises(KeyError):
        await h.store.delete(child_id=h.child_id, row_ids=(mine.id, theirs.id))

    assert await _all(h) == [mine]
    assert await _all(h, child=h.other_child_id) == [theirs]


Scenario = Callable[[Harness], Awaitable[None]]

CASES: list[Scenario] = [
    칸을_그대로_읽는다,
    rows_는_날짜_양끝을_포함한다,
    rows_는_다른_아이_행을_주지_않는다,
    has_rows_는_그_아이_행이_있을_때만_True,
    update_는_메뉴_양_확인_칸을_바꾼다,
    update_는_날짜_끼니_출처를_바꾸지_않는다,
    없는_행_update_는_KeyError,
    다른_아이_행_update_는_KeyError_이고_그대로다,
    delete_는_그_아이_행만_지우고_없는_id_는_넘긴다,
    다른_아이_id_가_섞이면_KeyError_이고_하나도_안_지운다,
]
