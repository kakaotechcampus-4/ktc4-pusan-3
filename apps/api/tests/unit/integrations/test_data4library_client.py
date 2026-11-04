"""도서관 정보나루 integration — 정보나루 서버를 부르지 않는다.

httpx.MockTransport 로 응답을 바꿔 끼우고 호출 여부와 입력값을 함께 검증한다 (apps/api/CLAUDE.md
자동 검증). 응답은 `tests/fixtures/data4library_srch_books_sample.json` — 실제 응답에서 쓰는 칸만
남긴 공개 서지 정보다(요청 블록 · 인증키 없음).

여기서 지키는 것 넷.
    ① 요청은 검색어(공백 정리) · 쪽 크기 · 형식으로 한 번 나간다. 검색어 말고 아이 값은 없다
    ② 실제 응답의 칸이 원문으로 옮겨진다 — 칸 이름이 바뀌면 여기서 깨진다. 부가기호 · KDC 가
       GT-3 가 읽는 모양으로 오고, 실제 응답에서 보드북 · 그림책이 가려진다
    ③ 어떻게 실패하든 UpstreamUnavailable 하나로 모이고, 문구에 키 · 주소 · 검색어가 없다
    ④ 검색어 캐시 — 있으면 부르지 않고, 없으면 받아서 30일로 담는다. 실패 응답은 담지 않고 캐시가
       죽어도 정보나루로 간다
"""

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis

from app.core.book_raw import RawBook, UpstreamUnavailable
from app.integrations.data4library.client import (
    BASE_URL,
    CACHE_PREFIX,
    CACHE_TTL_S,
    FIELDS,
    PAGE_SIZE,
    TITLE_CACHE_PREFIX,
    Data4LibraryBooks,
)
from app.rules.book_form import classify_book_form

# 가짜 키다 — 실제 키를 픽스처 · 테스트에 넣지 않는다 (루트 CLAUDE.md §9)
KEY = "test-auth-key-0123456789abcdef"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "data4library_srch_books_sample.json"
SAMPLE = json.loads(FIXTURE.read_text(encoding="utf-8"))
DOCS = [item["doc"] for item in SAMPLE["response"]["docs"]]
USABLE = [doc for doc in DOCS if doc.get(FIELDS["isbn13"]) and doc.get(FIELDS["title"])]


class FakeCache:
    """redis 동기 클라이언트에서 어댑터가 쓰는 두 메서드만."""

    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    def get(self, key: str) -> str | None:
        if self.fail:
            raise redis.ConnectionError("down")
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        if self.fail:
            raise redis.ConnectionError("down")
        self.values[key] = value
        self.ttls[key] = ttl


