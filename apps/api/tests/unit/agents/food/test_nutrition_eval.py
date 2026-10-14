"""하루 영양 합계 — 정본은 `docs/agents/food/영양소_계산_설계.md` §3.

판정 자체(구간·히스테리시스)는 `test_nutrition_eval.py` 의 Task 6 부분(§6)이 본다.
"""

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agents.food.context import FoodContext
from app.agents.food.nutrition_eval import daily_totals, evaluate_nutrient_bands
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


# ═══════════════════════════════════════════════════════════════════════
# Task 6 — 구간 판정 (evaluate_nutrient_bands)
# ═══════════════════════════════════════════════════════════════════════
# "오늘" = NOW(2026-10-02) → 판정 창(최근 7일) = 09-25 ~ 10-01, 다시 판정 창 = 09-18 ~ 09-24.
# 유아 1인분 보정 계수(1-2y) = 0.1872 가 모든 섭취량 계산에 끼어든다.
_WINDOW_END = date(2026, 10, 1)  # today - 1
_CALCIUM_MENU = "칼슘식품"
_FILLER_MENU = "밥"


def _nutrient_row(menu_key: str, **nutrients: float) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=menu_key,
        display_name=menu_key,
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=1000.0,
        nutrients=dict(nutrients),
        food_groups=frozenset({"우유유제품"}),
    )


def _filler_row() -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=_FILLER_MENU,
        display_name=_FILLER_MENU,
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=200.0,
        nutrients={
            "protein": 3.0,
            "fat": 0.5,
            "carbohydrate": 28.0,
            "calcium": 3.0,
            "iron": 0.2,
            "sodium": 2.0,
            "energy": 130.0,
        },
        food_groups=frozenset({"곡류"}),
    )


def _daily_rows(factor_for_offset) -> list[DaycareMealRow]:
    """14일치(오늘 이전) 급식 행. 매일 `_CALCIUM_MENU`(변동 양) + `_FILLER_MENU`(고정 1.0)."""
    rows: list[DaycareMealRow] = []
    for offset in range(14):  # 0 = 오늘-1(최신) … 13 = 오늘-14(최고령)
        day = _WINDOW_END - timedelta(days=offset)
        rows.append(
            DaycareMealRow(
                id=uuid4(),
                child_id=CHILD,
                serve_date=day,
                meal_slot="lunch",
                menu_keys=(_CALCIUM_MENU,),
                amount_factor=factor_for_offset(offset),
                amount_known=True,
            )
        )
        rows.append(
            DaycareMealRow(
                id=uuid4(),
                child_id=CHILD,
                serve_date=day,
                meal_slot="snack_am",
                menu_keys=(_FILLER_MENU,),
                amount_factor=1.0,
                amount_known=True,
            )
        )
    return rows


def _band_context(*, calcium_row: MenuCatalogRow, factor_for_offset) -> FoodContext:
    rows = _daily_rows(factor_for_offset)
    return _context(daycare_rows=rows, catalog_rows=[calcium_row, _filler_row()])


@pytest.fixture
def context_8rows() -> FoodContext:
    # 최근 7일에 8행 — MIN_RECORDS(10) 미만
    rows = [
        DaycareMealRow(
            id=uuid4(),
            child_id=CHILD,
            serve_date=_WINDOW_END - timedelta(days=offset),
            meal_slot="lunch",
            menu_keys=(_CALCIUM_MENU,),
            amount_factor=1.0,
            amount_known=True,
        )
        for offset in range(8)
    ]
    return _context(
        daycare_rows=rows,
        catalog_rows=[_nutrient_row(_CALCIUM_MENU, protein=10.0, calcium=200.0, energy=100.0)],
    )


