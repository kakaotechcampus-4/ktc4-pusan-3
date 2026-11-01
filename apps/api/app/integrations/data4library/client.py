"""도서관 정보나루(data4library.kr) 도서 검색 — Growth `search_books` 의 원문.

이 모듈만 정보나루 서버를 안다. 요청 주소 · 인자 · 필드 이름 옮기기 · 외부 오류 변환 · 검색어
캐시가 여기 모여 있고, 밖으로는 `core/book_raw.py` 의 `RawBook` 만 나간다. 아동 도서인지 · 책 형태 ·
월령은 Growth `library.py` 가 정한다 — 여기서는 해석하지 않는다.

실제 응답으로 확인한 것 (`tests/fixtures/data4library_srch_books_sample.json`)
  - 결과는 `response.docs[].doc` 에 온다. 쓰는 칸은 `FIELDS` 다
  - 키가 틀리거나 한도를 넘으면 HTTP 200 에 `response` 없이 최상위 `error` · `errCode` 가 온다
  - 부가기호는 5자리 숫자, KDC 분류번호는 `813.8` 꼴이다(빈 문자열도 온다) —
    `rules/book_form.py` 가 읽는 모양. 발행년도는 없는 책이 있다

검색어 캐시 (growth_agent_own_table.md §2)
  - 키는 검색어 하나(앞뒤 공백을 지우고 사이 공백을 한 칸으로)이고, 찾는 방식(`keyword` ·
    `title`)마다 접두어가 다르다(`CACHE_PREFIX` · `TITLE_CACHE_PREFIX`). 월령 · 형태는 넣지 않는다 —
    Growth 가 캐시 뒤에서 건다
  - 값은 정보나루 결과 순서 그대로의 원문 행 JSON, 50권까지 · `SETEX` 30일. 책 정보 표는
    두지 않는다
  - Redis 가 없거나(`REDIS_URL` 없음) 읽기 · 쓰기가 실패하면 캐시 없이 정보나루를 바로 부른다 —
    캐시는 호출을 줄일 뿐이라 없어도 깨지는 동작이 없다. 실패 · 오류 응답은 담지 않는다. 0권 응답은
    담는다

🚨 인증키는 요청 **주소**에 들어간다. httpx 예외 문구에도 그 주소가 있다. 그래서 밖으로는
   `UpstreamUnavailable` 만 나가고, 문구에는 어느 단계에서 실패했는지(예외 종류 · 상태 코드)까지만
   싣는다. 원인 예외는 `from None` 으로 끊는다. 응답 본문 · 오류 문자열 · 검색어도 싣지 않는다.
"""

import dataclasses
import json
import logging
from typing import Any, Literal, Protocol

import httpx
import redis

from app.core.book_raw import RawBook, UpstreamUnavailable

log = logging.getLogger(__name__)

BASE_URL = "https://data4library.kr/api/srchBooks"

# 내부 이름(`RawBook` 칸) → 정보나루 칸. 실제 응답 픽스처로 검증한다
FIELDS: dict[str, str] = {
    "isbn13": "isbn13",
    "title": "bookname",
    "authors": "authors",
    "publisher": "publisher",
    "publication_year": "publication_year",
    "addition_symbol": "addition_symbol",
    "class_no": "class_no",
    "cover_url": "bookImageURL",
}

# 검색어마다 한 쪽으로 받는 책 수. Growth `tools/books.SEARCH_POOL` 과 같은 값이다 — 적게 담으면
# 영아에게 나갈 보드북이 모자란다
PAGE_SIZE = 50
# 응답을 기다리는 시간(연결은 절반). 재시도는 하지 않는다 — Growth run 은 20초 안에서 돈다
HTTP_TIMEOUT_S = 3.0
# 값 칸을 바꾸면 v 를 올린다 — 옛 캐시를 읽지 않게
CACHE_PREFIX = "book:q:v1:"
# 제목 검색(`title`)은 같은 낱말이어도 결과가 달라서 키를 따로 둔다
TITLE_CACHE_PREFIX = "book:t:v1:"
CACHE_TTL_S = 30 * 24 * 3600


class KeywordCache(Protocol):
    """검색어 캐시가 쓰는 redis 동기 클라이언트의 두 메서드."""

    def get(self, name: str) -> Any: ...

    def setex(self, name: str, time: int, value: str) -> Any: ...


