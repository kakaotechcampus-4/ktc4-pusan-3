"""어린이집 급식 표 `daycare_meal` 과 저장 함수 (K 묶음 1/2).

설계 정본은 docs/agents/food/food_agent_own_table.md §5 다. Food Agent 포트
(agents/food/store/ports.py 의 DaycareMealRow · DaycareMealStore)가 이 표를 읽고 고친다.

여기서 지키는 것.
    ① 같은 아이 · 같은 날 · 같은 끼니는 한 행 — UNIQUE (child_id, serve_date, meal_slot)
    ② 끼니는 기관이 정한 셋(lunch · snack_am · snack_pm), 출처는 ocr · center_standard 만
    ③ 먹은 양은 모르는 것이 기본
       — amount_factor 1.00 · amount_known false · caregiver_checked false
    ④ 아이가 지워지면 같이 지워진다
    ⑤ 급식표를 넣을 때 이미 있는 날 · 끼니는 건드리지 않는다
       — Food 가 보호자 말로 고친 것을 덮지 않게
    ⑥ 읽기는 날짜 범위의 양 끝을 포함하고 (날짜, 끼니) 순 — Agent 메모리 구현과 같다

메뉴 · 날짜는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.daycare_meal.repository import (
    NewDaycareMeal,
    insert_daycare_meals,
    list_daycare_meals,
)
from app.domains.identity.models import Parent


async def _child(session) -> UUID:
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="테스트아이", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child.id


async def _insert_raw(session, child_id, **values):
    columns = {
        "child_id": child_id,
        "serve_date": date(2026, 10, 12),
        "meal_slot": "lunch",
        **values,
    }
    names = ", ".join(columns)
    params = ", ".join(f":{name}" for name in columns)
    return (
        await session.execute(
            text(f"INSERT INTO daycare_meal ({names}) VALUES ({params}) RETURNING *"), columns
        )
    ).one()


# ── 표 규칙 ────────────────────────────────────────────────────────


async def test_먹은_양은_모르는_것이_기본이다(session):
    child_id = await _child(session)

    row = await _insert_raw(session, child_id, menu_keys=["쌀밥", "미역국"])

    assert (row.amount_factor, row.amount_known, row.caregiver_checked, row.origin) == (
        Decimal("1.00"),
        False,
        False,
        "ocr",
    )


@pytest.mark.parametrize(
    "values",
    [
        {"menu_keys": ["쌀밥"], "meal_slot": "dinner"},  # 기관이 정한 셋 밖
        {"menu_keys": ["쌀밥"], "origin": "neis"},  # NEIS 는 쓰지 않는다 (10-09)
        {"menu_keys": None},  # 메뉴 목록은 비울 수 없다 — 결석은 행 삭제다
    ],
)
async def test_정해진_값_밖은_DB_가_막는다(session, values):
    child_id = await _child(session)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _insert_raw(session, child_id, **values)


async def test_같은_날_같은_끼니는_한_행(session):
    child_id = await _child(session)
    await _insert_raw(session, child_id, menu_keys=["쌀밥"])

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _insert_raw(session, child_id, menu_keys=["보리밥"])


async def test_아이가_지워지면_급식도_지워진다(session):
    child_id = await _child(session)
    await _insert_raw(session, child_id, menu_keys=["쌀밥"])

    await session.execute(delete(Child).where(Child.id == child_id))

    left = await session.scalar(
        text("SELECT count(*) FROM daycare_meal WHERE child_id = :id"), {"id": child_id}
    )
    assert left == 0


# ── 저장 함수 ──────────────────────────────────────────────────────


async def test_넣을_때_이미_있는_날_끼니는_건드리지_않는다(session):
    child_id = await _child(session)
    # Food 가 보호자 말("두유로 받았대")로 이미 고친 행
    await _insert_raw(session, child_id, menu_keys=["두유"], caregiver_checked=True)

    inserted = await insert_daycare_meals(
        session,
        child_id=child_id,
        meals=[
            NewDaycareMeal(serve_date=date(2026, 10, 12), meal_slot="lunch", menu_keys=("우유",)),
            NewDaycareMeal(
                serve_date=date(2026, 10, 12), meal_slot="snack_pm", menu_keys=("떡", "보리차")
            ),
        ],
    )

    assert inserted == 1
    rows = await list_daycare_meals(
        session, child_id=child_id, date_from=date(2026, 10, 12), date_to=date(2026, 10, 12)
    )
    assert [(row.meal_slot, list(row.menu_keys), row.caregiver_checked) for row in rows] == [
        ("lunch", ["두유"], True),
        ("snack_pm", ["떡", "보리차"], False),
    ]


async def test_읽기는_양_끝을_포함하고_날짜_끼니_순이며_다른_아이는_빠진다(session):
    child_a = await _child(session)
    child_b = await _child(session)
    await insert_daycare_meals(
        session,
        child_id=child_a,
        meals=[
            NewDaycareMeal(date(2026, 10, 14), "lunch", ("카레",)),
            NewDaycareMeal(date(2026, 10, 12), "snack_pm", ("떡",)),
            NewDaycareMeal(date(2026, 10, 12), "lunch", ("쌀밥",)),
            NewDaycareMeal(date(2026, 10, 15), "lunch", ("범위 밖",)),
        ],
    )
    await insert_daycare_meals(
        session, child_id=child_b, meals=[NewDaycareMeal(date(2026, 10, 13), "lunch", ("남의 것",))]
    )

    rows = await list_daycare_meals(
        session, child_id=child_a, date_from=date(2026, 10, 12), date_to=date(2026, 10, 14)
    )

    assert [(row.serve_date.day, row.meal_slot) for row in rows] == [
        (12, "lunch"),
        (12, "snack_pm"),
        (14, "lunch"),
    ]
