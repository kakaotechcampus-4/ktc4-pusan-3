"""도서관 정보나루 원문 계약. 어댑터(`app/integrations/data4library/`)가 싣고 Growth 가 읽는 모양.

`app/integrations/` 는 core 만 import 할 수 있어서 어댑터와 Agent 가 함께 보는 모양은 여기 둔다
(`core/weather_raw.py` 와 같은 자리). 값은 전부 API 원문 문자열이다 — 어댑터는 호출 · 타임아웃 ·
필드 이름 옮기기 · 검색어 캐시까지만 하고 해석하지 않는다. 아동 도서인지 · 책 형태(`form`) · 저자
표기 다듬기는 Growth `library.py` 가 `rules/book_form.py` 로 테스트와 함께 맡는다.
"""

from dataclasses import dataclass


class UpstreamUnavailable(Exception):
    """정보나루 조회 실패. 결과가 0건인 것과 다르다.

    빈 목록으로 바꾸지 않는다 — "그 낱말의 책이 없다" 로 읽힌다. 메시지에 검색어 · 요청 주소를 넣지
    않는다 — 주소에 인증키가 들어간다. 어댑터가 던지고 Growth 가 받아 `closed.book_api` 로 닫는다.
    Growth 포트(`store/ports.py`)는 이 클래스를 그대로 다시 내보낸다.
    """


@dataclass(frozen=True)
class RawBook:
    """도서 검색 결과 한 권의 원문. 빈 칸은 None. ISBN · 서명이 없는 행은 어댑터가 싣지 않는다."""

    isbn13: str
    title: str  # 서명
    authors: str | None = None  # 저자 칸 원문 — 여러 사람 · 역할이 섞여 온다
    publisher: str | None = None
    publication_year: str | None = None
    addition_symbol: str | None = None  # ISBN 부가기호. 첫 자리가 독자대상(7 = 아동)
    class_no: str | None = None  # KDC 분류번호. 첫 자리가 대분류(8 = 문학)
    cover_url: str | None = None
