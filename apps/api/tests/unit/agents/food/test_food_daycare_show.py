"""`show_daycare_meal` · `daycare_lookup` — 날짜를 말하면 그날(그 주) 급식 행을 보여 준다.

읽기만 한다 — 쓰기 · 알레르기 판정 · 추천이 없다. 날짜는 코드가 풀고, 모델이 준 구간이 원문과
다르거나 더 긴 날짜 표현을 잘라 준 것이면 돌려보낸다. 식단 추천용 `lookup_daycare_menu` 테스트는
`test_food_daycare_lookup.py` 다. 이름 · 메뉴 · 날짜는 전부 가짜다(오늘 = 2026-10-14 수).
"""

from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID

import pytest

from app.agents.food.daycare_lookup import (
    LOOKUP_MAX_DAYS,
    cut_from_longer_lookup_span,
    date_label,
    lookup_readouts,
    resolve_lookup_range,
)
from app.agents.food.readouts import READOUTS
from app.agents.food.result import ErrorCode
from app.agents.food.schemas.daycare import DaycareLookupArgs
from app.agents.food.store import DaycareMealRow, InMemoryDaycareMeals, SafetyEntry
from app.agents.food.tools.menu import show_daycare_meal
from tests.unit.agents.food.support import TODAY, context, day, daycare

MONDAY = date(2026, 11, 9)  # 월요일 — "다음 주 화요일" 과 "화요일" 이 갈리는 날


def code(key: str, **values: str) -> str:
    return READOUTS.render(key, **values).body


def texts(ctx) -> list[str]:
    return [readout.body for readout in ctx.state.readouts]


def label(days_ago: int) -> str:
    return date_label(day(days_ago))


class SpyDaycare(InMemoryDaycareMeals):
    """읽기 호출의 인자와 쓰기 호출 수를 센다."""

    def __init__(self, rows: list[DaycareMealRow]) -> None:
        super().__init__(rows)
        self.reads: list[tuple[date, date]] = []
        self.writes = 0

    async def rows(self, *, child_id: UUID, date_from: date, date_to: date) -> list[DaycareMealRow]:
        self.reads.append((date_from, date_to))
        return await super().rows(child_id=child_id, date_from=date_from, date_to=date_to)

    async def update(self, row: DaycareMealRow) -> None:
        self.writes += 1
        await super().update(row)

    async def delete(self, *, child_id: UUID, row_ids: tuple[UUID, ...]) -> None:
        self.writes += 1
        await super().delete(child_id=child_id, row_ids=row_ids)


async def show(utterance: str, span: str | None, rows: list[DaycareMealRow] | None = None):
    meals = SpyDaycare(rows if rows is not None else [])
    ctx = context(daycare=meals, utterance=utterance)
    result = await show_daycare_meal(ctx, DaycareLookupArgs(date_span=span))
    return ctx, meals, result


# ── 날짜 풀기 (코드) ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("span", "first", "last"),
    [
        (None, 0, 0),  # 날짜를 말하지 않았으면 오늘 — "급식 뭐야?"
        ("", 0, 0),
        ("오늘", 0, 0),
        ("어제", -1, -1),
        ("내일", 1, 1),
        ("모레", 2, 2),
        ("내일모레", 2, 2),
    ],
)
def test_상대_날짜_표현을_코드가_푼다(span: str | None, first: int, last: int) -> None:
    assert resolve_lookup_range(span, today=TODAY) == (
        TODAY + timedelta(days=first),
        TODAY + timedelta(days=last),
    )


def test_주를_밝힌_요일_이번_주_월일을_푼다() -> None:
    monday = TODAY - timedelta(days=TODAY.weekday())  # 2026-10-12
    assert resolve_lookup_range("다음 주 화요일", today=TODAY) == (
        monday + timedelta(days=8),
        monday + timedelta(days=8),
    )
    # 하루가 아니라 월~일 일곱 날이다
    assert resolve_lookup_range("이번 주", today=TODAY) == (monday, monday + timedelta(days=6))
    assert resolve_lookup_range("10월 20일", today=TODAY) == (date(2026, 10, 20),) * 2


