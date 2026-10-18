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
from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.common.evidence import RankedEvidence
from app.agents.common.refs import Ref
from app.agents.growth.context import GrowthContext, build_gate
from app.agents.growth.review import BOOK_GENERAL_REASON
from app.agents.growth.schemas.books import ProposeBooksArgs, SearchBooksArgs
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
    search_books_tool,
)
from app.agents.growth.tools.propose import propose_books_tool
from app.rules.book_evidence import book_evidence_id
from tests.unit.agents.growth.support import CHILD, NOW, TODAY, birth_for, context

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

    async def test_검색하면_이번_run_에_검색한_것으로_친다(self):
        ctx = await ready(8, [book(1, "picture")])
        assert ctx.state.book_searched is False
        await search_books(ctx, keywords=["공룡"])  # 연령 필터로 0권이어도
        assert ctx.state.book_searched is True

    async def test_조회가_실패하면_검색한_것으로_치지_않는다(self):
        ctx = context(birth_for(30), books=InMemoryBooks([book(1)], fail=True))
        ctx.state.gate = await build_gate(ctx, BOOK)
        with pytest.raises(UpstreamUnavailable):
            await search_books(ctx, keywords=["공룡"])
        assert ctx.state.book_searched is False


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


class TestAvailable:
    """낼 수 있는 책 = 이번 run 에 검색한 책 − 이미 낸 책 − 재호출 전에 고르거나 빠진 책,
    3권까지."""

    async def test_검색한_책에서_이미_낸_책을_뺀_수_3권까지(self):
        many = await searched(30, [book(n, "picture") for n in range(1, 6)], issued=["책1"])
        assert (await propose_books(many, [])).available == 3
        few = await searched(30, [book(1, "picture"), book(2, "picture")], issued=["책1"])
        assert (await propose_books(few, [])).available == 1

    async def test_재호출에서는_이번_run_에_고른_책도_이미_낸_책이다(self):
        ctx = await searched(30, [book(n, "picture") for n in range(1, 4)])
        ctx.state.done_books = frozenset({"책1"})
        review = await propose_books(ctx, [isbn(1), isbn(2)])
        assert [r.reason for r in review.rejections] == [BookRejectReason.ALREADY_ISSUED]
        assert review.available == 2


class TestSearchTool:
    async def test_찾은_책을_이름_저자_출판사_형태로_돌려준다(self):
        row = book(1, "picture", author="가", publisher="나", cover_url="https://example.com/c.jpg")
        ctx = await ready(30, [row])
        result = await search_books_tool(ctx, SearchBooksArgs(keywords=["공룡"]))
        assert result.success
        assert result.data["books"] == [
            {"isbn": isbn(1), "title": "책1", "author": "가", "publisher": "나", "form": "picture"}
        ]

    async def test_맞는_책이_없으면_NO_RECORDS_로_다른_낱말이나_빈_제출을_알린다(self):
        ctx = await ready(8, [book(1, "picture")])
        result = await search_books_tool(ctx, SearchBooksArgs(keywords=["공룡"]))
        assert result.error["code"] == "NO_RECORDS"
        assert "items 를 비워" in result.error["message"]
        assert "공룡" not in result.error["message"]


DINO = RankedEvidence(
    ref=Ref(kind="observation_education", id=UUID(int=901)),
    tier=3,
    label="공룡 그림을 오래 봤다",
    polarity=1,
    observed_on=TODAY - timedelta(days=2),
    subject="공룡",
)


def pick(
    n: int, *, evidence: Sequence[dict] = (), reason: str = "공룡이 나오는 그림책이에요"
) -> dict:
    return {"isbn": isbn(n), "evidence": list(evidence), "reason": reason}


def books_of(*picks: dict) -> ProposeBooksArgs:
    return ProposeBooksArgs.model_validate({"items": list(picks)})