def recording(payload: Any = SAMPLE, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    return handler, seen


def books(handler, *, cache: FakeCache | None = None) -> Data4LibraryBooks:
    return Data4LibraryBooks(auth_key=KEY, cache=cache, transport=httpx.MockTransport(handler))


class TestRequest:
    async def test_검색어_쪽크기_형식으로_한_번_부른다(self):
        handler, seen = recording()
        await books(handler).search(keyword="  아기   공룡 ", limit=10)

        (request,) = seen
        assert str(request.url.copy_with(query=None)) == BASE_URL
        params = request.url.params
        assert params["keyword"] == "아기 공룡"
        assert params["pageSize"] == str(PAGE_SIZE) and params["pageNo"] == "1"
        assert params["format"] == "json" and params["authKey"] == KEY

    async def test_제목_검색은_title_인자로_보낸다(self):
        handler, seen = recording()
        await books(handler).search(keyword="곰", limit=10, field="title")

        (request,) = seen
        params = request.url.params
        assert params["title"] == "곰" and "keyword" not in params
        assert params["authKey"] == KEY and params["format"] == "json"

    async def test_빈_검색어는_부르지_않는다(self):
        handler, seen = recording()
        with pytest.raises(ValueError):
            await books(handler).search(keyword="   ", limit=10)
        assert seen == []

    def test_빈_인증키로는_만들지_않는다(self):
        with pytest.raises(ValueError):
            Data4LibraryBooks(auth_key=" ", cache=None)


class TestContract:
    def test_픽스처에_쓰는_칸이_다_있다(self):
        # 발행년도처럼 없는 책이 있는 칸은 실제 응답에서도 빠진다 — 행마다가 아니라 합쳐서 본다
        assert DOCS, "픽스처에 책이 없다 — Task 0 Step 4 를 다시 돌린다"
        always = {
            FIELDS[name] for name in ("isbn13", "title", "authors", "addition_symbol", "class_no")
        }
        for doc in DOCS:
            assert always <= set(doc), sorted(always - set(doc))
        assert set(FIELDS.values()) <= set().union(*DOCS)

    async def test_실제_응답의_칸을_원문으로_옮긴다(self):
        handler, _ = recording()
        rows = await books(handler).search(keyword="공룡", limit=PAGE_SIZE)

        assert len(rows) == len(USABLE)
        for row, doc in zip(rows, USABLE, strict=True):
            assert row.isbn13 == str(doc[FIELDS["isbn13"]]).strip()
            assert row.title == str(doc[FIELDS["title"]]).strip()
            symbol = (doc.get(FIELDS["addition_symbol"]) or "").strip()
            assert row.addition_symbol == (symbol or None)
            class_no = (doc.get(FIELDS["class_no"]) or "").strip()
            assert row.class_no == (class_no or None)

    def test_부가기호와_분류번호는_GT3_이_읽는_모양이다(self):
        for doc in DOCS:
            symbol = (doc.get(FIELDS["addition_symbol"]) or "").strip()
            class_no = (doc.get(FIELDS["class_no"]) or "").strip()
            assert not symbol or re.fullmatch(r"\d{5}", symbol), symbol
            assert not class_no or re.fullmatch(r"\d{1,3}(\.\d+)?", class_no), class_no

    def test_실제_응답에서_보드북과_그림책이_가려진다(self):
        forms = {
            classify_book_form(
                title=str(doc.get(FIELDS["title"]) or ""),
                addition_symbol=doc.get(FIELDS["addition_symbol"]),
                class_no=doc.get(FIELDS["class_no"]),
            )
            for doc in DOCS
        }
        assert {"board", "picture"} <= forms


class TestFailure:
    @staticmethod
    def _connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"연결 실패 {request.url}", request=request)

    @pytest.mark.parametrize(
        "handler",
        [
            _connect_error,
            lambda request: httpx.Response(500, json={}),
            lambda request: httpx.Response(200, text="<error>key</error>"),
            # 실제 오류 응답은 response 없이 최상위에 error · errCode 가 온다 (HTTP 200)
            lambda request: httpx.Response(200, json={"errCode": "E1", "error": "잘못된 키"}),
            lambda request: httpx.Response(200, json={"response": {"error": "잘못된 키"}}),
            lambda request: httpx.Response(200, json={"foo": 1}),
            lambda request: httpx.Response(200, json={"response": {"docs": "x"}}),
            lambda request: httpx.Response(200, json={"response": {"docs": [{"x": 1}]}}),
        ],
    )
    async def test_어떻게_실패하든_UpstreamUnavailable_이고_키_검색어가_없다(self, handler):
        cache = FakeCache()
        with pytest.raises(UpstreamUnavailable) as caught:
            await books(handler, cache=cache).search(keyword="공룡", limit=10)

        text = str(caught.value)
        assert KEY not in text and "공룡" not in text and "data4library" not in text
        assert caught.value.__cause__ is None
        assert cache.values == {}  # 실패한 응답은 캐시에 담지 않는다


class TestCache:
    async def test_캐시에_있으면_정보나루를_부르지_않는다(self):
        cached = RawBook(isbn13="9788900000001", title="책1")
        cache = FakeCache()
        cache.values[CACHE_PREFIX + "공룡"] = json.dumps(
            [dataclasses.asdict(cached)], ensure_ascii=False
        )
        handler, seen = recording()

        assert await books(handler, cache=cache).search(keyword="공룡", limit=10) == (cached,)
        assert seen == []

    async def test_없으면_받아서_30일로_담고_다음엔_부르지_않는다(self):
        cache = FakeCache()
        handler, seen = recording()
        client = books(handler, cache=cache)

        first = await client.search(keyword="공룡", limit=2)
        again = await client.search(keyword=" 공룡 ", limit=PAGE_SIZE)

        assert len(seen) == 1
        assert CACHE_TTL_S == 30 * 24 * 3600 and cache.ttls == {CACHE_PREFIX + "공룡": CACHE_TTL_S}
        assert first == again[:2] and len(again) == len(USABLE)  # 캐시는 limit 보다 많이 담는다

    async def test_제목_검색은_캐시를_따로_둔다(self):
        cache = FakeCache()
        handler, seen = recording()
        client = books(handler, cache=cache)

        await client.search(keyword="곰", limit=10, field="title")
        await client.search(keyword="곰", limit=10, field="keyword")
        await client.search(keyword="곰", limit=10, field="title")

        assert len(seen) == 2  # 같은 낱말도 찾는 방식이 다르면 다른 결과다
        assert set(cache.values) == {TITLE_CACHE_PREFIX + "곰", CACHE_PREFIX + "곰"}

    async def test_캐시가_죽어도_정보나루로_간다(self):
        handler, seen = recording()
        rows = await books(handler, cache=FakeCache(fail=True)).search(keyword="공룡", limit=10)
        assert len(seen) == 1 and rows

    async def test_칸이_바뀐_옛_캐시는_버리고_다시_받는다(self):
        cache = FakeCache()
        cache.values[CACHE_PREFIX + "공룡"] = json.dumps([{"isbn": "x"}])
        handler, seen = recording()
        await books(handler, cache=cache).search(keyword="공룡", limit=10)
        assert len(seen) == 1

    async def test_캐시가_없으면_바로_부른다(self):
        handler, seen = recording()
        await books(handler, cache=None).search(keyword="공룡", limit=10)
        await books(handler, cache=None).search(keyword="공룡", limit=10)
        assert len(seen) == 2
