"""`search_books` · `propose_books` — 도서는 API 응답에 있던 ISBN 만 나간다 (Tool_명세 §2)

지키는 것 다섯 가지.
- 연령 필터는 코드가 넣는다. 8개월 아이에게는 `board` 외 도서가 0건이다
- 월령 범위는 `form` 하나에서 나온다 — 캐시 행의 월령 칸이 틀려도 form 으로 다시 건다
- 조회 실패는 빈 목록이 아니라 `UpstreamUnavailable` 이다. 캐시로 목록을 꾸미지 않는다
- 이번 run 의 `search_books` 결과에 없는 ISBN 은 거절한다 — 지어낸 책 · 연령 필터에 걸러진 책
- 만료 전 `suggestion` 에 이미 낸 책(승인 · 거절한 책도) · 같은 호출 안의 같은 책 · 4권째는 거절.
  같은 책은 `items` 에 들어가는 "제목(저자)" 로 가른다 — ISBN 은 저장하지 않는다
"""

from collections.abc import Sequence
from uuid import UUID

import pytest

from app.agents.growth.context import GrowthContext, build_gate
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemoryBooks, InMemoryIssuedBooks
from app.agents.growth.store.ports import BookForm, BookRow, UpstreamUnavailable
from app.agents.growth.tools.books import (
    MAX_BOOKS,
    SEARCH_LIMIT,
    SEARCH_POOL,
    BookRejectReason,
    book_label,
    explain,
    normalize_isbn,
    propose_books,
    search_books,
)
from tests.unit.agents.growth.support import CHILD, NOW, birth_for, context

BOOK = GrowthTaskType.BOOK_SUGGESTION


def isbn(n: int) -> str:
    return f"978890000{n:04d}"


def book(n: int, form: BookForm = "board", **kw) -> BookRow:
    return BookRow(isbn=isbn(n), title=f"책{n}", form=form, **kw)


async def ready(
    months: int, rows: Sequence[BookRow] = (), *, issued: Sequence[str] = ()
) -> GrowthContext:
    """게이트까지 채운 context. `run()` 이 `build_gate` 뒤에 `state.gate` 를 채우는 것과 같다."""
    ctx = context(
        birth_for(months),
        books=InMemoryBooks(rows),
        issued_books=InMemoryIssuedBooks(issued),
    )
    ctx.state.gate = await build_gate(ctx, BOOK)
    return ctx


def isbns_of(rows: Sequence[BookRow]) -> list[str]:
    return [row.isbn for row in rows]


class TestSearchAgeFilter:
    ALL_FORMS = [book(1, "board"), book(2, "picture"), book(3, "info"), book(4, "unknown")]

    async def test_8개월_아이에게는_board_외_도서가_0건이다(self):
        ctx = await ready(8, self.ALL_FORMS)
        found = await search_books(ctx, keywords=["공룡"])
        assert isbns_of(found) == [isbn(1)]

    async def test_30개월은_info_만_빠진다(self):
        ctx = await ready(30, self.ALL_FORMS)
        found = await search_books(ctx, keywords=["공룡"])
        assert isbns_of(found) == [isbn(1), isbn(2), isbn(4)]

    async def test_48개월은_board_만_빠진다(self):
        ctx = await ready(48, self.ALL_FORMS)
        found = await search_books(ctx, keywords=["공룡"])
        assert isbns_of(found) == [isbn(2), isbn(3), isbn(4)]

    async def test_unknown_은_돌_전에만_빠진다(self):
        rows = [book(1, "unknown")]
        assert await search_books(await ready(11, rows), keywords=["공룡"]) == ()
        assert isbns_of(await search_books(await ready(12, rows), keywords=["공룡"])) == [isbn(1)]

    async def test_캐시_행의_월령_칸이_틀려도_form_으로_다시_건다(self):
        # 월령 칸을 다시 믿으면 오래된 캐시 하나로 8개월 아이에게 그림책이 간다
        stale = book(1, "picture", age_min_month=0, age_max_month=71)
        ctx = await ready(8, [stale])
        assert await search_books(ctx, keywords=["공룡"]) == ()

    async def test_연령_밖_책이_앞에_많아도_맞는_책으로_k권을_채운다(self):
        misfit = [book(n, "picture") for n in range(1, 21)]
        fit = [book(n, "board") for n in range(21, 24)]
        ctx = await ready(8, [*misfit, *fit])
        found = await search_books(ctx, keywords=["공룡"], k=3)
        assert isbns_of(found) == [isbn(21), isbn(22), isbn(23)]

    async def test_범위_밖_72개월은_책이_없다(self):
        ctx = await ready(72, self.ALL_FORMS)
        assert await search_books(ctx, keywords=["공룡"]) == ()


