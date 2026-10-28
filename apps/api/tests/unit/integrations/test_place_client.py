"""장소 표준데이터 integration — 전국도시공원정보표준데이터 · 전국도서관표준데이터 (#516).

공공데이터포털을 부르지 않는다. httpx.MockTransport 로 응답을 바꿔 끼우고 호출 여부와 입력값을 함께
검증한다 (apps/api/CLAUDE.md 자동 검증). 응답 모양은 2026-10-27 실제 응답의 칸 이름 · 타입을 따랐고
값은 지어낸 것이다 — 실제 장소 이름 · 좌표를 넣지 않는다.

여기서 지키는 것 넷.
    ① 요청이 쪽수대로 나가고 받은 행 수가 totalCount 와 다르면 실패한다 — 일부만 실으면 빠진 곳이
       "문 닫은 곳" 으로 지워진다
    ② 화이트리스트 칸만 옮긴다 — 전화번호 · 관리기관은 원문 행에 칸이 없다. 주소는 공원 중복을
       가리는 데만 쓴다
    ③ 어떻게 실패하든 PlaceApiError 하나로 모이고, 문구에 키 · 주소 · 응답 본문이 없다
    ④ Encoding 키도 Decoding 키도 주소에는 한 번만 인코딩돼 나간다
"""

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.place_raw import RawPlaceRow
from app.integrations.place.client import (
    KIDS_MAX_PAGES,
    KIDS_URL,
    LIBRARY_MAX_PAGES,
    LIBRARY_URL,
    PAGE_ROWS,
    PARK_MAX_PAGES,
    PARK_URL,
    PlaceApiError,
    PlaceStdClient,
)

# 가짜 키다 — 실제 키를 픽스처 · 테스트에 넣지 않는다 (루트 CLAUDE.md §9)
KEY = "test+service/key=="
ENCODED_KEY = "test%2Bservice%2Fkey%3D%3D"


