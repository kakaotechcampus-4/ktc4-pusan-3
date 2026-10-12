"""식약처 식품영양성분DB integration — 명세 data.go.kr 15127578 (FoodNtrCpntDbInfo03).

영양DB 서버를 부르지 않는다. httpx.MockTransport 로 응답을 바꿔 끼우고 **호출 여부와 입력값을
함께** 검증한다 (apps/api/CLAUDE.md 자동 검증). 응답은 `tests/fixtures/mfds_nutri_sample.json` —
실제 응답 3행에서 필드를 줄인 것이다(영양DB 는 이용허락범위 제한 없음).

여기서 지키는 것 넷.
    ① 요청이 이름 · 품목대표 필터 · 쪽수대로 나간다 — 포함 검색이라 필터 없이는 우유가 2,809행이다
    ② 실제 응답의 AMT_NUM 칸이 내부 이름으로 옮겨진다 — 칼슘과 나트륨이 바뀌어도 숫자는 그럴듯해서
       어디서도 안 걸리므로, 에너지 = 단백질·지방·탄수화물 합이 맞는지로 옮겨 읽기를 잡는다
    ③ 어떻게 실패하든 MfdsApiError 하나로 모이고, 문구에 키 · 주소 · 응답 본문이 없다
    ④ 쪽수에는 상한이 있다 — 흔한 낱말은 수백 행이라 끝없이 넘기지 않는다
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.agents.food.menu_resolver import MenuResolver, NotFoundMemory, Resolved, Unresolved
from app.agents.food.store import InMemoryMenuCatalog
from app.integrations.mfds.client import (
    BASE_URL,
    HTTP_TIMEOUT_S,
    MAX_PAGES,
    NUTRIENT_FIELDS,
    PAGE_ROWS,
    MfdsApiError,
    MfdsNutriClient,
    mfds_nutri_http,
)

# 가짜 키다 — 실제 키를 픽스처 · 테스트에 넣지 않는다 (루트 CLAUDE.md §9)
KEY = "test-service-key-0123456789abcdef"
SAMPLE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "mfds_nutri_sample.json").read_text(
        encoding="utf-8"
    )
)


def build_client(handler: Any) -> MfdsNutriClient:
    return MfdsNutriClient(
        service_key=KEY, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


def page_of(items: list[dict[str, Any]], *, total: int | None = None, page: int = 1) -> dict:
    return {
        "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
        "body": {
            "pageNo": page,
            "totalCount": len(items) if total is None else total,
            "numOfRows": PAGE_ROWS,
            "items": items,
        },
    }


def item(code: str, name: str = "된장찌개") -> dict[str, Any]:
    return {**SAMPLE["body"]["items"][0], "FOOD_CD": code, "FOOD_NM_KR": name}


# ── ① 요청 ────────────────────────────────────────────────────────────
async def test_요청은_이름_품목대표_필터_쪽수를_담아_나간다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(SAMPLE)

    await build_client(handler).nutrition_rows(name="된장찌개")

    [request] = seen
    assert request.method == "GET"
    assert str(request.url).startswith(BASE_URL)
    query = parse_qs(request.url.query.decode())
    assert query["serviceKey"] == [KEY]
    assert query["type"] == ["json"]
    assert query["FOOD_NM_KR"] == ["된장찌개"]
    # 포함 검색이라 필터가 없으면 우유가 2,809행이고 첫 100행에 정확히 같은 이름이 없다
    assert query["DB_CLASS_NM"] == ["품목대표"]
    assert query["pageNo"] == ["1"]
    assert query["numOfRows"] == [str(PAGE_ROWS)]


async def test_이름이_비면_부르지_않는다() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return ok(SAMPLE)

    assert await build_client(handler).nutrition_rows(name="  ") == ()
    assert calls == 0


# ── ② 실제 응답 → 원문 행 ─────────────────────────────────────────────
async def test_실제_응답을_원문_행으로_옮긴다() -> None:
    rows = await build_client(lambda request: ok(SAMPLE)).nutrition_rows(name="된장찌개")

    assert [row.name for row in rows] == ["된장찌개", "된장찌개_냉이", "우유"]
    dish = rows[0]
    assert dish.food_code == "D106-275000000-0001"
    assert (dish.db_group, dish.db_class) == ("음식", "품목대표")
    assert dish.category == "찌개 및 전골류"
    assert dish.ref_name == "된장찌개"
    assert dish.source == "식품의약품안전처"
    assert dish.researched_on == "2018-12-31"
    assert dish.food_weight == "200.000g"
    # 값은 원문 문자열 그대로다 — 숫자로 바꾸는 것은 Agent(nutrition_source)가 한다
    assert dish.nutrients == {
        "energy": "46.00",
        "protein": "3.38",
        "fat": "1.63",
        "carbohydrate": "4.44",
        "sugars": "0.00",
        "calcium": "21.00",
        "iron": "0.70",
        "sodium": "318.000",
        "saturated_fat": "0.27",
    }


async def test_원재료성_행은_1인분_중량이_없다() -> None:
    rows = await build_client(lambda request: ok(SAMPLE)).nutrition_rows(name="우유")

    milk = rows[2]
    assert (milk.db_group, milk.db_class) == ("원재료성", "품목대표")
    assert milk.food_weight is None
    assert milk.nutrients["calcium"] == "118.000"
    assert milk.nutrients["sodium"] == "40.00"


async def test_옮겨_읽지_않았다_에너지는_세_영양소_열량_합과_맞는다() -> None:
    # 열량 = 단백질·탄수화물 4kcal/g + 지방 9kcal/g. 칸이 한 칸이라도 밀리면 이 식이 깨진다.
    # (실제 응답 3행으로 확인한 식이다 — 계수는 일반 열량 환산계수)
    rows = await build_client(lambda request: ok(SAMPLE)).nutrition_rows(name="x")

    for row in rows:
        n = {key: float(value) for key, value in row.nutrients.items() if value is not None}
        estimated = n["protein"] * 4 + n["carbohydrate"] * 4 + n["fat"] * 9
        assert estimated == pytest.approx(n["energy"], rel=0.08), row.name


async def test_포화지방은_콜레스테롤_칸이_아니다() -> None:
    # 포화지방(AMT_NUM24)과 콜레스테롤(AMT_NUM23)은 이웃이다.
    # 우유: 포화지방 2.17g · 콜레스테롤 10.13mg
    rows = await build_client(lambda request: ok(SAMPLE)).nutrition_rows(name="우유")

    assert rows[2].nutrients["saturated_fat"] == "2.17"
    assert "10.13" not in rows[2].nutrients.values()


def test_영양소_이름과_API_칸_짝() -> None:
    assert NUTRIENT_FIELDS == {
        "energy": "AMT_NUM1",
        "protein": "AMT_NUM3",
        "fat": "AMT_NUM4",
        "carbohydrate": "AMT_NUM6",
        "sugars": "AMT_NUM7",
        "calcium": "AMT_NUM9",
        "iron": "AMT_NUM10",
        "sodium": "AMT_NUM13",
        "saturated_fat": "AMT_NUM24",
    }


async def test_빈_칸과_없는_칸은_None_이다() -> None:
    sparse = {**item("S1"), "AMT_NUM7": "", "AMT_NUM24": None, "Z10500": ""}
    del sparse["AMT_NUM10"]

    [row] = await build_client(lambda request: ok(page_of([sparse]))).nutrition_rows(name="x")

    assert row.nutrients["sugars"] is None
    assert row.nutrients["saturated_fat"] is None
    assert row.nutrients["iron"] is None  # 칸이 아예 없다
    assert row.food_weight is None
    assert row.nutrients["energy"] == "46.00"  # 있는 칸은 그대로


async def test_결과가_없으면_빈_튜플이고_장애가_아니다() -> None:
    for payload in (
        page_of([], total=0),
        {"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": None}},
        {"header": {"resultCode": "00"}, "body": {"totalCount": 0}},
    ):
        assert await build_client(lambda request, p=payload: ok(p)).nutrition_rows(name="x") == ()


# ── ④ 쪽수 ────────────────────────────────────────────────────────────
async def test_한_쪽에_다_담기면_한_번만_부른다() -> None:
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pages.append(parse_qs(request.url.query.decode())["pageNo"][0])
        return ok(page_of([item("A"), item("B")]))

    assert len(await build_client(handler).nutrition_rows(name="x")) == 2
    assert pages == ["1"]


async def test_결과가_더_있으면_나머지_쪽을_받아_이어_붙인다() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(parse_qs(request.url.query.decode())["pageNo"][0])
        pages.append(page)
        start = (page - 1) * PAGE_ROWS
        count = PAGE_ROWS if page < 3 else 50
        return ok(page_of([item(f"C{start + k}") for k in range(count)], total=250, page=page))

    rows = await build_client(handler).nutrition_rows(name="x")

    assert sorted(pages) == [1, 2, 3]
    assert [row.food_code for row in rows] == [f"C{k}" for k in range(250)]  # 쪽 순서대로


async def test_쪽수에는_상한이_있다() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(parse_qs(request.url.query.decode())["pageNo"][0])
        pages.append(page)
        return ok(page_of([item(f"P{page}-{k}") for k in range(PAGE_ROWS)], total=5000, page=page))

    rows = await build_client(handler).nutrition_rows(name="국")

    assert sorted(pages) == list(range(1, MAX_PAGES + 1))
    assert (
        len(rows) == PAGE_ROWS * MAX_PAGES
    )  # 넘는 만큼은 받지 않는다 — Agent 가 못 찾으면 unresolved


async def test_뒤쪽이_실패하면_앞쪽만으로_돌려주지_않는다() -> None:
    # 정확히 같은 이름의 행이 빠진 채로 "없음" 처럼 보이면 안 된다
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(parse_qs(request.url.query.decode())["pageNo"][0])
        if page == 2:
            return httpx.Response(500)
        return ok(page_of([item(f"C{k}") for k in range(PAGE_ROWS)], total=250, page=page))

    with pytest.raises(MfdsApiError):
        await build_client(handler).nutrition_rows(name="x")


# ── ③ 실패 ────────────────────────────────────────────────────────────
def _assert_clean(exc: MfdsApiError) -> None:
    text = str(exc)
    assert KEY not in text
    assert "apis.data.go.kr" not in text
    assert "serviceKey" not in text
    # httpx 예외(주소가 든 문구)가 traceback 에 이어 보이지 않는다 — 원인이 없거나 숨겨졌다
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__


async def test_연결_실패는_주소와_키_없이_MfdsApiError() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # httpx 예외 문구에는 요청 주소(키)가 든다
        raise httpx.ConnectError(f"connect failed: {request.url}")

    with pytest.raises(MfdsApiError) as caught:
        await build_client(handler).nutrition_rows(name="된장찌개")

    _assert_clean(caught.value)
    assert "ConnectError" in str(caught.value)  # 종류만 남긴다


async def test_시간_초과도_MfdsApiError() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timeout {request.url}")

    with pytest.raises(MfdsApiError) as caught:
        await build_client(handler).nutrition_rows(name="x")

    _assert_clean(caught.value)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_HTTP_오류는_상태_코드만_남긴다(status: int) -> None:
    with pytest.raises(MfdsApiError) as caught:
        await build_client(
            lambda request: httpx.Response(status, text=f"oops {KEY}")
        ).nutrition_rows(name="x")

    _assert_clean(caught.value)
    assert str(status) in str(caught.value)


async def test_JSON_이_아닌_응답은_본문을_싣지_않는다() -> None:
    # 키가 틀리면 data.go.kr 은 200 에 XML 오류를 준다
    xml = f"<OpenAPI_ServiceResponse><cmmMsgHeader>{KEY}</cmmMsgHeader></OpenAPI_ServiceResponse>"

    with pytest.raises(MfdsApiError) as caught:
        await build_client(lambda request: httpx.Response(200, text=xml)).nutrition_rows(name="x")

    _assert_clean(caught.value)
    assert "OpenAPI_ServiceResponse" not in str(caught.value)


async def test_결과_코드가_00이_아니면_코드만_남긴다() -> None:
    payload = {"header": {"resultCode": "30", "resultMsg": f"SERVICE KEY IS NOT REGISTERED {KEY}"}}

    with pytest.raises(MfdsApiError) as caught:
        await build_client(lambda request: ok(payload)).nutrition_rows(name="x")

    _assert_clean(caught.value)
    assert "30" in str(caught.value)
    assert "REGISTERED" not in str(caught.value)  # 메시지는 신뢰하지 않는다


@pytest.mark.parametrize(
    "payload",
    [
        [],  # 최상위가 객체가 아님
        {},  # header 없음
        {"header": {"resultCode": "00"}},  # body 없음
        {"header": {"resultCode": "00"}, "body": {"totalCount": "많음", "items": []}},
        {"header": {"resultCode": "00"}, "body": {"totalCount": 1, "items": ["문자열"]}},
        {"header": {"resultCode": "00"}, "body": {"totalCount": 1, "items": "x"}},
    ],
)
async def test_모양이_깨진_응답은_빈_결과로_보지_않는다(payload: Any) -> None:
    # 모양이 깨진 것을 "없음" 으로 읽으면 못 찾은 이름으로 기억되어 장애가 사라진 척한다
    with pytest.raises(MfdsApiError) as caught:
        await build_client(lambda request: ok(payload)).nutrition_rows(name="x")

    _assert_clean(caught.value)


async def test_실패해도_로그에_키가_남지_않는다(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed: {request.url}")

    with caplog.at_level(logging.DEBUG), pytest.raises(MfdsApiError):
        await build_client(handler).nutrition_rows(name="된장찌개")

    assert KEY not in caplog.text


# ── Agent 와 맞물림: 이 클라이언트가 resolve_menu 의 소스가 된다 ──────────
# integration 은 agents 를 import 하지 못해 Agent 포트의 MenuSourceError 를 던지지 못한다.
# resolve_menu 는 예외 종류를 가리지 않고 unresolved 로 두므로 MfdsApiError 도 같은 길로 간다
def _resolver(handler: Any) -> tuple[MenuResolver, InMemoryMenuCatalog]:
    catalog = InMemoryMenuCatalog()
    resolver = MenuResolver(
        catalog=catalog,
        source=build_client(handler),  # type: ignore[arg-type]
        now=datetime(2026, 10, 11, tzinfo=UTC),
        not_found=NotFoundMemory(),
    )
    return resolver, catalog


async def test_실제_응답_모양으로_된장찌개가_해석돼_카탈로그에_들어간다() -> None:
    resolver, catalog = _resolver(lambda request: ok(SAMPLE))

    result = await resolver.resolve("된장찌개 (2.5)")

    assert isinstance(result, Resolved) and result.origin == "fetched"
    row = result.row
    assert row.menu_key == "된장찌개"  # 같은 응답의 "된장찌개_냉이" 는 받지 않았다
    assert row.food_code == "D106-275000000-0001"
    assert row.serving_g == 200.0
    assert row.nutrients["calcium"] == 21.0
    assert row.nutrients["sodium"] == 318.0
    assert await catalog.get("된장찌개") == row


async def test_실제_응답_모양으로_우유는_중량_없이도_해석된다() -> None:
    resolver, _ = _resolver(lambda request: ok(SAMPLE))

    result = await resolver.resolve("우유")

    assert isinstance(result, Resolved)
    assert result.row.serving_g is None
    assert result.row.nutrients["calcium"] == 118.0
    assert 2 in result.row.allergen_codes  # 우유 — 메뉴명에서 뽑은 코드


async def test_변형만_있으면_해석하지_않는다() -> None:
    only_variant = page_of([SAMPLE["body"]["items"][1]])  # 된장찌개_냉이
    resolver, catalog = _resolver(lambda request: ok(only_variant))

    result = await resolver.resolve("된장찌개")

    assert result == Unresolved(menu_key="된장찌개", reason="not_found")
    assert await catalog.get("된장찌개") is None


async def test_클라이언트_실패는_unresolved_이고_카탈로그에_남지_않는다() -> None:
    resolver, catalog = _resolver(lambda request: httpx.Response(503))

    result = await resolver.resolve("된장찌개")

    assert result == Unresolved(menu_key="된장찌개", reason="source_error")
    assert await catalog.get("된장찌개") is None


# ── HTTP 클라이언트 ───────────────────────────────────────────────────
async def test_HTTP_클라이언트는_느린_응답을_기다릴_만큼_길게_걸되_끝없지는_않다() -> None:
    # 100행은 보통 3 ~ 4초이고 느린 때는 10초를 넘는다(17초 관측). 호출별 timeout 은 Agent 가 건다
    async with mfds_nutri_http() as http:
        assert http.timeout.read == HTTP_TIMEOUT_S
        assert 5.0 <= HTTP_TIMEOUT_S <= 30.0
