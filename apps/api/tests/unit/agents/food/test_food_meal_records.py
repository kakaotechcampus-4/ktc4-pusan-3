"""`analyze_meal_records` · `check_repeated_menus` — 집 식사와 급식을 합쳐 읽는 기록 tool.

기관 중복(N-10)은 영양 판정(`daily_totals`)과 같은 읽기 코드를 쓴다. 이름 · 메뉴 · 기록은
전부 가짜다.
"""

from uuid import uuid4

from app.agents.food.schemas.common import MealPeriod, MealSlot
from app.agents.food.schemas.records import AnalyzeMealRecordsArgs, CheckRepeatedMenusArgs
from app.agents.food.store import InMemoryDaycareMeals, InMemoryFoodMemory
from app.agents.food.tools.records import (
    REPEATED_MIN_DAYS,
    analyze_meal_records,
    check_repeated_menus,
)
from tests.unit.agents.food.support import CHILD, context, day, daycare, observation


def memory(*observations) -> InMemoryFoodMemory:
    return InMemoryFoodMemory({}, {CHILD: list(observations)})


def meals_on(result, days_ago: int) -> list[dict]:
    wanted = day(days_ago).isoformat()
    return next(d["meals"] for d in result.data["days"] if d["date"] == wanted)


# ── analyze_meal_records ──────────────────────────────────────────────
async def test_가정_식사와_급식을_날짜별로_한_목록에_합친다() -> None:
    ctx = context(
        memory=memory(observation("계란말이", days_ago=1)),
        daycare=InMemoryDaycareMeals([daycare("된장찌개", "밥", days_ago=1)]),
    )

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs())

    assert result.success
    assert {(m["source"], m["menu"]) for m in meals_on(result, 1)} == {
        ("home", "계란말이"),
        ("daycare", "된장찌개"),
        ("daycare", "밥"),
    }
    assert result.data["records"] == 2  # 관찰 1행 + 급식 1행


async def test_기록이_없는_날은_비었다고_표시하고_채우지_않는다() -> None:
    ctx = context(memory=memory(observation("계란말이", days_ago=1)))

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs(period=MealPeriod.LAST_3_DAYS))

    days = {d["date"]: d for d in result.data["days"]}
    assert set(days) == {day(2).isoformat(), day(1).isoformat(), day(0).isoformat()}
    assert days[day(2).isoformat()] == {"date": day(2).isoformat(), "recorded": False, "meals": []}
    assert days[day(1).isoformat()]["recorded"] is True


async def test_기록이_하나도_없어도_실패가_아니다() -> None:
    result = await analyze_meal_records(context(), AnalyzeMealRecordsArgs())

    assert result.success
    assert result.data["records"] == 0
    assert all(not d["recorded"] for d in result.data["days"])


async def test_오늘_급식은_읽지_않고_오늘_가정_식사는_읽는다() -> None:
    ctx = context(
        memory=memory(observation("아침죽", days_ago=0)),
        daycare=InMemoryDaycareMeals([daycare("오늘급식", days_ago=0)]),
    )

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs(period=MealPeriod.TODAY))

    assert [(m["source"], m["menu"]) for m in meals_on(result, 0)] == [("home", "아침죽")]


async def test_그날_급식이_있으면_기관_공지에서_온_관찰은_한_번만_센다() -> None:
    # 알림장이 알려 준 점심과 급식표의 점심은 같은 식사다 (N-10)
    ctx = context(
        memory=memory(
            observation("된장찌개", days_ago=1, confidence_source="institution_notice"),
            observation("고구마", days_ago=1, source_notice_id=uuid4()),
            observation("할머니댁간식", days_ago=1, confidence_source="parent_direct"),
        ),
        daycare=InMemoryDaycareMeals([daycare("된장찌개", days_ago=1)]),
    )

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs())

    assert {(m["source"], m["menu"]) for m in meals_on(result, 1)} == {
        ("daycare", "된장찌개"),
        ("home", "할머니댁간식"),
    }
    assert result.data["records"] == 2


async def test_남긴_메뉴는_표시하고_양은_숫자로_싣지_않는다() -> None:
    ctx = context(
        memory=memory(
            observation("국", days_ago=1, amount_text="남김"),
            observation("밥", days_ago=1, amount_text="다 먹음"),
            observation("반찬", days_ago=1),
        ),
        daycare=InMemoryDaycareMeals(
            [daycare("간식", days_ago=2, amount_factor=0.5, amount_known=True)]
        ),
    )

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs())

    left = {m["menu"]: m["left"] for d in result.data["days"] for m in d["meals"]}
    assert left == {"국": True, "밥": False, "반찬": False, "간식": True}
    assert "factor" not in str(result.data)
    assert "amount" not in str(result.data)