def test_지난_요일은_지난_날이고_요일만은_다가올_요일이다() -> None:
    assert resolve_lookup_range("지난 화요일", today=TODAY) == (date(2026, 10, 13),) * 2
    assert resolve_lookup_range("저번 화요일", today=TODAY) == (date(2026, 10, 13),) * 2
    assert resolve_lookup_range("화요일", today=TODAY) == (date(2026, 10, 20),) * 2
    # 오늘이 그 요일이면 다음 주다 — 오늘은 "오늘 급식" 으로 묻는다
    assert resolve_lookup_range("수요일", today=TODAY) == (date(2026, 10, 21),) * 2


def test_연도_없는_지난_월일은_올해다_내년으로_보내지_않는다() -> None:
    # future 로 풀면 2027-11-05 가 되어 "등록된 급식이 없어요" 가 나간다
    assert resolve_lookup_range("11월 5일", today=MONDAY) == (date(2026, 11, 5),) * 2
    assert resolve_lookup_range("11월 12일", today=MONDAY) == (date(2026, 11, 12),) * 2


@pytest.mark.parametrize("span", ["그날", "언젠가", "며칠 전"])
def test_풀_수_없는_표현은_짐작하지_않는다(span: str) -> None:
    with pytest.raises(ValueError):
        resolve_lookup_range(span, today=TODAY)


# ── 잘린 구간 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("utterance", "span"),
    [
        ("다음 주 화요일 급식 알려줘", "화요일"),  # 월요일엔 다가올 화요일(이번 주)이 된다
        ("지난 화요일 급식 뭐였어", "화요일"),  # 지난 날 대신 다가올 날이 된다
        ("내일모레 급식 뭐야", "내일"),
        ("이번 주말 급식 뭐야", "이번 주"),
        ("다음 주 화요일 급식 알려줘", "다음 주"),  # 요일이 빠지면 하루가 일주일이 된다
    ],
)
def test_더_긴_날짜_표현을_자른_구간은_잘린_것으로_본다(utterance: str, span: str) -> None:
    assert cut_from_longer_lookup_span(utterance, span, today=MONDAY) is True


@pytest.mark.parametrize(
    ("utterance", "span"),
    [
        ("다음 주 화요일 급식 알려줘", "다음 주 화요일"),
        ("이번 주 급식 알려줘", "이번 주"),
        ("내일 급식 뭐야?", "내일"),
        ("11월 12일 급식 뭐야", "11월 12일"),
        ("내일모레 급식 뭐야", "모레"),  # 같은 날을 가리키는 짧은 구간
        ("화요일 급식 뭐야", "화요일"),
        ("급식 뭐야", "급식"),  # 풀 수 없는 구간 — 어차피 되묻는다
    ],
)
def test_전체를_말했거나_같은_날을_가리키면_잘린_것이_아니다(utterance: str, span: str) -> None:
    assert cut_from_longer_lookup_span(utterance, span, today=MONDAY) is False


def test_일요일엔_다음_주_화요일과_다가올_화요일이_같은_날이라_받는다() -> None:
    sunday = date(2026, 11, 8)
    assert cut_from_longer_lookup_span("다음 주 화요일 급식", "화요일", today=sunday) is False


# ── 화면 문구 ─────────────────────────────────────────────────────────
async def test_하루_조회는_그날_행을_끼니_순으로_한_줄씩_보여_준다() -> None:
    rows = [
        daycare("과일", days_ago=-1, slot="snack_pm"),
        daycare("현미밥", "미역국", days_ago=-1, slot="lunch"),
        daycare("남는급식", days_ago=-2, slot="lunch"),
    ]

    ctx, _, result = await show("내일 급식 뭐야?", "내일", rows)

    assert result.success
    assert texts(ctx) == [
        f"{label(-1)} 점심: 현미밥, 미역국\n{label(-1)} 오후 간식: 과일",
    ]
    (readout,) = ctx.state.readouts
    assert (readout.kind, readout.authored_by) == ("daycare", "code")
    assert "10월 15일(목)" in readout.body  # 날짜 라벨에 요일이 붙는다