@pytest.fixture
def context_boundary() -> FoodContext:
    # calcium EAR(1-2y)=400mg. per_100g=200·serving_g=1000·scale=0.1872 이므로
    #   factor=0.5 → 187.2mg/일(뚜렷한 low) · factor=1.15 → 430.56mg/일(EAR~1.1*EAR 사이)
    # 오래된 6일(187.2mg)로 low 를 먼저 세운 뒤, 최근 8일(430.56mg)이 raw EAR 은 넘지만
    # 히스테리시스 복귀선(440mg)은 못 넘어 low 로 남는다
    calcium_row = _nutrient_row(
        _CALCIUM_MENU,
        protein=5.0,
        fat=2.0,
        carbohydrate=20.0,
        calcium=200.0,
        iron=1.0,
        sodium=50.0,
        energy=150.0,
    )

    def factor_for_offset(offset: int) -> float:
        return 1.15 if offset < 8 else 0.5  # offset 0~7 = 최근 8일, 8~13 = 오래된 6일

    return _band_context(calcium_row=calcium_row, factor_for_offset=factor_for_offset)


@pytest.fixture
def context_between_ear_and_rni() -> FoodContext:
    # 매일 430.56mg — EAR(400) 은 넘지만 RNI(450) 에는 못 미친다
    calcium_row = _nutrient_row(
        _CALCIUM_MENU,
        protein=5.0,
        fat=2.0,
        carbohydrate=20.0,
        calcium=200.0,
        iron=1.0,
        sodium=50.0,
        energy=150.0,
    )
    return _band_context(calcium_row=calcium_row, factor_for_offset=lambda _offset: 1.15)


@pytest.fixture
def context_high_micro() -> FoodContext:
    # 철 UL(1-2y)=40mg 을 훨씬 넘기는 양 — 그래도 high 로 보고하지 않는다(미량영양소는 상한 미보고)
    calcium_row = _nutrient_row(
        _CALCIUM_MENU,
        protein=5.0,
        fat=2.0,
        carbohydrate=20.0,
        calcium=200.0,
        iron=500.0,
        sodium=50.0,
        energy=150.0,
    )
    return _band_context(calcium_row=calcium_row, factor_for_offset=lambda _offset: 1.0)


@pytest.fixture
def context_missing_sugars() -> FoodContext:
    # 당류 칸 자체가 없다 — 모든 날에 걸쳐 빠져 있어 그 기간 전부가 unscored 다
    calcium_row = _nutrient_row(
        _CALCIUM_MENU,
        protein=5.0,
        fat=2.0,
        carbohydrate=20.0,
        calcium=200.0,
        iron=1.0,
        sodium=50.0,
        energy=150.0,
    )
    return _band_context(calcium_row=calcium_row, factor_for_offset=lambda _offset: 1.15)


@pytest.fixture
def context_sugars_partially_known() -> FoodContext:
    # 당류 칸이 있는 메뉴(_CALCIUM_MENU)와 없는 메뉴(_filler_row, 당류 칸 자체가 없다)가 매일
    # 섞인다 — "모든 날에 걸쳐 빠진" context_missing_sugars 와 다르다. 아는 메뉴만으로 합계를
    # 내면 모르는 메뉴의 당류를 0 취급하는 것과 같은 효과라 거짓으로 낮은 평균이 나온다
    calcium_row = _nutrient_row(
        _CALCIUM_MENU,
        protein=5.0,
        fat=2.0,
        carbohydrate=20.0,
        calcium=200.0,
        iron=1.0,
        sodium=50.0,
        energy=150.0,
        sugars=80.0,
    )
    return _band_context(calcium_row=calcium_row, factor_for_offset=lambda _offset: 1.15)


@pytest.fixture
def context_mostly_unweighed() -> FoodContext:
    # 그날 메뉴 3개 중 1개만 1인분 중량이 있다 — 합계에 들어간 메뉴 비율이 50% 미만("정하는 것 ③")
    weighed = _nutrient_row(_CALCIUM_MENU, protein=10.0, calcium=200.0, energy=100.0)
    unweighed_a = MenuCatalogRow(
        menu_key="무게모름A",
        display_name="무게모름A",
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=None,
        nutrients={},
        food_groups=frozenset({"채소"}),
    )
    unweighed_b = MenuCatalogRow(
        menu_key="무게모름B",
        display_name="무게모름B",
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=None,
        nutrients={},
        food_groups=frozenset({"과일"}),
    )
    rows: list[DaycareMealRow] = []
    for offset in range(7):  # 최근 7일(판정 창)만 있으면 충분하다
        day = _WINDOW_END - timedelta(days=offset)
        for menu_key in (_CALCIUM_MENU, "무게모름A", "무게모름B"):
            rows.append(
                DaycareMealRow(
                    id=uuid4(),
                    child_id=CHILD,
                    serve_date=day,
                    meal_slot="lunch" if menu_key == _CALCIUM_MENU else "snack_am",
                    menu_keys=(menu_key,),
                    amount_factor=1.0,
                    amount_known=True,
                )
            )
    return _context(daycare_rows=rows, catalog_rows=[weighed, unweighed_a, unweighed_b])


