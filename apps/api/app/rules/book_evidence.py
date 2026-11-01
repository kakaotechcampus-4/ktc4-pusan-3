"""Growth 일반 도서 추천의 책 근거 — 고른 책의 서지 행(도서관 정보나루)을
`suggestion_evidence` 로 가리킨다.

Food 일반 추천이 고른 메뉴를 근거로 다는 것(#442, `rules/menu_evidence.py`)과 같은 방식이다.
일반 도서 추천은 아이 기록이 없어도 그 책 자체를 근거로 단다 — 모델이 지어낸 책이 아니라 공공
API 가 돌려준 실제 행이다. 책 정보 표를 두지 않아서(검색어 캐시는 Redis) ISBN 에서 늘 같은
uuid5 를 만든다 — `suggestion_evidence.source_id` 가 UUID 칸이라 ISBN 을 그대로 못 넣는다.

Agent(근거를 만들 때) · 저장(Growth 일반 추천인지 볼 때) · 05(출처 카드를 그릴 때)가 이 모듈을
쓴다. 세 레이어가 다 부르므로 표준 라이브러리만 쓴다.
"""

from uuid import NAMESPACE_URL, UUID, uuid5

# `suggestion_evidence.source_kind` 값. agents 의 `refs.BookKind` 와 같은 글자다
BOOK_EVIDENCE_KIND = "book_catalog"


def normalize_isbn(raw: str) -> str:
    """하이픈 · 공백을 지우고 X 는 대문자로. 모델이 `978-89-…` 로 적어도 같은 책으로 본다."""
    return "".join(ch for ch in raw if ch.isascii() and ch.isalnum()).upper()


def book_evidence_id(isbn: str) -> UUID:
    """책 한 권을 가리키는 근거 id. 하이픈 · 공백이 달라도 같은 ISBN 이면 늘 같다."""
    return uuid5(NAMESPACE_URL, f"{BOOK_EVIDENCE_KIND}:{normalize_isbn(isbn)}")
