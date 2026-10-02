"""app/rules/term_match.py — Food·Activity 가 함께 쓰는 공통 용어 매처.

순수 함수만 검증한다. LLM·DB 없음.
"""

import pytest

from app.rules.term_match import Term, match_terms, normalize

EGG = Term(key="1", aliases=("난류", "알류", "계란", "달걀"))
WHEAT = Term(key="6", aliases=("밀", "밀가루"), guards=("밀크", "밀키", "밀감"))
TERMS = (EGG, WHEAT)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("계란말이", ("1",)),
        ("달 걀 찜", ("1",)),
        ("밀크티", ()),
        ("밀크티와 통밀빵", ("6",)),  # 통밀의 밀은 guard 가 덮지 않는다
        ("", ()),
    ],
)
def test_match_terms_table(text: str, expected: tuple[str, ...]) -> None:
    assert match_terms(text, TERMS) == expected


def test_normalize_strips_all_whitespace_and_lowercases() -> None:
    assert normalize("A  b\tc\n한 글") == "abc한글"


def test_match_terms_returns_keys_in_terms_order() -> None:
    result = match_terms("계란과 밀가루", TERMS)

    assert result == ("1", "6")


def test_guard_only_cancels_the_span_it_covers() -> None:
    # "밀감" 앞의 "밀"은 guard가 덮지만, 뒤에 이어지는 "밀가루"의 "밀"은 다른 guard가 없다.
    term = Term(key="6", aliases=("밀",), guards=("밀감",))

    assert match_terms("밀감밀가루", (term,)) == ("6",)


def test_no_hit_returns_empty_tuple() -> None:
    assert match_terms("현미밥과 미역국", TERMS) == ()


def test_empty_alias_and_guard_are_ignored() -> None:
    term = Term(key="x", aliases=("", "새우"), guards=("",))

    assert match_terms("새우볶음밥", (term,)) == ("x",)