async def test_이번_주_조회는_행이_있는_날만_날짜순으로_보여_준다() -> None:
    monday = TODAY - timedelta(days=TODAY.weekday())
    rows = [
        daycare("나중", days_ago=-3),
        daycare("먼저", days_ago=2),  # 월요일
    ]

    ctx, _, _ = await show("이번 주 급식 알려줘", "이번 주", rows)

    assert [row.split(": ")[1] for row in texts(ctx)[0].split("\n")] == ["먼저", "나중"]
    assert date_label(monday) in texts(ctx)[0]  # 행 없는 날은 건너뛰고 있는 날만 쓴다
    assert len(texts(ctx)[0].split("\n")) == 2


async def test_점심만_들어오는_기관도_간식만_들어오는_기관도_하루_한_줄이다() -> None:
    ctx, _, _ = await show("내일 급식 뭐야?", "내일", [daycare("국밥", days_ago=-1, slot="lunch")])
    assert texts(ctx) == [f"{label(-1)} 점심: 국밥"]

    ctx, _, _ = await show(
        "내일 간식 뭐야?", "내일", [daycare("요거트", days_ago=-1, slot="snack_am")]
    )
    assert texts(ctx) == [f"{label(-1)} 오전 간식: 요거트"]


async def test_날짜를_말하지_않으면_오늘이다() -> None:
    ctx, meals, _ = await show("급식 뭐야?", None, [daycare("오늘밥", days_ago=0)])

    assert meals.reads == [(TODAY, TODAY)]
    assert texts(ctx) == [f"{label(0)} 점심: 오늘밥"]


async def test_행이_없는_날은_없다고_말한다_날짜_라벨이_하루와_기간으로_갈린다() -> None:
    ctx, _, result = await show("모레 급식 뭐야?", "모레", [daycare("엉뚱한날", days_ago=-5)])
    assert result.success
    assert texts(ctx) == [code("daycare.lookup_empty", when=label(-2))]

    ctx, _, _ = await show("이번 주 급식 뭐야?", "이번 주", [])
    monday = TODAY - timedelta(days=TODAY.weekday())
    assert texts(ctx) == [
        code(
            "daycare.lookup_empty",
            when=f"{monday.month}월 {monday.day}일 ~ {monday.month}월 {monday.day + 6}일",
        )
    ]


async def test_지역_표준식단_행이_있으면_실제와_다를_수_있다는_안내가_따로_붙는다() -> None:
    standard = replace(daycare("표준밥", days_ago=-1), origin="center_standard")

    ctx, _, _ = await show(
        "내일 급식 뭐야?", "내일", [standard, daycare("실제밥", days_ago=-1, slot="snack_pm")]
    )

    assert texts(ctx) == [
        f"{label(-1)} 점심: 표준밥\n{label(-1)} 오후 간식: 실제밥",
        code("daycare.reference_menu"),
    ]


async def test_급식표에서_읽은_행뿐이면_표준식단_안내가_없다() -> None:
    ctx, _, _ = await show("내일 급식 뭐야?", "내일", [daycare("밥", days_ago=-1)])

    assert code("daycare.reference_menu") not in texts(ctx)


def test_메뉴가_빈_행은_줄을_만들지_않는다() -> None:
    row = daycare(days_ago=-1)  # menu_keys == ()

    (readout,) = lookup_readouts([row], date_from=day(-1), date_to=day(-1))

    assert readout.body == code("daycare.lookup_empty", when=label(-1))


