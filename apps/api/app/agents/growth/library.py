"""정보나루 원문 → Growth 의 책 행(`BookRow`). `BookSource` 포트의 실제 구현.

어댑터(`integrations/data4library`)는 원문만 싣는다(호출 · 캐시 · 필드 이름 옮기기).
해석은 여기서 한다.

    아동 도서인지 · 형태   `rules/book_form.classify_book_form`. None(아동 도서 아님)은 뺀다
    월령 범위              형태 하나에서 나온다(`BOOK_AGE_MONTHS`).
                           월령을 거는 것은 `search_books` 다
    저자                   첫 사람 하나. "글:" 같은 역할 머리는 뗀다 — "제목(저자)" 가 카드 제목이자
                           일정 준비물이자 이미 낸 책 대조 키다
    검색어 여럿            검색어마다 `limit` 권씩 받아 번갈아 섞는다 — 앞 검색어의 50권이 뒤를 다
                           밀어내지 않게. 합쳐서 자르지 않는다: 월령은 이 뒤에 걸려서, 먼저 자르면
                           뒤 검색어의 보드북이 사라진다
    같은 책                ISBN(하이픈 · 공백 무시)이 같으면 먼저 온 것만

검색어 하나라도 조회에 실패하면 `UpstreamUnavailable` — 나머지로 목록을 꾸미지 않는다.
"""

import asyncio
import re
from collections.abc import Iterator, Sequence

from app.agents.growth.store.ports import BookRow, RawBookSearch, UpstreamUnavailable
from app.core.book_raw import RawBook
from app.rules.book_evidence import normalize_isbn
from app.rules.book_form import BOOK_AGE_MONTHS, classify_book_form

# "글: 가나다" · "지은이：가나다" 같은 역할 머리. 실제 표기는 정보나루 응답 픽스처로 본다
_ROLE_HEAD = re.compile(r"^[^:：]{1,12}[:：]\s*")


class LibraryBooks:
    def __init__(self, raw: RawBookSearch) -> None:
        self._raw = raw

    async def search(self, *, keywords: tuple[str, ...], limit: int) -> list[BookRow]:
        results = await asyncio.gather(
            *(self._raw.search(keyword=keyword, limit=limit) for keyword in keywords),
            return_exceptions=True,
        )
        batches: list[tuple[RawBook, ...]] = []
        for result in results:
            if isinstance(result, UpstreamUnavailable):
                raise UpstreamUnavailable("도서 검색 실패") from None
            if isinstance(result, BaseException):
                raise result
            batches.append(result)
        rows: list[BookRow] = []
        seen: set[str] = set()
        for raw in _interleave(batches):
            row = to_book_row(raw)
            if row is None or row.isbn in seen:
                continue
            seen.add(row.isbn)
            rows.append(row)
        return rows


def to_book_row(raw: RawBook) -> BookRow | None:
    """원문 한 권 → 책 행. 아동 도서가 아니거나 ISBN · 서명이 비면 None."""
    isbn = normalize_isbn(raw.isbn13)
    title = raw.title.strip()
    if not isbn or not title:
        return None
    form = classify_book_form(
        title=title, addition_symbol=raw.addition_symbol, class_no=raw.class_no
    )
    if form is None:
        return None
    low, high = BOOK_AGE_MONTHS[form]
    return BookRow(
        isbn=isbn,
        title=title,
        form=form,
        author=first_author(raw.authors),
        publisher=(raw.publisher or "").strip() or None,
        cover_url=raw.cover_url,
        age_min_month=low,
        age_max_month=high,
    )


def first_author(raw: str | None) -> str | None:
    """저자 칸의 첫 사람. 역할 머리("글:" · "글,그림:")를 떼고, 비면 None.

    역할 머리 안에도 쉼표가 있다("글,그림: 이름") — 첫 `;` 묶음에서 콜론 앞을 먼저 떼고 나서 사람을
    나눈다.
    """
    if not raw:
        return None
    segment = raw.split(";", maxsplit=1)[0]
    people = _ROLE_HEAD.sub("", segment)
    name = people.split(",", maxsplit=1)[0].strip()
    return name or None


def _interleave(batches: Sequence[Sequence[RawBook]]) -> Iterator[RawBook]:
    for rank in range(max((len(batch) for batch in batches), default=0)):
        for batch in batches:
            if rank < len(batch):
                yield batch[rank]
