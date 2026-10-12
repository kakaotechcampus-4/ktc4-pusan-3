"""app/agents/common/allergy.py — 보호자가 적은 이름을 막을 것으로 읽는 공통 규칙 (#258).

health_safety.label 은 자유 입력이라 한 칸에 여러 이름 · 뒤에 붙인 말 · 영어가 섞여 온다.
이름 하나가 정확히 별칭과 같을 때만 읽으면 나머지가 조용히 통과한다.
"""

import unicodedata

import pytest

from app.agents.common.allergy import (
    Restriction,
    make_book,
    read_label,
    shared_book,
    shared_name_rules,
    split_label,
)
from app.rules.term_match import Term

MILK = Restriction(codes=frozenset({2}))
PEANUT = Restriction(codes=frozenset({4}))
KIWI = Term(key="키위", aliases=("키위", "참다래"))
BOOK = make_book(
    [
        ("우유", MILK, ()),
        ("땅콩", PEANUT, ("땅콩호박",)),
        ("키위", Restriction(terms=(KIWI,)), ()),
    ]
)


@pytest.mark.parametrize(
    ("label", "pieces"),
    [
        ("우유", ("우유",)),
        ("우유 알레르기", ("우유",)),
        ("우유알러지 있음", ("우유",)),
        ("땅콩 알레르기 의심", ("땅콩",)),
        ("우유(유제품)", ("우유", "유제품")),
        ("우유（유제품）", ("우유", "유제품")),
        ("우유, 땅콩 / 계란", ("우유", "땅콩", "계란")),
        ("우유 및 계란", ("우유", "계란")),
        ("Egg white allergy", ("eggwhite",)),
        ("달 걀", ("달걀",)),
        ("우유, 우유", ("우유",)),
        ("알레르기", ("알레르기",)),
        # 가운뎃점 — 천지인 자판의 ㆍ(NFKC 뒤 U+119E) · 글머리 기호 · 일본식 가운뎃점
        ("우유\u318d땅콩", ("우유", "땅콩")),
        ("우유\u2022땅콩", ("우유", "땅콩")),
        ("우유\u30fb땅콩", ("우유", "땅콩")),
        (" , ", ()),
    ],
)
def test_split_label(label: str, pieces: tuple[str, ...]) -> None:
    assert split_label(label) == pieces


def test_read_label_exact_name_has_no_rest() -> None:
    assert read_label("우유 알레르기", BOOK) == (MILK, ())


def test_read_label_finds_names_inside_a_piece_and_keeps_the_piece() -> None:
    found, rest = read_label("우유단백질", BOOK)

    assert found == MILK
    assert rest == ("우유단백질",)


def test_read_label_respects_guards_when_searching_inside() -> None:
    # 땅콩호박은 땅콩이 아니다 — 글자 그대로만 남는다
    assert read_label("땅콩호박", BOOK) == (Restriction(), ("땅콩호박",))


def test_read_label_unknown_name_is_left_for_the_caller() -> None:
    assert read_label("망고", BOOK) == (Restriction(), ("망고",))


def test_read_label_guard_does_not_join_two_names_written_with_a_space() -> None:
    # "땅콩 호박" 은 땅콩과 호박을 띄어 쓴 것일 수 있다.
    # guard "땅콩호박" 이 둘을 이어 덮지 않는다
    found, rest = read_label("땅콩 호박", BOOK)

    assert found == PEANUT
    assert "호박" in rest


def test_read_label_blocks_each_unknown_word_written_with_spaces() -> None:
    found, rest = read_label("우유 망고 아보카도", BOOK)

    assert found == MILK
    assert {"망고", "아보카도"} <= set(rest)


def test_read_label_skips_filler_words_between_names() -> None:
    _, rest = read_label("우유 등 약간 심함", BOOK)

    assert not {"등", "약간", "심함"} & set(rest)


@pytest.mark.parametrize(
    "label",
    [
        "아보카도랑 망고",
        "아보카도와 망고",
        "아보카도하고 망고",
        "아보카도에 알레르기 있음",
        "아보카도랑",
    ],
)
def test_read_label_strips_particles_after_unknown_names(label: str) -> None:
    # 사전에 없는 이름에 조사가 붙으면 그 글자로는 메뉴에 안 걸린다 — 조사를 뗀 이름도 막는다
    _, rest = read_label(label, BOOK)

    assert "아보카도" in rest


def test_read_label_particle_after_a_known_name_reads_the_name() -> None:
    found, rest = read_label("땅콩이랑 키위", BOOK)

    assert found == PEANUT | Restriction(terms=(KIWI,))
    assert "땅콩이랑" not in rest


