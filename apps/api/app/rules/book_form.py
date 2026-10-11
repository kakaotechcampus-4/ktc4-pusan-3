"""도서의 책 형태(`form`)와 월령 범위. 처음 값은 `growth_agent_own_table.md` §7 GT-3 이다.

정보나루 `age` 는 대출자 연령대라 아이 월령을 가르지 못한다(0 = 영유아 0~5세 한 칸). 그래서 책
자체의 정보로 형태를 정하고, 월령 범위는 형태 하나에서 나온다.

    부가기호 첫 자리가 7 이 아님      아동 도서가 아니다 → 후보에서 뺀다 (None)
    부가기호가 없음                   `unknown` — 아동 도서인지 확인이 안 된다
    서명에 보드북 · 촉감책 …          `board`
    KDC 대분류 8 (문학)               `picture`
    그 밖의 KDC                       `info`
    KDC 를 못 읽음                    `unknown`

청구기호(`유` 계열)는 소장 도서관마다 달라 쓰지 않는다.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
정보나루 어댑터(`integrations`)가 만든 원문을 `BookRow` 로 바꾸는 쪽과 Growth `search_books` 가 같은
함수를 쓰려고 `rules/` 에 둔다.
"""

from typing import Literal

from app.rules.term_match import normalize

BookForm = Literal["board", "picture", "info", "unknown"]

# ISBN 부가기호 첫 자리 = 독자대상. 7 이 아동이다
CHILD_AUDIENCE = "7"
# KDC 대분류 8 = 문학
KDC_LITERATURE = "8"

# 서명 키워드. 공백을 지우고 찾는다 — "보드 북" 도 같은 말이다
BOARD_TITLE_KEYWORDS = ("보드북", "촉감책", "헝겊책", "사운드북", "초점책")

# 형태별 월령 범위. 개월 수 기준으로 양 끝을 포함한다(0–35 = 0개월부터 35개월까지).
# `unknown` 이 12 에서 시작하는 것은 0–11개월 제외다 — 보드북인지 모르면 영아에게 내지 않는다.
# 처음 값이라 어댑터 계약 테스트에서 실제 응답으로 확정한다
BOOK_AGE_MONTHS: dict[BookForm, tuple[int, int]] = {
    "board": (0, 35),
    "picture": (12, 71),
    "info": (36, 71),
    "unknown": (12, 71),
}


def classify_book_form(
    *, title: str, addition_symbol: str | None, class_no: str | None
) -> BookForm | None:
    """책 한 권의 형태. `None` 이면 아동 도서가 아니라서 후보에서 뺀다.

    서명 키워드는 부가기호가 아동일 때만 본다 — "보드북 만들기" 같은 성인 도서가 영아에게
    가지 않는다. 부가기호가 없으면 서명이나 KDC 가 그럴듯해도 `unknown` 이다.
    """
    symbol = (addition_symbol or "").strip()
    if not symbol:
        return "unknown"
    if symbol[0] != CHILD_AUDIENCE:
        return None
    squeezed = normalize(title)
    if any(keyword in squeezed for keyword in BOARD_TITLE_KEYWORDS):
        return "board"
    major = (class_no or "").strip()[:1]
    if not major.isdigit():
        return "unknown"
    return "picture" if major == KDC_LITERATURE else "info"


def fits_age(form: BookForm, months: int) -> bool:
    """이 월령 아이에게 낼 수 있는 형태인가."""
    low, high = BOOK_AGE_MONTHS[form]
    return low <= months <= high