class Data4LibraryBooks:
    """정보나루 키워드 검색 + 검색어 캐시. Growth `RawBookSearch` 포트의 실제 구현.

    호출마다 httpx 클라이언트를 열고 닫는다 — 캐시가 대부분을 받아 정보나루를 부르는 일이 드물어서
    프로세스에 하나를 두지 않는다(날씨 어댑터와 다르다). 테스트는 `transport` 로 MockTransport 를
    넣는다. `cache` 는 api 가 넘긴 `infra/redis.get_redis()` 의 동기 클라이언트다 — integrations 는
    infra 를 import 하지 않는다. 로컬 Redis 왕복이라 이벤트 루프를 잠깐 막는 것은 idempotency 와
    같이 받아들인다.
    """

    def __init__(
        self,
        *,
        auth_key: str,
        cache: KeywordCache | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not auth_key.strip():
            raise ValueError("정보나루 인증키가 비었다 — 키가 없으면 어댑터를 만들지 않는다")
        self._key = auth_key.strip()
        self._cache = cache
        self._transport = transport

    async def search(
        self, *, keyword: str, limit: int, field: Literal["keyword", "title"] = "keyword"
    ) -> tuple[RawBook, ...]:
        query = " ".join(keyword.split())
        if not query:
            raise ValueError("검색어가 비었다")
        prefix = TITLE_CACHE_PREFIX if field == "title" else CACHE_PREFIX
        cached = self._read(prefix + query)
        if cached is not None:
            return cached[:limit]
        rows = await self._fetch(query, field)
        self._write(prefix + query, rows)
        return rows[:limit]

    def _read(self, key: str) -> tuple[RawBook, ...] | None:
        if self._cache is None:
            return None
        try:
            raw = self._cache.get(key)
        except redis.RedisError as exc:
            log.warning("도서 검색어 캐시 읽기 실패 error=%s", type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            return tuple(RawBook(**item) for item in json.loads(raw))
        except (TypeError, ValueError):
            return None  # 칸이 바뀐 옛 값 — 버리고 다시 받는다

    def _write(self, key: str, rows: tuple[RawBook, ...]) -> None:
        if self._cache is None:
            return
        value = json.dumps([dataclasses.asdict(row) for row in rows], ensure_ascii=False)
        try:
            self._cache.setex(key, CACHE_TTL_S, value)
        except redis.RedisError as exc:
            log.warning("도서 검색어 캐시 쓰기 실패 error=%s", type(exc).__name__)

    async def _fetch(self, query: str, field: str) -> tuple[RawBook, ...]:
        params = {
            "authKey": self._key,
            field: query,
            "pageNo": 1,
            "pageSize": PAGE_SIZE,
            "format": "json",
        }
        timeout = httpx.Timeout(HTTP_TIMEOUT_S, connect=HTTP_TIMEOUT_S / 2)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout) as http:
                response = await http.get(BASE_URL, params=params)
        except Exception as exc:  # 연결 · 시간 초과 — 문구에 요청 주소(키)가 들어 있다
            raise UpstreamUnavailable(f"정보나루 요청 실패 {type(exc).__name__}") from None
        if response.status_code != 200:
            raise UpstreamUnavailable(f"정보나루 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise UpstreamUnavailable("정보나루 응답이 JSON 이 아니다") from None
        if isinstance(payload, dict) and payload.get("error") is not None:
            # 키가 틀리거나 한도를 넘으면 최상위에 온다. 오류 문자열은 싣지 않는다 — 신뢰할 수 없는
            # 글이고 키가 되돌아올 수 있다
            raise UpstreamUnavailable("정보나루 오류 응답")
        body = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            raise UpstreamUnavailable("정보나루 응답 모양이 다르다")
        if body.get("error") is not None:
            raise UpstreamUnavailable("정보나루 오류 응답")
        docs = body.get("docs", [])
        if not isinstance(docs, list):
            raise UpstreamUnavailable("정보나루 응답 모양이 다르다")
        return tuple(row for item in docs if (row := _row(item)) is not None)


def _row(item: Any) -> RawBook | None:
    doc = item.get("doc") if isinstance(item, dict) else None
    if not isinstance(doc, dict):
        raise UpstreamUnavailable("정보나루 응답 모양이 다르다")
    values = {name: _text(doc.get(field)) for name, field in FIELDS.items()}
    if not values["isbn13"] or not values["title"]:
        return None  # ISBN · 서명이 없으면 검색 결과와 대조할 수 없다
    return RawBook(**values)


def _text(value: Any) -> str | None:
    """빈 문자열 · 없는 칸은 None. 값은 해석하지 않는다."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
