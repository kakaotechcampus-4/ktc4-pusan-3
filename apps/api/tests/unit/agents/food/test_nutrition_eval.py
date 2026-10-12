"""하루 영양 합계 — 정본은 `docs/agents/food/영양소_계산_설계.md` §3.

판정 자체(구간·히스테리시스)는 `test_nutrition_eval.py` 의 Task 6 부분(§6)이 본다.
"""

from datetime import date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agents.food.context import FoodContext
from app.agents.food.nutrition_eval import daily_totals
from app.agents.food.store import (
    DaycareMealRow,
    FoodObservation,
    InMemoryDaycareMeals,
    InMemoryFoodMemory,
    InMemoryMenuCatalog,
    InMemoryMenuSource,
    InMemoryProfile,
    MenuCatalogRow,
    in_memory_ports,
)

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)  # "오늘" = 2026-10-02 → 10-01 은 오늘 이전
BIRTH_DATE = date(2024, 6, 2)  # NOW 기준 28개월 — 섭취기준 1-2y 구간
DAY = date(2026, 10, 1)
MENU_KEY = "된장찌개"


def _menu_row(**overrides: object) -> MenuCatalogRow:
    base = dict(
        menu_key=MENU_KEY,
        display_name=MENU_KEY,
        source="mfds_nutri",
        resolved=True,
        synced_at=NOW,
        serving_g=300.0,
        nutrients={"protein": 10.0, "calcium": 5.0, "energy": 100.0},
        food_groups=frozenset({"고기생선달걀콩"}),
    )
    base.update(overrides)
    return MenuCatalogRow(**base)  # type: ignore[arg-type]


def _observation(
    *,
    subject: str = MENU_KEY,
    amount_text: str | None = "다 먹음",
    confidence_source: str | None = "parent_direct",
    source_notice_id: UUID | None = None,
) -> FoodObservation:
    return FoodObservation(
        id=uuid4(),
        child_id=CHILD,
        observed_on=DAY,
        subject=subject,
        amount_text=amount_text,
        polarity=1,
        updated_at=NOW,
        confidence_source=confidence_source,  # type: ignore[arg-type]
        source_notice_id=source_notice_id,
    )


def _daycare_row(*, menu_keys: tuple[str, ...] = (MENU_KEY,)) -> DaycareMealRow:
    return DaycareMealRow(
        id=uuid4(),
        child_id=CHILD,
        serve_date=DAY,
        meal_slot="lunch",
        menu_keys=menu_keys,
    )


def _context(
    *,
    observations: list[FoodObservation] = (),
    daycare_rows: list[DaycareMealRow] = (),
    catalog_rows: list[MenuCatalogRow] = (),
    menu_source: InMemoryMenuSource | None = None,
) -> FoodContext:
    ports_kwargs: dict[str, object] = {
        "profile": InMemoryProfile({CHILD: BIRTH_DATE}),
        "memory": InMemoryFoodMemory(observations={CHILD: list(observations)}),
        "catalog": InMemoryMenuCatalog(list(catalog_rows)),
        "daycare": InMemoryDaycareMeals(list(daycare_rows)),
    }
    if menu_source is not None:
        ports_kwargs["menu_source"] = menu_source
    return FoodContext(
        child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=in_memory_ports(**ports_kwargs)
    )


@pytest.fixture
def context() -> FoodContext:
    return _context(observations=[_observation()], catalog_rows=[_menu_row()])


@pytest.fixture
def context_with_notice() -> FoodContext:
    return _context(
        observations=[_observation(confidence_source="institution_notice")],
        daycare_rows=[_daycare_row()],
        catalog_rows=[_menu_row()],
    )


@pytest.fixture
def context_hearsay_other() -> FoodContext:
    return _context(
        observations=[_observation(subject="할머니댁간식", confidence_source="parent_hearsay")],
        daycare_rows=[_daycare_row()],
        catalog_rows=[_menu_row()],
    )


@pytest.fixture
def context_no_serving() -> FoodContext:
    return _context(
        observations=[_observation()],
        catalog_rows=[_menu_row(serving_g=None)],
    )


@pytest.fixture
def context_source_down() -> FoodContext:
    return _context(
        observations=[_observation(subject="맛있는된장찌개")],
        menu_source=InMemoryMenuSource(fail=True),
    )


@pytest.fixture
def context_unknown_amount() -> FoodContext:
    return _context(observations=[_observation(amount_text=None)], catalog_rows=[_menu_row()])


async def test_집_식사와_급식이_하루_합계에_들어간다(context) -> None:
    totals = await daily_totals(context, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1))

    assert len(totals) == 1
    assert totals[0].grams["protein"] > 0


async def test_그날_급식이_있으면_알림장_관찰은_합계에서_뺀다(context_with_notice) -> None:
    # N-10 — 같은 끼니가 두 번 세어지면 거짓 high 가 난다 (설계 §3)
    totals = await daily_totals(
        context_with_notice, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)
    )

    assert totals[0].records == 1  # 급식 행만


async def test_급식에_없는_메뉴를_전해_들은_것은_집_식사로_센다(context_hearsay_other) -> None:
    totals = await daily_totals(
        context_hearsay_other, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)
    )

    assert totals[0].records == 2


async def test_기록이_없는_날은_목록에_없다(context) -> None:
    # 0 으로 넣으면 일평균이 내려가 거짓 low 가 난다 (설계 §1-3)
    totals = await daily_totals(context, date_from=date(2026, 9, 1), date_to=date(2026, 9, 3))

    assert totals == []


async def test_1인분_중량이_없는_메뉴는_합계에서_빼고_식품군에만_쓴다(context_no_serving) -> None:
    # 100g 으로 가정하면 없는 숫자를 만드는 것이다 (설계 §4)
    totals = await daily_totals(
        context_no_serving, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)
    )

    assert totals[0].grams == {}
    assert totals[0].food_groups
    assert totals[0].weighed_menus == 0


async def test_외부_API_장애면_그_메뉴는_unresolved_이고_카탈로그에_남지_않는다(
    context_source_down,
) -> None:
    # MenuSourceError 를 "정말 없음" 으로 뭉개면 못 찾은 이름이 전역 테이블에 남는다
    totals = await daily_totals(
        context_source_down, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)
    )

    assert totals[0].resolved_menus == 0
    assert await context_source_down.ports.catalog.get("맛있는된장찌개") is None


async def test_양을_모르는_기록은_절반_가중으로_들어간다(context_unknown_amount, context) -> None:
    totals = await daily_totals(
        context_unknown_amount, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1)
    )
    known = await daily_totals(context, date_from=date(2026, 10, 1), date_to=date(2026, 10, 1))

    assert totals[0].grams["protein"] == pytest.approx(known[0].grams["protein"] * 0.5)