async def test_끼니를_고르면_급식만_걸러지고_끼니_모르는_가정_기록은_따로_센다() -> None:
    ctx = context(
        memory=memory(observation("집밥", days_ago=1)),
        daycare=InMemoryDaycareMeals(
            [
                daycare("점심급식", days_ago=1, slot="lunch"),
                daycare("오전간식", days_ago=1, slot="snack_am"),
            ]
        ),
    )

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs(meal_slots=[MealSlot.LUNCH]))

    assert [(m["menu"], m["slot"]) for m in meals_on(result, 1)] == [("점심급식", "lunch")]
    assert result.data["unslotted_omitted"] == 1


async def test_간식_두_칸은_간식_하나로_본다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals(
            [
                daycare("오전간식", days_ago=1, slot="snack_am"),
                daycare("오후간식", days_ago=1, slot="snack_pm"),
            ]
        )
    )

    result = await analyze_meal_records(ctx, AnalyzeMealRecordsArgs(meal_slots=[MealSlot.SNACK]))

    assert {m["slot"] for m in meals_on(result, 1)} == {"snack"}
    assert len(meals_on(result, 1)) == 2


# ── check_repeated_menus ──────────────────────────────────────────────
def test_반복_기준은_서로_다른_날_세_번이다() -> None:
    assert REPEATED_MIN_DAYS == 3


async def test_세_날_이상_나온_메뉴만_반복으로_나온다() -> None:
    ctx = context(
        memory=memory(
            observation("계란말이", days_ago=0),
            observation("계란말이", days_ago=2),
            observation("계란말이", days_ago=4),
            observation("김밥", days_ago=1),
            observation("김밥", days_ago=3),
        )
    )

    result = await check_repeated_menus(ctx, CheckRepeatedMenusArgs())

    assert result.success
    repeated = result.data["repeated"]
    assert [(r["menu"], r["days"]) for r in repeated] == [("계란말이", 3)]
    assert repeated[0]["last_on"] == day(0).isoformat()


async def test_같은_날_두_번_나온_메뉴는_하루로_센다() -> None:
    ctx = context(
        memory=memory(
            observation("계란말이", days_ago=0),
            observation("계란말이", days_ago=0),
            observation("계란말이", days_ago=1),
        )
    )

    result = await check_repeated_menus(ctx, CheckRepeatedMenusArgs())

    assert result.data["repeated"] == []


async def test_집과_급식에서_같은_메뉴가_나오면_합쳐_세고_어느_쪽인지_표시한다() -> None:
    ctx = context(
        memory=memory(observation("된장찌개", days_ago=2)),
        daycare=InMemoryDaycareMeals(
            [daycare("된장찌개", days_ago=1), daycare("된장찌개", days_ago=3)]
        ),
    )

    result = await check_repeated_menus(ctx, CheckRepeatedMenusArgs())

    [entry] = result.data["repeated"]
    assert entry["days"] == 3
    assert entry["sources"] == ["daycare", "home"]


async def test_기관_공지에서_온_관찰은_급식과_겹치면_두_번_세지_않는다() -> None:
    ctx = context(
        memory=memory(
            observation("된장찌개", days_ago=1, confidence_source="institution_notice"),
            observation("된장찌개", days_ago=2, confidence_source="institution_notice"),
        ),
        daycare=InMemoryDaycareMeals(
            [daycare("된장찌개", days_ago=1), daycare("된장찌개", days_ago=2)]
        ),
    )

    result = await check_repeated_menus(ctx, CheckRepeatedMenusArgs())

    assert result.data["repeated"] == []  # 이틀이다. 알림장 두 건이 보태 사흘이 되지 않는다


async def test_반복이_없으면_빈_목록이고_기록_수를_함께_준다() -> None:
    ctx = context(memory=memory(observation("계란말이", days_ago=0)))

    result = await check_repeated_menus(ctx, CheckRepeatedMenusArgs())

    assert result.success
    assert result.data["repeated"] == []
    assert result.data["records"] == 1


async def test_기간_밖의_기록은_세지_않는다() -> None:
    ctx = context(memory=memory(*[observation("계란말이", days_ago=n) for n in (0, 1, 5)]))

    result = await check_repeated_menus(ctx, CheckRepeatedMenusArgs(period=MealPeriod.LAST_3_DAYS))

    assert result.data["repeated"] == []
