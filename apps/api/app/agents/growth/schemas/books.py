"""도서 검색 · 제출 인자. 책은 검색 결과의 ISBN 으로만 고른다 — 제목을 받지 않는다.

검색어는 정보나루로 나간다. 그래서 모양을 코드가 묶는다 — 3개까지 · 한 개 12자까지 · 한글 · 영문 ·
숫자와 낱말 사이 띄어쓰기 한 칸만. 아이 이름이나 문장이 밖으로 나가지 않게 하는 하한이다
(외부연결_계획 §1 "외부 쿼리에 보호자 발화를 넣지 않는다").

모델이 kind 를 고르지 않는다 — 아이 기록 근거가 0행이면 코드가 일반 추천으로 분류하고, 그 책
자체(`book_catalog`)를 근거로 단다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.growth.schemas.common import EvidencePick, ToolArgs

# 검색어 하나 = 캐시가 없을 때 정보나루 1회. `search_books` 한 번에 넣을 수 있는 수다
MAX_KEYWORDS = 3
# run 하나에서 정보나루로 보내는 검색어의 합. 모델이 여러 번 불러도 여기까지다 — IP 등록 전에는
# 하루 500회라 run 하나가 한도를 쓰지 않게 묶는다(#452 리뷰: 상한이 호출에만 걸려 18번 나갔다)
MAX_RUN_KEYWORDS = 6
KEYWORD_MAX_LENGTH = 12
# 낱말 사이 띄어쓰기는 한 칸만 — 앞뒤 공백 · 줄바꿈 · 문장부호는 받지 않는다
_KEYWORD_PATTERN = r"^[0-9A-Za-z가-힣]+( [0-9A-Za-z가-힣]+)*$"

Keyword = Annotated[
    str, Field(min_length=1, max_length=KEYWORD_MAX_LENGTH, pattern=_KEYWORD_PATTERN)
]


class SearchBooksArgs(ToolArgs):
    keywords: Annotated[
        list[Keyword],
        Field(
            min_length=1,
            max_length=MAX_KEYWORDS,
            description="찾을 주제 낱말. 예: 공룡, 자동차. 아이 이름 · 문장 · 나이는 넣지 않는다",
        ),
    ]


class BookItem(ToolArgs):
    isbn: Annotated[
        str,
        Field(min_length=10, max_length=20, description="search_books 결과에 있던 isbn 만"),
    ]
    evidence: Annotated[
        list[EvidencePick],
        Field(
            default_factory=list,
            description="근거로 쓴 배움 기록 · 관심. 없으면 비운다 — 책 자체는 코드가 근거로 단다",
        ),
    ]
    reason: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "이 아이에게 이 책인 이유 한두 문장. 조회한 기록과 제목 · 형태로만. "
                "책 내용을 지어내지 않는다. 화면에 그대로 나간다"
            ),
        ),
    ]


class ProposeBooksArgs(ToolArgs):
    # 낼 수 있는 책이 3권보다 적으면 그만큼만 낸다. 0권 제출은 검색한 뒤에만 받는다(코드)
    items: Annotated[
        list[BookItem],
        Field(
            default_factory=list,
            max_length=MAX_SUGGESTIONS,
            description=(
                f"책 {MAX_SUGGESTIONS}권. search_books 결과에 낼 책이 모자라면 있는 만큼, "
                "하나도 없으면 비운다"
            ),
        ),
    ]
