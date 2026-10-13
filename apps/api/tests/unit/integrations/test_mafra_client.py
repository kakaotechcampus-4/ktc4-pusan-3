"""농식품정보원 레시피 integration — 농림축산식품 공공데이터포털(data.mafra.go.kr).

서버를 부르지 않는다. httpx.MockTransport 로 응답을 바꿔 끼운다. 🚨 아래 행은 전부 **가짜**다 —
이용 조건 답을 받기 전에는 원문(레시피 이름 · 재료)을 저장소에 넣지 않는다. 모양(칸 이름 · 결과 코드
· 감싸는 방식)만 실제 응답(`sample` 키, 2026-10-11)에서 옮겼다.

여기서 지키는 것 넷.
    ① 전체 동기화는 1,000행씩 순서대로 받아 totalCnt 와 맞춘다 — 모자라면 적재하지 않는다
    ② 기본정보와 재료를 RECIPE_ID 로 잇는다. 기본정보 없는 재료 행은 버리고 센다
    ③ 재료 이름이 빈 행은 버리지 않는다 — "모르는 재료" 로 남겨야 그 레시피가 후보에서 빠진다
    ④ 키가 주소 **경로**에 들어간다. 어떻게 실패하든 MafraApiError 하나로 모이고 문구에 키 · 주소 ·
       응답 메시지가 없다
"""

import logging
from typing import Any

import httpx
import pytest

from app.integrations.mafra.client import (
    BASE_URL,
    HTTP_TIMEOUT_S,
    INGREDIENT_SERVICE,
    MAX_CALLS,
    RECIPE_SERVICE,
    ROWS_PER_CALL,
    MafraApiError,
    MafraRecipeClient,
    mafra_recipe_http,
)

KEY = "0123456789abcdef0123456789abcdef"  # 가짜 키


def recipe(recipe_id: int, name: str, *, nation: str = "한식", kind: str = "밥") -> dict:
    return {
        "ROW_NUM": recipe_id,
        "RECIPE_ID": recipe_id,
        "RECIPE_NM_KO": name,
        "SUMRY": "가짜 요약",
        "NATION_CODE": "0",
        "NATION_NM": nation,
        "TY_CODE": "0",
        "TY_NM": kind,
        "COOKING_TIME": "10분",
        "CALORIE": "1Kcal",
        "QNT": "1인분",
        "LEVEL_NM": "초보환영",
        "IRDNT_CODE": "곡류",
        "PC_NM": "1,000원",
    }


def ingredient(recipe_id: int, sn: int, name: str | None, *, role: str = "주재료") -> dict:
    return {
        "ROW_NUM": sn,
        "RECIPE_ID": recipe_id,
        "IRDNT_SN": sn,
        "IRDNT_NM": name,
        "IRDNT_CPCTY": "1개",
        "IRDNT_TY_CODE": "0",
        "IRDNT_TY_NM": role,
    }


def page(service: str, rows: list[dict], *, total: int, start: int, end: int) -> dict:
    return {
        service: {
            "totalCnt": total,
            "startRow": start,
            "endRow": end,
            "result": {"code": "INFO-000", "message": "정상 처리되었습니다."},
            "row": rows,
        }
    }


def error(code: str, message: str = "") -> dict:
    # 오류는 HTTP 200 에 서비스 이름 없이 result 만 온다
    return {"result": {"code": code, "message": message}}


class FakeServer:
    """서비스별 전체 행을 들고 요청 범위(start/end)만큼 잘라 준다. 요청 경로를 기록한다."""

    def __init__(self, recipes: list[dict], ingredients: list[dict]) -> None:
        self.data = {RECIPE_SERVICE: recipes, INGREDIENT_SERVICE: ingredients}
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        *_, service, start, end = request.url.path.split("/")
        rows = self.data[service]
        chunk = rows[int(start) - 1 : int(end)]
        return httpx.Response(
            200, json=page(service, chunk, total=len(rows), start=int(start), end=int(end))
        )