def build_client(handler: Any, *, key: str = KEY) -> PlaceStdClient:
    return PlaceStdClient(
        service_key=key, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def ok(payload: Any) -> httpx.Response:
    return httpx.Response(200, json=payload)


def page_of(items: Any, *, total: int, page: int = 1) -> dict[str, Any]:
    return {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {
            "items": {"item": items},
            "numOfRows": PAGE_ROWS,
            "pageNo": page,
            "totalCount": total,
        },
    }


def park(no: str, **over: str) -> dict[str, str]:
    return {
        "manageNo": no,
        "parkNm": f"시험공원{no}",
        "parkSe": "어린이공원",
        "rdnmadr": "시험시 시험로 1",
        "lnmadr": "시험시 시험동 1",
        "latitude": "35.1000",
        "longitude": "129.0000",
        "parkAr": "1500",
        "amsmtFclty": "그네, 미끄럼틀",
        "institutionNm": "시험시청",
        "phoneNumber": "000-0000-0000",
        "referenceDate": "2026-06-30",
        "insttCode": "0000000",
        "insttNm": "시험시",
    } | over


def library(name: str, **over: str) -> dict[str, str]:
    return {
        "lbrryNm": name,
        "ctprvnNm": "시험도",
        "signguNm": "시험시",
        "lbrrySe": "공공도서관",
        "rdnmadr": "시험시 시험로 2",
        "operInstitutionNm": "시험시청",
        "phoneNumber": "000-0000-0000",
        "homepageUrl": "https://example.invalid",
        "latitude": "35.2000",
        "longitude": "129.1000",
        "referenceDate": "2026-05-31",
        "insttCode": "0000000",
        "insttNm": "시험시",
    } | over


def facility(sn: str, code: str = "A013", **over: str) -> dict[str, str | None]:
    """행안부 어린이놀이시설 한 행 — 실제 응답의 칸 이름 · 타입을 따랐고 값은 지어냈다."""
    return {
        "pfctSn": sn,
        "pfctNm": f"시험놀이시설{sn}",
        "zip": "00000",
        "lotnoAddr": None,
        "ronaAddr": "시험시 시험로 3",
        "instlYmd": "20200101",
        "instlPlaceCd": code,
        "instlPlaceCdNm": "놀이제공영업소",
        "prvtPblcYnCd": "C001",
        "operYnCd": "B001",
        "operYnCdNm": "운영",
        "idrodrCd": "O001",
        "idrodrCdNm": "실내",
        "rgnCd": "1111010100",
        "rgnCdNm": "시험시 시험구 시험동",
        "latCrtsVl": "35.3000",
        "lotCrtsVl": "129.2000",
        "exfcYn": "N",
    } | over


def kids_handler(rows_by_code: dict[str, list[dict]], seen: list[httpx.Request] | None = None):
    """설치장소 코드로 거르고 1,000행씩 나눠 주는 서버. 응답은 `response` 로 감싸 온다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        q = query(request)
        rows = rows_by_code.get(q["instlPlaceCd"][0], [])
        page = int(q["pageIndex"][0])
        items = rows[(page - 1) * PAGE_ROWS : page * PAGE_ROWS]
        body = {"items": items, "pageIndex": page, "recordCountPerPage": PAGE_ROWS}
        body |= {"totalCnt": len(rows), "totalPageCnt": -(-len(rows) // PAGE_ROWS)}
        return ok({"response": {"header": {"resultCode": "00", "resultMsg": "OK"}, "body": body}})

    return handler


def query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


# ── ① 요청 · 쪽수 ──────────────────────────────────────────────────────
async def test_요청은_JSON_쪽번호_쪽크기를_담아_나간다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(page_of([park("11110-00001")], total=1))

    await build_client(handler).parks()

    [request] = seen
    assert str(request.url).startswith(PARK_URL)
    q = query(request)
    assert q["serviceKey"] == [KEY]
    assert q["type"] == ["json"]
    assert q["pageNo"] == ["1"]
    assert q["numOfRows"] == [str(PAGE_ROWS)]


async def test_도서관은_도서관_주소로_부른다() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return ok(page_of([library("시험도서관")], total=1))

    await build_client(handler).libraries()

    assert seen[0].startswith(LIBRARY_URL)


async def test_쪽을_차례로_받아_이어_붙인다() -> None:
    pages: list[int] = []
    total = PAGE_ROWS * 2 + 3

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(query(request)["pageNo"][0])
        pages.append(page)
        start = (page - 1) * PAGE_ROWS
        count = min(PAGE_ROWS, total - start)
        items = [park(f"11110-{start + k:05d}") for k in range(count)]
        return ok(page_of(items, total=total, page=page))

    rows = await build_client(handler).parks()

    assert pages == [1, 2, 3]
    assert [row.manage_no for row in rows] == [f"11110-{k:05d}" for k in range(total)]


async def test_받은_행_수가_totalCount_와_다르면_실패한다() -> None:
    # 중간 쪽이 덜 오면 그 장소들이 "이번 받기에 없는 행" 으로 지워진다
    def handler(request: httpx.Request) -> httpx.Response:
        return ok(page_of([park("11110-00001")], total=2))

    with pytest.raises(PlaceApiError, match="받은 행 수"):
        await build_client(handler).parks()


@pytest.mark.parametrize(
    ("fetch", "max_pages"), [("parks", PARK_MAX_PAGES), ("libraries", LIBRARY_MAX_PAGES)]
)
async def test_쪽수가_소스의_상한을_넘으면_받지_않는다(fetch: str, max_pages: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return ok(page_of([park("11110-00001")], total=PAGE_ROWS * max_pages + 1))

    with pytest.raises(PlaceApiError, match="상한"):
        await getattr(build_client(handler), fetch)()
    assert calls == 1


def _pages_of(total: int):
    """`total` 행을 쪽마다 나눠 주는 handler 와 부른 쪽 번호."""
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(query(request)["pageNo"][0])
        pages.append(page)
        start = (page - 1) * PAGE_ROWS
        count = max(0, min(PAGE_ROWS, total - start))
        items = [park(f"11110-{start + k:05d}") for k in range(count)]
        return ok(page_of(items, total=total, page=page))

    return handler, pages


@pytest.mark.parametrize(("max_pages", "allowed"), [(2, False), (3, True)])
async def test_쪽수_상한은_부르는_쪽이_소스마다_정한다(max_pages: int, allowed: bool) -> None:
    # 쪽수가 많은 소스(어린이놀이시설 약 86쪽)는 공원 · 도서관보다 큰 상한을 둔다
    handler, pages = _pages_of(PAGE_ROWS * 2 + 1)  # 3쪽
    client = build_client(handler)

    if allowed:
        rows = await client._all(PARK_URL, what="시험", max_pages=max_pages)
        assert len(rows) == PAGE_ROWS * 2 + 1
        assert pages == [1, 2, 3]
    else:
        with pytest.raises(PlaceApiError, match="상한"):
            await client._all(PARK_URL, what="시험", max_pages=max_pages)
        assert pages == [1]


@pytest.mark.parametrize(
    "items",
    [
        {"item": []},
        {},
        "",
        None,
    ],
)
async def test_0건은_빈_튜플이다(items: Any) -> None:
    # 0건을 장애로 볼지는 적재가 정한다(남은 행 0건이면 쓰지 않는다)
    payload = {"header": {"resultCode": "00"}, "body": {"items": items, "totalCount": 0}}

    assert await build_client(lambda request: ok(payload)).parks() == ()


async def test_한_행이면_목록_대신_객체로_와도_읽는다() -> None:
    rows = await build_client(lambda request: ok(page_of(park("11110-00001"), total=1))).parks()

    assert [row.manage_no for row in rows] == ["11110-00001"]


# ── ② 화이트리스트 ─────────────────────────────────────────────────────
async def test_공원은_화이트리스트_칸만_옮긴다() -> None:
    [row] = await build_client(lambda request: ok(page_of([park("11110-00001")], total=1))).parks()

    assert row == RawPlaceRow(
        name="시험공원11110-00001",
        kind="어린이공원",
        latitude="35.1000",
        longitude="129.0000",
        reference_date="2026-06-30",
        manage_no="11110-00001",
        address="시험시 시험동 1",
        amusement="그네, 미끄럼틀",
        area="1500",
    )


async def test_도서관은_번호_칸이_없다() -> None:
    [row] = await build_client(
        lambda request: ok(page_of([library("시험도서관")], total=1))
    ).libraries()

    assert row == RawPlaceRow(
        name="시험도서관",
        kind="공공도서관",
        latitude="35.2000",
        longitude="129.1000",
        reference_date="2026-05-31",
        manage_no=None,
    )


def test_원문_행에는_전화번호_관리기관_칸이_없다() -> None:
    # 주소는 관리번호가 겹친 공원을 가리는 데만, 유희시설은 놀이터 여부를 정하는 데만 쓰고 표에는
    # 싣지 않는다(place_values 테스트)
    fields = set(RawPlaceRow.__dataclass_fields__)

    assert fields == {
        "name",
        "kind",
        "latitude",
        "longitude",
        "reference_date",
        "manage_no",
        "address",
        "amusement",
        "area",
        "indoor",
        "operating",
        "region_code",
        "safety_excluded",
    }


async def test_빈_칸은_None_이다() -> None:
    sparse = park(
        "11110-00001", latitude="", longitude="  ", referenceDate="", amsmtFclty="", parkAr=" "
    )

    [row] = await build_client(lambda request: ok(page_of([sparse], total=1))).parks()

    assert (row.latitude, row.longitude, row.reference_date) == (None, None, None)
    assert (row.amusement, row.area) == (None, None)


# ── ③ 실패 ────────────────────────────────────────────────────────────
def _no_secret(error: PlaceApiError) -> None:
    text = str(error)
    for secret in (KEY, ENCODED_KEY, "serviceKey", "api.data.go.kr", "SECRET-BODY"):
        assert secret not in text
    # 원인 예외(주소가 든 httpx 오류)가 traceback 에 따라붙지 않는다
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="SECRET-BODY"),
        httpx.Response(200, text="<OpenAPI_ServiceResponse>SECRET-BODY</OpenAPI_ServiceResponse>"),
        httpx.Response(200, json={"header": {"resultCode": "30", "resultMsg": "SECRET-BODY"}}),
        httpx.Response(200, json={"header": {"resultCode": "00"}, "body": "SECRET-BODY"}),
        httpx.Response(200, json={"header": {"resultCode": "00"}, "body": {"totalCount": "x"}}),
        httpx.Response(200, json={"response": {"header": {"resultCode": "00"}}}),
        httpx.Response(200, json=["SECRET-BODY"]),
    ],
)
async def test_실패는_PlaceApiError_하나로_모이고_키_본문을_싣지_않는다(
    response: httpx.Response,
) -> None:
    with pytest.raises(PlaceApiError) as caught:
        await build_client(lambda request: response).parks()

    _no_secret(caught.value)


async def test_연결_실패도_주소를_싣지_않는다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"연결 실패 {request.url}")

    with pytest.raises(PlaceApiError) as caught:
        await build_client(handler).libraries()

    _no_secret(caught.value)
    assert "ConnectError" in str(caught.value)


async def test_행_모양이_다르면_실패한다() -> None:
    with pytest.raises(PlaceApiError, match="모양"):
        await build_client(lambda request: ok(page_of(["SECRET-BODY"], total=1))).parks()


async def test_키가_비면_부르지_않는다() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return ok(page_of([], total=0))

    with pytest.raises(PlaceApiError, match="인증키"):
        await build_client(handler, key="  ").parks()
    assert calls == 0


# ── 어린이놀이시설 — 설치장소 코드로 서버에서 거른다 ────────────────────
async def test_놀이시설은_설치장소_코드마다_걸러_받는다() -> None:
    seen: list[httpx.Request] = []
    handler = kids_handler(
        {"A013": [facility("1"), facility("2")], "A092": [facility("3", "A092")]}, seen
    )

    rows = await build_client(handler).kids_facilities(place_codes=["A013", "A092"])

    assert [row.manage_no for row in rows] == ["1", "2", "3"]
    for request in seen:
        assert str(request.url).startswith(KIDS_URL)
        q = query(request)
        assert q["serviceKey"] == [KEY]
        assert q["returnType"] == ["json"]
        assert q["recordCountPerPage"] == [str(PAGE_ROWS)]
    assert [query(request)["instlPlaceCd"] for request in seen] == [["A013"], ["A092"]]


async def test_놀이시설은_쪽을_넘겨_받는다() -> None:
    seen: list[httpx.Request] = []
    rows = [facility(str(k)) for k in range(PAGE_ROWS + 5)]

    found = await build_client(kids_handler({"A013": rows}, seen)).kids_facilities(
        place_codes=["A013"]
    )

    assert len(found) == PAGE_ROWS + 5
    assert [query(request)["pageIndex"] for request in seen] == [["1"], ["2"]]


async def test_놀이시설은_화이트리스트_칸만_옮긴다() -> None:
    handler = kids_handler({"A092": [facility("7", "A092", idrodrCd="O002")]})

    [row] = await build_client(handler).kids_facilities(place_codes=["A092"])

    assert row == RawPlaceRow(
        name="시험놀이시설7",
        kind="A092",
        latitude="35.3000",
        longitude="129.2000",
        reference_date=None,  # 데이터기준일자 칸이 없다
        manage_no="7",
        indoor="O002",
        operating="B001",
        region_code="1111010100",
        safety_excluded=False,
    )
    assert row.address is None  # 주소는 옮기지 않는다


async def test_안전관리_대상_제외_표시를_옮긴다() -> None:
    handler = kids_handler({"A013": [facility("8", exfcYn="Y")]})

    [row] = await build_client(handler).kids_facilities(place_codes=["A013"])

    assert row.safety_excluded is True


async def test_설치장소_필터가_안_먹으면_실패한다() -> None:
    # 서버가 필터를 무시하고 다른 설치장소를 섞어 주면 주택단지 놀이터까지 실린다
    handler = kids_handler({"A013": [facility("1"), facility("2", "A010")]})

    with pytest.raises(PlaceApiError, match="필터"):
        await build_client(handler).kids_facilities(place_codes=["A013"])


async def test_놀이시설_쪽수가_상한을_넘으면_받지_않는다() -> None:
    # 필터가 안 먹어 전체(86쪽)가 오는 경우도 여기서 걸린다
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = {"items": [facility("1")], "totalCnt": PAGE_ROWS * KIDS_MAX_PAGES + 1}
        return ok({"response": {"header": {"resultCode": "00"}, "body": body}})

    with pytest.raises(PlaceApiError, match="상한"):
        await build_client(handler).kids_facilities(place_codes=["A013"])
    assert calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"header": {"resultCode": "00"}, "body": {"items": [], "totalCnt": 0}},  # 감싸지 않았다
        {"response": {"header": {"resultCode": "03", "resultMsg": "SECRET-BODY"}}},
        {"response": {"header": {"resultCode": "00"}, "body": "SECRET-BODY"}},
        ["SECRET-BODY"],
    ],
)
async def test_놀이시설_응답_모양이_다르면_실패하고_키를_싣지_않는다(payload) -> None:
    with pytest.raises(PlaceApiError) as caught:
        await build_client(lambda request: ok(payload)).kids_facilities(place_codes=["A013"])

    _no_secret(caught.value)


# ── ④ 키 인코딩 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("key", [KEY, ENCODED_KEY])
async def test_Encoding_키도_Decoding_키도_한_번만_인코딩된다(key: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(page_of([], total=0))

    await build_client(handler, key=key).parks()

    # 서버가 푼 값이 원래 키와 같다. 두 번 인코딩되면 %252B 가, 안 하면 + 가 공백이 된다
    assert query(seen[0])["serviceKey"] == [KEY]
    assert "%25" not in seen[0].url.query.decode()