class TestProposeTool:
    async def test_검색한_책_3권은_일반_추천이고_책이_근거다(self):
        ctx = await searched(30, [book(n, "picture", author="가") for n in range(1, 4)])
        result = await propose_books_tool(ctx, books_of(pick(1), pick(2), pick(3)))
        assert result.success
        drafts = ctx.state.suggestions
        assert [d.content for d in drafts] == ["책1(가)", "책2(가)", "책3(가)"]
        assert [d.items for d in drafts] == [("책1(가)",), ("책2(가)",), ("책3(가)",)]
        assert {d.kind for d in drafts} == {"general"}
        assert drafts[0].reason == BOOK_GENERAL_REASON
        assert [(c.ref.kind, c.ref.id) for c in drafts[0].citations] == [
            ("book_catalog", book_evidence_id(isbn(1)))
        ]
        assert ctx.state.capacity == 3 and ctx.state.excluded == ()

    async def test_개인화_도서는_아이_기록만_근거고_책_근거는_0행이다(self):
        # 책 근거는 코드가 general_basis 로만 넘기고 개인화면 build() 가 버린다 (Food #442 와 같다)
        ctx = await searched(30, [book(n, "picture", author="가") for n in range(1, 4)])
        ctx.state.seen_evidence[DINO.ref.id] = DINO
        cite = [{"id": str(DINO.ref.id), "note": "공룡 그림을 오래 봤어요"}]
        await propose_books_tool(ctx, books_of(*(pick(n, evidence=cite) for n in (1, 2, 3))))
        for draft in ctx.state.suggestions:
            assert draft.kind == "personalized" and draft.reason == "공룡이 나오는 그림책이에요"
            assert [(c.ref.kind, c.ref.id) for c in draft.citations] == [
                ("observation_education", DINO.ref.id)
            ]
            assert not any(c.ref.kind == "book_catalog" for c in draft.citations)

    async def test_모델이_책_id_를_근거로_적어도_받지_않는다(self):
        # 책 근거는 모델 citations 로 들어오지 않는다 — 조회 결과에 없는 id 라 근거 대조가 거절한다
        ctx = await searched(30, [book(n, "picture") for n in range(1, 4)])
        forged = [{"id": str(book_evidence_id(isbn(1))), "note": "이 책이에요"}]
        result = await propose_books_tool(ctx, books_of(pick(1, evidence=forged), pick(2), pick(3)))
        assert result.error["code"] == "EVIDENCE_REQUIRED"

    async def test_검색하기_전에는_빈_제출도_받지_않는다(self):
        ctx = await ready(30, [book(1, "picture")])
        result = await propose_books_tool(ctx, books_of())
        assert result.error["code"] == "CANDIDATE_REJECTED"
        assert "search_books" in result.error["message"]

    async def test_낼_수_있는_책보다_적게_내면_거절한다(self):
        ctx = await searched(30, [book(n, "picture") for n in range(1, 5)])
        result = await propose_books_tool(ctx, books_of(pick(1), pick(2)))
        assert result.error["code"] == "CANDIDATE_REJECTED" and "3권" in result.error["message"]
        assert ctx.state.suggestions == ()

    async def test_맞는_책이_2권뿐이면_2권으로_끝난다(self):
        ctx = await searched(8, [book(1, "board"), book(2, "board"), book(3, "picture")])
        result = await propose_books_tool(ctx, books_of(pick(1), pick(2)))
        assert result.success and len(ctx.state.suggestions) == 2
        assert ctx.state.capacity == 2

    async def test_맞는_책이_없으면_빈_제출로_끝난다(self):
        ctx = await searched(8, [book(1, "picture")])
        result = await propose_books_tool(ctx, books_of())
        assert result.success and ctx.state.suggestions == () and ctx.state.capacity == 0

    async def test_지어낸_ISBN_은_거절하고_ISBN_을_싣지_않는다(self):
        ctx = await searched(30, [book(n, "picture") for n in range(1, 4)])
        result = await propose_books_tool(ctx, books_of(pick(1), pick(2), pick(999)))
        assert result.error["code"] == "CANDIDATE_REJECTED"
        assert isbn(999) not in result.error["message"]

    async def test_이미_낸_책뿐이면_비우라고_알린다(self):
        ctx = await searched(30, [book(1, "picture")], issued=["책1"])
        result = await propose_books_tool(ctx, books_of(pick(1)))
        assert "items 를 비운다" in result.error["message"]

    async def test_모르는_근거_id_는_EVIDENCE_REQUIRED(self):
        ctx = await searched(30, [book(n, "picture") for n in range(1, 4)])
        stranger = [{"id": str(UUID(int=999)), "note": "지어낸 기록"}]
        result = await propose_books_tool(
            ctx, books_of(pick(1, evidence=stranger), pick(2), pick(3))
        )
        assert result.error["code"] == "EVIDENCE_REQUIRED"

    async def test_이유에_금지_표현이면_빼고_제외_목록에_책_이름을_남긴다(self):
        ctx = await searched(30, [book(n, "picture", author="가") for n in range(1, 4)])
        result = await propose_books_tool(
            ctx, books_of(pick(1, reason="숫자에 재능이 있어서 골랐어요"), pick(2), pick(3))
        )
        assert result.success
        assert [d.content for d in ctx.state.suggestions] == ["책2(가)", "책3(가)"]
        assert ctx.state.excluded == ("책1(가)",) and ctx.state.capacity == 3

    async def test_책_이름에_금지_표현이_있어도_뺀다(self):
        rows = [
            BookRow(isbn=isbn(1), title="소질 찾기", form="picture"),
            book(2, "picture"),
            book(3, "picture"),
        ]
        ctx = await searched(30, rows)
        await propose_books_tool(ctx, books_of(pick(1), pick(2), pick(3)))
        assert [d.content for d in ctx.state.suggestions] == ["책2", "책3"]
        assert ctx.state.excluded == ("소질 찾기",)

    async def test_관찰_근거에_성향_표현을_쓰면_거절한다(self):
        # 한 번의 관찰을 성향으로 단정하지 않는다 — 티어 1 근거에만 쓸 수 있다 (교육 · 루틴과 같다)
        ctx = await searched(30, [book(n, "picture") for n in range(1, 4)])
        ctx.state.seen_evidence[DINO.ref.id] = DINO
        cite = [{"id": str(DINO.ref.id), "note": "공룡을 늘 좋아해요"}]
        result = await propose_books_tool(
            ctx, books_of(*(pick(n, evidence=cite) for n in (1, 2, 3)))
        )
        assert result.error["code"] == "CANDIDATE_REJECTED"
        assert "tier 1" in result.error["message"]
