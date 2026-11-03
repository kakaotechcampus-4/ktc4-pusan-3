"""농촌진흥청 「농식품 이달의 식재료」 레시피 integration — 공공데이터포털(apis.data.go.kr).

서버를 부르지 않는다. httpx.MockTransport 로 응답을 바꿔 끼운다. 🚨 아래 행은 전부 **가짜**다 — 모양
(오퍼레이션 이름 · 칸 이름 · `header`/`body` 로 감싸는 방식)만 실제 호출을 확인한 기록(2026-10-18)과
공개 안내 페이지에서 옮겼다.

실제 응답(2026-11-02 키로 확인)은 `response` 로 한 번 감싸고 성공 코드가 "200" 이다. 목록 항목은
`body.items.item` 에 든다.

여기서 지키는 것 다섯.
    ① 12달 목록을 빈 식재료명 · numOfRows=1000 으로 받고 고유 번호마다 상세를 한 번만 부른다
    ② 받은 수가 `totalCount` · 목록 고유 수와 다르거나 상세가 어긋나면 하나도 돌려주지 않는다
    ③ 재료 문자열은 `split_materials` 로 옮기고, 구조가 깨지면 동기화를 멈춘다
    ④ 키가 요청 **쿼리**에 들어간다. 어떻게 실패하든 NicsApiError 하나로 모이고 문구 · 로그에 키 ·
       주소 · 응답 메시지가 없다
    ⑤ NICS 가 주는 영양 · 조리법 · 이미지는 읽지 않는다
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from app.core.mafra_recipe_raw import RawIngredient
from app.integrations.nics.client import (
    BASE_URL,
    CONCURRENCY,
    HTTP_TIMEOUT_S,
    MAX_DETAIL_CALLS,
    ROWS_PER_CALL,
    NicsApiError,
    NicsRecipeClient,
    nics_recipe_http,
)
from app.integrations.nics import client as nics_client

# 가짜 Decoding 키. 실제 키처럼 + / = 가 섞여 있어야 쿼리 인코딩을 거쳐도 그대로 가는지 본다
KEY = "Fake+Decoding/Key=0123456789abcdefABCDEF"


def ok(body: dict[str, Any]) -> dict[str, Any]:
    # 실제 응답(2026-11-02 확인): `response` 로 한 번 감싸고 성공 코드는 "200" 이다
    return {"response": {"header": {"resultCode": "200", "resultMsg": "성공"}, "body": body}}


def listing(items: list[Any], *, total: int | None = None, form: str = "list") -> dict[str, Any]:
    """월별 목록 응답. `form` 은 항목이 든 칸의 두 모양 — items 가 목록 / items.item."""
    rows = [{"recipeSn": sn} for sn in items]
    wrapped: Any = rows if form == "list" else {"item": rows[0] if form == "one" else rows}
    return ok(
        {
            "items": wrapped,
            "numOfRows": ROWS_PER_CALL,
            "pageNo": 1,
            "totalCount": len(rows) if total is None else total,
        }
    )


def detail(sn: Any, name: str | None = None, matrl: Any = None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "recipeSn": sn,
        "recipeNm": f"가짜{sn}" if name is None else name,
        "matrlInfo": f"▶주재료 : 가짜재료{sn}" if matrl is None else matrl,
        "ckngMthInfo": "가짜 조리법 문장",
        "grpMlsvApplc": "가짜 활용 문장",
    }
    return ok(
        {
            "stdInfo": info,
            "nutriInfo": {"clri": "999", "prot": "9"},
            "ckngImgList": [{"imgUrl": "https://example.invalid/가짜.jpg"}],
        }
    )


def default_months() -> dict[int, list[Any]]:
    return {month: [month] for month in range(1, 13)}


class FakeServer:
    """달별 목록과 레시피별 상세를 들고 요청을 기록한다. `over` 로 상세 응답을 바꿔 끼운다."""

    def __init__(
        self,
        months: dict[int, list[Any]] | None = None,
        *,
        matrl: dict[str, str] | None = None,
        over: dict[str, Callable[[], httpx.Response]] | None = None,
        form: str = "list",
    ) -> None:
        self.months = default_months() if months is None else months
        self.matrl = matrl or {}
        self.over = over or {}
        self.form = form
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        op = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        self.calls.append((op, params))
        if op == "recipeList":
            items = self.months.get(int(params["srchMonth"]), [])
            return httpx.Response(200, json=listing(items, form=self.form))
        sn = params["srchRecipeSn"]
        if sn in self.over:
            return self.over[sn]()
        return httpx.Response(200, json=detail(sn, matrl=self.matrl.get(sn)))

    def ops(self, op: str) -> list[dict[str, str]]:
        return [params for name, params in self.calls if name == op]


def build(handler: Any, key: str = KEY) -> NicsRecipeClient:
    return NicsRecipeClient(
        api_key=key, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


# ── ① 요청과 목록 · 상세 ──────────────────────────────────────────────
async def test_12달_목록을_빈_식재료명과_numOfRows_로_받는다() -> None:
    server = FakeServer()

    await build(server).recipes_with_ingredients()

    listed = server.ops("recipeList")
    # 달별 목록은 동시에 부르므로 순서가 아니라 빠짐없이 한 번씩인지 본다
    assert sorted(int(params["srchMonth"]) for params in listed) == list(range(1, 13))
    assert all(params["srchFdmtNm"] == "" for params in listed)  # 빈 값이면 그 달 전체다
    assert all(params["numOfRows"] == str(ROWS_PER_CALL) for params in listed)
    assert all(params["format"] == "json" for params in listed)
    assert BASE_URL == "https://apis.data.go.kr/1390803/allbaroFoodMonth"


async def test_키는_쿼리로_가고_인코딩을_거쳐도_그대로_도착한다() -> None:
    server = FakeServer()

    await build(server).recipes_with_ingredients()

    assert {params["serviceKey"] for _, params in server.calls} == {KEY}


async def test_같은_레시피가_여러_달에_있어도_상세는_한_번만_받는다() -> None:
    months = default_months() | {1: [1, 2], 2: [2, 3]}  # 2 는 두 달, 3 은 3월과 겹침
    server = FakeServer(months)

    synced = await build(server).recipes_with_ingredients()

    asked = [params["srchRecipeSn"] for params in server.ops("recipeCont")]
    assert sorted(asked, key=int) == [str(n) for n in range(1, 13)]
    assert len(asked) == len(set(asked))
    assert synced.recipe_rows == 12


async def test_recipeSn_의_숫자와_글자는_같은_ID_다() -> None:
    months = default_months() | {1: [55], 2: ["55"]}
    server = FakeServer(months)

    synced = await build(server).recipes_with_ingredients()

    ids = [recipe.recipe_id for recipe in synced.recipes]
    assert ids.count("55") == 1
    assert [p["srchRecipeSn"] for p in server.ops("recipeCont")].count("55") == 1


@pytest.mark.parametrize("form", ["list", "item_list", "one"])
async def test_목록_항목이_든_칸의_두_모양을_받는다(form: str) -> None:
    months = {month: [month] for month in range(1, 13)}  # 달마다 하나라 `one` 모양도 가능하다

    synced = await build(FakeServer(months, form=form)).recipes_with_ingredients()

    assert [recipe.recipe_id for recipe in synced.recipes] == [str(m) for m in range(1, 13)]


async def test_레시피는_번호_순으로_돌려준다() -> None:
    months = default_months() | {1: [30, 20, 10]}

    synced = await build(FakeServer(months)).recipes_with_ingredients()

    ids = [int(recipe.recipe_id) for recipe in synced.recipes]
    assert ids == sorted(ids)


async def test_상세는_동시에_CONCURRENCY_개까지만_부른다() -> None:
    running = peak = 0
    months = default_months() | {1: list(range(100, 140))}  # 고유 51건

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        server = FakeServer(months)
        if request.url.path.endswith("recipeCont"):
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
        return server(request)

    await build(handler).recipes_with_ingredients()

    assert 2 <= peak <= CONCURRENCY


async def test_달별_목록도_동시에_CONCURRENCY_개까지_부른다() -> None:
    # 12달을 차례로 부르면 느린 응답이 그대로 쌓여 배포의 SSM 제한(900초)에 가까워진다
    running = peak = 0
    server = FakeServer()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        if request.url.path.endswith("recipeList"):
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
        return server(request)

    synced = await build(handler).recipes_with_ingredients()

    assert 2 <= peak <= CONCURRENCY
    assert [recipe.recipe_id for recipe in synced.recipes] == [str(m) for m in range(1, 13)]


def test_클라이언트를_찍어도_키가_보이지_않는다() -> None:
    # traceback 이 지역 변수를 찍거나(pytest · Sentry) 로그가 객체를 찍어도 키가 새지 않는다
    client = build(FakeServer())

    assert KEY not in repr(client)
    assert KEY not in str(client)


# ── 상세 → RawRecipe ─────────────────────────────────────────────────
async def test_상세의_이름과_재료_문자열을_옮긴다() -> None:
    matrl = {"1": "(3인분) ▶주재료 : 오이지(80g), 무 ▶양념 : 소금"}

    synced = await build(FakeServer(matrl=matrl)).recipes_with_ingredients()

    first = synced.recipes[0]
    assert (first.recipe_id, first.name, first.nation, first.kind) == ("1", "가짜1", None, None)
    assert first.ingredients == (
        RawIngredient("오이지", "80g", "주재료"),
        RawIngredient("무", None, "주재료"),
        RawIngredient("소금", None, "양념"),
    )


async def test_영양_조리법_이미지는_읽지_않는다() -> None:
    synced = await build(FakeServer()).recipes_with_ingredients()

    text = repr(synced)
    for unread in ("가짜 조리법 문장", "가짜 활용 문장", "999", "example.invalid"):
        assert unread not in text


async def test_건수를_센다() -> None:
    matrl = {"1": "▶주재료 : 두부,, 파", "2": "▶주재료 : 무"}

    synced = await build(FakeServer(matrl=matrl)).recipes_with_ingredients()

    assert synced.recipe_rows == 12
    assert synced.ingredient_rows == 3 + 1 + 10  # 두부 · (빈 조각) · 파 / 무 / 나머지 열 개
    assert synced.unnamed_ingredient_rows == 1
    assert synced.orphan_ingredient_rows == 0


async def test_재료_문자열이_비면_재료_없는_레시피로_남긴다() -> None:
    synced = await build(FakeServer(matrl={"1": ""})).recipes_with_ingredients()

    assert synced.recipes[0].ingredients == ()


@pytest.mark.parametrize(
    "bad",
    ["▶주재료 :", "▶주재료 : 두부(큰 것, 파", "▶주재료 : 오이 ▶양념 :", "▶▶양념 : 소금"],
)
async def test_재료_문자열이_깨진_레시피만_모르는_재료로_두고_나머지는_받는다(
    bad: str, caplog: pytest.LogCaptureFixture
) -> None:
    # 598건 중 하나가 깨졌다고 전체를 멈추면 NICS 는 물론 농식품정보원 · 영양DB 적재까지 막힌다.
    # 그 레시피만 이름 없는 재료로 두면 후보에서 빠지고(resolved=false) 나머지는 그대로 실린다
    with caplog.at_level(logging.WARNING):
        synced = await build(FakeServer(matrl={"7": bad})).recipes_with_ingredients()

    recipes = {recipe.recipe_id: recipe for recipe in synced.recipes}
    assert len(recipes) == 12
    assert recipes["7"].ingredients == (RawIngredient(None),)
    assert recipes["8"].ingredients == (RawIngredient("가짜재료8", None, "주재료"),)
    assert "7" in caplog.text  # 어느 레시피인지는 남긴다
    assert "주재료" not in caplog.text and "두부" not in caplog.text  # 원문은 싣지 않는다
    assert KEY not in caplog.text


# ── ② 받은 수 · 상세 검증 ─────────────────────────────────────────────
async def test_받은_목록_수가_totalCount_와_다르면_받지_않는다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("recipeList"):
            return httpx.Response(200, json=listing([1, 2], total=5))
        return httpx.Response(200, json=detail(1))

    with pytest.raises(NicsApiError) as caught:
        await build(handler).recipes_with_ingredients()
    _assert_clean(caught.value)


async def test_목록이_하나도_없으면_받지_않는다() -> None:
    server = FakeServer({month: [] for month in range(1, 13)})

    with pytest.raises(NicsApiError):
        await build(server).recipes_with_ingredients()
    assert server.ops("recipeCont") == []


async def test_한_달이라도_목록이_비면_받지_않는다() -> None:
    # 12달 모두 레시피가 있다. 한 달이 비면 응답이 일부만 온 것이고, 그대로 적재하면 그 달의
    # 레시피가 후보 풀에서 조용히 내려간다
    server = FakeServer(default_months() | {5: []})

    with pytest.raises(NicsApiError):
        await build(server).recipes_with_ingredients()
    assert server.ops("recipeCont") == []


async def test_고유_번호가_상한을_넘으면_상세를_받지_않는다() -> None:
    per_month = MAX_DETAIL_CALLS // 12 + 1
    months = {m: list(range(m * 1000, m * 1000 + per_month)) for m in range(1, 13)}
    server = FakeServer(months)

    with pytest.raises(NicsApiError):
        await build(server).recipes_with_ingredients()
    assert server.ops("recipeCont") == []


async def test_상세_번호가_목록_번호와_다르면_받지_않는다() -> None:
    server = FakeServer(over={"4": lambda: httpx.Response(200, json=detail(99))})

    with pytest.raises(NicsApiError):
        await build(server).recipes_with_ingredients()


async def test_상세에_같은_레시피가_두_번_오면_받지_않는다() -> None:
    # 서로 다른 두 번호를 물었는데 같은 레시피가 답으로 온다
    server = FakeServer(over={"6": lambda: httpx.Response(200, json=detail(5))})

    with pytest.raises(NicsApiError):
        await build(server).recipes_with_ingredients()


@pytest.mark.parametrize("name", [None, "", "   "])
async def test_상세에_이름이_없으면_받지_않는다(name: str | None) -> None:
    body = detail(3)
    body["response"]["body"]["stdInfo"]["recipeNm"] = name

    with pytest.raises(NicsApiError):
        await build(
            FakeServer(over={"3": lambda: httpx.Response(200, json=body)})
        ).recipes_with_ingredients()


async def test_상세_하나가_실패하면_하나도_돌려주지_않는다() -> None:
    server = FakeServer(over={"8": lambda: httpx.Response(500, text="오류")})

    with pytest.raises(NicsApiError) as caught:
        await build(server).recipes_with_ingredients()

    assert "500" in str(caught.value)


async def test_상세에_stdInfo_가_없으면_받지_않는다() -> None:
    server = FakeServer(over={"2": lambda: httpx.Response(200, json=ok({}))})

    with pytest.raises(NicsApiError):
        await build(server).recipes_with_ingredients()


# ── ④ 실패 ────────────────────────────────────────────────────────────
def _assert_clean(exc: NicsApiError) -> None:
    text = str(exc)
    assert KEY not in text
    assert quote(KEY, safe="") not in text
    assert "apis.data.go.kr" not in text
    assert "allbaroFoodMonth" not in text
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("30", f"SERVICE KEY IS NOT REGISTERED {KEY}"),
        ("22", "LIMITED NUMBER OF SERVICE REQUESTS EXCEEDS ERROR"),
        ("03", "NODATA_ERROR"),
    ],
)
async def test_결과_코드가_정상이_아니면_코드만_남기고_메시지는_싣지_않는다(
    code: str, message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"header": {"resultCode": code, "resultMsg": message}})

    with pytest.raises(NicsApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert code in str(caught.value)
    assert message not in str(caught.value)


async def test_연결_실패는_주소와_키_없이_NicsApiError() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed: {request.url}")

    with pytest.raises(NicsApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert "ConnectError" in str(caught.value)


@pytest.mark.parametrize("status", [401, 403, 500, 502])
async def test_HTTP_오류는_상태_코드만_남긴다(status: int) -> None:
    with pytest.raises(NicsApiError) as caught:
        await build(
            lambda request: httpx.Response(status, text=f"{request.url}")
        ).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert str(status) in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        # 키 오류는 JSON 이 아니라 XML 로 온다
        "<OpenAPI_ServiceResponse><cmmMsgHeader/></OpenAPI_ServiceResponse>",
        [],
        {},
        {"header": {"resultCode": "00"}},
        {"header": {"resultCode": "00"}, "body": "문자열"},
        {"header": {"resultCode": "00"}, "body": {"items": "문자열", "totalCount": 1}},
        {"header": {"resultCode": "00"}, "body": {"items": ["문자열"], "totalCount": 1}},
        {"header": {"resultCode": "00"}, "body": {"items": [{"recipeSn": "x"}], "totalCount": 1}},
        {"header": {"resultCode": "00"}, "body": {"items": [{"recipeSn": True}], "totalCount": 1}},
        {"header": {"resultCode": "00"}, "body": {"items": [], "totalCount": "많음"}},
        {"header": {"resultCode": "00"}, "body": {"items": []}},
    ],
)
async def test_모양이_깨진_응답은_받지_않는다(payload: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    with pytest.raises(NicsApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)


@pytest.mark.parametrize("code", ["200", "00"])
async def test_감싸지_않은_응답과_표준_성공_코드도_받는다(code: str) -> None:
    # 실제 응답은 `response` 로 감싸고 "200" 이다. 감싸지 않은 모양 · "00"(공공데이터포털 표준)도 받는다
    server = FakeServer()

    def bare(request: httpx.Request) -> httpx.Response:
        payload = server(request).json()["response"]
        payload["header"]["resultCode"] = code
        return httpx.Response(200, json=payload)

    synced = await build(bare).recipes_with_ingredients()

    assert synced.recipe_rows == 12


async def test_키가_없으면_부르지_않는다() -> None:
    server = FakeServer()

    with pytest.raises(NicsApiError):
        await build(server, key="").recipes_with_ingredients()
    assert server.calls == []


@pytest.mark.parametrize("bad", ["abc def", "abc\ndef", "키값", "a" * 300])
async def test_키_모양이_이상하면_부르지_않는다(bad: str) -> None:
    server = FakeServer()

    with pytest.raises(NicsApiError) as caught:
        await build(server, key=bad).recipes_with_ingredients()

    assert server.calls == []
    assert bad not in str(caught.value)


# ── 로그 ─────────────────────────────────────────────────────────────
@pytest.fixture
def httpx_logger_open():
    """서버 밖(배치 스크립트)처럼 httpx 로거가 막혀 있지 않은 상태로 시험한다."""
    logger = logging.getLogger("httpx")
    before = logger.level
    logger.setLevel(logging.NOTSET)
    yield logger
    logger.setLevel(before)


async def test_받기에_성공해도_로그에_키가_남지_않는다(
    httpx_logger_open: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    # httpx 는 요청마다 요청 주소 전체를 INFO 로 남긴다 — 키가 쿼리에 있어서 그대로 새는 길이다
    with caplog.at_level(logging.DEBUG):
        await build(FakeServer()).recipes_with_ingredients()

    assert KEY not in caplog.text
    assert quote(KEY, safe="") not in caplog.text


async def test_실패해도_로그에_키가_남지_않는다(
    httpx_logger_open: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("recipeCont"):
            return httpx.Response(500, text="오류")
        return httpx.Response(200, json=listing([1]))

    with caplog.at_level(logging.DEBUG), pytest.raises(NicsApiError):
        await build(handler).recipes_with_ingredients()

    assert KEY not in caplog.text
    assert quote(KEY, safe="") not in caplog.text


async def test_httpx_로거_수준은_끝나면_되돌려_놓는다(httpx_logger_open: logging.Logger) -> None:
    await build(FakeServer()).recipes_with_ingredients()
    assert httpx_logger_open.level == logging.NOTSET

    with pytest.raises(NicsApiError):
        await build(lambda request: httpx.Response(500)).recipes_with_ingredients()
    assert httpx_logger_open.level == logging.NOTSET


# ── HTTP 클라이언트 ───────────────────────────────────────────────────
async def test_HTTP_클라이언트는_느린_서버를_기다리되_끝없지는_않다() -> None:
    # 배치 전용이라 요청 중 20초 예산과 무관하다
    async with nics_recipe_http() as http:
        assert http.timeout.read == HTTP_TIMEOUT_S
        assert 10.0 <= HTTP_TIMEOUT_S <= 120.0


def test_호출_상한과_동시성은_실측_값이다() -> None:
    assert (ROWS_PER_CALL, MAX_DETAIL_CALLS, CONCURRENCY) == (1000, 1000, 2)


# ── 호출 빈도 제한(429) ───────────────────────────────────────────────
# 실제 API 는 짧은 시간에 몰리면 429 를 준다(2026-11-02 실측: 동시 5 로 몰면 약 20건 뒤부터,
# 초당 5건 안팎의 지속 호출은 120건까지 통과). 쉬었다가 다시 부른다
@pytest.fixture
def no_wait(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(nics_client.asyncio, "sleep", fake_sleep)
    return waits


async def test_429_는_쉬었다가_다시_불러_받는다(no_wait) -> None:
    server = FakeServer()
    limited = {"left": 3}

    def handler(request: httpx.Request) -> httpx.Response:
        if limited["left"] > 0:
            limited["left"] -= 1
            return httpx.Response(429, text="Too Many Requests")
        return server(request)

    synced = await build(handler).recipes_with_ingredients()

    assert synced.recipe_rows == 12
    assert no_wait == list(nics_client.RETRY_DELAYS_S[:3])  # 점점 길게 쉰다


async def test_429_가_계속되면_상태_코드만_남기고_멈춘다(no_wait) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text=f"{request.url}")

    with pytest.raises(NicsApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert "429" in str(caught.value)
    # 달 목록 하나가 다 쉬고도 안 되면 끝낸다 — 12달이 각자 재시도해도 한 달의 시도 수는 정해져 있다
    assert calls <= (len(nics_client.RETRY_DELAYS_S) + 1) * 12


@pytest.mark.parametrize("status", [401, 403, 500, 502])
async def test_429_가_아닌_HTTP_오류는_다시_부르지_않는다(no_wait, status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    with pytest.raises(NicsApiError):
        await build(handler).recipes_with_ingredients()

    assert no_wait == []
