"""app/rules/term_match.py — Food·Activity 가 함께 쓰는 공통 용어 매처.

순수 함수만 검증한다. LLM·DB 없음.
"""

import unicodedata

import pytest

from app.rules.term_match import (
    Term,
    TermMatcher,
    match_fields,
    match_terms,
    normalize,
    prepare_fields,
    split_words,
)

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


def test_normalize_drops_punctuation_and_symbols() -> None:
    # 메뉴를 훑을 때처럼 문장부호 · 기호는 글자가 아니다 — "pine-nut" 은 "pine nut" 과 같은 이름이다
    assert normalize("Pine-Nut") == normalize("pine nut") == "pinenut"
    assert normalize("'우유'!") == "우유"
    assert normalize("우유★") == "우유"


def test_split_words_breaks_on_spaces_and_punctuation() -> None:
    assert split_words("아보카도-리치 / Cow's milk") == ["아보카도", "리치", "cow", "s", "milk"]
    assert split_words("우\u200b유 푸딩") == ["우유", "푸딩"]


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


def test_normalize_folds_nfd_hangul_and_fullwidth_latin() -> None:
    # 자모로 풀린 한글(NFD)과 전각 영문은 눈에는 같아도 그대로는 별칭과 맞지 않는다
    assert normalize(unicodedata.normalize("NFD", "우 유")) == "우유"
    assert normalize("ｍｉｌｋ") == "milk"


# ── match_fields — 필드마다 따로, guard 는 단어 경계를 넘지 않는다 (#258) ─────────


def test_match_fields_guard_does_not_cover_across_fields() -> None:
    # 이어 붙이면 "통밀크래커통밀크림치즈" — 모든 "밀" 뒤에 "크" 가 와서 guard "밀크" 가 다 덮는다
    assert match_terms("통밀 크래커 통밀 크림치즈", TERMS) == ()
    assert match_fields(("통밀 크래커", "통밀", "크림치즈"), TERMS) == ("6",)


def test_match_fields_guard_does_not_cover_across_a_space_in_one_field() -> None:
    assert match_fields(("통밀 크래커",), TERMS) == ("6",)
    assert match_fields(("밀, 크림",), TERMS) == ("6",)


def test_match_fields_guard_still_cancels_inside_a_word() -> None:
    assert match_fields(("밀크티", "홍차", "밀크 티"), TERMS) == ()


def test_match_fields_guard_written_with_a_space_covers_both_spellings() -> None:
    gluten = Term(key="6", aliases=("글루텐",), guards=("글루텐 프리",))

    assert match_fields(("글루텐 프리 쿠키",), (gluten,)) == ()
    assert match_fields(("글루텐프리 쿠키",), (gluten,)) == ()
    assert match_fields(("밀 글루텐",), (gluten,)) == ("6",)


def test_match_fields_alias_spans_spaces_inside_a_field() -> None:
    pine = Term(key="19", aliases=("pine nut",))

    assert match_fields(("달 걀 찜",), TERMS) == ("1",)
    assert match_fields(("pine-nut salad",), (pine,)) == ("19",)


def test_match_fields_alias_does_not_span_punctuation_inside_a_field() -> None:
    # 문장부호는 재료를 가르는 자리다 — "소스 : 시금치" 에 "스시" 가 있는 게 아니다
    raw_fish = Term(key="날음식", aliases=("스시",))
    milk = Term(key="2", aliases=("우유",))

    assert match_fields(("시금치우유 소스 : 시금치",), (raw_fish,)) == ()
    assert match_fields(("우.유 푸딩",), (milk,)) == ()


@pytest.mark.parametrize("mark", ["\u200b", "\u00ad", "\u200d", "\ufeff"])
def test_invisible_format_characters_are_not_breaks(mark: str) -> None:
    # 폭 없는 공백 · soft hyphen 은 보이지 않는 글자다 — 문장부호처럼 별칭을 끊으면 "우유" 가 빠진다
    milk = Term(key="2", aliases=("우유",))

    assert normalize(f"우{mark}유") == "우유"
    assert match_fields((f"우{mark}유 푸딩",), (milk,)) == ("2",)


def test_match_fields_alias_split_across_fields_is_not_a_hit() -> None:
    # 어느 필드에도 혼자 없는 별칭은 없는 재료다 — Activity 의 "작은" + "블록 담는 통" 과 같은 규칙
    assert match_fields(("계", "란"), TERMS) == ()


def test_match_fields_returns_keys_in_terms_order() -> None:
    assert match_fields(("밀가루", "계란"), TERMS) == ("1", "6")


def test_match_fields_with_no_text_returns_empty_tuple() -> None:
    assert match_fields((), TERMS) == ()
    assert match_fields(("", "  ", "!!"), TERMS) == ()


def test_term_matcher_reuses_one_index_for_many_rows() -> None:
    matcher = TermMatcher(TERMS)

    assert matcher.match(prepare_fields(("계란말이",))) == ("1",)
    assert matcher.match(prepare_fields(("밀크티",))) == ()
    assert matcher.match(prepare_fields(("통밀빵", "달걀"))) == ("1", "6")
