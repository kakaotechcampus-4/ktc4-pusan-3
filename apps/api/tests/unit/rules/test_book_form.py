"""도서 책 형태(`form`)와 월령 범위 — GT-3 처음 값 (growth_agent_own_table.md §7)

지키는 것 네 가지.
- 아동 도서(ISBN 부가기호 첫 자리 7)가 아니면 후보에서 뺀다. 부가기호가 없는 책은 `unknown`
- 서명 키워드가 있으면 `board`, KDC 대분류 8(문학)이면 `picture`, 그 밖의 KDC 면 `info`
- 월령 범위는 form 하나로 정해진다 — 8개월 아이에게는 `board` 만 나간다
- `unknown` 은 0–11개월에서 제외한다. 보드북인지 모르면 영아에게 내지 않는 쪽이 안전하다
"""

import pytest

from app.rules.book_form import BOOK_AGE_MONTHS, classify_book_form, fits_age

CHILD_SYMBOL = "77810"  # 독자대상 7 = 아동


class TestClassify:
    @pytest.mark.parametrize("keyword", ["보드북", "촉감책", "헝겊책", "사운드북", "초점책"])
    def test_title_keyword_is_board(self, keyword):
        form = classify_book_form(
            title=f"아기 {keyword} 동물 친구들", addition_symbol=CHILD_SYMBOL, class_no="813.8"
        )
        assert form == "board"

    def test_keyword_matches_across_spaces(self):
        # 서명에 띄어쓰기가 끼어도 같은 말이다
        form = classify_book_form(
            title="곰돌이 보드 북", addition_symbol=CHILD_SYMBOL, class_no="813.8"
        )
        assert form == "board"

    def test_literature_kdc_is_picture(self):
        form = classify_book_form(
            title="공룡이 나타났다", addition_symbol=CHILD_SYMBOL, class_no="813.8"
        )
        assert form == "picture"

    @pytest.mark.parametrize("class_no", ["404", "470", "911", "0"])
    def test_other_kdc_is_info(self, class_no):
        form = classify_book_form(
            title="공룡 대백과", addition_symbol=CHILD_SYMBOL, class_no=class_no
        )
        assert form == "info"

    @pytest.mark.parametrize("symbol", [None, "", "  "])
    def test_missing_addition_symbol_is_unknown(self, symbol):
        # 아동 도서인지 확인이 안 된다 — 제목에 보드북이 있어도, KDC 가 8 이어도 확정하지 않는다
        form = classify_book_form(title="보드북 공룡", addition_symbol=symbol, class_no="813.8")
        assert form == "unknown"

    @pytest.mark.parametrize("class_no", [None, "", "유813.8"])
    def test_unreadable_kdc_is_unknown(self, class_no):
        # 분류번호를 못 읽으면 문학인지 지식책인지 모른다
        form = classify_book_form(title="공룡 책", addition_symbol=CHILD_SYMBOL, class_no=class_no)
        assert form == "unknown"

    @pytest.mark.parametrize("symbol", ["03810", "13810", "93810", "83810"])
    def test_non_child_audience_is_excluded(self, symbol):
        # 서명에 보드북이 있어도 독자대상이 아동이 아니면 후보가 아니다
        form = classify_book_form(title="보드북 만들기", addition_symbol=symbol, class_no="813.8")
        assert form is None

    def test_title_keyword_does_not_override_kdc_when_not_in_title(self):
        # 키워드는 서명에서만 본다 — 분류번호가 문학이어도 서명에 없으면 board 가 아니다
        form = classify_book_form(title="곰 세 마리", addition_symbol=CHILD_SYMBOL, class_no="8")
        assert form == "picture"


class TestAgeRange:
    def test_ranges_are_the_gt3_values(self):
        assert BOOK_AGE_MONTHS == {
            "board": (0, 35),
            "picture": (12, 71),
            "info": (36, 71),
            "unknown": (12, 71),
        }

    @pytest.mark.parametrize("months", [0, 8, 11])
    def test_infant_gets_only_board(self, months):
        assert fits_age("board", months)
        assert not fits_age("picture", months)
        assert not fits_age("info", months)
        assert not fits_age("unknown", months)

    def test_board_ends_at_35_months_inclusive(self):
        assert fits_age("board", 35)
        assert not fits_age("board", 36)

    def test_picture_starts_at_12_months_inclusive(self):
        assert not fits_age("picture", 11)
        assert fits_age("picture", 12)

    def test_info_starts_at_36_months_inclusive(self):
        assert not fits_age("info", 35)
        assert fits_age("info", 36)

    def test_unknown_is_out_only_for_the_first_year(self):
        assert not fits_age("unknown", 11)
        assert fits_age("unknown", 12)

    @pytest.mark.parametrize("form", ["picture", "info", "unknown"])
    def test_last_month_of_v1_range_is_included(self, form):
        assert fits_age(form, 71)

    @pytest.mark.parametrize("form", ["board", "picture", "info", "unknown"])
    def test_nothing_after_v1_limit(self, form):
        # 72개월(만 6세)부터는 v1 범위 밖이다
        assert not fits_age(form, 72)
