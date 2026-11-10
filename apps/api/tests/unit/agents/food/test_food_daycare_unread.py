"""급식 표를 못 읽었을 때(`DaycareLookupError`) Food 가 어떻게 되는가 (#956).

급식 조회 · 고치기는 "못 읽음" 문구로 닫고(없음 문구가 아니다), 영양소 분석 · 식단 추천은
집 식사 기록으로 계속 간다. 아이 · 급식 · 메뉴는 전부 가짜다.
"""

from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from app.agents.food.agent import run
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.nutrition_eval import read_meal_records
from app.agents.food.readouts import READOUTS
from app.agents.food.schemas.daycare import (
    DaycareLookupArgs,
    DeleteDaycareMealArgs,
    UpdateDaycareMealArgs,
)
from app.agents.food.schemas.menu import DaycareMenuArgs
from app.agents.food.store import (
    DaycareLookupError,
    DaycareMealRow,
    InMemoryDaycareMeals,
    InMemoryFoodMemory,
    InMemoryMenuCatalog,
)
from app.agents.food.tools.menu import (
    delete_daycare_meal,
    lookup_daycare_menu,
    show_daycare_meal,
    update_daycare_meal,
)
from tests.unit.agents.food.support import (
    CHILD,
    ScriptedLLM,
    context,
    day,
    daycare,
    food_task,
    menu_row,
    observation,
    propose,
)
from tests.unit.agents.food.test_food_meal_run import (
    SOUP,
    SPINACH,
    TOFU,
    meal_context,
    meal_task,
    shown_menus,
)

UNREAD = READOUTS.get("closed.daycare_unread").template
NUTRIENT_UNREAD = READOUTS.get("nutrient.daycare_unread").template
MEAL_UNREAD = READOUTS.get("meal.daycare_unread").template


class FlakyDaycare(InMemoryDaycareMeals):
    """`has_rows` 는 되는데 `rows` 만 실패한다 — task 도중에 읽기가 끊긴 경우."""

    async def rows(self, *, child_id: UUID, date_from: date, date_to: date) -> list[DaycareMealRow]:
        raise DaycareLookupError("daycare_meal 조회 실패 (테스트 주입)")


async def test_급식을_못_읽으면_게이트는_없음과_못_읽음을_따로_안다() -> None:
    ctx = context(daycare=InMemoryDaycareMeals(fail_reads=True))

    gate = await build_gate(ctx)

    assert (gate.data.daycare_meal, gate.data.daycare_unread) == (False, True)
    assert ctx.state.daycare_unread is True


async def test_급식을_읽었으면_게이트는_못_읽음이_아니다() -> None:
    ctx = context(daycare=InMemoryDaycareMeals([daycare("밥", days_ago=1)]))

    gate = await build_gate(ctx)

    assert (gate.data.daycare_meal, gate.data.daycare_unread) == (True, False)
    assert ctx.state.daycare_unread is False


@pytest.mark.parametrize(
    ("task_type", "text"),
    [
        ("daycare_lookup", "오늘 급식 뭐야"),
        ("daycare_meal", "오늘 우유 대신 두유 받았대"),
    ],
)
async def test_급식을_못_읽으면_없음_문구가_아니라_못_읽음_문구로_닫는다(
    task_type: str, text: str
) -> None:
    ctx = context(daycare=InMemoryDaycareMeals(fail_reads=True))
    llm = ScriptedLLM()

    result = await run(food_task(task_type, text), ctx, client=llm)

    assert result.status == "blocked"
    assert [readout.body for readout in result.readouts] == [UNREAD]
    assert READOUTS.get("closed.no_daycare").template not in UNREAD
    assert llm.calls == 0


async def test_조회_중에_급식_읽기가_실패하면_못_읽음_문구로_멈춘다() -> None:
    ctx = context(daycare=FlakyDaycare([daycare("밥", days_ago=0)]), utterance="오늘 급식 뭐야")

    result = await show_daycare_meal(ctx, DaycareLookupArgs(date_span="오늘"))

    assert result.success and result.data["status"] == "stopped"
    assert [readout.body for readout in ctx.state.readouts] == [UNREAD]
    assert ctx.state.daycare_unread is True


async def test_고치기_중에_급식_읽기가_실패하면_못_읽음_문구로_멈추고_쓰지_않는다() -> None:
    flaky = FlakyDaycare([daycare("밥", "우유", days_ago=0)])
    ctx = context(daycare=flaky, utterance="오늘 우유 대신 두유 받았대")

    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    assert result.success and result.data["status"] == "stopped"
    assert [readout.body for readout in ctx.state.readouts] == [UNREAD]
    assert next(iter(flaky._rows.values())).menu_keys == ("밥", "우유")