async def test_7일_8행이면_수치_결론을_내지_않는다(context_8rows) -> None:
    verdict = await evaluate_nutrient_bands(context_8rows, months=30)

    assert verdict.insufficient is True
    assert verdict.bands == {}
    # insufficient 여도 run state 에는 담아 둔다 — 같은 run 의 다른 곳이 다시 읽을 수 있다
    assert context_8rows.state.nutrient_verdict == verdict


async def test_경계에서_구간이_매일_뒤집히지_않는다(context_boundary) -> None:
    # 히스테리시스 — 한 번 low 면 EAR 의 1.1 배를 넘어야 ok (설계 §6)
    verdict = await evaluate_nutrient_bands(context_boundary, months=30)

    assert verdict.bands["calcium"] == "low"


async def test_EAR_을_쓴다_RNI_가_아니다(context_between_ear_and_rni) -> None:
    # 같은 섭취량에서 RNI 기준이면 low, EAR 기준이면 ok
    verdict = await evaluate_nutrient_bands(context_between_ear_and_rni, months=30)

    assert verdict.bands["calcium"] == "ok"


async def test_미량영양소_상한_초과를_high_로_보고하지_않는다(context_high_micro) -> None:
    verdict = await evaluate_nutrient_bands(context_high_micro, months=30)

    assert verdict.bands.get("iron") != "high"


async def test_당류가_빈_메뉴가_섞이면_그_항목만_구간을_내지_않는다(context_missing_sugars) -> None:
    # 리뷰 초점 2 — 0 으로 채우면 거짓 high 가 난다 (설계 §3)
    verdict = await evaluate_nutrient_bands(context_missing_sugars, months=30)

    assert "sugars" not in verdict.bands
    assert "sugars" in verdict.unscored
    assert "calcium" in verdict.bands  # 다른 항목은 그대로 판정한다


async def test_당류가_있는_메뉴와_없는_메뉴가_섞이면_그_항목만_구간을_내지_않는다(
    context_sugars_partially_known,
) -> None:
    # 리뷰 초점 2 — "아예 빠짐"(위 테스트)과 다른 경우. 그날 두 메뉴 중 하나만 당류를 알면
    # 아는 메뉴만으로 하루 합계를 내서는 안 된다 — 모르는 메뉴를 0 으로 채우는 것과 같아진다
    verdict = await evaluate_nutrient_bands(context_sugars_partially_known, months=30)

    assert "sugars" not in verdict.bands
    assert "sugars" in verdict.unscored
    assert "calcium" in verdict.bands  # 다른 항목은 그대로 판정한다


async def test_합계에_들어간_메뉴가_모자라면_수치_결론을_막는다(context_mostly_unweighed) -> None:
    # "정하는 것 ③" 의 임계
    verdict = await evaluate_nutrient_bands(context_mostly_unweighed, months=30)

    assert verdict.insufficient is True


async def test_판정이_끝난_뒤_DB_와_로그에_합계가_남지_않는다(context, caplog) -> None:
    # 리뷰 초점 4 (설계 §3 · 루트 CLAUDE.md §2 개인정보) — 영양 합계를 저장하는 포트 자체가 없다
    # (test_food_store.py 의 test_영양_구간을_저장하는_포트가_없다 가 구조로 고정한다).
    # 여기서는 로그에 수치가 남지 않는지만 본다
    await evaluate_nutrient_bands(context, months=30)

    assert not [r for r in caplog.records if "protein" in r.getMessage()]


async def test_돌_전에는_판정하지_않는다(context) -> None:
    with pytest.raises(ValueError, match="섭취기준 연령군"):
        await evaluate_nutrient_bands(context, months=9)
