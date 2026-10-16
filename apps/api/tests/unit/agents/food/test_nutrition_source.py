"""영양DB 행 고르는 규칙 — `docs/agents/shared/외부연결_계획.md` §2.

같은 이름이 여러 행이다(된장찌개 12 · 김치찌개 25). 아래 이름 · 값은 전부 가짜다.
원문을 옮기지 않았다.
"""

import pytest

from app.agents.food.nutrition_source import (
    parse_grams,
    parse_nutrient,
    pick_nutrition,
    pick_nutrition_row,
)
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, OPTIONAL_NUTRIENT_KEYS, RawNutriRow

_FULL = {key: "10" for key in CORE_NUTRIENT_KEYS}


def row(
    name: str = "된장찌개",
    *,
    code: str = "D1",
    group: str = "음식",
    cls: str = "품목대표",
    researched: str | None = "20200101",
    weight: str | None = "300g",
    nutrients: dict[str, str | None] | None = None,
    source: str | None = "출처A",
) -> RawNutriRow:
    return RawNutriRow(
        food_code=code,
        name=name,
        db_group=group,
        db_class=cls,
        category="분류",
        source=source,
        researched_on=researched,
        food_weight=weight,
        nutrients=_FULL if nutrients is None else nutrients,
    )


# ── 이름이 정확히 같은 행만 ───────────────────────────────────────────
def test_이름이_정확히_같은_행을_고른다() -> None:
    picked = pick_nutrition_row([row("된장찌개", code="A")], "된장찌개")

    assert picked is not None
    assert picked.food_code == "A"


def test_이름이_다른_결과는_받지_않는다() -> None:
    # 된장찌개 → 된장찌개_두부 · 부대된장찌개 로 추정하지 않는다. 알레르기를 놓치는 쪽으로 틀린다
    rows = [row("된장찌개_두부"), row("부대된장찌개"), row("된장찌개국")]

    assert pick_nutrition_row(rows, "된장찌개") is None


def test_공백과_문장부호만_다른_이름은_같은_이름이다() -> None:
    assert pick_nutrition_row([row("김치 볶음밥", code="K")], "김치볶음밥") is not None


def test_이름_뒤_괄호_속_말은_다른_변형이라_지우고_견주지_않는다() -> None:
    # 메뉴 쪽 괄호는 양 · 번호라 지우지만, 영양DB 쪽 괄호는 변형 이름일 수 있다
    assert pick_nutrition_row([row("김치찌개(참치)")], "김치찌개") is None


# ── 행 고르는 순서: 음식 → 원재료성 → 가공식품, 품목대표만 ──────────────
def test_음식_품목대표가_원재료성보다_먼저다() -> None:
    rows = [
        row("우유", code="RAW", group="원재료성"),
        row("우유", code="FOOD", group="음식"),
    ]

    assert pick_nutrition_row(rows, "우유").food_code == "FOOD"  # type: ignore[union-attr]


def test_원재료성_품목대표가_가공식품보다_먼저다() -> None:
    rows = [
        row("백김치", code="PROC", group="가공식품"),
        row("백김치", code="RAW", group="원재료성"),
    ]

    assert pick_nutrition_row(rows, "백김치").food_code == "RAW"  # type: ignore[union-attr]


def test_우유처럼_원재료성_그룹에만_있는_이름도_해석된다() -> None:
    # 실제 응답에서 원재료성 품목대표는 1인분 중량 칸이 비어 있다 — 중량이 없다고 떨어뜨리면
    # 우유 · 두유 같은 급식 단골이 영양 계산에서 빠진다
    assert pick_nutrition_row([row("우유", group="원재료성", weight=None)], "우유") is not None


def test_가공식품_품목대표도_다른_그룹에_없으면_받는다() -> None:
    # 백김치 · 동그랑땡은 가공식품 그룹에만 있었다
    assert pick_nutrition_row([row("동그랑땡", group="가공식품")], "동그랑땡") is not None