async def test_알레르기_판정을_붙이지_않는다() -> None:
    meals = SpyDaycare([daycare("새우볶음밥", days_ago=-1)])
    ctx = context(
        daycare=meals,
        utterance="내일 급식 뭐야?",
        safety=[SafetyEntry(kind="allergy", label="새우", status="active")],
    )

    await show_daycare_meal(ctx, DaycareLookupArgs(date_span="내일"))

    assert texts(ctx) == [f"{label(-1)} 점심: 새우볶음밥"]  # "알레르기" · "괜찮아요" 가 없다


# ── 범위 ─────────────────────────────────────────────────────────────
async def test_이번_주는_일곱_날이라_통과하고_끝_날짜가_범위_마지막_날이다() -> None:
    monday = TODAY - timedelta(days=TODAY.weekday())

    _, meals, result = await show("이번 주 급식 뭐야?", "이번 주")

    assert result.success
    assert LOOKUP_MAX_DAYS == 7
    # 포트는 양끝 포함이다 — [start, end) 의 end 를 그대로 넘기면 하루가 더 읽힌다
    assert meals.reads == [(monday, monday + timedelta(days=6))]


@pytest.mark.parametrize("span", ["이번 달", "다음 달"])
async def test_한_번에_일주일을_넘으면_행을_읽지_않고_좁혀_달라고_한다(span: str) -> None:
    ctx, meals, result = await show(f"{span} 급식 알려줘", span, [daycare("밥", days_ago=0)])

    assert result.success  # 모델이 다시 부를 일이 아니라 보호자에게 되묻는 일이다
    assert texts(ctx) == [code("daycare.lookup_too_long")]
    assert meals.reads == []


async def test_과거_30일까지는_보이고_그보다_앞은_보존_범위_밖이다() -> None:
    _, meals, _ = await show("9월 14일 급식 뭐였어?", "9월 14일", [])
    assert meals.reads == [(date(2026, 9, 14),) * 2]  # 30일 전

    ctx, meals, result = await show(
        "9월 13일 급식 뭐였어?", "9월 13일", [daycare("밥", days_ago=31)]
    )
    assert result.success
    assert texts(ctx) == [code("daycare.out_of_range")]
    assert meals.reads == []


async def test_앞날은_제한이_없다_한_달_치_급식표가_들어온다() -> None:
    ctx, _, _ = await show("11월 12일 급식 뭐야?", "11월 12일", [daycare("밥", days_ago=-29)])

    assert texts(ctx) == [f"{label(-29)} 점심: 밥"]


# ── 모델이 준 구간 검사 ───────────────────────────────────────────────
async def test_원문에_없는_구간은_거절하고_아무것도_읽지_않는다() -> None:
    ctx, meals, result = await show("급식 뭐야?", "내일", [daycare("밥", days_ago=-1)])

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "내일" not in result.error["message"]  # 모델이 쓴 값을 message 에 싣지 않는다
    assert ctx.state.readouts == () and meals.reads == []


async def test_날짜_표현을_잘라_준_구간은_거절한다() -> None:
    ctx, meals, result = await show("내일모레 급식 뭐야?", "내일", [daycare("밥", days_ago=-1)])

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert ctx.state.readouts == () and meals.reads == []


async def test_풀_수_없는_날짜는_짐작하지_않고_되묻는다() -> None:
    ctx, meals, result = await show("그날 급식 뭐였어?", "그날", [daycare("밥", days_ago=0)])

    assert result.success
    assert texts(ctx) == [code("daycare.ambiguous")]
    assert meals.reads == []


# ── 읽기만 한다 ───────────────────────────────────────────────────────
async def test_조회는_어떤_경우에도_쓰지_않는다() -> None:
    rows = [daycare("밥", days_ago=-1), daycare("국", days_ago=0)]
    meals = SpyDaycare(rows)
    before = list(meals._rows.values())
    for span, utterance in [
        ("내일", "내일 급식 뭐야?"),
        ("이번 주", "이번 주 급식 알려줘"),
        (None, "급식"),
    ]:
        ctx = context(daycare=meals, utterance=utterance)
        await show_daycare_meal(ctx, DaycareLookupArgs(date_span=span))

    assert meals.writes == 0
    assert list(meals._rows.values()) == before
