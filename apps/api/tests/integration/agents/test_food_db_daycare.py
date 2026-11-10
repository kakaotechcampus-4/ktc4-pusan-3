"""사진으로 저장한 급식이 Food 의 조회 · 고치기 · 결석 · 영양 판정까지 가는가 (#956).

급식은 전부 사진 저장이 쓰는 `insert_daycare_meals` 로 넣고, Food 는 러너 · 05 와 같은 어댑터
(`DbDaycareMeals`)로 읽는다. 메뉴 · 날짜 · 아이는 전부 가짜다.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents import entrypoint
from app.agents.food.agent import run
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.nutrition_eval import read_meal_records
from app.agents.food.readouts import READOUTS
from app.agents.food.registry import closed_readout_key
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.daycare import (
    DaycareLookupArgs,
    DeleteDaycareMealArgs,
    UpdateDaycareMealArgs,
)
from app.agents.food.tools.menu import delete_daycare_meal, show_daycare_meal, update_daycare_meal
from app.agents.memory.store import InMemoryStore
from app.agents.run_writes import RunWrites
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.daycare_meal.agent_reader import DbDaycareMeals
from app.domains.daycare_meal.repository import (
    NewDaycareMeal,
    insert_daycare_meals,
    list_daycare_meals,
)
from app.domains.food.agent_reader import DbMenuCatalog
from app.domains.identity.models import Parent
from app.domains.safety.agent_reader import DbSafetyReader
from tests.unit.agents.food.support import food_task

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=KST)
TODAY = date(2026, 10, 14)
BIRTH = date(2023, 2, 1)  # 44개월 — preschool


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    return await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=BIRTH,
        relation=ParentChildRelation.MOTHER,
    )


def wired(child, opener, *, daycare=None) -> tuple[FoodContext, RunWrites]:
    contexts, writes = entrypoint._domain_contexts(
        child_id=child.id,
        run_id="run-1",
        now=NOW,
        store=InMemoryStore(now=NOW),
        birth_date=BIRTH,
        child_health_granted=True,
        location=None,
        activity_weather=None,
        domain_store=None,
        food_catalog=DbMenuCatalog(opener),
        food_safety=DbSafetyReader(opener),
        food_daycare=daycare or DbDaycareMeals(opener),
    )
    return contexts["food"].for_task(), writes


async def save(session, child, day: date, slot: str, *menus: str) -> int:
    return await insert_daycare_meals(
        session,
        child_id=child.id,
        meals=[NewDaycareMeal(serve_date=day, meal_slot=slot, menu_keys=menus)],
    )


async def stored(session, child, day: date):
    return await list_daycare_meals(session, child_id=child.id, date_from=day, date_to=day)


async def test_사진으로_저장한_급식이_급식_조회에_나온다(session, opener, child):
    await save(session, child, TODAY, "lunch", "밥", "된장찌개")
    ctx, _ = wired(child, opener)
    ctx.state.utterance = "오늘 급식 뭐야"

    gate = await build_gate(ctx)
    result = await show_daycare_meal(ctx, DaycareLookupArgs(date_span="오늘"))

    assert gate.data.daycare_meal is True
    assert closed_readout_key(FoodTaskType.DAYCARE_LOOKUP, gate) is None
    assert result.success
    assert any("된장찌개" in readout.body for readout in ctx.state.readouts)


async def test_보호자_말로_고친_급식이_DB_에_남는다(session, opener, child):
    await save(session, child, TODAY, "lunch", "밥", "우유")
    ctx, writes = wired(child, opener)
    ctx.state.utterance = "오늘 우유 대신 두유 받았대"
    await build_gate(ctx)

    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    (row,) = await stored(session, child, TODAY)
    assert result.success
    assert (row.menu_keys, row.caregiver_checked) == (["밥", "두유"], True)
    assert writes.wrote is True


async def test_결석이면_그날_행이_지워진다(session, opener, child):
    tomorrow = date(2026, 10, 15)
    await save(session, child, tomorrow, "lunch", "밥")
    await save(session, child, tomorrow, "snack_pm", "과일")
    await save(session, child, TODAY, "lunch", "밥")
    ctx, _ = wired(child, opener)
    ctx.state.utterance = "내일 어린이집 빠져"
    await build_gate(ctx)

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="내일"))

    assert result.success and result.data["deleted"] == 2
    assert await stored(session, child, tomorrow) == []
    assert len(await stored(session, child, TODAY)) == 1  # 오늘 행은 그대로


async def test_고친_급식은_급식표를_다시_넣어도_남는다(session, opener, child):
    await save(session, child, TODAY, "lunch", "밥", "우유")
    ctx, _ = wired(child, opener)
    ctx.state.utterance = "오늘 우유 대신 두유 받았대"
    await build_gate(ctx)
    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    inserted = await save(session, child, TODAY, "lunch", "밥", "우유")  # 같은 급식표를 다시 올림

    (row,) = await stored(session, child, TODAY)
    assert inserted == 0
    assert row.menu_keys == ["밥", "두유"]


async def test_지난_급식이_영양_판정_기록에_들어간다(session, opener, child):
    await save(session, child, date(2026, 10, 13), "lunch", "밥", "미역국")
    await save(session, child, TODAY, "lunch", "밥")
    ctx, _ = wired(child, opener)

    records = await read_meal_records(
        ctx, date_from=date(2026, 10, 8), date_to=TODAY, daycare_before=TODAY
    )

    assert set(records.daycare) == {date(2026, 10, 13)}  # 오늘 급식은 아직 안 먹은 것이다


async def test_급식_어댑터가_실패해도_영양소_분석은_완료되고_안내가_붙는다(session, opener, child):
    @asynccontextmanager
    async def broken():
        raise ConnectionError("db")
        yield

    ctx, _ = wired(child, opener, daycare=DbDaycareMeals(broken))

    result = await run(
        food_task("nutrient_analysis", "요즘 영양은 괜찮은지 봐줘"), ctx, client=None
    )

    assert result.status == "completed"
    assert result.readouts[0].body == READOUTS.get("nutrient.daycare_unread").template