class RecordingBooks(InMemoryBooks):
    def __init__(self, rows: Sequence[BookRow] = ()) -> None:
        super().__init__(rows)
        self.calls: list[tuple[tuple[str, ...], int]] = []

    async def search(self, *, keywords: tuple[str, ...], limit: int) -> list[BookRow]:
        self.calls.append((keywords, limit))
        return await super().search(keywords=keywords, limit=limit)


class ScriptedBooks(InMemoryBooks):
    """호출마다 다음 묶음을 돌려준다."""

    def __init__(self, batches: Sequence[Sequence[BookRow]]) -> None:
        super().__init__()
        self._batches = list(batches)

    async def search(self, *, keywords: tuple[str, ...], limit: int) -> list[BookRow]:
        return list(self._batches.pop(0))


class TestSearchCall:
    async def test_조회_실패는_빈_목록이_아니라_오류다(self):
        ctx = context(birth_for(30), books=InMemoryBooks([book(1)], fail=True))
        ctx.state.gate = await build_gate(ctx, BOOK)
        with pytest.raises(UpstreamUnavailable):
            await search_books(ctx, keywords=["공룡"])
        assert ctx.state.seen_books == {}  # 실패한 호출은 본 책을 남기지 않는다

    async def test_키워드는_다듬어서_포트에_넘긴다(self):
        recording = RecordingBooks()
        ctx = context(birth_for(30), books=recording)
        ctx.state.gate = await build_gate(ctx, BOOK)
        await search_books(ctx, keywords=["  공룡 ", "", "공룡", "한글"])
        assert recording.calls == [(("공룡", "한글"), SEARCH_POOL)]

    @pytest.mark.parametrize("keywords", [[], [""], ["  ", "\n"]])
    async def test_키워드가_비면_포트까지_가지_않는다(self, keywords):
        recording = RecordingBooks([book(1)])
        ctx = context(birth_for(30), books=recording)
        ctx.state.gate = await build_gate(ctx, BOOK)
        with pytest.raises(ValueError, match="키워드"):
            await search_books(ctx, keywords=keywords)
        assert recording.calls == []

    @pytest.mark.parametrize("k", [0, -1, SEARCH_LIMIT + 1])
    async def test_k_는_1에서_10까지다(self, k):
        ctx = await ready(30, [book(1)])
        with pytest.raises(ValueError, match="k"):
            await search_books(ctx, keywords=["공룡"], k=k)

    async def test_k_권까지만_돌려준다(self):
        ctx = await ready(30, [book(n, "picture") for n in range(1, 9)])
        found = await search_books(ctx, keywords=["공룡"], k=5)
        assert len(found) == 5

    async def test_포트가_같은_ISBN_을_두_번_줘도_한_권이다(self):
        ctx = await ready(30, [book(1, "picture"), book(1, "picture"), book(2, "picture")])
        found = await search_books(ctx, keywords=["공룡"])
        assert isbns_of(found) == [isbn(1), isbn(2)]

    async def test_도서_포트가_없으면_열리지_않았어야_한다(self):
        ctx = context(birth_for(30))
        ctx.state.gate = await build_gate(ctx, BOOK)
        with pytest.raises(RuntimeError, match="포트"):
            await search_books(ctx, keywords=["공룡"])

    async def test_게이트_없이_불리면_열리지_않았어야_한다(self):
        ctx = context(birth_for(30), books=InMemoryBooks([book(1)]))
        with pytest.raises(RuntimeError, match="Gate"):
            await search_books(ctx, keywords=["공룡"])


class TestSeenBooks:
    async def test_돌려준_책만_run_state_에_남는다(self):
        ctx = await ready(8, [book(1, "board"), book(2, "picture")])
        await search_books(ctx, keywords=["공룡"])
        assert set(ctx.state.seen_books) == {isbn(1)}

    async def test_검색을_여러_번_하면_이번_run_안에서_쌓인다(self):
        books = ScriptedBooks([[book(1, "picture")], [book(2, "picture")]])
        ctx = context(birth_for(30), books=books)
        ctx.state.gate = await build_gate(ctx, BOOK)
        await search_books(ctx, keywords=["공룡"])
        await search_books(ctx, keywords=["한글"])
        assert set(ctx.state.seen_books) == {isbn(1), isbn(2)}

    async def test_for_task_는_본_책을_새로_시작한다(self):
        ctx = await ready(30, [book(1, "picture")])
        await search_books(ctx, keywords=["공룡"])
        assert ctx.for_task().state.seen_books == {}


class IssuedBooks(InMemoryIssuedBooks):
    def __init__(self, labels: Sequence[str] = ()) -> None:
        super().__init__(labels)
        self.calls: list[tuple[UUID, object]] = []

    async def labels(self, *, child_id, now):
        self.calls.append((child_id, now))
        return await super().labels(child_id=child_id, now=now)