def test_상용제품은_어느_그룹에서든_메뉴명에_쓰지_않는다() -> None:
    # "백미밥" 은 즉석밥 제품 · "불고기" · "계란찜" 은 제품 행에만 걸렸고,
    # 칼슘 · 철이 빈 행도 그 행들이었다
    rows = [
        row("백미밥", group="가공식품", cls="상용제품"),
        row("백미밥", group="음식", cls="상용제품"),
        row("백미밥", group="원재료성", cls="상용제품"),
    ]

    assert pick_nutrition_row(rows, "백미밥") is None


def test_모르는_그룹과_모르는_분류는_받지_않는다() -> None:
    # 값이 다르게 오면 아무것도 고르지 않는다 — 해석 못 하는 쪽으로 틀린다(알레르기 "확인 못 함")
    assert pick_nutrition_row([row(group="외식")], "된장찌개") is None
    assert pick_nutrition_row([row(cls="대표")], "된장찌개") is None


# ── 영양 칸이 빈 행은 쓰지 않는다 ─────────────────────────────────────
@pytest.mark.parametrize("missing", CORE_NUTRIENT_KEYS)
def test_핵심_영양_칸이_하나라도_비면_쓰지_않는다(missing: str) -> None:
    # 0 으로 넣지 않는다 — 합계가 실제보다 작아져 거짓 low 가 된다
    nutrients = {**_FULL, missing: None}

    assert pick_nutrition_row([row(nutrients=nutrients)], "된장찌개") is None


@pytest.mark.parametrize("bad", ["", "-", "Tr", "tr", "n/a", "abc", "-5", "nan", "inf"])
def test_숫자로_읽을_수_없거나_음수인_칸은_빈_칸이다(bad: str) -> None:
    assert parse_nutrient(bad) is None
    assert pick_nutrition_row([row(nutrients={**_FULL, "calcium": bad})], "된장찌개") is None


def test_0은_빈_칸이_아니다() -> None:
    # 실제로 0 인 값(당류 0g)은 유효하다
    assert parse_nutrient("0") == 0.0
    assert pick_nutrition_row([row(nutrients={**_FULL, "sugars": "0"})], "된장찌개") is not None


def test_영양_칸이_빈_첫_행_대신_같은_순위의_찬_행을_고른다() -> None:
    rows = [
        row(code="EMPTY", nutrients={**_FULL, "iron": None}, researched="20240101"),
        row(code="FULL", researched="20200101"),
    ]

    assert pick_nutrition_row(rows, "된장찌개").food_code == "FULL"  # type: ignore[union-attr]


@pytest.mark.parametrize("weight", [None, "", "0g", "한 그릇", "3kg"])
def test_1인분_중량이_없거나_읽을_수_없어도_100g_기준_영양은_받는다(weight: str | None) -> None:
    # 원재료성 · 가공식품 품목대표는 거의 다 중량이 없다. 못 읽은 중량은 모르는 것(None)이지
    # 100g 이나 0 이 아니다 — 양을 정하는 일은 영양 계산이 따로 한다
    facts = pick_nutrition([row(weight=weight)], "된장찌개")

    assert facts is not None
    assert facts.serving_g is None
    assert facts.per_100g["calcium"] == 10.0


def test_식품코드가_없으면_쓰지_않는다() -> None:
    assert pick_nutrition_row([row(code="")], "된장찌개") is None


# ── 같은 순위가 여럿이면 최신 조사일, 그다음 식품코드 ──────────────────
def test_같은_순위면_조사일이_가장_최근인_행() -> None:
    rows = [
        row(code="OLD", researched="20180101"),
        row(code="NEW", researched="20230615"),
        row(code="MID", researched="20200101"),
    ]

    assert pick_nutrition_row(rows, "된장찌개").food_code == "NEW"  # type: ignore[union-attr]


def test_조사일_표기가_달라도_최근_순으로_견준다() -> None:
    rows = [
        row(code="DASH", researched="2023-06-15"),
        row(code="YM", researched="2023-01"),
        row(code="UNKNOWN", researched=None),
    ]

    assert pick_nutrition_row(rows, "된장찌개").food_code == "DASH"  # type: ignore[union-attr]


