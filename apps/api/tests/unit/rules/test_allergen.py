"""app/rules/allergen.py — 급식표 메뉴 원문에서 알레르기 번호를 뽑는 규칙.

순수 함수만 검증한다. LLM·DB 없음.
"""

import json
from pathlib import Path

import pytest

from app.rules.allergen import ALLERGEN_NAMES, parse_allergens

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meal_plan_2025_01.json"


def test_parses_parenthesized_comma_list():
    result = parse_allergens("쇠고기무국 (1,5,6,16)")

    assert result.codes == (1, 5, 6, 16)
    assert result.unknown == ()


def test_parses_circled_numbers():
    result = parse_allergens("돈까스 ①②⑤⑬")

    assert result.codes == (1, 2, 5, 13)
    assert result.unknown == ()


@pytest.mark.parametrize("raw", ["차수수밥", ""])
def test_no_notation_yields_empty(raw):
    result = parse_allergens(raw)

    assert result.codes == ()
    assert result.unknown == ()


@pytest.mark.parametrize(
    ("raw", "codes", "unknown"),
    [
        ("(1,5,20)", (1, 5), (20,)),
        ("(0)", (), (0,)),
        ("⑳", (), (20,)),
        ("(2026)", (), (2026,)),
    ],
)
def test_out_of_range_numbers_are_kept_separately(raw, codes, unknown):
    result = parse_allergens(raw)

    assert result.codes == codes
    assert result.unknown == unknown


def test_mixed_notations_are_unioned():
    assert parse_allergens("돈까스 (1,2) ⑤").codes == (1, 2, 5)


def test_deduplicates_and_sorts():
    assert parse_allergens("(16,1,1) ①").codes == (1, 16)


@pytest.mark.parametrize(
    ("raw", "codes"),
    [
        ("3색나물", ()),
        ("1인분 (5)", (5,)),
    ],
)
def test_ignores_numbers_outside_parentheses(raw, codes):
    assert parse_allergens(raw).codes == codes


@pytest.mark.parametrize(
    "raw",
    [
        "（1, 5）",
        "( 1 , 5 )",
        "(1.5)",
        "(1.5.)",
    ],
)
def test_accepts_fullwidth_parens_spaces_and_dot_separators(raw):
    assert parse_allergens(raw).codes == (1, 5)


@pytest.mark.parametrize(
    ("raw", "codes"),
    [
        ("(중)", ()),
        ("(1,새우)", (1,)),
    ],
)
def test_ignores_non_numeric_tokens_in_parentheses(raw, codes):
    assert parse_allergens(raw).codes == codes


def test_multiple_parenthesis_groups_are_merged():
    assert parse_allergens("(1)(5)").codes == (1, 5)


def test_missing_closing_paren_still_parses():
    assert parse_allergens("쇠고기무국 (1,5,6,16").codes == (1, 5, 6, 16)


def test_allergen_names_cover_exactly_nineteen():
    assert sorted(ALLERGEN_NAMES) == list(range(1, 20))


def test_same_input_gives_same_result():
    raw = "돈까스 (1,2,5,6,10,15)"

    assert parse_allergens(raw) == parse_allergens(raw)


# ── 실제 급식표(성남시 센터 2025-01)에서 확인된 표기 ─────────────────────────


@pytest.mark.parametrize(
    ("raw", "codes"),
    [
        ("모듬버섯된장국5,6", (5, 6)),
        ("닭곰탕15", (15,)),
        ("돼지고기하이라이스2,5,6,10,12,15,16,18", (2, 5, 6, 10, 12, 15, 16, 18)),
        ("경기도과일 또는 고구마우유죽2", (2,)),
        ("모듬버섯된장국 5,6", (5, 6)),
    ],
)
def test_parses_numbers_appended_to_menu_name(raw, codes):
    assert parse_allergens(raw).codes == codes


@pytest.mark.parametrize(
    ("raw", "codes"),
    [
        ("연근조림★5,6", (5, 6)),
        ("쇠고기무밥&양념장5,6,16", (5, 6, 16)),
        ("빵(식빵2,5,6&잼)", (2, 5, 6)),
        ("빵(통밀빵1,2,5,6&잼)", (1, 2, 5, 6)),
        ("케이크1,2,5,6(떡)", (1, 2, 5, 6)),
    ],
)
def test_parses_numbers_next_to_symbols(raw, codes):
    assert parse_allergens(raw).codes == codes


@pytest.mark.parametrize("raw", ["백미밥1/2", "472/20", "1.5배", "5곡밥", "만 1~2세(13~35개월)"])
def test_fractions_and_counts_are_not_allergens(raw):
    result = parse_allergens(raw)

    assert result.codes == ()
    assert result.unknown == ()


def _fixture_raws() -> list[str]:
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [it["raw"] for d in plan["days"] for m in d["meals"] for it in m["items"]]


def test_real_meal_plan_yields_no_out_of_range_numbers():
    bad = {raw: r.unknown for raw in _fixture_raws() if (r := parse_allergens(raw)).unknown}

    assert bad == {}


def test_real_meal_plan_numbers_are_all_parsed():
    not_allergen = {"백미밥1/2"}
    missed = [
        raw
        for raw in _fixture_raws()
        if any(ch.isdigit() for ch in raw)
        and raw not in not_allergen
        and not parse_allergens(raw).codes
    ]

    assert missed == []