async def searched(months: int, rows: Sequence[BookRow], *, issued: Sequence[str] = ()):
    ctx = await ready(months, rows, issued=issued)
    await search_books(ctx, keywords=["공룡"])
    return ctx


class TestProposeIsbn:
    async def test_검색_결과_안의_ISBN_은_통과한다(self):
        ctx = await searched(30, [book(1, "picture"), book(2, "picture")])
        review = await propose_books(ctx, [isbn(2), isbn(1)])
        assert isbns_of(review.accepted) == [isbn(2), isbn(1)]
        assert review.rejections == ()

    async def test_통과한_책은_검색이_돌려준_행_그대로다(self):
        row = book(1, "picture", author="저자", cover_url="https://example.com/c.jpg")
        ctx = await searched(30, [row])
        review = await propose_books(ctx, [isbn(1)])
        assert review.accepted == (row,)

    async def test_지어낸_ISBN_은_거절한다(self):
        ctx = await searched(30, [book(1, "picture")])
        review = await propose_books(ctx, [isbn(1), isbn(999)])
        assert isbns_of(review.accepted) == [isbn(1)]
        assert [(r.index, r.reason) for r in review.rejections] == [
            (1, BookRejectReason.NOT_SEARCHED)
        ]

    async def test_검색_없이_부르면_전부_거절한다(self):
        ctx = await ready(30, [book(1, "picture")])
        review = await propose_books(ctx, [isbn(1)])
        assert review.accepted == ()
        assert review.rejections[0].reason is BookRejectReason.NOT_SEARCHED

    async def test_연령_필터에_걸러진_책은_검색_밖이다(self):
        # 포트는 그림책도 줬지만 8개월 아이에게는 돌려주지 않았다 — 모델이 ISBN 을 알아도 못 쓴다
        ctx = await searched(8, [book(1, "board"), book(2, "picture")])
        review = await propose_books(ctx, [isbn(1), isbn(2)])
        assert isbns_of(review.accepted) == [isbn(1)]
        assert review.rejections[0].reason is BookRejectReason.NOT_SEARCHED

    @pytest.mark.parametrize("written", ["978-89-0000-0001", "978 890000 0001", " 9788900000001 "])
    async def test_하이픈과_공백이_달라도_같은_책이다(self, written):
        ctx = await searched(30, [book(1, "picture")])
        assert isbn(1) == "9788900000001"
        review = await propose_books(ctx, [written])
        assert isbns_of(review.accepted) == [isbn(1)]

    async def test_이미_낸_책은_거절한다(self):
        rows = [book(1, "picture", author="가"), book(2, "picture", author="가")]
        ctx = await searched(30, rows, issued=["책1(가)"])
        review = await propose_books(ctx, [isbn(1), isbn(2)])
        assert isbns_of(review.accepted) == [isbn(2)]
        assert [(r.index, r.reason) for r in review.rejections] == [
            (0, BookRejectReason.ALREADY_ISSUED)
        ]

    async def test_이미_낸_책은_띄어쓰기가_달라도_거절한다(self):
        # 캐시가 갱신되며 서명 띄어쓰기가 바뀌어도 같은 책이다
        ctx = await searched(30, [book(1, "picture", author="가")], issued=["책 1 (가)"])
        review = await propose_books(ctx, [isbn(1)])
        assert review.accepted == ()
        assert review.rejections[0].reason is BookRejectReason.ALREADY_ISSUED

    async def test_제목과_저자가_같으면_다른_판도_이미_낸_책이다(self):
        # 보드북판 · 양장판처럼 ISBN 만 다른 같은 책 — 부모에게는 "다른 책" 이 아니다
        other_edition = BookRow(isbn=isbn(2), title="책1", form="picture", author="가")
        ctx = await searched(30, [other_edition], issued=["책1(가)"])
        review = await propose_books(ctx, [isbn(2)])
        assert review.accepted == ()
        assert review.rejections[0].reason is BookRejectReason.ALREADY_ISSUED

    async def test_제목이_같아도_저자가_다르면_다른_책이다(self):
        ctx = await searched(30, [book(1, "picture", author="나")], issued=["책1(가)"])
        review = await propose_books(ctx, [isbn(1)])
        assert isbns_of(review.accepted) == [isbn(1)]

    async def test_이미_낸_책은_아이_하나와_지금_시각으로_묻는다(self):
        issued = IssuedBooks()
        ctx = context(birth_for(30), books=InMemoryBooks([book(1, "picture")]), issued_books=issued)
        ctx.state.gate = await build_gate(ctx, BOOK)
        await search_books(ctx, keywords=["공룡"])
        await propose_books(ctx, [isbn(1)])
        assert issued.calls == [(CHILD, NOW)]

    async def test_같은_호출_안의_중복은_뒤엣것을_거절한다(self):
        ctx = await searched(30, [book(1, "picture"), book(2, "picture")])
        review = await propose_books(ctx, [isbn(1), isbn(2), "978-89-0000-0001"])
        assert isbns_of(review.accepted) == [isbn(1), isbn(2)]
        assert [(r.index, r.reason) for r in review.rejections] == [(2, BookRejectReason.DUPLICATE)]

    async def test_같은_호출_안에서_제목과_저자가_같은_다른_판은_뒤엣것을_거절한다(self):
        # 둘 다 내면 카드 두 장에 같은 준비물 "책1(가)" 가 찍힌다
        rows = [
            book(1, "picture", author="가"),
            BookRow(isbn=isbn(2), title="책1", form="picture", author="가"),
        ]
        ctx = await searched(30, rows)
        review = await propose_books(ctx, [isbn(1), isbn(2)])
        assert isbns_of(review.accepted) == [isbn(1)]
        assert [(r.index, r.reason) for r in review.rejections] == [(1, BookRejectReason.DUPLICATE)]

    async def test_네_권째부터는_거절한다(self):
        rows = [book(n, "picture") for n in range(1, 6)]
        ctx = await searched(30, rows)
        review = await propose_books(ctx, [isbn(n) for n in range(1, 6)])
        assert MAX_BOOKS == 3
        assert isbns_of(review.accepted) == [isbn(1), isbn(2), isbn(3)]
        assert [(r.index, r.reason) for r in review.rejections] == [
            (3, BookRejectReason.OVER_LIMIT),
            (4, BookRejectReason.OVER_LIMIT),
        ]

    async def test_거절된_책은_3권_한도를_쓰지_않는다(self):
        rows = [book(n, "picture") for n in range(1, 5)]
        ctx = await searched(30, rows, issued=["책1"])
        review = await propose_books(ctx, [isbn(1), isbn(2), isbn(3), isbn(4)])
        assert isbns_of(review.accepted) == [isbn(2), isbn(3), isbn(4)]
        assert [r.reason for r in review.rejections] == [BookRejectReason.ALREADY_ISSUED]

    async def test_검색_밖_ISBN_이_먼저_걸린다(self):
        # 지어낸 책이 우연히 이미 낸 목록과 같은 이름이어도 사유는 검색 밖이다
        ctx = await searched(30, [book(1, "picture")], issued=["책999"])
        review = await propose_books(ctx, [isbn(999)])
        assert review.rejections[0].reason is BookRejectReason.NOT_SEARCHED

    async def test_이미_낸_책_포트가_없으면_조용히_통과시키지_않는다(self):
        ctx = context(birth_for(30), books=InMemoryBooks([book(1, "picture")]))
        ctx.state.gate = await build_gate(ctx, BOOK)
        await search_books(ctx, keywords=["공룡"])
        with pytest.raises(RuntimeError, match="issued_books"):
            await propose_books(ctx, [isbn(1)])

    async def test_거절_설명은_후보_번호와_고칠_방향만_싣는다(self):
        ctx = await searched(30, [book(1, "picture")], issued=["책1"])
        review = await propose_books(ctx, [isbn(1), isbn(999)])
        text = explain(review.rejections)
        assert "1번 후보" in text and "2번 후보" in text
        assert isbn(1) not in text and isbn(999) not in text  # 로그로 흘러가는 값은 싣지 않는다
        assert "책1" not in text


class TestBookLabel:
    """`suggestion.items` 에 들어가 일정 준비물이 되는 글자. 이미 낸 책도 이 글자로 가른다."""

    @pytest.mark.parametrize(
        ("title", "author", "expected"),
        [
            ("공룡 대백과", "김철수", "공룡 대백과(김철수)"),
            ("공룡 대백과", None, "공룡 대백과"),
            ("공룡 대백과", "  ", "공룡 대백과"),
            ("  공룡 대백과 ", " 김철수\n", "공룡 대백과(김철수)"),
        ],
    )
    def test_제목_뒤에_저자를_괄호로_붙인다(self, title, author, expected):
        row = BookRow(isbn=isbn(1), title=title, form="picture", author=author)
        assert book_label(row) == expected


class TestNormalizeIsbn:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("9788900000001", "9788900000001"),
            ("978-89-0000-0001", "9788900000001"),
            (" 978 89 0000 0001\n", "9788900000001"),
            ("89-0000-000-x", "890000000X"),
        ],
    )
    def test_숫자와_X_만_남긴다(self, raw, expected):
        assert normalize_isbn(raw) == expected
