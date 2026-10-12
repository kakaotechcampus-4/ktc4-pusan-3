"""도서 검색 · 출력 검증 (모델 tool 2개) — `search_books` · `propose_books`.

**책은 API 응답에 있던 ISBN 만 나간다.** 모델이 책 제목을 지어내면 `propose_books` 가 ISBN 으로
거절한다 (Growth_Tool_명세.md §2 · growth_agent_own_table.md §2).

    search_books   포트(캐시 먼저, 없으면 정보나루)에서 찾아 **코드가 월령을 건다**.
                   모델에게 월령 인자는 없다. 범위는 `form` 하나에서 나온다(`rules/book_form.py`)
                   — 캐시 행의 월령 칸을 다시 믿지 않는다
    propose_books  이번 run 의 `search_books` 결과에 없는 ISBN · 만료 전 `suggestion` 에 이미 낸
                   책 · 같은 호출 안의 같은 책 · 4권째를 거절한다. 같은 책은 "제목(저자)" 로 가른다

조회에 실패하면 `UpstreamUnavailable` 을 그대로 올린다. 빈 목록으로 바꾸면 "그 키워드의 책이 없다"
로 읽히고, 캐시로 목록을 꾸미면 절판된 책이 나간다. 호출부(`run()`)가 이 예외를 받아
`book_suggestion` 을 `closed.book_api` 로 닫는다.

draft 로 바꾸는 일 — 근거 id 대조 · `reason` · 일반 추천 문구 — 은 모델 경로가 한다. 여기는
어느 책이 나갈 수 있는가 까지다. 추천 행의 `suggestion.items` 에는 `book_label` 의 "제목(저자)"
하나를 넣는다. 일정으로 만들면 준비물로 그대로 이동한다. ISBN 은 저장하지 않는다 — run 안에서
검색 결과와 대조하는 데만 쓴다(#292 리뷰).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.growth.context import GrowthContext
from app.agents.growth.store.ports import BookRow
from app.rules.book_form import fits_age
from app.rules.term_match import normalize

# 모델이 한 번에 받는 책 수의 상한 (Tool_명세 §2 — `k≤10`)
SEARCH_LIMIT = 10
# 포트에서 월령을 걸기 전에 가져오는 수. 연령 밖 책이 앞에 몰려도 맞는 책으로 `k` 권을 채우려고
# 넉넉히 받는다. 8개월처럼 `board` 만 통과하는 월령은 50권 안에 보드북이 적으면
# `k` 권보다 적게 돌려준다. 정보나루 실제 응답에서 `board` 비율을 보고 조정한다
SEARCH_POOL = 50
# 한 번에 내는 책 수 (Tool_명세 §2 — `items[3]`)
MAX_BOOKS = MAX_SUGGESTIONS


def normalize_isbn(raw: str) -> str:
    """하이픈 · 공백을 지우고 X 는 대문자로. 모델이 `978-89-…` 로 적어도 같은 책으로 본다."""
    return "".join(ch for ch in raw if ch.isascii() and ch.isalnum()).upper()


def book_label(row: BookRow) -> str:
    """`suggestion.items` 에 들어가는 책 이름 — "제목(저자)", 저자가 없으면 제목만.

    일정으로 만들면 이 글자가 준비물이 된다. 이미 낸 책도 이 글자로 가른다(`propose_books`).
    """
    title = row.title.strip()
    author = (row.author or "").strip()
    return f"{title}({author})" if author else title


async def search_books(
    context: GrowthContext, *, keywords: Sequence[str], k: int = SEARCH_LIMIT
) -> tuple[BookRow, ...]:
    """키워드로 책을 찾고, 이 아이 월령에 맞는 형태만 `k` 권까지 돌려준다.

    - 월령은 `Gate` 의 것이다. 8개월이면 `board` 만, 12개월 미만에게 `unknown` 은 나가지 않는다.
    - 돌려준 책만 `context.state.seen_books` 에 적는다 — `propose_books` 가 이 표와 대조한다.
      연령 필터에 걸러진 책은 모델이 ISBN 을 알아도 낼 수 없다. 검색을 여러 번 하면 쌓인다.
    - 포트가 실패하면 `UpstreamUnavailable`. 빈 목록도 캐시 목록도 아니다.
    - 키워드는 코드가 다듬는다(공백 제거 · 중복 제거). 비면 `ValueError` — 포트까지 가지 않는다.
    - 같은 ISBN 은 한 번만 준다. 먼저 온 행을 쓴다.
    """
    if not 1 <= k <= SEARCH_LIMIT:
        raise ValueError(f"k 는 1~{SEARCH_LIMIT} 이다: {k}")
    cleaned = tuple(dict.fromkeys(word.strip() for word in keywords if word.strip()))
    if not cleaned:
        raise ValueError("키워드가 비었다")
    gate = context.state.gate
    if gate is None:
        raise RuntimeError("Gate 없이 도서 검색이 불렸다 — run() 이 먼저 build_gate 를 부른다")
    books = context.ports.books
    if books is None:
        raise RuntimeError("도서 포트 없이 도서 검색이 불렸다 — registry 가 열지 않았어야 한다")

    months = gate.stage.months
    rows = await books.search(keywords=cleaned, limit=SEARCH_POOL)
    found: dict[str, BookRow] = {}
    for row in rows:
        key = normalize_isbn(row.isbn)
        if key in found or not fits_age(row.form, months):
            continue
        found[key] = row
        if len(found) == k:
            break
    context.state.seen_books.update(found)
    return tuple(found.values())


class BookRejectReason(StrEnum):
    NOT_SEARCHED = "not_searched"
    ALREADY_ISSUED = "already_issued"
    DUPLICATE = "duplicate"
    OVER_LIMIT = "over_limit"


# 모델에게 돌려줄 설명 — 무엇을 고치면 되는지만. ISBN 을 되풀이하지 않는다
GUIDANCE: dict[BookRejectReason, str] = {
    BookRejectReason.NOT_SEARCHED: "isbn 에는 search_books 결과에 있던 것만 쓴다.",
    BookRejectReason.ALREADY_ISSUED: "이미 추천한 책이다. 다른 책으로 바꾼다.",
    BookRejectReason.DUPLICATE: "같은 책을 두 번 골랐다. 다른 책으로 바꾼다.",
    BookRejectReason.OVER_LIMIT: f"책은 최대 {MAX_BOOKS}권이다.",
}


@dataclass(frozen=True)
class BookRejection:
    index: int  # 후보 순서 (0부터)
    reason: BookRejectReason


@dataclass(frozen=True)
class BookReview:
    accepted: tuple[BookRow, ...]  # `search_books` 가 돌려줬던 행 그대로
    rejections: tuple[BookRejection, ...]


async def propose_books(context: GrowthContext, isbns: Sequence[str]) -> BookReview:
    """고른 ISBN 을 순서대로 검사해 통과한 책과 거절 사유를 돌려준다.

    검사 순서가 사유를 정한다 — 검색 밖 → 이미 낸 책 → 같은 호출 안의 중복 → 4권째.
    지어낸 책이 우연히 낸 목록과 같은 이름이어도 사유는 검색 밖이다. 거절된 책은 3권 한도를
    쓰지 않는다. 사유는 코드와 후보 번호로만 남긴다 — ISBN · 제목을 싣지 않는다 (로그로 흘러간다).

    검색 밖인지는 ISBN 으로, 같은 책인지는 `book_label`("제목(저자)")로 본다. ISBN 은 저장하지
    않아서 지난 추천과는 `items` 의 이 글자로만 맞춰 볼 수 있다. 그래서 보드북판 · 양장판처럼
    ISBN 만 다른 같은 책도 같은 책으로 거른다 — 같은 호출 안에서도 둘 다 내면 카드 두 장에 같은
    준비물이 찍힌다.

    만료 전 추천은 상태를 가리지 않는다 — 승인 · 거절한 책도 다시 나오지 않는다. 낸 책 포트는 run 이
    아니라 호출마다 읽는다(같은 run 의 draft 는 아직 저장 전이라 읽어도 같다).
    """
    issued_reader = context.ports.issued_books
    if issued_reader is None:
        raise RuntimeError(
            "issued_books 포트 없이 propose_books 가 불렸다 — 낸 책을 모르면 같은 책이 다시 나온다"
        )
    issued = {
        normalize(label)
        for label in await issued_reader.labels(child_id=context.child_id, now=context.now)
    }
    seen = context.state.seen_books

    accepted: dict[str, BookRow] = {}
    rejections: list[BookRejection] = []
    for index, raw in enumerate(isbns):
        row = seen.get(normalize_isbn(raw))
        if row is None:
            rejections.append(BookRejection(index=index, reason=BookRejectReason.NOT_SEARCHED))
            continue
        same_book = normalize(book_label(row))
        if same_book in issued:
            reason = BookRejectReason.ALREADY_ISSUED
        elif same_book in accepted:
            reason = BookRejectReason.DUPLICATE
        elif len(accepted) >= MAX_BOOKS:
            reason = BookRejectReason.OVER_LIMIT
        else:
            accepted[same_book] = row
            continue
        rejections.append(BookRejection(index=index, reason=reason))
    return BookReview(accepted=tuple(accepted.values()), rejections=tuple(rejections))


def explain(rejections: Sequence[BookRejection]) -> str:
    """모델이 읽는 거절 설명. 후보 번호(1부터)와 고칠 방향만 싣는다."""
    return " / ".join(f"{r.index + 1}번 후보: {GUIDANCE[r.reason]}" for r in rejections)