async def test_삭제_중에_급식_읽기가_실패하면_못_읽음_문구로_멈추고_지우지_않는다() -> None:
    flaky = FlakyDaycare([daycare("밥", days_ago=0)])
    ctx = context(daycare=flaky, utterance="오늘 어린이집 빠져")

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="오늘"))

    assert result.success and result.data["status"] == "stopped"
    assert [readout.body for readout in ctx.state.readouts] == [UNREAD]
    assert len(flaky._rows) == 1


# ── 영양소 분석 · 식단 추천 — 급식 없이 집 기록으로 계속 간다 ──────────────────
class SpyDaycare(InMemoryDaycareMeals):
    """`rows` 호출 수를 센다. `InMemoryDaycareMeals` 의 읽기 실패(`fail_reads`)도 그대로 쓴다."""

    row_calls = 0

    async def rows(self, *, child_id: UUID, date_from: date, date_to: date) -> list[DaycareMealRow]:
        self.row_calls += 1
        return await super().rows(child_id=child_id, date_from=date_from, date_to=date_to)


def home_meals() -> InMemoryFoodMemory:
    """7일 · 매일 집에서 쌀밥, 어제 · 2일 전 시금치나물."""
    rows = [observation("쌀밥", days_ago=d) for d in range(7)]
    rows += [observation("시금치나물", days_ago=d) for d in (1, 2)]
    return InMemoryFoodMemory(observations={CHILD: rows})


def with_daycare(ctx: FoodContext, store: InMemoryDaycareMeals) -> FoodContext:
    return replace(ctx, ports=replace(ctx.ports, daycare=store))


async def test_급식을_못_읽으면_기록은_집_식사만_돌려주고_다시_읽지_않는다() -> None:
    spy = SpyDaycare([daycare("밥", days_ago=1)], fail_reads=True)
    ctx = context(daycare=spy, memory=home_meals())

    first = await read_meal_records(ctx, date_from=day(6), date_to=day(0))
    await read_meal_records(ctx, date_from=day(6), date_to=day(0))

    assert first.daycare == {} and day(1) in first.observations
    assert ctx.state.daycare_unread is True
    assert spy.row_calls == 1  # 두 번째 읽기는 급식 표를 다시 부르지 않는다


async def _nutrient(store: InMemoryDaycareMeals):
    catalog = InMemoryMenuCatalog(
        [
            menu_row("쌀밥", food_groups=frozenset({"grains"})),
            menu_row("시금치나물", food_groups=frozenset({"vegetables"})),
        ]
    )
    ctx = context(daycare=store, memory=home_meals(), catalog=catalog)
    return await run(food_task("nutrient_analysis", "요즘 영양은 괜찮은지 봐줘"), ctx, client=None)


async def test_급식을_못_읽어도_영양소_분석은_집_기록으로_답한다() -> None:
    baseline = await _nutrient(InMemoryDaycareMeals())

    result = await _nutrient(InMemoryDaycareMeals(fail_reads=True))

    assert baseline.status == result.status == "completed"
    assert result.readouts[0].body == NUTRIENT_UNREAD
    assert [r.body for r in result.readouts[1:]] == [r.body for r in baseline.readouts]
    assert NUTRIENT_UNREAD not in [r.body for r in baseline.readouts]


async def test_급식을_못_읽어도_식단_추천은_나오고_안내가_붙는다() -> None:
    ctx = with_daycare(meal_context(TOFU, SPINACH, SOUP), InMemoryDaycareMeals(fail_reads=True))
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), ctx, client=llm)

    assert result.status == "completed" and len(result.suggestions) == 3
    assert MEAL_UNREAD in [r.body for r in result.readouts]


async def test_이유기_식단_추천도_급식을_못_읽으면_계속_간다() -> None:
    birth = date(2026, 2, 14)
    names = await shown_menus(birth)
    ctx = with_daycare(meal_context(birth=birth), InMemoryDaycareMeals(fail_reads=True))
    llm = ScriptedLLM(propose(*names, evidence=[]))

    result = await run(meal_task(), ctx, client=llm)

    assert result.status == "completed" and len(result.suggestions) == 3
    assert MEAL_UNREAD in [r.body for r in result.readouts]


async def test_lookup_daycare_menu_가_실패하면_모델에게_UPSTREAM_ERROR_를_준다() -> None:
    ctx = context(daycare=FlakyDaycare([daycare("밥", days_ago=0)]))

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs(day="today"))

    assert (result.success, result.error["code"]) == (False, "UPSTREAM_ERROR")
    assert ctx.state.daycare_unread is True
    assert "밥" not in result.error["message"]


async def test_급식을_읽었으면_안내가_붙지_않는다() -> None:
    served = InMemoryDaycareMeals([daycare("밥", days_ago=1)])
    nutrient = await _nutrient(served)
    ctx = with_daycare(meal_context(TOFU, SPINACH, SOUP), served)
    meal = await run(
        meal_task(), ctx, client=ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))
    )

    assert NUTRIENT_UNREAD not in [r.body for r in nutrient.readouts]
    assert MEAL_UNREAD not in [r.body for r in meal.readouts]
