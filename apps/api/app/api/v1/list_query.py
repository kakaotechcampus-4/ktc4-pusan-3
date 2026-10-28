"""기록 · 기억 목록이 같이 받는 쿼리 — 쪽(page · size) · 날짜 범위 · 검색어 (#584).

🚨 검증 실패는 422 가 아니라 400 `validation_failed` 다 (`app/api/errors.py` — 경로 값만 422).
   `date_from > date_to` 는 FastAPI 가 못 잡아서 여기서 같은 400 으로 던진다. 안 막으면
   repository 의 `ValueError` 가 500 으로 샌다.
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import Depends, Query

from app.api.errors import ApiError

# 🚨 상한이 없으면 터무니없이 큰 page 가 offset 을 DB 정수 범위 밖으로 밀어 500 이 된다
#    (size=100 에서 page=10^17 부터 재현). size 최대 100 을 곱해도 100만 행이라
#    정상 사용은 닿지 않는다.
MAX_PAGE = 10_000


_Page = Annotated[int, Query(ge=1, le=MAX_PAGE, description="쪽 번호 (1부터, 최대 10,000)")]
_Size = Annotated[int, Query(ge=1, le=100, description="한 쪽에 보여줄 개수")]


@dataclass(frozen=True)
class PageQuery:
    """날짜 · 검색어 없이 쪽만 받는 목록(추천 · #592)이 쓴다."""

    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    def total_pages(self, total: int) -> int:
        return math.ceil(total / self.size)


def page_query(page: _Page = 1, size: _Size = 20) -> PageQuery:
    return PageQuery(page=page, size=size)


PageQueryDep = Annotated[PageQuery, Depends(page_query)]


@dataclass(frozen=True)
class ListQuery:
    page: int
    size: int
    date_from: date | None
    date_to: date | None
    q: str | None  # 앞뒤 공백을 자른 값. 비어 있으면 None — 검색하지 않는다

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    def total_pages(self, total: int) -> int:
        return math.ceil(total / self.size)


def list_query(
    page: _Page = 1,
    size: _Size = 20,
    date_from: Annotated[date | None, Query(description="이 날짜 이후 (포함)")] = None,
    date_to: Annotated[date | None, Query(description="이 날짜 이전 (포함)")] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="부분 일치 검색어. 앞뒤 공백은 자른다")
    ] = None,
) -> ListQuery:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ApiError(400, "validation_failed", "시작 날짜가 끝 날짜보다 늦을 수 없어요")
    return ListQuery(
        page=page, size=size, date_from=date_from, date_to=date_to, q=(q or "").strip() or None
    )


ListQueryDep = Annotated[ListQuery, Depends(list_query)]
