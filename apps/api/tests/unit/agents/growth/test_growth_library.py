"""정보나루 원문 → 책 행 (`LibraryBooks`). 원문은 어댑터가, 해석은 여기가 한다.

지키는 것
- 아동 도서가 아니면 뺀다 · 형태와 월령 범위는 `rules/book_form` 하나에서 나온다
- 검색어가 여럿이면 번갈아 섞는다 — 앞 검색어 50권이 뒤 검색어를 다 밀어내지 않게
- 검색어 하나라도 조회에 실패하면 빈 목록이 아니라 오류다
"""

import pytest

from app.agents.growth.library import LibraryBooks, first_author, to_book_row
from app.agents.growth.store import ports
from app.agents.growth.store.ports import UpstreamUnavailable
from app.core import book_raw
from tests.unit.agents.growth.support import FakeRawSearch, raw_book


class TestToBookRow:
    def test_아동_문학은_그림책이고_월령_범위가_붙는다(self):
        row = to_book_row(raw_book(1))
        assert (row.form, row.age_min_month, row.age_max_month) == ("picture", 12, 71)
        assert (row.title, row.author, row.publisher) == ("책1", "가나다", "출판사")

    def test_보드북_서명은_보드북이다(self):
        assert to_book_row(raw_book(1, title="꼬마 공룡 보드북")).form == "board"

    def test_아동_도서가_아니면_뺀다(self):
        assert to_book_row(raw_book(1, symbol="03810")) is None

    def test_부가기호가_없으면_unknown(self):
        assert to_book_row(raw_book(1, symbol=None)).form == "unknown"

    def test_ISBN_이나_서명이_비면_뺀다(self):
        assert to_book_row(raw_book(1, isbn=" ")) is None
        assert to_book_row(raw_book(1, title="  ")) is None

    def test_ISBN_은_하이픈을_지운다(self):
        assert to_book_row(raw_book(1, isbn="978-89-0000-0001")).isbn == "9788900000001"


class TestFirstAuthor:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("글: 가나다 ; 그림: 라마바", "가나다"),
            ("글,그림: 가나다", "가나다"),  # 실제 응답 표기 — 역할 머리 안의 쉼표
            ("글·그림·사진: 가나다 ; 옮김: 라마바", "가나다"),  # 6자를 넘는 역할 머리
            ("지은이：가나다, 라마바", "가나다"),
            ("가나다 지음", "가나다 지음"),
            ("글:", None),
            ("", None),
            (None, None),
        ],
    )
    def test_첫_사람_하나_역할_머리는_뗀다(self, given, expected):
        assert first_author(given) == expected


class TestSearch:
    async def test_검색어마다_받아_번갈아_섞는다(self):
        fake = FakeRawSearch(
            {"공룡": [raw_book(1), raw_book(2)], "기차": [raw_book(3), raw_book(4)]}
        )
        rows = await LibraryBooks(fake).search(keywords=("공룡", "기차"), limit=50)
        assert [row.title for row in rows] == ["책1", "책3", "책2", "책4"]
        assert sorted(fake.calls) == [("공룡", 50), ("기차", 50)]

    async def test_같은_ISBN_은_한_권이다(self):
        fake = FakeRawSearch(
            {"공룡": [raw_book(1)], "기차": [raw_book(1, isbn="978-89-0000-0001")]}
        )
        rows = await LibraryBooks(fake).search(keywords=("공룡", "기차"), limit=50)
        assert len(rows) == 1

    async def test_아동_도서가_아닌_책은_빠지고_검색어마다_limit_권을_받는다(self):
        fake = FakeRawSearch(
            {
                "공룡": [raw_book(1, symbol="03400"), raw_book(2), raw_book(3)],
                "기차": [raw_book(4), raw_book(5), raw_book(6)],
            }
        )
        rows = await LibraryBooks(fake).search(keywords=("공룡", "기차"), limit=3)
        # 합쳐서 limit 으로 자르지 않는다 — 월령 필터(`search_books`)가 걸리기 전에 자르면
        # 뒤 검색어의 책이 먼저 사라진다
        assert [row.title for row in rows] == ["책4", "책2", "책5", "책3", "책6"]

    async def test_검색어를_더해도_월령_필터_전에_책이_잘리지_않는다(self):
        """ "공룡" 50권이 "공룡 보드북" 의 뒤쪽 보드북을 밀어내면 8개월은 0권이 된다."""
        pictures = [raw_book(n) for n in range(1, 51)]
        boards = [raw_book(n) for n in range(51, 81)] + [
            raw_book(n, title=f"공룡 보드북{n}") for n in range(81, 101)
        ]
        fake = FakeRawSearch({"공룡": pictures, "공룡 보드북": boards})

        rows = await LibraryBooks(fake).search(keywords=("공룡", "공룡 보드북"), limit=50)

        assert sum(row.form == "board" for row in rows) == 20

    async def test_검색어_하나라도_실패하면_빈_목록이_아니라_오류다(self):
        fake = FakeRawSearch({"공룡": [raw_book(1)]}, fail={"기차"})
        with pytest.raises(UpstreamUnavailable):
            await LibraryBooks(fake).search(keywords=("공룡", "기차"), limit=50)

    def test_포트는_core_의_예외를_그대로_내보낸다(self):
        # 어댑터(integrations)는 agents 를 import 하지 못해 core 의 것을 던진다
        # 같은 클래스여야 잡힌다
        assert ports.UpstreamUnavailable is book_raw.UpstreamUnavailable