@pytest.mark.parametrize(
    ("label", "kept", "not_kept"),
    [
        ("귤이랑 키위", "귤", None),  # 받침 있는 한 글자 이름은 남긴다
        ("다과", None, "다"),  # "과" 앞에 받침이 없다 — 조사가 아니다
        ("누가", None, "누"),  # 받침 없는 한 글자는 남기지 않는다(누가 · 사랑)
    ],
)
def test_read_label_particle_stem_rules(label: str, kept: str | None, not_kept: str | None) -> None:
    _, rest = read_label(label, BOOK)

    if kept:
        assert kept in rest
    if not_kept:
        assert not_kept not in rest


def test_read_label_ignores_number_only_pieces() -> None:
    # "우유(2)" 의 2 는 급식표의 알레르기 번호다. 글자 "2" 로 막으면 숫자가 든 메뉴가 다 막힌다
    assert read_label("우유(2)", BOOK) == (MILK, ())


@pytest.mark.parametrize("label", ["우유 알레르기가 있어요", "우유 전체", "우유 그리고 땅콩"])
def test_read_label_skips_words_that_are_not_names(label: str) -> None:
    _, rest = read_label(label, BOOK)

    assert not {"알레르기가", "알레르기", "있어요", "전체", "그리고"} & set(rest)


def test_read_label_ignores_punctuation_around_a_name() -> None:
    # 메뉴를 훑을 때처럼 이름을 견줄 때도 문장부호 · 기호는 글자가 아니다
    assert read_label("우유.", BOOK) == (MILK, ())
    assert read_label("'우유'!", BOOK) == (MILK, ())


def test_read_label_finds_a_spaced_name_written_with_a_hyphen_inside_a_piece() -> None:
    pine = Restriction(codes=frozenset({19}))
    book = make_book([("pine nut", pine, ())])

    assert read_label("pine-nut 쿠키", book)[0] == pine


def test_read_label_name_joined_with_punctuation_is_also_read_word_by_word() -> None:
    # "땅콩-버터" 가 땅콩버터 하나인지 땅콩과 버터 둘인지 모른다 — 둘 다 막는다
    book = make_book([("땅콩버터", PEANUT, ()), ("땅콩", PEANUT, ()), ("버터", MILK, ())])

    assert read_label("땅콩-버터", book)[0] == PEANUT | MILK


@pytest.mark.parametrize("label", ["아보카도-리치", "아보카도~리치", "아보카도.리치"])
def test_read_label_splits_unknown_names_joined_with_punctuation(label: str) -> None:
    _, rest = read_label(label, BOOK)

    assert {"아보카도", "리치"} <= set(rest)


@pytest.mark.parametrize("label", ["우유 h", "Food allergy", "우유 and 땅콩"])
def test_read_label_does_not_block_letters_or_words_that_are_not_names(label: str) -> None:
    _, rest = read_label(label, BOOK)

    assert not {"h", "food", "and"} & set(rest)


def test_read_label_does_not_split_off_single_latin_letters() -> None:
    _, rest = read_label("Cow's milk", shared_book())

    assert "s" not in rest


def test_read_label_folds_nfd_hangul() -> None:
    assert read_label(unicodedata.normalize("NFD", "우유"), BOOK) == (MILK, ())


def test_make_book_merges_the_same_name() -> None:
    book = make_book([("콩", Restriction(codes=frozenset({5})), ("강낭콩",)), ("콩", MILK, ())])

    assert book.names["콩"] == Restriction(codes=frozenset({2, 5}))
    assert book.scan_terms == (Term(key="콩", aliases=("콩",), guards=("강낭콩",)),)


def test_shared_book_reads_group_names_without_food_words() -> None:
    # Activity 처럼 음식 어휘를 더하지 않아도 묶음은 코드와 묶음 이름 글자 그대로를 막는다
    crustacean = Term(key="갑각류", aliases=("갑각류", "crustacean", "crustaceans"))

    assert shared_book().names["갑각류"] == Restriction(
        codes=frozenset({8, 9}), terms=(crustacean,)
    )
    assert read_label("갑각류 알레르기", shared_book())[0].codes == {8, 9}


def test_group_name_also_blocks_its_own_words_for_every_member() -> None:
    # "견과류" · "해물" 이 레시피 재료에 그대로 나온다. develop 은 글자로 막았다
    seafood = shared_book().names["해산물"].terms

    assert {term.key for term in seafood} == {"해산물", "생선", "갑각류", "연체류"}
    meat = next(term for term in shared_book().names["육류"].terms if term.key == "육류")
    assert "물고기" in meat.guards


def test_shared_name_rules_attach_group_foods_for_every_member() -> None:
    lobster = Term(key="갑각류", aliases=("랍스터",))
    sea = Term(key="해산물", aliases=("해삼",))

    book = make_book(shared_name_rules({"갑각류": lobster, "해산물": sea}))

    assert lobster in book.names["갑각류"].terms
    assert {sea, lobster} <= set(book.names["해산물"].terms)