def build(handler: Any, key: str = KEY) -> MafraRecipeClient:
    return MafraRecipeClient(
        api_key=key, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


# ── ① 요청과 쪽 넘기기 ────────────────────────────────────────────────
async def test_요청_경로는_키_형식_서비스_시작_끝이다() -> None:
    server = FakeServer([recipe(1, "가짜밥")], [ingredient(1, 1, "가짜쌀")])

    await build(server).recipes_with_ingredients()

    assert BASE_URL.startswith("http://211.237.50.150:7080/openapi")
    assert server.paths == [
        f"/openapi/{KEY}/json/{RECIPE_SERVICE}/1/{ROWS_PER_CALL}",
        f"/openapi/{KEY}/json/{INGREDIENT_SERVICE}/1/{ROWS_PER_CALL}",
    ]


async def test_천_행을_넘으면_다음_범위를_차례로_받는다() -> None:
    recipes = [recipe(i, f"가짜{i}") for i in range(1, 6)]
    ingredients = [ingredient(1 + i % 5, i, f"가짜재료{i}") for i in range(1, 2501)]
    server = FakeServer(recipes, ingredients)

    synced = await build(server).recipes_with_ingredients()

    assert server.paths[1:] == [
        f"/openapi/{KEY}/json/{INGREDIENT_SERVICE}/1/1000",
        f"/openapi/{KEY}/json/{INGREDIENT_SERVICE}/1001/2000",
        f"/openapi/{KEY}/json/{INGREDIENT_SERVICE}/2001/3000",
    ]
    assert synced.ingredient_rows == 2500
    assert sum(len(r.ingredients) for r in synced.recipes) == 2500


async def test_받은_행_수가_totalCnt_와_다르면_적재하지_않는다() -> None:
    # 중간에 행이 빠지면 재료를 덜 아는 레시피가 생긴다 — 알레르기를 놓치는 쪽이다
    def handler(request: httpx.Request) -> httpx.Response:
        *_, service, start, end = request.url.path.split("/")
        return httpx.Response(
            200, json=page(service, [recipe(1, "가짜밥")], total=3, start=1, end=1000)
        )

    with pytest.raises(MafraApiError):
        await build(handler).recipes_with_ingredients()


async def test_행이_상한을_넘으면_받지_않는다() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        *_, service, start, end = request.url.path.split("/")
        total = ROWS_PER_CALL * MAX_CALLS + 1
        return httpx.Response(200, json=page(service, [], total=total, start=1, end=1000))

    with pytest.raises(MafraApiError):
        await build(handler).recipes_with_ingredients()
    assert calls == 1  # 첫 쪽에서 totalCnt 를 보고 멈춘다 — 하루 호출 한도를 쓰지 않는다


async def test_데이터가_없다는_안내는_빈_결과다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=error("INFO-200", "해당하는 데이터가 없습니다."))

    synced = await build(handler).recipes_with_ingredients()

    assert synced.recipes == ()


# ── ② · ③ RECIPE_ID 로 잇기 ───────────────────────────────────────────
async def test_기본정보와_재료를_RECIPE_ID_로_잇고_순번대로_둔다() -> None:
    server = FakeServer(
        [recipe(1, "가짜찌개", kind="찌개"), recipe(2, "가짜볶음", nation="중국")],
        [
            ingredient(2, 2, "가짜고기", role="부재료"),
            ingredient(1, 1, "가짜두부"),
            ingredient(2, 1, "가짜양파"),
            ingredient(1, 2, "가짜간장", role="양념"),
        ],
    )

    synced = await build(server).recipes_with_ingredients()

    first, second = synced.recipes
    assert (first.recipe_id, first.name, first.kind, first.nation) == (
        "1",
        "가짜찌개",
        "찌개",
        "한식",
    )
    assert [i.name for i in first.ingredients] == ["가짜두부", "가짜간장"]
    assert [i.role for i in first.ingredients] == ["주재료", "양념"]
    assert first.ingredients[0].amount == "1개"
    assert (second.recipe_id, second.nation) == ("2", "중국")
    assert [i.name for i in second.ingredients] == ["가짜양파", "가짜고기"]


async def test_기본정보_없는_재료_행은_버리고_센다() -> None:
    # 실제 데이터에도 있다 — 재료 171행이 기본정보에 없는 RECIPE_ID 38개(501 ~ 540)를
    # 가리킨다(10-11)
    server = FakeServer(
        [recipe(1, "가짜밥")],
        [
            ingredient(1, 1, "가짜쌀"),
            ingredient(537, 1, "가짜재료"),
            ingredient(538, 1, "가짜재료"),
        ],
    )

    synced = await build(server).recipes_with_ingredients()

    assert [r.recipe_id for r in synced.recipes] == ["1"]
    assert synced.orphan_ingredient_rows == 2
    assert synced.ingredient_rows == 3  # 받은 행 수는 그대로 센다


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_재료_이름이_빈_행은_모르는_재료로_남긴다(blank: str | None) -> None:
    # 버리면 그 레시피가 재료를 다 아는 것처럼 보인다. 남겨 두면 정리 단계가 resolved=false 로 둔다
    server = FakeServer(
        [recipe(1, "가짜밥")], [ingredient(1, 1, "가짜쌀"), ingredient(1, 2, blank)]
    )

    synced = await build(server).recipes_with_ingredients()

    [only] = synced.recipes
    assert [i.name for i in only.ingredients] == ["가짜쌀", None]
    assert synced.unnamed_ingredient_rows == 1


