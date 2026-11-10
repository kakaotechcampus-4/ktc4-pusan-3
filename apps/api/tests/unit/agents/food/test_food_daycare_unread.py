"""급식 표를 못 읽었을 때(`DaycareLookupError`) Food 가 어떻게 되는가 (#956).

급식 조회 · 고치기는 "못 읽음" 문구로 닫고(없음 문구가 아니다), 영양소 분석 · 식단 추천은
집 식사 기록으로 계속 간다. 아이 · 급식 · 메뉴는 전부 가짜다.
"""

from datetime import date
from uuid import UUID

import pytest

from app.agents.food.agent import run
from app.agents.food.context import build_gate
from app.agents.food.readouts import READOUTS
from app.agents.food.schemas.daycare import (
    DaycareLookupArgs,
    DeleteDaycareMealArgs,
    UpdateDaycareMealArgs,
)
from app.agents.food.store import DaycareLookupError, DaycareMealRow, InMemoryDaycareMeals
from app.agents.food.tools.menu import delete_daycare_meal, show_daycare_meal, update_daycare_meal
from tests.unit.agents.food.support import ScriptedLLM, context, daycare, food_task

UNREAD = READOUTS.get("closed.daycare_unread").template


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
