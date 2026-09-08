"""app/rules/allergen.py — 급식표 메뉴 원문에서 알레르기 번호를 뽑는 규칙.

순수 함수만 검증한다. LLM·DB 없음.
"""

import pytest

from app.rules.allergen import ALLERGEN_NAMES, parse_allergens


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