async def test_재료가_하나도_없는_레시피도_남긴다() -> None:
    # 재료를 모르는 레시피다 — 버리지 않고 정리 단계가 후보에서 뺀다
    server = FakeServer([recipe(1, "가짜밥"), recipe(2, "가짜국")], [ingredient(1, 1, "가짜쌀")])

    synced = await build(server).recipes_with_ingredients()

    assert [(r.recipe_id, len(r.ingredients)) for r in synced.recipes] == [("1", 1), ("2", 0)]


async def test_RECIPE_ID_가_겹치면_적재하지_않는다() -> None:
    server = FakeServer([recipe(1, "가짜밥"), recipe(1, "가짜밥2")], [])

    with pytest.raises(MafraApiError):
        await build(server).recipes_with_ingredients()


# ── ④ 실패 ────────────────────────────────────────────────────────────
def _assert_clean(exc: MafraApiError) -> None:
    text = str(exc)
    assert KEY not in text
    assert "211.237.50.150" not in text
    assert "openapi" not in text
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("INFO-100", f"인증키가 유효하지 않습니다 {KEY}"),
        ("ERROR-335", "샘플데이터는 한번에 최대 5건"),
        ("ERROR-334", "요청종료위치 보다 요청시작위치가 더 큽니다"),
    ],
)
async def test_오류_코드는_코드만_남기고_메시지는_싣지_않는다(code: str, message: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=error(code, message))

    with pytest.raises(MafraApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert code in str(caught.value)
    assert message not in str(caught.value)


async def test_연결_실패는_주소와_키_없이_MafraApiError() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed: {request.url}")

    with pytest.raises(MafraApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert "ConnectError" in str(caught.value)


@pytest.mark.parametrize("status", [404, 500, 502])
async def test_HTTP_오류는_상태_코드만_남긴다(status: int) -> None:
    with pytest.raises(MafraApiError) as caught:
        await build(
            lambda request: httpx.Response(status, text=f"{request.url}")
        ).recipes_with_ingredients()

    _assert_clean(caught.value)
    assert str(status) in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        [],
        {},
        {RECIPE_SERVICE: {"totalCnt": "많음", "result": {"code": "INFO-000"}, "row": []}},
        {RECIPE_SERVICE: {"totalCnt": 1, "result": {"code": "INFO-000"}, "row": ["문자열"]}},
        {
            RECIPE_SERVICE: {
                "totalCnt": 1,
                "result": {"code": "INFO-000"},
                "row": [{"RECIPE_NM_KO": "x"}],
            }
        },
    ],
)
async def test_모양이_깨진_응답은_적재하지_않는다(payload: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    with pytest.raises(MafraApiError) as caught:
        await build(handler).recipes_with_ingredients()

    _assert_clean(caught.value)


async def test_키가_없으면_부르지_않는다() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(MafraApiError):
        await build(handler, key="").recipes_with_ingredients()
    assert calls == 0


@pytest.mark.parametrize("bad", ["abc/../x", "abc def", "키값", "abc?x=1", "abc#x"])
async def test_키에_경로를_바꿀_글자가_있으면_부르지_않는다(bad: str) -> None:
    # 키가 경로에 그대로 들어간다 — "/" · "?" 가 섞이면 다른 주소를 부르게 된다
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(MafraApiError) as caught:
        await build(handler, key=bad).recipes_with_ingredients()
    assert calls == 0
    assert bad not in str(caught.value)


async def test_실패해도_로그에_키가_남지_않는다(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed: {request.url}")

    with caplog.at_level(logging.DEBUG), pytest.raises(MafraApiError):
        await build(handler).recipes_with_ingredients()

    assert KEY not in caplog.text


# ── HTTP 클라이언트 ───────────────────────────────────────────────────
async def test_HTTP_클라이언트는_느린_서버를_기다리되_끝없지는_않다() -> None:
    # 배치 전용이라 요청 중 20초 예산과 무관하다. 10-11 오전에 연결이 끊긴 적이 있다
    async with mafra_recipe_http() as http:
        assert http.timeout.read == HTTP_TIMEOUT_S
        assert 10.0 <= HTTP_TIMEOUT_S <= 120.0
