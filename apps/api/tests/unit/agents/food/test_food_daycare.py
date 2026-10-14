"""`update_daycare_meal` · `delete_daycare_meal` — 보호자 말로 급식 행을 고치거나 지운다.

Food 가 보호자 말을 반영해 쓰는 유일한 자리이고 승인 게이트가 없다. 없는 급식을 새로 만들지 않고,
날짜 · 메뉴 · 양은 발화 원문의 부분 문자열이어야 하며 날짜 해석은 코드가 한다. 알레르기 · 메뉴 ·
날짜
는 전부 가짜다.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest

from app.agents.food.meal_dates import resolve_meal_date
from app.agents.food.readouts import READOUTS
from app.agents.food.result import ErrorCode
from app.agents.food.schemas.daycare import (
    DeleteDaycareMealArgs,
    KeepDaycareMealArgs,
    UpdateDaycareMealArgs,
)
from app.agents.food.store import (
    DaycareMealRow,
    InMemoryDaycareMeals,
    InMemoryMenuCatalog,
    SafetyEntry,
)
from app.agents.food.tools.menu import (
    delete_daycare_meal,
    keep_daycare_meal,
    update_daycare_meal,
)
from tests.unit.agents.food.support import CHILD, TODAY, context, day, daycare, menu_row

SHRIMP = [SafetyEntry(kind="allergy", label="새우", status="active")]


def texts(ctx) -> list[str]:
    return [readout.body for readout in ctx.state.readouts]


def code(key: str, **values: str) -> str:
    return READOUTS.render(key, **values).body


async def rows_on(meals: InMemoryDaycareMeals, days_ago: int = 0) -> list[DaycareMealRow]:
    return await meals.rows(child_id=CHILD, date_from=day(days_ago), date_to=day(days_ago))


def lunch(*menu_keys: str, days_ago: int = 0, **kwargs) -> DaycareMealRow:
    return daycare(*menu_keys, days_ago=days_ago, slot="lunch", **kwargs)


def label_of(days_ago: int) -> str:
    target = day(days_ago)
    return f"{target.month}월 {target.day}일"


# ── 날짜 해석 (코드) ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("span", "offset"),
    [
        ("오늘", 0),
        ("오늘자", 0),
        ("어제", -1),
        ("내일", 1),
        ("모레", 2),
        ("내일모레", 2),
    ],
)
def test_상대_날짜_표현을_코드가_푼다(span: str, offset: int) -> None:
    assert resolve_meal_date(span, today=TODAY) == TODAY + timedelta(days=offset)


def test_요일_표현은_주를_밝힌_것만_푼다() -> None:
    monday = TODAY - timedelta(days=TODAY.weekday())  # 2026-10-12 월
    assert resolve_meal_date("이번 주 화요일", today=TODAY) == monday + timedelta(days=1)
    assert resolve_meal_date("다음 주 화요일", today=TODAY) == monday + timedelta(days=8)
    # 지난 · 저번 {요일} 은 직전에 지나간 그 요일이다(오늘이 그 요일이면 지난주)
    last_tuesday = TODAY - timedelta(days=(TODAY.weekday() - 1) % 7 or 7)
    assert resolve_meal_date("지난 화요일", today=TODAY) == last_tuesday
    assert resolve_meal_date("저번 화요일", today=TODAY) == last_tuesday


def test_월일은_오늘과_가장_가까운_해로_푼다() -> None:
    assert resolve_meal_date("10월 12일", today=TODAY) == date(2026, 10, 12)
    assert resolve_meal_date("12월 30일", today=TODAY) == date(2026, 12, 30)


@pytest.mark.parametrize("span", ["", "  ", "화요일", "언젠가", "그때"])
def test_사전에_없거나_주를_안_밝힌_표현은_풀지_않는다(span: str) -> None:
    with pytest.raises(ValueError):
        resolve_meal_date(span, today=TODAY)


# ── update ───────────────────────────────────────────────────────────
async def test_우유_빼고_두유를_넣고_두_번_해도_두유는_한_번이다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유", "김치")])
    ctx = context(daycare=meals, utterance="오늘 급식 우유 빼고 두유 받았대")
    args = UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])

    first = await update_daycare_meal(ctx, args)
    after_first = await rows_on(meals)
    second = await update_daycare_meal(ctx, args)
    after_second = await rows_on(meals)

    assert first.success and second.success
    assert after_first[0].menu_keys == ("밥", "김치", "두유")
    assert after_second == after_first
    assert texts(ctx) == [code("daycare.updated", date=label_of(0), slot="점심", menu="두유")]


async def test_메뉴를_고치면_보호자가_확인한_행이_된다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 우유 대신 두유")

    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    [row] = await rows_on(meals)
    assert row.caregiver_checked is True


async def test_원문에_없는_span_은_거절하고_아무것도_바꾸지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 급식 우유 대신 뭔가 받았대")

    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "두유" not in result.error["message"]  # 모델이 쓴 값을 message 에 싣지 않는다
    assert (await rows_on(meals))[0].menu_keys == ("밥", "우유")
    assert ctx.state.readouts == ()


async def test_넣을_메뉴만_있으면_어떤_메뉴_대신인지_되묻고_바꾸지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 급식 대신 두유 받았대")

    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", added_spans=["두유"])
    )

    assert result.success  # 되묻기로 끝난다
    assert texts(ctx) == [code("daycare.replaced_unknown")]
    assert (await rows_on(meals))[0].menu_keys == ("밥", "우유")


async def test_뺄_메뉴가_그날_급식에_없으면_되묻고_바꾸지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "국")])
    ctx = context(daycare=meals, utterance="오늘 우유 대신 두유 받았대")

    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    assert texts(ctx) == [code("daycare.replaced_unknown")]
    assert (await rows_on(meals))[0].menu_keys == ("밥", "국")


async def test_뺄_메뉴만_있고_양도_없으면_되묻고_바꾸지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 급식 우유 빼고 줬대")

    await update_daycare_meal(ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"]))

    assert texts(ctx) == [code("daycare.ask_substitute")]
    assert (await rows_on(meals))[0].menu_keys == ("밥", "우유")


async def test_날짜를_말하지_않았으면_되묻고_오늘로_추정하지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="급식 우유 대신 두유 받았대")

    result = await update_daycare_meal(
        ctx,
        UpdateDaycareMealArgs(date_span="", removed_spans=["우유"], added_spans=["두유"]),
    )

    assert result.success
    assert texts(ctx) == [code("daycare.ambiguous")]
    assert (await rows_on(meals))[0].menu_keys == ("밥", "우유")


async def test_날짜를_못_풀면_되묻는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="언젠가 우유 대신 두유 받았대")

    await update_daycare_meal(
        ctx,
        UpdateDaycareMealArgs(date_span="언젠가", removed_spans=["우유"], added_spans=["두유"]),
    )

    assert texts(ctx) == [code("daycare.ambiguous")]


async def test_과거_30일_밖이면_거절하고_30일째는_받는다() -> None:
    meals = InMemoryDaycareMeals(
        [lunch("밥", "우유", days_ago=30), lunch("밥", "우유", days_ago=31)]
    )
    far = f"{day(31).month}월 {day(31).day}일"
    edge = f"{day(30).month}월 {day(30).day}일"

    ctx = context(daycare=meals, utterance=f"{far} 우유 대신 두유")
    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span=far, removed_spans=["우유"], added_spans=["두유"])
    )
    assert texts(ctx) == [code("daycare.out_of_range")]
    assert (await rows_on(meals, 31))[0].menu_keys == ("밥", "우유")

    ctx = context(daycare=meals, utterance=f"{edge} 우유 대신 두유")
    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span=edge, removed_spans=["우유"], added_spans=["두유"])
    )
    assert (await rows_on(meals, 30))[0].menu_keys == ("밥", "두유")


async def test_급식이_없는_날은_새로_만들지_않고_못_찾았다고_한다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유", days_ago=0)])
    ctx = context(daycare=meals, utterance="내일 급식은 카레래 우유 대신 두유")

    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="내일", removed_spans=["우유"], added_spans=["두유"])
    )

    assert texts(ctx) == [code("daycare.not_found")]
    assert await rows_on(meals, -1) == []  # INSERT 0건


async def test_그날_행이_여럿이면_끼니를_되묻고_하나면_바로_고친다() -> None:
    meals = InMemoryDaycareMeals(
        [lunch("밥", "우유"), daycare("과일", days_ago=0, slot="snack_pm")]
    )
    ctx = context(daycare=meals, utterance="오늘 우유 대신 두유")
    args = UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])

    await update_daycare_meal(ctx, args)

    assert texts(ctx) == [code("daycare.slot_ambiguous")]
    assert {r.meal_slot: r.menu_keys for r in await rows_on(meals)}["lunch"] == ("밥", "우유")

    ctx = context(daycare=meals, utterance="오늘 점심 우유 대신 두유")
    await update_daycare_meal(ctx, args.model_copy(update={"slot": "lunch"}))
    assert {r.meal_slot: r.menu_keys for r in await rows_on(meals)}["lunch"] == ("밥", "두유")


async def test_양만_말하면_메뉴는_그대로_두고_양_계수만_바꾼다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "국")])
    ctx = context(daycare=meals, utterance="오늘 급식 엄청 많이 먹었대")

    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", amount_span="엄청 많이")
    )

    [row] = await rows_on(meals)
    assert result.success
    assert (row.amount_factor, row.amount_known) == (1.5, True)
    assert row.menu_keys == ("밥", "국")
    assert row.caregiver_checked is False  # 양만 말했다 — 대체식을 확인한 것이 아니다


async def test_양만_말해도_반영했다는_문구를_낸다() -> None:
    # 메뉴가 안 바뀌어도 화면에 아무것도 안 나가면 보호자는 반영됐는지 알 수 없다
    meals = InMemoryDaycareMeals([lunch("밥", "국")])
    ctx = context(daycare=meals, utterance="오늘 급식 엄청 많이 먹었대")

    await update_daycare_meal(ctx, UpdateDaycareMealArgs(date_span="오늘", amount_span="엄청 많이"))

    assert texts(ctx) == [code("daycare.amount_updated", date=label_of(0), slot="점심")]


async def test_남겼다는_말은_양을_모르는_절반이다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "국", days_ago=1)])
    ctx = context(daycare=meals, utterance="어제 급식 남겼대")

    await update_daycare_meal(ctx, UpdateDaycareMealArgs(date_span="어제", amount_span="남겼대"))

    [row] = await rows_on(meals, 1)
    assert (row.amount_factor, row.amount_known) == (0.5, False)


async def test_양_사전에_없는_말은_있던_양을_덮지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=0, amount_factor=0.5, amount_known=True)])
    ctx = context(daycare=meals, utterance="오늘 급식 우물우물 먹었대")

    await update_daycare_meal(ctx, UpdateDaycareMealArgs(date_span="오늘", amount_span="우물우물"))

    [row] = await rows_on(meals)
    assert (row.amount_factor, row.amount_known) == (0.5, True)


async def test_다음_주_화요일_행의_메뉴를_바꾼다() -> None:
    target = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=8)  # 다음 주 화요일
    row = DaycareMealRow(
        id=UUID(int=7),
        child_id=CHILD,
        serve_date=target,
        meal_slot="lunch",
        menu_keys=("밥", "우유"),
    )
    meals = InMemoryDaycareMeals([row])
    ctx = context(daycare=meals, utterance="다음 주 화요일 우유 대신 두유 준대")

    await update_daycare_meal(
        ctx,
        UpdateDaycareMealArgs(
            date_span="다음 주 화요일", removed_spans=["우유"], added_spans=["두유"]
        ),
    )

    [after] = await meals.rows(child_id=CHILD, date_from=target, date_to=target)
    assert after.menu_keys == ("밥", "두유")


# ── 안전 재검사 ───────────────────────────────────────────────────────
async def test_알레르기_메뉴를_넣어도_저장은_하고_경고를_함께_낸다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 우유 대신 새우볶음밥 줬대", safety=SHRIMP)

    result = await update_daycare_meal(
        ctx,
        UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["새우볶음밥"]),
    )

    assert result.success
    assert (await rows_on(meals))[0].menu_keys == ("밥", "새우볶음밥")  # 저장은 막지 않는다
    assert texts(ctx) == [
        code("daycare.updated", date=label_of(0), slot="점심", menu="새우볶음밥"),
        code("daycare.allergy_conflict", label="새우"),
    ]
    assert "새우" not in str(result.data).replace(
        "새우볶음밥", ""
    )  # 알레르기 이름은 모델 입력에 없다


async def test_알레르기가_없으면_경고하지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 우유 대신 새우볶음밥 줬대", safety=())

    await update_daycare_meal(
        ctx,
        UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["새우볶음밥"]),
    )

    assert len(texts(ctx)) == 1


async def test_넣은_메뉴만_다시_본다_원래_급식의_알레르기_식품은_경고하지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("새우튀김", "우유")])
    ctx = context(daycare=meals, utterance="오늘 우유 대신 두유", safety=SHRIMP)

    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    assert len(texts(ctx)) == 1


async def test_안전_정보를_못_읽었어도_급식은_고치고_확인_못_했다고_알린다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(daycare=meals, utterance="오늘 우유 대신 두유", safety=None)

    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"])
    )

    assert result.success
    assert result.data["allergy_checked"] is False
    assert (await rows_on(meals))[0].menu_keys == ("밥", "두유")


async def test_카탈로그에_없는_메뉴도_키로_저장한다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", "우유")])
    ctx = context(
        daycare=meals,
        utterance="오늘 우유 대신 처음보는죽",
        catalog=InMemoryMenuCatalog([menu_row("두유")]),
    )

    await update_daycare_meal(
        ctx,
        UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["처음보는죽"]),
    )

    assert (await rows_on(meals))[0].menu_keys == ("밥", "처음보는죽")


# ── 쓰기가 끝난 뒤에만 "바꿨어요" ─────────────────────────────────────
class FailingDaycare(InMemoryDaycareMeals):
    async def update(self, row: DaycareMealRow) -> None:
        raise ConnectionError("쓰기 실패 (테스트 주입)")

    async def delete(self, *, child_id: UUID, row_ids: tuple[UUID, ...]) -> None:
        raise ConnectionError("쓰기 실패 (테스트 주입)")


async def test_쓰기_포트가_예외면_바꿨다는_문구가_나가지_않는다() -> None:
    ctx = context(daycare=FailingDaycare([lunch("밥", "우유")]), utterance="오늘 우유 대신 두유")

    with pytest.raises(ConnectionError):
        await update_daycare_meal(
            ctx,
            UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"]),
        )

    assert ctx.state.readouts == ()


# ── delete ───────────────────────────────────────────────────────────
async def test_끼니를_말하지_않으면_그날_행을_전부_지운다() -> None:
    meals = InMemoryDaycareMeals(
        [
            daycare("밥", days_ago=-2, slot="lunch"),
            daycare("과일", days_ago=-2, slot="snack_pm"),
            daycare("남는급식", days_ago=-3, slot="lunch"),
        ]
    )
    ctx = context(daycare=meals, utterance="모레 어린이집 빠져")

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="모레"))

    assert result.success
    assert await rows_on(meals, -2) == []
    assert len(await rows_on(meals, -3)) == 1  # 다른 날은 건드리지 않는다
    assert texts(ctx) == [code("daycare.absent", date=label_of(-2))]


async def test_끼니를_말하면_그_끼니만_지운다() -> None:
    meals = InMemoryDaycareMeals(
        [daycare("밥", days_ago=0, slot="lunch"), daycare("과일", days_ago=0, slot="snack_pm")]
    )
    ctx = context(daycare=meals, utterance="오늘 간식은 없었대")

    await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="오늘", slot="snack_pm"))

    assert [r.meal_slot for r in await rows_on(meals)] == ["lunch"]


async def test_삭제는_날짜가_모호하면_반드시_되묻고_지우지_않는다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥")])
    ctx = context(daycare=meals, utterance="어린이집 빠져")

    await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span=""))

    assert texts(ctx) == [code("daycare.ambiguous")]
    assert len(await rows_on(meals)) == 1


async def test_삭제도_원문에_없는_날짜는_거절한다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥")])
    ctx = context(daycare=meals, utterance="어린이집 빠져")

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="오늘"))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert len(await rows_on(meals)) == 1


async def test_삭제도_과거_30일_밖이면_거절하고_행이_없으면_못_찾았다고_한다() -> None:
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=40)])
    far = f"{day(40).month}월 {day(40).day}일"
    ctx = context(daycare=meals, utterance=f"{far} 빠졌어")
    await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span=far))
    assert texts(ctx) == [code("daycare.out_of_range")]
    assert len(await rows_on(meals, 40)) == 1

    ctx = context(daycare=meals, utterance="내일 빠져")
    await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="내일"))
    assert texts(ctx) == [code("daycare.not_found")]


async def test_삭제_쓰기가_실패하면_빠졌다는_문구가_나가지_않는다() -> None:
    ctx = context(daycare=FailingDaycare([lunch("밥")]), utterance="오늘 빠져")

    with pytest.raises(ConnectionError):
        await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="오늘"))

    assert ctx.state.readouts == ()


# ── 급식이 실제로 바뀌었다는 말인가 (코드 가드) ──────────────────────────
# 급식 갱신은 tool 을 꼭 부르게 해서(required) 묻는 말 · 가정도 갱신 · 삭제로 올 수 있다.
# 되돌릴 수 없는 삭제를 모델 판단에 맡기지 않는다 — 원문을 코드가 본다
@pytest.mark.parametrize(
    "utterance",
    [
        "모레 어린이집 쉬는 날이야?",
        "모레 어린이집 빠지니",
        "모레 어린이집 쉬나요",
        "모레 어린이집 쉴까",
    ],
)
async def test_묻는_말이면_지우지_않고_그대로_뒀다고_한다(utterance: str) -> None:
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=-2)])
    ctx = context(daycare=meals, utterance=utterance)

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="모레"))

    assert result.success
    assert len(await rows_on(meals, -2)) == 1
    assert texts(ctx) == [code("daycare.unchanged")]


@pytest.mark.parametrize(
    "utterance",
    [
        "내일 어린이집 빠지면",
        "내일 비 오면 어린이집 안 보낼래",
        "내일 어린이집 빠질 수도 있어",
        "내일 어린이집 쉴 것 같아",
    ],
)
async def test_가정이나_짐작이면_지우지_않는다(utterance: str) -> None:
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=-1)])
    ctx = context(daycare=meals, utterance=utterance)

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="내일"))

    assert result.success
    assert len(await rows_on(meals, -1)) == 1
    assert texts(ctx) == [code("daycare.unchanged")]


@pytest.mark.parametrize("utterance", ["내일 급식은 카레래", "내일 급식 뭐 나와"])
async def test_빠지거나_끼니가_없었다는_말이_없으면_삭제를_거절한다(utterance: str) -> None:
    # 모델에게 돌려줘서 갱신 · 그대로 두기 중 맞는 tool 을 다시 고르게 한다
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=-1)])
    ctx = context(daycare=meals, utterance=utterance)

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="내일"))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert len(await rows_on(meals, -1)) == 1


@pytest.mark.parametrize(
    "utterance",
    [
        "내일 어린이집 빠져",
        "내일 어린이집 안 가",
        "내일 결석해요",
        "내일은 가정보육해요",
        "내일 어린이집 쉬어",
        "내일 점심은 없대",
    ],
)
async def test_빠지거나_끼니가_없다는_말이면_지운다(utterance: str) -> None:
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=-1)])
    ctx = context(daycare=meals, utterance=utterance)

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="내일"))

    assert result.success
    assert await rows_on(meals, -1) == []


@pytest.mark.parametrize(
    ("utterance", "args"),
    [
        ("어제 급식 남겼어?", UpdateDaycareMealArgs(date_span="어제", amount_span="남겼어")),
        (
            "오늘 우유 대신 두유 받았니",
            UpdateDaycareMealArgs(date_span="오늘", removed_spans=["우유"], added_spans=["두유"]),
        ),
    ],
)
async def test_묻는_말이면_양도_메뉴도_바꾸지_않는다(
    utterance: str, args: UpdateDaycareMealArgs
) -> None:
    yesterday = lunch("밥", "우유", days_ago=1)
    today = lunch("밥", "우유", days_ago=0)
    meals = InMemoryDaycareMeals([yesterday, today])
    ctx = context(daycare=meals, utterance=utterance)

    result = await update_daycare_meal(ctx, args)

    assert result.success
    assert await rows_on(meals, 1) == [yesterday]
    assert await rows_on(meals, 0) == [today]
    assert texts(ctx) == [code("daycare.unchanged")]


async def test_가정이어도_메뉴를_바꿔_달라는_말은_고친다() -> None:
    # 갱신은 되돌릴 수 있다(다시 말하면 고친다) — 가정을 막는 것은 삭제뿐이다
    meals = InMemoryDaycareMeals([lunch("밥", "우유", days_ago=-1)])
    ctx = context(daycare=meals, utterance="내일 우유 나오면 두유로 바꿔줘")

    await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="내일", removed_spans=["우유"], added_spans=["두유"])
    )

    [row] = await rows_on(meals, -1)
    assert row.menu_keys == ("밥", "두유")


async def test_더_긴_날짜_낱말의_일부를_날짜로_쓰면_거절한다() -> None:
    # "내일모레" 의 "내일" 은 원문의 부분 문자열이지만 다른 날이다 — 그대로 풀면 내일 급식을 지운다
    tomorrow = lunch("밥", days_ago=-1)
    meals = InMemoryDaycareMeals([tomorrow, lunch("밥", days_ago=-2)])
    ctx = context(daycare=meals, utterance="내일모레는 어린이집 빠져")

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="내일"))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert await rows_on(meals, -1) == [tomorrow]

    ctx = context(daycare=meals, utterance="내일모레 점심은 많이 먹는대")
    result = await update_daycare_meal(
        ctx, UpdateDaycareMealArgs(date_span="내일", amount_span="많이")
    )
    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert await rows_on(meals, -1) == [tomorrow]


async def test_같은_날을_가리키는_더_짧은_낱말은_받는다() -> None:
    # "내일모레" 의 "모레" 는 같은 날(오늘 + 2)이라 거절할 이유가 없다
    meals = InMemoryDaycareMeals([lunch("밥", days_ago=-2)])
    ctx = context(daycare=meals, utterance="내일모레는 어린이집 빠져")

    result = await delete_daycare_meal(ctx, DeleteDaycareMealArgs(date_span="모레"))

    assert result.success
    assert await rows_on(meals, -2) == []


async def test_그대로_두는_tool_은_아무것도_쓰지_않고_그대로_뒀다고_한다() -> None:
    row = lunch("밥", "우유")
    meals = InMemoryDaycareMeals([row])
    ctx = context(daycare=meals, utterance="오늘 어린이집 급식 뭐 나왔어?")

    result = await keep_daycare_meal(ctx, KeepDaycareMealArgs())

    assert result.success
    assert await rows_on(meals) == [row]
    assert texts(ctx) == [code("daycare.unchanged")]