def test_조사일도_같으면_식품코드_순으로_정해_매번_같은_행이다() -> None:
    rows = [row(code="B"), row(code="A"), row(code="C")]

    first = pick_nutrition_row(rows, "된장찌개")
    again = pick_nutrition_row(list(reversed(rows)), "된장찌개")

    assert first is not None and again is not None
    assert first.food_code == again.food_code == "A"


def test_그룹_순위가_조사일보다_앞선다() -> None:
    rows = [
        row(code="FOOD_OLD", group="음식", researched="20100101"),
        row(code="RAW_NEW", group="원재료성", researched="20250101"),
    ]

    assert pick_nutrition_row(rows, "된장찌개").food_code == "FOOD_OLD"  # type: ignore[union-attr]


def test_행이_없으면_None() -> None:
    assert pick_nutrition_row([], "된장찌개") is None


# ── 숫자 읽기 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("300g", 300.0),
        ("300", 300.0),
        (" 12.5 g ", 12.5),
        ("200ml", 200.0),
        ("1,200g", 1200.0),
        ("200.000g", 200.0),  # 실제 응답 표기
        ("190.000mL", 190.0),  # 실제 응답 표기 — 마시는 것은 대문자 L
    ],
)
def test_1인분_중량_읽기(raw: str, expected: float) -> None:
    assert parse_grams(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "한 그릇", "abc", "-3g", "0", "3kg", "inf"])
def test_읽을_수_없는_1인분_중량은_None(raw: str | None) -> None:
    assert parse_grams(raw) is None


# ── 영양성분으로 옮기기 ───────────────────────────────────────────────
def test_고른_행은_100g_기준_영양성분으로_옮겨진다() -> None:
    nutrients = {key: str(index + 1) for index, key in enumerate(CORE_NUTRIENT_KEYS)}
    facts = pick_nutrition([row("우유", code="M1", nutrients=nutrients, weight="200ml")], "우유")

    assert facts is not None
    assert facts.food_code == "M1"
    assert facts.name == "우유"
    assert facts.serving_g == 200.0
    assert facts.per_100g == {key: float(index + 1) for index, key in enumerate(CORE_NUTRIENT_KEYS)}


def test_당류가_비어도_메뉴는_해석한다() -> None:
    # 실제 김치볶음밥(가공식품 품목대표)은 당류 칸만 비어 있다. 메뉴를 떨어뜨리면 그 메뉴의 칼슘 ·
    # 단백질까지 합계에서 빠져 거짓 low 가 난다(10-11 결정 — 탈락시키지 않고 안내만 낸다)
    facts = pick_nutrition([row("김치볶음밥", nutrients={**_FULL, "sugars": None})], "김치볶음밥")

    assert facts is not None
    assert "sugars" not in facts.per_100g  # 0 으로 채우지 않는다 — 당류를 모르는 메뉴다
    assert facts.per_100g["calcium"] == 10.0


def test_당류는_핵심_칸이_아니다() -> None:
    assert "sugars" not in CORE_NUTRIENT_KEYS
    assert "sugars" in OPTIONAL_NUTRIENT_KEYS


def test_포화지방은_있을_때만_싣는다() -> None:
    with_sat = pick_nutrition([row(nutrients={**_FULL, "saturated_fat": "1.5"})], "된장찌개")
    without = pick_nutrition([row(nutrients={**_FULL, "saturated_fat": None})], "된장찌개")

    assert with_sat is not None and with_sat.per_100g["saturated_fat"] == 1.5
    # 포화지방이 비어도 행은 쓴다. 없는 칸을 0 으로 채우지 않는다
    assert without is not None and "saturated_fat" not in without.per_100g


def test_못_고르면_영양성분도_None() -> None:
    assert pick_nutrition([row("된장찌개_두부")], "된장찌개") is None
